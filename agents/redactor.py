from __future__ import annotations

from .base import Agent, Case


class AgenteRedactor(Agent):
    """Redacta borradores; nunca decide ni envía. No incluye la identidad del Oficial (deber de reserva)."""
    name = "agente_redactor"
    capabilities = ["redaccion", "borrador_ros", "sustento_no_reporte"]
    description = "Redacta el borrador de ROS o el sustento de no reporte para que el oficial lo revise y firme."

    def run(self, case: Case) -> list[dict]:
        risk = case.shared.get("riesgo", 0.0)
        risk_f = [f for f in case.findings if f["kind"] == "riesgo" and f.get("estado") != "rechazado"]
        mit = [f for f in case.findings if f["kind"] == "mitigante"]
        normas = [f["claim"] for f in case.findings if f["kind"] == "norma"]
        ros = risk >= 0.5
        tipo = "Borrador de ROS" if ros else "Sustento de no reporte"
        señales = "\n".join(f"- {f['claim']}" for f in risk_f) or "- Ninguna señal de riesgo vigente."
        mitig = "\n".join(f"- {f['claim']}" for f in mit) or "- Sin mitigantes relevantes."
        base_txt = (
            f"{tipo.upper()} — Alerta {case.alert_id} — Cuenta {case.token}\n\n"
            f"1. Señales de alerta y tipologías:\n{señales}\n\n"
            f"2. Mitigantes evaluados (abogado del diablo):\n{mitig}\n\n"
            f"3. Normas aplicables:\n" + ("\n".join(f"- {n}" for n in normas) or "- N/A") + "\n\n"
            f"4. Riesgo consolidado: {risk:.2f}\n"
            f"5. Recomendación: {'Calificar como sospechosa y comunicar a la UIF-Perú vía ROSEL dentro de 24 h desde la calificación.' if ros else 'No calificar como sospechosa; mantener constancia de este análisis por 10 años.'}\n\n"
            "Nota: borrador generado por agentes; requiere revisión y firma del Oficial de Cumplimiento. "
            "No comunicar al cliente (deber de reserva)."
        )
        payload = {"tipo": tipo, "riesgo": risk, "senales": [f["claim"] for f in risk_f],
                   "mitigantes": [f["claim"] for f in mit], "normas": normas}
        data, meta = self.ask_llm(case, f"Redacta un párrafo de 'Análisis' (4-6 frases, tono formal) para un {tipo} "
                                        "y un resumen ejecutivo de máximo 3 frases para el Oficial de Cumplimiento "
                                        "(qué pasó, por qué importa y qué se recomienda), basado SOLO en estos hallazgos. "
                                        'JSON: {"analisis": str, "resumen_ejecutivo": str}',
                                  payload, {"analisis": "", "resumen_ejecutivo": ""})
        case.shared["resumen_ejecutivo"] = data.get("resumen_ejecutivo") or ""
        txt = base_txt + ("\n\nAnálisis:\n" + data["analisis"] if data.get("analisis") else "")
        return [self.finding(case, tipo, "borrador", 0.0, [], f"{self.name} ({meta['modo']})", txt, kind="borrador",
                             extra={"tipo": "ros" if ros else "no_reporte", "modo_llm": meta["modo"]})]
