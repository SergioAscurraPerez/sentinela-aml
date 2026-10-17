from __future__ import annotations

import json
from pathlib import Path

from .base import Agent, Case

NORMAS = Path(__file__).resolve().parent.parent / "data" / "normas.json"


class AgenteNormativo(Agent):
    """Citas exactas desde un corpus curado: no genera texto legal con el LLM (cero alucinación)."""
    name = "agente_normativo"
    capabilities = ["normativa", "citas_sbs"]
    description = "Asocia cada tipología con la norma SBS/UIF aplicable y la cita textual."

    def run(self, case: Case) -> list[dict]:
        normas = json.loads(NORMAS.read_text(encoding="utf-8"))["normas"]
        tips = set(case.shared.get("tipologias", []))
        out = []
        for n in normas:
            if "*" in n["tipologias"] and not tips:
                continue
            if tips & set(n["tipologias"]) or ("*" in n["tipologias"] and n["tema"] in ("plazo_ros", "sustento")):
                out.append(self.finding(case, f"{n['norma']}: {n['tema'].replace('_', ' ')}", "norma", 0.0,
                                        [n["id"]], n["fuente"], n["texto"], kind="norma"))
        rt = case.shared.get("rasgos_transaccionales", {}).get("tipologias", {})
        if rt.get("structuring", {}).get("supera_multiples_mes"):
            out.append(self.finding(case, "Las operaciones fraccionadas suman ≥ US$ 50,000 en el mes: "
                                          "correspondía Registro de Operaciones múltiples", "norma", 0.0,
                                    ["SBS-2660-2015-art49"], "Res. SBS 2660-2015, art. 49",
                                    "Umbral de operaciones múltiples (verificar vigencia).", kind="norma"))
        return out
