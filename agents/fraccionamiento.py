"""Ficha de fraccionamiento (pitufeo): 8 parámetros ponderados en una ventana de 30 días.

Los números los calcula el código; el LLM no recalcula nada. Pesos y umbrales en policy.yaml (fraccionamiento).
Triaje de la ficha: 0-2 pasa · 3-5 revisión manual · 6+ crítico · datos incompletos = revisión manual siempre.
"""
from __future__ import annotations

import pandas as pd

from core.pseudonymizer import amount_band

from .base import Agent, Case

PARAMS = {
    "cerca_umbral": "Depósitos cerca del umbral",
    "suma_ventana": "Suma en la ventana supera el umbral sin que ningún depósito lo supere",
    "depositantes_distintos": "Depositantes distintos",
    "canales_mismo_dia": "Canales distintos el mismo día",
    "dias_consecutivos": "Días consecutivos con depósitos",
    "montos_repetidos": "Montos repetidos o redondos",
    "relacion_ingreso": "Recibido vs ingreso declarado",
    "salida_rapida": "Salida rápida del dinero (48 h)",
}


def _max_consecutive(days: pd.Series) -> int:
    d = sorted(set(days))
    best = run = 1 if d else 0
    for a, b in zip(d, d[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        best = max(best, run)
    return best


def scorecard(w: pd.DataFrame, acc: str, alert_date: str, pol: dict, ingreso_usd: float | None) -> dict:
    cfg = pol.get("fraccionamiento", {})
    fx = pol.get("fx_a_usd", 1.0)
    umbral = pol["umbrales_registro"]["operacion_unica_usd"] / fx
    end = pd.Timestamp(alert_date) + pd.Timedelta(days=1)
    w30 = w[w.timestamp.between(end - pd.Timedelta(days=30), end)]
    inn = w30[w30.receiver == acc]
    out = w30[w30.sender == acc]
    total_in = float(inn.amount.sum())
    rows: list[dict] = []

    def add(key, valor, activa, peso, evid):
        rows.append({"parametro": key, "descripcion": PARAMS[key], "valor": valor, "activa": bool(activa),
                     "peso": peso if activa else 0, "peso_max": peso, "evidencia": list(evid)[:20]})

    lo, hi = cfg.get("cerca_umbral_banda", [0.8, 0.99])
    near = inn[inn.amount.between(lo * umbral, hi * umbral)]
    add("cerca_umbral", f"{len(near)} entre {lo:.0%} y {hi:.0%} del umbral", len(near) >= cfg.get("cerca_umbral_min", 3),
        2, near.tx_id)
    add("suma_ventana", f"suma {amount_band(total_in)}; mayor depósito {amount_band(float(inn.amount.max() or 0))}",
        len(inn) > 1 and total_in > umbral and float(inn.amount.max()) < umbral, 1, inn.tx_id)
    n_dep = inn.sender.nunique()
    add("depositantes_distintos", f"{n_dep} depositantes", n_dep >= cfg.get("depositantes_min", 4), 2, inn.tx_id)
    per_day = inn.groupby(inn.timestamp.dt.date).payment_type.nunique() if len(inn) else pd.Series(dtype=int)
    add("canales_mismo_dia", f"máx. {int(per_day.max() if len(per_day) else 0)} canales en un día",
        len(per_day) and per_day.max() >= cfg.get("canales_mismo_dia_min", 3), 1, [])
    consec = _max_consecutive(pd.Series(pd.to_datetime(inn.timestamp.dt.date)))
    add("dias_consecutivos", f"{consec} días seguidos", consec >= cfg.get("dias_consecutivos_min", 5), 1, [])
    rounded = inn[(inn.amount.round(0) % 100 == 0)]
    repeated = inn[inn.amount.round(0).duplicated(keep=False)]
    rep = pd.concat([rounded, repeated]).drop_duplicates("tx_id")
    add("montos_repetidos", f"{len(rep)} montos repetidos/redondos", len(rep) >= cfg.get("montos_repetidos_min", 3),
        1, rep.tx_id)
    if ingreso_usd:
        ratio = total_in * fx / max(ingreso_usd, 1.0)
        add("relacion_ingreso", f"{ratio:.1f}× el ingreso mensual", ratio > cfg.get("relacion_ingreso_max", 3), 2, [])
    else:
        add("relacion_ingreso", "sin ingreso declarado", False, 2, [])
    rapid = 0.0
    if total_in > 0:
        out_48 = sum(float(out[(out.timestamp > t) & (out.timestamp <= t + pd.Timedelta(hours=48))].amount.sum())
                     for t in inn.timestamp)
        rapid = min(out_48, float(out.amount.sum())) / total_in
    add("salida_rapida", f"{min(rapid, 9.99):.0%} sale en 48 h", rapid > cfg.get("salida_rapida_min", 0.7), 1, out.tx_id)

    puntaje = sum(r["peso"] for r in rows)
    incompletos = ingreso_usd is None
    t = cfg.get("triaje", {"revision": 3, "critico": 6})
    nivel = "critico" if puntaje >= t["critico"] else "revision" if puntaje >= t["revision"] or incompletos else "pasa"
    return {"puntaje": puntaje, "puntaje_max": sum(r["peso_max"] for r in rows), "nivel": nivel,
            "datos_incompletos": incompletos, "parametros": rows,
            "senales": [r["parametro"] for r in rows if r["activa"]]}


class AgenteFraccionamiento(Agent):
    name = "agente_fraccionamiento"
    capabilities = ["scorecard_fraccionamiento"]
    description = "Ficha de 8 parámetros de fraccionamiento (pitufeo) con pesos y triaje pasa / revisión / crítico."

    def run(self, case: Case) -> list[dict]:
        sc = scorecard(case.window, case.account, case.alert["fecha"], case.policy, case.raw.get("ingreso_mensual_usd"))
        case.shared["fraccionamiento"] = {k: v for k, v in sc.items() if k != "parametros"}
        nivel_txt = {"pasa": "pasa", "revision": "revisión manual", "critico": "crítico"}[sc["nivel"]]
        claim = (f"Ficha de fraccionamiento: {sc['puntaje']}/{sc['puntaje_max']} puntos → {nivel_txt}"
                 + (f" ({', '.join(PARAMS[s].lower() for s in sc['senales'][:3])})" if sc["senales"] else ""))
        evid = sorted({e for r in sc["parametros"] for e in r["evidencia"]})
        reasoning = ("Parámetros activados: " + "; ".join(f"{r['descripcion']} — {r['valor']} (+{r['peso']})"
                                                         for r in sc["parametros"] if r["activa"])
                     if sc["senales"] else "Ningún parámetro de fraccionamiento se activó.")
        if sc["datos_incompletos"]:
            reasoning += " Faltan datos del perfil (ingreso declarado): la ficha manda el caso a revisión manual."
        extra = {"ficha": [{k: v for k, v in r.items() if k != "evidencia"} for r in sc["parametros"]],
                 "nivel": sc["nivel"], "puntaje": sc["puntaje"]}
        if sc["nivel"] == "critico":
            case.shared.setdefault("tipologias", []).append("fraccionamiento")
            return [self.finding(case, claim, "fraccionamiento", 0.7, evid, "Ficha de 8 parámetros (determinista)",
                                 reasoning, extra=extra)]
        return [self.finding(case, claim, "fraccionamiento", 0.0, evid, "Ficha de 8 parámetros (determinista)",
                             reasoning, kind="info", extra=extra)]
