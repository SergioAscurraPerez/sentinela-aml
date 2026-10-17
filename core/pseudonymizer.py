"""Pseudonimización: el LLM nunca ve cuentas, nombres ni montos exactos.

- Cuentas -> tokens estables por caso (CTA_001, CTA_002, ...)
- Montos  -> rangos ("9.5k-10k")
- La tabla de re-identificación vive solo en memoria y solo la usa la UI del oficial.
"""
from __future__ import annotations

import re
from typing import Any


def amount_band(x: float) -> str:
    x = float(x)
    if x < 1000:
        step = 250
    elif x < 10_000:
        step = 500
    elif x < 100_000:
        step = 5_000
    else:
        step = 50_000
    lo = int(x // step * step)
    hi = lo + step

    def fmt(v: int) -> str:
        return f"{v/1000:g}k" if v >= 1000 else str(v)
    return f"{fmt(lo)}-{fmt(hi)}"


class Pseudonymizer:
    def __init__(self, prefix: str = "CTA"):
        self.prefix = prefix
        self.fwd: dict[str, str] = {}
        self.rev: dict[str, str] = {}

    def token(self, account: str) -> str:
        if account not in self.fwd:
            t = f"{self.prefix}_{len(self.fwd) + 1:03d}"
            self.fwd[account] = t
            self.rev[t] = account
        return self.fwd[account]

    def reidentify(self, token: str) -> str:
        return self.rev.get(token, token)

    def scrub(self, obj: Any) -> Any:
        """Reemplaza recursivamente cualquier cuenta conocida por su token."""
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub(v) for v in obj]
        if isinstance(obj, str):
            for acc, tok in sorted(self.fwd.items(), key=lambda kv: -len(kv[0])):
                obj = obj.replace(acc, tok)
            return obj
        return obj

    @staticmethod
    def leaks(text: str, forbidden: list[str]) -> list[str]:
        """Devuelve los valores prohibidos que aparecen en el texto (guardia antes de llamar al LLM)."""
        found = [v for v in forbidden if v and v in text]
        if re.search(r"ACC-[0-9A-Z]{3,}", text):
            found.append("patrón de cuenta ACC-*")
        return found
