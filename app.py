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
from core.orchestrator import HUMANO, Orchestrator

st.set_page_config(page_title="Sentinela AML", page_icon="🛡️", layout="wide")

KIND_ICON = {"riesgo": "🔴", "mitigante": "🟢", "norma": "📜", "info": "ℹ️", "borrador": "📝", "respuesta": "💬"}
ACCION_LABEL = {"descartar": "Descartar (no sospechosa)", "vigilancia": "Mantener en vigilancia",
                "escalar_ros": "Calificar como sospechosa → ROS"}


def orch() -> Orchestrator:
    if "orch" not in st.session_state:
        st.session_state.orch = Orchestrator()
    return st.session_state.orch


o = orch()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🛡️ Sentinela AML")
    st.caption("Cada conclusión con firma, fuente y dueño.")
    alert_ids = list(o.alerts)
    aid = st.selectbox("Alerta", alert_ids,
                       format_func=lambda a: f"{a} · {o.alerts[a]['regla'][:38]}…",
                       index=alert_ids.index(st.session_state.get("aid", alert_ids[0])))
    st.session_state.aid = aid

    llm_ok = o.llm.available(refresh=st.button("↻ Revisar LLM local", use_container_width=True))
    st.markdown(f"**LLM local:** {'🟢 ' + o.llm.model if llm_ok else '🟡 modo reglas (Ollama no detectado)'}")
    st.markdown(f"**Protocolos NANDA:** {'🟢 nandatown' if o.town.backend == 'nandatown' else '🟡 respaldo local'}")

    st.subheader("Confianza en agentes")
    st.caption("Capa *trust* de NANDA · sube/baja con el feedback del oficial")
    st.dataframe(pd.DataFrame([{"agente": n, "confianza": o.town.trust.score(n),
                                "peso": round(o._w(n), 2)} for n in o.agents]),
                 hide_index=True, use_container_width=True)
    with st.expander("Registro NANDA (tarjetas verificadas)"):
        for name, e in o.town.registry.cards.items():
            st.markdown(f"{'✅' if e['verified'] else '⛔'} **{name}** `{e['card']['agent_id']}`  \n"
                        f"<small>{', '.join(e['card']['capabilities'])}</small>", unsafe_allow_html=True)
    with st.expander("Política de autonomía (policy.yaml)"):
        st.json(o.policy["autonomia"])
        st.json({"umbrales_registro": o.policy["umbrales_registro"], "plazo_ros_horas": o.policy["plazo_ros_horas"]})

case = o.cases.get(aid)
alert = o.alerts[aid]

tab_ctrl, tab_exp, tab_priv, tab_bandeja = st.tabs(
    ["🛰️ Sala de control", "📁 Expediente y decisión", "🔒 Privacidad y auditoría", "📥 Bandeja"])

# ---------------------------------------------------------------- bandeja
with tab_bandeja:
    rows = []
    for a in o.alerts.values():
        c, d = o.cases.get(a["alert_id"]), o.decisions.get(a["alert_id"])
        rows.append({"alerta": a["alert_id"], "regla": a["regla"], "fecha": a["fecha"], "origen": a["origen"],
                     "estado": ("decidida: " + d["accion"]) if d else ("en revisión" if c else "nueva"),
                     "riesgo": c.shared.get("riesgo") if c else None,
                     "etiqueta real (solo eval)": a.get("etiqueta_real")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- sala de control
with tab_ctrl:
    c1, c2 = st.columns([3, 1])
    c1.subheader(f"Alerta {aid}")
    c1.write(f"**Regla:** {alert['regla']} · **Fecha:** {alert['fecha']} · **Origen:** {alert['origen']}")
    run = c2.button("▶ Investigar con agentes", type="primary", use_container_width=True)

    if run:
        t0 = datetime.now()
        with st.status("Equipo de agentes investigando…", expanded=True) as status:
            def show(ev):
                k = ev["kind"]
                if k.startswith("registry.descubrimiento"):
                    status.write(f"🔎 Registro NANDA → **{ev['detail']['agente']}** para *{ev['detail']['capacidad']}*")
                elif k == "agente.fin":
                    status.write(f"✅ **{ev['actor']}** · {ev['detail']['hallazgos']} hallazgos firmados · {ev['latency_ms']} ms")
                elif k == "llm.llamada":
                    status.write(f"🧠 {ev['actor']} razonó con LLM ({ev['detail']['modo']})")
                elif k == "privacidad.pseudonimizacion":
                    status.write(f"🔒 Datos pseudonimizados: cuenta → **{ev['detail']['cuenta']}**")
                elif k == "nanda.signature_invalid":
                    status.write("⛔ Firma inválida detectada")
            o.log.listeners.append(show)
            try:
                case = o.investigate(aid)
            finally:
                o.log.listeners.remove(show)
            secs = (datetime.now() - t0).total_seconds()
            st.session_state[f"t_{aid}"] = secs
            status.update(label=f"Investigación completa en {secs:.1f} s → pendiente de revisión humana",
                          state="complete")

    if case:
        m = st.columns(4)
        m[0].metric("Riesgo consolidado", f"{case.shared.get('riesgo', 0):.2f}")
        m[1].metric("Hallazgos firmados", len(case.findings))
        m[2].metric("Sugerencia del sistema", o.suggestion(case).replace("sugerir_", "").replace("_", " "))
        m[3].metric("Tiempo de investigación", f"{st.session_state.get(f't_{aid}', 0):.1f} s", "vs ~30 min manual")

        g1, g2 = st.columns([3, 2])
        with g1:
            st.markdown("**Grafo de contrapartes** (pseudonimizado · rojo = cuenta alertada, naranja = evidencia)")
            try:
                from pyvis.network import Network
                nb = case.neighborhood
                ev_ids = {e for f in case.findings for e in f["evidence"]}
                net = Network(height="430px", width="100%", directed=True, bgcolor="#0e1117", font_color="#ddd")
                nodes = set(nb.sender) | set(nb.receiver)
                hot = {a for r in nb.itertuples() if r.tx_id in ev_ids for a in (r.sender, r.receiver)}
                for n in nodes:
                    tok = case.pseudo.token(n)
                    color = "#ff4b4b" if n == case.account else ("#ffa500" if n in hot else "#4a6fa5")
                    net.add_node(tok, label=tok, color=color, size=22 if n == case.account else 10)
                for r in nb.itertuples():
                    hot_e = r.tx_id in ev_ids
                    net.add_edge(case.pseudo.token(r.sender), case.pseudo.token(r.receiver),
                                 color="#ffa500" if hot_e else "#555", width=2.5 if hot_e else 0.6)
                net.set_options('{"physics": {"stabilization": {"iterations": 150}}}')
                components.html(net.generate_html(), height=450)
            except Exception as exc:
                st.info(f"Grafo no disponible ({exc}). Instala pyvis.")
        with g2:
            st.markdown("**Bitácora de agentes** (todo queda registrado)")
            evs = o.log.list(aid)
            df = pd.DataFrame([{"hora": e["ts"][11:], "actor": e["actor"], "evento": e["kind"],
                                "estado": e["status"], "ms": e["latency_ms"]} for e in evs])
            st.dataframe(df, hide_index=True, use_container_width=True, height=430)
    else:
        st.info("Presiona **Investigar con agentes** para empezar.")

# ---------------------------------------------------------------- expediente
with tab_exp:
    if not case:
        st.info("Primero investiga la alerta en la Sala de control.")
    else:
        risk = case.shared.get("riesgo", 0)
        st.progress(min(risk, 1.0), text=f"Riesgo {risk:.2f} · sugerencia: {o.suggestion(case)}")
        left, right = st.columns([3, 2])

        with left:
            st.subheader("Hallazgos firmados")
            order = ["riesgo", "mitigante", "respuesta", "norma", "info"]
            for kind in order:
                fs = [f for f in case.findings if f["kind"] == kind]
                if not fs:
                    continue
                st.markdown(f"#### {KIND_ICON[kind]} {kind.capitalize()}")
                for f in fs:
                    with st.container(border=True):
                        estado = {"validado": "✅ validado", "rechazado": "❌ rechazado"}.get(f.get("estado"), "⏳ pendiente")
                        st.markdown(f"**{f['claim']}**  \n<small>{f['agent']} · severidad {f['severity']} · "
                                    f"firma `{f['signature'][:12]}…` · {estado}</small>", unsafe_allow_html=True)
                        with st.expander("Razonamiento y evidencia"):
                            st.write(f["reasoning"])
                            st.caption(f"Fuente: {f['source']}")
                            if f["evidence"]:
                                st.caption("Evidencia: " + ", ".join(f["evidence"][:20]))
                            if f["extra"].get("preguntas"):
                                st.write("Preguntas sugeridas:", f["extra"]["preguntas"])
                        if kind in ("riesgo", "mitigante") and aid not in o.decisions:
                            b1, b2, _ = st.columns([1, 1, 3])
                            if b1.button("✅ Válido", key=f"ok_{f['finding_id']}"):
                                o.feedback(aid, f["finding_id"], True)
                                st.rerun()
                            if b2.button("❌ Incorrecto", key=f"ko_{f['finding_id']}"):
                                o.feedback(aid, f["finding_id"], False)
                                st.rerun()

        with right:
            st.subheader("💬 Pedir más información")
            ag = st.selectbox("Agente", [n for n in o.agents if n != "agente_redactor"], key="ask_ag")
            q = st.text_input("Pregunta del oficial", placeholder="¿La contraparte principal tiene historial previo?")
            if st.button("Preguntar") and q:
                o.ask(aid, ag, q)
                st.rerun()

            st.subheader("📝 Borrador del agente redactor")
            draft = next((f for f in case.findings if f["kind"] == "borrador"), None)
            if st.button("↻ Regenerar borrador con el feedback actual"):
                o.redraft(aid)
                st.rerun()
            if draft:
                st.text_area("Borrador (editable por el oficial)", draft["reasoning"], height=260, key=f"draft_{aid}")

            st.subheader("🧑‍⚖️ Decisión del Oficial de Cumplimiento")
            dec = o.decisions.get(aid)
            if dec:
                st.success(f"Decisión firmada: **{ACCION_LABEL[dec['accion']]}** por {dec['actor']} · {dec['ts']}")
                if dec.get("vence_ros"):
                    left_h = (datetime.fromisoformat(dec["vence_ros"]) - datetime.now()).total_seconds() / 3600
                    st.error(f"⏱️ Plazo ROS a la UIF-Perú: vence {dec['vence_ros']} · quedan {left_h:.1f} h")
            else:
                sug = o.suggestion(case).replace("sugerir_", "")
                opts = list(ACCION_LABEL)
                accion = st.radio("Acción", opts, index=opts.index(sug) if sug in opts else 1,
                                  format_func=ACCION_LABEL.get)
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

            st.subheader("📦 Expediente")
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
                        f"Integridad: {'✅' if v['hash_integro'] else '❌'} · Firmas de agentes: "
                        f"{'✅' if v['firmas_ok'] else '❌'} · Decisión firmada: {v['decision_firmada_ok']}")
                st.caption(f"sha256 {exp['sha256'][:24]}… · conservar hasta {exp['conservar_hasta']}")

            st.divider()
            if st.button("😈 Simular agente impostor"):
                r = attack(o, aid)
                st.session_state[f"imp_{aid}"] = r
            if st.session_state.get(f"imp_{aid}"):
                r = st.session_state[f"imp_{aid}"]
                st.error(f"Impostor intentó colar 'DESCARTAR' suplantando a agente_transaccional → "
                         f"hallazgo {'ACEPTADO ⚠️' if r['hallazgo_aceptado'] else 'RECHAZADO (firma inválida) ✅'} · "
                         f"decisión {'BLOQUEADA (sin permiso) ✅' if r['decision_bloqueada'] else 'EJECUTADA ⚠️'}")

# ---------------------------------------------------------------- privacidad
with tab_priv:
    st.subheader("🔒 Lo que vio el modelo")
    st.caption("Contenido EXACTO enviado al LLM local. Sin nombres, DNI, cuentas reales ni montos exactos.")
    evs = [e for e in o.log.list(aid) if e["kind"] in ("llm.llamada", "llm.error", "privacy.bloqueado")]
    if not evs:
        st.info("Aún no hay llamadas al LLM para esta alerta.")
    for e in evs:
        seen = e["detail"].get("lo_que_vio_el_modelo", {})
        with st.expander(f"{e['ts'][11:]} · {e['actor']} · modo {e['detail'].get('modo', e['kind'])}"):
            st.code(seen.get("user", json.dumps(e["detail"], ensure_ascii=False)), language="json")
    st.subheader("Evidencia NANDA (data_facts)")
    st.dataframe(pd.DataFrame([{k: r[k] for k in ("record_id", "observer", "claim", "signature")}
                               for r in o.town.facts.about(aid)]), hide_index=True, use_container_width=True)
    st.subheader("Re-identificación (solo Oficial de Cumplimiento)")
    if st.toggle("Mostrar datos reales del cliente (queda registrado en la bitácora)", key=f"reid_{aid}"):
        st.json(o.reidentify(aid))
