# CLAUDE.md — Guía del proyecto INF_FIN_IA

Guía para entender, retomar y trabajar en el proyecto (para humanos y asistentes de IA).
El README es la documentación de usuario; este archivo es la guía de desarrollo.

## Qué es

Repositorio de datos financieros y macroeconómicos de Chile en Python, con BD local
**DuckDB**, ingesta automática (catch-up + APScheduler), **CLI** (Click + Rich), una
**capa web** (FastAPI + Jinja2 + HTMX + Plotly, sin Node) y una **capa analítica** de
proyecciones de EEFF (`models/`). ~3,06M filas, ~168 MB.

## Cómo correr (importante)

`python`/`py` apuntan al stub de Microsoft Store. **Usar siempre el intérprete del venv**:

```powershell
.\.venv\Scripts\python.exe main.py <comando>
```

Comandos clave: `status`, `fetch --all`, `catchup [--dry-run]`, `serve [--with-scheduler]`,
`web-preview`, `eeff-export [-p YYYYMM]`, `query-cmf`, `query-banks`, `query-sp-cuotas`,
`fetch-sp-cartera -p YYYYMM`. Backtest de modelos: `python -m models.backtest`.
Para shell scripts en este repo se usa el Bash tool con heredoc (evita problemas de quoting).
Nota de encoding: al correr scripts que imprimen Δ/acentos, anteponer `PYTHONIOENCODING=utf-8`
(la consola de Windows usa cp1252 y revienta con caracteres Unicode).

## Arquitectura

```
Fuentes (BCCh BDE API · CMF XBRL plano · CMF/SBIF API · SP scraping)
  → collectors/ (descarga + parseo, con caché en data/*_raw/)
  → processors/normalizer (limpieza macro)
  → db/database.Database (DuckDB; upserts idempotentes)
  → CLI (main.py)  +  Web (api/)  +  models/ (proyecciones)  +  scheduler/ (catch-up + jobs)
```

## Fuentes y tablas (db/schema.py)

| Tabla | Fuente | Notas |
|---|---|---|
| `observations` (+ `series`) | BCCh | series macro; **1975–hoy**; incluye expectativas `F089` (EEE+EOF, Fase 6) |
| `cmf_financial_statements` | CMF (archivo plano `.txt` trimestral) | EEFF corporativos; ~1,5M filas, 45 períodos |
| `cmf_bank_statements` | CMF/SBIFv3 (API JSON) | balances/resultados con desglose por moneda; ~857k filas |
| `sp_quota_values` | SP (scraping) | valor cuota + patrimonio diario por AFP/fondo |
| `sp_instrument_prices` | SP | cinta de precios diaria |
| `sp_portfolio_holdings` | SP (XML) | cartera mensual; backfill 2015-01→2026-01 (133 meses); cols `row_order`/`section` |
| `fetch_log` | — | bitácora de ingestas (todas las fuentes vía catch-up) |

## Dónde está cada cosa

- `collectors/`: `bcentral.py`, `cmf.py` (plano), `cmf_banks.py` (SBIF), `sp_pensions.py`.
- `db/database.py`: TODA la lógica SQL. Métodos `query_*`, `get_*`, `insert_*`/`upsert_*`.
- `scheduler/freshness.py`: sondas "qué falta por publicar". `scheduler/jobs.py`: `run_catchup` + jobs.
- `api/`: `main.py` (app + lifespan con scheduler embebido opcional), `routers/` (macro, cmf,
  banks, sp, dashboard_kpi, **views** = páginas HTML), `templates/` + `partials/`, `static/app.css`,
  `eeff_format.py` (organización de EEFF), `preview.py` (export estático).
- `models/`: capa de proyecciones (Fase 6). `macro_path.py` (senda de factores anclada a
  encuestas EEE+EOF, con vintage `as_of`), `structural.py` (activos→rotación→margen),
  `forecast.py` (SARIMAX), `hybrid.py` (modelo de producción + bandas empíricas), `backtest.py`
  (arnés rolling-origin). Las proyecciones se sirven en `/proyecciones` vía `hybrid`.
- `config/`: `settings.py` (pydantic-settings + `.env`), `series_catalog.yaml` (series BCCh +
  expectativas), `company_profiles.yaml` (reseñas/sectores de empresas).

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
- **Cartera SP, clasificar por `section` NO por código**: el XML repite el mismo código de
  instrumento en secciones distintas (`CFID(6)` está en fondos mutuos nacionales Y en
  extranjero). El parser preserva `<fila numero>` como `row_order` y asigna `section` (el
  totalizador `TOTAL …` que cierra el bloque). `get_foreign_portfolio` filtra
  `section = 'TOTAL EXTRANJERO'`. Nunca usar listas de códigos de instrumento.
- **Modelos: el macro NO se proyecta**. La senda futura se ancla a las medianas de las
  encuestas EEE+EOF del BCCh (solo se interpola entre horizontes publicados). No introducir
  modelos que proyecten el macro propio. Y el macro **solo entra en la ecuación de activos**
  del modelo estructural — usarlo como exógena de flujos trimestrales EMPEORA el backtest.
- **Series EEE/EOF (`F089`)**: `F089.TPM.TAS.13` y `F089.EOF.TC.7MA` están descontinuadas;
  `F089.TCN.V12.LP` es una variación (%) no un nivel. Ver `Backtest-Modelos.md` y el catálogo.

## Cómo hacer tareas comunes

- **Agregar una serie BCCh**: editar `config/series_catalog.yaml` y `fetch --id <code> --from-date`.
- **Nueva vista web**: ruta en `api/routers/views.py` + plantilla en `api/templates/` (extiende
  `base.html`); patrón HTMX = `hx-get` a un endpoint que devuelve un fragmento `partials/`.
- **Nuevo dato del panel**: método en `db/database.py` → incluir en `get_overview_kpis` → render
  en `partials/kpi_cards.html`.
- **Regenerar estáticos**: `web-preview` (panel) y `eeff-export` (EEFF) → carpeta `preview/` (gitignored).

## Análisis y proyecciones (Fase 6)

- **Ratios financieros**: `db.database.compute_ratios(df)` (función módulo, sin conexión) →
  ROE, ROA, márgenes neto/bruto, liquidez corriente, deuda/patrimonio. Son "del período"
  (income acumulado en el año para trimestres). Se muestran en `/eeff` (tarjeta) y en
  `/comparar` (tabla comparativa entre empresas). El export estático también los incluye.
- **Proyecciones de EEFF** (`models/`, servidas en `/proyecciones`): modelo **híbrido** de
  producción (`hybrid.forecast_company_hybrid`) — SARIMAX puro para el 1er trimestre y modelo
  **estructural** (activos operacionales ← macro → rotación → margen) del 2° en adelante.
  Bandas de confianza **empíricas** = cuantiles del error del backtest (`hybrid.BAND_QUANTILES`;
  regenerar si cambian specs/anclajes). Toda decisión de diseño está arbitrada por el arnés
  `models/backtest.py` (rolling-origin con vintages honestos de encuestas). **Metodología y
  resultados en `docs/wiki/Backtest-Modelos.md`** (leer antes de tocar los modelos).

## Estado y roadmap

Fases 1–4 + SP operativas; catch-up (Fase 5 parcial) y capa web (Fase 7) muy avanzadas;
**Fase 6 operativa** (ratios + comparación sectorial + radar de salud + ranking sectorial +
**proyecciones de EEFF en producción**). Datos: ~3,06M filas; CMF con 45 períodos
(201503→202603, 10 cierres anuales); cartera SP con 133 meses. Vistas: panel, EEFF,
evolución, comparar, ranking, salud, **proyecciones**, banca, AFP. Reseñas de empresas en
`config/company_profiles.yaml` + `api/company_profiles.py` (inferencia por tipo). Lanzador de
un clic: `iniciar_web.bat`.

**El plan vivo y las decisiones pendientes están en `docs/wiki/Hoja-de-Ruta.md`** (mantenerlo
al día). El backtest y las decisiones de modelado están en `docs/wiki/Backtest-Modelos.md`.
Convenciones EEFF: solo se muestran los estados estándar **ESF C/NC, ERFG, ERI, EFMD**
(se descartan ESF OL, ERNG, EFMI). Siguiente: ampliar proyecciones (márgenes intermedios,
rezagos post-M&A) y Fase 8 storytelling con LLM. Trabajo en rama `feature/web-api-skeleton`.
