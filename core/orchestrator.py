"""Orquestador de Sentinela AML.

Flujo: alerta -> privacidad -> descubrimiento (registry NANDA) -> agentes -> firma + evidencia
       -> verificación -> riesgo ponderado por confianza -> redactor -> GATE HUMANO.
Solo un humano con rol autorizado puede decidir (descartar / vigilancia / escalar ROS).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from agents import ALL_AGENTS
from agents.base import Case

from . import analytics as A
from .events import EventLog, Timer
from .llm import LocalLLM
from .nanda_layer import BankTown, canonical_json
from .policy import load_policy
from .pseudonymizer import Pseudonymizer

DATA = Path(__file__).resolve().parent.parent / "data"
HUMANO = "oficial_cumplimiento"
ANALISTA = "analista_aml"
ROLES = {HUMANO: "Oficial de Cumplimiento", ANALISTA: "Analista AML"}
ZONAS = {"cierre_sugerido": "🟢 Cierre sugerido", "revision_humana": "🟡 Revisión humana",
         "escalamiento": "🔴 Escalamiento"}

# orden fijo de capacidades que el orquestador busca en el registro
PIPELINE = ["analisis_transaccional", "perfil_cliente", "scorecard_fraccionamiento", "anomalia_ml",
            "extraccion_documentos", "screening_listas", "normativa", "contraargumento"]


class Orchestrator:
    def __init__(self, log: EventLog | None = None):
        self.policy = load_policy()
        self.log = log or EventLog()
        self.town = BankTown(self.log)
        self.town.privacy.configure(self.policy["campos_privados"])
        self.llm = LocalLLM(self.policy, self.log)
        self.agents = {cls.name: cls() for cls in ALL_AGENTS}
        for ag in self.agents.values():  # orden fijo => llaves reproducibles para verificar expedientes
            self.town.register_agent(ag.name, ag.capabilities, {"descripcion": ag.description,
                                                                 "puede_decidir": False})
        self.town.identity.create(HUMANO)
        self.town.identity.create(ANALISTA)
        self.alerts = {a["alert_id"]: a for a in json.loads((DATA / "alerts.json").read_text(encoding="utf-8"))}
        self.cases: dict[str, Case] = {}
        self.decisions: dict[str, dict] = {}
        self.documents: dict[str, list[dict]] = {}   # documentos de origen de fondos por alerta

    # ------------------------------------------------------------------ investigación
    def investigate(self, alert_id: str, on_step: Callable[[str, str, list[dict]], None] | None = None) -> Case:
        alert = self.alerts[alert_id]
        self.log.clear(alert_id)
        self.log.current_alert = alert_id
        self.decisions.pop(alert_id, None)
        self.log.add("orquestador", "alerta.recibida", self.town.privacy.redact(alert))

        forbidden = [str(alert.get(k, "")) for k in self.policy["campos_privados"]] + [alert["account"]]
        w = A.account_window(alert["account"], alert["fecha"])
        nb = A.neighborhood(alert["account"], alert["fecha"])
        pol = copy.deepcopy(self.policy)
        pol["fx_a_usd"] = pol.get("fx_a_usd_por_moneda", {}).get(alert.get("moneda", "USD"), 1.0)
        case = Case(alert=alert, policy=pol, llm=self.llm, pseudo=Pseudonymizer(), window=w,
                    neighborhood=nb, forbidden=forbidden)
        case.raw["documentos"] = self.documents.get(alert_id, [])
        self.cases[alert_id] = case
        self.log.add("orquestador", "privacidad.pseudonimizacion",
                     {"campos_protegidos": self.policy["campos_privados"], "cuenta": case.token,
                      "transacciones_en_ventana": len(w), "aristas_vecindario": len(nb)})

        for cap in PIPELINE:
            cards = self.town.registry.lookup(cap)
            if not cards:
                self.log.add("orquestador", "registry.sin_agente", {"capacidad": cap}, status="alerta")
                continue
            card = cards[0]
            self.log.add("orquestador", "registry.descubrimiento",
                         {"capacidad": cap, "agente": card["name"], "agent_id": card["agent_id"],
                          "confianza": self.town.trust.score(card["name"])})
            self._run_agent(case, card["name"], on_step)

        self.score(case)
        self._run_agent(case, "agente_redactor", on_step)
        self.log.add("orquestador", "gate.humano",
                     {"estado": "pendiente_revision_humana", "riesgo": case.shared["riesgo"],
                      "sugerencia": self.suggestion(case)})
        return case

    def _run_agent(self, case: Case, name: str, on_step=None) -> None:
        ag = self.agents[name]
        self.log.add(name, "agente.inicio", {"capacidades": ag.capabilities})
        with Timer() as t:
            try:
                found = ag.run(case)
                status = "ok"
            except Exception as exc:  # un agente que falla no tumba el caso
                found, status = [], "error"
                self.log.add(name, "agente.error", {"error": str(exc)[:300]}, status="error")
        accepted = []
        for f in found:
            signed = self.town.sign_finding(name, f)
            if self.submit_finding(case.alert_id, signed):
                accepted.append(signed)
        self.log.add(name, "agente.fin", {"hallazgos": len(accepted)}, status=status, latency_ms=t.ms)
        if on_step:
            on_step(name, status, accepted)

    def submit_finding(self, alert_id: str, finding: dict) -> bool:
        """Toda entrada al expediente pasa por aquí: verificación de firma + registro de evidencia."""
        case = self.cases[alert_id]
        if not self.town.verify_finding(finding):
            self.log.add("orquestador", "hallazgo.rechazado",
                         {"motivo": "firma inválida", "dice_ser": finding.get("agent"),
                          "claim": finding.get("claim")}, status="alerta", alert_id=alert_id)
            return False
        finding = dict(finding)
        finding["fact_id"] = self.town.facts.attest(finding["agent"], alert_id, finding["typology"],
                                                    {"finding_id": finding["finding_id"],
                                                     "fingerprint": finding.get("fingerprint")})
        finding.setdefault("estado", "pendiente")
        case.findings.append(finding)
        self.log.add(finding["agent"], "hallazgo.firmado",
                     {"finding_id": finding["finding_id"], "kind": finding["kind"], "claim": finding["claim"],
                      "severidad": finding["severity"], "firma": finding["signature"][:16] + "…"},
                     alert_id=alert_id)
        return True

    # ------------------------------------------------------------------ riesgo
    def _w(self, agent: str) -> float:
        return max(0.4, min(1.6, 1 + 0.15 * self.town.trust.score(agent)))

    def score(self, case: Case) -> float:
        """Riesgo (qué tan sospechoso) y confianza (qué tan seguro está el sistema) -> zona de triaje."""
        pol = self.policy
        live = [f for f in case.findings if f.get("estado") != "rechazado"]
        risk_f = [f for f in live if f["kind"] == "riesgo"]
        mit_f = [f for f in live if f["kind"] == "mitigante"]
        combined = 1 - math.prod(1 - min(f["severity"] * self._w(f["agent"]), 0.99) for f in risk_f) if risk_f else 0.05
        mitig = max((min(f["severity"] * self._w(f["agent"]), 0.9) for f in mit_f), default=0.0)
        risk = round(combined * (1 - 0.6 * mitig), 2)

        # ---- confianza
        fh = pol.get("forzar_humano_si", {})
        conf, motivos, forzar = 0.92, [], []
        max_sev = max((f["severity"] for f in risk_f), default=0.0)
        if fh.get("desacuerdo_agentes") and max_sev >= 0.6 and mitig >= 0.4:
            conf -= 0.35
            forzar.append("Los agentes no están de acuerdo (riesgo alto vs. explicación legítima fuerte)")
        if len(risk_f) == 1 and 0.35 <= max_sev < 0.7:
            conf -= 0.12
            motivos.append("Una sola señal de riesgo, de intensidad media")
        n_tx = len(case.window)
        if n_tx < fh.get("pocas_transacciones", 5):
            conf -= 0.2
            forzar.append(f"Poco historial: {n_tx} transacciones en la ventana")
        perfil = case.shared.get("perfil", {})
        if not perfil.get("disponible", False):
            conf -= 0.1
            if fh.get("sin_perfil_kyc"):
                forzar.append("Sin perfil KYC del cliente")
        if case.raw.get("cliente_nuevo"):
            conf -= 0.15
            forzar.append(f"Cliente nuevo (< {fh.get('cliente_nuevo_meses', 6)} meses)")
        pct = case.shared.get("anomalia_percentil")
        nunca = fh.get("patron_nunca_visto_percentil")
        if nunca and pct is not None and pct >= nunca and not risk_f:
            conf -= 0.15
            forzar.append(f"Patrón nunca visto: el modelo ML lo ubica en el percentil {pct:.0f} y ninguna regla lo explica")
        pico_usd = A.peak_30d_inflow(case.window, case.account) * case.policy.get("fx_a_usd", 1.0)
        if pico_usd > fh.get("monto_mayor_a_usd", 1e18):
            forzar.append(f"Monto muy alto: {pico_usd:,.0f} USD en 30 días")
        used = {f["agent"] for f in risk_f + mit_f}
        if used:
            wmean = sum(self._w(a) for a in used) / len(used)
            if wmean < 1:
                conf *= wmean
                motivos.append("Agentes con confianza reducida por feedback humano")
        conf = round(max(0.05, min(conf, 0.99)), 2)

        # ---- triaje
        tz = pol.get("triaje", {})
        cs, esc = tz.get("cierre_sugerido", {}), tz.get("escalamiento", {})
        if risk >= esc.get("riesgo_min", 0.7):
            zona = "escalamiento"
            motivos.insert(0, f"Riesgo {risk:.2f} ≥ {esc.get('riesgo_min', 0.7)}")
        elif risk <= cs.get("riesgo_max", 0.25) and conf >= cs.get("confianza_min", 0.75) and not forzar:
            zona = "cierre_sugerido"
            motivos.insert(0, f"Riesgo bajo ({risk:.2f}) y confianza alta ({conf:.2f})")
            qa = tz.get("muestreo_qa_cierres", 0)
            if int(hashlib.sha256(("qa-v1:" + case.alert_id).encode()).hexdigest(), 16) % 100 < qa * 100:
                zona = "revision_humana"
                forzar.append(f"Muestreo de control de calidad ({qa:.0%} de los cierres sugeridos)")
        else:
            zona = "revision_humana"
            if not forzar:
                motivos.insert(0, f"Zona gris: riesgo {risk:.2f}, confianza {conf:.2f}")
        case.shared.update({"riesgo": risk, "confianza": conf, "zona": zona,
                            "motivos_triaje": forzar + motivos, "forzado_humano": bool(forzar)})
        self.log.add("orquestador", "triaje.calculado",
                     {"riesgo": risk, "confianza": conf, "zona": zona, "motivos": forzar + motivos,
                      "hallazgos_riesgo": len(risk_f), "mitigacion": round(mitig, 2),
                      "formula_riesgo": "1-Π(1-sev·w_confianza) × (1-0.6·mitigación)"}, alert_id=case.alert_id)
        return risk

    def suggestion(self, case: Case) -> str:
        z, r = case.shared.get("zona"), case.shared.get("riesgo", 0)
        if z == "cierre_sugerido":
            return "sugerir_descartar"
        if z == "escalamiento" or r >= 0.5:
            return "sugerir_escalar_ros"
        return "sugerir_vigilancia"

    # ------------------------------------------------------------------ human-in-the-loop
    def feedback(self, alert_id: str, finding_id: str, valido: bool, actor: str = HUMANO) -> int:
        case = self.cases[alert_id]
        f = next(x for x in case.findings if x["finding_id"] == finding_id)
        f["estado"] = "validado" if valido else "rechazado"
        receipt = self.town.facts.attest(actor, f["agent"], "feedback_humano",
                                         {"finding_id": finding_id, "valido": valido})
        score = self.town.trust.update(actor, f["agent"], "good" if valido else "bad", receipt)
        self.log.add(actor, "humano.feedback", {"finding_id": finding_id, "agente": f["agent"],
                                                "valido": valido, "nueva_confianza": score}, alert_id=alert_id)
        self.score(case)
        return score

    def ask(self, alert_id: str, agent: str, question: str) -> dict:
        case = self.cases[alert_id]
        self.log.add(HUMANO, "humano.pregunta", {"agente": agent, "pregunta": question}, alert_id=alert_id)
        f = self.town.sign_finding(agent, self.agents[agent].answer(case, question))
        self.submit_finding(alert_id, f)
        return f

    def redraft(self, alert_id: str) -> None:
        case = self.cases[alert_id]
        case.findings = [f for f in case.findings if f["kind"] != "borrador"]
        self._run_agent(case, "agente_redactor")

    def decide(self, alert_id: str, actor: str, role: str, action: str, justification: str,
               justification2: str | None = None) -> dict:
        pol = self.policy["autonomia"]
        case = self.cases[alert_id]
        if actor != HUMANO or role not in pol["roles_con_decision"]:
            self.log.add(actor, "permiso.denegado", {"accion": action, "rol": role,
                                                     "regla": "acciones_prohibidas_a_agentes"},
                         status="alerta", alert_id=alert_id)
            raise PermissionError(f"{actor} no tiene permiso para '{action}'")
        if not justification or len(justification.strip()) < 15:
            raise ValueError("La justificación es obligatoria (mín. 15 caracteres): la norma exige dejar constancia.")
        risk = case.shared.get("riesgo", 0)
        if action == "descartar" and risk > pol["doble_justificacion_si_riesgo_mayor_a"] and not justification2:
            raise ValueError("Riesgo alto: descartar exige una segunda justificación.")
        now = datetime.now()
        dec = {"alert_id": alert_id, "accion": action, "actor": actor, "rol": role, "riesgo": risk,
               "justificacion": justification, "justificacion_2": justification2,
               "sugerencia_sistema": self.suggestion(case), "ts": now.isoformat(timespec="seconds")}
        if action == "escalar_ros":
            dec["calificada_sospechosa_en"] = dec["ts"]
            dec["vence_ros"] = (now + timedelta(hours=self.policy["plazo_ros_horas"])).isoformat(timespec="seconds")
        dec["signature"] = self.town.auth.sign_as(actor, {k: v for k, v in dec.items() if k != "signature"})
        dec["fact_id"] = self.town.facts.attest(actor, alert_id, "decision", {"accion": action})
        self.decisions[alert_id] = dec
        self.log.add(actor, "humano.decision", {k: v for k, v in dec.items() if k != "signature"},
                     alert_id=alert_id)
        return dec

    def attach_document(self, alert_id: str, name: str, data: bytes, actor: str = ANALISTA) -> dict:
        """Recibe un documento de origen de fondos: huella sha256 + registro NANDA; si el caso existe, lo analiza."""
        from agents.extraccion import fingerprint
        doc = {"nombre": name, "data": data, "sha256": fingerprint(data), "bytes": len(data)}
        doc["fact_id"] = self.town.facts.attest("agente_extraccion", alert_id, "documento_recibido",
                                                {"nombre": name, "sha256": doc["sha256"]})
        self.documents.setdefault(alert_id, []).append(doc)
        self.log.add(actor, "documento.recibido", {"nombre": name, "sha256": doc["sha256"], "bytes": len(data)},
                     alert_id=alert_id)
        case = self.cases.get(alert_id)
        if case:
            case.raw["documentos"] = self.documents[alert_id]
            ag = self.agents["agente_extraccion"]
            self.log.add(ag.name, "agente.inicio", {"capacidades": ag.capabilities}, alert_id=alert_id)
            with Timer() as t:
                f = ag.analyze(case, doc)
            self.submit_finding(alert_id, self.town.sign_finding(ag.name, f))
            self.log.add(ag.name, "agente.fin", {"hallazgos": 1}, latency_ms=t.ms, alert_id=alert_id)
            self.score(case)
            self.redraft(alert_id)
        return doc

    def find_by_dni(self, dni: str) -> list[str]:
        """Consulta por DNI (vista del analista): devuelve las alertas del cliente; el DNI no se registra en claro."""
        digits = "".join(ch for ch in dni if ch.isdigit())
        if len(digits) < 6:
            return []
        hits = [a for a, al in self.alerts.items()
                if digits in "".join(ch for ch in str(al.get("documento_identidad", "")) if ch.isdigit())]
        self.log.add(ANALISTA, "consulta.dni", {"dni_sha256": hashlib.sha256(digits.encode()).hexdigest()[:16],
                                                "alertas": hits}, alert_id="-")
        return hits

    def reidentify(self, alert_id: str, actor: str = HUMANO) -> dict:
        if actor not in self.policy["autonomia"].get("roles_con_reidentificacion", [HUMANO]):
            self.log.add(actor, "permiso.denegado", {"accion": "reidentificar"}, status="alerta", alert_id=alert_id)
            raise PermissionError("Solo el Oficial de Cumplimiento puede ver los datos reales del cliente")
        a = self.alerts[alert_id]
        self.log.add(actor, "privacidad.reidentificacion", {"motivo": "consulta del oficial"},
                     status="alerta", alert_id=alert_id)
        return {k: a.get(k) for k in self.policy["campos_privados"] + ["account"] if k in a}

    # ------------------------------------------------------------------ lote / operación
    def investigate_all(self, on_progress: Callable[[int, int, str], None] | None = None) -> None:
        ids = [a for a in self.alerts if a not in self.cases]
        for i, aid in enumerate(ids, start=1):
            self.investigate(aid)
            if on_progress:
                on_progress(i, len(ids), aid)

    def add_live_alert(self, tipo: str, params: dict, ocupacion: str) -> str:
        """Crea una alerta nueva en vivo (transacciones nuevas) y la deja lista para investigar."""
        from .live import generate
        seq = 1 + sum(1 for a in self.alerts if a.startswith("L-"))
        df, alert = generate(tipo, params, ocupacion, seq)
        A.add_live_transactions(df)
        self.alerts[alert["alert_id"]] = alert
        self.log.add("jurado", "alerta.creada_en_vivo",
                     {"tipologia_elegida": tipo, "transacciones_nuevas": len(df), "parametros": params},
                     alert_id=alert["alert_id"])
        return alert["alert_id"]

    def zone_of(self, alert_id: str) -> str | None:
        c = self.cases.get(alert_id)
        return c.shared.get("zona") if c else None

    def approve_batch(self, actor: str, role: str) -> list[str]:
        """El oficial aprueba en lote los cierres sugeridos; cada uno queda con su sustento de no reporte firmado."""
        done = []
        for aid, c in self.cases.items():
            if c.shared.get("zona") != "cierre_sugerido" or aid in self.decisions:
                continue
            draft = next((f["reasoning"] for f in c.findings if f["kind"] == "borrador"), "")
            just = ("Aprobado en lote: cierre sugerido por riesgo bajo y confianza alta. Sustento de no reporte:\n"
                    + draft[:1500])
            self.decide(aid, actor, role, "descartar", just)
            done.append(aid)
        self.log.add(actor, "humano.aprobacion_lote", {"alertas": done}, alert_id="-")
        return done

    def reset(self) -> None:
        for aid in list(self.cases):
            self.log.clear(aid)
        self.log.clear("-")
        self.cases.clear()
        self.decisions.clear()

    # ------------------------------------------------------------------ expediente
    def export(self, alert_id: str) -> dict:
        case = self.cases[alert_id]
        exp = {
            "expediente": alert_id,
            "generado": datetime.now().isoformat(timespec="seconds"),
            "conservar_hasta": (datetime.now() + timedelta(days=365 * self.policy["conservacion_anios"])).date().isoformat(),
            "nanda_backend": self.town.backend,
            "alerta": self.town.privacy.redact(case.alert),
            "cuenta_pseudonimo": case.token,
            "riesgo": case.shared.get("riesgo"),
            "hallazgos": case.findings,
            "documentos": [{k: d[k] for k in ("nombre", "sha256", "bytes", "fact_id")}
                           for d in self.documents.get(alert_id, [])],
            "decision": self.decisions.get(alert_id),
            "bitacora": self.log.list(alert_id),
        }
        exp["sha256"] = hashlib.sha256(canonical_json({k: v for k, v in exp.items() if k != "sha256"}).encode()).hexdigest()
        (DATA / "expedientes").mkdir(exist_ok=True)
        (DATA / "expedientes" / f"{alert_id}.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1, default=str),
                                                               encoding="utf-8")
        return exp

    def verify(self, exp: dict) -> dict[str, Any]:
        body = {k: v for k, v in exp.items() if k != "sha256"}
        hash_ok = hashlib.sha256(canonical_json(body).encode()).hexdigest() == exp.get("sha256")
        res = []
        for f in exp["hallazgos"]:
            clean = {k: v for k, v in f.items() if k not in ("estado",)}
            res.append({"finding_id": f["finding_id"], "agente": f["agent"], "firma_ok": self.town.verify_finding(clean)})
        dec = exp.get("decision")
        dec_ok = None
        if dec:
            dec_ok = self.town.auth.verify(dec["actor"], {k: v for k, v in dec.items()
                                                          if k not in ("signature", "fact_id")}, dec["signature"])
        return {"hash_integro": hash_ok, "hallazgos": res, "firmas_ok": all(r["firma_ok"] for r in res),
                "decision_firmada_ok": dec_ok}
