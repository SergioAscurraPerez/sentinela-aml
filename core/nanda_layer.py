"""Puente con Project NANDA (nandatown).

Usamos 6 de las 12 capas de protocolo del Lab de nandatown como librería:
  identity   -> identidad y tarjeta (AgentFacts) de cada agente
  registry   -> índice: el orquestador descubre agentes por capacidad
  auth       -> firma HMAC de tarjetas y hallazgos; remitentes falsos fallan
  data_facts -> registros de evidencia firmados (un hecho, un observador, un sujeto)
  trust      -> reputación de cada agente según el feedback del oficial humano
  privacy    -> campos que nunca salen (redacción)

Las capas esperan un `engine` con: rng, emit(), layers, now. BankTown lo implementa
y manda cada emit() a nuestra bitácora, así todo lo que hace NANDA queda trazado.

Si nandatown no está instalado, se usa un respaldo local con la misma API (avisado en la UI).
NOTA: las llaves se derivan de una semilla persistida para poder re-verificar expedientes;
es aceptable para una demo, no para producción (ahí: HSM / KMS del banco).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import secrets
import time
from pathlib import Path
from typing import Any

from .events import EventLog

SEED_FILE = Path(__file__).resolve().parent.parent / "data" / ".town_seed"
LAYERS = ["identity", "auth", "registry", "trust", "data_facts", "privacy"]

try:
    from nandatown.layers import DEFAULT_PLUGINS, resolve  # type: ignore
    from nandatown.records import canonical_json, fingerprint  # type: ignore
    BACKEND = "nandatown"
except Exception:  # pragma: no cover - respaldo
    BACKEND = "fallback"

    def canonical_json(obj: Any) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

    def fingerprint(obj: Any) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def _seed() -> int:
    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SEED_FILE.exists():
        SEED_FILE.write_text(str(secrets.randbits(64)))
    return int(SEED_FILE.read_text().strip())


class BankTown:
    """Engine mínimo para hospedar las capas de nandatown dentro del banco."""

    def __init__(self, log: EventLog, seed: int | None = None):
        self.log = log
        self.rng = random.Random(_seed() if seed is None else seed)
        self.layers: dict[str, Any] = {}
        self.backend = BACKEND
        for layer in LAYERS:
            if BACKEND == "nandatown":
                cls = resolve(layer, DEFAULT_PLUGINS[layer])
            else:
                cls = _FALLBACK[layer]
            self.layers[layer] = cls(self)

    @property
    def now(self) -> float:
        return time.time()

    def emit(self, observer: str, kind: str, subject: str, data: dict | None = None) -> None:
        self.log.add(actor=observer, kind=f"nanda.{kind}",
                     detail={"subject": subject, **(data or {})},
                     status="alerta" if kind in ("signature_invalid", "card_unverified") else "ok")

    # --- atajos -----------------------------------------------------------------
    @property
    def identity(self):
        return self.layers["identity"]

    @property
    def auth(self):
        return self.layers["auth"]

    @property
    def registry(self):
        return self.layers["registry"]

    @property
    def trust(self):
        return self.layers["trust"]

    @property
    def facts(self):
        return self.layers["data_facts"]

    @property
    def privacy(self):
        return self.layers["privacy"]

    def register_agent(self, name: str, capabilities: list[str], facts: dict) -> dict:
        card = self.identity.card(name, capabilities, facts)
        sig = self.auth.sign_as(name, card)
        self.registry.publish(name, card, sig)
        return card

    def sign_finding(self, agent: str, finding: dict) -> dict:
        payload = {k: v for k, v in finding.items() if k not in ("signature", "fingerprint", "fact_id", "estado")}
        out = dict(finding)
        out["signature"] = self.auth.sign_as(agent, payload)
        out["fingerprint"] = fingerprint(payload)
        return out

    def verify_finding(self, finding: dict) -> bool:
        payload = {k: v for k, v in finding.items() if k not in ("signature", "fingerprint", "fact_id", "estado")}
        return bool(finding.get("signature")) and self.auth.verify(
            finding["agent"], payload, finding["signature"], subject=finding.get("finding_id", ""))


# ---------------------------------------------------------------------------------
# Respaldo local con la misma API que las capas de nandatown (solo si no se puede importar)
class _Identity:
    def __init__(self, engine):
        self.engine, self.ids, self._seq = engine, {}, 0

    def create(self, name):
        if name not in self.ids:
            self._seq += 1
            self.ids[name] = {"name": name, "agent_id": f"aid-{self._seq}",
                              "key": "".join(self.engine.rng.choices("0123456789abcdef", k=32))}
            self.engine.emit("town", "identity_created", name, {"agent_id": self.ids[name]["agent_id"]})
        return self.ids[name]

    def key(self, name):
        return self.ids.get(name, {}).get("key")

    def card(self, name, capabilities, facts):
        i = self.create(name)
        return {"name": name, "agent_id": i["agent_id"], "capabilities": capabilities, "facts": facts}


class _Auth:
    def __init__(self, engine):
        self.engine = engine

    def sign_as(self, name, payload):
        key = self.engine.layers["identity"].key(name)
        if key is None:
            raise KeyError(name)
        return hmac.new(key.encode(), canonical_json(payload).encode(), hashlib.sha256).hexdigest()

    def verify(self, claimed, payload, signature, subject=""):
        key = self.engine.layers["identity"].key(claimed)
        ok = key is not None and hmac.compare_digest(
            hmac.new(key.encode(), canonical_json(payload).encode(), hashlib.sha256).hexdigest(), signature)
        if not ok:
            self.engine.emit("town", "signature_invalid", subject or claimed, {"claimed": claimed})
        return ok


class _Registry:
    def __init__(self, engine):
        self.engine, self.cards = engine, {}

    def publish(self, publisher, card, signature):
        ok = self.engine.layers["auth"].verify(card["name"], card, signature, subject=card["name"])
        self.cards[card["name"]] = {"card": card, "verified": ok, "publisher": publisher}
        self.engine.emit("town", "card_registered" if ok else "card_unverified", card["name"],
                         {"capabilities": card["capabilities"]})
        return ok

    def lookup(self, capability, include_unverified=False):
        t = self.engine.layers["trust"]
        hits = [e["card"] for e in self.cards.values()
                if capability in e["card"]["capabilities"] and (e["verified"] or include_unverified)]
        return sorted(hits, key=lambda c: (-t.score(c["name"]), c["name"]))


class _Trust:
    def __init__(self, engine):
        self.engine, self.scores = engine, {}

    def score(self, name):
        return self.scores.get(name, 0)

    def update(self, observer, subject, outcome, receipt_id):
        d = 1 if outcome == "good" else -1
        self.scores[subject] = self.score(subject) + d
        self.engine.emit(observer, "reputation_updated", subject,
                         {"outcome": outcome, "delta": d, "score": self.scores[subject], "receipt": receipt_id})
        return self.scores[subject]


class _Facts:
    def __init__(self, engine):
        self.engine, self.records, self._seq = engine, [], 0

    def attest(self, observer, subject, claim, value):
        if observer == subject:
            raise ValueError("a subject cannot write its own record")
        self._seq += 1
        payload = {"record_id": f"fact-{self._seq}", "observer": observer, "subject": subject,
                   "claim": claim, "value": value, "at": self.engine.now}
        rec = dict(payload, signature=self.engine.layers["auth"].sign_as(observer, payload),
                   content_fingerprint=fingerprint(payload))
        self.records.append(rec)
        self.engine.emit(observer, "receipt_attested", subject, {"record_id": payload["record_id"], "claim": claim})
        return payload["record_id"]

    def about(self, subject):
        return [r for r in self.records if r["subject"] == subject]


class _Privacy:
    def __init__(self, engine):
        self.engine, self.fields = engine, set()

    def configure(self, fields):
        self.fields = set(fields)

    def redact(self, record):
        def scrub(o):
            if isinstance(o, dict):
                return {k: ("[redacted]" if k in self.fields else scrub(v)) for k, v in o.items()}
            if isinstance(o, list):
                return [scrub(v) for v in o]
            return o
        return scrub(record) if self.fields else record


_FALLBACK = {"identity": _Identity, "auth": _Auth, "registry": _Registry,
             "trust": _Trust, "data_facts": _Facts, "privacy": _Privacy}
