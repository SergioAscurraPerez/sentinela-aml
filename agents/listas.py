from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import Agent, Case

LISTAS = Path(__file__).resolve().parent.parent / "data" / "listas_sinteticas.csv"
JURIS = Path(__file__).resolve().parent.parent / "data" / "jurisdicciones.csv"
SEV = {"sanciones_ficticia": 0.9, "pep_relacionado": 0.6, "interna_alto_riesgo": 0.55}


class AgenteListas(Agent):
    name = "agente_listas"
    capabilities = ["screening_listas", "pep", "sanciones"]
    description = "Cruza la cuenta y sus contrapartes contra listas de riesgo (PEP, sanciones, lista interna)."

    def run(self, case: Case) -> list[dict]:
        listas = pd.read_csv(LISTAS)
        direct = set(case.window.sender) | set(case.window.receiver) | case.raw.get("miembros_ciclo", set())
        involved = set(case.neighborhood.sender) | set(case.neighborhood.receiver) | {case.account}
        hits = listas[listas.entidad.isin(involved)]
        out = []
        for r in hits.itertuples():
            tok = case.pseudo.token(r.entidad)
            if r.entidad == case.account:
                rol, factor, kind = "titular", 1.0, "riesgo"
            elif r.entidad in direct:
                rol, factor, kind = "contraparte directa", 0.6, "riesgo"
            else:
                rol, factor, kind = "contraparte a 2 saltos", 0.0, "info"
            out.append(self.finding(case, f"Coincidencia en lista '{r.tipo_lista}' ({rol} {tok})", "lista",
                                    SEV.get(r.tipo_lista, 0.5) * factor, [tok], "Listas de riesgo (sintéticas)",
                                    str(r.motivo), kind=kind))
        out += self._jurisdicciones(case)
        case.shared["listas"] = [f"{f['claim']}" for f in out]
        if not out:
            out.append(self.finding(case, f"Sin coincidencias en listas para {case.token} y {len(involved)-1} contrapartes",
                                    "lista", 0.0, [], "Listas de riesgo (sintéticas)",
                                    "No hay coincidencias exactas.", kind="info"))
        return out

    def _jurisdicciones(self, case: Case) -> list[dict]:
        """Contrapartes directas en jurisdicciones de mayor riesgo (sender_loc / receiver_loc)."""
        if not JURIS.exists():
            return []
        j = pd.read_csv(JURIS)
        risk = dict(zip(j.pais, zip(j.lista, j.severidad, j.fuente)))
        w, acc = case.window, case.account
        locs = pd.concat([w[w.receiver == acc].sender_loc, w[w.sender == acc].receiver_loc])
        out = []
        for pais, n in locs.value_counts().items():
            if pais not in risk:
                continue
            lista, sev, fuente = risk[pais]
            ev = w[((w.receiver == acc) & (w.sender_loc == pais)) | ((w.sender == acc) & (w.receiver_loc == pais))].tx_id
            out.append(self.finding(case, f"Jurisdicción de mayor riesgo: {n} operaciones con contrapartes en {pais} "
                                          f"({lista})", "jurisdiccion", float(sev), list(ev), fuente,
                                    "Operaciones con jurisdicciones de mayor riesgo requieren debida diligencia reforzada. "
                                    "Verificar la vigencia de la lista antes de usarla en producción.",
                                    kind="riesgo" if "GAFI" in lista else "info"))
        return out
