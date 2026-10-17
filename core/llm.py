"""Cliente LLM local (Ollama). Ningún dato sale de la máquina del banco.

- Guardia de privacidad: si el prompt contiene un valor prohibido, NO se envía.
- Caché en disco: la demo es reproducible y no depende de la velocidad del modelo.
- Respaldo determinista: si Ollama no está disponible, el agente usa su plantilla de reglas
  y la UI lo indica ("modo reglas").
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

from .events import EventLog
from .pseudonymizer import Pseudonymizer

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "llm_cache.json"


class LocalLLM:
    def __init__(self, policy: dict, log: EventLog):
        cfg = policy.get("llm", {})
        self.provider = cfg.get("proveedor", "ollama")
        self.url = cfg.get("url", "http://localhost:11434").rstrip("/")
        self.model = cfg.get("modelo", "qwen2.5:7b")
        self.timeout = cfg.get("timeout_s", 60)
        self.use_cache = cfg.get("usar_cache", True)
        self.log = log
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self._available: bool | None = None

    def available(self, refresh: bool = False) -> bool:
        if self.provider != "ollama":
            return False
        if self._available is None or refresh:
            try:
                r = requests.get(f"{self.url}/api/tags", timeout=2)
                names = [m.get("name", "") for m in r.json().get("models", [])]
                self._available = r.ok and any(n.startswith(self.model.split(":")[0]) for n in names)
            except Exception:
                self._available = False
        return bool(self._available)

    def json_chat(self, agent: str, system: str, user: str, fallback: dict,
                  forbidden: list[str] | None = None) -> tuple[dict, dict]:
        """Devuelve (respuesta_json, meta). meta.modo in {llm, cache, reglas, bloqueado}."""
        leaks = Pseudonymizer.leaks(system + user, forbidden or [])
        if leaks:
            self.log.add(agent, "privacy.bloqueado", {"motivo": "valores prohibidos en el prompt",
                                                        "n_valores": len(leaks)}, status="alerta")
            return fallback, {"modo": "bloqueado"}

        key = hashlib.sha256(f"{self.model}|{system}|{user}".encode()).hexdigest()
        seen = {"system": system, "user": user, "modelo": self.model}
        if self.use_cache and key in self._cache:
            self.log.add(agent, "llm.llamada", {"modo": "cache", "lo_que_vio_el_modelo": seen})
            return self._cache[key], {"modo": "cache"}

        if not self.available():
            self.log.add(agent, "llm.llamada", {"modo": "reglas", "lo_que_vio_el_modelo": seen,
                                                "nota": "Ollama no disponible: plantilla determinista"})
            return fallback, {"modo": "reglas"}

        t = time.perf_counter()
        try:
            r = requests.post(f"{self.url}/api/chat", timeout=self.timeout, json={
                "model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0.1},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            data = json.loads(r.json()["message"]["content"])
            ms = int((time.perf_counter() - t) * 1000)
            data = {**fallback, **{k: v for k, v in data.items() if v not in (None, "", [])}}
            self._cache[key] = data
            CACHE.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
            self.log.add(agent, "llm.llamada", {"modo": "llm", "lo_que_vio_el_modelo": seen,
                                                "respuesta": data}, latency_ms=ms)
            return data, {"modo": "llm", "latency_ms": ms}
        except Exception as exc:
            self.log.add(agent, "llm.error", {"error": str(exc)[:200], "lo_que_vio_el_modelo": seen},
                         status="error")
            return fallback, {"modo": "reglas"}
