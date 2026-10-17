from __future__ import annotations

import pandas as pd

from .base import Agent, Case

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre",
         "noviembre", "diciembre"]
SIMBOLO = {"PEN": "S/", "USD": "US$", "GBP": "£", "EUR": "€"}
TIPO_PAGO = {"Cash Deposit": "depósitos en efectivo", "deposito_efectivo": "depósitos en efectivo en ventanilla",
             "Cash Withdrawal": "retiros en efectivo", "Cross-border": "transferencias internacionales",
             "transferencia_internacional": "transferencias al exterior", "ACH": "transferencias ACH",
             "transferencia": "transferencias", "Cheque": "cheques", "Credit card": "pagos con tarjeta de crédito",
             "Debit card": "pagos con tarjeta de débito", "pago_planilla": "pagos de planilla"}


def _money(v: float, cur: str) -> str:
    return f"{SIMBOLO.get(cur, cur + ' ')} {v:,.0f}"


def _n(k: int, sing: str, plur: str, art: str = "un") -> str:
    return f"{art if k == 1 else k} {sing if k == 1 else plur}"


def _periodo(ini: pd.Timestamp, fin: pd.Timestamp) -> str:
    if ini.month == fin.month and ini.year == fin.year:
        return f"durante el mes de {MESES[fin.month - 1]} de {fin.year}"
    return f"entre el {ini:%d/%m/%Y} y el {fin:%d/%m/%Y}"


def _tipos(df: pd.DataFrame) -> str:
    vc = df.groupby("payment_type").amount.sum().sort_values(ascending=False)
    parts = [TIPO_PAGO.get(t, str(t)) for t in vc.index[:2]]
    return " y ".join(parts) if parts else "operaciones"


def describe_case(case: Case) -> str:
    """Descripción del caso en lenguaje de cumplimiento, SOLO con hechos del expediente (nada inventado).
    Incluye la identidad del cliente: es un documento interno; nunca se envía al LLM."""
    al, acc, w = case.alert, case.account, case.window
    cur = al.get("moneda", "USD")
    fx = case.policy.get("fx_a_usd", 1.0)
    fin = pd.Timestamp(al["fecha"]) + pd.Timedelta(days=1)
    w = w[w.timestamp.between(fin - pd.Timedelta(days=30), fin)]
    inn, out = w[w.receiver == acc], w[w.sender == acc]
    perfil = case.shared.get("perfil", {})
    nombre = al.get("nombre_cliente") or case.token
    seg = {"persona_natural": "Persona Natural", "empresa": "Persona Jurídica"}.get(perfil.get("segmento"), "cliente")
    ingreso_usd = case.raw.get("ingreso_mensual_usd")

    txt = f"{nombre} ({seg})"
    if perfil.get("disponible"):
        txt += f", cliente con perfil económico declarado de '{perfil.get('ocupacion')}'"
        if ingreso_usd:
            txt += f" e ingresos mensuales promedio de {_money(ingreso_usd / fx, cur)}"
    txt += ", "
    if len(inn):
        ini = inn.timestamp.min()
        txt += (f"registró {_periodo(ini, inn.timestamp.max())} {_n(len(inn), 'ingreso', 'ingresos')} por "
                f"{_tipos(inn)} por un total acumulado de {_money(inn.amount.sum(), cur)}, provenientes de "
                f"{_n(inn.sender.nunique(), 'contraparte', 'contrapartes distintas', 'una')}")
        cash = inn.payment_type.astype(str).str.contains("Cash|efectivo", case=False)
        if cash.any() and not cash.all():
            txt += f" ({cash.mean():.0%} de las operaciones en efectivo)"
        intl = inn.sender_loc != inn.receiver_loc
        if intl.any():
            txt += f", {intl.sum()} de ellas desde el exterior"
        txt += ". "
    else:
        txt += "no registró ingresos en la ventana de análisis de 30 días. "

    sospechoso = case.shared.get("riesgo", 0) >= 0.5
    docs = [f for f in case.findings if f["agent"] == "agente_extraccion" and "documento" in f["extra"]]
    for d in docs:
        e = d["extra"]
        cob = e.get("cobertura", 0)
        txt += (f"Como sustento del origen de los fondos, se presentó el documento '{e['documento']}'"
                + (f" ({e['concepto']})" if e.get("concepto") and e["concepto"] != "no identificado" else "")
                + f", que declara {_money(e.get('declarado_usd', 0) / fx, cur)}: "
                + (f"explica el {cob:.0%} de lo recibido. " if cob >= 0.8 else
                   f"solo explica el {cob:.0%} de lo recibido, por lo que no sustenta el total de los fondos. "))
    if not docs and len(inn) and sospechoso:
        txt += ("A la fecha del análisis no se ha recibido documentación (contratos, facturas u otros) "
                "que sustente el origen de los fondos. ")

    if len(out) and len(inn):
        out_48 = sum(float(out[(out.timestamp > t) & (out.timestamp <= t + pd.Timedelta(hours=48))].amount.sum())
                     for t in inn.timestamp)
        rapido = min(out_48, float(out.amount.sum())) / max(float(inn.amount.sum()), 1.0)
        destino = _n(out.receiver.nunique(), "cuenta de terceros", "cuentas de terceros", "una")
        intl_out = (out.sender_loc != out.receiver_loc).sum()
        txt += (f"El análisis de sus cuentas muestra salidas por {_money(out.amount.sum(), cur)} "
                f"({out.amount.sum() / inn.amount.sum():.0%} de lo ingresado) mediante {_tipos(out)} hacia {destino}"
                + (f", {intl_out} de ellas al exterior" if intl_out else "")
                + (f"; el {min(rapido, 1):.0%} de lo recibido salió dentro de las 48 horas siguientes" if rapido >= 0.3 else "")
                + ". ")
    elif len(out):
        txt += (f"Se observan salidas por {_money(out.amount.sum(), cur)} mediante {_tipos(out)} sin ingresos visibles "
                "que las financien en la ventana analizada. ")

    ratio = perfil.get("ingreso_30d_vs_declarado")
    if sospechoso:
        txt += ("Este comportamiento no guarda relación con su perfil económico habitual"
                + (f" (lo recibido equivale a {ratio:.1f} veces su ingreso mensual declarado)" if ratio and ratio >= 1.5 else "")
                + " ni se justifica financieramente con la información disponible.")
    else:
        txt += ("El movimiento es consistente con su perfil económico declarado y no se identifican elementos que "
                "lo califiquen como sospechoso.")
    return txt


class AgenteRedactor(Agent):
    """Redacta borradores; nunca decide ni envía. No incluye la identidad del Oficial (deber de reserva)."""
    name = "agente_redactor"
    capabilities = ["redaccion", "borrador_ros", "sustento_no_reporte"]
    description = "Redacta el borrador de ROS o el sustento de no reporte para que el oficial lo revise y firme."

    def run(self, case: Case) -> list[dict]:
        risk = case.shared.get("riesgo", 0.0)
        risk_f = [f for f in case.findings if f["kind"] == "riesgo" and f.get("estado") != "rechazado"]
        mit = [f for f in case.findings if f["kind"] == "mitigante"]
        normas = [f["claim"] for f in case.findings if f["kind"] == "norma"]
        ros = risk >= 0.5
        tipo = "Borrador de ROS" if ros else "Sustento de no reporte"
        señales = "\n".join(f"- {f['claim']}" for f in risk_f) or "- Ninguna señal de riesgo vigente."
        mitig = "\n".join(f"- {f['claim']}" for f in mit) or "- Sin mitigantes relevantes."
        base_txt = (
            f"{tipo.upper()} — Alerta {case.alert_id} — Cuenta {case.token}\n\n"
            f"DESCRIPCIÓN DEL CASO\n{describe_case(case)}\n\n"
            f"1. Señales de alerta y tipologías:\n{señales}\n\n"
            f"2. Mitigantes evaluados (abogado del diablo):\n{mitig}\n\n"
            f"3. Normas aplicables:\n" + ("\n".join(f"- {n}" for n in normas) or "- N/A") + "\n\n"
            f"4. Riesgo consolidado: {risk:.2f}\n"
            f"5. Recomendación: {'Calificar como sospechosa y comunicar a la UIF-Perú vía ROSEL dentro de 24 h desde la calificación.' if ros else 'No calificar como sospechosa; mantener constancia de este análisis por 10 años.'}\n\n"
            "Nota: borrador generado por agentes; requiere revisión y firma del Oficial de Cumplimiento. "
            "No comunicar al cliente (deber de reserva)."
        )
        payload = {"tipo": tipo, "riesgo": risk, "senales": [f["claim"] for f in risk_f],
                   "mitigantes": [f["claim"] for f in mit], "normas": normas}
        data, meta = self.ask_llm(case, f"Redacta un párrafo de 'Análisis' (4-6 frases, tono formal) para un {tipo} "
                                        "y un resumen ejecutivo de máximo 3 frases para el Oficial de Cumplimiento "
                                        "(qué pasó, por qué importa y qué se recomienda), basado SOLO en estos hallazgos. "
                                        'JSON: {"analisis": str, "resumen_ejecutivo": str}',
                                  payload, {"analisis": "", "resumen_ejecutivo": ""})
        case.shared["resumen_ejecutivo"] = data.get("resumen_ejecutivo") or ""
        txt = base_txt + ("\n\nAnálisis:\n" + data["analisis"] if data.get("analisis") else "")
        return [self.finding(case, tipo, "borrador", 0.0, [], f"{self.name} ({meta['modo']})", txt, kind="borrador",
                             extra={"tipo": "ros" if ros else "no_reporte", "modo_llm": meta["modo"]})]
