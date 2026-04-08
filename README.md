# 🇨🇱 Repositorio de Información Financiera — Chile

Sistema de ingestión, almacenamiento y consulta de datos financieros y macroeconómicos de Chile. Se actualiza automáticamente según la frecuencia de cada fuente.

## Estado actual

| Fase | Estado | Descripción |
|------|--------|-------------|
| **1 — Macro BCCh** | ✅ Activa | PIB, IPC, TPM, empleo, tipo de cambio |
| **2 — CMF Indicadores** | 🔜 Próxima | UF, UTM, IVP + scraping web CMF |
| **3 — EEFF Empresas** | ⏳ Planificada | XBRL + PDF empresas listadas |
| **4 — Calendarios** | ⏳ Planificada | Fechas de publicaciones |
| **5 — API + Dashboard** | ⏳ Planificada | FastAPI + visualización web |
| **6 — IA** | ⏳ Planificada | Análisis, reportes automáticos |

---

## Instalación

### Requisitos
- Python 3.11+
- pip

### 1. Clonar y configurar entorno

```powershell
cd c:\Users\mbrav\Desktop\INF_FIN_IA
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configurar credenciales

```powershell
copy .env.example .env
```

Edita `.env` y completa:

| Variable | Fuente | Url de registro |
|---|---|---|
| `BCENTRAL_USER` | Banco Central Chile | [si3.bcentral.cl](https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/index.htm) |
| `BCENTRAL_PASS` | Banco Central Chile | (misma url) |
| `CMF_API_KEY` | CMF Chile | [api.cmfchile.cl](https://api.cmfchile.cl) |

> **Nota**: Ambas son gratuitas y solo requieren registro con email.

---

## Uso de la CLI

```powershell
# Activar entorno virtual primero
.venv\Scripts\activate

# Ver estado del sistema y credenciales
python main.py status

# Backfill inicial — descarga todas las series (recomendado la primera vez)
python main.py fetch --all

# Descargar una serie por nombre
python main.py fetch --series IPC
python main.py fetch --series "tipo de cambio"
python main.py fetch --series cobre

# Descargar por código exacto de la BDE
python main.py fetch --id F073.IPC.IND.N.DIC.Z.Z.2023100

# Descargar con rango de fechas
python main.py fetch --series IPC --from-date 2015-01-01 --to-date 2025-12-31

# Consultar datos almacenados
python main.py query --series IPC
python main.py query --series IPC --from-date 2020-01-01
python main.py query --series IPC --format csv
python main.py query --series IPC --format json

# Listar series en la base de datos
python main.py list

# Buscar series disponibles en la BDE del BCCh
python main.py search --term "cobre"
python main.py search --term "imacec"

# Iniciar scheduler automático (bloqueante)
python main.py run-scheduler
```

---

## Estructura del proyecto

```
INF_FIN_IA/
├── collectors/
│   └── bcentral.py          # Cliente BDE API del Banco Central
├── processors/
│   └── normalizer.py        # Normalización y limpieza de datos
├── db/
│   ├── database.py          # Capa de acceso DuckDB
│   └── schema.py            # Definición de tablas
├── scheduler/
│   └── jobs.py              # Jobs APScheduler (daily/monthly/quarterly/annual)
├── config/
│   ├── settings.py          # Configuración centralizada (pydantic-settings)
│   └── series_catalog.yaml  # Catálogo de series a ingestar
├── data/                    # Base de datos DuckDB (generado automáticamente)
├── logs/                    # Logs de ejecución (generado automáticamente)
├── main.py                  # CLI entry point
├── .env.example             # Plantilla de variables de entorno
├── requirements.txt
└── README.md
```

---

## Series configuradas (Fase 1)

| Categoría | Serie | Frecuencia |
|---|---|---|
| Actividad | PIB Real (encadenado 2018) | Trimestral |
| Actividad | PIB Real — var. % anual | Trimestral |
| Precios | IPC (base dic. 2023) | Mensual |
| Precios | Variación mensual IPC | Mensual |
| Precios | Inflación 12 meses | Mensual |
| Política Monetaria | TPM nominal | Mensual |
| Laboral | Tasa de desempleo | Mensual |
| Tipo de Cambio | CLP/USD diario | Diario |
| Tipo de Cambio | CLP/USD mensual | Mensual |
| Sector Externo | Cuenta corriente BoP | Trimestral |
| Fiscal | Balance fiscal GC | Anual |
| Materias Primas | Precio del cobre | Mensual |

Para agregar más series, edita `config/series_catalog.yaml`.

---

## Scheduler automático

El scheduler ejecuta fetches según la frecuencia de cada serie:

| Job | Trigger | Series |
|---|---|---|
| `daily_fetch` | Lun–Vie a las 08:00 | Tipo de cambio diario |
| `monthly_fetch` | Día 6 de cada mes, 09:00 | IPC, TPM, desempleo, etc. |
| `quarterly_fetch` | Día 10 (ene/abr/jul/oct), 09:30 | PIB, balanza de pagos |
| `annual_fetch` | 15 de febrero, 10:00 | Balance fiscal |

Para correrlo como servicio en Windows, puedes usar el Programador de Tareas o `nssm`.

---

## Base de datos

Se usa **DuckDB** (`data/finanzas_chile.duckdb`), ideal para análisis OLAP local.

Puedes consultarla directamente:

```python
import duckdb
conn = duckdb.connect("data/finanzas_chile.duckdb")

# Últimos 12 meses de IPC
df = conn.execute("""
    SELECT date, value 
    FROM observations o
    JOIN series s ON o.series_id = s.id
    WHERE s.name LIKE '%IPC%' AND o.date >= date '2024-01-01'
    ORDER BY date
""").fetchdf()
print(df)
```

---

## Roadmap

Ver el [plan de implementación completo](../../../.gemini/antigravity/brain/ef9a714e-9bae-4b9d-a70b-168ff49a8ea6/implementation_plan.md).

**Próxima fase (2)**: Indicadores CMF — UF, UTM, IVP, y datos del sector real vía scraping del portal CMF.
