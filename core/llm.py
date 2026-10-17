"""Cliente LLM: Ollama (local, ningún dato sale de la máquina) u OpenRouter (nube, solo rasgos pseudonimizados).

- Proveedor: policy.yaml llm.proveedor = auto | ollama | openrouter | ninguno.
  "auto" usa Ollama si está corriendo y, si no, OpenRouter si hay clave.
- Clave de OpenRouter: variable de entorno OPENROUTER_API_KEY o .streamlit/secrets.toml (fuera de git).
- Guardia de privacidad: si el prompt contiene un valor prohibido, NO se envía a ningún proveedor.
- Caché en disco: la demo es reproducible y no depende de la velocidad del modelo.
- Respaldo determinista: si no hay LLM disponible, el agente usa su plantilla de reglas
  y la UI lo indica ("modo reglas").
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from .events import EventLog
from .pseudonymizer import Pseudonymizer

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "llm_cache.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1"


def _secret(name: str) -> str:
    if os.getenv(name):
        return os.environ[name]
    try:
        import streamlit as st
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def _parse_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])


class LocalLLM:
    def __init__(self, policy: dict, log: EventLog):
        cfg = policy.get("llm", {})
        self.provider_cfg = cfg.get("proveedor", "auto")
        self.url = cfg.get("url", "http://localhost:11434").rstrip("/")
        self.ollama_model = cfg.get("modelo", "qwen2.5:3b")
        self.openrouter_model = cfg.get("modelo_openrouter", "google/gemini-2.5-flash-lite")
        self.timeout = cfg.get("timeout_s", 60)
        self.use_cache = cfg.get("usar_cache", True)
        self.log = log
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self._ollama_ok: bool | None = None

    # ------------------------------------------------------------ disponibilidad
    def _ollama_available(self, refresh: bool) -> bool:
        if self._ollama_ok is None or refresh:
            try:
                r = requests.get(f"{self.url}/api/tags", timeout=2)
                names = [m.get("name", "") for m in r.json().get("models", [])]
                self._ollama_ok = r.ok and any(n.startswith(self.ollama_model.split(":")[0]) for n in names)
            except Exception:
                self._ollama_ok = False
        return bool(self._ollama_ok)

    def active(self, refresh: bool = False) -> str | None:
        """Proveedor que se usará ahora: 'ollama', 'openrouter' o None (modo reglas)."""
        p = self.provider_cfg
        if p in ("ollama", "auto") and self._ollama_available(refresh):
            return "ollama"
        if p in ("openrouter", "auto") and _secret("OPENROUTER_API_KEY"):
            return "openrouter"
        return None

    def available(self, refresh: bool = False) -> bool:
        return self.active(refresh) is not None

    @property
    def model(self) -> str:
        return self.openrouter_model if self.active() == "openrouter" else self.ollama_model

    @property
    def label(self) -> str:
        prov = self.active()
        return {"ollama": f"local · {self.ollama_model}",
                "openrouter": f"OpenRouter · {self.openrouter_model}"}.get(prov or "", "modo reglas")

    # ------------------------------------------------------------ llamadas
    def _call_ollama(self, system: str, user: str) -> dict:
        r = requests.post(f"{self.url}/api/chat", timeout=self.timeout, json={
            "model": self.ollama_model, "stream": False, "format": "json",
            "options": {"temperature": 0.1},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return _parse_json(r.json()["message"]["content"])

    def _call_openrouter(self, system: str, user: str) -> dict:
        r = requests.post(f"{OPENROUTER_URL}/chat/completions", timeout=self.timeout, headers={
            "Authorization": f"Bearer {_secret('OPENROUTER_API_KEY')}",
            "X-Title": "Sentinela AML"}, json={
            "model": self.openrouter_model, "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\nResponde SOLO con un objeto JSON válido."},
                         {"role": "user", "content": user}]})
        r.raise_for_status()
        return _parse_json(r.json()["choices"][0]["message"]["content"])

    def json_chat(self, agent: str, system: str, user: str, fallback: dict,
                  forbidden: list[str] | None = None) -> tuple[dict, dict]:
        """Devuelve (respuesta_json, meta). meta.modo in {llm, cache, reglas, bloqueado}."""
        leaks = Pseudonymizer.leaks(system + user, forbidden or [])
        if leaks:
            self.log.add(agent, "privacy.bloqueado", {"motivo": "valores prohibidos en el prompt",
                                                        "n_valores": len(leaks)}, status="alerta")
            return fallback, {"modo": "bloqueado"}

        prov = self.active()
        model = self.model
        key = hashlib.sha256(f"{model}|{system}|{user}".encode()).hexdigest()
        seen = {"system": system, "user": user, "modelo": model, "proveedor": prov}
        if self.use_cache and key in self._cache:
            self.log.add(agent, "llm.llamada", {"modo": "cache", "lo_que_vio_el_modelo": seen})
            return self._cache[key], {"modo": "cache"}

        if prov is None:
            self.log.add(agent, "llm.llamada", {"modo": "reglas", "lo_que_vio_el_modelo": seen,
                                                "nota": "Sin LLM disponible: plantilla determinista"})
            return fallback, {"modo": "reglas"}

        t = time.perf_counter()
        try:
            data = self._call_ollama(system, user) if prov == "ollama" else self._call_openrouter(system, user)
            ms = int((time.perf_counter() - t) * 1000)
            data = {**fallback, **{k: v for k, v in data.items() if v not in (None, "", [])}}
            self._cache[key] = data
            CACHE.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
            self.log.add(agent, "llm.llamada", {"modo": "llm", "proveedor": prov, "lo_que_vio_el_modelo": seen,
                                                "respuesta": data}, latency_ms=ms)
            return data, {"modo": "llm", "proveedor": prov, "latency_ms": ms}
        except Exception as exc:
            self.log.add(agent, "llm.error", {"error": str(exc)[:200], "proveedor": prov,
                                              "lo_que_vio_el_modelo": seen}, status="error")
            return fallback, {"modo": "reglas"}
