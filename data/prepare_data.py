"""Prepara transacciones y alertas para Sentinela.

Siempre genera 3 alertas sintéticas controladas para la demo:
  A-001 estructuración (fraccionamiento + pass-through)
  A-002 ciclo de layering
  A-003 falso positivo (planilla)
Con --source samld o --source ibm añade alertas reales del dataset (etiquetadas).

Uso:
    python data/prepare_data.py                       # solo sintético (funciona sin Kaggle)
    python data/prepare_data.py --source samld            # lee SAML-D completo en bloques (~2-4 min)
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
        dict(alert_id="A-001", account=tgt, moneda="USD",
             titulo="Bodega en Gamarra: depósitos en efectivo fraccionados y envío al exterior",
             regla="Depósitos en efectivo recurrentes cercanos al umbral de registro",
             fecha=(t0 + timedelta(days=15)).date().isoformat(), etiqueta_real="Structuring", origen="sintetico",
             cuenta_real="191-2345678-0-11", **_fake_pii(rng)),
        dict(alert_id="A-002", account=cyc[0], moneda="USD",
             titulo="Red de cuentas mula: el dinero da la vuelta en 26 horas",
             regla="Movimiento circular de fondos de alto monto en <48 h",
             fecha=(t0 + timedelta(days=19)).date().isoformat(), etiqueta_real="Cycle", origen="sintetico",
             cuenta_real="191-8765432-0-22", **_fake_pii(rng)),
        dict(alert_id="A-003", account=emp, moneda="USD",
             titulo="Empresa textil: pagos a 30 cuentas el mismo día",
             regla="Fan-out: pagos a más de 25 cuentas distintas en un día",
             fecha=(t0 + timedelta(days=30)).date().isoformat(), etiqueta_real="Normal", origen="sintetico",
             cuenta_real="191-1112223-0-33", **_fake_pii(rng)),
    ]
    return df, alerts


def from_samld(nrows: int, per_type: int, seed: int = 7, min_laund_tx: int = 4,
               n_normales: int | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Lee SAML-D completo en bloques (no carga 1 GB en memoria) en dos pasadas.

    Pasada 1: filas de lavado + conteo de transacciones por cuenta.
    Pasada 2: TODAS las transacciones de las cuentas elegidas (historial completo).
    La fecha de cada alerta = última transacción de lavado de la cuenta (no la última transacción cualquiera).
    """
    files = [f for f in (RAW / "saml_d").glob("*.csv") if "subset" not in f.name] or list((RAW / "saml_d").glob("*.csv"))
    if not files:
        raise SystemExit("No encontré SAML-D en data/raw/saml_d. Corre data/download_data.py")
    src = files[0]
    rng = random.Random(seed)
    chunks = dict(chunksize=1_000_000, nrows=nrows if nrows and nrows > 0 else None)
    laund, counts = [], {}
    for ch in pd.read_csv(src, **chunks):
        laund.append(ch[ch.Is_laundering == 1])
        for a, c in ch.Sender_account.value_counts().items():
            counts[a] = counts.get(a, 0) + c
    L = pd.concat(laund)
    bad = set(L.Sender_account) | set(L.Receiver_account)
    picks: list[tuple[int, str]] = []
    for ltype, g in L.groupby("Laundering_type"):
        acc = pd.concat([g.Sender_account, g.Receiver_account]).value_counts()
        acc = acc[acc >= min_laund_tx].head(per_type)
        picks += [(a, ltype) for a in acc.index]
    normals = sorted(a for a, c in counts.items() if c >= 15 and a not in bad)
    rng.shuffle(normals)
    picks += [(a, "Normal") for a in normals[: n_normales or max(per_type * 3, 6)]]
    sel = {a for a, _ in picks}

    parts = [ch[ch.Sender_account.isin(sel) | ch.Receiver_account.isin(sel)] for ch in pd.read_csv(src, **chunks)]
    df = pd.concat(parts).drop_duplicates()
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
    alerts = []
    for i, (acc, ltype) in enumerate(picks, start=1):
        a = "ACC-" + str(acc)
        mine = out[(out.sender == a) | (out.receiver == a)]
        if ltype != "Normal":
            fecha = mine[mine.is_laundering == 1].timestamp.max()
        else:
            ts = sorted(mine.timestamp)
            fecha = ts[len(ts) // 2] if ts else None
        if not fecha:
            continue
        alerts.append(dict(alert_id=f"S-{i:03d}", account=a, titulo=f"SAML-D · alerta de monitoreo {i:03d}",
                           regla="Regla de monitoreo transaccional (SAML-D)", fecha=str(fecha)[:10],
                           etiqueta_real=ltype, origen="samld", moneda="GBP",
                           cuenta_real=f"191-{rng.randint(1_000_000, 9_999_999)}-0-{rng.randint(10, 99)}",
                           **_fake_pii(rng)))
    print(f"SAML-D: {len(L):,} filas de lavado, {len(alerts)} alertas, {len(out):,} transacciones del historial")
    return out.reset_index(drop=True), alerts


def from_ibm(nrows: int, per_type: int, seed: int = 7) -> tuple[pd.DataFrame, list[dict]]:
    f = RAW / "ibm" / "HI-Small_Trans.csv"
    if not f.exists():
        raise SystemExit("No encontré data/raw/ibm/HI-Small_Trans.csv")
    df = pd.read_csv(f, nrows=nrows or None)
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
    out_df, al = _sample_alerts(out, per_type, "ibm", seed)
    for a in al:
        a["moneda"] = "USD"
    return out_df, al


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


KYC_DEMO = {
    "A-001": ("persona_natural", "Comerciante minorista (bodega)", 1_200, 38),
    "A-002": ("persona_natural", "Estudiante universitario", 300, 3),
    "A-003": ("empresa", "Empresa textil (fabricación de prendas)", 110_000, 72),
}
def kyc(alerts: list[dict], df: pd.DataFrame | None = None, seed: int = 11) -> pd.DataFrame:
    """Perfil KYC por cuenta alertada (ingreso mensual declarado en USD).

    - Alertas de demo: perfil fijo (KYC_DEMO).
    - Alertas de SAML-D/IBM: el perfil se DERIVA del historial de la cuenta ANTERIOR a la ventana de la alerta
      (lo que el cliente "declaró" al abrir la cuenta se parece a cómo operaba antes), nunca de la etiqueta.
      Sin historial previo -> cliente nuevo con un perfil personal modesto.
    """
    rng = random.Random(seed)
    fx = {"GBP": 1.27, "EUR": 1.08, "USD": 1.0, "PEN": 0.27}
    if df is not None and len(df):
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    rows = []
    for a in alerts:
        acc = a["account"]
        if a["alert_id"] in KYC_DEMO:
            seg, ocu, ing, ant = KYC_DEMO[a["alert_id"]]
        else:
            hist = pd.DataFrame()
            if df is not None and len(df):
                cut = pd.Timestamp(a["fecha"]) - pd.Timedelta(days=35)
                hist = df[((df.sender == acc) | (df.receiver == acc)) & (df.timestamp < cut)]
            if len(hist) < 3:
                seg, ocu = "persona_natural", rng.choice(["Empleado dependiente", "Comerciante", "Profesional independiente"])
                ing, ant = rng.randint(800, 3_000), rng.randint(1, 5)
            else:
                months = max((hist.timestamp.max() - hist.timestamp.min()).days / 30, 1)
                vol = max(hist[hist.receiver == acc].amount.sum(), hist[hist.sender == acc].amount.sum())
                ing = int(vol / months * fx.get(a.get("moneda", "USD"), 1.0) * 1.2)
                ant = int(months) + rng.randint(6, 36)
                if ing > 30_000:
                    seg, ocu = "empresa", "Empresa (comercio / servicios)"
                elif ing > 6_000:
                    seg, ocu = "persona_natural", "Comerciante"
                else:
                    seg, ocu = "persona_natural", rng.choice(["Empleado dependiente", "Profesional independiente"])
        rows.append({"cuenta": acc, "segmento": seg, "ocupacion": ocu,
                     "ingreso_mensual_usd": ing, "antiguedad_meses": ant})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["sintetico", "samld", "ibm"], default="sintetico")
    ap.add_argument("--nrows", type=int, default=0, help="0 = archivo completo (recomendado)")
    ap.add_argument("--per-type", type=int, default=4)
    args = ap.parse_args()

    df, alerts = synthetic()
    if args.source == "samld":
        d2, a2 = from_samld(args.nrows, args.per_type)
        df, alerts = pd.concat([df, d2], ignore_index=True), alerts + a2
    elif args.source == "ibm":
        d2, a2 = from_ibm(args.nrows, args.per_type)
        df, alerts = pd.concat([df, d2], ignore_index=True), alerts + a2

    df.to_csv(DATA / "transactions.csv", index=False)
    kyc(alerts, df).to_csv(DATA / "clientes.csv", index=False)
    (DATA / "alerts.json").write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(df):,} transacciones, {len(alerts)} alertas -> data/transactions.csv, data/alerts.json")


if __name__ == "__main__":
    main()
