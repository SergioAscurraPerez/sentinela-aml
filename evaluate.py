"""Métrica de impacto (modo sombra): corre el pipeline sobre todas las alertas y compara con la etiqueta real.

    python evaluate.py            # modo reglas (rápido y reproducible); guarda data/metricas.json
    python evaluate.py --llm      # además hace razonar al LLM configurado (lento)

El riesgo, la confianza y la zona se calculan de forma determinista: el LLM solo explica y redacta,
por eso las métricas no cambian entre ambos modos.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.orchestrator import Orchestrator


def main() -> None:
    o = Orchestrator()
    if "--llm" not in sys.argv:
        o.llm.provider_cfg = "ninguno"
    rows, t0 = [], time.perf_counter()
    for aid, a in o.alerts.items():
        t = time.perf_counter()
        c = o.investigate(aid)
        rows.append({"alerta": aid, "real": a.get("etiqueta_real"), "riesgo": c.shared["riesgo"],
                     "confianza": c.shared["confianza"], "zona": c.shared["zona"], "seg": time.perf_counter() - t})
    for r in rows:
        print(f"{r['alerta']:7} real={str(r['real'])[:20]:<20} riesgo={r['riesgo']:.2f} conf={r['confianza']:.2f} {r['zona']:<16} {r['seg']:.2f}s")
    lav = [r for r in rows if r["real"] and r["real"] != "Normal"]
    nor = [r for r in rows if r["real"] == "Normal"]
    det = sum(r["zona"] != "cierre_sugerido" for r in lav)
    filt = sum(r["zona"] == "cierre_sugerido" for r in nor)
    esc = sum(r["zona"] == "escalamiento" for r in lav)
    m = {"fecha": datetime.now().isoformat(timespec="minutes"), "alertas": len(rows),
         "lavado_no_cerrado": f"{det}/{len(lav)}", "lavado_escalado": f"{esc}/{len(lav)}",
         "normales_a_cierre_sugerido": f"{filt}/{len(nor)}",
         "zonas": dict(Counter(r["zona"] for r in rows)),
         "seg_por_alerta": round(sum(r["seg"] for r in rows) / max(len(rows), 1), 2),
         "lavado_que_iria_a_cierre": [r["alerta"] + " " + str(r["real"]) for r in lav if r["zona"] == "cierre_sugerido"]}
    Path("data/metricas.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nAlertas: {len(rows)} · {m['seg_por_alerta']} s/alerta (total {time.perf_counter()-t0:.1f}s)")
    print(f"Lavado que NO se va a cierre sugerido: {det}/{len(lav)} · escalado directo: {esc}/{len(lav)}")
    print(f"Normales que van a cierre sugerido (ahorro): {filt}/{len(nor)} · Zonas: {m['zonas']}")
    if m["lavado_que_iria_a_cierre"]:
        print("Revisar (lavado que se iría a cierre):", ", ".join(m["lavado_que_iria_a_cierre"]))


if __name__ == "__main__":
    main()
