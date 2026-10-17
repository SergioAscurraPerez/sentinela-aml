"""Herramientas deterministas sobre datos crudos (corren dentro del banco, sin LLM).

Los agentes llaman a estas herramientas; el LLM solo recibe los rasgos ya calculados y
pseudonimizados. Así la parte numérica es exacta y auditable, y el LLM razona y explica.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

TX_PATH = Path(__file__).resolve().parent.parent / "data" / "transactions.csv"


_LIVE: list[pd.DataFrame] = []   # transacciones creadas en vivo durante la demo


def add_live_transactions(df: pd.DataFrame) -> None:
    """Agrega transacciones generadas en vivo y refresca la caché."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    _LIVE.append(df)
    load_tx.cache_clear()


@lru_cache(maxsize=1)
def load_tx(path: str = str(TX_PATH)) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    if _LIVE:
        df = pd.concat([df, *_LIVE], ignore_index=True)
    return df


def account_window(account: str, alert_date: str, days_back: int = 35) -> pd.DataFrame:
    df = load_tx()
    end = pd.Timestamp(alert_date) + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=days_back)
    m = ((df.sender == account) | (df.receiver == account)) & df.timestamp.between(start, end)
    return df[m].sort_values("timestamp")


def neighborhood(account: str, alert_date: str, hops: int = 2, days_back: int = 35,
                 max_edges: int = 400) -> pd.DataFrame:
    df = load_tx()
    end = pd.Timestamp(alert_date) + pd.Timedelta(days=1)
    df = df[df.timestamp.between(end - pd.Timedelta(days=days_back), end)]
    frontier, seen = {account}, {account}
    parts = []
    for _ in range(hops):
        e = df[df.sender.isin(frontier) | df.receiver.isin(frontier)]
        parts.append(e)
        nxt = set(e.sender) | set(e.receiver)
        frontier = nxt - seen
        seen |= nxt
        if not frontier:
            break
    out = pd.concat(parts).drop_duplicates("tx_id") if parts else df.iloc[0:0]
    return out.head(max_edges)


def profile(w: pd.DataFrame, account: str) -> dict[str, Any]:
    inc, out = w[w.receiver == account], w[w.sender == account]
    return {
        "n_tx": int(len(w)), "n_entrantes": int(len(inc)), "n_salientes": int(len(out)),
        "total_entrante": float(inc.amount.sum()), "total_saliente": float(out.amount.sum()),
        "contrapartes_entrantes": int(inc.sender.nunique()), "contrapartes_salientes": int(out.receiver.nunique()),
        "tipos_pago": w.payment_type.value_counts().to_dict(),
        "desde": str(w.timestamp.min())[:16] if len(w) else None,
        "hasta": str(w.timestamp.max())[:16] if len(w) else None,
    }


def detect_structuring(w: pd.DataFrame, account: str, pol: dict) -> dict | None:
    thr = pol["umbrales_registro"]["operacion_unica_usd"] / pol.get("fx_a_usd", 1.0)
    band = pol["deteccion"]["estructuracion_banda_inferior"]
    days = pol["deteccion"]["ventana_dias"]
    inc = w[(w.receiver == account) & (w.amount >= band * thr) & (w.amount < thr)]
    if len(inc) < pol["deteccion"]["estructuracion_min_operaciones"]:
        return None
    s = inc.set_index("timestamp").sort_index()
    roll = s.amount.rolling(f"{days}D").count()
    best_end = roll.idxmax()
    win = s.loc[best_end - pd.Timedelta(days=days): best_end]
    if len(win) < pol["deteccion"]["estructuracion_min_operaciones"]:
        return None
    return {"n_operaciones": int(len(win)), "dias": days, "monto_min": float(win.amount.min()),
            "monto_max": float(win.amount.max()), "total": float(win.amount.sum()),
            "umbral": float(thr), "contrapartes": int(win.sender.nunique()),
            "supera_multiples_mes": bool(win.amount.sum() * pol.get("fx_a_usd", 1.0)
                                         >= pol["umbrales_registro"]["multiples_mes_usd"]),
            "evidencia": win.tx_id.tolist()}


def detect_fan(w: pd.DataFrame, account: str, pol: dict) -> dict | None:
    k = pol["deteccion"]["fan_min_contrapartes"]
    res = {}
    for side, col, other in (("fan_in", "receiver", "sender"), ("fan_out", "sender", "receiver")):
        sub = w[w[col] == account]
        if sub.empty:
            continue
        by_day = sub.groupby(sub.timestamp.dt.date)[other].nunique()
        if by_day.max() >= k:
            day = by_day.idxmax()
            ev = sub[sub.timestamp.dt.date == day]
            res[side] = {"contrapartes_dia": int(by_day.max()), "dia": str(day),
                         "total": float(ev.amount.sum()), "evidencia": ev.tx_id.tolist()[:40]}
    return res or None


def detect_cycles(nb: pd.DataFrame, account: str, pol: dict) -> dict | None:
    g = nx.DiGraph()
    for r in nb.itertuples():
        g.add_edge(r.sender, r.receiver)
    maxlen = pol["deteccion"]["ciclo_max_longitud"]
    cycles = []
    try:
        for c in nx.simple_cycles(g, length_bound=maxlen):
            if account in c and len(c) >= 3:
                cycles.append(c)
            if len(cycles) >= 5:
                break
    except TypeError:  # networkx antiguo sin length_bound
        for c in nx.simple_cycles(g):
            if account in c and 3 <= len(c) <= maxlen:
                cycles.append(c)
            if len(cycles) >= 5:
                break
    if not cycles:
        return None
    c = cycles[0]
    pairs = set(zip(c, c[1:] + c[:1]))
    ev = nb[[(s, r) in pairs for s, r in zip(nb.sender, nb.receiver)]]
    hours = (ev.timestamp.max() - ev.timestamp.min()).total_seconds() / 3600 if len(ev) else 0
    return {"ciclo": c, "longitud": len(c), "n_ciclos": len(cycles), "horas": round(hours, 1),
            "monto_promedio": float(ev.amount.mean()) if len(ev) else 0.0, "evidencia": ev.tx_id.tolist()}


def detect_pass_through(w: pd.DataFrame, account: str, pol: dict) -> dict | None:
    inc, out = w[w.receiver == account], w[w.sender == account]
    if inc.empty or out.empty:
        return None
    ratio = out.amount.sum() / max(inc.amount.sum(), 1)
    lag_h = (out.timestamp.max() - inc.timestamp.max()).total_seconds() / 3600
    if ratio >= pol["deteccion"]["pass_through_ratio"] and lag_h <= 24 * 7:
        intl = out.payment_type.astype(str).str.contains("internacional").any()
        return {"ratio_salida_entrada": round(float(ratio), 2), "horas_tras_ultimo_ingreso": round(lag_h, 1),
                "salida_internacional": bool(intl), "evidencia": out.tx_id.tolist()[:10]}
    return None


def benign_signals(w: pd.DataFrame, account: str) -> dict:
    """Señales que el abogado del diablo usa para buscar explicaciones legítimas."""
    out = w[w.sender == account]
    sig: dict[str, Any] = {}
    if not out.empty:
        types = out.payment_type.astype(str)
        sig["pct_pago_planilla"] = round(float(types.str.contains("planilla|payroll|salary", case=False).mean()), 2)
        months = out.timestamp.dt.to_period("M")
        recips_by_month = out.groupby(months).receiver.apply(set)
        if len(recips_by_month) >= 2:
            a, b = recips_by_month.iloc[-2], recips_by_month.iloc[-1]
            sig["recurrencia_destinatarios"] = round(len(a & b) / max(len(a | b), 1), 2)
        sig["n_salidas"] = int(len(out))
        sig["montos_distintos"] = int(out.amount.round(0).nunique())
        sig["dias_del_mes_distintos"] = int(out.timestamp.dt.day.nunique())
    inc = w[w.receiver == account]
    if not inc.empty:
        sig["pct_ingresos_efectivo"] = round(float(inc.payment_type.astype(str)
                                                    .str.contains("efectivo|cash", case=False).mean()), 2)
    return sig


# ---------------------------------------------------------------------------- reglas añadidas (Sprint 2)
def _thr(pol: dict) -> float:
    """Umbral de registro expresado en la moneda de la alerta."""
    return pol["umbrales_registro"]["operacion_unica_usd"] / pol.get("fx_a_usd", 1.0)


def detect_smurfing(w: pd.DataFrame, account: str, pol: dict) -> dict | None:
    """Pitufeo: muchos ingresos pequeños (< 50% del umbral) que en 7 días suman más que el umbral."""
    thr, days = _thr(pol), pol["deteccion"]["ventana_dias"]
    small = w[(w.receiver == account) & (w.amount < 0.5 * thr)]
    if len(small) < pol["deteccion"].get("pitufeo_min_operaciones", 8):
        return None
    s = small.set_index("timestamp").sort_index()
    roll_n = s.amount.rolling(f"{days}D").count()
    roll_sum = s.amount.rolling(f"{days}D").sum()
    ok = (roll_n >= pol["deteccion"].get("pitufeo_min_operaciones", 8)) & (roll_sum >= thr)
    if not ok.any():
        return None
    end = roll_sum[ok].idxmax()
    win = s.loc[end - pd.Timedelta(days=days): end]
    return {"n_operaciones": int(len(win)), "dias": days, "total": float(win.amount.sum()), "umbral": float(thr),
            "contrapartes": int(win.sender.nunique()), "evidencia": win.tx_id.tolist()}


def detect_gather_scatter(w: pd.DataFrame, account: str, pol: dict) -> dict | None:
    """Concentración y dispersión: entra de muchas cuentas y sale a muchas cuentas en < 72 h (o al revés)."""
    k = pol["deteccion"].get("concentracion_min_contrapartes", 4)
    inc, out = w[w.receiver == account], w[w.sender == account]
    if inc.sender.nunique() < k or out.receiver.nunique() < k:
        return None
    for first, second, a, b, kind in ((inc, out, "sender", "receiver", "gather_scatter"),
                                      (out, inc, "receiver", "sender", "scatter_gather")):
        for t in first.timestamp.sort_values().unique():
            f = first[first.timestamp.between(t - pd.Timedelta(hours=72), t)]
            g = second[second.timestamp.between(t, t + pd.Timedelta(hours=72))]
            if f[a].nunique() >= k and g[b].nunique() >= k:
                ratio = g.amount.sum() / max(f.amount.sum(), 1)
                if 0.5 <= ratio <= 1.5:
                    return {"patron": kind, "contrapartes_entrada": int(f[a].nunique()),
                            "contrapartes_salida": int(g[b].nunique()), "ratio": round(float(ratio), 2),
                            "evidencia": f.tx_id.tolist()[:20] + g.tx_id.tolist()[:20]}
    return None


def detect_behaviour_change(w: pd.DataFrame, account: str, alert_date: str, pol: dict) -> dict | None:
    """Cambio de comportamiento: la última semana se aparta > z desviaciones del historial de la cuenta."""
    end = pd.Timestamp(alert_date) + pd.Timedelta(days=1)
    last = w[w.timestamp >= end - pd.Timedelta(days=7)]
    prev = w[w.timestamp < end - pd.Timedelta(days=7)]
    if len(prev) < 4 or last.empty:
        return None
    weekly = prev.set_index("timestamp").amount.resample("7D").sum()
    mu, sd = float(weekly.mean()), float(weekly.std() or 0) or 1.0
    cur = float(last.amount.sum())
    z = (cur - mu) / sd
    new_types = sorted(set(last.payment_type.astype(str)) - set(prev.payment_type.astype(str)))
    new_curr = sorted(set(last.currency.astype(str)) - set(prev.currency.astype(str)))
    if z >= pol["deteccion"].get("cambio_z", 3.0) and cur >= 2 * max(mu, 1) or (new_types and cur > mu * 1.5 and len(last) >= 2):
        return {"z": round(z, 1), "semana_actual": cur, "media_semanal": round(mu, 2),
                "tipos_pago_nuevos": new_types, "monedas_nuevas": new_curr, "evidencia": last.tx_id.tolist()[:30]}
    return None


def peak_30d_inflow(w: pd.DataFrame, account: str) -> float:
    inc = w[w.receiver == account]
    if inc.empty:
        return 0.0
    s = inc.set_index("timestamp").sort_index().amount
    return float(s.rolling("30D").sum().max())
