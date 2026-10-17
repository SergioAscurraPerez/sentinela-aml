from __future__ import annotations

from core import analytics as A

from .base import Agent, Case


class AbogadoDelDiablo(Agent):
    name = "abogado_del_diablo"
    capabilities = ["contraargumento", "falsos_positivos"]
    description = "Busca explicaciones legítimas antes de escalar, para reducir falsos positivos."

    def run(self, case: Case) -> list[dict]:
        sig = A.benign_signals(case.window, case.account)
        case.shared["senales_benignas"] = sig
        weight, razones = 0.0, []
        if sig.get("pct_pago_planilla", 0) >= 0.5:
            weight += 0.4
            razones.append(f"{sig['pct_pago_planilla']:.0%} de las salidas son pagos de planilla")
        if sig.get("recurrencia_destinatarios", 0) >= 0.6:
            weight += 0.25
            razones.append(f"{sig['recurrencia_destinatarios']:.0%} de los destinatarios se repiten mes a mes")
        if sig.get("n_salidas", 0) >= 5 and sig.get("dias_del_mes_distintos", 99) <= 3 and sig.get("montos_distintos", 99) <= 8:
            weight += 0.1
            razones.append("pagos concentrados en pocos días del mes y con montos estándar")
        if sig.get("pct_ingresos_efectivo", 0) >= 0.5:
            weight -= 0.15
            razones.append(f"pero {sig['pct_ingresos_efectivo']:.0%} de los ingresos son en efectivo")
        weight = max(0.0, min(weight, 0.8))

        fb = {"argumento": ("Explicación legítima plausible: " + "; ".join(razones)) if weight > 0.2
              else "No encuentro una explicación legítima convincente para el patrón.",
              "preguntas_para_el_analista": ["¿La actividad declarada del cliente explica este volumen?",
                                             "¿Existe sustento documental (contratos, planillas, facturas)?"]}
        payload = {"cuenta": case.token, "tipologias": case.shared.get("tipologias"), "senales_benignas": sig,
                   "rasgos": case.shared.get("rasgos_transaccionales", {}).get("tipologias")}
        data, meta = self.ask_llm(case, "Actúa como abogado del diablo: busca la explicación LEGÍTIMA más fuerte "
                                        "para el patrón y lista qué debería verificar el analista. "
                                        'JSON: {"argumento": str, "preguntas_para_el_analista": [str]}',
                                  payload, fb)
        kind = "mitigante" if weight > 0.2 else "info"
        return [self.finding(case, "Contraargumento: " + ("; ".join(razones) if razones else "sin mitigantes fuertes"),
                             "mitigacion", weight, [], f"Señales benignas + {meta['modo']}",
                             str(data.get("argumento")), kind=kind,
                             extra={"preguntas": data.get("preguntas_para_el_analista"), "senales": sig,
                                    "modo_llm": meta["modo"]})]
