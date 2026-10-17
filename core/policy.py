from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parent / "policy.yaml"


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
