"""Sentinela AML — UI (Streamlit). El usuario es el analista AML: consulta un cliente por DNI y recibe
pasa / revisión manual / crítico con el motivo y la evidencia. El oficial de cumplimiento solo recibe escalamientos.

    streamlit run app.py
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from agents.impostor import attack
from agents.redactor import TIPO_PAGO
from core.live import OCUPACIONES, TIPOLOGIAS, default_params
from core.orchestrator import ANALISTA, HUMANO, ROLES, ZONAS, Orchestrator
from ui import components as ui

st.set_page_config(page_title="Sentinela AML", page_icon="🛡️", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

KIND_LABEL = {"riesgo": "🔴 Señales de riesgo", "mitigante": "🟢 Explicaciones legítimas (abogado del diablo)",
              "respuesta": "💬 Respuestas a tus preguntas", "norma": "📜 Normativa aplicable", "info": "ℹ️ Contexto"}
NIVEL = {"cierre_sugerido": "🟢 Pasa", "revision_humana": "🟡 Revisión manual", "escalamiento": "🔴 Crítico"}
ACCION_LABEL = {"descartar": "Cerrado sin sospecha (con motivo)", "vigilancia": "En vigilancia",
                "escalar_oficial": "Escalado al Oficial de Cumplimiento", "escalar_ros": "Calificado como sospechoso (ROS)"}
VISTAS = ["🔎 Consulta por DNI", "📥 Bandeja por riesgo", "🔒 Privacidad y auditoría", "📈 Impacto"]


def orch() -> Orchestrator:
    if "orch" not in st.session_state:
        st.session_state.orch = Orchestrator()
    return st.session_state.orch


def _llm_badge(modo: str | None, proveedor: str | None = None) -> str:
    return {"llm": "🧠 razonó con OpenRouter" if proveedor == "openrouter" else "🧠 razonó con LLM local",
            "cache": "🧠 LLM (caché)", "bloqueado": "🔒 bloqueado por privacidad"}.get(modo or "", "⚙️ reglas (LLM apagado)")


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
            stt.setdefault(a, {"st": "run", "badges": []})["badges"].append(_llm_badge(e["detail"].get("modo"),
                                                                                      e["detail"].get("proveedor")))
        elif e["kind"] == "registry.descubrimiento":
            stt.setdefault(e["detail"]["agente"], {"st": "wait", "badges": []})["badges"].append("🔎 descubierto en NANDA")
    return stt


def open_client(dni: str, run: bool = False, set_view: bool = False) -> None:
    st.session_state.dni = dni
    st.session_state.run_now = run
    if set_view:  # solo desde un callback (antes de dibujar el selector de vista)
        st.session_state.vista = VISTAS[0]


def dni_of(alert: dict) -> str:
    return "".join(ch for ch in str(alert.get("documento_identidad", "")) if ch.isdigit())


o = orch()
st.session_state.setdefault("vista", VISTAS[0])

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 🛡️ Sentinela AML")
    st.caption("Cada conclusión con firma, fuente y dueño.")
    st.radio("Vista", VISTAS, key="vista", label_visibility="collapsed")
    st.caption("Trabajas como **Analista AML**. Los casos críticos se escalan al Oficial de Cumplimiento.")
    llm_ok = o.llm.available(refresh=st.button("↻ Revisar LLM", width="stretch"))
    st.markdown(f"**LLM:** {'🟢 ' + o.llm.label if llm_ok else '🟡 modo reglas (sin Ollama ni OpenRouter)'}  \n"
                f"**Protocolos NANDA:** {'🟢 nandatown' if o.town.backend == 'nandatown' else '🟡 respaldo local'}")
    st.markdown("**Confianza en agentes** · capa *trust*")
    st.dataframe(pd.DataFrame([{"agente": ui.AGENT_NAME.get(n, n), "reputación": o.town.trust.score(n),
                                "peso": round(o._w(n), 2)} for n in o.agents]), hide_index=True, width="stretch")
    with st.expander("Registro NANDA (tarjetas verificadas)"):
        for name, e in o.town.registry.cards.items():
            st.markdown(f"{'✅' if e['verified'] else '⛔'} **{name}** `{e['card']['agent_id']}`  \n"
                        f"<small>{', '.join(e['card']['capabilities'])}</small>", unsafe_allow_html=True)
    with st.expander("Política del banco (policy.yaml)"):
        st.json({"triaje": o.policy.get("triaje"), "forzar_humano_si": o.policy.get("forzar_humano_si"),
                 "fraccionamiento": o.policy.get("fraccionamiento"), "autonomia": o.policy["autonomia"]})
    if st.button("🔄 Reiniciar demo", width="stretch"):
        o.reset()
        o.documents.clear()
        for k in [k for k in st.session_state if k.startswith(("exp_", "imp_", "t_", "draft_", "reveal_", "doc_"))
                  or k in ("dni", "run_now")]:
            del st.session_state[k]
        st.rerun()

vista = st.session_state.vista


# ---------------------------------------------------------------- piezas del caso
def run_agents(aid: str, grid) -> None:
    state: dict[str, dict] = {}
    grid.markdown(ui.agent_grid(state), unsafe_allow_html=True)
    pace = 0 if o.llm.available() else 0.2

    def show(ev):
        k = ev["kind"]
        if k == "registry.descubrimiento":
            state.setdefault(ev["detail"]["agente"], {"st": "wait", "badges": []})["badges"].append("🔎 descubierto en NANDA")
        elif k == "agente.inicio":
            state[ev["actor"]] = {"st": "run", "badges": state.get(ev["actor"], {}).get("badges", [])}
        elif k == "llm.llamada":
            state.setdefault(ev["actor"], {"st": "run", "badges": []})["badges"].append(
                _llm_badge(ev["detail"]["modo"], ev["detail"].get("proveedor")))
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
        o.investigate(aid)
    finally:
        o.log.listeners.remove(show)
    st.session_state[f"t_{aid}"] = (datetime.now() - t0).total_seconds()
    st.session_state.pop(f"exp_{aid}", None)


def findings_block(aid: str, case, kinds: list[str], expanded: tuple[str, ...]) -> None:
    for kind in kinds:
        fs = [f for f in case.findings if f["kind"] == kind]
        if not fs:
            continue
        with st.expander(f"{KIND_LABEL[kind]} ({len(fs)})", expanded=kind in expanded):
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
                                     hide_index=True, width="stretch")
                if kind in ("riesgo", "mitigante") and aid not in o.decisions:
                    estado = f.get("estado")
                    if cols[1].button("✅ Válido" if estado != "validado" else "✅ validado", key=f"ok_{f['finding_id']}",
                                      disabled=estado == "validado"):
                        o.feedback(aid, f["finding_id"], True, actor=ANALISTA)
                        st.rerun()
                    if cols[2].button("❌ Incorrecto" if estado != "rechazado" else "❌ rechazado",
                                      key=f"ko_{f['finding_id']}", disabled=estado == "rechazado"):
                        o.feedback(aid, f["finding_id"], False, actor=ANALISTA)
                        st.rerun()


def ficha_block(case) -> None:
    f = next((x for x in case.findings if x["agent"] == "agente_fraccionamiento"), None)
    if not f:
        return
    st.markdown(f"#### 🧮 {f['claim']}")
    df = pd.DataFrame(f["extra"]["ficha"])
    st.dataframe(pd.DataFrame({"señal": ["✅" if a else "—" for a in df.activa], "parámetro": df.descripcion,
                               "valor observado": df.valor, "puntos": [f"{p}/{m}" for p, m in zip(df.peso, df.peso_max)]}),
                 hide_index=True, width="stretch")
    st.caption("0-2 puntos = pasa · 3-5 = revisión manual · 6+ = crítico. Umbrales en policy.yaml (fraccionamiento).")


def graph_block(aid: str, case) -> None:
    st.markdown("#### 🕸️ El movimiento del dinero")
    full = st.toggle("Ver vecindario completo (2 saltos)", key=f"full_{aid}")
    components.html(ui.graph_html(case, full=full), height=520)


def transactions_block(aid: str, case, expanded: bool) -> None:
    """Registro de transacciones de la cuenta con montos exactos (vista interna del analista; no va al LLM)."""
    w, acc = case.window, case.account
    cur = o.alerts[aid].get("moneda", "USD")
    evid = {e for f in case.findings if f["kind"] == "riesgo" for e in f["evidence"]}
    entra = w.receiver == acc
    tot_in, tot_out = float(w[entra].amount.sum()), float(w[~entra].amount.sum())
    with st.expander(f"📒 Registro de transacciones ({len(w)}) · entradas {tot_in:,.0f} · salidas {tot_out:,.0f} {cur}",
                     expanded=expanded):
        if not len(w):
            st.info("La cuenta no tiene movimientos en la ventana analizada.")
            return
        c1, c2 = st.columns([1, 1])
        sentido = c1.radio("Mostrar", ["Todas", "Entradas", "Salidas"], horizontal=True, key=f"tx_dir_{aid}",
                           label_visibility="collapsed")
        solo_ev = c2.toggle("Solo las que son evidencia de una señal", key=f"tx_ev_{aid}", disabled=not evid)
        df = pd.DataFrame({
            "fecha": w.timestamp.dt.strftime("%Y-%m-%d %H:%M"),
            "sentido": ["⬇ entrada" if x else "⬆ salida" for x in entra],
            "tipo": [TIPO_PAGO.get(t, t) for t in w.payment_type.astype(str)],
            "contraparte": [case.pseudo.token(s if x else r) for s, r, x in zip(w.sender, w.receiver, entra)],
            f"monto ({cur})": w.amount.round(2),
            "país contraparte": [s if x else r for s, r, x in zip(w.sender_loc, w.receiver_loc, entra)],
            "evidencia": ["🔴" if t in evid else "" for t in w.tx_id],
            "tx_id": w.tx_id,
        })
        if sentido != "Todas":
            df = df[df.sentido.str.contains(sentido[:-1].lower())]
        if solo_ev:
            df = df[df.evidencia != ""]
        st.dataframe(df.sort_values("fecha", ascending=False), hide_index=True, width="stretch",
                     height=min(420, 38 + 35 * len(df)),
                     column_config={f"monto ({cur})": st.column_config.NumberColumn(format="localized")})
        k = st.columns(4)
        k[0].markdown(ui.kpi(f"{tot_in:,.0f}", f"entradas ({cur})"), unsafe_allow_html=True)
        k[1].markdown(ui.kpi(f"{tot_out:,.0f}", f"salidas ({cur})"), unsafe_allow_html=True)
        k[2].markdown(ui.kpi(f"{tot_in - tot_out:,.0f}", f"neto ({cur})"), unsafe_allow_html=True)
        k[3].markdown(ui.kpi(str(len(set(w.sender) | set(w.receiver)) - 1), "contrapartes distintas"), unsafe_allow_html=True)
        st.caption("Montos exactos: vista interna del analista. Al modelo de lenguaje solo le llegan rangos y CTA_###.")


def documents_block(aid: str) -> None:
    with st.container(border=True):
        st.markdown("#### 📄 Documento de origen de fondos")
        st.caption("TXT, PDF o DOCX. Se registra su huella sha256; el LLM lo lee con montos y datos personales enmascarados.")
        up = st.file_uploader("Adjuntar", type=["txt", "pdf", "docx"], key=f"up_{aid}", label_visibility="collapsed")
        if up is not None and not st.session_state.get(f"doc_{aid}_{up.name}"):
            with st.spinner("El agente de extracción está leyendo el documento…"):
                o.attach_document(aid, up.name, up.getvalue(), actor=ANALISTA)
            st.session_state[f"doc_{aid}_{up.name}"] = True
            st.rerun()
        for d in o.documents.get(aid, []):
            st.markdown(f"📎 **{d['nombre']}** · sha256 `{d['sha256'][:16]}…` · {d['fact_id']}")


def ask_block(aid: str) -> None:
    with st.container(border=True):
        st.markdown("#### 💬 Pregúntale a un agente")
        ag = st.selectbox("Agente", [n for n in o.agents if n not in ("agente_redactor", "agente_extraccion")],
                          key=f"ask_ag_{aid}", format_func=lambda n: ui.AGENT_NAME.get(n, n))
        with st.form(f"ask_form_{aid}", clear_on_submit=True, border=False):
            q = st.text_input("Pregunta", placeholder="¿La contraparte principal tiene historial previo?", key=f"q_{aid}")
            sent = st.form_submit_button("Preguntar")
        if sent and q.strip():
            with st.spinner(f"{ui.AGENT_NAME.get(ag, ag)} está revisando el caso…"):
                o.ask(aid, ag, q.strip())
        st.caption("Ideas: contrapartes e historial · efectivo · exterior · perfil e ingreso · documentos · ¿por qué el riesgo?")
        resp = [f for f in o.cases[aid].findings if f["kind"] == "respuesta"]
        for f in reversed(resp[-4:]):
            with st.chat_message("user"):
                st.markdown(f["extra"].get("pregunta") or f["claim"].replace("Respuesta a: ", ""))
            with st.chat_message("assistant", avatar=ui.AGENT_ICON.get(f["agent"], "🤖")):
                st.markdown(f"**{ui.AGENT_NAME.get(f['agent'], f['agent'])}** "
                            f"<small>({'🧠 LLM' if f['extra'].get('modo_llm') in ('llm', 'cache') else '⚙️ reglas'} · "
                            f"firma <code>{f['signature'][:8]}…</code>)</small><br>{f['reasoning']}", unsafe_allow_html=True)


def expediente_block(aid: str) -> None:
    with st.container(border=True):
        st.markdown("#### 📦 Expediente listo y verificable")
        e1, e2, e3 = st.columns(3)
        if e1.button("Generar", key=f"gen_{aid}"):
            st.session_state[f"exp_{aid}"] = o.export(aid)
        exp = st.session_state.get(f"exp_{aid}")
        if exp:
            e2.download_button("⬇ JSON", json.dumps(exp, ensure_ascii=False, indent=1, default=str),
                               file_name=f"expediente_{aid}.json", mime="application/json")
            if e3.button("🔏 Verificar", key=f"ver_{aid}"):
                v = o.verify(exp)
                ok = v["hash_integro"] and v["firmas_ok"]
                (st.success if ok else st.error)(
                    f"Integridad {'✅' if v['hash_integro'] else '❌'} · Firmas de agentes "
                    f"{'✅' if v['firmas_ok'] else '❌'} · Decisión firmada: {v['decision_firmada_ok']}")
            st.caption(f"sha256 {exp['sha256'][:24]}… · {len(exp['hallazgos'])} hallazgos firmados · "
                       f"{len(exp.get('documentos', []))} documentos · conservar hasta {exp['conservar_hasta']}")


def decision_block(aid: str, case, zona: str) -> None:
    with st.container(border=True):
        st.markdown("#### ✍️ Tu decisión (la firma el analista; el sistema no envía nada solo)")
        dec = o.decisions.get(aid)
        if dec:
            st.success(f"Firmado: **{ACCION_LABEL[dec['accion']]}** · {ROLES.get(dec['actor'], dec['actor'])} · {dec['ts']}")
            if dec["accion"] == "escalar_oficial":
                st.info("📨 En la bandeja del Oficial de Cumplimiento con el expediente adjunto. El plazo de 24 h del "
                        "ROS corre desde que el oficial califique la operación como sospechosa.")
            if st.button("➡ Siguiente cliente", key=f"next_{aid}"):
                st.session_state.pop("dni", None)
                st.rerun()
            return
        draft = next((f["reasoning"] for f in case.findings if f["kind"] == "borrador"), "")
        risk = case.shared.get("riesgo", 0)
        if zona == "cierre_sugerido":
            motivo = st.text_area("Motivo del cierre (queda en el expediente 10 años)",
                                  "Sin señales de riesgo relevantes; movimiento consistente con el perfil declarado.",
                                  key=f"mot_{aid}")
            c1, c2 = st.columns(2)
            if c1.button("✓ Confirmar cierre", type="primary", key=f"cerrar_{aid}"):
                _decide(aid, "descartar", motivo + "\n\n" + draft[:1200])
            if c2.button("🟡 No estoy seguro: pasar a revisión", key=f"vig_{aid}"):
                _decide(aid, "vigilancia", "El analista prefiere revisar en detalle: " + motivo)
            return
        motivo = st.text_area("Motivo (obligatorio)", key=f"mot_{aid}",
                              placeholder="Ej.: depósitos fraccionados desde 7 cuentas sin relación con el ingreso declarado")
        motivo2 = None
        c1, c2, c3 = st.columns(3)
        label = "🔴 Escalar al Oficial" if zona == "escalamiento" else "Confirmar sospecha → escalar al Oficial"
        if c1.button(label, type="primary", key=f"esc_{aid}"):
            _decide(aid, "escalar_oficial", motivo)
        if c2.button("Mantener en vigilancia", key=f"vig_{aid}"):
            _decide(aid, "vigilancia", motivo)
        if risk > o.policy["autonomia"]["doble_justificacion_si_riesgo_mayor_a"]:
            motivo2 = st.text_area("⚠️ Riesgo alto: para descartar se exige una segunda justificación", key=f"mot2_{aid}")
        if c3.button("Descartar (no sospechosa)", key=f"desc_{aid}"):
            _decide(aid, "descartar", motivo, motivo2)


def _decide(aid: str, accion: str, motivo: str, motivo2: str | None = None) -> None:
    try:
        o.decide(aid, ANALISTA, ANALISTA, accion, motivo, motivo2)
        st.rerun()
    except (ValueError, PermissionError) as exc:
        st.warning(str(exc))


def extras_block(aid: str, case) -> None:
    with st.expander("📝 Borrador del redactor · 🛰️ línea de tiempo de agentes · 😈 prueba de seguridad"):
        draft = next((f for f in case.findings if f["kind"] == "borrador"), None)
        if st.button("↻ Regenerar borrador con el feedback actual", key=f"redraft_{aid}"):
            o.redraft(aid)
            st.rerun()
        if draft:
            st.text_area("Borrador (editable)", draft["reasoning"], height=220, key=f"draft_{aid}")
        sw = ui.swimlane_df(o.log.list(aid))
        if len(sw):
            st.altair_chart(alt.Chart(sw).mark_bar(cornerRadius=4, height=16).encode(
                x=alt.X("inicio:Q", title="ms desde el inicio"), x2="fin:Q", y=alt.Y("agente:N", sort=None, title=None),
                color=alt.Color("estado:N", scale=alt.Scale(domain=["ok", "error"], range=[ui.TEAL, ui.RED]), legend=None),
                tooltip=["agente", "ms"]).properties(height=260), width="stretch")
        if st.button("Lanzar un agente impostor", key=f"imp_b_{aid}"):
            st.session_state[f"imp_{aid}"] = attack(o, aid)
        r = st.session_state.get(f"imp_{aid}")
        if r:
            st.error(f"Un impostor intentó colar 'DESCARTAR' suplantando al agente transaccional → hallazgo "
                     f"{'ACEPTADO ⚠️' if r['hallazgo_aceptado'] else 'RECHAZADO por firma inválida ✅'} · decisión "
                     f"{'BLOQUEADA: los agentes no deciden ✅' if r['decision_bloqueada'] else 'EJECUTADA ⚠️'}")


def steps_block(aid: str) -> None:
    with st.expander("🧭 Paso a paso: qué hizo cada agente", expanded=False):
        quien = st.selectbox("Ver", [None, *o.agents], key=f"steps_ag_{aid}",
                             format_func=lambda n: "Todo el equipo" if n is None else ui.AGENT_NAME.get(n, n))
        for i, s in enumerate(ui.narrate(o.log.list(aid), quien), start=1):
            st.markdown(f"<small>{s['hora']}</small> {s['estado']} **{s['quien']}** — {s['que']}", unsafe_allow_html=True)


def render_client(aid: str) -> None:
    alert = o.alerts[aid]
    run = st.session_state.pop("run_now", False) or aid not in o.cases
    case = o.cases.get(aid)
    zona = case.shared.get("zona") if case else None
    perfil = case.shared.get("perfil", {}) if case else {}
    titulo = f"{alert.get('nombre_cliente', 'Cliente')} · DNI {dni_of(alert)}"
    meta = (f"{perfil.get('ocupacion', 'perfil en consulta…')} · cuenta {case.token if case else '…'} · "
            f"ventana de 30 días al {alert['fecha']} · moneda {alert.get('moneda', 'USD')}")
    head = st.empty()
    head.markdown(ui.hero(titulo, meta, zona, NIVEL.get(zona), case.shared.get("motivos_triaje", []) if case else []),
                  unsafe_allow_html=True)
    grid = st.empty()
    if run:
        run_agents(aid, grid)
        st.rerun()
    case = o.cases[aid]
    graph_block(aid, case)
    transactions_block(aid, case, expanded=case.shared.get("zona") == "cierre_sugerido" or len(case.window) <= 15)
    with st.expander("🤖 Equipo de agentes que analizó al cliente", expanded=False):
        st.markdown(ui.agent_grid(agent_state_from_log(o.log.list(aid))), unsafe_allow_html=True)
    steps_block(aid)
    if st.button("↻ Volver a analizar", key=f"rerun_{aid}"):
        st.session_state.run_now = True
        st.rerun()

    risk, conf = case.shared.get("riesgo", 0), case.shared.get("confianza", 0)
    r1, r2, r3 = st.columns([1, 1, 3])
    r1.markdown(ui.ring(risk, "Riesgo", ui.risk_color(risk)), unsafe_allow_html=True)
    r2.markdown(ui.ring(conf, "Confianza del sistema", ui.TEAL if conf >= 0.75 else ui.AMBER), unsafe_allow_html=True)
    with r3:
        kk = st.columns(3)
        kk[0].markdown(ui.kpi(f"{st.session_state.get(f't_{aid}', 0):.1f} s", "expediente armado (vs ~30 min manual)"),
                       unsafe_allow_html=True)
        kk[1].markdown(ui.kpi(str(len(case.findings)), "hallazgos firmados (NANDA)"), unsafe_allow_html=True)
        kk[2].markdown(ui.kpi(str(len(case.window)), "transacciones analizadas"), unsafe_allow_html=True)
        top = [f["claim"] for f in sorted(case.findings, key=lambda f: -f["severity"]) if f["kind"] == "riesgo"][:3]
        mit = [f["claim"] for f in case.findings if f["kind"] == "mitigante"]
        if case.shared.get("resumen_ejecutivo"):
            st.markdown(f"**Resumen** <small>(🧠 {o.llm.label})</small>: {case.shared['resumen_ejecutivo']}",
                        unsafe_allow_html=True)
        else:
            st.markdown("**En resumen:** " + ("; ".join(top) if top else "sin señales de riesgo relevantes")
                        + (f". **Pero:** {mit[0]}" if mit else "") + ".")
        if alert.get("sorpresa") and st.toggle("👀 Revelar la tipología real del cliente sorpresa", key=f"reveal_{aid}"):
            st.success(f"Tipología generada: **{TIPOLOGIAS.get(alert['etiqueta_real'], 'sin lavado (normal o planilla)')}**")

    if zona == "cierre_sugerido":
        st.success("✓ **Sin señales de riesgo relevantes.** El sistema sugiere cerrar; tú confirmas con un clic.")
        decision_block(aid, case, zona)
        ask_block(aid)
        with st.expander("Ver el detalle (auditoría)"):
            ficha_block(case)
            findings_block(aid, case, ["riesgo", "mitigante", "respuesta", "info", "norma"], ())
        extras_block(aid, case)
        return

    left, right = st.columns([3, 2])
    with left:
        findings_block(aid, case, ["riesgo", "mitigante", "respuesta"], ("riesgo", "mitigante", "respuesta"))
        ficha_block(case)
        findings_block(aid, case, ["norma", "info"], ())
    with right:
        decision_block(aid, case, zona)
        if zona == "escalamiento":
            expediente_block(aid)
        documents_block(aid)
        ask_block(aid)
    extras_block(aid, case)


# ---------------------------------------------------------------- consulta por DNI
if vista == VISTAS[0]:
    clients = o.clients()
    with st.container(border=True):
        st.markdown("#### 🔎 Consulta de cliente por DNI")
        c1, c2, c3 = st.columns([2, 1, 1])
        dni_in = c1.text_input("DNI del cliente", value=st.session_state.get("dni", ""), max_chars=11,
                               placeholder="Ej.: 45002178", label_visibility="collapsed")
        if c2.button("Analizar", type="primary", width="stretch") and dni_in:
            open_client(dni_in, run=True)
            st.rerun()
        if c3.button("🎲 Cliente al azar", width="stretch"):
            open_client(random.choice(clients)["dni"], run=True)
            st.rerun()
        with st.expander(f"DNIs de clientes registrados en el core simulado ({len(clients)})"):
            st.caption("Cada DNI está enlazado a una cuenta del dataset (SAML-D + casos sintéticos). "
                       "El nivel esperado no se muestra: el sistema no lo sabe.")
            st.dataframe(pd.DataFrame([{"DNI": c["dni"], "cliente": c["nombre"]} for c in clients]),
                         hide_index=True, width="stretch", height=220)
        with st.expander("🧪 Registrar un cliente nuevo en vivo (el jurado elige la tipología)"):
            cc1, cc2 = st.columns(2)
            tipo = cc1.selectbox("Tipología", ["sorpresa", *TIPOLOGIAS],
                                 format_func=lambda t: "🎲 Sorpresa (nadie lo sabe)" if t == "sorpresa" else TIPOLOGIAS[t])
            ocup = cc2.selectbox("Perfil declarado del cliente", list(OCUPACIONES))
            p = default_params(tipo) if tipo != "sorpresa" else {}
            if tipo != "sorpresa":
                d1, d2, d3, d4 = st.columns(4)
                p = {"n": d1.slider("Nº de operaciones / cuentas", 3, 40, int(p["n"])),
                     "monto": d2.number_input("Monto típico (USD)", 100.0, 500_000.0, float(p["monto"]), step=100.0),
                     "dias": d3.slider("Días", 1, 30, int(max(p["dias"], 1))),
                     "salida_exterior": d4.checkbox("Sale al exterior", bool(p["salida_exterior"]))}
            if st.button("Crear cliente y analizar", type="primary"):
                new = o.add_live_alert(tipo, p, ocup)
                open_client(dni_of(o.alerts[new]), run=True)
                st.rerun()

    dni = st.session_state.get("dni")
    if dni:
        aid = o.consult(dni)
        if not aid:
            st.error(f"El DNI {dni} no está registrado en el core del banco (o no tiene movimientos). Revisa el número.")
        else:
            st.session_state.aid = aid
            render_client(aid)
    else:
        st.info("Escribe un DNI y presiona **Analizar**: 9 agentes especialistas se descubren por el registro NANDA, "
                "analizan al cliente y firman cada hallazgo. Resultado: 🟢 pasa · 🟡 revisión manual · 🔴 crítico.")

# ---------------------------------------------------------------- bandeja por riesgo
elif vista == VISTAS[1]:
    pend = [a for a in o.alerts if a not in o.cases]
    b1, b2, _ = st.columns([1.2, 1.2, 2])
    if b1.button(f"▶ Analizar todos ({len(pend)} pendientes)", disabled=not pend):
        bar = st.progress(0.0, text="Analizando…")
        prev = o.llm.provider_cfg
        o.llm.provider_cfg = "ninguno"   # el lote corre en modo reglas: el LLM razona al abrir cada cliente
        try:
            o.investigate_all(lambda i, n, a: bar.progress(i / n, text=f"{i}/{n}"))
        finally:
            o.llm.provider_cfg = prev
        st.rerun()
    cierres = [a for a, c in o.cases.items() if c.shared.get("zona") == "cierre_sugerido" and a not in o.decisions]
    if b2.button(f"✓ Confirmar cierres sugeridos ({len(cierres)})", disabled=not cierres):
        done = o.approve_batch(ANALISTA, ANALISTA)
        st.success(f"{len(done)} cierres confirmados, cada uno con su sustento de no reporte firmado.")
    st.caption("El lote corre en modo reglas (rápido y sin consumir el LLM); al abrir un cliente puedes volver a "
               "analizarlo con el LLM.")
    zc = {z: sum(1 for c in o.cases.values() if c.shared.get("zona") == z) for z in ZONAS}
    k = st.columns(5)
    for col, (v, l) in zip(k, [(zc["escalamiento"], NIVEL["escalamiento"]), (zc["revision_humana"], NIVEL["revision_humana"]),
                               (zc["cierre_sugerido"], NIVEL["cierre_sugerido"]), (len(pend), "sin analizar"),
                               (len(o.decisions), "firmados por el analista")]):
        col.markdown(ui.kpi(str(v), l), unsafe_allow_html=True)
    rows = []
    for a in o.alerts.values():
        c, d = o.cases.get(a["alert_id"]), o.decisions.get(a["alert_id"])
        rows.append({"nivel": NIVEL.get(c.shared.get("zona"), "") if c else "⏳ sin analizar",
                     "cliente": a.get("nombre_cliente", ""), "DNI": dni_of(a),
                     "riesgo": c.shared.get("riesgo") if c else None, "confianza": c.shared.get("confianza") if c else None,
                     "estado": ACCION_LABEL.get(d["accion"], d["accion"]) if d else ("pendiente de firma" if c else "nuevo"),
                     "_r": c.shared.get("riesgo", 0) if c else -1})
    df = pd.DataFrame(rows).sort_values("_r", ascending=False).drop(columns="_r").reset_index(drop=True)
    sel = st.dataframe(df, hide_index=True, width="stretch", height=420, on_select="rerun",
                       selection_mode="single-row",
                       column_config={"riesgo": st.column_config.ProgressColumn("riesgo", min_value=0, max_value=1, format="%.2f"),
                                      "confianza": st.column_config.ProgressColumn("confianza", min_value=0, max_value=1, format="%.2f")})
    rows_sel = sel.selection.rows if sel and sel.selection else []
    if rows_sel:
        dni_sel = df.iloc[rows_sel[0]]["DNI"]
        st.button(f"Abrir cliente DNI {dni_sel}", type="primary", on_click=open_client, args=(dni_sel, False, True))
    st.caption("Ordenada por riesgo: empieza por los críticos. Ninguna cola cierra sola: el analista confirma cada cierre.")

# ---------------------------------------------------------------- privacidad
elif vista == VISTAS[2]:
    aid = st.session_state.get("aid")
    if not aid or aid not in o.cases:
        st.info("Consulta un cliente por DNI para ver qué vio el modelo.")
    else:
        al = o.alerts[aid]
        st.subheader(f"🔒 Lo que vio el modelo · {al.get('nombre_cliente')} (DNI {dni_of(al)})")
        st.caption("Contenido EXACTO enviado al LLM (local u OpenRouter). Sin nombres, DNI, cuentas reales ni montos "
                   "exactos: el analista ve al cliente; el modelo solo ve CTA_### y rangos.")
        evs = [e for e in o.log.list(aid) if e["kind"] in ("llm.llamada", "llm.error", "privacy.bloqueado")]
        if not evs:
            st.info("Aún no hay llamadas al LLM para este cliente.")
        for e in evs:
            seen = e["detail"].get("lo_que_vio_el_modelo", {})
            with st.expander(f"{e['ts'][11:]} · {e['actor']} · modo {e['detail'].get('modo', e['kind'])}"):
                st.code(seen.get("user", json.dumps(e["detail"], ensure_ascii=False)), language="json")
        st.subheader("Evidencia NANDA (data_facts)")
        st.dataframe(pd.DataFrame([{k: r[k] for k in ("record_id", "observer", "claim", "signature")}
                                   for r in o.town.facts.about(aid)]), hide_index=True, width="stretch")
        with st.expander("Bitácora completa del caso"):
            st.dataframe(pd.DataFrame([{"hora": e["ts"][11:], "actor": e["actor"], "evento": e["kind"],
                                        "estado": e["status"], "ms": e["latency_ms"]} for e in o.log.list(aid)]),
                         hide_index=True, width="stretch", height=320)

# ---------------------------------------------------------------- impacto
else:
    st.subheader("📈 Impacto y modo sombra")
    try:
        m = json.loads((__import__("pathlib").Path("data") / "metricas.json").read_text(encoding="utf-8"))
    except Exception:
        m = None
    if m:
        st.caption(f"Corrida de evaluate.py del {m['fecha']} sobre {m['alertas']} clientes con resultado conocido "
                   "(SAML-D + sintéticos), sin decidir nada.")
        k = st.columns(4)
        lav_n, lav_d = map(int, m["lavado_no_cerrado"].split("/"))
        nor_n, nor_d = map(int, m["normales_a_cierre_sugerido"].split("/"))
        for col, (v, l) in zip(k, [(f"{lav_n}/{lav_d}", "lavado que NO se va a cierre (no se escapa)"),
                                   (str(lav_d - lav_n), "críticos que se escaparían a cierre"),
                                   (f"{nor_n}/{nor_d}", "normales que el sistema cierra"),
                                   (f"{m['seg_por_alerta']} s", "por cliente (modo reglas)")]):
            col.markdown(ui.kpi(v, l), unsafe_allow_html=True)
    lab = [(a, c) for a, c in o.cases.items() if o.alerts[a].get("etiqueta_real")]
    if lab:
        df = pd.DataFrame([{"real": "Lavado" if o.alerts[a]["etiqueta_real"] != "Normal" else "Normal",
                            "tipologia_real": o.alerts[a]["etiqueta_real"], "nivel": NIVEL[c.shared["zona"]]} for a, c in lab])
        st.markdown("##### Modo sombra de esta sesión")
        st.dataframe(pd.crosstab(df.real, df.nivel), width="stretch")
        with st.expander("Detalle por tipología"):
            st.dataframe(pd.crosstab(df.tipologia_real, df.nivel), width="stretch")
        pct_cierre = (df.nivel == NIVEL["cierre_sugerido"]).mean()
    else:
        pct_cierre = (nor_n / max(m["alertas"], 1)) if m else 0.1
        st.caption("Bandeja → Analizar todos para ver el modo sombra de esta sesión.")

    st.markdown("##### ⏱️ Ahorro de tiempo del analista (supuestos visibles y editables)")
    st.latex(r"\text{Ahorro} = \text{cerrados} \times t_{manual} + \text{revisados} \times (t_{manual} - t_{expediente})")
    c1, c2, c3, c4 = st.columns(4)
    n_dia = c1.number_input("Alertas por día", 10, 10000, 100, step=10)
    t_man = c2.number_input("Min. por alerta a mano", 5, 120, 30)
    t_exp = c3.number_input("Min. con expediente armado", 1, 120, 15)
    p_cie = c4.slider("% que el sistema cierra", 0, 100, int(round(pct_cierre * 100)),
                      help="Por defecto, el % de cierre sugerido observado en el modo sombra")
    cerrados, revisados = n_dia * p_cie / 100, n_dia * (1 - p_cie / 100)
    antes, despues = n_dia * t_man / 60, revisados * t_exp / 60
    ahorro = antes - despues
    k = st.columns(3)
    k[0].markdown(ui.kpi(f"{antes:,.1f} h", "horas-analista al día hoy"), unsafe_allow_html=True)
    k[1].markdown(ui.kpi(f"{despues:,.1f} h", "con Sentinela"), unsafe_allow_html=True)
    k[2].markdown(ui.kpi(f"{ahorro / antes:.0%}" if antes else "—", f"ahorro ({ahorro:,.1f} h/día)"), unsafe_allow_html=True)
    st.caption("Referencia de la industria: 90-95% de las alertas de monitoreo por reglas son falsos positivos "
               "(recopilación atribuida a PwC). Los minutos por alerta son un supuesto a validar con el especialista "
               "del banco. Ejemplo del mentor: 100 alertas × 30 min = 50 h; si el sistema cierra el 70% y el resto "
               "toma 15 min con el expediente armado → 7.5 h (≈ 85% de ahorro) sin dejar pasar críticos.")
