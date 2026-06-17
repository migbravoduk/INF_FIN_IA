# CLAUDE.md — Guía del proyecto INF_FIN_IA

Guía para entender, retomar y trabajar en el proyecto (para humanos y asistentes de IA).
El README es la documentación de usuario; este archivo es la guía de desarrollo.

## Qué es

Repositorio de datos financieros y macroeconómicos de Chile en Python, con BD local
**DuckDB**, ingesta automática (catch-up + APScheduler), **CLI** (Click + Rich) y una
**capa web** (FastAPI + Jinja2 + HTMX + Plotly, sin Node). ~626k filas, ~52 MB.

## Cómo correr (importante)

`python`/`py` apuntan al stub de Microsoft Store. **Usar siempre el intérprete del venv**:

```powershell
.\.venv\Scripts\python.exe main.py <comando>
```

Comandos clave: `status`, `fetch --all`, `catchup [--dry-run]`, `serve [--with-scheduler]`,
`web-preview`, `eeff-export [-p YYYYMM]`, `query-cmf`, `query-banks`, `query-sp-cuotas`.
Para shell scripts en este repo se usa el Bash tool con heredoc (evita problemas de quoting).

## Arquitectura

```
Fuentes (BCCh BDE API · CMF XBRL plano · CMF/SBIF API · SP scraping)
  → collectors/ (descarga + parseo, con caché en data/*_raw/)
  → processors/normalizer (limpieza macro)
  → db/database.Database (DuckDB; upserts idempotentes)
  → CLI (main.py)  +  Web (api/)  +  scheduler/ (catch-up + jobs APScheduler)
```

## Fuentes y tablas (db/schema.py)

| Tabla | Fuente | Notas |
|---|---|---|
| `observations` (+ `series`) | BCCh | series macro; **1975–hoy** tras backfill completo |
| `cmf_financial_statements` | CMF (archivo plano `.txt` trimestral) | EEFF corporativos; ~209k filas |
| `cmf_bank_statements` | CMF/SBIFv3 (API JSON) | balances/resultados con desglose por moneda |
| `sp_quota_values` | SP (scraping) | valor cuota + patrimonio diario por AFP/fondo |
| `sp_instrument_prices` | SP | cinta de precios diaria |
| `sp_portfolio_holdings` | SP (XML) | cartera mensual (hoy solo 1 período: 2026-01) |
| `fetch_log` | — | bitácora de ingestas (todas las fuentes vía catch-up) |

## Dónde está cada cosa

- `collectors/`: `bcentral.py`, `cmf.py` (plano), `cmf_banks.py` (SBIF), `sp_pensions.py`.
- `db/database.py`: TODA la lógica SQL. Métodos `query_*`, `get_*`, `insert_*`/`upsert_*`.
- `scheduler/freshness.py`: sondas "qué falta por publicar". `scheduler/jobs.py`: `run_catchup` + jobs.
- `api/`: `main.py` (app + lifespan con scheduler embebido opcional), `routers/` (macro, cmf,
  banks, sp, dashboard_kpi, **views** = páginas HTML), `templates/` + `partials/`, `static/app.css`,
  `eeff_format.py` (organización de EEFF), `preview.py` (export estático).
- `config/`: `settings.py` (pydantic-settings + `.env`), `series_catalog.yaml` (series BCCh).

## Gotchas / decisiones (LEER antes de tocar datos)

- **DuckDB = un solo proceso escritor.** La API abre `read_only` salvo en modo embebido
  (`serve --with-scheduler`, donde comparte proceso). No correr `run-scheduler` aparte mientras
  la web sirve. Los backfills largos toman la BD: hacerlos en background y no leer en paralelo.
- **Parser decimal BCCh**: la API BDE entrega `.` como separador DECIMAL. El bug original lo
  trataba como miles y corrompía todo (`bcentral._parse_value`, ya corregido). Si aparecen
  valores raros en BCCh, **cruzar contra el archivo/fuente antes de asumir bug**.
- **Ventana de fetch por defecto = 5 años.** `fetch --all`/catch-up solo cubren reciente. Para
  historia completa hay que pasar `from_date` explícito (se hizo backfill desde 1975).
- **EEFF, orden IFRS**: `query_cmf_statements` ordena por `id` (= orden del archivo plano CMF =
  orden oficial IFRS), NO alfabético. No cambiar a `ORDER BY account_name`.
- **EEFF, cuentas repetidas (XBRL)**: el archivo plano repite nombres bajo contextos que no
  quedan en columnas (`taxonomy_code` siempre 'TAX CI'). `api/eeff_format.build_statement_groups`
  colapsa duplicados exactos, numera los repetidos con valores distintos, mapea códigos de estado
  a nombres legibles y los ordena lógicamente. Es la función compartida por `/eeff` y el export.
- **Unidades**: CMF en la moneda de reporte de cada empresa (USD para ENEL/CODELCO/COPEC, CLP
  resto), NO en miles. Cobre BCCh en **US$/libra** (no centavos).
- **Datos validados**: la extracción CMF/EEFF es fiel (counts e identidad contable Activos =
  Pasivos + Patrimonio cuadran). Valores grandes en USD son reales.

## Cómo hacer tareas comunes

- **Agregar una serie BCCh**: editar `config/series_catalog.yaml` y `fetch --id <code> --from-date`.
- **Nueva vista web**: ruta en `api/routers/views.py` + plantilla en `api/templates/` (extiende
  `base.html`); patrón HTMX = `hx-get` a un endpoint que devuelve un fragmento `partials/`.
- **Nuevo dato del panel**: método en `db/database.py` → incluir en `get_overview_kpis` → render
  en `partials/kpi_cards.html`.
- **Regenerar estáticos**: `web-preview` (panel) y `eeff-export` (EEFF) → carpeta `preview/` (gitignored).

## Estado y roadmap

Fases 1–4 + SP operativas; catch-up (Fase 5 parcial) y capa web (Fase 7) muy avanzadas.
**Siguiente: Fase 6** — análisis (proyecciones macro ARIMA/VAR, ratios, anomalías) y Fase 8
storytelling con LLM (Anthropic SDK con prompt caching). Trabajo en rama `feature/web-api-skeleton`.
