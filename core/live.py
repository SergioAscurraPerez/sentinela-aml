"""Laboratorio en vivo: genera una alerta NUEVA con transacciones nuevas en el momento de la demo.

Nada está precalculado: el jurado elige la tipología (o "sorpresa") y los parámetros, se crean
transacciones con una semilla basada en la hora, y los agentes las investigan desde cero.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pandas as pd

TIPOLOGIAS = {
    "estructuracion": "Estructuración (depósitos justo bajo el umbral)",
    "pitufeo": "Pitufeo (muchos depósitos pequeños de muchas personas)",
    "ciclo": "Ciclo / layering (el dinero da la vuelta)",
    "concentracion_dispersion": "Concentración y dispersión (entra de muchos, sale a muchos)",
    "planilla": "Planilla legítima (falso positivo)",
    "normal": "Cliente normal (sin lavado)",
}
OCUPACIONES = {
    "Estudiante universitario": ("persona_natural", 300),
    "Comerciante minorista (bodega)": ("persona_natural", 1_500),
    "Empleado dependiente": ("persona_natural", 2_500),
    "Profesional independiente": ("persona_natural", 5_000),
    "Pequeña empresa": ("empresa", 40_000),
    "Empresa mediana": ("empresa", 150_000),
}
NOMBRES = ["Rosa Quispe", "Juan Mamani", "Lucía Torres", "Carlos Huamán", "Ana Flores", "Miguel Rojas"]


def default_params(tipo: str) -> dict:
    return {
        "estructuracion": {"n": 12, "monto": 9_700.0, "dias": 5, "salida_exterior": True},
        "pitufeo": {"n": 18, "monto": 900.0, "dias": 6, "salida_exterior": False},
        "ciclo": {"n": 4, "monto": 60_000.0, "dias": 2, "salida_exterior": False},
        "concentracion_dispersion": {"n": 7, "monto": 8_000.0, "dias": 2, "salida_exterior": True},
        "planilla": {"n": 25, "monto": 2_500.0, "dias": 1, "salida_exterior": False},
        "normal": {"n": 10, "monto": 400.0, "dias": 30, "salida_exterior": False},
    }[tipo]


def generate(tipo: str, params: dict, ocupacion: str, seq: int, seed: int | None = None) -> tuple[pd.DataFrame, dict]:
    sorpresa = tipo == "sorpresa"
    if sorpresa:
        rng0 = random.Random(seed)
        tipo = rng0.choice([t for t in TIPOLOGIAS])
        params = default_params(tipo)
        ocupacion = rng0.choice(list(OCUPACIONES))
    rng = random.Random(seed if seed is not None else datetime.now().timestamp())
    now = datetime.now().replace(microsecond=0)
    acc = f"ACC-LIVE-{seq:03d}"
    rows: list[list] = []
    k = 0

    def tx(ts, s, r, amt, ptype="transferencia", lab=0):
        nonlocal k
        k += 1
        rows.append([f"L{seq}-{k:04d}", ts.isoformat(), s, r, round(float(amt), 2), "USD", ptype, "PE",
                     "PE" if "internacional" not in ptype else rng.choice(["PA", "BZ", "KY", "US"]), lab,
                     tipo if lab else "Normal"])

    other = lambda i: f"ACC-L{seq:03d}-{i:03d}"  # noqa: E731
    # historial normal de 30 días (para que haya con qué comparar)
    for i in range(rng.randint(6, 12)):
        ts = now - timedelta(days=rng.uniform(8, 34))
        if rng.random() < 0.5:
            tx(ts, other(900 + i), acc, rng.uniform(150, 1_200))
        else:
            tx(ts, acc, other(950 + i), rng.uniform(50, 800), rng.choice(["pago_servicios", "transferencia"]))

    n, monto, dias = int(params["n"]), float(params["monto"]), max(float(params["dias"]), 0.5)
    start = now - timedelta(days=dias)
    lab = 0 if tipo in ("planilla", "normal") else 1
    total_in = 0.0
    if tipo in ("estructuracion", "pitufeo"):
        senders = [other(i) for i in range(max(3, n // (2 if tipo == "estructuracion" else 1)))]
        for i in range(n):
            amt = monto * rng.uniform(0.96, 1.02) if tipo == "estructuracion" else monto * rng.uniform(0.4, 1.6)
            amt = min(amt, 9_990) if tipo == "estructuracion" else amt
            total_in += amt
            tx(start + timedelta(hours=rng.uniform(0, dias * 24 - 2)), rng.choice(senders), acc, amt,
               "deposito_efectivo", lab)
    if tipo == "ciclo":
        ring = [acc] + [other(100 + i) for i in range(max(2, n - 1))]
        amt, t = monto, start
        for _ in range(2):
            for i in range(len(ring)):
                amt *= rng.uniform(0.97, 0.995)
                t += timedelta(hours=rng.uniform(1, max(dias * 24 / (2 * len(ring)), 1.5)))
                tx(t, ring[i], ring[(i + 1) % len(ring)], amt, "transferencia", lab)
    if tipo == "concentracion_dispersion":
        t = start
        for i in range(n):
            a = monto * rng.uniform(0.8, 1.2)
            total_in += a
            tx(t + timedelta(hours=rng.uniform(0, 20)), other(200 + i), acc, a, "transferencia", lab)
        for i in range(n):
            tx(t + timedelta(hours=rng.uniform(22, 40)), acc, other(300 + i), total_in / n * rng.uniform(0.85, 0.98),
               "transferencia_internacional" if params.get("salida_exterior") else "transferencia", lab)
        total_in = 0
    if tipo == "planilla":
        staff = [other(400 + i) for i in range(n)]
        for m in (2, 1, 0):
            day = now - timedelta(days=30 * m + 0.2)
            tx(day - timedelta(days=2), other(899), acc, n * monto * 1.15, "transferencia")
            for e in staff:
                tx(day + timedelta(minutes=rng.randint(0, 50)), acc, e, rng.choice([0.8, 1.0, 1.2]) * monto,
                   "pago_planilla")
    if tipo == "normal":
        for i in range(n):
            tx(now - timedelta(days=rng.uniform(0, 7)), other(500 + i), acc, monto * rng.uniform(0.5, 1.5))
    if total_in and params.get("salida_exterior"):
        tx(now - timedelta(hours=rng.uniform(1, 6)), acc, other(777), total_in * rng.uniform(0.85, 0.95),
           "transferencia_internacional", lab)

    cols = ["tx_id", "timestamp", "sender", "receiver", "amount", "currency", "payment_type", "sender_loc",
            "receiver_loc", "is_laundering", "laundering_type"]
    df = pd.DataFrame(rows, columns=cols).sort_values("timestamp").reset_index(drop=True)
    seg, ingreso = OCUPACIONES.get(ocupacion, ("persona_natural", 2_000))
    alert = {
        "alert_id": f"L-{seq:03d}", "account": acc,
        "titulo": (f"🎲 En vivo · Caso sorpresa (tipología oculta) · {ocupacion}" if sorpresa
                   else f"🧪 En vivo · {TIPOLOGIAS[tipo].split(' (')[0]} · {ocupacion}"),
        "sorpresa": sorpresa,
        "regla": "Alerta generada en vivo por el jurado", "fecha": now.date().isoformat(),
        "etiqueta_real": "Normal" if tipo in ("planilla", "normal") else tipo, "origen": "en_vivo", "moneda": "USD",
        "cuenta_real": f"191-{rng.randint(1_000_000, 9_999_999)}-0-{rng.randint(10, 99)}",
        "nombre_cliente": rng.choice(NOMBRES), "documento_identidad": f"DNI {rng.randint(10_000_000, 79_999_999)}",
        "direccion": f"Jr. Ejemplo {rng.randint(100, 999)}, Lima", "telefono": f"9{rng.randint(10_000_000, 99_999_999)}",
        "kyc": {"segmento": seg, "ocupacion": ocupacion, "ingreso_mensual_usd": ingreso,
                "antiguedad_meses": rng.randint(2, 60)},
        "creada": now.isoformat(),
    }
    return df, alert
