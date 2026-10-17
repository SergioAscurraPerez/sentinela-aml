"""Agente de extracción: lee un documento de origen de fondos (TXT / PDF / DOCX) y lo contrasta con lo que entró.

Privacidad: el texto se enmascara ANTES de ir al LLM (montos -> [MONTO_k], documentos -> [DOC], datos del cliente
-> [REDACTADO]). El LLM solo dice cuál monto es el declarado y el concepto; la comparación numérica es determinista.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile

from core import analytics as A
from core.pseudonymizer import amount_band

from .base import Agent, Case

AMOUNT_RE = re.compile(r"(?:S/\.?|US\$|USD|\$|£|GBP|PEN)?\s?\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|"
                       r"(?:S/\.?|US\$|USD|\$|£|GBP|PEN)\s?\d+(?:[.,]\d{1,2})?")
DOC_RE = re.compile(r"\b\d{8,11}\b")


def read_text(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    if n.endswith(".docx"):
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)
    return data.decode("utf-8", errors="replace")


def _to_number(s: str) -> float:
    s = re.sub(r"[^\d.,]", "", s)
    if re.search(r"[.,]\d{1,2}$", s):
        s = re.sub(r"[.,\s]", "", s[:-3]) + "." + s[-2:] if s[-3] in ".," else re.sub(r"[.,]", "", s[:-2]) + "." + s[-1:]
    else:
        s = re.sub(r"[.,]", "", s)
    return float(s or 0)


def _currency(s: str) -> str:
    return "PEN" if "S/" in s or "PEN" in s else "GBP" if "£" in s or "GBP" in s else "USD"


def mask(text: str, forbidden: list[str]) -> tuple[str, list[dict]]:
    for v in sorted({f for f in forbidden if f and len(f) > 3}, key=len, reverse=True):
        text = text.replace(v, "[REDACTADO]")
    amounts: list[dict] = []

    def sub(m):
        amounts.append({"id": f"MONTO_{len(amounts) + 1}", "valor": _to_number(m.group()), "moneda": _currency(m.group())})
        return f"[{amounts[-1]['id']}]"
    text = AMOUNT_RE.sub(sub, text)
    return DOC_RE.sub("[DOC]", text), amounts


class AgenteExtraccion(Agent):
    name = "agente_extraccion"
    capabilities = ["extraccion_documentos", "origen_fondos"]
    description = "Lee documentos de origen de fondos (TXT/PDF/DOCX), extrae lo declarado y lo contrasta con lo recibido."

    def run(self, case: Case) -> list[dict]:
        return [self.analyze(case, d) for d in case.raw.get("documentos", [])]

    def analyze(self, case: Case, doc: dict) -> dict:
        text = read_text(doc["nombre"], doc["data"])
        masked, amounts = mask(text, case.forbidden)
        fb_id = max(amounts, key=lambda a: a["valor"])["id"] if amounts else None
        fb = {"monto_declarado": fb_id, "concepto": "no identificado", "fecha": "", "tipo_documento": "documento"}
        data, meta = self.ask_llm(case, "Lee el documento (montos y datos personales enmascarados). Indica cuál "
                                        "marcador [MONTO_k] es el monto que el cliente declara como origen de los fondos, "
                                        "el concepto (venta de inmueble, préstamo, herencia, sueldo...), la fecha y el tipo "
                                        'de documento. JSON: {"monto_declarado": "MONTO_k" | null, "concepto": str, '
                                        '"fecha": str, "tipo_documento": str}',
                                  {"documento": masked[:6000]}, fb)
        sel = str(data.get("monto_declarado") or "").strip("[]")
        hit = next((a for a in amounts if a["id"] == sel), None) or next((a for a in amounts if a["id"] == fb_id), None)
        fx_doc = case.policy.get("fx_a_usd_por_moneda", {}).get(hit["moneda"], 1.0) if hit else 1.0
        declarado_usd = hit["valor"] * fx_doc if hit else 0.0
        recibido_usd = A.peak_30d_inflow(case.window, case.account) * case.policy.get("fx_a_usd", 1.0)
        cobertura = declarado_usd / recibido_usd if recibido_usd else 1.0
        base = (f"Documento '{doc['nombre']}' ({data.get('tipo_documento')}, {data.get('concepto')}) declara "
                f"{amount_band(declarado_usd)} USD vs {amount_band(recibido_usd)} USD recibidos en 30 días")
        extra = {"sha256": doc["sha256"], "cobertura": round(cobertura, 2), "concepto": data.get("concepto"),
                 "modo_llm": meta["modo"], "montos_encontrados": len(amounts)}
        src = f"{self.name} ({meta['modo']}) · sha256 {doc['sha256'][:12]}…"
        why = (f"El documento se leyó con huella sha256 {doc['sha256']}. El LLM vio el texto enmascarado; la comparación "
               f"de montos es determinista. Cobertura del origen declarado: {cobertura:.0%} de lo recibido.")
        if not hit:
            return self.finding(case, f"Documento '{doc['nombre']}' sin monto identificable", "origen_fondos", 0.3, [],
                                src, why, extra=extra)
        if cobertura >= 0.8:
            return self.finding(case, base + ": el origen declarado lo explica", "origen_fondos", 0.5, [], src, why,
                                kind="mitigante", extra=extra)
        return self.finding(case, base + f": solo explica el {cobertura:.0%}, no cuadra", "origen_fondos",
                            0.65 if cobertura < 0.5 else 0.45, [], src, why, extra=extra)


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
