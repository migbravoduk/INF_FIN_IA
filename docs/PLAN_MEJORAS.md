# Plan de mejoras, reestructuración y limpieza — INF_FIN_IA

> Auditoría hecha el 2026-11-16 sobre la rama `feature/web-api-skeleton` y la base
> `data/finanzas_chile.duckdb` (abierta en solo lectura; no se modificó ningún dato).
> Este documento está escrito para ejecutarse por fases con un asistente (Opus). Cada tarea
> trae qué hacer, dónde y cómo comprobar que quedó bien.

**Objetivo del producto** (lo que ordena las prioridades): entregar información de inversiones
confiable a personas y, más adelante, derivarlas a los proveedores de los instrumentos que
aparecen (AGF, AFP, corredoras, aseguradoras). Eso exige tres cosas que hoy no están:
datos al día y verificables, un catálogo de *productos y proveedores* (hoy solo hay tablas
por fuente) y métricas de riesgo y costo comparables entre productos.

---

## 0. Reglas para quien ejecute este plan

1. **Leer `CONTEXTO.md` completo antes de tocar datos.** Sus "gotchas" siguen vigentes.
2. **Respaldar la base antes de cualquier fase que escriba**: copiar
   `data/finanzas_chile.duckdb` a `data/backups/finanzas_chile_AAAAMMDD.duckdb`.
3. **DuckDB admite un solo proceso escritor.** Cerrar la web (`main.py serve`) antes de
   ingestar o migrar.
4. **No crear índices secundarios** en tablas con reingesta por período (ver CONTEXTO).
5. **Usar siempre** `.\.venv\Scripts\python.exe` y anteponer `PYTHONIOENCODING=utf-8`.
6. **No borrar archivos ni datos sin confirmación del usuario.** La sección 7 lista
   candidatos; cada borrado se confirma.
7. **No inventar glosas, códigos de serie ni nombres de emisor.** Si falta la fuente, se deja
   el código crudo y se anota.
8. Un commit por tarea o grupo lógico; al cerrar cada fase, actualizar `CONTEXTO.md` y
   `docs/wiki/Hoja-de-Ruta.md`.

---

## 1. Diagnóstico: lo que se encontró

### 1.1 Estado de la base (verificado)

| Tabla | Filas | Cobertura | Observación |
|---|---:|---|---|
| `observations` | 64.595 | 1975 → 2026-10-16 | 52 series con datos |
| `cmf_financial_statements` | 1.521.945 | 201503 → 202606 (46) | **202606 incompleto** (1.2-B) |
| `cmf_bank_statements` | 1.950.332 | 201901 → 202608 (92) | **202308 duplicado** (1.2-C) |
| `cmf_insurer_statements` | 593.614 | 201503 → 202606 | sin duplicados |
| `cmf_broker_statements` | 213.325 | 201503 → 202606 | sin duplicados |
| `cmf_insurer_portfolio_control` | 74.145 | 202001 → 202608 (80) | completo |
| `cmf_insurer_portfolio_fixed_income` | 8.110.466 | 202001 → 202608 | fechas 100% válidas en 202608 |
| `sp_quota_values` | 211.570 | 2008-04 → 2026-10-14 | sin huecos diarios |
| `sp_instrument_prices` | 137.424 | 2026-01 → 2026-08-21 | sin historia previa a 2026 |
| `sp_portfolio_holdings` | 340.881 | 2015-01 → **2026-01** | **detenida hace 9 meses** (1.2-D) |
| `cmf_mutual_fund_quota_values` | 95.418 | 2024-01 → 2026-08 (32 cortes) | sin duplicados de llave |
| `cmf_mutual_fund_portfolio` | 33.665 | **solo 2026-06** | sin historia |
| `cmf_mutual_fund_fees` | 2.372 | **solo 202603** | sin historia |
| `cmf_mutual_fund_returns` | 1.981 | **solo corte 2026-03-31** | sin historia |

Última ingesta registrada en `fetch_log`: **2026-10-16**. Todo lo diario y mensual lleva un
mes de atraso.

### 1.2 Problemas que hay que corregir (por gravedad)

**A. El catch-up está roto en el código fuente.** `scheduler/freshness.py:297` llama a
`_probe_cmf_fm`, pero la función se llama `_probe_cmf_fm_cartera`. Comprobado:
`main.py catchup --dry-run` termina en `NameError`. Afecta a `catchup`, al scheduler y al menú
de `actualizar.py`. Las ingestas de septiembre y octubre funcionaron solo porque
`Actualizar_Datos.exe` se compiló el 10 de agosto con código anterior; si se recompila hoy,
deja de funcionar. Problemas asociados en el mismo bloque:
- La sonda emite `kind="cmf_fm_cartera"` y `actualizar.py` espera `"cmf_fm"` (`CATCHUP_KINDS`
  y `MENU_FUENTES`), así que el menú nunca vería el estado.
- La sonda calcula `MAX(period_year), MAX(period_month)` por separado: con datos de 2025-12
  y 2026-01 respondería "2026-12". Debe ser `MAX(period_year*100 + period_month)`.
- `actualizar.py:496` usa `res['inserted']`, pero `upsert_mutual_fund_quotas` está declarada
  como `-> int` (`db/database.py:394`). Revisar qué devuelve y alinear.
- La opción 7 del menú llama a `fetch_daily_quotas(hoy)`, que contradice la decisión
  documentada de guardar un corte mensual.

**B. EEFF corporativos 202606 quedó a medias y el sistema lo da por cargado.** Tiene 200
empresas; los junios anteriores tienen 378–385. Se ingirió el 2026-09-09, antes de que la CMF
terminara de publicar. La sonda solo compara "último período en BD ≥ esperado", así que nunca
vuelve a bajarlo. Es un defecto de diseño que afecta a todas las fuentes que publican por
goteo: **"el período existe" no significa "el período está completo"**.

**C. Bancos: el período 202308 está cargado dos veces.** 19.664 llaves
(`period, bank_code, report_type, account_code`) repetidas con el mismo valor. Cualquier suma
de agosto 2023 sale al doble. El resto de los 92 períodos está limpio.

**D. Cartera de AFP detenida en 2026-01.** `fetch_log` muestra "ZIP demasiado pequeño" en
junio y julio y `no_data` en agosto, septiembre y octubre. Lo más probable es que la SP haya
cambiado la URL o el formato de descarga; no es que falten datos publicados. Las vistas
`/afp` y `/fondos` muestran composición de hace 9 meses sin avisarlo.

**E. Fondos mutuos fuera de la actualización automática.** No hay sonda para valores cuota,
comisiones ni comparador; la cartera tiene una sonda rota (punto A). Por eso las cuotas
llegan a agosto y lo demás es una sola foto. Para el objetivo del proyecto este es el
conjunto de datos más importante y es el menos mantenido.

**F. Cartera de seguros sin sonda en el scheduler.** `freshness.py` no tiene sonda
`cmf_cartera`; la lógica vive solo en `actualizar.py`. `Hoja-de-Ruta.md` afirma que "las 9
fuentes tienen sonda" y que "el job horario las cubre todas": no es así para esta fuente.

**G. Catálogo de series con basura.** `series` tiene 64 filas y 12 no tienen ninguna
observación: son códigos inválidos del primer intento (abril 2026), varios duplicando en
nombre a series buenas ("Tasa de Desempleo", "TPM", "Precio del Cobre (¢US/lb)"). Además hay
3 series descontinuadas (`F089.TPM.TAS.13.M`, `F089.EOF.TC.7MA.D`, IMACEC base 2013 con 9
observaciones) que la sonda seguirá intentando bajar.

**H. Identidad de entidades inconsistente.** No existe una tabla maestra:
- `cmf_bank_statements` identifica por `bank_code` y no tiene RUT.
- 77 RUT tienen más de una razón social en EEFF (cambios de nombre) y 6 razones sociales
  tienen más de un RUT.
- La cartera de seguros trae hasta 3 grafías por RUT (`033 METLIFE…`, `Compa#ia…`).
- Solo 185 de 325 emisores de renta fija (B.1, 202608) se resuelven a un nombre.
- En `cmf_mutual_fund_quota_values`, 93 de 2.861 filas del último corte no tienen `agf_rut`,
  repartidas en 14 administradoras que sí tienen RUT conocido: `propagar_agf_rut()` cruza por
  RUN del fondo y pierde los fondos nuevos. Con la AGF ya identificada por nombre en otras
  filas, se puede completar por `agf_name`.
- Monedas `NODEF1`/`NODEF2` en EEFF de 3 empresas (Walmart Chile, GAP International,
  Inmobiliaria Frontera Country Club): código crudo de la CMF sin traducir.

**I. Convenciones de período distintas en cada tabla.** `INTEGER AAAAMM` (EEFF, bancos,
seguros), `VARCHAR 'AAAA-MM'` (`sp_portfolio_holdings`), `VARCHAR 'AAAAMM'`
(`cmf_mutual_fund_fees.periodo`), `VARCHAR 'AAAAMMDD'` (`cmf_mutual_fund_returns.fecha` y las
cinco fechas de B.1), par `period_year`/`period_month` (cartera FFMM) y `DATE` (cuotas).
Cada cruce entre tablas necesita una conversión a mano.

**J. Unidades heterogéneas sin metadato.** Pesos (bancos 2022+), millones (bancos pre-2022 y
patrimonio FFMM), miles (seguros, intermediarios), moneda de reporte (EEFF), UF/USD/EUR
(nominal B.1). Está explicado en prosa en `CONTEXTO.md`, pero no hay nada en la base que lo
diga y la homologación se hace en cada consulta.

**K. Documentación desfasada respecto del código.**
- `api/main.py` dice "ESQUELETO: los endpoints son stubs".
- `db/schema.py` dice que la cartera de seguros se descarga "A MANO" por el CAPTCHA.
- `Hoja-de-Ruta.md` (agosto) no menciona fondos mutuos ni `/banco-central`.
- `config/settings.py`: `CMF_API_KEY` y `cmf_scraping_enabled` no se usan.
- Tabla `sources`: `ine`, `sii`, `bolsa_stgo` sembradas y sin un solo dato.

**L. Ingeniería.**
- **Sin tests** (no hay carpeta `tests/`; el único arnés es `models/backtest.py`). El error
  del punto A lo habría detectado un test de humo de una línea.
- **34 rutas sin confirmar** (18 modificadas, 12 nuevas, 4 borrados en stage) y la rama va
  59 commits por delante de `main`. Todo el trabajo de fondos mutuos y Banco Central existe
  solo en el disco local.
- Archivos monolíticos: `db/database.py` (2.815 líneas, toda la SQL), `api/routers/views.py`
  (1.900), `main.py` (1.622).
- Agregar una fuente obliga a tocar tres lugares (sonda, dispatch, menú): es exactamente
  como se produjo el punto A.
- `scheduler/jobs.py` mantiene 6 jobs cron antiguos (diario, mensual, trimestral, anual, SP
  diario, SP mensual) además del catch-up horario, que ya cubre lo mismo.
- `requirements.txt` sin versiones fijas (`openpyxl` sin límite alguno).
- `logs/app.log` (2,7 MB) sin rotación.
- La API REST (`/api/*`) solo cubre macro, EEFF, bancos y SP; no expone seguros, cartera ni
  fondos mutuos.
- HTMX y Plotly se cargan por CDN: sin internet la web no grafica.

### 1.3 Lo que está bien y no hay que tocar

- Identidad contable verificada en seguros; sin duplicados en seguros, intermediarios, FFMM
  cuotas, comisiones y rentabilidades.
- Valores cuota de AFP sin huecos (365/366 días por año).
- Rentabilidad de FFMM validada contra la CMF (mediana de diferencia 0,000 pp).
- Las 22 AGF de fondos mutuos cruzan por RUT con sus EEFF: el vínculo
  producto → administradora → estados financieros ya es posible.
- Los 19.790 grupos de filas idénticas en `cmf_financial_statements` son el comportamiento
  conocido del archivo XBRL plano (contextos repetidos) y `eeff_format` ya los colapsa al
  mostrar. No son un error de carga.

---

## 2. Fase 0 — Asegurar el trabajo y reparar la actualización

Sin esto no se puede hacer nada más con seguridad. Esfuerzo: medio día.

| # | Tarea | Comprobación |
|---|---|---|
| 0.1 | Respaldar la base (regla 2). | Existe la copia en `data/backups/`. |
| 0.2 | Confirmar el trabajo pendiente en commits lógicos: (a) borrados ya en stage, (b) fondos mutuos (colector, esquema, vistas, plantillas), (c) `/banco-central`, (d) `build_actualizador.py` + `.gitignore`, (e) docs. Antes, sacar los dos archivos de la "megarreforma" (sección 7). Push. | `git status` limpio. |
| 0.3 | Reparar la sonda de FFMM (1.2-A): nombre de función, `kind` único (`cmf_fm_cartera`) en sonda, dispatch, `CATCHUP_KINDS`, `MENU_FUENTES`, y el `MAX` combinado. Alinear el tipo de retorno de `upsert_mutual_fund_quotas`. | `main.py catchup --dry-run` termina sin error y lista todas las fuentes. |
| 0.4 | Crear `tests/` con pytest y tres tests de humo: `probe_all` corre sobre la base en solo lectura; cada `kind` emitido por una sonda tiene rama en `_dispatch` y entrada en el menú; la app FastAPI responde 200 en `/`, `/health` y en cada entrada de la barra de navegación. Agregar `pytest` a `requirements.txt`. | `pytest -q` en verde. |
| 0.5 | Fijar versiones: generar `requirements.lock` con `pip freeze` desde el venv actual. | El archivo existe y está versionado. |
| 0.6 | Agregar `.gitattributes` (`* text=auto eol=lf`, `*.bat eol=crlf`) para terminar con los avisos LF/CRLF. | `git diff` sin avisos. |

---

## 3. Fase 1 — Reparar los datos existentes

Esfuerzo: 1 día (más tiempos de descarga).

| # | Tarea | Comprobación |
|---|---|---|
| 1.1 | **Bancos 202308**: eliminar duplicados. Lo más seguro es reingestar el mes (`fetch-banks --year 2023 --month 8 --force`), que usa Delete-then-Insert. Investigar por qué se duplicó: el `DELETE` filtra por `year, month, bank_code, report_type`; sospecha: una carga con `bank_code` sin relleno de ceros o `report_type` con otra grafía. | `SELECT count(*) FROM (SELECT 1 FROM cmf_bank_statements GROUP BY period, bank_code, report_type, account_code HAVING count(*)>1)` devuelve 0. |
| 1.2 | **EEFF 202606**: reingestar (`fetch-cmf --period 202606 --force`). Luego 202609 si ya está publicado. | `count(distinct rut)` de 202606 queda en el rango 370–390. |
| 1.3 | **Cartera AFP**: diagnosticar el colector (`collectors/sp_pensions.py`, descarga del ZIP alrededor de la línea 237–261). Bajar a mano un período reciente desde spensiones.cl, comparar URL y formato, corregir y hacer backfill 2026-02 → hoy con `fetch-sp-cartera`. | `max(period)` de `sp_portfolio_holdings` ≥ mes esperado por la sonda. |
| 1.4 | **Poner todo al día**: `catchup` completo. Para fondos mutuos, hasta que exista la sonda (Fase 2): `fetch-ffmm-cuotas --months 3`, `fetch-ffmm --period` de cada mes faltante, `fetch-ffmm-comisiones`, `fetch-ffmm-comparador`. | `main.py status` sin pendientes. |
| 1.5 | **Limpiar el catálogo de series**: borrar de `series` las 12 filas sin observaciones (verificar antes que no estén en `series_catalog.yaml`). Marcar las 3 descontinuadas con un campo `discontinued: true` en el YAML para que la sonda las salte. | La consulta de series sin observaciones devuelve 0 y la sonda no las lista. |
| 1.6 | **Completar `agf_rut`** en cuotas FFMM: segundo paso en `propagar_agf_rut()` que cruce por `agf_name` normalizado contra filas que ya tienen RUT. | 0 filas con `agf_rut IS NULL` en el último corte. |
| 1.7 | Borrar de `sources` las semillas sin uso (`ine`, `sii`, `bolsa_stgo`) o dejarlas solo si la Fase 4 las ocupa. | Decisión anotada en CONTEXTO. |

---

## 4. Fase 2 — Que el dato no se vuelva a romper en silencio

Esfuerzo: 2–3 días. Es la fase que más protege la credibilidad del producto.

**2.1 Frescura por completitud, no por existencia (corrige 1.2-B de raíz).**
Cada sonda debe poder decir "período presente pero incompleto". Regla simple: comparar el
número de entidades del período con la mediana de los 4 períodos comparables anteriores; si
está bajo el 90%, el período sigue `due` y se reingesta hasta que se cumpla o hasta que
pasen N días desde la fecha esperada. Aplica a EEFF corporativos, seguros, intermediarios,
bancos, FFMM y cartera de seguros. Como la CMF además corrige períodos ya publicados
(CONTEXTO, "la CMF RESTATA"), agregar una reingesta programada de los últimos 2 períodos de
cada fuente una vez al mes.

**2.2 Registro único de fuentes.** Reemplazar los "tres lugares" por un módulo
`scheduler/sources.py` con una lista de objetos `Source(kind, titulo, probe, fetch,
manual_fn)`. `probe_all`, `_dispatch` y el menú se generan recorriendo esa lista. Con esto
el error del punto A no puede volver a ocurrir.

**2.3 Sondas que faltan**: `cmf_fm_cuotas` (mensual, corte de fin de mes), `cmf_fm_cartera`
(mensual), `cmf_fm_comisiones` (mensual), `cmf_fm_comparador` (trimestral; verificar la
frecuencia real de publicación) y `cmf_cartera` (mover la lógica desde `actualizar.py`).

**2.4 Comando `main.py validate`.** Una batería de controles de calidad que escribe en una
tabla nueva `dq_results(run_at, check_id, tabla, periodo, estado, detalle)` y devuelve código
de salida distinto de cero si hay fallas graves. Controles mínimos:
- unicidad de la llave natural por tabla (los de la sección 1 de esta auditoría);
- completitud por período (regla de 2.1);
- identidad contable Activo = Pasivo + Patrimonio en EEFF, seguros e intermediarios;
- `valor_final = repr_rt_pr + no_repr_rt_pr` en cartera de seguros;
- continuidad: sin meses faltantes en series mensuales, sin días hábiles faltantes en diarias;
- rangos: valor cuota > 0, variación diaria de cuota AFP dentro de ±10%, patrimonio ≥ 0;
- cruces: todo `fund_run` de cartera existe en cuotas; todo `agf_rut` existe en la maestra.
Ejecutarlo al final de cada catch-up y mostrar el resultado en el panel.

**2.5 "Datos al …" en cada vista.** Toda página y todo bloque HTMX debe mostrar la fecha o
período del dato que usa, y un aviso visible si supera el atraso esperado de su fuente. Hoy
`/afp` muestra una cartera de enero sin advertirlo. Para un sitio de inversiones esto no es
opcional.

**2.6 Quitar los 6 jobs cron antiguos** de `scheduler/jobs.py` y dejar solo el catch-up
(más la reingesta mensual de 2.1). Verificar antes que el catch-up cubre cada uno.

**2.7 Rotación de logs**: `RotatingFileHandler` (5 MB × 5 archivos).

**2.8 Recompilar `Actualizar_Datos.exe`** una vez que 2.1–2.6 estén en verde, y agregar un
`--version` que imprima el hash del commit con que se compiló, para saber si el ejecutable
está desfasado respecto del código.

---

## 5. Fase 3 — Reestructuración del modelo de datos

Esfuerzo: 4–6 días. Hacerla con migraciones idempotentes en un módulo `db/migrations/`
(archivos numerados y una tabla `schema_migrations`), no editando `SCHEMA_SQL` a mano. Las
tablas actuales se conservan; lo nuevo se construye encima y las vistas van migrando.

### 5.1 Maestra de entidades — `dim_entidad`

La pieza que hoy falta y que más cosas destraba.

```
dim_entidad(
  rut VARCHAR PRIMARY KEY,      -- sin DV, sin ceros a la izquierda
  dv VARCHAR,
  nombre_canonico VARCHAR,      -- la razón social más reciente
  nombre_corto VARCHAR,         -- 'BANCHILE', 'METLIFE'
  tipo VARCHAR,                 -- banco | agf | afp | aseguradora_vida | aseguradora_generales
                                -- | corredora | agente_valores | emisor | otro
  bank_code VARCHAR,            -- código SBIF, solo bancos
  grupo VARCHAR,                -- grupo financiero, curado a mano (opcional)
  vigente BOOLEAN
)
dim_entidad_alias(rut, nombre, fuente, desde_periodo, hasta_periodo)
```

- Poblarla desde las cinco tablas de EEFF, la cartera de seguros y `cmf_mutual_fund_agf`.
- El mapa `bank_code → RUT` son 16 filas: curarlo a mano en `config/bancos.yaml` con el RUT
  tomado de la ficha de cada banco en la CMF. Esto solo ya permite resolver los bonos
  bancarios en la tabla de emisores de renta fija.
- Las AFP (7) también van en un YAML: hoy solo existen como texto (`afp_name`).
- Los alias guardan la historia de nombres (ej. `039` = Itaú Corpbanca → Itaú Chile) y
  absorben las grafías sucias. La limpieza de `insurer_glossary.clean_company_name` pasa a
  aplicarse una sola vez al poblar.
- Efecto medible: subir la cobertura de emisores B.1 desde 185/325. Los originadores de
  mutuos y leasing seguirán sin nombre; eso es correcto (ver CONTEXTO) y no se rellena.

### 5.2 Catálogo de productos — `dim_producto`

Es lo que convierte un repositorio de datos en un sitio de inversiones: una fila por cosa en
la que una persona puede invertir.

```
dim_producto(
  producto_id VARCHAR PRIMARY KEY,   -- 'ffmm:8011:A', 'afp:HABITAT:C'
  tipo VARCHAR,                      -- ffmm | afp_obligatorio | afp_cuenta2 | apv | ...
  rut_proveedor VARCHAR,             -- → dim_entidad
  codigo_fuente VARCHAR,             -- RUN del fondo, etc.
  serie VARCHAR,
  nombre VARCHAR,
  categoria VARCHAR,                 -- familia del comparador CMF / tipo de fondo AFP
  clase_inversionista VARCHAR,       -- Retail | Alto Patrimonio | APV
  moneda VARCHAR,
  vigente BOOLEAN, primera_fecha DATE, ultima_fecha DATE
)
```

- FFMM: una fila por fondo y serie (≈2.860), desde cuotas + comparador + comisiones.
- AFP: 7 × 5 fondos. Dejar el diseño preparado para los fondos generacionales de la reforma
  previsional (el selector de `/fondos` ya lee los tipos desde la base).
- La **clase de inversionista** importa mucho: a una persona común no se le deben mostrar
  series de Alto Patrimonio como si pudiera comprarlas. Hoy la web las mezcla.

### 5.3 Proveedores y derivación — tablas nuevas, fuera del DuckDB analítico

```
proveedor_canal(rut_proveedor, url_sitio, url_contratacion, canal, notas, verificado_en)
```

Curado a mano en `config/proveedores.yaml` (22 AGF + 7 AFP para empezar). Es el dato que
permite el botón "ir al proveedor".

El registro de clics o contactos **no puede ir en este DuckDB**: la API lo abre en solo
lectura y DuckDB admite un único escritor. Usar un SQLite aparte (`data/eventos.sqlite`) con
`evento_salida(ts, producto_id, rut_proveedor, origen)`. No guardar datos personales hasta
tener resuelto el punto legal de la sección 8.

### 5.4 Tablas de hechos de producto

- `fact_producto_cuota(producto_id, fecha, valor_cuota, valor_cuota_clp, patrimonio_clp,
  participes)`: vista o tabla materializada que unifica cuotas FFMM (ya convertidas a pesos
  con la regla validada del tipo de cambio del día siguiente) y cuotas AFP.
- `fact_producto_metricas(producto_id, fecha_corte, ...)`: las métricas de la sección 6.2,
  recalculadas al cierre de cada ingesta. Las vistas leen de aquí en vez de recalcular en
  cada request.
- **Comisiones y rentabilidades oficiales con historia**: hoy cada ingesta reemplaza la foto
  anterior. Cambiar la idempotencia a "reemplazar el mismo corte" y conservar los demás, para
  poder mostrar cómo cambian los costos y validar nuestras cifras en más de una fecha.
- **Cartera FFMM tipada**: `raw_data` JSON obliga a parsear en cada consulta. Crear
  `cmf_mutual_fund_holdings` con columnas reales (emisor, país, código de instrumento,
  moneda, monto, % del activo) para NACI y EXTR, usando el mapa `Database.FFM_CAMPOS` ya
  validado. Conservar `raw_data` como respaldo. OPCI y FUTU siguen sin desglosar.

### 5.5 Convenciones

- **Período**: `period INTEGER AAAAMM` en todas las tablas mensuales o trimestrales, y `DATE`
  para fechas. Migrar `sp_portfolio_holdings.period`, `cmf_mutual_fund_fees.periodo`,
  `cmf_mutual_fund_returns.fecha`, el par año/mes de la cartera FFMM y las cinco fechas
  `VARCHAR` de B.1. Hacerlo agregando la columna nueva, poblando, cambiando los lectores y
  recién después eliminando la antigua.
- **Unidades**: tabla `dim_dataset(tabla, unidad, moneda, frecuencia, llave_natural, fuente,
  url_fuente, rezago_dias)`. Sirve a la vez para documentar, para la página de metodología y
  para parametrizar las sondas y los controles de calidad.
- **Bancos en una sola unidad**: vista `v_bank_statements` que entregue `val_total_clp`
  homologado (×1e6 antes de 2022), para que la homologación deje de vivir dentro de
  `get_bank_statement_evolution`.
- **`fetch_log.series_id`** con formato único `kind:periodo` (hoy conviven `sp_cartera`,
  `sp_cartera:2026-06` y `cmf_fm`).
- `NODEF1`/`NODEF2`: averiguar en la documentación del archivo plano de la CMF qué
  significan; si no hay fuente, mostrarlos como "moneda no informada".

### 5.6 Tamaño

La renta fija B.1 son 8,1M de filas y la mayor parte de los 780 MB. No es urgente, pero si
la base se va a publicar en un servidor conviene exportar B.1 a Parquet particionado por
período y dejar en DuckDB una vista sobre esos archivos, además de una tabla agregada por
(período, compañía, emisor, tipo de instrumento, moneda), que es lo único que la web consulta.

---

## 6. Fase 4 — Información que no puede faltar y cálculos de alto valor

### 6.1 Datos que faltan (ordenados por valor para un inversionista)

| # | Dato | Por qué | Cómo incorporarlo |
|---|---|---|---|
| D1 | **Historia de cuotas FFMM 2018 → 2023** | Con 32 cortes mensuales no se puede calcular riesgo a 5 años ni comparar ciclos (2020, 2022). La fuente sirve historia desde 2018 (verificado en CONTEXTO). | `fetch-ffmm-cuotas --from-month 2018-01 --to-month 2023-12`. Cerca de 72 descargas y 200 mil filas. |
| D2 | **Cuotas FFMM diarias de los últimos 12–24 meses** | El corte mensual no permite medir volatilidad real ni caída máxima dentro del mes. | Tabla aparte `cmf_mutual_fund_quota_daily` con retención móvil; la mensual queda como está. Cerca de 700 mil filas por año. |
| D3 | **Factor de reparto de dividendos** | Hoy se subestima la rentabilidad de 27 fondos (mediana −4,97 pp). Publicar una cifra equivocada a la baja de un fondo con nombre es un riesgo reputacional. | Buscar en la CMF la estadística de repartos por fondo. Mientras no exista, mostrar la cifra oficial del comparador para esos fondos y marcar los 27 con un aviso. |
| D4 | **Historia de cartera, comisiones y comparador FFMM** | Una sola foto no permite ver rotación ni cambios de costo. | Backfill mensual de 24 meses con los comandos existentes, después de 5.4. |
| D5 | **Tasas de referencia sin riesgo y de depósitos a plazo** | Toda rentabilidad necesita un punto de comparación: "¿le ganó al depósito?". Ya están TPM y BCP/BCU a 5 y 10 años; faltan tasas de captación 30–89 días y 90–365 días, y la curva a 1 y 2 años. | Series BCCh. **Buscar los códigos con el servicio de búsqueda de la BDE; no escribirlos de memoria.** Agregar a `series_catalog.yaml`. |
| D6 | **Índices de mercado** (IPSA, y globales en USD) | Comparador para fondos accionarios. | Revisar si la BDE los publica (capítulo de indicadores bursátiles). Si no, evaluar fuente con licencia; los índices tienen restricciones de redistribución. |
| D7 | **Fondos de inversión públicos** (CMF) | Es la otra mitad de la industria de fondos y hoy no aparece. Además las AFP y aseguradoras invierten en ellos (`CFI*` en la cinta SP). | Colector nuevo sobre la estadística de fondos de inversión de la CMF; mismo patrón que FFMM. Explorar primero qué publica y con qué llave. |
| D8 | **APV y Cuenta 2 de AFP** | Productos voluntarios: son los que una persona efectivamente elige, a diferencia del ahorro obligatorio. La comisión es distinta de la obligatoria. | Comisiones desde la SP en `config/afp_commissions.yaml` (revisar qué cubre hoy); el valor cuota es el mismo del fondo. |
| D9 | **Historia de precios SP anterior a 2026** | Pendiente ya anotado en la hoja de ruta. | `fetch-sp-precios --history`. |
| D10 | **FECU anual auditada de seguros** | Pendiente ya anotado (`report_freq='anual'`). | `fetch-seguros --freq anual --history`. |
| D11 | **Solvencia y reclamos del proveedor** | Al derivar a un proveedor conviene mostrar su solidez. Los EEFF de AGF, bancos y aseguradoras ya están; faltan indicadores regulatorios (patrimonio de riesgo y endeudamiento en seguros; estadística de reclamos de la CMF). | Seguros: cuentas de la FECU ya cargada. Reclamos: explorar la publicación de la CMF. |

No está en esta lista el precio de acciones individuales: la Bolsa de Santiago no ofrece una
fuente abierta con derecho a redistribución. Evaluarlo solo si el producto lo requiere y con
una licencia de datos.

### 6.2 Cálculos de alto valor

Todos por `producto_id` y fecha de corte, guardados en `fact_producto_metricas`. Cada uno
debe llevar una prueba contra un caso calculado a mano y, donde exista, contra la cifra
oficial.

**Rentabilidad**
- Nominal y **real (descontando UF)** a 1, 3, 6, 12, 36, 60 y 120 meses; anualizada desde 3
  años. Ya existe en parte; unificar FFMM y AFP en una sola función.
- **Exceso sobre la mediana de su categoría** y **percentil dentro de la categoría**. Es la
  respuesta a "¿este fondo es bueno?" y es más útil que la rentabilidad absoluta.
- **Exceso sobre la tasa sin riesgo** (D5).
- **Consistencia**: porcentaje de ventanas móviles de 12 meses en que el fondo quedó en la
  mitad superior de su categoría. Distingue al que es bueno de forma sostenida del que tuvo
  un año de suerte.

**Riesgo** (requiere D1 y D2)
- Volatilidad anualizada, **caída máxima** y meses que tardó en recuperarse, peor mes y peor
  año, porcentaje de meses negativos.
- Sharpe y Sortino contra la tasa sin riesgo. Comparar con el `ind_rar` oficial de la CMF
  donde exista; si difieren, mostrar el oficial.
- Escala de riesgo de 1 a 7 derivada de la volatilidad, para lectores no técnicos (declarar
  la metodología).

**Costo** — el diferenciador más claro frente a los sitios de las propias administradoras
- **Costo total anual** = remuneración + gasto operacional (ambos ya dentro del valor cuota)
  y, aparte, comisión de colocación.
- **Costo en pesos**: "por cada $1.000.000 invertido 10 años, pagas $X en comisiones", con la
  rentabilidad histórica de la categoría como supuesto declarado.
- **Costo relativo**: percentil de costo dentro de la categoría y clase de inversionista.
- **Rentabilidad por unidad de costo** y gráfico costo versus rentabilidad por categoría: la
  evidencia de si pagar más ha rendido más.
- Mantener la regla de CONTEXTO: **no restar la remuneración dos veces**.

**Flujos y tamaño**
- **Flujo neto estimado** = Δ patrimonio − patrimonio anterior × rentabilidad del período.
  Muestra hacia dónde se mueve el dinero (aportes y rescates) por fondo, categoría y
  administradora. Solo sumar dentro de una misma moneda.
- Variación de partícipes, y participación de mercado por AGF y categoría.

**Cartera** (requiere 5.4)
- Exposición por clase de activo, país, moneda y emisor; concentración (los 10 mayores, HHI).
- **Traslape entre dos fondos**: porcentaje de cartera en común. Muy útil para quien tiene
  varios fondos y cree estar diversificado.
- **Lectura cruzada institucional**: en qué emisores coinciden AFP, aseguradoras y fondos
  mutuos. Es un contenido que ningún sitio público ofrece y el repositorio ya tiene las tres
  carteras; depende de `dim_entidad` (5.1).

**AFP**
- Rentabilidad neta de comisión por nivel de renta (ya existe `get_afp_net_simulation`):
  exponerla como comparador directo entre AFP.
- Costo del cambio: diferencia acumulada entre la AFP actual y la mejor del período, en pesos.

### 6.3 Presentación mínima para que esto sea útil a una persona

- **Ficha de producto** (`/producto/{id}`): qué es, quién lo administra (con enlace a sus
  EEFF), categoría, rentabilidad nominal y real, riesgo, costo, cartera, posición en su
  categoría, fecha del dato y botón al proveedor.
- **Comparador** de hasta 4 productos de la misma clase de inversionista.
- **Explorador por categoría** con filtros por clase de inversionista, moneda, costo y
  horizonte, ordenable por cualquiera de las métricas.
- **Página de metodología** generada desde `dim_dataset` y desde los docstrings de las
  métricas: fuentes, fechas, fórmulas y limitaciones conocidas (por ejemplo, el residuo del
  factor de reparto).

---

## 7. Limpieza de la carpeta del proyecto

Nada de esto se borra sin confirmación (regla 6).

### 7.1 Sacar del proyecto

| Archivo | Por qué es prescindible | Acción |
|---|---|---|
| `Impacto_megarreforma_impuestos_diferidos.docx` | Por su nombre trata de impuestos diferidos y una reforma tributaria, sin relación con este repositorio. No se abrió: confirmar con el usuario. Está sin versionar en la raíz y un `git add .` lo subiría. | Mover a otra carpeta del usuario. |
| `Presentacion_megarreforma_impuestos_diferidos.pptx` (788 KB) | Igual que el anterior. | Mover a otra carpeta del usuario. |

### 7.2 Borrar o regenerar

| Archivo | Por qué | Acción |
|---|---|---|
| `build_exe.py`, `interactive.py`, `test_bde.py`, `test_bde_search.py` | Ya no existen en disco; el borrado está en stage. `build_exe.py` fue reemplazado por `build_actualizador.py`, el menú interactivo por `actualizar.py`, y los `test_bde*` eran pruebas manuales de la API. | Confirmar el commit (tarea 0.2). |
| `Actualizar_Datos.exe` (62 MB) | Artefacto compilado el 2026-08-10, desfasado: no trae los cambios de fondos mutuos. Ya está en `.gitignore`. | Regenerar después de la Fase 2 (tarea 2.8). No se pierde nada. |
| `docs/sp_integration.md` | Versión anterior de `docs/wiki/Integracion-SP.md` (mismo tema, 128 contra 160 líneas, contenido divergente). Dos documentos sobre lo mismo terminan contradiciéndose. | Revisar si tiene algo que la versión wiki no tenga, fusionarlo y borrar. |
| `logs/app.log` (2,7 MB) | Crece sin límite desde abril. | Truncar tras implementar la rotación (2.7). |
| `__pycache__/` en todo el árbol | Bytecode regenerable; ya ignorado por git. | Borrar libremente. |
| `data/bank_raw/` (60 JSON, 22 MB), `data/sp_raw/` (30 CSV), `data/cmf_raw/`, `data/insurer_raw/`, `data/broker_raw/` | Caché de descargas ya ingeridas; se pueden volver a bajar. | Definir una política única: conservar los 2 períodos más recientes por fuente como evidencia ante correcciones de la CMF y borrar el resto. Implementarla como `main.py clean-cache`. |
| `data/seguros_cartera_raw/` (5 ZIP, 41 MB) | Igual; `import-cartera-seguros` los usa como respaldo sin red. | Misma política: conservar los 2 últimos meses por ramo. |

### 7.3 Código y configuración sin uso

| Elemento | Evidencia | Acción |
|---|---|---|
| `Database.get_mutual_fund_performance` (`db/database.py:448`) | Ninguna referencia en el repositorio; viene de la primera versión con datos de prueba. | Borrar. |
| `settings.CMF_API_KEY` y `cmf_scraping_enabled` | Solo aparecen en su definición. | Borrar, también de `.env.example`. |
| Dependencias comentadas en `requirements.txt` (`pdfplumber`, `camelot`, `python-xbrl`) | Planes de la Fase 3 que no se usaron: la CMF se lee del archivo plano. | Borrar las líneas. |
| Semillas `ine`, `sii`, `bolsa_stgo` en `SEED_SOURCES_SQL` | Sin series ni datos. | Borrar (tarea 1.7). |
| 12 series huérfanas en la tabla `series` | Sin observaciones. | Tarea 1.5. |
| 6 jobs cron antiguos en `scheduler/jobs.py` | Duplican el catch-up. | Tarea 2.6. |
| `api/preview.py` + comandos `web-preview` y `eeff-export` | Exportación estática de cuando no había servidor web. Sigue funcionando y está referenciada, pero cubre solo el panel y los EEFF. | Preguntar al usuario si aún la usa. Si no, borrar; si sí, dejarla anotada como "no se amplía". |

### 7.4 Se conservan (aunque lo parezcan)

- `docs/Informe_Inconsistencias_CMF_Circular_1835.docx`: es la evidencia de por qué el
  layout de B.1 se reconstruyó a partir de los datos; `db/schema.py` lo cita.
- `.claude/launch.json`, `iniciar_web.bat`, `build_actualizador.py`: lanzadores en uso.
- `processors/normalizer.py`: lo usan `main.py`, `actualizar.py` y `scheduler/jobs.py`.
- `README.md` y `CONTEXTO.md` son ambos necesarios, pero se pisan (805 y 466 líneas).
  Reducir el README a instalación y uso, y dejar el detalle técnico solo en CONTEXTO.

---

## 8. Requisitos no técnicos que condicionan el objetivo comercial

Hay que resolverlos antes de publicar el sitio o de activar la derivación a proveedores.
No son tareas de código y **deben revisarse con un abogado**; se anotan porque cambian el
diseño.

1. **Asesoría de inversión.** En Chile la Ley 21.521 (Ley Fintec) y la normativa de la CMF
   que la desarrolla regulan la asesoría de inversión y exigen inscripción en un registro de
   la CMF para prestarla. Un sitio que ordena fondos "de mejor a peor" y deriva al proveedor
   puede caer dentro de esa definición. Consecuencias de diseño mientras no se aclare:
   mostrar datos y comparaciones objetivas con metodología publicada, no recomendaciones
   personalizadas; no construir un perfilador que termine en "te conviene este fondo".
2. **Conflicto de interés.** Si un proveedor paga por la derivación, ese pago no puede
   alterar el orden ni la visibilidad, y debe declararse. Diseñar los rankings de modo que
   dependan solo de métricas reproducibles.
3. **Avisos obligatorios** en toda cifra de rentabilidad: la rentabilidad pasada no garantiza
   la futura, los valores cuota pueden variar, fuente y fecha del dato.
4. **Datos personales.** Si se registran contactos o clics asociados a una persona, aplica
   la legislación chilena de protección de datos (Ley 19.628 y su reforma, Ley 21.719).
   Por eso 5.3 parte registrando eventos anónimos.
5. **Condiciones de uso de las fuentes.** CMF, SP y Banco Central publican datos abiertos,
   pero conviene revisar los términos de cada portal, citar la fuente en cada vista y
   reemplazar la descarga que salta el CAPTCHA de la Circular 1.835 por un canal acordado con
   la CMF antes de operar en público. El usuario y la clave de la API del Banco Central son
   personales: revisar si sus condiciones permiten un uso público.
6. **Proyecciones de EEFF.** Publicar proyecciones de resultados de empresas con nombre es
   delicado. Mantener las bandas, la nota metodológica y el aviso de "bandas no calibradas"
   donde corresponde.

---

## 9. Fase 5 — Arquitectura para salir a público (después de las fases 0–4)

- **Dividir los archivos monolíticos** sin cambiar comportamiento, con los tests de la Fase 0
  como red: `db/database.py` → `db/repos/{macro,eeff,bancos,seguros,sp,ffmm}.py` (la clase
  `Database` queda como fachada); `api/routers/views.py` → un router por dominio; `main.py` →
  paquete `cli/`.
- **Separar ingesta y servicio**: la ingesta escribe la base de trabajo y publica una copia
  de solo lectura (más las tablas de métricas ya calculadas) que es la que sirve la web. Así
  desaparece el conflicto de escritor único y el sitio nunca queda bloqueado por un backfill.
- **Completar la API REST** con seguros, cartera, fondos mutuos y productos, y versionarla
  (`/api/v1`).
- **Servir HTMX y Plotly desde `api/static/`** en vez de CDN.
- **Caché** de las respuestas de las vistas pesadas (los datos cambian una vez al día).
- **CI** (GitHub Actions): `pytest` y un chequeo de estilo en cada push.
- Fusionar `feature/web-api-skeleton` en `main` (59 commits de diferencia) y trabajar desde
  ahí con ramas cortas.

---

## 10. Orden de ejecución sugerido

| Fase | Contenido | Esfuerzo | Depende de |
|---|---|---|---|
| 0 | Commits, reparar catch-up, tests de humo | 0,5 día | — |
| 1 | Reparar datos y poner al día | 1 día | 0 |
| 7 (limpieza) | Sección 7, con confirmación | 0,5 día | 0 |
| 2 | Frescura por completitud, registro de fuentes, `validate`, "datos al…" | 2–3 días | 1 |
| 3 | `dim_entidad`, `dim_producto`, convenciones, hechos | 4–6 días | 2 |
| 4a | Datos D1, D4, D5 y métricas de rentabilidad, costo y flujos | 3–4 días | 3 |
| 4b | D2, D3 y métricas de riesgo; cartera tipada y traslape | 3–4 días | 4a |
| 4c | Ficha de producto, comparador, explorador, metodología | 4–5 días | 4a |
| 8 | Revisión legal | en paralelo desde ya | — |
| 5 | División de módulos, copia publicada, API, CI | 4–6 días | 3 |
| 4d | D7 (fondos de inversión), D8 (APV y Cuenta 2), D11 | según prioridad | 3 |

**Si solo se hace una cosa además de las fases 0 a 2:** `dim_producto` con las métricas de
costo y de posición en la categoría para fondos mutuos. Es lo que más acerca el repositorio
a su objetivo con los datos que ya están cargados y validados.

---

## Anexo — Consultas de verificación usadas en la auditoría

```sql
-- Duplicados en bancos (hoy: 19.664, todos en 202308)
SELECT period, count(*) FROM (
  SELECT period FROM cmf_bank_statements
  GROUP BY period, bank_code, report_type, account_code HAVING count(*) > 1
) GROUP BY 1;

-- Completitud de EEFF por período (hoy: 202606 = 200; junios previos 378–385)
SELECT period, count(DISTINCT rut) FROM cmf_financial_statements
GROUP BY 1 ORDER BY 1 DESC LIMIT 8;

-- Series sin observaciones (hoy: 12)
SELECT s.id, s.name FROM series s LEFT JOIN observations o ON o.series_id = s.id
GROUP BY ALL HAVING count(o.date) = 0;

-- Cuotas FFMM sin RUT de administradora en el último corte (hoy: 93)
SELECT agf_name, count(*) FROM cmf_mutual_fund_quota_values
WHERE agf_rut IS NULL AND date = (SELECT max(date) FROM cmf_mutual_fund_quota_values)
GROUP BY 1;

-- Emisores de renta fija resolubles a nombre (hoy: 185 de 325 en 202608)
WITH e AS (SELECT DISTINCT ltrim(rut_emisor,'0') r
           FROM cmf_insurer_portfolio_fixed_income WHERE period = 202608),
     k AS (SELECT rut FROM cmf_financial_statements UNION SELECT rut FROM cmf_insurer_statements
           UNION SELECT rut FROM cmf_broker_statements UNION SELECT agf_rut FROM cmf_mutual_fund_agf)
SELECT count(*), count(k.rut) FROM e LEFT JOIN k ON e.r = k.rut;

-- Última ingesta por fuente
SELECT source_id, status, count(*), max(started_at) FROM fetch_log GROUP BY ALL ORDER BY 1, 2;
```

Comando que hoy falla y debe quedar funcionando al cerrar la Fase 0:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe main.py catchup --dry-run
```
