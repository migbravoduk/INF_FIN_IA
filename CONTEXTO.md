# CONTEXTO.md — Guía del proyecto INF_FIN_IA

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
| `cmf_bank_statements` | CMF/SBIFv3 (API JSON) | balances/resultados con desglose por moneda; ~1,8M filas, 2019+ (plan de cuentas cambió en 2022, ver gotchas) |
| `cmf_insurer_statements` | CMF (descarga Excel FECU seguros) | EEFF de seguros **vida + generales** (col `insurance_type`); plan de cuentas propio (Circulares 2022/2050); **trimestral** 2015Q1+; ~581K filas (vida 338K + generales 243K), 72 compañías; cifras en **miles de CLP** |
| `cmf_insurer_portfolio_control` | CMF Circular 1.835 (ZIP mensual, **descarga manual**) | Cartera de inversiones de seguros: totales por tipo de inversión y compañía (archivo B.8 Control); miles de CLP |
| `cmf_insurer_portfolio_fixed_income` | CMF Circular 1.835 (archivo B.1) | Detalle de instrumentos de renta fija por compañía: emisor, tipo, nemotécnico/ISIN, país, valor nominal (campos **reconciliados** contra los datos; valoración de mercado pendiente) |
| `cmf_broker_statements` | CMF (descarga Excel FECU IFRS intermediarios) | EEFF de **corredores de bolsa + agentes de valores** (col `broker_type`); plan de cuentas propio (códigos `11.01.00`), 14 secciones en `section`; **trimestral** 2015Q1+; ~210K filas, 56 entidades; cifras en **miles de CLP** |
| `sp_quota_values` | SP (scraping) | valor cuota + patrimonio diario por AFP/fondo |
| `sp_instrument_prices` | SP | cinta de precios diaria |
| `sp_portfolio_holdings` | SP (XML) | cartera mensual; backfill 2015-01→2026-01 (133 meses); cols `row_order`/`section` |
| `fetch_log` | — | bitácora de ingestas (todas las fuentes vía catch-up) |

## Dónde está cada cosa

- `collectors/`: `bcentral.py`, `cmf.py` (plano), `cmf_banks.py` (SBIF), `cmf_fecu.py` (**parser
  compartido** de los Excel "FECU tabla"), `cmf_insurers.py` (seguros vida/generales),
  `cmf_brokers.py` (corredores + agentes), `sp_pensions.py`.
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
  - Los nombres de compañía vienen sucios desde el archivo (código interno "033 METLIFE…", Ñ
    escrita como '#', campos en blanco). `insurer_glossary.clean_company_name` los normaliza
    **solo para mostrar**; el prefijo numérico se saca únicamente si empieza en cero, porque
    "4 LIFE SEGUROS DE VIDA" es una compañía real.
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
  | `--history`) baja **e** ingesta en un paso, como los demás colectores; `list_remote_periods`
  descubre los períodos publicados (126 a jul-2026, desde 2016-01). Fallback offline:
  `import-cartera-seguros` reingesta ZIP ya presentes en `data/seguros_cartera_raw/`
  (`YYYYMM<v|g>.zip`) sin tocar la red. Solo **vida** por ahora (falta ubicar el `tipoentidad`
  de generales en la CMF). El ZIP trae 12 tipos de archivo × compañía, de **ancho fijo, UTF-8, separados
  por `\n`**, con registros tipo 1 (identificación) / 2 (detalle) / 3 (totales).
  **Enfoque escalonado**: hoy solo se importa el archivo **C = B.8 Control**, cuyo layout
  reconcilia EXACTO con los datos (registro de 138 chars; 0 montos inválidos en 33 compañías).
  Los archivos de detalle (I/A/F/X…) tienen specs publicadas que **NO cuadran** con los
  archivos reales (el PDF de B.1 declara un registro de identificación de 930 chars y el dato
  real tiene 970; en el detalle quedan ~105 chars sin explicar y campos fundidos). Sus
  primeros ~15 campos sí validan; el resto exige reconciliación campo a campo contra los
  datos antes de confiar en los montos. **No estimar posiciones a ojo.**
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

## Cómo hacer tareas comunes

- **Agregar una serie BCCh**: editar `config/series_catalog.yaml` y `fetch --id <code> --from-date`.
- **Nueva vista web**: ruta en `api/routers/views.py` + plantilla en `api/templates/` (extiende
  `base.html`); patrón HTMX = `hx-get` a un endpoint que devuelve un fragmento `partials/`.
- **Nueva clasificación de EEFF**: agregar entrada a `EEFF_CLASSES` (views.py) → aparece en el hub
  `/eeff`. Si comparte el formato FECU (seguros/intermediarios), reusar `fecu_entidad.html` +
  `partials/fecu_statements.html` y un método `get_*_evolution` sobre `_fecu_evolution`.
- **Nuevo dato del panel**: método en `db/database.py` → incluir en `get_overview_kpis` → render
  en `partials/kpi_cards.html`.
- **Regenerar estáticos**: `web-preview` (panel) y `eeff-export` (EEFF) → carpeta `preview/` (gitignored).
- **Actualizar seguros de vida**: `fetch-seguros --year YYYY --month {3,6,9,12}` (o `--history` para
  backfill trimestral completo; el catch-up también los toma automáticamente vía frescura).
- **Cargar cartera de seguros**: bajar el ZIP del mes a mano (CAPTCHA), dejarlo en
  `data/seguros_cartera_raw/` y correr `import-cartera-seguros` (idempotente por período).
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
