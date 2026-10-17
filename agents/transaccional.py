from __future__ import annotations

from core import analytics as A
from core.pseudonymizer import amount_band

from .base import Agent, Case


class AgenteTransaccional(Agent):
    name = "agente_transaccional"
    capabilities = ["analisis_transaccional", "grafo", "tipologias"]
    description = "Arma el perfil y el grafo de la cuenta y detecta tipologías (estructuración, ciclos, fan-in/out, pass-through)."

    def run(self, case: Case) -> list[dict]:
        acc, pol, w, nb = case.account, case.policy, case.window, case.neighborhood
        # tokenizar todas las cuentas del vecindario antes de construir cualquier texto
        for a in sorted(set(nb.sender) | set(nb.receiver) | set(w.sender) | set(w.receiver)):
            case.pseudo.token(a)

        prof = A.profile(w, acc)
        det = {
            "structuring": A.detect_structuring(w, acc, pol),
            "cycle": A.detect_cycles(nb, acc, pol),
            "pass_through": A.detect_pass_through(w, acc, pol),
        }
        det["smurfing"] = A.detect_smurfing(w, acc, pol)
        det["concentracion_dispersion"] = A.detect_gather_scatter(w, acc, pol)
        det["cambio_comportamiento"] = A.detect_behaviour_change(w, acc, case.alert["fecha"], pol)
        fan = A.detect_fan(w, acc, pol) or {}
        det.update(fan)
        det = {k: v for k, v in det.items() if v}
        case.raw["deteccion"] = det
        if "cycle" in det:
            case.raw["miembros_ciclo"] = set(det["cycle"]["ciclo"])

        feats = {"perfil": {**prof, "total_entrante": amount_band(prof["total_entrante"]),
                            "total_saliente": amount_band(prof["total_saliente"])},
                 "tipologias": {k: {kk: (amount_band(vv) if kk in ("total", "monto_min", "monto_max", "monto_promedio", "semana_actual", "media_semanal")
                                         else vv) for kk, vv in v.items() if kk != "evidencia"}
                                for k, v in det.items()}}
        case.shared["rasgos_transaccionales"] = case.pseudo.scrub(feats)
        case.shared["tipologias"] = list(det)

        fb = {"explicaciones": {k: self._template(k, v, case) for k, v in det.items()},
              "hipotesis": "Patrón compatible con " + (", ".join(det) if det else "actividad normal") + "."}
        data, meta = self.ask_llm(
            case,
            "Explica en 1-2 frases por tipología por qué el patrón detectado es (o no) sospechoso, citando los "
            'rasgos numéricos. JSON: {"explicaciones": {tipologia: texto}, "hipotesis": texto}',
            feats, fb)
        expl = data.get("explicaciones") or fb["explicaciones"]

        sev = {"structuring": 0.8, "cycle": 0.85, "pass_through": 0.6, "fan_in": 0.5, "fan_out": 0.4,
               "smurfing": 0.75, "concentracion_dispersion": 0.7, "cambio_comportamiento": 0.55}
        out = []
        for k, v in det.items():
            s = sev.get(k, 0.4)
            if k == "structuring" and v.get("supera_multiples_mes"):
                s += 0.1
            if k == "pass_through" and v.get("salida_internacional"):
                s += 0.1
            out.append(self.finding(case, self._claim(k, v, case), k, min(s, 0.95), v.get("evidencia", []),
                                    f"Transacciones internas + regla determinista ({meta['modo']})",
                                    str(expl.get(k, fb["explicaciones"][k])),
                                    extra=case.pseudo.scrub({k2: v2 for k2, v2 in v.items() if k2 != "evidencia"})
                                    | {"modo_llm": meta["modo"]}))
        if not out:
            out.append(self.finding(case, f"Sin tipologías detectadas para {case.token}", "ninguna", 0.1, [],
                                    "Regla determinista", "Ninguna regla de tipología se activó.", kind="info"))
        out.append(self.finding(case, f"Hipótesis: {data.get('hipotesis', fb['hipotesis'])}", "hipotesis", 0.0, [],
                                f"{self.name} ({meta['modo']})", "Síntesis del agente.", kind="info"))
        return out

    def _claim(self, k, v, case) -> str:
        t = case.token
        if k == "structuring":
            lo, hi = amount_band(v['monto_min']), amount_band(v['monto_max'])
            rango = f"en el rango {lo}" if lo == hi else f"entre {lo} y {hi}"
            return (f"Estructuración: {v['n_operaciones']} depósitos {rango} (umbral {v['umbral']:,.0f}) "
                    f"en {v['dias']} días hacia {t}, "
                    f"desde {v['contrapartes']} cuentas")
        if k == "cycle":
            c = v["ciclo"]
            i = c.index(case.account) if case.account in c else 0
            c = c[i:] + c[:i]
            cyc = " → ".join(case.pseudo.token(a) for a in c)
            return f"Ciclo de layering de {v['longitud']} cuentas en {v['horas']} h: {cyc} → {t}"
        if k == "pass_through":
            intl = " con salida internacional" if v["salida_internacional"] else ""
            return f"Pass-through: sale el {v['ratio_salida_entrada']:.0%} de lo que entra{intl}"
        if k == "smurfing":
            return (f"Pitufeo: {v['n_operaciones']} ingresos pequeños desde {v['contrapartes']} cuentas suman "
                    f"{amount_band(v['total'])} en {v['dias']} días (umbral {v['umbral']:,.0f})")
        if k == "concentracion_dispersion":
            return (f"Concentración-dispersión ({v['patron']}): {v['contrapartes_entrada']} cuentas → {t} → "
                    f"{v['contrapartes_salida']} cuentas en < 72 h")
        if k == "cambio_comportamiento":
            extra = f"; tipos de pago nuevos: {', '.join(v['tipos_pago_nuevos'])}" if v["tipos_pago_nuevos"] else ""
            return f"Cambio de comportamiento: última semana {amount_band(v['semana_actual'])} (z = {v['z']}){extra}"
        if k in ("fan_in", "fan_out"):
            return f"{k}: {v['contrapartes_dia']} contrapartes distintas en un día ({v['dia']})"
        return k

    def _template(self, k, v, case) -> str:
        return {
            "structuring": "Montos repetidos justo bajo el umbral de registro, desde varias cuentas y en pocos días: "
                           "señal clásica de fraccionamiento para evitar el registro de operaciones.",
            "cycle": "Los fondos regresan a la cuenta de origen tras pasar por varias cuentas en poco tiempo, "
                     "con montos casi iguales: típico de layering para ocultar el origen.",
            "pass_through": "La cuenta funciona como puente: los fondos salen casi en su totalidad poco después de entrar.",
            "fan_in": "Muchas contrapartes distintas depositan el mismo día.",
            "fan_out": "La cuenta paga a muchas cuentas distintas el mismo día; puede ser legítimo (planilla) o dispersión.",
            "smurfing": "Muchos ingresos pequeños de distintas cuentas que juntos superan el umbral: pitufeo.",
            "concentracion_dispersion": "Los fondos se concentran y se reparten en pocas horas: la cuenta actúa como nodo de paso.",
            "cambio_comportamiento": "El volumen o el tipo de operaciones de la última semana no se parece al historial de la cuenta.",
        }.get(k, "Patrón detectado por regla.")
