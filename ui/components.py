"""Componentes visuales de Sentinela (HTML/CSS propio, sin dependencias extra)."""
from __future__ import annotations

import html
import json
import math
import os
from functools import lru_cache

import pandas as pd

TEAL, AMBER, RED, BLUE, MUTED = "#2DD4BF", "#F59E0B", "#F43F5E", "#60A5FA", "#7C93A0"
ZONE_COLOR = {"cierre_sugerido": TEAL, "revision_humana": AMBER, "escalamiento": RED}

CSS = """
<style>
  .block-container {padding-top: 3.2rem; max-width: 1400px;}
  .sn-hero {background: linear-gradient(120deg,#0F2A35 0%,#0B3B47 55%,#0E4D55 100%); border:1px solid #1C4452;
            border-radius:18px; padding:20px 26px; margin-bottom:14px; position:relative; overflow:hidden;}
  .sn-hero:after {content:""; position:absolute; right:-60px; top:-60px; width:220px; height:220px; border-radius:50%;
            background: radial-gradient(circle, rgba(45,212,191,.25), transparent 70%);}
  .sn-hero h2 {margin:0 0 4px 0; font-size:1.55rem; color:#F1FAFB;}
  .sn-hero .meta {color:#9DB7C0; font-size:.86rem;}
  .sn-pill {display:inline-block; padding:5px 14px; border-radius:999px; font-weight:700; font-size:.85rem;
            margin-top:10px; letter-spacing:.2px;}
  .sn-motivos {color:#C9DCE1; font-size:.85rem; margin-top:8px;}
  .sn-grid {display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; margin:6px 0 12px 0;}
  .sn-card {background:#0F2A35; border:1px solid #1C4452; border-radius:14px; padding:12px 14px; min-height:96px;
            transition: all .3s ease;}
  .sn-card .ic {font-size:1.35rem;}
  .sn-card .nm {font-weight:700; color:#E6F1F3; font-size:.9rem; margin-top:2px;}
  .sn-card .st {color:#9DB7C0; font-size:.78rem; margin-top:4px;}
  .sn-card.run {border-color:#2DD4BF; box-shadow:0 0 0 1px #2DD4BF, 0 0 18px rgba(45,212,191,.35);
                animation: snpulse 1.1s ease-in-out infinite;}
  .sn-card.done {border-color:#1E6B60;}
  .sn-card.err {border-color:#F43F5E;}
  @keyframes snpulse {0%,100%{box-shadow:0 0 0 1px #2DD4BF,0 0 10px rgba(45,212,191,.25)} 50%{box-shadow:0 0 0 1px #2DD4BF,0 0 26px rgba(45,212,191,.55)}}
  .sn-badge {display:inline-block; background:#123845; color:#8FE9DC; border-radius:6px; padding:1px 7px;
             font-size:.72rem; margin-right:4px; margin-top:6px;}
  .sn-rings {display:flex; gap:22px; align-items:center; flex-wrap:wrap;}
  .sn-ring-lbl {text-align:center; color:#9DB7C0; font-size:.8rem; margin-top:-4px;}
  .sn-find {background:#0F2A35; border:1px solid #1C4452; border-left-width:1px; border-radius:12px; padding:10px 14px; margin-bottom:8px;}
  .sn-find .cl {font-weight:650; color:#EAF6F7;}
  .sn-find .mt {color:#8EA9B2; font-size:.76rem; margin-top:3px;}
  .sn-sev {display:inline-block; height:6px; border-radius:3px; margin-top:6px;}
  .sn-kpi {background:#0F2A35; border:1px solid #1C4452; border-radius:14px; padding:12px 16px;}
  .sn-kpi .v {font-size:1.7rem; font-weight:800; color:#F1FAFB;} .sn-kpi .l {color:#9DB7C0; font-size:.8rem;}
</style>
"""


def ring(value: float, label: str, color: str, size: int = 112) -> str:
    v = max(0.0, min(1.0, float(value)))
    r = size / 2 - 9
    c = 2 * math.pi * r
    return f"""<div><svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
      <circle cx="{size/2}" cy="{size/2}" r="{r}" stroke="#1C4452" stroke-width="10" fill="none"/>
      <circle cx="{size/2}" cy="{size/2}" r="{r}" stroke="{color}" stroke-width="10" fill="none" stroke-linecap="round"
        stroke-dasharray="{c:.1f}" stroke-dashoffset="{c*(1-v):.1f}" transform="rotate(-90 {size/2} {size/2})">
        <animate attributeName="stroke-dashoffset" from="{c:.1f}" to="{c*(1-v):.1f}" dur="0.9s" fill="freeze"/></circle>
      <text x="50%" y="52%" text-anchor="middle" dominant-baseline="middle" fill="#F1FAFB" font-size="{size*0.22:.0f}"
        font-weight="800" font-family="sans-serif">{v:.2f}</text></svg><div class="sn-ring-lbl">{html.escape(label)}</div></div>"""


def risk_color(v: float) -> str:
    return RED if v >= 0.7 else (AMBER if v >= 0.35 else TEAL)


def hero(titulo: str, meta: str, zona: str | None, zona_label: str | None, motivos: list[str]) -> str:
    pill = ""
    if zona:
        col = ZONE_COLOR.get(zona, MUTED)
        pill = (f'<div class="sn-pill" style="background:{col}22;color:{col};border:1px solid {col}">{html.escape(zona_label)}</div>'
                f'<div class="sn-motivos">{" · ".join(html.escape(m) for m in motivos[:3])}</div>')
    return f'<div class="sn-hero"><h2>{html.escape(titulo)}</h2><div class="meta">{html.escape(meta)}</div>{pill}</div>'


AGENT_ICON = {"agente_transaccional": "🕸️", "agente_perfil": "🪪", "agente_listas": "📋", "agente_normativo": "⚖️",
              "abogado_del_diablo": "😈", "agente_redactor": "📝", "agente_fraccionamiento": "🧮",
              "agente_anomalia": "🤖", "agente_extraccion": "📄"}
AGENT_NAME = {"agente_transaccional": "Transaccional", "agente_perfil": "Perfil KYC", "agente_listas": "Listas PEP / sanciones",
              "agente_normativo": "Normativo SBS", "abogado_del_diablo": "Abogado del diablo", "agente_redactor": "Redactor ROS",
              "agente_fraccionamiento": "Ficha de fraccionamiento", "agente_anomalia": "Anomalía ML",
              "agente_extraccion": "Extracción de documentos"}


def agent_grid(state: dict[str, dict]) -> str:
    cards = []
    for name, ic in AGENT_ICON.items():
        s = state.get(name, {"st": "wait"})
        cls = {"run": "run", "done": "done", "err": "err"}.get(s["st"], "")
        txt = {"wait": "en espera", "run": "trabajando…", "done": f"✓ {s.get('n', 0)} hallazgos · {s.get('ms', 0)} ms",
               "err": "error"}.get(s["st"], "")
        badges = "".join(f'<span class="sn-badge">{html.escape(b)}</span>' for b in s.get("badges", []))
        cards.append(f'<div class="sn-card {cls}"><div class="ic">{ic}</div><div class="nm">{AGENT_NAME[name]}</div>'
                     f'<div class="st">{txt}</div>{badges}</div>')
    return '<div class="sn-grid">' + "".join(cards) + "</div>"


def finding_card(f: dict) -> str:
    col = {"riesgo": risk_color(f["severity"]), "mitigante": TEAL, "respuesta": BLUE}.get(f["kind"], MUTED)
    w = max(6, int(f["severity"] * 100))
    return (f'<div class="sn-find" style="border-left:4px solid {col}"><div class="cl">{html.escape(f["claim"])}</div>'
            f'<div class="mt">{AGENT_ICON.get(f["agent"], "🤖")} {html.escape(AGENT_NAME.get(f["agent"], f["agent"]))} · '
            f'severidad {f["severity"]:.2f} · firma NANDA <code>{f["signature"][:10]}…</code></div>'
            f'<div class="sn-sev" style="width:{w}%;background:{col}"></div></div>')


def kpi(value: str, label: str) -> str:
    return f'<div class="sn-kpi"><div class="v">{html.escape(value)}</div><div class="l">{html.escape(label)}</div></div>'


@lru_cache(maxsize=1)
def _vis_script() -> str:
    """vis-network embebido desde pyvis (funciona sin internet); si no está, usa el CDN."""
    try:
        import pyvis
        path = os.path.join(os.path.dirname(pyvis.__file__), "lib", "vis-9.1.2", "vis-network.min.js")
        with open(path, encoding="utf-8") as f:
            return "<script>" + f.read().replace("</script>", "<\\/script>") + "</script>"
    except Exception:
        return '<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>'


def graph_html(case, height: int = 520, full: bool = False) -> str:
    """Grafo interactivo (vis-network) con 'replay' cronológico de las transacciones."""
    nb = case.neighborhood.copy()
    ev_ids = {e for f in case.findings if f["kind"] == "riesgo" for e in f["evidence"]}
    if not full:
        nb = nb[nb.tx_id.isin(ev_ids) | (nb.sender == case.account) | (nb.receiver == case.account)]
    nb = nb.sort_values("timestamp").head(350)
    hot = {a for r in nb.itertuples() if r.tx_id in ev_ids for a in (r.sender, r.receiver)}
    vol: dict[str, float] = {}
    for r in nb.itertuples():
        vol[r.sender] = vol.get(r.sender, 0) + r.amount
        vol[r.receiver] = vol.get(r.receiver, 0) + r.amount
    vmax = max(vol.values(), default=1)
    nodes = []
    for n in vol:
        tok = case.pseudo.token(n)
        main = n == case.account
        col = RED if main else (AMBER if n in hot else "#3B6E85")
        nodes.append({"id": tok, "label": tok, "value": 8 + 40 * (vol[n] / vmax) ** 0.5 + (25 if main else 0),
                      "color": {"background": col, "border": "#F1FAFB" if main else col,
                                "highlight": {"background": "#FFFFFF", "border": col}},
                      "shadow": {"enabled": main or n in hot, "color": col, "size": 22 if main else 12},
                      "title": f"{tok} · volumen {vol[n]:,.0f}", "font": {"color": "#E6F1F3", "size": 13 if main else 11}})
    edges = []
    amax = float(nb.amount.max()) if len(nb) else 1
    for r in nb.itertuples():
        h = r.tx_id in ev_ids
        edges.append({"id": r.tx_id, "from": case.pseudo.token(r.sender), "to": case.pseudo.token(r.receiver),
                      "t": str(r.timestamp)[:16], "width": 1 + 5 * (r.amount / amax) ** 0.5,
                      "color": {"color": AMBER if h else "#35566A", "opacity": 1 if h else 0.6},
                      "dashes": False, "arrows": "to", "smooth": {"type": "curvedCW", "roundness": 0.15},
                      "title": f"{str(r.timestamp)[:16]} · {r.amount:,.0f} · {r.payment_type}"
                               + (" · EVIDENCIA" if h else "")})
    data = json.dumps({"nodes": nodes, "edges": edges})
    return f"""<!doctype html><html><head><meta charset="utf-8">
{_vis_script()}
<style>body{{margin:0;background:transparent;font-family:sans-serif}}
#wrap{{position:relative;height:{height}px;border-radius:16px;overflow:hidden;
 background:radial-gradient(ellipse at 30% 20%,#123845 0%,#0A2029 60%,#071820 100%);border:1px solid #1C4452}}
#net{{height:100%}}
.bar{{position:absolute;left:12px;right:12px;bottom:10px;display:flex;gap:10px;align-items:center;color:#C9DCE1;font-size:12px}}
button{{background:#2DD4BF;color:#062027;border:0;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer}}
button.sec{{background:#123845;color:#C9DCE1}}
.track{{flex:1;height:6px;background:#1C4452;border-radius:3px;overflow:hidden}} .fill{{height:100%;width:0;background:#F59E0B}}
.leg{{position:absolute;top:10px;left:12px;color:#C9DCE1;font-size:12px;background:#0A202999;padding:6px 10px;border-radius:8px}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 10px}}</style></head><body>
<div id="wrap"><div id="net"></div>
<div class="leg"><span class="dot" style="background:{RED}"></span>cuenta alertada<span class="dot" style="background:{AMBER}"></span>evidencia<span class="dot" style="background:#3B6E85"></span>contrapartes · tamaño = volumen</div>
<div class="bar"><button id="play">▶ Reproducir el movimiento del dinero</button><button class="sec" id="all">Ver todo</button>
<div class="track"><div class="fill" id="fill"></div></div><span id="ts">—</span></div></div>
<script>
const D={data};
const nodes=new vis.DataSet(D.nodes); const edges=new vis.DataSet(D.edges);
const net=new vis.Network(document.getElementById('net'),{{nodes,edges}},{{
  nodes:{{shape:'dot',scaling:{{min:8,max:60}},borderWidth:2}},
  edges:{{selectionWidth:2,hoverWidth:1.5}},
  physics:{{barnesHut:{{gravitationalConstant:-5200,springLength:120,springConstant:0.03}},stabilization:{{iterations:220}}}},
  interaction:{{hover:true,tooltipDelay:80,zoomView:false}}}});
net.once('stabilizationIterationsDone',()=>net.setOptions({{physics:{{enabled:false}}}}));
let timer=null;
function replay(){{ if(timer)clearInterval(timer); const all=D.edges; edges.clear(); let i=0;
  const step=Math.max(25,Math.floor(5200/Math.max(all.length,1)));
  timer=setInterval(()=>{{ if(i>=all.length){{clearInterval(timer);return;}} const e=all[i];
    edges.add(e); document.getElementById('ts').textContent=e.t;
    document.getElementById('fill').style.width=((i+1)/all.length*100)+'%';
    net.selectEdges([e.id]); i++; }},step); }}
document.getElementById('play').onclick=replay;
document.getElementById('all').onclick=()=>{{ if(timer)clearInterval(timer); edges.clear(); edges.add(D.edges);
  document.getElementById('fill').style.width='100%'; document.getElementById('ts').textContent='todas las transacciones'; }};
</script></body></html>"""


def swimlane_df(events: list[dict]) -> pd.DataFrame:
    rows, t = [], 0
    for e in events:
        if e["kind"] == "agente.fin" and e.get("latency_ms") is not None:
            ms = max(int(e["latency_ms"]), 15)
            rows.append({"agente": AGENT_NAME.get(e["actor"], e["actor"]), "inicio": t, "fin": t + ms,
                         "estado": e["status"], "ms": ms})
            t += ms
    return pd.DataFrame(rows)
