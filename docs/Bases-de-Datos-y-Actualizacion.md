# Bases de datos y actualización incremental — INF_FIN_IA

Referencia para planificar/mantener el actualizador de datos. Todo vive en **una sola base
DuckDB**: `data/finanzas_chile.duckdb` (~239 MB, ~3,06M filas). El esquema está en
`db/schema.py` y toda la lógica SQL en `db/database.py`.

> **Punto clave:** la actualización incremental **ya está construida** (no hay que reinventarla).
> Ver la sección "Cómo se actualiza hoy". Cada fuente tiene una *sonda de frescura* que calcula
> "qué me falta" y un *catch-up* idempotente que descarga solo eso.

---

## 1. Tablas, fuentes y frecuencias

| Tabla | Fuente | Granularidad | Frecuencia | Rezago de publicación | Clave de upsert (idempotencia) |
|---|---|---|---|---|---|
| `observations` (+ `series`) | BCCh (API BDE) | por serie × fecha | **mixta**: 17 diarias, 31 mensuales, 3 trimestrales | diaria: 1d · mensual: ~8d · trimestral: ~85d (override `publish_lag_days` por serie) | `(series_id, date)` |
| `cmf_financial_statements` | CMF — archivo plano `.txt` XBRL | empresa × período × cuenta | **trimestral** | ~60 días tras cierre | por `period` (se reinserta el período) |
| `cmf_bank_statements` | CMF / SBIFv3 (API JSON) | banco × período × cuenta × moneda | **mensual** | ~35 días | `(year, month, bank_code, report_type)` |
| `sp_quota_values` | SP (scraping) | AFP × fondo × día | **diaria** | 1 día hábil | por año en curso (re-ingesta idempotente) |
| `sp_instrument_prices` | SP (cinta de precios) | instrumento × día | **diaria** | 1 día hábil | por fecha |
| `sp_portfolio_holdings` | SP (XML cartera) | AFP × fondo × instrumento × mes | **mensual** | ~40 días | por `period` (YYYY-MM) |
| `fetch_log` | — | bitácora | cada ingesta | — | append |

### Series diarias del BCCh (las que te importan para "frecuencia diaria")
- `F073.TCO.PRE.Z.D` — **Tipo de cambio CLP/USD (dólar observado)**
- `F073.UFF.PRE.Z.D` — **UF**
- `F073.IVP.PRE.Z.D` — IVP
- `F022.TPM.TIN.D001.NO.Z.D` — TPM
- (+ expectativas EOF diarias). Todas se agregan/editan en `config/series_catalog.yaml`.

### Notas de unidades/empalmes (ya documentadas, no re-derivar)
- **Bancos:** la CMF cambió el plan de cuentas en 2022 (códigos 7→9 dígitos; pre-2022 en
  **millones** de CLP, vigente en **pesos**). El empalme lo hace `get_bank_statement_evolution`.
- **EEFF empresas:** en la moneda de reporte de cada empresa (USD para ENEL/CODELCO/COPEC), no en miles.
- **Cartera SP:** clasificar nacional/extranjero por `section`, nunca por código de instrumento.

---

## 2. Cómo se actualiza hoy (infraestructura existente)

El diseño ya cumple tu requisito de **"solo incorporar el dato nuevo, no rehacer todo"**. Tres piezas:

### a) Sondas de frescura — `scheduler/freshness.py`
Solo lectura. Para cada fuente calcula:
- `latest_have` = último período/fecha que hay en la BD,
- `expected` = siguiente período que **ya debería estar publicado** (fin de período + rezago ≤ hoy),
- `due` = `True` si falta y ya está dentro de su ventana de publicación.

Los rezagos están en `LAG_DEFAULTS` (`daily=1, monthly=8, quarterly=85`) y los overrides
`LAG_CMF_EMPRESAS=60`, `LAG_CMF_BANCOS=35`, `LAG_SP_CARTERA=40`. `probe_all(db)` devuelve el
estado de **todas** las fuentes de una vez.

### b) Catch-up idempotente — `scheduler/jobs.py::run_catchup`
Consume `probe_all()`, filtra los `due` y despacha (`_dispatch_catchup`) reusando los
*collectors* + *inserts idempotentes*. Como las inserciones son idempotentes (claves de upsert
de la tabla de arriba), **correrlo de más es barato y no duplica**. Todo queda en `fetch_log`.
Para diarias hay un tope `MAX_DAILY_BACKFILL=10` (evita loops largos).

### c) Programación automática — `scheduler/jobs.py::create_scheduler` (APScheduler)
7 jobs: diario BCCh (L–V 08:00), mensual (día 6), trimestral (día 10 de ene/abr/jul/oct),
anual (15-feb), SP diario (L–V 18:30), SP mensual (día 15) y un **catch-up horario** (L–V 8–20h)
que actúa de red de seguridad. Se arranca embebido con `main.py serve --with-scheduler`.

### d) App de actualización — `actualizar.py` (→ `Actualizar_Fuentes.exe`)
**Ya existe una app interactiva** (TUI Rich, compilada a .exe con `Actualizar_Fuentes.spec`) que:
muestra la tabla de frescura (último en BD / siguiente esperado / al día vs. pendiente),
y ejecuta el catch-up por fuente o completo. Es exactamente el aplicativo que describes.

---

## 3. Recomendaciones si vas a construir/ampliar el aplicativo

1. **No dupliques la lógica de frescura.** Construye la UI sobre `probe_all()` + `_dispatch_catchup()`.
   Ya resuelven "qué falta" y "descargar solo eso" por frecuencia.
2. **Respeta el escritor único de DuckDB.** Un solo proceso puede escribir. Si la web corre con
   `--with-scheduler`, no lances otro actualizador en paralelo (tomaría el lock). Para diagnóstico
   usa `read_only=True` (como hace `run_catchup(dry_run=True)`).
3. **Frecuencias → cadencia de chequeo:** diarias conviene sondearlas 1×/día hábil; mensuales
   alrededor del rezago (bancos ~día 35, cartera SP ~día 40); trimestrales (EEFF empresas) ~día 60.
   El catch-up horario ya se auto-restringe por ventana, así que "correr de más" no cuesta.
4. **Idempotencia = tu red de seguridad.** Si dudas, re-corre; las claves de upsert impiden duplicar.
5. **Bitácora:** usa `fetch_log` para el panel de estado (última corrida, nuevos, errores).
