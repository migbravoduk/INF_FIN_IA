# 🇨🇱 Repositorio de Información Financiera — Chile

> **Visión del proyecto**: Sistema que acumula datos financieros y macroeconómicos de Chile de forma continua, aplica procesamiento analítico (proyecciones de estados financieros con fundamento macro, detección de anomalías, comparación sectorial) y entrega la información procesada a través de herramientas de storytelling (dashboards, reportes narrativos, API consumible por otros sistemas).

---

## Arquitectura general

```
FUENTES (BCCh, CMF web, SII, Bolsa)
        ↓ ingestión automática (scheduler)
BASE DE DATOS (DuckDB local)
        ↓ procesamiento + modelos
CAPA ANALÍTICA (proyecciones, ratios, series derivadas)
        ↓ entrega
STORYTELLING (dashboard web, API REST, reportes Jules)
```

---

## Estado actual

| Fase | Estado | Descripción |
|------|--------|-------------|
| **1 — Macro BCCh** | ✅ Activa | PIB, IPC, TPM, empleo, tipo de cambio |
| **2 — Series adicionales BCCh + SII** | ✅ Activa | UF, UTM, IVP, IMACEC desde la API de la BDE |
| **3 — CMF: Empresas y Mercados** | ✅ Activa | Ingesta de archivos trimestrales planos .txt de estados financieros corporativos |
| **4 — CMF: Bancos e Inst. Financieras** | ✅ Activa | Ingesta mensual de balances y resultados con desglose por moneda desde la API REST SBIFv3 |
| **SP — Fondos de Pensiones** | ✅ Activa | Valores cuota diarios (desde 2002), carteras mensuales XML, cinta de precios diaria |
| **5 — Calendarios y Alertas** | 🟡 Parcial | Catch-up por frescura: ingesta automática "al publicarse" (`main.py catchup`) |
| **6 — Análisis y Proyecciones** | ⏳ Planificada | Proyecciones macrofundadas, ratios, anomalías |
| **7 — API + Dashboard** | 🟡 En desarrollo | FastAPI + dashboard (panel multi-fuente, EEFF, banca, AFP) — ver "Capa Web" |
| **8 — Storytelling / Jules** | ⏳ Planificada | Reportes narrativos automáticos con LLM |

---

## Requisitos para retomar el proyecto

### Entorno Python

| Requisito | Versión | Notas |
|---|---|---|
| Python | 3.11+ | Entorno virtual local del proyecto en `.venv\` |
| venv | — | `python -m venv .venv` y activar/usar su intérprete |
| pip | — | Disponible dentro del `.venv` |

> **Importante**: En este equipo `python`/`py` apuntan al stub de Microsoft Store. Usar siempre el intérprete del entorno virtual del proyecto: `.\.venv\Scripts\python.exe`.

### Dependencias

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Credenciales y accesos

| Servicio | Variable en `.env` | Cómo obtenerla | Estado |
|---|---|---|---|
| **Banco Central Chile — BDE API** | `BCENTRAL_USER` / `BCENTRAL_PASS` | Registro gratuito en [si3.bcentral.cl/estadisticas/Principal1/Web_Services](https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/index.htm) → luego iniciar sesión en la misma página para **activar** las credenciales (paso obligatorio) | ✅ Configurada |
| **CMF Chile** | — | Sin credenciales — acceso por scraping del portal web [cmfchile.cl](https://www.cmfchile.cl) | Sin API pública |
| **SII Chile** | — | Sin credenciales — scraping HTML público [sii.cl](https://www.sii.cl/valores_y_fechas/uf/) | Sin API pública |
| **Bolsa de Santiago** | — | Sin credenciales — scraping (Fase futura) | Pendiente |

> **Nota sobre la activación del BCCh**: El registro en si3.bcentral.cl crea la cuenta, pero las credenciales para la API REST BDE requieren un **segundo paso manual**: iniciar sesión en [si3.bcentral.cl/Siete/es/Siete/API](https://si3.bcentral.cl/Siete/es/Siete/API) y hacer clic en "Activar credenciales". Sin este paso, la API devuelve `Invalid username or password` aunque las credenciales sean correctas.

### Archivo `.env`

```powershell
# Copiar la plantilla y editar
copy .env.example .env
```

Contenido esperado del `.env`:

```env
BCENTRAL_USER=tu_email@ejemplo.com
BCENTRAL_PASS=tu_contraseña_bde
CMF_API_KEY=
DB_PATH=data/finanzas_chile.duckdb
DAILY_FETCH_TIME=08:00
TIMEZONE=America/Santiago
LOG_LEVEL=INFO
LOG_FILE=logs/app.log
```

### Verificar que todo está en orden

```powershell
.\.venv\Scripts\python.exe main.py status
```

Salida esperada:
```
✅ Banco Central API
⚠️  (Fase 2) CMF API
```

---

## Instalación desde cero

```powershell
cd C:\Users\mbrav\Desktop\PROYECTOS_IA\INF_FIN_IA

# Crear el entorno virtual (si no existe) e instalar dependencias
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Copiar y editar credenciales
copy .env.example .env
# (editar .env con tus datos)

# Verificar estado
.\.venv\Scripts\python.exe main.py status

# Backfill inicial (descarga histórico de todas las series)
.\.venv\Scripts\python.exe main.py fetch --all
```

---

## Uso de la CLI

```powershell
# Ver estado del sistema y credenciales
.\.venv\Scripts\python.exe main.py status

# Backfill inicial — descarga todas las series del catálogo
.\.venv\Scripts\python.exe main.py fetch --all

# Descargar una serie por nombre (búsqueda en catálogo local)
.\.venv\Scripts\python.exe main.py fetch --series IPC
.\.venv\Scripts\python.exe main.py fetch --series "tipo de cambio"
.\.venv\Scripts\python.exe main.py fetch --series cobre

# Descargar por código exacto BDE (verificar código en si3.bcentral.cl primero)
.\.venv\Scripts\python.exe main.py fetch --id F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T

# Descargar con rango de fechas
.\.venv\Scripts\python.exe main.py fetch --series PIB --from-date 2010-01-01 --to-date 2025-12-31

# Consultar datos almacenados
.\.venv\Scripts\python.exe main.py query --series PIB
.\.venv\Scripts\python.exe main.py query --series PIB --format csv
.\.venv\Scripts\python.exe main.py query --series PIB --format json

# ============================================================
# Ingesta y Consulta Corporativa (Fase 3 — CMF de Chile)
# ============================================================

# Descargar e ingestar estados financieros trimestrales corporativos (ej. 202512)
.\.venv\Scripts\python.exe main.py fetch-cmf --period 202512

# Consultar estados financieros corporativos de forma interactiva (ej. Correos de Chile)
.\.venv\Scripts\python.exe main.py query-cmf --rut 60503000 --period 202512

# Consultar estados financieros filtrando por nombre de empresa en formato JSON
.\.venv\Scripts\python.exe main.py query-cmf --company "CORREOS" --period 202512 --limit 5 --format json

# ============================================================
# Ingesta y Consulta Bancaria (Fase 4 — CMF Bancos)
# ============================================================

# Descargar e ingestar reportes mensuales bancarios para un banco específico (ej. Banco de Chile 001, Dic 2025)
.\.venv\Scripts\python.exe main.py fetch-banks --year 2025 --month 12 --bank 001

# Descargar automáticamente todos los bancos de la plaza para un mes específico (Nov 2025)
.\.venv\Scripts\python.exe main.py fetch-banks --year 2025 --month 11

# Realizar un backfill histórico completo de todos los bancos principales desde 2024 de forma automatizada
.\.venv\Scripts\python.exe main.py fetch-banks --history

# Consultar activos u otras cuentas bancarias (ej. Total Activos 100000000 de Banco de Chile)
.\.venv\Scripts\python.exe main.py query-banks --bank 001 --account 100000000

# Exportar en formato JSON de alta precisión (limitado a 2 registros)
.\.venv\Scripts\python.exe main.py query-banks --bank 001 --account 100000000 --format json --limit 2

# ============================================================
# Ingesta y Consulta de Pensiones (Superintendencia de Pensiones — SP)
# ============================================================

# Descargar e ingestar valores cuota de multifondos (ej. histórico completo desde 2002 para todos los fondos)
.\.venv\Scripts\python.exe main.py fetch-sp-cuotas --year-start 2002

# Descargar valores cuota para un fondo y rango específico (ej. Fondo A entre 2025 y 2026)
.\.venv\Scripts\python.exe main.py fetch-sp-cuotas --year-start 2025 --year-end 2026 --fund A

# Descargar la cartera de inversión mensual desagregada para un período específico (ej. Enero 2026)
.\.venv\Scripts\python.exe main.py fetch-sp-cartera --period 202601

# Descargar la cinta diaria de precios de instrumentos financieros para una fecha específica (ej. 2026-01-02)
.\.venv\Scripts\python.exe main.py fetch-sp-precios --date 2026-01-02

# Realizar un backfill completo de precios diarios (últimos 5 años diarios, y anteriores 5 años los miércoles)
.\.venv\Scripts\python.exe main.py fetch-sp-precios --history

# Consultar valores cuota y patrimonio en terminal
.\.venv\Scripts\python.exe main.py query-sp-cuotas --afp CAPITAL --fund A --limit 5

# Consultar precios diarios de instrumentos (ej. nemotécnico o RUT)
.\.venv\Scripts\python.exe main.py query-sp-precios --instrument AESANDES --limit 5

# ============================================================
# Listar y Scheduler
# ============================================================

# Listar series macroeconómicas registradas en la base de datos local
.\.venv\Scripts\python.exe main.py list

# Iniciar scheduler automático (bloqueante — corre indefinidamente)
.\.venv\Scripts\python.exe main.py run-scheduler
```

---

## Capa Web (Fase 7) — API REST + Dashboard

Stack: **FastAPI + Jinja2 + HTMX + Plotly** (server-rendered, sin Node). La API reusa la
capa DuckDB existente y se sirve en un solo proceso.

```powershell
# Levantar la web (panel + API navegable en /docs)
.\.venv\Scripts\python.exe main.py serve

# Levantar en proceso único CON el scheduler embebido (BD en lectura/escritura)
.\.venv\Scripts\python.exe main.py serve --with-scheduler

# Generar un HTML local autocontenido del panel (sin servidor) para examinarlo
.\.venv\Scripts\python.exe main.py web-preview
```

Vistas disponibles:

| Ruta | Descripción |
|---|---|
| `/` | Panel multi-fuente (KPIs de BCCh, banca, AFP, mercado + gráfico UF) |
| `/eeff` | Estados financieros corporativos (CMF) por empresa/período |
| `/banca` | Estados bancarios con desglose por moneda, por banco/período |
| `/afp` | Valor cuota por AFP y multifondo (gráfico de 2 años) |
| `/docs` | Swagger de la API REST (`/api/...`) |

> **Concurrencia DuckDB**: solo un proceso puede escribir a la vez. Por eso, para correr la
> web y el scheduler simultáneamente, usar `serve --with-scheduler` (proceso único) en vez de
> levantar `run-scheduler` aparte.

### Ingesta automática "al publicarse" (catch-up por frescura)

```powershell
# Ver qué fuentes tienen datos pendientes sin descargar nada
.\.venv\Scripts\python.exe main.py catchup --dry-run

# Descargar lo que falte dentro de su ventana de publicación (idempotente)
.\.venv\Scripts\python.exe main.py catchup
```

El catch-up compara, por fuente, el último período en la BD contra el siguiente esperado
(según frecuencia + rezago de publicación) y descarga solo lo que corresponda. Corre también
como job horario dentro del scheduler.

---

## Estructura del proyecto

```
INF_FIN_IA/
├── collectors/
│   ├── bcentral.py          # ✅ Cliente BDE API del Banco Central (Fases 1 y 2)
│   ├── cmf.py               # ✅ Ingestionador plano de Estados Financieros CMF (Fase 3)
│   ├── cmf_banks.py         # ✅ Cliente API REST de Estados Financieros Bancos CMF (Fase 4)
│   ├── sp_pensions.py       # ✅ Scraping de valores cuota, carteras y precios SP
│   └── sii.py               # 🔜 Scraping SII alternativo (Fase 2)
├── processors/
│   ├── normalizer.py        # ✅ Normalización y limpieza de datos macro
│   ├── standardizer.py      # 🔜 Mapeo XBRL → esquema estándar (Fase 3)
│   ├── xbrl_parser.py       # 🔜 Parseo XBRL con arelle (Fase 3)
│   └── pdf_extractor.py     # 🔜 Extracción de tablas de PDFs (Fase 3)
├── db/
│   ├── database.py          # ✅ Capa de acceso DuckDB
│   └── schema.py            # ✅ Definición de tablas (series, observations, cmf, fetch_log)
├── scheduler/
│   ├── jobs.py              # ✅ APScheduler: daily/monthly/quarterly/annual + catch-up
│   └── freshness.py         # ✅ Sondas de frescura (ingesta "al publicarse")
├── config/
│   ├── settings.py          # ✅ Configuración centralizada (pydantic-settings)
│   └── series_catalog.yaml  # ✅ Catálogo de series a ingestar
├── api/                     # ✅ FastAPI: routers /api/*, vistas HTML, templates Jinja2, static
│   ├── main.py              #    app + lifespan (scheduler embebido opcional)
│   ├── routers/             #    macro, cmf, banks, sp, dashboard_kpi, views
│   ├── templates/           #    base, overview, eeff, banca, afp + partials
│   └── preview.py           #    export estático del panel (main.py web-preview)
├── data/
│   ├── cmf_raw/             # Caché local de archivos planos trimestrales (.txt) de la CMF
│   ├── bank_raw/            # Caché local de respuestas JSON de la API CMF Bancos (Fase 4)
│   └── finanzas_chile.duckdb  # Base de datos DuckDB (generado automáticamente)
├── logs/                    # Logs de ejecución (generado automáticamente)
├── main.py                  # ✅ CLI entry point
├── .env                     # Credenciales (NO subir a git)
├── .env.example             # Plantilla de variables
├── requirements.txt         # ✅ Dependencias Python
└── README.md
```

---

## Series configuradas (Fase 1 y Fase 2 — BCCh BDE API)

| Categoría | Serie | Código BDE | Frecuencia |
|---|---|---|---|
| **Actividad** | PIB Real (encadenado 2018) | `F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T` | Trimestral |
| **Precios** | IPC General (base dic. 2023) | `G073.IPC.IND.2023.M` | Mensual |
| **Precios** | Variación mensual IPC | `G073.IPC.VAR.2023.M` | Mensual |
| **Precios** | Inflación 12 meses (anual) | `G073.IPC.V12.2023.M` | Mensual |
| **Política Monetaria** | TPM nominal | `F022.TPM.TIN.D001.NO.Z.D` | Diario |
| **Laboral** | Tasa de desempleo | `F049.DES.TAS.INE.10.M` | Mensual |
| **Tipo de Cambio** | CLP/USD observado diario | `F073.TCO.PRE.Z.D` | Diario |
| **Tipo de Cambio** | CLP/USD promedio mensual | `F073.TCO.PRE.HIST.M` | Mensual |
| **Sector Externo** | Cuenta corriente BoP | `F068.A.FLU.Z.0.S.N.Z.Z.Z.Z.6.0.T` | Trimestral |
| **Fiscal** | Deuda Gobierno Central (% PIB) | `F051.D7.PPB.C.Z.Z.T` | Trimestral |
| **Materias Primas** | Precio del cobre (BML) | `F019.PPB.PRE.40.M` | Mensual |
| **Fase 2: UF** | Unidad de Fomento | `F073.UFF.PRE.Z.D` | Diario |
| **Fase 2: IVP** | Índice de Valor Promedio | `F073.IVP.PRE.Z.D` | Diario |
| **Fase 2: UTM** | Unidad Tributaria Mensual | `F073.UTR.PRE.Z.M` | Mensual |
| **Fase 2: IMACEC** | IMACEC mensual (Base 2018) | `F032.ICF.IND.Z.Z.EP18.Z.Z.0.M` | Mensual |

Para agregar más series, editar `config/series_catalog.yaml`.

---

## Scheduler automático

| Job | Trigger | Series |
|---|---|---|
| `daily_fetch` | Lun–Vie a las 08:00 (Santiago) | Tipo de cambio diario |
| `monthly_fetch` | Día 6 de cada mes, 09:00 | IPC, TPM, desempleo, etc. |
| `quarterly_fetch` | Día 10 (ene/abr/jul/oct), 09:30 | PIB, balanza de pagos |
| `annual_fetch` | 15 de febrero, 10:00 | Balance fiscal |
| `sp_daily_fetch` | Lun–Vie a las 18:30 (Santiago) | Cinta de precios e ingesta de valores cuota SP |
| `sp_monthly_fetch` | Día 15 de cada mes, 20:00 | Cartera desagregada mensual del mes anterior SP |

Para correrlo como servicio persistente en Windows:
- Usar el **Programador de Tareas** de Windows (Task Scheduler)
- O instalar `nssm` (Non-Sucking Service Manager) para gestionar el proceso

---

## Base de datos

Se usa **DuckDB** (`data/finanzas_chile.duckdb`), ideal para análisis OLAP local sin servidor.

### Consulta de Series Macroeconómicas

```python
import duckdb
conn = duckdb.connect("data/finanzas_chile.duckdb")

# Ver todas las series disponibles
df = conn.execute("SELECT id, name, frequency FROM series").fetchdf()

# Últimos datos del PIB
df = conn.execute("""
    SELECT date, value
    FROM observations
    WHERE series_id = 'F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T'
    ORDER BY date DESC LIMIT 10
""").fetchdf()
print(df)
```

### Consulta de Estados Financieros CMF (Empresas)

```python
import duckdb
conn = duckdb.connect("data/finanzas_chile.duckdb")

# Consultar cuentas del balance de una empresa específica (ej. 2025-12)
df = conn.execute("""
    SELECT period, company_name, account_name, value, statement_group
    FROM cmf_financial_statements
    WHERE rut = '60503000' AND period = 202512
    LIMIT 10
""").fetchdf()
print(df)
```

### Consulta de Estados Financieros de Bancos (Desglose de Moneda)

```python
import duckdb
conn = duckdb.connect("data/finanzas_chile.duckdb")

# Consultar la cuenta de Total Activos (100000000) mostrando la distribución de monedas en Banco de Chile
df = conn.execute("""
    SELECT 
        period, 
        bank_name, 
        account_name, 
        val_clp_no_reaj AS clp_no_reajustable,
        val_clp_reaj_ipc AS reajustable_uf,
        val_clp_reaj_tc AS reajustable_usd,
        val_extranjera AS moneda_extranjera,
        val_total AS total_consolidado
    FROM cmf_bank_statements
    WHERE bank_code = '001' AND account_code = '100000000'
    ORDER BY period DESC
""").fetchdf()
print(df)
```

### Consulta de Fondos de Pensiones (SP)

```python
import duckdb
conn = duckdb.connect("data/finanzas_chile.duckdb")

# Valores cuota diarios de AFP Habitat para el Fondo A (últimos 10 registros)
df = conn.execute("""
    SELECT date, fund_type, quota_value, equity_value
    FROM sp_quota_values
    WHERE afp_name = 'HABITAT' AND fund_type = 'A'
    ORDER BY date DESC LIMIT 10
""").fetchdf()
print(df)

# Distribución de portafolio mensual (Glosa / Activos con mayor porcentaje en Ene 2026)
df = conn.execute("""
    SELECT period, fund_type, instrument_glosa, porcentaje
    FROM sp_portfolio_holdings
    WHERE afp_name = 'TOTAL' AND period = '2026-01'
    ORDER BY porcentaje DESC LIMIT 10
""").fetchdf()
print(df)

# Consulta de nemotécnicos en la cinta de precios diaria (ej. AESANDES)
df = conn.execute("""
    SELECT date, instrument_id, instrument_type, currency, price
    FROM sp_instrument_prices
    WHERE instrument_id = 'AESANDES'
    ORDER BY date DESC LIMIT 5
""").fetchdf()
print(df)
```

---

## Roadmap de procesamiento analítico

Una vez que la base de datos tenga cobertura histórica, se incorporarán:

| Capacidad | Descripción | Dependencias |
|---|---|---|
| **Proyecciones macro** | Modelos de proyección de variables macro (VAR, ARIMA, Kalman) usando los datos del BCCh | Fase 1–2 completas |
| **Proyecciones de EEFF** | Proyecciones de estados financieros de empresas ancladas al escenario macro | Fase 3 completa |
| **Ratios y comparación sectorial** | Cálculo automático de ROE, ROA, EV/EBITDA por empresa y sector | Fase 3 completa |
| **Detección de anomalías** | Alertas cuando una empresa o indicador se desvía de su comportamiento histórico | Fase 3 + datos históricos |
| **Informes narrativos (Jules)** | Integración con el agente Jules para generación automática de reportes Word/PPT | Fase 6 |

---

## Roadmap de storytelling

| Herramienta | Descripción | Estado |
|---|---|---|
| **CLI** | Consulta de datos por terminal | ✅ Operativa |
| **API REST** (FastAPI) | Endpoints `/api/*` para consumir los datos (`main.py serve` → `/docs`) | 🟡 En desarrollo |
| **Dashboard web** | Panel multi-fuente + EEFF + banca + AFP (Jinja2/HTMX/Plotly) | 🟡 En desarrollo |
| **Reportes automáticos** | Documentos Word/PPT generados por Jules a partir de la BD | ⏳ Fase 7 |
| **Alertas** | Notificaciones cuando se acercan publicaciones o hay datos nuevos | ⏳ Fase 4 |
