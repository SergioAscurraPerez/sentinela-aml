"""Prepara transacciones y alertas para Sentinela.

Siempre genera 3 alertas sintéticas controladas para la demo:
  A-001 estructuración (fraccionamiento + pass-through)
  A-002 ciclo de layering
  A-003 falso positivo (planilla)
Con --source samld o --source ibm añade alertas reales del dataset (etiquetadas).

Uso:
    python data/prepare_data.py                       # solo sintético (funciona sin Kaggle)
    python data/prepare_data.py --source samld --nrows 2000000
    python data/prepare_data.py --source ibm
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent
RAW = DATA / "raw"
COLS = ["tx_id", "timestamp", "sender", "receiver", "amount", "currency",
        "payment_type", "sender_loc", "receiver_loc", "is_laundering", "laundering_type"]

NOMBRES = ["Rosa Quispe", "Juan Mamani", "Lucía Torres", "Carlos Huamán", "Ana Flores",
           "Miguel Rojas", "Patricia Chávez", "Jorge Vargas", "Elena Ramos", "Luis Castillo"]


def _fake_pii(rng: random.Random) -> dict:
    return {"nombre_cliente": rng.choice(NOMBRES),
            "documento_identidad": f"DNI {rng.randint(10_000_000, 79_999_999)}",
            "direccion": f"Av. Ejemplo {rng.randint(100, 2999)}, Lima",
            "telefono": f"9{rng.randint(10_000_000, 99_999_999)}"}


def synthetic(seed: int = 7) -> tuple[pd.DataFrame, list[dict]]:
    rng = random.Random(seed)
    t0 = datetime(2026, 9, 1, 9, 0)
    rows: list[list] = []
    n = 0

    def tx(ts, s, r, amt, ptype="transferencia", lab=0, ltype="Normal"):
        nonlocal n
        n += 1
        rows.append([f"T{n:06d}", ts.replace(microsecond=0).isoformat(), s, r, round(amt, 2), "USD", ptype,
                     "PE", "PE", lab, ltype])

    accounts = [f"ACC-{i:07d}" for i in rng.sample(range(1_000_000, 9_999_999), 120)]
    # fondo normal
    for _ in range(900):
        s, r = rng.sample(accounts, 2)
        tx(t0 + timedelta(minutes=rng.randint(0, 60 * 24 * 30)), s, r,
           rng.lognormvariate(6.2, 1.0))

    # A-001 estructuración: 14 depósitos de 9.5k-9.95k desde 9 cuentas en 5 días, luego salida
    tgt = "ACC-STR-001"
    feeders = rng.sample(accounts, 9)
    total = 0.0
    for i in range(14):
        amt = rng.uniform(9500, 9950)
        total += amt
        tx(t0 + timedelta(days=10 + i * 0.35, minutes=rng.randint(0, 90)),
           feeders[i % 9], tgt, amt, "deposito_efectivo", 1, "Structuring")
    tx(t0 + timedelta(days=15, hours=3), tgt, "ACC-OFF-777", total * 0.93,
       "transferencia_internacional", 1, "Structuring")

    # A-002 ciclo: 001->002->003->004->001, dos rondas en 36 h
    cyc = [f"ACC-CYC-00{i}" for i in range(1, 5)]
    amt = 48_000.0
    t = t0 + timedelta(days=18)
    for _round in range(2):
        for i in range(4):
            amt *= rng.uniform(0.97, 0.995)
            t += timedelta(hours=rng.uniform(2, 5))
            tx(t, cyc[i], cyc[(i + 1) % 4], amt, "transferencia", 1, "Cycle")
    for _ in range(4):  # algo de actividad normal en el ciclo
        tx(t0 + timedelta(days=rng.randint(1, 28)), rng.choice(accounts), cyc[0],
           rng.uniform(300, 2000))

    # A-003 falso positivo: planilla mensual de empresa a 30 empleados (fan-out regular)
    emp = "ACC-PAY-500"
    staff = rng.sample(accounts, 30)
    for month in (0, 1):
        day = t0 + timedelta(days=month * 30 + 0.2)
        tx(day - timedelta(days=2), "ACC-CLI-900", emp, 95_000, "transferencia")
        for e in staff:
            tx(day + timedelta(minutes=rng.randint(0, 50)), emp, e,
               rng.choice([1800, 2200, 2500, 3100, 3500]), "pago_planilla")

    df = pd.DataFrame(rows, columns=COLS).sort_values("timestamp").reset_index(drop=True)
    alerts = [
        dict(alert_id="A-001", account=tgt, regla="Depósitos en efectivo recurrentes cercanos al umbral de registro",
             fecha=(t0 + timedelta(days=15)).date().isoformat(), etiqueta_real="Structuring", origen="sintetico",
             cuenta_real="191-2345678-0-11", **_fake_pii(rng)),
        dict(alert_id="A-002", account=cyc[0], regla="Movimiento circular de fondos de alto monto en <48 h",
             fecha=(t0 + timedelta(days=19)).date().isoformat(), etiqueta_real="Cycle", origen="sintetico",
             cuenta_real="191-8765432-0-22", **_fake_pii(rng)),
        dict(alert_id="A-003", account=emp, regla="Fan-out: pagos a más de 25 cuentas distintas en un día",
             fecha=(t0 + timedelta(days=30)).date().isoformat(), etiqueta_real="Normal", origen="sintetico",
             cuenta_real="191-1112223-0-33", **_fake_pii(rng)),
    ]
    return df, alerts


def from_samld(nrows: int, per_type: int, seed: int = 7) -> tuple[pd.DataFrame, list[dict]]:
    files = list((RAW / "saml_d").glob("*.csv"))
    if not files:
        raise SystemExit("No encontré SAML-D en data/raw/saml_d. Corre data/download_data.py")
    df = pd.read_csv(files[0], nrows=nrows)
    df["timestamp"] = pd.to_datetime(df["Date"].astype(str) + " " + df["Time"].astype(str))
    out = pd.DataFrame({
        "tx_id": "S" + df.index.astype(str),
        "timestamp": df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "sender": "ACC-" + df["Sender_account"].astype(str),
        "receiver": "ACC-" + df["Receiver_account"].astype(str),
        "amount": df["Amount"].astype(float),
        "currency": df["Payment_currency"],
        "payment_type": df["Payment_type"],
        "sender_loc": df["Sender_bank_location"],
        "receiver_loc": df["Receiver_bank_location"],
        "is_laundering": df["Is_laundering"].astype(int),
        "laundering_type": df["Laundering_type"],
    })
    return _sample_alerts(out, per_type, "samld", seed)


def from_ibm(nrows: int, per_type: int, seed: int = 7) -> tuple[pd.DataFrame, list[dict]]:
    f = RAW / "ibm" / "HI-Small_Trans.csv"
    if not f.exists():
        raise SystemExit("No encontré data/raw/ibm/HI-Small_Trans.csv")
    df = pd.read_csv(f, nrows=nrows)
    acc_cols = [c for c in df.columns if c.startswith("Account")]
    out = pd.DataFrame({
        "tx_id": "I" + df.index.astype(str),
        "timestamp": pd.to_datetime(df["Timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "sender": "ACC-" + df[acc_cols[0]].astype(str),
        "receiver": "ACC-" + df[acc_cols[1]].astype(str),
        "amount": df["Amount Paid"].astype(float),
        "currency": df["Payment Currency"],
        "payment_type": df["Payment Format"],
        "sender_loc": df["From Bank"].astype(str),
        "receiver_loc": df["To Bank"].astype(str),
        "is_laundering": df["Is Laundering"].astype(int),
        "laundering_type": df["Is Laundering"].map({1: "Laundering", 0: "Normal"}),
    })
    return _sample_alerts(out, per_type, "ibm", seed)


def _sample_alerts(df: pd.DataFrame, per_type: int, origen: str, seed: int):
    rng = random.Random(seed)
    alerts, keep = [], set()
    launder = df[df.is_laundering == 1]
    for ltype, g in launder.groupby("laundering_type"):
        top = g["receiver"].value_counts().head(per_type).index.tolist()
        for acc in top:
            alerts.append((acc, ltype))
    normal_top = df[df.is_laundering == 0]["sender"].value_counts().head(per_type * 2).index.tolist()
    alerts += [(a, "Normal") for a in normal_top]
    for acc, _ in alerts:
        keep.add(acc)
    sub = df[df.sender.isin(keep) | df.receiver.isin(keep)]
    out_alerts = []
    for i, (acc, ltype) in enumerate(alerts, start=1):
        last = sub[(sub.sender == acc) | (sub.receiver == acc)]["timestamp"].max()
        out_alerts.append(dict(alert_id=f"{origen[:1].upper()}-{i:03d}", account=acc,
                               regla=f"Regla de monitoreo ({origen})", fecha=str(last)[:10],
                               etiqueta_real=ltype, origen=origen,
                               cuenta_real=f"191-{rng.randint(1_000_000, 9_999_999)}-0-{rng.randint(10, 99)}",
                               **_fake_pii(rng)))
    return sub.reset_index(drop=True), out_alerts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["sintetico", "samld", "ibm"], default="sintetico")
    ap.add_argument("--nrows", type=int, default=2_000_000)
    ap.add_argument("--per-type", type=int, default=2)
    args = ap.parse_args()

    df, alerts = synthetic()
    if args.source == "samld":
        d2, a2 = from_samld(args.nrows, args.per_type)
        df, alerts = pd.concat([df, d2], ignore_index=True), alerts + a2
    elif args.source == "ibm":
        d2, a2 = from_ibm(args.nrows, args.per_type)
        df, alerts = pd.concat([df, d2], ignore_index=True), alerts + a2

    df.to_csv(DATA / "transactions.csv", index=False)
    (DATA / "alerts.json").write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(df):,} transacciones, {len(alerts)} alertas -> data/transactions.csv, data/alerts.json")


if __name__ == "__main__":
    main()
