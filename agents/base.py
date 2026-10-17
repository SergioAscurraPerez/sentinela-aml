from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from core.llm import LocalLLM
from core.pseudonymizer import Pseudonymizer

SYSTEM_BASE = (
    "Eres un agente especialista en prevención de lavado de activos (AML) de un banco peruano. "
    "Trabajas SOLO con datos pseudonimizados (CTA_###) y montos en rangos. Nunca inventes transacciones ni "
    "datos que no estén en la entrada. No recomiendes contactar al cliente (deber de reserva). "
    "Responde SIEMPRE en español y SOLO con un objeto JSON válido."
)


@dataclass
class Case:
    alert: dict                      # alerta cruda (con PII) — nunca va al LLM
    policy: dict
    llm: LocalLLM
    pseudo: Pseudonymizer
    window: pd.DataFrame             # transacciones de la cuenta (crudas)
    neighborhood: pd.DataFrame       # vecindario a 2 saltos (crudo)
    forbidden: list[str]             # valores que nunca pueden salir
    shared: dict[str, Any] = field(default_factory=dict)  # rasgos ya pseudonimizados
    findings: list[dict] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)      # resultados crudos: NUNCA al LLM ni al expediente

    @property
    def alert_id(self) -> str:
        return self.alert["alert_id"]

    @property
    def account(self) -> str:
        return self.alert["account"]

    @property
    def token(self) -> str:
        return self.pseudo.token(self.account)


class Agent:
    name = "agente"
    capabilities: list[str] = []
    description = ""

    def run(self, case: Case) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def finding(self, case: Case, claim: str, typology: str, severity: float, evidence: list[str],
                source: str, reasoning: str, kind: str = "riesgo", extra: dict | None = None) -> dict:
        return {
            "finding_id": f"f-{uuid.uuid4().hex[:8]}",
            "alert_id": case.alert_id,
            "agent": self.name,
            "kind": kind,                      # riesgo | mitigante | norma | info | borrador | respuesta
            "claim": claim,
            "typology": typology,
            "severity": round(float(severity), 2),
            "evidence": [str(e) for e in evidence][:40],
            "source": source,
            "reasoning": reasoning,
            "extra": extra or {},
            "ts": datetime.now().isoformat(timespec="seconds"),
        }

    def ask_llm(self, case: Case, task: str, payload: dict, fallback: dict) -> tuple[dict, dict]:
        user = task + "\n\nDATOS (pseudonimizados):\n" + json.dumps(case.pseudo.scrub(payload), ensure_ascii=False,
                                                                     default=str, indent=1)
        return case.llm.json_chat(self.name, SYSTEM_BASE, user, fallback, case.forbidden)

    def answer(self, case: Case, question: str) -> dict:
        """Responde una pregunta del analista (human-in-the-loop: 'pedir más información') con hechos del caso."""
        from .consulta import case_facts, rule_answer
        facts = case_facts(case)
        fb = {"respuesta": rule_answer(question, facts), "confianza": 0.7}
        payload = {"pregunta_del_analista": question, "rol_del_agente": self.description, "hechos_del_caso": facts}
        data, meta = self.ask_llm(case, "Responde la pregunta del analista AML en 2-4 frases, desde tu especialidad, "
                                        "usando SOLO los hechos del caso (cita cuentas CTA_### y rangos tal como vienen). "
                                        "Si los hechos no alcanzan, dilo y sugiere qué revisar. "
                                        'JSON: {"respuesta": str, "confianza": 0-1}', payload, fb)
        return self.finding(case, f"Respuesta a: {question}", "consulta", 0.0, [], f"{self.name} ({meta['modo']})",
                            str(data.get("respuesta")), kind="respuesta",
                            extra={"confianza": data.get("confianza"), "modo_llm": meta["modo"], "pregunta": question})
