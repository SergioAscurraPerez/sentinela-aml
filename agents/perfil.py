"""Agente de perfil del cliente (KYC): ¿el movimiento cuadra con quien dice ser el cliente?"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core import analytics as A
from core.pseudonymizer import amount_band

from .base import Agent, Case

CLIENTES = Path(__file__).resolve().parent.parent / "data" / "clientes.csv"


class AgentePerfil(Agent):
    name = "agente_perfil"
    capabilities = ["perfil_cliente", "kyc"]
    description = "Compara lo que mueve la cuenta con el perfil declarado (ocupación, ingreso, antigüedad)."

    def run(self, case: Case) -> list[dict]:
        pol = case.policy
        fx = pol.get("fx_a_usd", 1.0)
        prof = dict(case.alert["kyc"], cuenta=case.account) if case.alert.get("kyc") else None
        if prof is None and CLIENTES.exists():
            df = pd.read_csv(CLIENTES)
            hit = df[df.cuenta == case.account]
            prof = hit.iloc[0].to_dict() if len(hit) else None
        if prof is None:
            case.shared["perfil"] = {"disponible": False}
            return [self.finding(case, f"Sin perfil KYC para {case.token}: no se puede contrastar con lo declarado",
                                 "perfil", 0.0, [], "Core / KYC", "Falta información del cliente.", kind="info",
                                 extra={"sin_perfil": True})]

        ingreso = float(prof["ingreso_mensual_usd"])
        case.raw["ingreso_mensual_usd"] = ingreso
        pico_usd = A.peak_30d_inflow(case.window, case.account) * fx
        ratio = pico_usd / max(ingreso, 1.0)
        nuevo = int(prof["antiguedad_meses"]) < pol.get("forzar_humano_si", {}).get("cliente_nuevo_meses", 6)
        case.shared["perfil"] = {"disponible": True, "segmento": prof["segmento"], "ocupacion": prof["ocupacion"],
                                 "ingreso_mensual_usd": amount_band(ingreso), "antiguedad_meses": int(prof["antiguedad_meses"]),
                                 "ingreso_30d_vs_declarado": round(ratio, 1), "cliente_nuevo": nuevo}
        case.raw["cliente_nuevo"] = nuevo
        out = []
        estudiante = "estudiante" in str(prof["ocupacion"]).lower()
        limite_est = pol.get("perfil", {}).get("estudiante_max_mensual_usd", 13_500)
        if estudiante and pico_usd > limite_est:
            sev = 0.75
        elif ratio >= 10:
            sev = 0.7
        elif ratio >= 3:
            sev = 0.5
        else:
            sev = 0.0
        claim = (f"Perfil vs monto: {prof['ocupacion']} con ingreso declarado {amount_band(ingreso)} USD/mes "
                 f"recibió {amount_band(pico_usd)} USD en 30 días ({ratio:.1f}× lo declarado)")
        if sev > 0:
            case.shared.setdefault("tipologias", []).append("perfil_vs_monto")
            out.append(self.finding(case, claim, "perfil_vs_monto", sev, [], "Core / KYC + transacciones",
                                    "El volumen no guarda relación con la actividad económica declarada "
                                    "(definición de operación inusual, Ley 27693 art. 11).",
                                    extra={"ratio": round(ratio, 1), "ocupacion": prof["ocupacion"]}))
        else:
            out.append(self.finding(case, claim + ": consistente con el perfil", "perfil_vs_monto", 0.0, [],
                                    "Core / KYC + transacciones", "El volumen es compatible con lo declarado.",
                                    kind="info"))
        if nuevo:
            out.append(self.finding(case, f"Cliente nuevo ({int(prof['antiguedad_meses'])} meses): poco historial para comparar",
                                    "cliente_nuevo", 0.0, [], "Core / KYC",
                                    "Con poco historial la confianza del sistema baja y decide un humano.", kind="info",
                                    extra={"cliente_nuevo": True}))
        return out
