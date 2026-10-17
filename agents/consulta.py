"""Hechos del caso para responder preguntas del analista ('pedir más información').

Todo se calcula dentro del banco y sale pseudonimizado (CTA_###, montos en rangos). El LLM redacta la respuesta
con estos hechos; sin LLM, `rule_answer` responde según el tema de la pregunta con los mismos hechos.
"""
from __future__ import annotations

import unicodedata

import pandas as pd

from core import analytics as A
from core.pseudonymizer import amount_band


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def case_facts(case) -> dict:
    w, acc = case.window, case.account
    inn, out = w[w.receiver == acc], w[w.sender == acc]
    start = w.timestamp.min() if len(w) else None
    df = A.load_tx()
    cps = []
    for cp, g in w.assign(cp=w.sender.where(w.receiver == acc, w.receiver)).groupby("cp"):
        prev = 0
        if start is not None:
            prev = int((((df.sender == cp) | (df.receiver == cp)) & (df.timestamp < start)).sum())
        cps.append({"cuenta": case.pseudo.token(cp), "rol": "envía fondos" if (g.receiver == acc).all() else
                    "recibe fondos" if (g.sender == acc).all() else "envía y recibe",
                    "operaciones": len(g), "_monto": float(g.amount.sum()), "monto_total": amount_band(float(g.amount.sum())),
                    "primera_operacion": g.timestamp.min().strftime("%Y-%m-%d"),
                    "operaciones_previas_a_la_ventana": prev,
                    "pais": sorted(set(g.sender_loc.where(g.receiver == acc, g.receiver_loc)))})
    cps.sort(key=lambda c: -c["_monto"])
    for c in cps:
        c.pop("_monto")
    cash = w.payment_type.astype(str).str.contains("Cash|efectivo", case=False)
    intl = (w.sender_loc != w.receiver_loc) | w.payment_type.astype(str).str.contains("Cross|internacional", case=False)
    return {
        "cuenta": case.token,
        "ventana": {"desde": start.strftime("%Y-%m-%d") if start is not None else None, "operaciones": len(w),
                    "entradas": amount_band(float(inn.amount.sum())), "salidas": amount_band(float(out.amount.sum())),
                    "contrapartes_distintas": len(cps)},
        "contrapartes_principales": cps[:5],
        "efectivo": {"operaciones": int(cash.sum()), "pct": round(float(cash.mean()) * 100 if len(w) else 0, 1)},
        "exterior": {"operaciones": int(intl.sum()),
                     "paises": sorted((set(w[intl].sender_loc) | set(w[intl].receiver_loc)) - {"PE"})},
        "perfil": case.shared.get("perfil", {}),
        "riesgo": case.shared.get("riesgo"), "confianza": case.shared.get("confianza"), "nivel": case.shared.get("zona"),
        "senales": [f["claim"] for f in case.findings if f["kind"] == "riesgo" and f.get("estado") != "rechazado"][:6],
        "mitigantes": [f["claim"] for f in case.findings if f["kind"] == "mitigante"][:3],
        "documentos": [f["claim"] for f in case.findings if f["agent"] == "agente_extraccion"],
    }


def rule_answer(question: str, f: dict) -> str:
    """Respuesta determinista por tema (sin LLM), solo con hechos del caso."""
    q = _norm(question)
    cps = f["contrapartes_principales"]
    if any(k in q for k in ("contraparte", "historial", "quien", "origen", "envia", "deposit", "beneficiari", "destin")):
        if not cps:
            return "La cuenta no tiene contrapartes en la ventana analizada."
        c = cps[0]
        hist = (f"tiene {c['operaciones_previas_a_la_ventana']} operaciones previas en el banco"
                if c["operaciones_previas_a_la_ventana"] else "no tiene historial previo en el banco (aparece por primera vez en esta ventana)")
        resto = "; ".join(f"{x['cuenta']} ({x['rol']}, {x['operaciones']} op., {x['monto_total']})" for x in cps[1:4])
        ops = "una operación" if c["operaciones"] == 1 else f"{c['operaciones']} operaciones"
        return (f"La contraparte principal es {c['cuenta']}: {c['rol']}, {ops} por "
                f"{c['monto_total']} desde el {c['primera_operacion']} ({', '.join(c['pais'])}); {hist}."
                + (f" Le siguen: {resto}." if resto else ""))
    if "efectivo" in q or "ventanilla" in q or "cash" in q:
        e = f["efectivo"]
        return f"Hay {e['operaciones']} operaciones en efectivo ({e['pct']}% del total de {f['ventana']['operaciones']})."
    if any(k in q for k in ("exterior", "pais", "internacional", "extranjer", "jurisdic")):
        e = f["exterior"]
        n = "Una operación involucra" if e["operaciones"] == 1 else f"{e['operaciones']} operaciones involucran"
        return (f"{n} el exterior" + (f" ({', '.join(e['paises'])})." if e["paises"] else " (transferencia internacional).")
                if e["operaciones"] else "No hay operaciones con el exterior en la ventana analizada.")
    if any(k in q for k in ("perfil", "ingreso", "ocupacion", "kyc", "declar", "sueldo")):
        p = f["perfil"]
        if not p.get("disponible"):
            return "No hay perfil KYC registrado para este cliente."
        return (f"Perfil declarado: {p.get('ocupacion')} ({p.get('segmento')}), ingreso {p.get('ingreso_mensual_usd')} USD/mes, "
                f"{p.get('antiguedad_meses')} meses de antigüedad. Lo recibido en 30 días equivale a "
                f"{p.get('ingreso_30d_vs_declarado')} veces lo declarado.")
    if "document" in q or "sustento" in q or "contrato" in q or "factura" in q:
        return ("; ".join(f["documentos"]) if f["documentos"] else
                "No se ha adjuntado documentación de origen de fondos. Puedes subirla en 'Documento de origen de fondos'.")
    if any(k in q for k in ("por que", "riesgo", "motivo", "sospech", "señal", "senal", "critico")):
        s = f["senales"]
        return (f"Riesgo {f['riesgo']} y confianza {f['confianza']}. " +
                ("Señales principales: " + "; ".join(s[:3]) + "." if s else "No hay señales de riesgo vigentes.")
                + (f" Mitigante: {f['mitigantes'][0]}." if f["mitigantes"] else ""))
    v = f["ventana"]
    return (f"En la ventana analizada hay {v['operaciones']} operaciones con {v['contrapartes_distintas']} contrapartes "
            f"(entradas {v['entradas']}, salidas {v['salidas']}). Puedes preguntar por contrapartes, efectivo, exterior, "
            "perfil, documentos o el motivo del riesgo.")
