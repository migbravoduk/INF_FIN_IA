# CONTEXTO.md — Guía del proyecto INF_FIN_IA

Guía para entender, retomar y trabajar en el proyecto (para humanos y asistentes de IA).
El README es la documentación de usuario; este archivo es la guía de desarrollo.

## Qué es

Repositorio de datos financieros y macroeconómicos de Chile en Python, con BD local
**DuckDB**, ingesta automática (catch-up + APScheduler), **CLI** (Click + Rich), una
**capa web** (FastAPI + Jinja2 + HTMX + Plotly, sin Node) y una **capa analítica** de
proyecciones de EEFF (`models/`). ~12,9M filas, ~700 MB (el grueso es el detalle de renta
fija de la cartera de seguros: 7,9M filas por sí solo).

## Cómo correr (importante)

`python`/`py` apuntan al stub de Microsoft Store. **Usar siempre el intérprete del venv**:

```powershell
.\.venv\Scripts\python.exe main.py <comando>
```

Comandos clave: `status`, `fetch --all`, `catchup [--dry-run] [--source KIND]`, `validate`,
`build-productos`, `clean-cache [--dry-run]`, `serve [--with-scheduler]`,
`web-preview`, `eeff-export [-p YYYYMM]`, `query-cmf`, `query-banks`, `query-sp-cuotas`,
`fetch-sp-cartera -p YYYYMM`, `fetch-seguros`, `fetch-intermediarios`,
`fetch-cartera-seguros [--latest | --period YYYYMM | --history --since YYYYMM]`.
Backtest de modelos: `python -m models.backtest`. Tests: `.\.venv\Scripts\python.exe -m pytest -q`
(los que necesitan la base se saltan solos si no existe o la tiene tomada un escritor).
Actualización de datos: `actualizar.py` (menú por fuente + `--todo`/`--catchup`/`--source`/
`--cartera-seguros`), que es lo que empaqueta `Actualizar_Datos.exe`. **Para agregar una fuente
se toca UN lugar**: una entrada `Source(kind, titulo, source_id, probe, fetch, refresh)` en
`scheduler/sources.py`, más su modo manual en `actualizar.MANUAL`. Sonda, dispatch y menú se
generan desde ese registro, y `tests/test_registry.py` falla si falta alguna pieza (antes
eran tres lugares y un nombre que no calzaba dejó el catch-up caído con un NameError).
Para shell scripts en este repo se usa el Bash tool con heredoc (evita problemas de quoting).
Nota de encoding: al correr scripts que imprimen Δ/acentos, anteponer `PYTHONIOENCODING=utf-8`
(la consola de Windows usa cp1252 y revienta con caracteres Unicode).

## Arquitectura

```
Fuentes (BCCh BDE API · CMF XBRL plano · CMF/SBIF API · SP scraping)
  → collectors/ (descarga + parseo, con caché en data/*_raw/)
  → processors/normalizer (limpieza macro)
  → db/database.Database (DuckDB; upserts idempotentes; métodos por dominio en db/repos/)
  → capas derivadas: db/entities (dim_entidad) → db/products (dim_producto + métricas)
  → db/quality (controles, dq_results)
  → CLI (main.py)  +  Web (api/)  +  models/ (proyecciones)  +  scheduler/ (registro de fuentes + catch-up)
```

## Fuentes y tablas (db/schema.py)

| Tabla | Fuente | Notas |
|---|---|---|
| `observations` (+ `series`) | BCCh | series macro; **1975–hoy**; incluye expectativas `F089` (EEE+EOF, Fase 6) |
| `cmf_financial_statements` | CMF (archivo plano `.txt` trimestral) | EEFF corporativos; ~1,5M filas, 45 períodos |
| `cmf_bank_statements` | CMF/SBIFv3 (API JSON) | balances/resultados con desglose por moneda; ~1,8M filas, 2019+ (plan de cuentas cambió en 2022, ver gotchas) |
| `cmf_insurer_statements` | CMF (descarga Excel FECU seguros) | EEFF de seguros **vida + generales** (col `insurance_type`); plan de cuentas propio (Circulares 2022/2050); **trimestral** 2015Q1+; ~581K filas (vida 338K + generales 243K), 72 compañías; cifras en **miles de CLP** |
| `cmf_insurer_portfolio_control` | CMF Circular 1.835 (ZIP **mensual**, descarga automática) | Cartera de inversiones: totales por tipo de inversión y compañía (archivo B.8 Control). **Vida + generales, 2020-01→2026-06 completo** (78 meses, sin huecos); ~72K filas, 75 compañías; miles de CLP. Es la base de `/seguros/cartera` |
| `cmf_insurer_portfolio_fixed_income` | CMF Circular 1.835 (archivo B.1) | Detalle instrumento a instrumento de renta fija: emisor, tipo, nemotécnico/ISIN, país, valor nominal (campos **reconciliados** contra los datos; valoración de mercado pendiente). **~7,9M filas** — la tabla más grande del repo |
| `cmf_broker_statements` | CMF (descarga Excel FECU IFRS intermediarios) | EEFF de **corredores de bolsa + agentes de valores** (col `broker_type`); plan de cuentas propio (códigos `11.01.00`), 14 secciones en `section`; **trimestral** 2015Q1+; ~210K filas, 56 entidades; cifras en **miles de CLP** |
| `sp_quota_values` | SP (scraping) | valor cuota + patrimonio diario por AFP/fondo |
| `sp_instrument_prices` | SP | cinta de precios diaria |
| `sp_portfolio_holdings` | SP (XML) | cartera mensual; backfill 2015-01→2026-01 (133 meses); cols `row_order`/`section` |
| `cmf_mutual_fund_portfolio` | CMF (`ffm_download.php`, mensual) | Cartera de **fondos mutuos** por instrumento. 33.665 filas, 440 fondos, período 2026-06. `raw_data` JSON con los campos `FFM_*`, **cuya numeración cambia según el tipo de cartera** (ver gotchas). Sin llave natural: idempotencia por Delete-then-Insert |
| `cmf_mutual_fund_quota_values` | CMF (`fm.fm_bpr_dia.php`) | Valor cuota, patrimonio (en MILLONES) y partícipes por fondo/serie, más **administradora y tipo de fondo**. La fuente es diaria pero se guarda **un corte por mes** (ver gotchas): 95K filas en 32 cortes, 2024-01→2026-08, 615 fondos. `fund_run` SIN dígito verificador. Idempotente por `date` |
| `cmf_mutual_fund_types` | CMF (pie del archivo diario) | Glosa oficial de los 8 tipos de fondo |
| `cmf_mutual_fund_returns` | CMF · Comparador de rentabilidad (xlsx) | Rentabilidades **oficiales** por fondo y serie: nominal y real a YTD/1/3/5/10 años (3+ **anualizadas**), indicador ajustado por riesgo, familia y clase de inversionista. Foto por corte (`fecha`), no serie histórica. 1.981 series, corte 2026-03-31; cubre 1.922 de 2.861 pares |
| `cmf_mutual_fund_fees` | CMF (`fm.fm_comision.php`, mensual) | Estructura de costos por fondo y serie: comisión de colocación, remuneración anual y gasto operacional. Valores en **fracción** (0,0119 = 1,19%), con IVA. 2.372 filas, cruzan 2.285 con la estadística |
| `cmf_mutual_fund_agf` | CMF · Comparador (hoja de benchmark por AGF) | 22 administradoras con **RUT**. Única fuente del RUT: la estadística diaria solo trae la razón social |
| `fetch_log` | — | bitácora de ingestas (todas las fuentes vía catch-up) |
| `schema_migrations` | — | migraciones aplicadas (`db/migrations.py`) |
| `dim_dataset` | — | metadatos por tabla: fuente, unidad, moneda, frecuencia, llave. Alimenta `/metodologia` |
| `dim_entidad` (+ `dim_entidad_alias`) | derivada | maestra por RUT de toda entidad que aparece en alguna fuente, con su tipo y razones sociales históricas. Bancos por `bank_code` vía `config/entidades.yaml` |
| `dim_producto` | derivada | una fila por serie de fondo mutuo y por fondo AFP, con proveedor, categoría y clase de inversionista |
| `fact_producto_metricas` | derivada | rentabilidad, riesgo, costo, flujos y posición en la categoría por producto y corte |
| `proveedor_canal` | `config/proveedores.yaml` | URL verificada de cada proveedor (vacía hasta curarla) |
| `dq_results` | `db/quality.py` | resultado de cada corrida de controles de calidad |
| vistas `v_*` | — | convenciones homogéneas: `period` INTEGER AAAAMM, fechas DATE, bancos en pesos (`v_bank_statements.val_total_clp`), cartera FFMM tipada (`v_mutual_fund_holdings`). **Lectores nuevos: usar las vistas** |

## Dónde está cada cosa

- `collectors/`: `bcentral.py`, `cmf.py` (plano), `cmf_banks.py` (SBIF), `cmf_fecu.py` (**parser
  compartido** de los Excel "FECU tabla"), `cmf_insurers.py` (seguros vida/generales),
  `cmf_brokers.py` (corredores + agentes), `sp_pensions.py`.
- `db/database.py`: fachada `Database` (conexión + esquema + migraciones) que hereda los mixins
  por dominio de `db/repos/` (`macro`, `panel`, `eeff`, `bancos`, `fecu`, `sp`, `ffmm`). Las
  utilidades sin conexión (`compute_ratios`, criterio AGF) están en `db/common.py` y se
  re-exportan desde `db.database`. Capas derivadas: `db/entities.py`, `db/products.py`,
  `db/curves.py`, `db/cross.py`; controles en `db/quality.py`; migraciones en `db/migrations.py`.
- `scheduler/sources.py`: registro único de fuentes (sonda + descarga + reingesta).
  `scheduler/freshness.py`: `FreshnessStatus`, utilidades de fechas y `probe_all`.
  `scheduler/jobs.py`: `run_catchup` (descarga → reconstruye derivadas → valida) y el scheduler
  (catch-up horario + reingesta mensual de los 2 últimos períodos).
- `api/`: `main.py` (app + lifespan con scheduler embebido opcional), `routers/` (REST: macro,
  cmf, banks, sp, dashboard_kpi; HTML: `views.py` agrega `routers/web/*` por dominio y
  `productos.py`), `templates/` + `partials/`, `static/app.css` + `static/vendor/` (HTMX y Plotly
  locales), `freshness_banner.py` ("datos al …" en cada vista), `eeff_format.py`, `preview.py`
  (export estático: se mantiene pero no se amplía).
- `models/`: capa de proyecciones (Fase 6). `macro_path.py` (senda de factores anclada a
  encuestas EEE+EOF, con vintage `as_of`), `structural.py` (activos→rotación→margen),
  `forecast.py` (SARIMAX), `hybrid.py` (modelo de producción + bandas empíricas), `backtest.py`
  (arnés rolling-origin). Las proyecciones se sirven en `/proyecciones` vía `hybrid`.
- `config/`: `settings.py` (pydantic-settings + `.env`), `series_catalog.yaml` (series BCCh +
  expectativas), `company_profiles.yaml` (reseñas/sectores de empresas).

## Gotchas / decisiones (LEER antes de tocar datos)

- **Curvas de tasas (`db/curves.py`, `/curvas`).** Cuatro curvas diarias desde 2005 (swap
  pesos, swap UF, bonos pesos, bonos UF) ajustadas con Nelson-Siegel(-Svensson); `curva_ajuste`
  guarda SOLO parámetros (se reescribe completa en ~1 s dentro de `rebuild_all`). Decisiones
  medidas, no cambiar sin volver a validar contra el BCCh (`F022.SWSP.*`):
  - Swaps cámara ≥ 1 año son base **actual/360** (`a360`): así la compensación inflacionaria a
    5/10 años queda a ~2 pb del BCCh (base 365 daba 4–5 pb). A 1 año hay ~10 pb (hasta 50 pb con
    inflación alta) por el rezago de la UF, que no se corrige.
  - Tasas par ajustadas como cero cupón (aproximación declarada en /metodologia).
  - Puntos sin dato valen hasta 5 días (`ffill(limit=5)`); con 3 puntos se usa NS con τ fijo en
    la mediana de la curva (`ns_tau_fijo`, sin RMSE). Las consultas no muestran ajustes con más
    de 10 días de antigüedad: en 2022–2023 no hay BCP a 2 años y la curva de bonos en pesos
    simplemente no aparece.
  - BUF 20/30 años transan esporádicamente (`publish_lag_days` 120/30 en el catálogo).
- **Duración efectiva** (`fact_producto_metricas.duracion_*`): regresión del retorno mensual
  sobre inflación UF + desplazamiento paralelo de la curva swap (`factor_tasas_mensual`), solo
  fondos en PESOS de deuda y fondos D/E. En fondos en dólares el tipo de cambio da "duraciones"
  de −4: por eso se excluyen, y se descartan estimaciones significativamente negativas.

- **DuckDB = un solo proceso escritor.** La API abre `read_only` salvo en modo embebido
  (`serve --with-scheduler`, donde comparte proceso). No correr `run-scheduler` aparte mientras
  la web sirve. Los backfills largos toman la BD: hacerlos en background y no leer en paralelo.
- **No poner índices secundarios en las tablas de cartera de seguros.** En DuckDB 1.5.1 un
  índice ART sobre una columna con claves muy duplicadas (p. ej. `period`, donde ~100K filas
  comparten valor) hace **fallar el DELETE** del patrón Delete-then-Insert de la reingesta por
  período —`Invalid Input Error: Failed to delete all rows from index`— y, peor, **invalida la
  base para todo el proceso** ("database has been invalidated because of a previous fatal
  error"). El síntoma engaña: parece corrupción de datos y no lo es; basta `DROP INDEX` y la
  BD queda sana. DuckDB es columnar con zonemaps, así que el filtrado por `period`/`rut` no
  necesita esos índices. Si se agrega otra tabla con reingesta masiva por período, misma regla.
- **Parser decimal BCCh**: la API BDE entrega `.` como separador DECIMAL. El bug original lo
  trataba como miles y corrompía todo (`bcentral._parse_value`, ya corregido). Si aparecen
  valores raros en BCCh, **cruzar contra el archivo/fuente antes de asumir bug**.
- **Ventana de fetch por defecto = 5 años.** `fetch --all`/catch-up solo cubren reciente. Para
  historia completa hay que pasar `from_date` explícito (se hizo backfill desde 1975).
- **EEFF, orden IFRS**: `query_cmf_statements` ordena por `id` (= orden del archivo plano CMF =
  orden oficial IFRS), NO alfabético. No cambiar a `ORDER BY account_name`.
- **`/proyecciones` también es un HUB** (`PROY_CLASSES`), con DOS motores según clasificación:
  corporativos y AGF usan el **híbrido** (macro EEE/EOF + estructural + bandas empíricas
  calibradas por backtest); bancos, seguros e intermediarios usan **SARIMAX univariante**
  (`models/simple_forecast.py`). El híbrido NO se reusa fuera de los IFRS corporativos a
  propósito: su cadena activos→rotación→margen no aplica a entidades financieras y sus
  `BAND_QUANTILES` se midieron sobre 40 empresas corporativas, así que las bandas serían
  inválidas. Las vistas SARIMAX lo declaran en pantalla ("bandas no calibradas"). Para
  calibrarlas hay que adaptar `models/backtest.py` a cada clasificación.
- **Specs SARIMAX por tipo de serie (no tocar sin leer esto)**: los STOCK (Total activos) van
  `(1,1,0)` **con deriva y SIN estacionalidad**; los FLUJO (resultado) van `(1,0,0)(0,1,1,s)`
  y se **desacumulan** antes (la CMF publica los estados de resultado acumulados en el año).
  Sin `d=1` el stock diverge a valores negativos; y con estacionalidad un cambio de nivel de
  una sola vez se replica cada año — comprobado con la fusión BICE-Security de nov-2025, que
  hacía proyectar un salto fantasma 12 meses después. Bancos usan la serie TOTAL empalmada
  (`get_bank_total_series`), no el desglose por moneda.
- **`/eeff` es un HUB, no una vista**: muestra el menú de clasificaciones (`EEFF_CLASSES`) y cada
  una lleva a su vista: `/eeff/corporativos`, `/eeff/agf` (ambas reusan `eeff.html` vía
  `_EEFF_SCOPES`, que solo cambia el filtro AGF y los textos), `/eeff/seguros`,
  `/eeff/intermediarios` (comparten `fecu_entidad.html`) y `/banca` (**mantiene su lógica propia**
  con desglose por moneda). El buscador `/eeff/search` recibe `scope` para acotar el universo.
- **`/inversion-institucional` es otro HUB** (`INST_CLASSES`) que agrupa a los administradores
  de activos: `/seguros/cartera` (nuevo), `/afp` y `/fondos`. Reemplazó a AFP y Fondos en la
  barra de navegación; **las rutas antiguas siguen vivas**, solo se llega a ellas por el hub.
- **`/seguros/cartera` = asset allocation de seguros** (Circular 1.835, archivo B.8). Cuatro
  bloques, cada uno un fragmento HTMX: panel del período, deriva histórica, comparación entre
  compañías y concentración por emisor. Reglas que NO hay que romper al tocarla:
  - Las **glosas de los códigos son oficiales** de la CMF y se transcribieron desde
    `https://www.cmfchile.cl/sitio/seil/certificacion_cir1835_tinver.php` (los Anexos Técnicos
    NO traen esa tabla: remiten a "Codificación CMF, Tipo de Inversión"). Viven en
    `config/insurer_investment_codes.yaml`; **no inventar glosas**. La agrupación `clase` +
    `geo` sí es propia y es el único lugar donde reagrupar la cartera.
  - **El mix de valorización se calcula sobre lo CLASIFICADO, no sobre el total**: créditos,
    siniestros por cobrar y avances a tenedores no llevan método de valorización, así que
    `costo_amortizado + valor_razonable + efectivo_equiv + otras_clasif` ≈ 86% del total (en
    cambio `valor_final = repr_rt_pr + no_repr_rt_pr` sí es identidad exacta, verificada).
  - **Los derivados pasivos se informan con signo NEGATIVO** (lo exige la circular), así que
    una clase puede pesar menos de 0% y el gráfico de evolución no puede fijar el eje en
    `[0,100]`. La composición igual suma 100% exacto en cada período.
  - **El nominal de renta fija (B.1) NO es sumable entre monedas** (viene en UF, $, EUR…) ni es
    valor de mercado, así que la vista de emisores lo muestra abierto por `unidad_monetaria` y
    ordena por número de posiciones. Ahí no hay vencimiento ni valorización: los campos
    reconciliados llegan hasta la unidad monetaria.
  - **En la tabla de emisores, que falte la razón social NO es un hueco de datos.** B.1
    identifica al emisor solo por RUT y el nombre se resuelve contra los RUT conocidos en las
    tablas de EEFF (cobertura medida vida 202606: **183 de 321** emisores). Los que encabezan
    el ranking son **agentes administradores de mutuos hipotecarios y originadores de leasing**,
    que no son emisores inscritos y por eso no están en ninguna de esas tablas. El anexo de la
    circular además fija que el nemotécnico se mantiene "independiente de las fusiones, compra
    o cambio de nombre de las compañías": la identidad de esos instrumentos es un código, no
    una razón social. Por eso la vista muestra `tipo_principal` junto al RUT — sin esa columna
    la fila es ilegible. **No intentar "completar" esos nombres por parecido**: no hay fuente.
    Ojo también con `cmf_bank_statements`, que **no tiene columna RUT** (solo `bank_code`), así
    que los bonos bancarios tampoco se resuelven por esa vía.
  - **Glosas de `tipo_instrumento` (MHA/MHB/CLEAS/BE/BB/…): misma regla que las de tipo de
    inversión.** Solo están transcritas las tres que los Anexos Técnicos definen de forma
    explícita —MHA §2.1 (agente administrador), MHB §2.2 (emitido por banco), CLEAS §3
    (leasing sobre bienes raíces)—, que cubren ~81% de los instrumentos. Para el resto el anexo
    remite al módulo SEIL de codificación CMF, que no está transcrito, y el código se muestra
    crudo. Viven en `api/insurer_glossary._TIPO_INSTRUMENTO`; **no inventar las que faltan**.
  - **Cada bloque de `/seguros/cartera` lleva su propio selector de alcance** (mercado o una
    compañía) y su propio período. El de emisores no lo tenía y quedaba fijo en el agregado del
    mercado aunque arriba hubiera una compañía elegida — dos alcances distintos en la misma
    página, sin avisar. Al agregar un bloque nuevo, o le pones selector propio o dejas escrito
    en pantalla a qué corresponde.
  - Los nombres de compañía vienen sucios desde el archivo (código interno "033 METLIFE…", Ñ
    escrita como '#', campos en blanco). `insurer_glossary.clean_company_name` los normaliza
    **solo para mostrar**; el prefijo numérico se saca únicamente si empieza en cero, porque
    "4 LIFE SEGUROS DE VIDA" es una compañía real.
- **Fondos mutuos: la carga se fue a OTRA base y por eso la web no mostraba nada.** El ingest
  se hizo a mano y escribió en `db/inf_fin_ia.duckdb` en vez de `settings.DB_PATH`
  (`data/finanzas_chile.duckdb`), que es la que lee la API. Causa de fondo: no existía comando
  CLI. Ahora está `main.py fetch-ffmm --period AAAAMM`, que usa la conexión estándar. **Si
  aparece un `.duckdb` suelto en el repo, sospechar de esto antes que de los datos.**
- **Los valores cuota de fondos mutuos que había cargados eran DATOS DE PRUEBA.** 120 filas, 4
  fondos con RUN inventados ('7001-4', '8002-2') y cero solapamiento con los fondos reales. Se
  descartaron. **Ya resuelto**: la fuente real es `fm.fm_bpr_dia.php` (ver abajo).
- **Valor cuota de FFMM: `fm.fm_bpr_dia.php`, y solo sirve `out=excel`.** Un POST con
  `admins=0&tipofondo=0&moneda=0&dia_select&mes_peri&anio_peri&out=excel` devuelve TODOS los
  fondos del día (~2.860 filas fondo/serie, 443 fondos, 22 AGF). Probados `out=csv|html|txt`:
  devuelven 228 bytes de error, así que **no hay alternativa al Excel**. Es además la ÚNICA
  fuente que enlaza cada fondo con su **administradora** y su **tipo**: el archivo de cartera
  no los trae. Sirve historia al menos desde 2018 (verificado con cortes de 2018, 2020, 2023).
- **DECISIÓN: la estadística de FFMM se guarda por CORTE MENSUAL, no diaria.** La CMF publica
  todos los días hábiles, pero un año diario son ~700 mil filas contra ~34 mil del mismo año
  en cortes de fin de mes, y para leer rendimientos mes a mes el corte basta. `fetch-ffmm-cuotas`
  trabaja por meses (`--months`, `--from-month/--to-month`) y `--date` queda para un corte
  puntual fuera de calendario. **No basta con tomar el último día hábil**: los feriados chilenos
  (18-19 de septiembre, algunos 31 de diciembre) caen en día hábil y ese día no hay publicación,
  así que el colector retrocede hasta encontrar datos, con tope de 8 intentos.
- **Ese .xls es OLE2 legado y no declara codepage.** `openpyxl` no lo lee (es para .xlsx) y
  `xlrd` cae a cp1252 y revienta con el byte 0x81. Hay que abrirlo con
  `xlrd.open_workbook(..., encoding_override='latin-1')`. De ahí la dependencia `xlrd`.
- **El RUN viene CON dígito verificador en la estadística ('8011-K') y SIN él en la cartera
  ('8011').** Sin normalizar, las dos tablas no cruzan y el zoom al portafolio sale vacío. Se
  normaliza en `CMFMutualFundsCollector.normalizar_run` (se corta en el guion).
- **La glosa del tipo de fondo y la unidad del patrimonio vienen EN EL PROPIO ARCHIVO**, al pie:
  "(1) Cifras en millones de pesos o de la moneda que corresponda" y "(2) Tipo de fondo: 1: FM
  DE INV.EN INST.DE DEUDA...". Se extraen en la ingesta y se guardan en
  `cmf_mutual_fund_types`; **no se transcriben a mano**. Corolario: el patrimonio está en
  MILLONES y no es sumable entre fondos de distinta moneda.
- **En `/fondos-mutuos` el zoom se abre por TRES caminos** y los tres comparten endpoint:
  el selector, un clic en una fila de la tabla y un clic en una línea del gráfico. Los dos
  últimos pasan además `?series=`, porque tabla y gráfico trabajan con pares fondo-serie;
  sin ese parámetro el panel cae a la serie de mayor patrimonio, y el rótulo cambia según el
  caso (`serie_elegida`) para no decir "la de mayor patrimonio" cuando el usuario eligió otra.
  El clic del gráfico va por `htmx.ajax` para no duplicar en JS la lógica del endpoint, e
  **ignora la línea de la mediana**, que no es un fondo. El panel además sincroniza el
  selector con el fondo mostrado, para que la página no diga dos cosas a la vez.
- **El gráfico comparado de FFMM va en BASE 100 y lleva la mediana del grupo.** Los valores
  cuota parten de niveles arbitrarios (un fondo en 1.200 y otro en 45.000), así que sin
  rebasar al corte inicial no son comparables en un mismo eje. Y la **mediana se calcula
  sobre todo el universo filtrado, no sobre las seis líneas dibujadas**: es lo que permite
  leer "le fue mejor que a su categoría" en vez de solo "subió". Mismo criterio que la R12
  del proyecto hermano (comparar contra la mediana del sector). Verificado: mediana 120,9
  en 2 años 8 meses para el universo completo, 112,4 al filtrar deuda de corto plazo y
  103,3 en la ventana de 2026 — el orden de magnitud es coherente entre los tres.
- **La rentabilidad se calcula por FONDO Y SERIE, exigiendo que la serie exista en ambos
  cortes.** El valor cuota es propio de cada serie (distintas comisiones), así que promediarlas
  escondería la diferencia que paga el partícipe; y sin exigir presencia en los dos cortes, una
  serie abierta o cerrada dentro de la ventana produce una cifra falsa.
- **VALIDADO contra las cifras oficiales de la CMF.** Fuente: base del *Comparador de
  rentabilidad* (`portal/estadisticas/626/w4-article-91848.html`, hoja
  `rentabilidades_por_serie_fondo`, corte 2026-03-31) y su Documento Metodológico. Resultado
  sobre 1.968 pares fondo-serie: **mediana de la diferencia 0,000 pp y 93,4% dentro de 0,5 pp**.
  Dos correcciones salieron de esa validación y **no hay que deshacerlas**:
  1. *Conversión a pesos*: los fondos en dólares (298 pares) medían su retorno en dólares. La
     metodología §3.1 exige convertir a pesos. Sin eso, desviación de +2,8 pp de mediana.
  2. *Fecha del tipo de cambio*: hay que usar la observación **posterior** al corte, no la del
     mismo día — el dólar observado publicado el día D refleja las transacciones del día D-1.
     Con la fecha del mismo día quedaba un residuo constante de +1,20 pp en TODOS los fondos en
     dólares; la razón implícita despejada (0,973129) coincidió con el par 2025-04-01 →
     2026-04-01 hasta el sexto decimal. Con la corrección, los fondos en dólares pasaron de
     **0% a 100%** de coincidencia dentro de 0,5 pp.
- **RENTABILIDAD NETA: la remuneración YA está descontada del valor cuota. No restarla dos
  veces.** La nota (2) del archivo de comisiones de la CMF lo dice: la remuneración de la AGF
  "se devengará diariamente" sobre el patrimonio del fondo, y lo mismo vale para el gasto
  operacional. Por eso la metodología del regulador llama *rentabilidad neta* al retorno que
  sale del valor cuota. Lo único que se cobra APARTE al partícipe es la **comisión de
  colocación**, y solo esa se descuenta: `neta = (1+r)·(1−c) − 1`.
  Datos medidos: remuneración mediana **1,1% anual**, y solo **284 de 2.372 series (12%)**
  cobran comisión de colocación — en el resto la rentabilidad del valor cuota ya es lo que
  recibe el inversionista. Cuando la CMF informa rango en vez de valor único se usa el
  **máximo** (peor caso), porque el cobro efectivo depende de los días de permanencia.
- **Para costos usar `fm.fm_comision.php`, NO el archivo de "costos asociados".** Los dos
  traen la estructura de costos, pero el de costos asociados (`fmdfm_excel2.php`) **no trae el
  RUN**: cruzarlo por nombre llega a 41,4% con 69 nombres ambiguos, porque usa el nombre
  comercial con marca ("Fondo Mutuo Security Agresivo Estratégico") mientras la estadística usa
  el corto ("AGRESIVO ESTRATÉGICO"). `fm.fm_comision.php` sí trae RUN y cruza al **79,9%**.
  Los porcentajes vienen como FRACCIÓN (0,0119 = 1,19%) e incluyen IVA.
- **`plazo_rescate` no se ingiere: viene corrupto DESDE la CMF.** Sus bytes son `EF BF BD`, es
  decir el carácter de reemplazo Unicode ya codificado ("UN D<?> H<?>BIL"): el texto se perdió
  antes de generarse el archivo. No es un problema de nuestra decodificación y no tiene arreglo.
- **Las rentabilidades OFICIALES se ingieren, no se recalculan** (`fetch-ffmm-comparador`).
  Vienen del Comparador de rentabilidad y llegan ya calculadas con la metodología del
  regulador. Tres cosas que hay que saber al mostrarlas: los horizontes de 3, 5 y 10 años
  están **anualizados** (no son el acumulado), la *real* descuenta inflación por variación de
  la UF, y **el comparador no cubre todas las series** (1.922 de 2.861 pares): una serie sin
  cifra oficial no es un error, y la vista lo dice en vez de mostrar la tabla vacía.
- **El RUT de la administradora solo existe en el comparador**, y se cruza por **RUN del
  fondo**, nunca por nombre: la estadística diaria trae la razón social larga
  ('BANCHILE ADMINISTRADORA GENERAL DE FONDOS S.A.') y el comparador el nombre corto
  ('BANCHILE'). Ver `propagar_agf_rut()`. Cobertura: 91,8% de las filas diarias.
- **Residuo conocido y acotado: el factor de reparto.** Quedan 130 pares (6,6%), de **27 fondos
  de 429**, donde nuestra cifra es MENOR que la oficial (mediana −4,97 pp) y nunca mayor. Son
  los fondos que reparten beneficios: la fórmula (1) de la CMF lleva un factor de reparto
  `F` que acumula las distribuciones, y ese dato no está en la estadística que ingerimos. Si
  alguna vez aparece la fuente del factor, este residuo se cierra. **El sesgo es de un solo
  lado: subestimamos, nunca sobrestimamos.**
- **Cada tipo de cartera de FFMM tiene su PROPIA numeración de campos**: nacional `601xxxx`,
  extranjera `602xxxx`, opciones `603xxxx`, futuros `604xxxx`. Aplicar el mapa de una a las
  otras devuelve NULL en silencio (se vio como "0 tipos, 0 países" en el ranking de cartera
  extranjera). NACI y EXTR son paralelas —mismos cuatro últimos dígitos— y eso se verificó
  perfilando 800 registros de cada una: `6010300`/`6020300` son países ISO, `6010400`/`6020400`
  códigos de instrumento. OPCI y FUTU tienen otro layout y quedan **sin mapear a propósito**:
  se cuentan sus posiciones pero no se desglosan. Ver `Database.FFM_CAMPOS`.
- **La cartera de FFMM no tiene llave natural.** Un fondo puede tener dos posiciones idénticas
  del mismo instrumento y el archivo no trae columna que las distinga: medido en 2026-06 hay
  1.506 grupos con 3.006 filas repetidas, que son posiciones reales. El `UNIQUE(...raw_data)`
  original hacía fallar la carga entera. Se quitó, junto con los índices secundarios, por la
  misma razón que en la cartera de seguros (índice ART sobre claves muy duplicadas rompe el
  DELETE de la reingesta). Idempotencia = Delete-then-Insert por (año, mes, tipo de cartera).
- **El archivo de FFMM viene en UTF-8, no en latin-1.** El colector lo decodificaba como
  `iso-8859-1` y producía mojibake en los nombres ('BI ESTRATÃ‰GICO'). Corregido: UTF-8 con
  fallback, más `fix_mojibake` sobre el nombre (idempotente, ver el gotcha de intermediarios).
- **Mojibake en el Excel de intermediarios**: la CMF entrega ese archivo con los bytes UTF-8
  leídos como latin-1 ('IntermediaciÃ³n'); el de seguros viene sano. `cmf_fecu.fix_mojibake`
  repara solo cuando los bytes latin-1 son UTF-8 válido (firma del doble encoding), así que un
  texto correcto como 'Préstamos' pasa intacto. NO quitarlo al tocar el parser.
- **Estáticos cacheados**: `base.html` cuelga `?v={{ asset_version }}` (mtime de `app.css`,
  calculado en `api/deps.py` al arrancar). Si cambias el CSS, **reinicia el servidor** para que
  el navegador vea los estilos nuevos.
- **AGF separadas del universo EEFF**: las Administradoras Generales de Fondos viven en el mismo
  `cmf_financial_statements` (mismo txt IFRS), pero se clasifican aparte para su propia vista (como
  bancos/seguros). El criterio único está en `db.database.AGF_SQL_PREDICATE` / `is_agf_name`
  (nombre con "ADMINISTRADORA" + "FONDOS", excluye pensiones/cesantía; el plural "FONDOS" evita
  falsos positivos como "Sociedad Administradora del **Fondo**…"). `get_cmf_companies(agf=...)` y
  `search_cmf_companies(..., agf=...)`: `None`=todas, `False`=excluye AGF, `True`=solo AGF. El
  selector de `/eeff` usa `agf=False`. Hoy: 736 empresas EEFF + 69 AGF. Si el criterio necesita
  ajuste, tocar solo esas constantes.
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
- **La tabla `series` es una COPIA de los metadatos del catálogo, y se desincronizaba.**
  `upsert_series` refresca nombre/unidad/categoría, pero el catch-up de BCCh solo llamaba a
  `upsert_observations`: editar `series_catalog.yaml` no llegaba nunca a la BD por esa vía.
  Se detectó porque el cobre (`F019.PPB.PRE.40.M`) seguía etiquetado
  `centavos_USD_por_libra` en la BD cuando el YAML ya decía `USD_por_libra` — y los valores
  (0,52 a 6,16 desde 1975) confirman que son **US$/libra**: mostrar "¢US/lb" junto a un 6,13
  era un error de lectura de un orden de magnitud. Corregido: la sonda pasa `meta` en
  `params` y el dispatch hace `upsert_series` antes de las observaciones. Si se edita el
  catálogo y se quiere propagar ya, sin esperar a que cada serie venza, hay que recorrer el
  YAML llamando a `upsert_series`.
- **Al comparar dos series macro, la unidad no basta: hay que mirar la MAGNITUD.**
  `porcentaje` y `porcentaje_anual` son etiquetas distintas de lo mismo. Si se comparan por
  string, TPM e inflación caen en ejes separados y se pierde justo la lectura que importa:
  con eje compartido, la distancia vertical entre ambas es la **tasa de política real**. Ver
  `_bc_dimension()` en `api/routers/views.py`. Por el mismo motivo, la variación interanual
  de una serie que ya es tasa se informa en **puntos porcentuales**, no en % (la TPM pasando
  de 4,75 a 4,50 no "cayó 5,3%": cayó 25 puntos base).
- **Bancos: la CMF cambió el plan de cuentas en 2022** — códigos de 7→9 dígitos, nombres
  distintos ('ACTIVOS'→'TOTAL ACTIVOS') y UNIDADES: el plan antiguo reporta en MILLONES de
  pesos, el vigente en PESOS (verificado contra cifras públicas del Banco de Chile; la
  etiqueta histórica "miles de CLP" era incorrecta). `get_bank_statement_evolution` empalma
  las series largas por nombre normalizado y homologa pre-2022 ×1e6; el detalle fino entre
  regímenes NO es comparable. Historia backfilleada desde 2019 (`fetch-banks --history
  --year-start N`).
- **Cartera de seguros (Circular 1.835): descarga AUTOMÁTICA.** La página de descarga de la
  CMF muestra un CAPTCHA de imagen, pero se verificó empíricamente que **no se valida del lado
  servidor** (`captcha.php {accion:valida}` es control de cliente; el endpoint `fnAjax=descarga`
  sirve el ZIP a cualquier GET). Por eso `fetch-cartera-seguros` (`--period YYYYMM` | `--latest`
  | `--history [--since YYYYMM]`) baja **e** ingesta en un paso, como los demás colectores;
  `list_remote_periods` descubre los períodos publicados (126 a jul-2026, desde 2016-01).
  Fallback offline: `import-cartera-seguros` reingesta ZIP ya presentes en
  `data/seguros_cartera_raw/` (`YYYYMM<v|g>.zip`) sin tocar la red.
  **Ambos ramos**: `tipoentidad=CSVID` (vida) y `CSGEN` (generales), servidos desde la MISMA
  carpeta `dcisgv/` — el código de generales no se deduce del de vida, hay que usar ese.
  **La descarga necesita reintentos**: aunque `fnAjax=archi` confirme que el período existe,
  el servidor devuelve HTML en vez del ZIP de forma intermitente (~6% de los meses en un
  backfill). `download_cartera` reintenta 3 veces antes de rendirse.
- **La CMF RESTATA períodos ya publicados: reingestar no es un no-op.** Verificado el
  2026-08-10 reingestando 202606 vida: el control (B.8) quedó idéntico, pero la renta fija
  (B.1) pasó de **103.119 a 103.110 filas** — 9 posiciones menos que en la descarga del
  2026-07-27. No es no-determinismo del parser (el mismo ZIP parseado dos veces da el mismo
  número) ni un error del Delete-then-Insert, que hizo lo correcto al adoptar la versión nueva:
  es el archivo de origen el que cambió. Consecuencias: (1) un conteo de filas **no** sirve como
  checksum de "ya está cargado"; (2) si alguien reporta que una cifra histórica se movió sin que
  se tocara el código, esta es la primera hipótesis; (3) vale la pena reingestar períodos
  antiguos de vez en cuando, no solo el corriente.
- **Nombres de archivo DENTRO del ZIP: tres convenciones conviviendo.** El parser matchea por
  letra de tipo + 6 dígitos, con el sufijo de ramo **opcional** (`_type_files`), porque:
  (1) los períodos antiguos usan `YYMMDD` del último día del mes (`c200131v`) y los recientes
  `YYYYMM` (`c202606v`); (2) hay meses sueltos —202412— que omiten la letra de ramo justo en
  los archivos de control (`c202412`) aunque sí la traigan en los de renta fija. No fijar el
  valor del período en el patrón.
- **Cartera: qué se importa.** El ZIP trae 12 tipos de archivo × compañía, de **ancho fijo,
  UTF-8, separados por `\n`**, con registros tipo 1 (identificación) / 2 (detalle) / 3
  (totales). Hoy se importan **B.8 Control** (registro de 138 chars, reconcilia EXACTO) y
  **B.1 Renta Fija**. Los registros de B.1 miden **930 chars en el layout antiguo y 970 en el
  nuevo** (`B1_RECORD_LENS`): no es un error del spec, son dos épocas; el prefijo `0:154` del
  que salen todos los campos reconciliados es idéntico en ambos (validado 500/500 sobre datos
  de 2020). Los montos de valoración al final del registro siguen **sin reconciliar**, y los
  demás archivos de detalle (A/F/X/O/P…) tampoco están incorporados. **No estimar posiciones a
  ojo** ni confiar en montos no reconciliados.
  El registro tipo 3 **no sirve de cuadratura**: su TOTAL_REGISTROS es inconsistente entre
  compañías (+2 en 56 archivos, +1 en 6, atípicos de +4/-2/-5, y 2 archivos sin tipo 3).
  Validación fiable = largo de registro + que los montos parseen. Cruce de sanidad: el total
  de cartera / "Total inversiones financieras" del EEFF da ~1,22-1,24 de forma consistente
  entre compañías (la cartera además incluye créditos y avances a tenedores de pólizas).
- **Reconciliación de archivos de detalle (B.1 renta fija)**: como el spec publicado NO cuadra,
  el layout se reconstruye EMPÍRICAMENTE desde los datos y se valida campo a campo. Método
  (reutilizable para B.2/B.3/B.5): (1) perfilar la clase de carácter de cada posición sobre
  miles de registros para hallar límites; (2) validar cada campo candidato contra TODOS los
  registros con una restricción semántica (fecha AAAAMMDD que parsea, RUT de 9 dígitos, país
  alfabético, monto 100% numérico) y exigir ≥98% de acierto; (3) usar campos-ancla fiables
  (PAÍS='CL' en 114:116) para cerrar tramos. La constante `B1_FIELDS` en
  `collectors/seguros_cartera.py` documenta el layout validado (registro de 970 chars).
  **Solo se cargan los campos que validan al 100%** (identificación + valor nominal, offsets
  0-154). Los montos de valoración de mercado del final del registro quedan pendientes: el spec
  suma 878≠970, no hay total interno para validarlos y requieren el catálogo de descriptores
  (archivo B.9). NO estimar posiciones a ojo. Datos coherentes: las aseguradoras de vida
  tienen mayoritariamente MHA (mutuos hipotecarios) y CLEAS (leasing), lo esperado.
- **Seguros (vida y generales): plan de cuentas propio y cifras en MILES de CLP.** La FECU de
  seguros (Circulares 2022/2050) NO es el IFRS corporativo ni el bancario: no mezclar con
  `cmf_financial_statements`. Ambos ramos comparten formato/parser y viven en la misma tabla,
  distinguidos por la columna `insurance_type` ('vida' | 'generales'); endpoints
  `seg_vida_fecu1.php` / `seg_gen_fecu1.php`. El Excel de la CMF trae las compañías como FILAS y
  las cuentas como COLUMNAS (código FECU + glosa concatenados, ej. `5.10.00.00Totalactivo`), en 3
  estados (`ESF`/`ERI`/`EFE`). Una sola descarga (`society=0`, "TODOS") = todas las compañías del
  ramo/período → `fetch-seguros` NO itera por compañía (a diferencia de bancos). Solo seguros
  directos (`tiposociedad=A`); reaseguradoras ('R') y crédito ('CR') se ignoran. `report_freq`
  distingue `trimestral` (cargado) de `anual` (diciembre auditado, disponible pero no cargado aún).
  Identidad contable verificada (Activo = Pasivo + Patrimonio). Backfill: `fetch-seguros --history`
  (por defecto `--ramo ambos`). La clave de borrado-reinserción idempotente es
  (`insurance_type`, `period`, `report_freq`).

- **"El período existe" NO es "el período está completo".** La CMF publica por goteo: el
  EEFF 202606 se ingirió temprano con 200 de ~380 empresas y la sonda antigua lo daba por
  cargado. Las sondas por entidad (`scheduler/sources.completeness`) comparan el número de
  entidades del último período con la mediana de los 4 comparables (mismo mes del año en
  las trimestrales) y, bajo 90%, lo vuelven a pedir con `force` hasta 120 días después de la
  fecha esperada. El mismo control corre en `validate` (`completitud_*`).
- **Cartera de seguros: el RUT del encabezado puede ser el de OTRA compañía.** Compañías
  absorbidas siguen reportando en su propio archivo pero con el RUT de la absorbente
  (`c240630g.99061000` declara 99231000 HDI; `c202412.76632384` Zurich Rentas Vitalicias declara
  99185000 Chilena Consolidada). Eso duplicaba a la absorbente con dos juegos de cifras
  (llaves repetidas en 202406 y 202411→202507). Regla en `seguros_cartera._resolve_rut`: si el
  RUT declarado es el de otro archivo del mismo ZIP, manda el del nombre del archivo y la
  razón social queda vacía. Fuera de ese caso sigue mandando el declarado.
- **Cartera de seguros: `valor_final = repr + no_repr` falla en ~116 filas**, casi todas código
  J00 de 13 compañías de generales, con cifras del propio informante que no cuadran (BCI
  202608: 2.219.634 contra 62.219.624). No es el parser: `validate` lo reporta como aviso.
- **Bancos: la API SBIFv3 puede repetir cuentas dentro de un mismo reporte.** El de resultados
  de 2023-08 trajo cada cuenta dos veces con el mismo valor (19.664 llaves repetidas, sumas
  al doble). `insert_bank_records` descarta la repetición exacta; si una cuenta viniera dos
  veces con valores distintos se conserva y `validate` la marca.
- **Cartera SP: rezago real ~4 meses.** El 2026-11-16 el último publicado era 202606; un mes
  no publicado responde un volcado `<pre>Array…` en vez del ZIP. La sonda antigua (40 días)
  pedía un mes inexistente y, como solo pedía ese, nunca rellenó 202602→202606. Ahora las
  sondas mensuales piden TODOS los meses faltantes y paran en el primero no publicado.
- **Métricas de productos: cada tipo en su propio corte.** Las AFP publican a diario y los
  fondos mutuos se cargan por corte mensual; con un corte común uno de los dos quedaba sin
  dato. Las consultas toman el último `fecha_corte` de CADA producto, no el máximo global.
- **Tasa libre de riesgo** de Sharpe/Sortino y del "exceso sobre depósito": tasa de captación
  bancaria 30-89 días en pesos (`F022.CAP.TIN.D089.NO.Z.M`); si no estuviera cargada, la TPM.
- **Códigos de series nuevas del BCCh: sacarlos de `SearchSeries`, no de memoria.** La función
  `search` del CLI solo filtra el YAML local. La API sí lista todo con
  `function=SearchSeries&frequency=DAILY|MONTHLY` (decodificar la respuesta como latin-1).
  El IPSA en la BDE es solo mensual.
- **RUT de bancos y AFP: no vienen en sus fuentes.** Los de bancos están en
  `config/entidades.yaml` con su evidencia (el RUT aparece como emisor de bonos BB/LH/DPC en la
  cartera B.1 y el prefijo del nemotécnico identifica al banco). China Construction Bank,
  Bank of China y las 7 AFP quedan pendientes: no hay evidencia en los datos cargados.
- **FastAPI 0.141 anida los routers incluidos** (`_IncludedRouter`): `app.routes` no lista las
  rutas hijas. Para inventariarlas hay que recorrer `original_router`.

- **Disco: los crudos se borran después de cada carga** (`scheduler/housekeeping.py`, regla
  pedida por el usuario en nov-2026). Llegaron a ocupar 1,65 GB contra 1,0 GB de la base. Se
  llama desde `run_catchup`, desde `main.py` (result_callback de la CLI para `fetch-*` e
  `import-*` exitosos) y desde `actualizar.py`. Consecuencia: `import-cartera-seguros` solo
  sirve para ZIP que se dejen a mano en la carpeta; ya no hay caché de descargas entre corridas.
  Colectores nuevos: escribir sus crudos bajo `data/<fuente>_raw/` para quedar cubiertos.
- **`compactar` usa EXPORT/IMPORT DATABASE, no `COPY FROM DATABASE`**: este último copia las
  tablas en un orden que viola las claves foráneas (observations antes que series) y falla.
  Se verifica que los conteos de todas las tablas coincidan antes de reemplazar el archivo, y
  las secuencias quedan bien (el siguiente `nextval` sigue al máximo id). 1.060 → 967 MB la
  primera vez. Requiere la base cerrada por todos los demás procesos.
- **El catch-up solo recalcula derivadas y controles si llegaron filas nuevas**: corre cada hora
  y casi siempre no hay nada publicado; antes gastaba ~20 s por corrida en vano.
- **`publish_lag_days` también rige en series diarias**: la EOF es quincenal aunque su código
  termine en `.D`; sin rezago aparecía siempre atrasada.
- **Las AFP no llegan al mismo día** (nov-2026: Capital al 15, las demás al 12). Toda
  comparación entre AFP va a `sp_common_date()` (mínimo de los máximos). Sin eso la
  participación del Fondo A sumaba 412%, el "líder" 12m salía mal y el explorador de productos
  marcaba como no vigentes a 6 de las 7 AFP (vigente era `MAX(date) = máximo global`; ahora
  es "informó en los últimos 7 días", `db/products.VIGENCIA_DIAS`).
- **Cartera SP: secciones en MAYÚSCULAS, subtotales en minúscula** ("TOTAL INSTITUCIONES
  ESTATALES" contiene "Total Tesorería General"). Filtrar `LIKE 'TOTAL %'` sensible a
  mayúsculas; con `UPPER()` se contaban dos veces (sumaba 100,9%).
- **Cartera de fondos mutuos (NACI) trae RUT del emisor y valor de mercado**: `FFM_6010211` =
  RUT emisor (en cuotas de fondos es el RUN del fondo), `FFM_6011200` = valorización en
  **miles de la moneda del fondo** (no del instrumento; verificado contra el patrimonio),
  `FFM_6011513` = % del activo del fondo. La vista `v_mutual_fund_holdings.monto` es el
  NOMINAL (`FFM_6010900`), no el valor. Lo usa `db/cross.issuer_holdings`.
- **Sector: dos clasificaciones.** La web usa la curada (`config/company_sectors.yaml` por
  RUT, luego nombre): "Otros" bajó de 199 a 18 empresas. El modelo estructural sigue con la de
  nombre (`models.structural.SECTOR_FUENTE = "nombre"`) porque la curada perdió el A/B
  (`backtest --sectores`, 17-nov: 45% de pares, z −3,8/−4,9). No unificar sin re-medir.
- **Derivados por sector (BCCh `F099.DER`)**: 73 series en el catálogo, categoría
  `derivados_sector` (monitor en `/inversion-institucional/derivados`, `db/derivados.py`). Los
  nocionales de AFP son reales aunque superen varias veces los activos (US$ 873 mil millones
  de total con US$ 232 mil millones de activos): las identidades cuadran exactas y se testean.
  "Compra de tasa variable" = recibe variable; neto negativo = recibe fija (pierde si suben
  las tasas). Las fechas mensuales del BCCh vienen como el 1° del mes = saldo a fin de ese mes.
  El catálogo de la BDE se lista con `SearchSeries` por frecuencia (la respuesta viene en
  latin-1, no UTF-8). En YAML, los títulos BDE traen `\`: no ponerlos entre comillas dobles.
- **Cartera FFMM, derivados (FUTU/OPCI)**: `v_mutual_fund_derivatives` (migración 011).
  `FFM_6040500` = monto comprometido y `FFM_6030800` = valor de la opción, en miles de la
  moneda del fondo. Subyacente rotulado sin norma (`PROM`/`DOLAR`/`USD`/`US$` = dólar).
- **Seguros B.7 (derivados, archivo `p` del ZIP 1.835)**: tipos de registro 2 opción, 3
  forward, 4 futuro, 5 swap, 6 pacto. El anexo no calza con los archivos: posiciones
  reconciliadas en `collectors/seguros_cartera._b7_record` (precio implícito de cada pata =
  UF/dólar/euro del cierre; valor razonable = B.8 M10). En swaps el nocional de la pata larga
  viene en M$ y el de la corta en pesos (escalas distintas). Objetivo `1512` en swaps: código
  no documentado en el anexo. Historia con `main.py backfill-derivados-seguros` (baja, lee
  solo B.7 y borra cada ZIP); el catch-up lo carga junto con B.1 y B.8.
- **Cartera desagregada SP (`sp_cartera_detalle`, migración 013)**: el MISMO ZIP del cuadro 1
  (`sp_portfolio_holdings`) trae ~28 cuadros más; `fetch_portfolio_full` saca ambos en una lectura
  por streaming (no descomprime los ~300 MB del XML). Cuadros por TÍTULO (la numeración cambia
  entre años). Swaps = VALORIZACIÓN (MMUSD, con signo), no nocional; forwards = unidades del
  activo objeto, y la fila con código de 4 letras es el total de las siguientes (no sumar ambas).
  Última letra del código C/V = compra/venta (validado contra el BCCh); W/Y y M/N sin
  diccionario oficial. Emisores nacionales (tabla `emisor_nac`) vienen por NOMBRE: cruce en
  `db/cross.mapa_emisores_sp` + `config/sp_emisores.yaml`. Rezago de publicación ~4 meses.
  En Windows un escritor no abre la base si otro proceso la tiene abierta (aunque sea lectura):
  `backfill-sp-detalle` abre la BD solo para insertar cada mes y reintenta.
- **Emisores sin EEFF curados en `config/entidades.yaml` › `emisores`** (Fisco/Tesorería
  60805000, Banco Central 97029000, Coopeuch, RUT antiguo de Itaú), con el nemotécnico como
  evidencia. 77844703 (BTBD/FNTBD) queda pendiente.

## Cómo hacer tareas comunes

- **Agregar una serie BCCh**: editar `config/series_catalog.yaml` y `fetch --id <code> --from-date`.
- **Nueva vista web**: ruta en el módulo de su dominio en `api/routers/web/` (o un router nuevo
  incluido desde `views.py`) + plantilla en `api/templates/` (extiende `base.html`); patrón
  HTMX = `hx-get` a un endpoint que devuelve un fragmento `partials/`. Si la vista lee una
  fuente nueva, agregar su prefijo a `api/freshness_banner.ROUTE_SOURCES` y la ruta a
  `tests/test_web.py`.
- **Nueva fuente de datos**: entrada en `scheduler/sources.SOURCES` + modo manual en
  `actualizar.MANUAL` + fila en `dim_dataset` (migración) + control de unicidad en `db/quality.py`.
- **Nueva tabla derivada o vista**: migración numerada en `db/migrations.py` (idempotente).
- **Nueva clasificación de EEFF**: agregar entrada a `EEFF_CLASSES` (views.py) → aparece en el hub
  `/eeff`. Si comparte el formato FECU (seguros/intermediarios), reusar `fecu_entidad.html` +
  `partials/fecu_statements.html` y un método `get_*_evolution` sobre `_fecu_evolution`.
- **Nuevo dato del panel**: método en `db/database.py` → incluir en `get_overview_kpis` → render
  en `partials/kpi_cards.html`.
- **Regenerar estáticos**: `web-preview` (panel) y `eeff-export` (EEFF) → carpeta `preview/` (gitignored).
- **Actualizar seguros de vida**: `fetch-seguros --year YYYY --month {3,6,9,12}` (o `--history` para
  backfill trimestral completo; el catch-up también los toma automáticamente vía frescura).
- **Cargar cartera de seguros**: `fetch-cartera-seguros --latest` (descarga **e** ingesta; el
  CAPTCHA no se valida del lado servidor, ver gotchas). `import-cartera-seguros` es el fallback
  offline para ZIP ya presentes en `data/seguros_cartera_raw/`. Idempotente por período.
- **Actualizar intermediarios de valores**: `fetch-intermediarios --year YYYY --month {3,6,9,12}`
  (o `--history`). Corredores y agentes vienen juntos en una sola descarga.
- **Sumar otra fuente con formato "FECU tabla" de la CMF**: reusar `collectors/cmf_fecu.py`
  (`parse_fecu_workbook`) — resuelve encabezado, secciones arrastradas, código+glosa y filas de
  totales. Solo hay que construir la URL de descarga y darle forma al registro.

## Análisis y proyecciones (Fase 6)

- **Ratios financieros**: `db.database.compute_ratios(df)` (función módulo, sin conexión) →
  ROE, ROA, márgenes neto/bruto, liquidez corriente, deuda/patrimonio. Son "del período"
  (income acumulado en el año para trimestres). Se muestran en `/eeff` (tarjeta) y en
  `/comparar` (tabla comparativa entre empresas). El export estático también los incluye.
- **Proyecciones de EEFF** (`models/`, servidas en `/proyecciones`): modelo **híbrido** de
  producción (`hybrid.forecast_company_hybrid`, configuración en `hybrid.PRODUCCION`) —
  SARIMAX puro para el 1er trimestre, promedio SARIMAX + estructural en el 2° y 3°, y modelo
  **estructural** (activos operacionales ← macro → rotación → margen) del 4° en adelante.
  Bandas de confianza **empíricas** = cuantiles del error del backtest (`hybrid.BAND_QUANTILES`;
  regenerar si cambian specs/anclajes). Toda decisión de diseño está arbitrada por el arnés
  `models/backtest.py` (rolling-origin con vintages honestos de encuestas). **Metodología y
  resultados en `docs/wiki/Backtest-Modelos.md`** (leer antes de tocar los modelos).
- **Regla de adopción programada** (`models/compare_bt.py`): un cambio entra si en pares
  (misma empresa, origen y horizonte) gana ≥ 50% de los pares distintos (los empates se
  descartan, test de signo), baja el MASE medio y la mediana, y ningún horizonte empeora
  > 0,02. Desde la ronda 6 se exige además que el Δ medio tenga el mismo signo en las 40
  empresas del backtest original y en 40 nuevas. No adoptar es un resultado válido; el código
  queda tras un parámetro.
- **Decisiones medidas (nov-2026):**
  - Estado de resultados: se lee ERFG o, si la empresa solo informa por naturaleza, ERNG
    (`models.forecast.VALOR_ER`; con ambos manda ERFG). +81 empresas, Aguas Andinas incluida.
  - Senda anclada a swaps (`build_macro_path(fuente="mercado"|"mezcla")`): **no adoptada**
    (gana < 50% de pares). Por defecto siguen las encuestas.
  - Factores de curva en la ecuación de activos (`models/curve_factors.py`,
    `backtest --factores`): pendiente y spread bancario empeoran. La tasa real 5a en lugar de
    la TPM **se adoptó en la ronda 6** (decisión del dueño de evaluar solo h2–4, donde manda).
  - **Ronda 6 (17-nov):** ancla del margen = mediana 4T (rotación sigue con `last`); h2–3 =
    promedio SARIMAX + estructural; tope al SARIMAX explosivo (`models/robusto.acotar`) en
    todos los modelos. Limpieza de atípicos (`robusto.limpiar_outliers`, STL robusto, solo
    atípicos aislados) medida en corporativos, bancos y seguros: **no adoptada** (la mediana
    del margen ya la cubre; en bancos empeora h3) — las vistas solo los marcan.
  - Bancos y seguros: arnés `models/backtest_simple.py`; bandas calibradas
    (`simple_forecast.BANDAS_SIMPLE`). El resultado de bancos se proyecta como base +
    exposición a la UF (`forecast_series_uf`; k por MCO 36 meses). En seguros de vida no pasa.
  - Gotcha: el SARIMAX de flujos (`enforce_stationarity=False`) puede explotar en series
    cortas (MASE 10⁸ medido en seguros). Desde la ronda 6 lo ataja `robusto.acotar` (rango
    histórico ± 1 rango → naive estacional / último valor); se activa en ~1 de 640 casos.

## Estado y roadmap

Fases 1–4 + SP operativas; catch-up (Fase 5 parcial) y capa web (Fase 7) muy avanzadas;
**Fase 6 operativa** (ratios + comparación sectorial + radar de salud + ranking sectorial +
**proyecciones de EEFF en producción**). Datos: **~12,85M filas**; CMF con 45 períodos
(201503→202603, 10 cierres anuales); cartera SP con 133 meses; cartera de seguros con 78.
Vistas: panel, EEFF, evolución, comparar, ranking, salud, **proyecciones**, banca, AFP.
Reseñas de empresas en `config/company_profiles.yaml` + `api/company_profiles.py` (inferencia
por tipo). Lanzadores de un clic: `iniciar_web.bat` (dashboard) y `Actualizar_Datos.exe`
(actualización de fuentes; se compila con `build_actualizador.py`).

**El plan vivo y las decisiones pendientes están en `docs/wiki/Hoja-de-Ruta.md`** (mantenerlo
al día). El backtest y las decisiones de modelado están en `docs/wiki/Backtest-Modelos.md`.
Convenciones EEFF: solo se muestran los estados estándar **ESF C/NC, ERFG, ERI, EFMD**
(se descartan ESF OL, ERNG, EFMI). Siguiente: ampliar proyecciones (márgenes intermedios,
rezagos post-M&A) y Fase 8 storytelling con LLM. Trabajo en rama `feature/web-api-skeleton`.
