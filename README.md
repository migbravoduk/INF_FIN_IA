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
| **5 — Calendarios y Alertas** | ⏳ Planificada | Fechas de publicaciones, alertas automáticas |
| **6 — Análisis y Proyecciones** | ⏳ Planificada | Proyecciones macrofundadas, ratios, anomalías |
| **7 — API + Dashboard** | ⏳ Planificada | FastAPI + visualización web interactiva |
| **8 — Storytelling / Jules** | ⏳ Planificada | Reportes narrativos automáticos con LLM |

---

## Requisitos para retomar el proyecto

### Entorno Python

| Requisito | Versión | Notas |
|---|---|---|
| Python | 3.11+ | Instalado en `C:\Users\mbrav\anaconda3\` |
| Anaconda | Cualquiera | Se usa el Python del entorno base de Anaconda |
| pip | — | Disponible en el entorno Anaconda |

> **Importante**: En este equipo, Python está en Anaconda. Usar siempre `C:\Users\mbrav\anaconda3\python.exe` en vez de `python` o `py` (los alias del sistema apuntan al Microsoft Store stub).

### Dependencias

```powershell
C:\Users\mbrav\anaconda3\python.exe -m pip install -r requirements.txt
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
C:\Users\mbrav\anaconda3\python.exe main.py status
```

Salida esperada:
```
✅ Banco Central API
⚠️  (Fase 2) CMF API
```

---

## Instalación desde cero

```powershell
cd c:\Users\mbrav\Desktop\INF_FIN_IA

# Instalar dependencias en Anaconda
C:\Users\mbrav\anaconda3\python.exe -m pip install -r requirements.txt

# Copiar y editar credenciales
copy .env.example .env
# (editar .env con tus datos)

# Verificar estado
C:\Users\mbrav\anaconda3\python.exe main.py status

# Backfill inicial (descarga histórico de todas las series)
C:\Users\mbrav\anaconda3\python.exe main.py fetch --all
```

---

## Uso de la CLI

```powershell
# Ver estado del sistema y credenciales
C:\Users\mbrav\anaconda3\python.exe main.py status

# Backfill inicial — descarga todas las series del catálogo
C:\Users\mbrav\anaconda3\python.exe main.py fetch --all

# Descargar una serie por nombre (búsqueda en catálogo local)
C:\Users\mbrav\anaconda3\python.exe main.py fetch --series IPC
C:\Users\mbrav\anaconda3\python.exe main.py fetch --series "tipo de cambio"
C:\Users\mbrav\anaconda3\python.exe main.py fetch --series cobre

# Descargar por código exacto BDE (verificar código en si3.bcentral.cl primero)
C:\Users\mbrav\anaconda3\python.exe main.py fetch --id F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T

# Descargar con rango de fechas
C:\Users\mbrav\anaconda3\python.exe main.py fetch --series PIB --from-date 2010-01-01 --to-date 2025-12-31

# Consultar datos almacenados
C:\Users\mbrav\anaconda3\python.exe main.py query --series PIB
C:\Users\mbrav\anaconda3\python.exe main.py query --series PIB --format csv
C:\Users\mbrav\anaconda3\python.exe main.py query --series PIB --format json

# ============================================================
# Ingesta y Consulta Corporativa (Fase 3 — CMF de Chile)
# ============================================================

# Descargar e ingestar estados financieros trimestrales corporativos (ej. 202512)
C:\Users\mbrav\anaconda3\python.exe main.py fetch-cmf --period 202512

# Consultar estados financieros corporativos de forma interactiva (ej. Correos de Chile)
C:\Users\mbrav\anaconda3\python.exe main.py query-cmf --rut 60503000 --period 202512

# Consultar estados financieros filtrando por nombre de empresa en formato JSON
C:\Users\mbrav\anaconda3\python.exe main.py query-cmf --company "CORREOS" --period 202512 --limit 5 --format json

# ============================================================
# Ingesta y Consulta Bancaria (Fase 4 — CMF Bancos)
# ============================================================

# Descargar e ingestar reportes mensuales bancarios para un banco específico (ej. Banco de Chile 001, Dic 2025)
C:\Users\mbrav\anaconda3\python.exe main.py fetch-banks --year 2025 --month 12 --bank 001

# Descargar automáticamente todos los bancos de la plaza para un mes específico (Nov 2025)
C:\Users\mbrav\anaconda3\python.exe main.py fetch-banks --year 2025 --month 11

# Realizar un backfill histórico completo de todos los bancos principales desde 2024 de forma automatizada
C:\Users\mbrav\anaconda3\python.exe main.py fetch-banks --history

# Consultar activos u otras cuentas bancarias (ej. Total Activos 100000000 de Banco de Chile)
C:\Users\mbrav\anaconda3\python.exe main.py query-banks --bank 001 --account 100000000

# Exportar en formato JSON de alta precisión (limitado a 2 registros)
C:\Users\mbrav\anaconda3\python.exe main.py query-banks --bank 001 --account 100000000 --format json --limit 2

# ============================================================
# Listar y Scheduler
# ============================================================

# Listar series macroeconómicas registradas en la base de datos local
C:\Users\mbrav\anaconda3\python.exe main.py list

# Iniciar scheduler automático (bloqueante — corre indefinidamente)
C:\Users\mbrav\anaconda3\python.exe main.py run-scheduler
```

---

## Estructura del proyecto

```
INF_FIN_IA/
├── collectors/
│   ├── bcentral.py          # ✅ Cliente BDE API del Banco Central (Fases 1 y 2)
│   ├── cmf.py               # ✅ Ingestionador plano de Estados Financieros CMF (Fase 3)
│   ├── cmf_banks.py         # ✅ Cliente API REST de Estados Financieros Bancos CMF (Fase 4)
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
│   └── jobs.py              # ✅ APScheduler: daily/monthly/quarterly/annual
├── config/
│   ├── settings.py          # ✅ Configuración centralizada (pydantic-settings)
│   └── series_catalog.yaml  # ✅ Catálogo de series a ingestar
├── api/                     # ⏳ FastAPI REST (Fase 6)
├── dashboard/               # ⏳ Frontend web (Fase 6)
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
| **API REST** (FastAPI) | Endpoints para consumir los datos desde cualquier app | ⏳ Fase 6 |
| **Dashboard web** | Visualizaciones interactivas (Chart.js / Plotly) | ⏳ Fase 6 |
| **Reportes automáticos** | Documentos Word/PPT generados por Jules a partir de la BD | ⏳ Fase 7 |
| **Alertas** | Notificaciones cuando se acercan publicaciones o hay datos nuevos | ⏳ Fase 4 |
