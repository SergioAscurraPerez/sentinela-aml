# 🛡️ Sentinela AML

**El analista AML escribe un DNI y, en segundos, recibe el resultado del cliente — 🟢 pasa, 🟡 revisión manual o 🔴 crítico — con el motivo, la evidencia y el expediente listo. Lo arma un equipo de 9 agentes de IA que se descubren y firman cada hallazgo con los protocolos de Project NANDA, sin que los datos del cliente lleguen al modelo de lenguaje.**

> *Cada conclusión con firma, fuente y dueño.*

Hack AI Agents Impact · NTT DATA × Sundai LATAM × Project NANDA (MIT Media Lab) · Reto **B1: Investigación de alertas AML**

---

## Problema
- Entre el **90% y el 95%** de las alertas de monitoreo basado en reglas son falsos positivos *(recopilación de estadísticas de la industria atribuida a PwC)*; revisar cada una a mano toma decenas de minutos de un analista.
- La norma peruana exige **sustentar** por qué una operación inusual no se reporta, comunicar el ROS a la UIF-Perú **en máximo 24 h desde que se califica como sospechosa**, conservar el expediente **10 años** y mantener **reserva** (Ley 27693, Res. SBS 2660-2015).
- Los datos de clientes **no pueden** enviarse tal cual a un LLM (secreto bancario, datos personales).

## Scope de la solución

**Qué resuelve hoy (de punta a punta):**

| Dentro del alcance | Detalle |
|---|---|
| Consulta de un cliente por **DNI** | El DNI se enlaza a su cuenta en el core bancario simulado (dataset SAML-D + casos sintéticos) y se analiza su ventana de 30 días |
| **Fraccionamiento (pitufeo)** como tipología principal | Ficha de 8 parámetros ponderados: depósitos cerca del umbral, suma que supera el umbral sin que ningún depósito lo haga, depositantes distintos, canales el mismo día, días consecutivos, montos repetidos/redondos, relación con el ingreso declarado y salida rápida del dinero |
| Otras tipologías sobre la **misma arquitectura** | Estratificación en varios pasos (ciclos A→B→C→A en el grafo), pass-through, concentración-dispersión, fan-in/out, cambio de comportamiento, efectivo recurrente, transfronterizo, actividad súbita, perfil vs monto |
| Triaje en 3 niveles | 🟢 pasa (cierre sugerido que el analista confirma) · 🟡 revisión manual · 🔴 crítico (expediente listo + escalar al Oficial) |
| Usuario: **el analista AML** | Valida o rechaza hallazgos, pregunta a los agentes, adjunta documentos de origen de fondos y firma la decisión. El **Oficial de Cumplimiento** solo aparece como destino del escalamiento (es quien califica el ROS) |
| Evidencia auditable | Cada hallazgo firmado (NANDA `auth`), evidencia atribuida (`data_facts`), bitácora paso a paso y expediente exportable con sha256 y botón **Verificar** |
| Privacidad | Pseudonimización antes del LLM (CTA_###, montos en rangos, `[MONTO_k]`, `[DOC]`) y guardia que bloquea cualquier prompt con datos sensibles |
| Validación | Modo sombra sobre 75 clientes con resultado conocido + fórmula de ahorro de tiempo con supuestos visibles |

**Fuera del alcance (siguiente fase, se menciona en el pitch):**
- Conexión al CRM / core real del banco (en producción: el extractor consulta por API, en solo lectura y con permisos mínimos).
- Envío del ROS a ROSEL: el sistema **nunca envía nada solo**; el Oficial califica y reporta.
- Listas reales de sanciones/PEP y lista GAFI vigente (hoy: listas sintéticas, marcadas como tales).
- Llaves en HSM/KMS del banco (hoy: HMAC derivadas de una semilla local).
- RAG sobre manuales internos y entrenamiento supervisado con decisiones históricas del banco.

## Enfoque en el desarrollo técnico

1. **Los números los calcula el código; el LLM solo explica.** Todas las detecciones (ficha de fraccionamiento, ciclos en el grafo, umbrales en la moneda de la alerta, perfil vs monto) son herramientas deterministas en `pandas`/`networkx`. El riesgo, la confianza y el nivel de triaje **no dependen del LLM**, por eso el resultado es estable, reproducible y auditable. El modelo de lenguaje redacta explicaciones, el resumen ejecutivo, el borrador de ROS y lee documentos.
2. **Una sola estructura, un especialista por tipología.** Orquestador, privacidad, normativo, triaje y trazabilidad son comunes. Cada tipología es un agente (o una regla dentro del agente transaccional) que devuelve **el mismo formato de hallazgo**; el orquestador consolida con `riesgo = 1 − Π(1 − severidad·w_confianza) × (1 − 0.6·mitigación)`. Agregar una tipología = agregar un especialista, sin tocar el resto.
3. **Agentes que se descubren y firman (Project NANDA).** El orquestador no tiene los agentes "cableados": los busca por capacidad en el `registry`, verifica su `identity`, exige firma `auth` en cada hallazgo (un **agente impostor** que suplanta a otro es rechazado), registra la evidencia en `data_facts` y ajusta la reputación de cada agente (`trust`) según el feedback del analista.
4. **Confianza separada del riesgo + humano en el control.** Un caso entra a revisión humana aunque el riesgo sea bajo si los agentes no coinciden (el **abogado del diablo** encuentra una explicación legítima fuerte), si el cliente es nuevo, si no hay KYC, si hay poco historial, si el monto es muy alto o si el **modelo de ML** lo ve como un patrón nunca visto; además, un 10% de los cierres sugeridos va a control de calidad. Ningún agente puede cerrar, descartar ni escalar: lo bloquea la política de permisos (`policy.yaml`), que define el banco.
5. **Privacidad por diseño.** El analista ve al cliente; el modelo **nunca**. Antes de cualquier llamada al LLM se pseudonimiza (cuentas → CTA_###, montos → rangos) y una guardia revisa que no se cuele ningún valor prohibido. En documentos, los montos se reemplazan por `[MONTO_k]` y los DNI por `[DOC]`: el LLM solo dice cuál marcador es el monto declarado y la comparación es determinista. El panel *"Lo que vio el modelo"* muestra el prompt exacto.
6. **LLM intercambiable y con respaldo.** `policy.yaml` → `proveedor: auto`: usa **Ollama local** si está corriendo (ningún dato sale de la máquina) y si no **OpenRouter con modelos gratuitos** (candado `solo_modelos_gratis` y cadena de respaldo ante saturación). Solo si los tres gratuitos fallan responde un **respaldo de pago** barato (`respaldo_pago: google/gemini-2.5-flash-lite`, ~US$0.001 por cliente); se apaga dejando el campo vacío. Si no hay LLM, cada agente cae a su plantilla de reglas y el caso no se detiene.
7. **ML no supervisado como segunda opinión.** Un IsolationForest entrenado sin etiquetas con el comportamiento de ~1,000 cuentas ubica al cliente en un percentil de rareza. No suma riesgo por sí solo (evita falsos positivos): fuerza revisión humana cuando la cuenta es muy rara y ninguna regla la explica.
8. **Validación honesta.** `evaluate.py` corre el pipeline sobre clientes con etiqueta conocida (modo sombra) y reporta cuánto lavado se escaparía a cierre y cuántos normales se cierran. Cada cambio de reglas se midió contra esta corrida antes de subirlo.

## Resultados (modo sombra, `python evaluate.py`)

| Métrica | Valor |
|---|---|
| Clientes evaluados (SAML-D + sintéticos) | 75 |
| Lavado que **no** se va a cierre (no se escapa) | **61 / 62** |
| Lavado escalado directo como crítico | 34 / 62 |
| Normales que el sistema cierra (trabajo ahorrado) | 5 / 13 |
| Tiempo por cliente (modo reglas) | < 1 s · ~15 s con LLM |

Ahorro de tiempo (pestaña **Impacto**, supuestos editables):
`Ahorro = casos cerrados × t_manual + casos revisados × (t_manual − t_con_expediente)`.
Ejemplo: 100 alertas/día × 30 min = 50 h; si el sistema cierra el 70% y el resto toma 15 min con el expediente armado → 7.5 h (≈ 85% de ahorro) sin dejar pasar críticos. Los minutos por alerta son un supuesto a validar con el especialista del banco.

## Los 9 agentes
| Agente | Qué hace |
|---|---|
| 🕸️ Transaccional | Grafo del dinero y 12 tipologías (estructuración, pitufeo, ciclos, pass-through, concentración-dispersión, fan-in/out…) |
| 🪪 Perfil KYC | Perfil declarado vs lo recibido, cliente nuevo |
| 🧮 Ficha de fraccionamiento | 8 parámetros ponderados → pasa / revisión / crítico |
| 🤖 Anomalía ML | IsolationForest: ¿qué tan rara es la cuenta frente a las demás? |
| 📄 Extracción de documentos | Lee TXT/PDF/DOCX de origen de fondos, registra su huella sha256 y lo contrasta con lo recibido |
| 📋 Listas | PEP, sanciones, lista interna y jurisdicciones de mayor riesgo |
| ⚖️ Normativo | Citas exactas de la norma SBS/UIF aplicable (corpus curado, sin texto generado) |
| 😈 Abogado del diablo | Busca la explicación legítima más fuerte (menos falsos positivos) |
| 📝 Redactor | Resumen ejecutivo, borrador de ROS o sustento de no reporte |

## Arquitectura
```
DNI ─► Orquestador ─► privacy (pseudonimiza) ─► registry.lookup(capacidad)   [NANDA]
          │
          ├─► Transaccional · Perfil KYC · Ficha de fraccionamiento · Anomalía ML
          ├─► Extracción de documentos · Listas · Normativo · Abogado del diablo
          │      herramientas deterministas (pandas / networkx / scikit-learn)
          │      + LLM (Ollama local u OpenRouter gratuito) solo con datos pseudonimizados
          │      └─► cada hallazgo firmado (auth) + evidencia (data_facts)
          ▼
   riesgo = 1-Π(1-sev·w_confianza) × (1-0.6·mitigación)   ·   confianza (forzar humano si…)
          ▼
   🟢 pasa · 🟡 revisión · 🔴 crítico ─► Redactor ─► ANALISTA firma ─► (crítico) Oficial de Cumplimiento
          ▼
   expediente verificable (sha256, firmas, documentos, bitácora; conservar 10 años)
```

## Cómo correr
```bash
# 1) entorno (Python 3.11+)
python -m venv .venv
.venv\Scripts\activate           # Windows   |   source .venv/bin/activate  (mac/linux)
pip install -r requirements.txt

# 2) LLM (opcional; sin él funciona en "modo reglas")
#    a) local, ningún dato sale de la máquina:
ollama pull qwen2.5:3b
#    b) o OpenRouter con modelos gratuitos: crear .streamlit/secrets.toml (no se sube a git)
#       OPENROUTER_API_KEY = "sk-or-..."

# 3) app
streamlit run app.py

# métricas del modo sombra -> data/metricas.json  (SENTINELA_LLM=ninguno fuerza modo reglas)
python evaluate.py
```

**Despliegue en Streamlit Cloud:** Ollama no corre en la nube (sin GPU y sin servidor local), así que allí se usa OpenRouter gratuito: en *Settings → Secrets* poner `OPENROUTER_API_KEY`. La demo en vivo desde la laptop puede usar Ollama.

> Límite de OpenRouter para modelos gratuitos: ~50 llamadas al día con menos de US$10 comprados y ~1,000/día desde US$10 (cada cliente usa ~3). Los gratuitos no consumen créditos; los créditos solo se usan si responde el respaldo de pago.

## Guion de demo (3 min)
1. **Consulta por DNI** → 🎲 *Cliente al azar* o un DNI del directorio: los agentes se descubren en NANDA y firman hallazgos en vivo.
2. Un cliente 🟢: una línea, ✓ y *Confirmar cierre*.
3. Un cliente 🔴: señales, **ficha de fraccionamiento**, grafo con replay, *Escalar al Oficial* (firma el analista).
4. **🧭 Paso a paso**: qué hizo cada agente, en lenguaje simple. **Privacidad**: lo que vio el modelo, ni un dato personal.
5. Adjuntar `data/docs/declaracion_origen_fondos.txt` → "declara 10k-15k vs 100k-150k recibidos: no cuadra".
6. Agente impostor → rechazado. Expediente → **Verificar** ✅.
7. **Registrar un cliente en vivo** (el jurado elige la tipología o *sorpresa*) → se analiza desde cero.
8. **Impacto**: modo sombra + fórmula de ahorro.

## Estructura
```
app.py                  UI Streamlit (consulta por DNI, bandeja por riesgo, privacidad, impacto)
evaluate.py             modo sombra: métricas sobre clientes con resultado conocido
core/orchestrator.py    flujo, consulta por DNI, triaje, permisos, documentos, expediente
core/nanda_layer.py     BankTown: identity, registry, auth, data_facts, trust, privacy (nandatown)
core/llm.py             Ollama local / OpenRouter gratuito + guardia de privacidad + respaldo
core/pseudonymizer.py   tokens CTA_###, rangos de monto, detección de fugas
core/analytics.py       herramientas deterministas (tipologías, grafo)
core/policy.yaml        política del banco: triaje, permisos, umbrales, LLM
agents/                 9 agentes + impostor
ui/components.py        tarjetas, anillos, grafo, narración paso a paso
data/                   alertas, clientes, transacciones, normas, listas, jurisdicciones, docs de ejemplo
```

## Limitaciones honestas
- Datos sintéticos (SAML-D + casos generados); el DNI y el nombre de cada cliente son ficticios.
- Llaves HMAC derivadas de una semilla local (demo). En producción: HSM/KMS del banco.
- Normas: corpus curado a partir de fuentes públicas; verificar vigencia con Cumplimiento. Listas y jurisdicciones sintéticas.
- La ficha de fraccionamiento usa umbrales iniciales; deben calibrarse con el banco.

## Fuentes
- [Res. SBS 2660-2015](https://img.lpderecho.pe/wp-content/uploads/2017/10/LEGIS.PE-Reglamento-de-gestión-de-riesgos-de-lavado-de-activos-y-financiamiento-del-terrorismo.pdf) · [Ley 27693](https://contralaft.gob.pe/) · [Manual gremial PLAFT](https://www.howdengroup.com/sites/peru.howdenprod.com/files/2024-10/manual-gremial-para-la-prevencion-del-laft.pdf)
- [Facctum – AML false positive report](https://www.facctum.com/blog/aml-false-positive-report) · [Sayari – Alert Adjudication brief](https://sayari.com/wp-content/uploads/2026/05/Sayari_Alert_Adjudication_AMLA_Intelligence_Brief.pdf)
- [Project NANDA – nandatown](https://github.com/projnanda/nandatown)
- Datos: [SAML-D](https://www.kaggle.com/datasets/berkanoztas/synthetic-transaction-monitoring-dataset-aml) · [IBM AML](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
