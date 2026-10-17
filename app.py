"""Sentinela AML — UI (Streamlit).

    streamlit run app.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from agents.impostor import attack
from core.live import OCUPACIONES, TIPOLOGIAS, default_params
from core.orchestrator import ANALISTA, HUMANO, ROLES, ZONAS, Orchestrator
from ui import components as ui

st.set_page_config(page_title="Sentinela AML", page_icon="🛡️", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

KIND_LABEL = {"riesgo": "🔴 Señales de riesgo", "mitigante": "🟢 Explicaciones legítimas (abogado del diablo)",
              "respuesta": "💬 Respuestas a tus preguntas", "norma": "📜 Normativa aplicable", "info": "ℹ️ Contexto"}
ACCION_LABEL = {"descartar": "Descartar (no sospechosa) con sustento", "vigilancia": "Mantener en vigilancia",
                "escalar_ros": "Calificar como sospechosa → ROS a la UIF"}


def orch() -> Orchestrator:
    if "orch" not in st.session_state:
        st.session_state.orch = Orchestrator()
    return st.session_state.orch


def _llm_badge(modo: str | None) -> str:
    return {"llm": "🧠 razonó con LLM local", "cache": "🧠 LLM (caché)", "bloqueado": "🔒 bloqueado por privacidad"}.get(
        modo or "", "⚙️ reglas (LLM apagado)")


def agent_state_from_log(events: list[dict]) -> dict:
    stt: dict[str, dict] = {}
    for e in events:
        a = e["actor"]
        if e["kind"] == "agente.inicio":
            stt[a] = {"st": "run", "badges": []}
        elif e["kind"] == "agente.fin":
            stt.setdefault(a, {"badges": []}).update(st="done" if e["status"] == "ok" else "err",
                                                     n=e["detail"].get("hallazgos", 0), ms=e["latency_ms"])
        elif e["kind"] == "llm.llamada":
            stt.setdefault(a, {"st": "run", "badges": []})["badges"].append(_llm_badge(e["detail"].get("modo")))
        elif e["kind"] == "registry.descubrimiento":
            stt.setdefault(e["detail"]["agente"], {"st": "wait", "badges": []})["badges"].append("🔎 descubierto en NANDA")
    return stt


o = orch()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 🛡️ Sentinela AML")
    st.caption("Cada conclusión con firma, fuente y dueño.")
    actor = st.radio("Estás trabajando como", [ANALISTA, HUMANO], format_func=ROLES.get)
    es_oficial = actor == HUMANO
    alert_ids = list(o.alerts)
    default = st.session_state.get("aid", alert_ids[0])
    aid = st.selectbox("Caso abierto", alert_ids, index=alert_ids.index(default) if default in alert_ids else 0,
                       format_func=lambda a: f"{a} · {o.alerts[a].get('titulo', o.alerts[a]['regla'])[:42]}")
    st.session_state.aid = aid
    llm_ok = o.llm.available(refresh=st.button("↻ Revisar LLM local", use_container_width=True))
    st.markdown(f"**LLM local:** {'🟢 ' + o.llm.model if llm_ok else '🟡 modo reglas (Ollama no detectado)'}  \n"
                f"**Protocolos NANDA:** {'🟢 nandatown' if o.town.backend == 'nandatown' else '🟡 respaldo local'}")
    st.markdown("**Confianza en agentes** · capa *trust*")
    st.dataframe(pd.DataFrame([{"agente": ui.AGENT_NAME.get(n, n), "reputación": o.town.trust.score(n),
                                "peso": round(o._w(n), 2)} for n in o.agents]), hide_index=True, use_container_width=True)
    with st.expander("Registro NANDA (tarjetas verificadas)"):
        for name, e in o.town.registry.cards.items():
            st.markdown(f"{'✅' if e['verified'] else '⛔'} **{name}** `{e['card']['agent_id']}`  \n"
                        f"<small>{', '.join(e['card']['capabilities'])}</small>", unsafe_allow_html=True)
    with st.expander("Política del banco (policy.yaml)"):
        st.json({"triaje": o.policy.get("triaje"), "forzar_humano_si": o.policy.get("forzar_humano_si"),
                 "autonomia": o.policy["autonomia"], "umbrales_registro": o.policy["umbrales_registro"]})
    if st.button("🔄 Reiniciar demo", use_container_width=True):
        o.reset()
        for k in [k for k in st.session_state if k.startswith(("exp_", "imp_", "t_", "draft_", "reveal_"))]:
            del st.session_state[k]
        st.rerun()

case = o.cases.get(aid)
alert = o.alerts[aid]
tab_bandeja, tab_caso, tab_priv, tab_imp = st.tabs(["📥 Bandeja", "🗂️ Caso", "🔒 Privacidad y auditoría", "📈 Impacto"])

# ---------------------------------------------------------------- bandeja
with tab_bandeja:
    with st.container(border=True):
        st.markdown("#### 🧪 Crear una alerta en vivo")
        st.caption("Nada está preparado: elige una tipología (o sorpresa) y se generan transacciones nuevas en este momento.")
        c1, c2, c3 = st.columns([2, 2, 1])
        tipo = c1.selectbox("Tipología", ["sorpresa", *TIPOLOGIAS],
                            format_func=lambda t: "🎲 Sorpresa (el sistema no lo sabe, tú tampoco)" if t == "sorpresa" else TIPOLOGIAS[t])
        ocup = c2.selectbox("Perfil declarado del cliente", list(OCUPACIONES))
        p = default_params(tipo) if tipo != "sorpresa" else {}
        if tipo != "sorpresa":
            d1, d2, d3, d4 = st.columns(4)
            p = {"n": d1.slider("Nº de operaciones / cuentas", 3, 40, int(p["n"])),
                 "monto": d2.number_input("Monto típico (USD)", 100.0, 500_000.0, float(p["monto"]), step=100.0),
                 "dias": d3.slider("Días", 1, 30, int(max(p["dias"], 1))),
                 "salida_exterior": d4.checkbox("Sale al exterior", bool(p["salida_exterior"]))}
        if c3.button("Crear y abrir", type="primary", use_container_width=True):
            new = o.add_live_alert(tipo, p, ocup)
            st.session_state.aid = new
            st.session_state[f"autorun_{new}"] = True
            st.rerun()

    pend = [a for a in o.alerts if a not in o.cases]
    cierres = [a for a, c in o.cases.items() if c.shared.get("zona") == "cierre_sugerido" and a not in o.decisions]
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button(f"▶ Investigar todas ({len(pend)} pendientes)", disabled=not pend):
        bar = st.progress(0.0, text="Investigando…")
        o.investigate_all(lambda i, n, a: bar.progress(i / n, text=f"{a} ({i}/{n})"))
        st.rerun()
    if b2.button(f"✅ Aprobar cierres sugeridos ({len(cierres)})", disabled=not (cierres and es_oficial),
                 help=None if es_oficial else "Solo el Oficial de Cumplimiento aprueba"):
        done = o.approve_batch(HUMANO, HUMANO)
        st.success(f"{len(done)} cierres aprobados, cada uno con su sustento de no reporte firmado.")

    zc = {z: sum(1 for c in o.cases.values() if c.shared.get("zona") == z) for z in ZONAS}
    k = st.columns(5)
    for col, (v, l) in zip(k, [(len(pend), "pendientes"), (zc["cierre_sugerido"], ZONAS["cierre_sugerido"]),
                               (zc["revision_humana"], ZONAS["revision_humana"]), (zc["escalamiento"], ZONAS["escalamiento"]),
                               (len(o.decisions), "decididas por un humano")]):
        col.markdown(ui.kpi(str(v), l), unsafe_allow_html=True)
    rows = []
    for a in o.alerts.values():
        c, d = o.cases.get(a["alert_id"]), o.decisions.get(a["alert_id"])
        rows.append({"alerta": a["alert_id"], "caso": a.get("titulo", a["regla"]), "fecha": a["fecha"],
                     "cola": ZONAS.get(c.shared.get("zona"), "") if c else "⏳ sin investigar",
                     "riesgo": c.shared.get("riesgo") if c else None, "confianza": c.shared.get("confianza") if c else None,
                     "estado": ("✔ " + d["accion"]) if d else ("en revisión" if c else "nueva"), "origen": a["origen"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=380,
                 column_config={"riesgo": st.column_config.ProgressColumn("riesgo", min_value=0, max_value=1, format="%.2f"),
                                "confianza": st.column_config.ProgressColumn("confianza", min_value=0, max_value=1, format="%.2f")})
    st.caption("Ninguna cola cierra sola: el cierre sugerido lo aprueba el Oficial y un % aleatorio igual pasa a revisión humana.")

# ---------------------------------------------------------------- caso
with tab_caso:
    zona = case.shared.get("zona") if case else None
    meta = (f"{aid} · {alert['regla']} · {alert['fecha']} · origen {alert['origen']} · {alert.get('moneda', 'USD')}")
    st.markdown(ui.hero(alert.get("titulo", alert["regla"]), meta, zona, ZONAS.get(zona),
                        case.shared.get("motivos_triaje", []) if case else []), unsafe_allow_html=True)

    autorun = st.session_state.pop(f"autorun_{aid}", False)
    run = st.button("▶ Investigar con el equipo de agentes" if not case else "↻ Re-investigar", type="primary")
    grid = st.empty()
    if run or autorun:
        state: dict[str, dict] = {}
        grid.markdown(ui.agent_grid(state), unsafe_allow_html=True)
        pace = 0 if o.llm.available() else 0.25

        def show(ev):
            k = ev["kind"]
            if k == "registry.descubrimiento":
                state.setdefault(ev["detail"]["agente"], {"st": "wait", "badges": []})["badges"].append("🔎 descubierto en NANDA")
            elif k == "agente.inicio":
                state[ev["actor"]] = {"st": "run", "badges": state.get(ev["actor"], {}).get("badges", [])}
            elif k == "llm.llamada":
                state.setdefault(ev["actor"], {"st": "run", "badges": []})["badges"].append(_llm_badge(ev["detail"]["modo"]))
            elif k == "agente.fin":
                state.setdefault(ev["actor"], {"badges": []}).update(st="done" if ev["status"] == "ok" else "err",
                                                                     n=ev["detail"]["hallazgos"], ms=ev["latency_ms"])
            else:
                return
            grid.markdown(ui.agent_grid(state), unsafe_allow_html=True)
            if k == "agente.inicio" and pace:
                time.sleep(pace)  # solo para que el ojo humano vea el relevo entre agentes en modo reglas
        t0 = datetime.now()
        o.log.listeners.append(show)
        try:
            case = o.investigate(aid)
        finally:
            o.log.listeners.remove(show)
        st.session_state[f"t_{aid}"] = (datetime.now() - t0).total_seconds()
        st.session_state.pop(f"exp_{aid}", None)
        st.rerun()

    if not case:
        grid.markdown(ui.agent_grid({}), unsafe_allow_html=True)
        st.info("Presiona **Investigar**: 6 agentes especialistas se descubren por el registro NANDA, analizan el caso "
                "en paralelo y firman cada hallazgo.")
    else:
        grid.markdown(ui.agent_grid(agent_state_from_log(o.log.list(aid))), unsafe_allow_html=True)
        r1, r2, r3 = st.columns([1.1, 1.1, 3])
        risk, conf = case.shared.get("riesgo", 0), case.shared.get("confianza", 0)
        r1.markdown(ui.ring(risk, "Riesgo", ui.risk_color(risk)), unsafe_allow_html=True)
        r2.markdown(ui.ring(conf, "Confianza del sistema", ui.TEAL if conf >= 0.75 else ui.AMBER), unsafe_allow_html=True)
        with r3:
            kk = st.columns(3)
            kk[0].markdown(ui.kpi(f"{st.session_state.get(f't_{aid}', 0):.1f} s", "preparación del caso (vs ~30 min manual)"), unsafe_allow_html=True)
            kk[1].markdown(ui.kpi(str(len(case.findings)), "hallazgos firmados (NANDA)"), unsafe_allow_html=True)
            kk[2].markdown(ui.kpi(str(len(case.window)), "transacciones analizadas"), unsafe_allow_html=True)
            top = [f["claim"] for f in sorted(case.findings, key=lambda f: -f["severity"]) if f["kind"] == "riesgo"][:3]
            mit = [f["claim"] for f in case.findings if f["kind"] == "mitigante"]
            st.markdown("**En resumen:** " + ("; ".join(top) if top else "sin señales de riesgo relevantes")
                        + (f". **Pero:** {mit[0]}" if mit else "") + ".")
            if alert.get("sorpresa"):
                if st.toggle("👀 Revelar la tipología real del caso sorpresa", key=f"reveal_{aid}"):
                    st.success(f"Tipología generada: **{TIPOLOGIAS.get(alert['etiqueta_real'], 'Cliente normal / planilla')}**"
                               if alert["etiqueta_real"] != "Normal" else "Tipología generada: **sin lavado (normal o planilla)**")

        st.markdown("#### 🕸️ El movimiento del dinero")
        full = st.toggle("Ver vecindario completo (2 saltos)", key=f"full_{aid}")
        components.html(ui.graph_html(case, full=full), height=540)

        left, right = st.columns([3, 2])
        with left:
            for kind in ["riesgo", "mitigante", "respuesta", "norma", "info"]:
                fs = [f for f in case.findings if f["kind"] == kind]
                if not fs:
                    continue
                with st.expander(f"{KIND_LABEL[kind]} ({len(fs)})", expanded=kind in ("riesgo", "mitigante", "respuesta")):
                    for f in fs:
                        st.markdown(ui.finding_card(f), unsafe_allow_html=True)
                        cols = st.columns([1.8, 1.1, 1.3, 1])
                        with cols[0].popover("🔍 Por qué y evidencia"):
                            st.write(f["reasoning"])
                            st.caption(f"Fuente: {f['source']}")
                            tx = case.window[case.window.tx_id.isin(f["evidence"])]
                            if len(tx):
                                st.dataframe(pd.DataFrame({"fecha": tx.timestamp.dt.strftime("%Y-%m-%d %H:%M"),
                                                           "de": [case.pseudo.token(x) for x in tx.sender],
                                                           "a": [case.pseudo.token(x) for x in tx.receiver],
                                                           "monto": tx.amount.round(0), "tipo": tx.payment_type}),
                                             hide_index=True, use_container_width=True)
                        if kind in ("riesgo", "mitigante") and aid not in o.decisions:
                            estado = f.get("estado")
                            if cols[1].button("✅ Válido" if estado != "validado" else "✅ validado", key=f"ok_{f['finding_id']}",
                                              disabled=estado == "validado"):
                                o.feedback(aid, f["finding_id"], True, actor=actor)
                                st.rerun()
                            if cols[2].button("❌ Incorrecto" if estado != "rechazado" else "❌ rechazado",
                                              key=f"ko_{f['finding_id']}", disabled=estado == "rechazado"):
                                o.feedback(aid, f["finding_id"], False, actor=actor)
                                st.rerun()
                        for qi, qq in enumerate((f["extra"].get("preguntas") or [])[:2]):
                            if st.button(f"❓ {qq}", key=f"q_{f['finding_id']}_{qi}"):
                                o.ask(aid, "agente_transaccional", qq)
                                st.rerun()

            with st.expander("🛰️ Línea de tiempo de los agentes y bitácora"):
                sw = ui.swimlane_df(o.log.list(aid))
                if len(sw):
                    st.altair_chart(alt.Chart(sw).mark_bar(cornerRadius=4, height=18).encode(
                        x=alt.X("inicio:Q", title="ms desde el inicio"), x2="fin:Q",
                        y=alt.Y("agente:N", sort=None, title=None),
                        color=alt.Color("estado:N", scale=alt.Scale(domain=["ok", "error"], range=[ui.TEAL, ui.RED]), legend=None),
                        tooltip=["agente", "ms"]).properties(height=220), use_container_width=True)
                evs = o.log.list(aid)
                st.dataframe(pd.DataFrame([{"hora": e["ts"][11:], "actor": e["actor"], "evento": e["kind"],
                                            "estado": e["status"], "ms": e["latency_ms"]} for e in evs]),
                             hide_index=True, use_container_width=True, height=300)

        with right:
            with st.container(border=True):
                st.markdown("#### 💬 Pedir más información")
                ag = st.selectbox("Agente", [n for n in o.agents if n != "agente_redactor"], key="ask_ag",
                                  format_func=lambda n: ui.AGENT_NAME.get(n, n))
                q = st.text_input("Pregunta", placeholder="¿La contraparte principal tiene historial previo?")
                if st.button("Preguntar") and q:
                    o.ask(aid, ag, q)
                    st.rerun()
            with st.container(border=True):
                st.markdown("#### 📝 Borrador del agente redactor")
                draft = next((f for f in case.findings if f["kind"] == "borrador"), None)
                if st.button("↻ Regenerar con el feedback actual"):
                    o.redraft(aid)
                    st.rerun()
                if draft:
                    st.text_area("Borrador (editable)", draft["reasoning"], height=200, key=f"draft_{aid}")
            with st.container(border=True):
                st.markdown("#### 🧑‍⚖️ Decisión")
                dec = o.decisions.get(aid)
                if dec:
                    st.success(f"Firmada: **{ACCION_LABEL[dec['accion']]}** · {ROLES.get(dec['actor'], dec['actor'])} · {dec['ts']}")
                    if dec.get("vence_ros"):
                        left_h = (datetime.fromisoformat(dec["vence_ros"]) - datetime.now()).total_seconds() / 3600
                        st.error(f"⏱️ ROS a la UIF-Perú vence {dec['vence_ros']} · quedan {left_h:.1f} h")
                elif not es_oficial:
                    st.info("Como **Analista** validas hallazgos y preguntas a los agentes. La decisión la firma el "
                            "**Oficial de Cumplimiento** (cambia el rol en la barra lateral).")
                else:
                    sug = o.suggestion(case).replace("sugerir_", "")
                    opts = list(ACCION_LABEL)
                    accion = st.radio("Acción", opts, index=opts.index(sug) if sug in opts else 1, format_func=ACCION_LABEL.get)
                    just = st.text_area("Justificación (obligatoria: queda en el expediente 10 años)")
                    just2 = None
                    if accion == "descartar" and risk > o.policy["autonomia"]["doble_justificacion_si_riesgo_mayor_a"]:
                        just2 = st.text_area("⚠️ Riesgo alto: segunda justificación obligatoria")
                    if st.button("Firmar decisión", type="primary"):
                        try:
                            o.decide(aid, HUMANO, HUMANO, accion, just, just2)
                            st.rerun()
                        except (ValueError, PermissionError) as exc:
                            st.warning(str(exc))
            with st.container(border=True):
                st.markdown("#### 📦 Expediente verificable")
                e1, e2, e3 = st.columns(3)
                if e1.button("Exportar"):
                    st.session_state[f"exp_{aid}"] = o.export(aid)
                exp = st.session_state.get(f"exp_{aid}")
                if exp:
                    e2.download_button("⬇ JSON", json.dumps(exp, ensure_ascii=False, indent=1, default=str),
                                       file_name=f"expediente_{aid}.json", mime="application/json")
                    if e3.button("🔏 Verificar"):
                        v = o.verify(exp)
                        ok = v["hash_integro"] and v["firmas_ok"]
                        (st.success if ok else st.error)(
                            f"Integridad {'✅' if v['hash_integro'] else '❌'} · Firmas de agentes "
                            f"{'✅' if v['firmas_ok'] else '❌'} · Decisión firmada: {v['decision_firmada_ok']}")
                    st.caption(f"sha256 {exp['sha256'][:24]}… · conservar hasta {exp['conservar_hasta']}")
            with st.container(border=True):
                st.markdown("#### 😈 Prueba de seguridad")
                if st.button("Lanzar un agente impostor"):
                    st.session_state[f"imp_{aid}"] = attack(o, aid)
                r = st.session_state.get(f"imp_{aid}")
                if r:
                    st.error(f"Un impostor intentó colar 'DESCARTAR' suplantando al agente transaccional → hallazgo "
                             f"{'ACEPTADO ⚠️' if r['hallazgo_aceptado'] else 'RECHAZADO por firma inválida ✅'} · decisión "
                             f"{'BLOQUEADA: los agentes no deciden ✅' if r['decision_bloqueada'] else 'EJECUTADA ⚠️'}")

# ---------------------------------------------------------------- privacidad
with tab_priv:
    st.subheader("🔒 Lo que vio el modelo")
    st.caption("Contenido EXACTO enviado al LLM local. Sin nombres, DNI, cuentas reales ni montos exactos.")
    evs = [e for e in o.log.list(aid) if e["kind"] in ("llm.llamada", "llm.error", "privacy.bloqueado")]
    if not evs:
        st.info("Aún no hay llamadas al LLM para este caso.")
    for e in evs:
        seen = e["detail"].get("lo_que_vio_el_modelo", {})
        with st.expander(f"{e['ts'][11:]} · {e['actor']} · modo {e['detail'].get('modo', e['kind'])}"):
            st.code(seen.get("user", json.dumps(e["detail"], ensure_ascii=False)), language="json")
    st.subheader("Evidencia NANDA (data_facts)")
    st.dataframe(pd.DataFrame([{k: r[k] for k in ("record_id", "observer", "claim", "signature")}
                               for r in o.town.facts.about(aid)]), hide_index=True, use_container_width=True)
    st.subheader("Re-identificación (solo Oficial de Cumplimiento)")
    if st.toggle("Mostrar datos reales del cliente (queda registrado en la bitácora)", key=f"reid_{aid}"):
        try:
            st.json(o.reidentify(aid, actor=actor))
        except PermissionError as exc:
            st.warning(str(exc))

# ---------------------------------------------------------------- impacto
with tab_imp:
    st.subheader("📈 Impacto y modo sombra")
    st.caption("Modo sombra: el sistema corre sobre alertas con resultado conocido y se compara contra esa etiqueta, "
               "sin decidir nada (así se valida antes de un piloto).")
    lab = [(a, c) for a, c in o.cases.items() if o.alerts[a].get("etiqueta_real")]
    if not lab:
        st.info("Investiga las alertas (Bandeja → Investigar todas) para ver el modo sombra.")
    else:
        df = pd.DataFrame([{"alerta": a, "real": "Lavado" if o.alerts[a]["etiqueta_real"] != "Normal" else "Normal",
                            "tipologia_real": o.alerts[a]["etiqueta_real"], "cola": c.shared["zona"]} for a, c in lab])
        lav, nor = df[df.real == "Lavado"], df[df.real == "Normal"]
        k = st.columns(4)
        vals = [(f"{(lav.cola != 'cierre_sugerido').mean():.0%}" if len(lav) else "—", f"lavado que NO va a cierre ({(lav.cola != 'cierre_sugerido').sum()}/{len(lav)})"),
                (f"{(nor.cola == 'cierre_sugerido').mean():.0%}" if len(nor) else "—", f"normales a cierre sugerido ({(nor.cola == 'cierre_sugerido').sum()}/{len(nor)})"),
                (f"{(df.cola == 'cierre_sugerido').mean():.0%}", "alertas sin investigación completa"), (str(len(df)), "alertas evaluadas")]
        for col, (v, l) in zip(k, vals):
            col.markdown(ui.kpi(v, l), unsafe_allow_html=True)
        st.dataframe(pd.crosstab(df.real, df.cola.map(ZONAS)), use_container_width=True)
        with st.expander("Detalle por tipología"):
            st.dataframe(pd.crosstab(df.tipologia_real, df.cola.map(ZONAS)), use_container_width=True)
        st.markdown("##### Horas-analista ahorradas (supuestos editables)")
        c1, c2, c3 = st.columns(3)
        n_mes = c1.number_input("Alertas por mes", 100, 100000, 1000, step=100)
        manual = c2.number_input("Minutos por alerta (manual)", 5, 120, 30)
        nuevo = c3.number_input("Minutos por alerta con Sentinela", 1, 60, 5)
        st.markdown(ui.kpi(f"{n_mes * (manual - nuevo) / 60:,.0f} h", "horas-analista ahorradas al mes · los 20–60 min por "
                           "alerta son estimaciones de la industria (Facctum, Sayari)"), unsafe_allow_html=True)
