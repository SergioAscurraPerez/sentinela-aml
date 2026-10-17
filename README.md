# 🛡️ Sentinela AML

**Equipo de agentes de IA que investiga alertas AML y produce un expediente verificable: con un humano en el control, sin que los datos del cliente salgan del banco, y usando los protocolos de Project NANDA.**

> *Cada conclusión con firma, fuente y dueño.*

Hack AI Agents Impact · NTT DATA × Sundai LATAM × Project NANDA (MIT Media Lab) · Reto **B1: Investigación de alertas AML**

## Problema
- El 85–95% de las alertas de monitoreo transaccional son falsos positivos, y cada una toma ~20–60 min de un analista *(estimaciones de la industria: Facctum, Sayari)*.
- La norma peruana exige **sustentar** por qué una alerta inusual no se reporta, comunicar el ROS a la UIF-Perú **en máximo 24 h desde que se califica como sospechosa**, conservar el expediente **10 años** y mantener **reserva** (Ley 27693, Res. SBS 2660-2015).
- Los datos de clientes **no pueden** enviarse a un LLM en la nube (secreto bancario, datos personales).

## Solución
| | |
|---|---|
| 🤖 **5 agentes especialistas** | Transaccional (grafo + tipologías), Listas (PEP / sanciones), Normativo (citas exactas SBS/UIF), **Abogado del diablo** (busca explicaciones legítimas → menos falsos positivos), Redactor (borrador de ROS o sustento de no reporte) |
| 🔏 **Protocolos NANDA** | `identity` + `registry` (el orquestador descubre agentes por capacidad), `auth` (cada hallazgo firmado), `data_facts` (evidencia atribuida), `trust` (reputación según el feedback humano), `privacy` (campos que nunca salen) |
| 🧑‍⚖️ **Human-in-the-loop** | Validar/rechazar cada hallazgo (cambia la confianza en el agente y el riesgo), pedir más información a un agente, decisión final firmada **solo por el Oficial de Cumplimiento**, justificación obligatoria, doble justificación si descarta riesgo alto, reloj de 24 h al calificar |
| 🔒 **Privacidad** | LLM **local** (Ollama) + pseudonimización (CTA_###, montos en rangos) + guardia que bloquea cualquier prompt con datos sensibles. Panel "Lo que vio el modelo" |
| 🛰️ **Seguimiento** | Sala de control en vivo, bitácora SQLite de cada paso (actor, herramienta, latencia), expediente exportable con hash y botón **Verificar** |
| 😈 **Agente impostor** | Intenta colar un "descartar" suplantando a otro agente → firma inválida → rechazado y registrado |

## Arquitectura
```
Alerta ─► Orquestador ─► privacy (pseudonimiza) ─► registry.lookup(capacidad)
                │
                ├─► Agente transaccional ─┐   herramientas deterministas (pandas/networkx)
                ├─► Agente listas ────────┤   + LLM local solo con rasgos pseudonimizados
                ├─► Agente normativo ─────┤
                ├─► Abogado del diablo ───┤──► hallazgo firmado (auth) + evidencia (data_facts)
                └─► Agente redactor ──────┘
                │
                ▼
     riesgo = 1-Π(1-sev·w_confianza) × (1-0.6·mitigación)   (w = capa trust)
                │
                ▼
     GATE HUMANO (Oficial de Cumplimiento) ─► decisión firmada ─► expediente (sha256, 10 años)
```

## Cómo correr
```bash
# 1) entorno (Python 3.11+)
python -m venv .venv
.venv\Scripts\activate          # Windows   |   source .venv/bin/activate  (mac/linux)
pip install -r requirements.txt  # o, con el repo clonado al lado: pip install -e ../nandatown

# 2) datos: ya incluye 3 alertas sintéticas de demo. Para sumar datos reales de Kaggle:
python data/download_data.py
python data/prepare_data.py --source samld --nrows 2000000

# 3) LLM local (opcional; sin él funciona en "modo reglas")
ollama pull qwen2.5:7b

# 4) app
streamlit run app.py

# métrica de impacto
python evaluate.py
```

## Guion de demo (3 min)
1. **A-001** → *Investigar*: ver a los agentes descubrirse en el registro NANDA y firmar hallazgos (estructuración + pass-through internacional).
2. Expediente: el **abogado del diablo** no encuentra mitigantes. Rechazar un hallazgo → baja la confianza en ese agente y se recalcula el riesgo.
3. **Privacidad**: "Lo que vio el modelo" → ni un dato personal.
4. **Impostor** → rechazado. Firmar **escalar a ROS** → reloj de 24 h.
5. **A-003** (planilla): el abogado del diablo lo identifica como falso positivo → sugerir descartar con sustento.
6. Exportar → **Verificar** ✅.

## Estructura
```
app.py                 UI Streamlit
evaluate.py            métrica (recall / falsos positivos / tiempo)
core/orchestrator.py   flujo, gate humano, permisos, expediente
core/nanda_layer.py    BankTown: hospeda 6 capas de nandatown
core/pseudonymizer.py  tokens + rangos + guardia de fuga
core/llm.py            Ollama local + caché + respaldo determinista
core/analytics.py      herramientas deterministas (tipologías)
core/policy.yaml       política de autonomía del banco (regla de negocio)
agents/                5 agentes + impostor
data/                  descarga y preparación de datasets, normas, listas
```

## Limitaciones honestas
- Llaves HMAC derivadas de una semilla local (demo). En producción: HSM/KMS del banco.
- Normas: corpus curado a partir de fuentes públicas; verificar vigencia con Cumplimiento.
- Listas de riesgo sintéticas.

## Fuentes
- [Res. SBS 2660-2015](https://img.lpderecho.pe/wp-content/uploads/2017/10/LEGIS.PE-Reglamento-de-gestión-de-riesgos-de-lavado-de-activos-y-financiamiento-del-terrorismo.pdf) · [Ley 27693](https://contralaft.gob.pe/) · [Manual gremial PLAFT](https://www.howdengroup.com/sites/peru.howdenprod.com/files/2024-10/manual-gremial-para-la-prevencion-del-laft.pdf)
- [Facctum – AML false positive report](https://www.facctum.com/blog/aml-false-positive-report) · [Sayari – Alert Adjudication brief](https://sayari.com/wp-content/uploads/2026/05/Sayari_Alert_Adjudication_AMLA_Intelligence_Brief.pdf)
- [Project NANDA – nandatown](https://github.com/projnanda/nandatown)
- Datos: [SAML-D](https://www.kaggle.com/datasets/berkanoztas/synthetic-transaction-monitoring-dataset-aml) · [IBM AML](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
