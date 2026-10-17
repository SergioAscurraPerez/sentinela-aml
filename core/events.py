"""Bitácora de todo lo que hacen los agentes y los humanos (SQLite + memoria)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sentinela.db"


class EventLog:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, alert_id TEXT, actor TEXT,
                kind TEXT, status TEXT, latency_ms INTEGER, detail TEXT)""")
        self.conn.commit()
        self.listeners: list[Callable[[dict], None]] = []
        self.current_alert: str | None = None

    def add(self, actor: str, kind: str, detail: dict[str, Any] | None = None,
            status: str = "ok", latency_ms: int | None = None,
            alert_id: str | None = None) -> dict:
        ev = {"ts": datetime.now().isoformat(timespec="seconds"),
              "alert_id": alert_id or self.current_alert or "-",
              "actor": actor, "kind": kind, "status": status,
              "latency_ms": latency_ms, "detail": detail or {}}
        with self._lock:
            self.conn.execute(
                "INSERT INTO events (ts, alert_id, actor, kind, status, latency_ms, detail) VALUES (?,?,?,?,?,?,?)",
                (ev["ts"], ev["alert_id"], actor, kind, status, latency_ms,
                 json.dumps(ev["detail"], ensure_ascii=False, default=str)))
            self.conn.commit()
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def list(self, alert_id: str | None = None) -> list[dict]:
        q = "SELECT ts, alert_id, actor, kind, status, latency_ms, detail FROM events"
        args: tuple = ()
        if alert_id:
            q += " WHERE alert_id = ?"
            args = (alert_id,)
        q += " ORDER BY id"
        with self._lock:
            rows = self.conn.execute(q, args).fetchall()
        return [{"ts": r[0], "alert_id": r[1], "actor": r[2], "kind": r[3], "status": r[4],
                 "latency_ms": r[5], "detail": json.loads(r[6] or "{}")} for r in rows]

    def clear(self, alert_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM events WHERE alert_id = ?", (alert_id,))
            self.conn.commit()


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = int((time.perf_counter() - self.t) * 1000)
