"""Métrica de impacto: corre el pipeline sobre todas las alertas y compara con la etiqueta real.

    python evaluate.py            # usa data/alerts.json (sintético + SAML-D/IBM si lo preparaste)
"""
from __future__ import annotations

import time

from core.orchestrator import Orchestrator


def main() -> None:
    o = Orchestrator()
    rows, t0 = [], time.perf_counter()
    for aid, a in o.alerts.items():
        t = time.perf_counter()
        c = o.investigate(aid)
        sug = o.suggestion(c)
        rows.append((aid, a.get("etiqueta_real"), c.shared["riesgo"], sug, time.perf_counter() - t))
    tp = sum(1 for r in rows if r[1] != "Normal" and r[3] != "sugerir_descartar")
    fn = sum(1 for r in rows if r[1] != "Normal" and r[3] == "sugerir_descartar")
    tn = sum(1 for r in rows if r[1] == "Normal" and r[3] == "sugerir_descartar")
    fp = sum(1 for r in rows if r[1] == "Normal" and r[3] != "sugerir_descartar")
    for r in rows:
        print(f"{r[0]:8} real={r[1]:<18} riesgo={r[2]:.2f} {r[3]:<20} {r[4]:.2f}s")
    n = len(rows)
    print(f"\nAlertas: {n} · tiempo medio {sum(r[4] for r in rows)/n:.2f}s/alerta (total {time.perf_counter()-t0:.1f}s)")
    print(f"Recall lavado (no descartadas): {tp}/{tp+fn} · Falsos positivos filtrados: {tn}/{tn+fp}")


if __name__ == "__main__":
    main()
