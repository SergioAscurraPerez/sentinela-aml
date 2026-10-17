"""Sentinela AML — UI (Streamlit).

    streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from agents.impostor import attack
from core.orchestrator import ANALISTA, HUMANO, ROLES, ZONAS, Orchestrator

st.set_page_config(page_title="Sentinela AML", page_icon="🛡️", layout="wide")

KIND_ICON = {"riesgo": "🔴", "mitigante": "🟢", "norma": "📜", "info": "ℹ️", "borrador": "📝", "respuesta": "💬"}
KIND_LABEL = {"riesgo": "Señales de riesgo", "mitigante": "Explicaciones legítimas (abogado del diablo)",
              "respuesta": "Respuestas a tus preguntas", "norma": "Normativa aplicable", "info": "Contexto"}
ACCION_LABEL = {"descartar": "Descartar (no sospechosa) con sustento", "vigilancia": "Mantener en vigilancia",
                "escalar_ros": "Calificar como sospechosa → ROS a la UIF"}


def orch() -> Orchestrator:
    if "orch" not in st.session_state:
        st.session_state.orch = Orchestrator()
    return st.session_state.orch


o = orch()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🛡️ Sentinela AML")
    st.caption("Cada conclusión con firma, fuente y dueño.")
    actor = st.radio("Estás trabajando como", [ANALISTA, HUMANO], format_func=ROLES.get)
    es_oficial = actor == HUMANO

    alert_ids = list(o.alerts)
    default = st.session_state.get("aid", alert_ids[0])
    aid = st.selectbox("Caso abierto", alert_ids, index=alert_ids.index(default) if default in alert_ids else 0,
                       format_func=lambda a: f"{a} · {o.alerts[a].get('titulo', o.alerts[a]['regla'])[:40]}")
    st.session_state.aid = aid

    llm_ok = o.llm.available(refresh=st.button("↻ Revisar LLM local", use_container_width=True))
    st.markdown(f"**LLM local:** {'🟢 ' + o.llm.model if llm_ok else '🟡 modo reglas (Ollama no detectado)'}")
    st.markdown(f"**Protocolos NANDA:** {'🟢 nandatown' if o.town.backend == 'nandatown' else '🟡 respaldo local'}")

    st.subheader("Confianza en agentes")
    st.caption("Capa *trust* de NANDA · sube o baja con el feedback humano")
    st.dataframe(pd.DataFrame([{"agente": n, "confianza": o.town.trust.score(n), "peso": round(o._w(n), 2)}
                               for n in o.agents]), hide_index=True, use_container_width=True)
    with st.expander("Registro NANDA (tarjetas verificadas)"):
        for name, e in o.town.registry.cards.items():
            st.markdown(f"{'✅' if e['verified'] else '⛔'} **{name}** `{e['card']['agent_id']}`  \n"
                        f"<small>{', '.join(e['card']['capabilities'])}</small>", unsafe_allow_html=True)
    with st.expander("Política del banco (policy.yaml)"):
        st.json({"triaje": o.policy.get("triaje"), "forzar_humano_si": o.policy.get("forzar_humano_si"),
                 "autonomia": o.policy["autonomia"], "umbrales_registro": o.policy["umbrales_registro"],
                 "plazo_ros_horas": o.policy["plazo_ros_horas"]})
    if st.button("🔄 Reiniciar demo", use_container_width=True):
        o.reset()
        for k in [k for k in st.session_state if k.startswith(("exp_", "imp_", "t_", "draft_"))]:
            del st.session_state[k]
        st.rerun()

case = o.cases.get(aid)
alert = o.alerts[aid]

tab_bandeja, tab_caso, tab_priv, tab_imp = st.tabs(["📥 Bandeja", "🗂️ Caso", "🔒 Privacidad y auditoría", "📈 Impacto"])

# ---------------------------------------------------------------- bandeja
with tab_bandeja:
    pend = [a for a in o.alerts if a not in o.cases]
    cierres = [a for a, c in o.cases.items() if c.shared.get("zona") == "cierre_sugerido" and a not in o.decisions]
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button(f"▶ Investigar todas ({len(pend)} pendientes)", type="primary", disabled=not pend):
        bar = st.progress(0.0, text="Investigando…")
        o.investigate_all(lambda i, n, a: bar.progress(i / n, text=f"{a} ({i}/{n})"))
        st.rerun()
    if b2.button(f"✅ Aprobar cierres sugeridos ({len(cierres)})", disabled=not (cierres and es_oficial),
                 help=None if es_oficial else "Solo el Oficial de Cumplimiento aprueba"):
        done = o.approve_batch(HUMANO, HUMANO)
        st.success(f"{len(done)} cierres aprobados, cada uno con su sustento de no reporte firmado.")

    zc = {z: sum(1 for c in o.cases.values() if c.shared.get("zona") == z) for z in ZONAS}
    m = st.columns(5)
    m[0].metric("Pendientes", len(pend))
    m[1].metric(ZONAS["cierre_sugerido"], zc["cierre_sugerido"])
    m[2].metric(ZONAS["revision_humana"], zc["revision_humana"])
    m[3].metric(ZONAS["escalamiento"], zc["escalamiento"])
    m[4].metric("Decididas", len(o.decisions))

    rows = []
    for a in o.alerts.values():
        c, d = o.cases.get(a["alert_id"]), o.decisions.get(a["alert_id"])
        rows.append({"alerta": a["alert_id"], "caso": a.get("titulo", a["regla"]), "fecha": a["fecha"],
                     "cola": ZONAS.get(c.shared.get("zona"), "") if c else "⏳ sin investigar",
                     "riesgo": c.shared.get("riesgo") if c else None,
                     "confianza": c.shared.get("confianza") if c else None,
                     "estado": ("✔ " + d["accion"]) if d else ("en revisión" if c else "nueva"),
                     "origen": a["origen"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=420,
                 column_config={"riesgo": st.column_config.ProgressColumn("riesgo", min_value=0, max_value=1, format="%.2f"),
                                "confianza": st.column_config.ProgressColumn("confianza", min_value=0, max_value=1, format="%.2f")})
    st.caption("Ninguna cola cierra sola: el 'cierre sugerido' lo aprueba el Oficial, y un % aleatorio igual pasa a "
               "revisión humana (control de calidad).")

# ---------------------------------------------------------------- caso
with tab_caso:
    h1, h2 = st.columns([4, 1])
    h1.markdown(f"### {alert.get('titulo', alert['regla'])}")
    h1.caption(f"{aid} · Regla: {alert['regla']} · Fecha: {alert['fecha']} · Origen: {alert['origen']} · "
               f"Moneda: {alert.get('moneda', 'USD')}")
    run = h2.button("▶ Investigar con agentes" if not case else "↻ Re-investigar", type="primary",
                    use_container_width=True)

    if run:
        t0 = datetime.now()
        with st.status("Equipo de agentes investigando…", expanded=True) as status:
            def show(ev):
                k = ev["kind"]
                if k == "registry.descubrimiento":
                    status.write(f"🔎 Registro NANDA → **{ev['detail']['agente']}** para *{ev['detail']['capacidad']}*")
                elif k == "agente.fin":
                    status.write(f"✅ **{ev['actor']}** · {ev['detail']['hallazgos']} hallazgos firmados · {ev['latency_ms']} ms")
                elif k == "llm.llamada":
                    status.write(f"🧠 {ev['actor']} razonó con el LLM local ({ev['detail']['modo']})")
                elif k == "privacidad.pseudonimizacion":
                    status.write(f"🔒 Datos pseudonimizados: cuenta → **{ev['detail']['cuenta']}**")
                elif k == "triaje.calculado":
                    status.write(f"🧭 Triaje: **{ZONAS[ev['detail']['zona']]}** (riesgo {ev['detail']['riesgo']}, "
                                 f"confianza {ev['detail']['confianza']})")
            o.log.listeners.append(show)
            try:
                case = o.investigate(aid)
            finally:
                o.log.listeners.remove(show)
            secs = (datetime.now() - t0).total_seconds()
            st.session_state[f"t_{aid}"] = secs
            st.session_state.pop(f"exp_{aid}", None)
            status.update(label=f"Investigación completa en {secs:.1f} s", state="complete", expanded=False)

    if not case:
        st.info("Presiona **Investigar con agentes**: 6 agentes especialistas preparan el caso con evidencia firmada.")
    else:
        zona = case.shared.get("zona", "revision_humana")
        motivos = case.shared.get("motivos_triaje", [])
        box = {"cierre_sugerido": st.success, "revision_humana": st.warning, "escalamiento": st.error}[zona]
        box(f"**{ZONAS[zona]}** — " + " · ".join(motivos[:3]))
        m = st.columns(4)
        m[0].metric("Riesgo", f"{case.shared.get('riesgo', 0):.2f}", help="Qué tan sospechoso es el caso")
        m[1].metric("Confianza del sistema", f"{case.shared.get('confianza', 0):.2f}",
                    help="Qué tan seguro está el sistema; baja si los agentes no coinciden o faltan datos")
        m[2].metric("Hallazgos firmados", len(case.findings))
        m[3].metric("Tiempo de preparación", f"{st.session_state.get(f't_{aid}', 0):.1f} s", "vs ~30 min manual",
                    delta_color="off")
        top = [f["claim"] for f in sorted(case.findings, key=lambda f: -f["severity"]) if f["kind"] == "riesgo"][:3]
        mit = [f["claim"] for f in case.findings if f["kind"] == "mitigante"]
        st.markdown("**En resumen:** " + ("; ".join(top) if top else "sin señales de riesgo relevantes")
                    + (f". **Pero:** {mit[0]}" if mit else "") + ".")

        left, right = st.columns([3, 2])
        with left:
            for kind in ["riesgo", "mitigante", "respuesta", "norma", "info"]:
                fs = [f for f in case.findings if f["kind"] == kind]
                if not fs:
                    continue
                with st.expander(f"{KIND_ICON[kind]} {KIND_LABEL[kind]} ({len(fs)})",
                                 expanded=kind in ("riesgo", "mitigante", "respuesta")):
                    for f in fs:
                        with st.container(border=True):
                            estado = {"validado": "✅ validado", "rechazado": "❌ rechazado"}.get(f.get("estado"), "⏳ pendiente")
                            st.markdown(f"**{f['claim']}**  \n<small>{f['agent']} · severidad {f['severity']} · "
                                        f"firma `{f['signature'][:12]}…` · {estado}</small>", unsafe_allow_html=True)
                            st.caption(f["reasoning"][:400])
                            if f["evidence"]:
                                tx = case.window[case.window.tx_id.isin(f["evidence"])]
                                if len(tx):
                                    with st.popover(f"Ver evidencia ({len(tx)} transacciones)"):
                                        st.dataframe(pd.DataFrame({
                                            "fecha": tx.timestamp.dt.strftime("%Y-%m-%d %H:%M"),
                                            "de": [case.pseudo.token(x) for x in tx.sender],
                                            "a": [case.pseudo.token(x) for x in tx.receiver],
                                            "monto": tx.amount.round(0), "tipo": tx.payment_type}),
                                            hide_index=True, use_container_width=True)
                            if f["extra"].get("preguntas"):
                                for qi, qq in enumerate(f["extra"]["preguntas"][:3]):
                                    if st.button(f"❓ {qq}", key=f"q_{f['finding_id']}_{qi}"):
                                        o.ask(aid, "agente_transaccional", qq)
                                        st.rerun()
                            if kind in ("riesgo", "mitigante") and aid not in o.decisions:
                                b1, b2, _ = st.columns([1, 1, 3])
                                if b1.button("✅ Válido", key=f"ok_{f['finding_id']}"):
                                    o.feedback(aid, f["finding_id"], True, actor=actor)
                                    st.rerun()
                                if b2.button("❌ Incorrecto", key=f"ko_{f['finding_id']}"):
                                    o.feedback(aid, f["finding_id"], False, actor=actor)
                                    st.rerun()

            with st.expander("🕸️ Grafo de contrapartes (pseudonimizado)"):
                try:
                    from pyvis.network import Network
                    full = st.toggle("Ver vecindario completo", key=f"full_{aid}")
                    nb = case.neighborhood
                    ev_ids = {e for f in case.findings for e in f["evidence"]}
                    if not full:
                        nb = nb[nb.tx_id.isin(ev_ids) | (nb.sender == case.account) | (nb.receiver == case.account)]
                    net = Network(height="430px", width="100%", directed=True, bgcolor="#0e1117", font_color="#ddd")
                    hot = {a for r in nb.itertuples() if r.tx_id in ev_ids for a in (r.sender, r.receiver)}
                    for n in set(nb.sender) | set(nb.receiver):
                        tok = case.pseudo.token(n)
                        color = "#ff4b4b" if n == case.account else ("#ffa500" if n in hot else "#4a6fa5")
                        net.add_node(tok, label=tok, color=color, size=22 if n == case.account else 10)
                    for r in nb.itertuples():
                        hot_e = r.tx_id in ev_ids
                        net.add_edge(case.pseudo.token(r.sender), case.pseudo.token(r.receiver),
                                     color="#ffa500" if hot_e else "#555", width=2.5 if hot_e else 0.6)
                    net.set_options('{"physics": {"stabilization": {"iterations": 150}}}')
                    components.html(net.generate_html(), height=450)
                    st.caption("🔴 cuenta alertada · 🟠 cuentas con evidencia · 🔵 otras contrapartes")
                except Exception as exc:
                    st.info(f"Grafo no disponible ({exc}). Instala pyvis.")

            with st.expander("🛰️ Bitácora de agentes (todo queda registrado)"):
                evs = o.log.list(aid)
                st.dataframe(pd.DataFrame([{"hora": e["ts"][11:], "actor": e["actor"], "evento": e["kind"],
                                            "estado": e["status"], "ms": e["latency_ms"]} for e in evs]),
                             hide_index=True, use_container_width=True, height=360)

        with right:
            st.markdown("#### 💬 Pedir más información")
            ag = st.selectbox("Agente", [n for n in o.agents if n != "agente_redactor"], key="ask_ag")
            q = st.text_input("Pregunta", placeholder="¿La contraparte principal tiene historial previo?")
            if st.button("Preguntar") and q:
                o.ask(aid, ag, q)
                st.rerun()

            st.markdown("#### 📝 Borrador del agente redactor")
            draft = next((f for f in case.findings if f["kind"] == "borrador"), None)
            if st.button("↻ Regenerar con el feedback actual"):
                o.redraft(aid)
                st.rerun()
            if draft:
                st.text_area("Borrador (editable)", draft["reasoning"], height=220, key=f"draft_{aid}")

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
                accion = st.radio("Acción", opts, index=opts.index(sug) if sug in opts else 1,
                                  format_func=ACCION_LABEL.get)
                just = st.text_area("Justificación (obligatoria: queda en el expediente 10 años)")
                just2 = None
                if accion == "descartar" and case.shared.get("riesgo", 0) > o.policy["autonomia"]["doble_justificacion_si_riesgo_mayor_a"]:
                    just2 = st.text_area("⚠️ Riesgo alto: segunda justificación obligatoria")
                if st.button("Firmar decisión", type="primary"):
                    try:
                        o.decide(aid, HUMANO, HUMANO, accion, just, just2)
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.warning(str(exc))

            st.markdown("#### 📦 Expediente")
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

            st.divider()
            if st.button("😈 Simular agente impostor"):
                st.session_state[f"imp_{aid}"] = attack(o, aid)
            r = st.session_state.get(f"imp_{aid}")
            if r:
                st.error(f"Un impostor intentó colar 'DESCARTAR' suplantando a agente_transaccional → hallazgo "
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
        recall = (lav.cola != "cierre_sugerido").mean() if len(lav) else 0
        filt = (nor.cola == "cierre_sugerido").mean() if len(nor) else 0
        aut = (df.cola == "cierre_sugerido").mean()
        k = st.columns(4)
        k[0].metric("Lavado que NO se va a cierre", f"{recall:.0%}",
                    f"{(lav.cola != 'cierre_sugerido').sum()}/{len(lav)}", delta_color="off")
        k[1].metric("Normales a cierre sugerido", f"{filt:.0%}",
                    f"{(nor.cola == 'cierre_sugerido').sum()}/{len(nor)}", delta_color="off")
        k[2].metric("Alertas sin investigación completa", f"{aut:.0%}")
        k[3].metric("Alertas evaluadas", len(df))
        st.dataframe(pd.crosstab(df.real, df.cola.map(ZONAS)), use_container_width=True)
        with st.expander("Detalle por tipología"):
            st.dataframe(pd.crosstab(df.tipologia_real, df.cola.map(ZONAS)), use_container_width=True)

        st.markdown("##### Horas-analista ahorradas (supuestos editables)")
        c1, c2, c3 = st.columns(3)
        n_mes = c1.number_input("Alertas por mes", 100, 100000, 1000, step=100)
        manual = c2.number_input("Minutos por alerta (manual)", 5, 120, 30)
        nuevo = c3.number_input("Minutos por alerta con Sentinela", 1, 60, 5)
        st.metric("Horas-analista ahorradas al mes", f"{n_mes * (manual - nuevo) / 60:,.0f} h",
                  help="alertas/mes × (min manual − min con Sentinela) / 60. Supuestos editables; los 20–60 min "
                       "por alerta son estimaciones de la industria (Facctum, Sayari).")
