"""Agente de anomalía (ML no supervisado): IsolationForest sobre el comportamiento de todas las cuentas.

Entrena en memoria con las ventanas de 30 días de las cuentas del banco (sin etiquetas) y devuelve en qué
percentil de rareza cae la cuenta de la alerta. Complementa a las reglas: detecta "patrones nunca vistos".
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from core import analytics as A

from .base import Agent, Case

FEATURES = ["n_tx", "monto_medio", "monto_cv", "pct_efectivo", "pct_internacional", "contrapartes",
            "ratio_salida_entrada", "dias_activos"]


def account_features(w: pd.DataFrame, acc: str) -> dict:
    inn, out = w[w.receiver == acc], w[w.sender == acc]
    amt = w.amount
    cash = w.payment_type.astype(str).str.contains("Cash|efectivo", case=False)
    intl = (w.sender_loc != w.receiver_loc) | w.payment_type.astype(str).str.contains("Cross|internacional", case=False)
    return {"n_tx": len(w), "monto_medio": float(amt.mean() or 0),
            "monto_cv": float(amt.std() / amt.mean()) if len(w) > 1 and amt.mean() else 0.0,
            "pct_efectivo": float(cash.mean() if len(w) else 0), "pct_internacional": float(intl.mean() if len(w) else 0),
            "contrapartes": len((set(w.sender) | set(w.receiver)) - {acc}),
            "ratio_salida_entrada": float(out.amount.sum() / inn.amount.sum()) if inn.amount.sum() else 0.0,
            "dias_activos": int(w.timestamp.dt.date.nunique())}


def _vec(f: dict) -> np.ndarray:
    v = np.array([f[k] for k in FEATURES], dtype=float)
    v[[0, 1, 5]] = np.log1p(v[[0, 1, 5]])          # cantidades y montos en escala log
    v[6] = np.log1p(min(v[6], 50))
    return v


@lru_cache(maxsize=1)
def _model():
    from sklearn.ensemble import IsolationForest
    df = A.load_tx()
    # cada cuenta con SUS últimos 30 días de actividad (los datos mezclan periodos distintos)
    long = pd.concat([df.assign(_acc=df.sender), df.assign(_acc=df.receiver)])
    long = long[long.groupby("_acc").timestamp.transform("max") - long.timestamp <= pd.Timedelta(days=30)]
    counts = long._acc.value_counts()
    accs = counts[counts >= 3].index.to_series().sample(frac=1, random_state=7)[:3000]
    groups = long[long._acc.isin(set(accs))].groupby("_acc")
    X = np.array([_vec(account_features(g.drop_duplicates("tx_id"), a)) for a, g in groups])
    accs = list(groups.groups)
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=7).fit(X)
    ref = model.score_samples(X)
    return model, np.sort(ref), len(accs)


class AgenteAnomalia(Agent):
    name = "agente_anomalia"
    capabilities = ["anomalia_ml"]
    description = "Modelo de ML no supervisado (IsolationForest): ¿qué tan rara es la cuenta frente a las demás?"

    def run(self, case: Case) -> list[dict]:
        thr = case.policy.get("anomalia", {}).get("percentil_alerta", 97)
        model, ref, n = _model()
        end = pd.Timestamp(case.alert["fecha"]) + pd.Timedelta(days=1)
        w = case.window[case.window.timestamp >= end - pd.Timedelta(days=30)]
        if len(w) < 3:
            return [self.finding(case, "Anomalía ML: historial insuficiente para puntuar", "anomalia_ml", 0.0, [],
                                 "IsolationForest", "Menos de 3 transacciones en 30 días.", kind="info")]
        feats = account_features(w, case.account)
        score = float(model.score_samples([_vec(feats)])[0])
        pct = round(100 * (1 - np.searchsorted(ref, score) / len(ref)), 1)   # % de cuentas más "normales"
        case.shared["anomalia_percentil"] = pct
        top = sorted(FEATURES, key=lambda k: -abs(feats[k]))[:3]
        claim = f"Anomalía ML: más rara que el {pct:.0f}% de {n:,} cuentas comparables"
        reasoning = ("IsolationForest entrenado sin etiquetas con el comportamiento de 30 días de las cuentas del banco "
                     f"({', '.join(FEATURES)}). Percentil {pct}; umbral de alerta {thr}.")
        extra = {"percentil": pct, "rasgos": {k: round(v, 2) for k, v in feats.items()}, "modelo": "IsolationForest"}
        # El ML no suma riesgo por sí solo (evita falsos positivos): si la cuenta es muy rara y ninguna regla
        # la explica, el orquestador fuerza revisión humana ("patrón nunca visto").
        txt = claim + (" ⚠️ patrón poco común" if pct >= thr else " (dentro de lo esperado)")
        return [self.finding(case, txt, "anomalia_ml", 0.0, list(w.tx_id)[:20] if pct >= thr else [],
                             "Modelo ML (IsolationForest)", reasoning, kind="info", extra=extra)]
