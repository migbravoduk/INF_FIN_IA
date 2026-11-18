# Hoja de Ruta (Roadmap)

> Situación actual y plan. Última actualización: noviembre 2026 (rama `feature/web-api-skeleton`).

---

## 0. Auditoría y plan de mejoras (nov-2026)

Plan completo y diagnóstico en [`docs/PLAN_MEJORAS.md`](../PLAN_MEJORAS.md). Ejecutado:

- [x] **Fase 0** — trabajo confirmado en git; catch-up reparado (`NameError`); tests (`pytest`);
  `requirements.lock`; `.gitattributes`.
- [x] **Fase 1** — bancos 202308 sin duplicados; EEFF 202606 reingerido; cartera de seguros
  sin RUT duplicados (10 períodos reingeridos); series huérfanas y semillas sin uso eliminadas;
  `agf_rut` completado; todas las fuentes puestas al día.
- [x] **Fase 2** — registro único de fuentes (`scheduler/sources.py`); completitud por período;
  sondas de fondos mutuos y cartera de seguros; `main.py validate` + `dq_results`; "datos al …"
  en cada vista; jobs cron antiguos eliminados; reingesta mensual de correcciones; rotación de logs.
- [x] **Fase 3** — migraciones numeradas; `dim_dataset`, `dim_entidad`, `dim_producto`,
  `fact_producto_metricas`, `proveedor_canal`; vistas `v_*` con convenciones homogéneas.
- [x] **Fase 4** — `/productos`, ficha, comparador con traslape, `/metodologia`, salida al
  proveedor con registro anónimo; emisores en común seguros–fondos mutuos; series BCCh de
  referencia (captación, curva 2 años, IPSA/IGPA, índices globales); backfills (sección 3).
- [x] **Fase 5** — `db/database.py` y `views.py` divididos por dominio; API `/api/v1`; JS local;
  CI; export a Parquet.

Después de la auditoría (nov-2026): curvas de tasas y `/curvas`, duración efectiva de fondos
de renta fija, y proyecciones con el resultado no operacional separado. Ejecutado el 16-nov
[`docs/PLAN_SEMANA.md`](../PLAN_SEMANA.md) S1–S4 y S6 (resumen en
[`docs/INFORME_SEMANA.md`](../INFORME_SEMANA.md)): empresas ERNG con proyección, bandas
regeneradas, bancos y seguros con bandas calibradas, exposición a la UF en el resultado de
bancos; anclas de mercado y factores de curva medidos y no adoptados. Queda S5 (frontera
eficiente, opcional) y las decisiones del §6 de ese plan. Detalle de modelos en
[`docs/PLAN_PROYECCIONES_MACRO.md`](../PLAN_PROYECCIONES_MACRO.md).

17-nov: inversión por emisor (`/inversion-institucional/emisor`), arreglos de AFP (fecha
común) y productos, sectores curados; y el **monitor de derivados institucionales**
(`/inversion-institucional/derivados`, [`docs/PLAN_DERIVADOS.md`](../PLAN_DERIVADOS.md)):
BCCh por sector, fondos mutuos por fondo y seguros por compañía (B.7). Además, la **cartera
desagregada de la SP** (`sp_cartera_detalle`, 2010→2026-06): swaps y forwards por AFP, fondo y
contraparte, y las AFP en la vista por emisor. Revisión visual con capturas y correcciones de
gráficos (bitácora de `docs/PLAN_SEMANA.md` §7).

18-nov: **vista `/derivados`** (pestaña propia): mercado, industrias, riesgos y coberturas por
entidad sobre 376 series nuevas del Banco Central y las fuentes por entidad (SP, B.7, FUTU/OPCI).
Pendientes: duración residual del swap cámara (hoy plazo original), valorización por sector
(no existe en el BCCh), objetivo `1512` de seguros.

### Pendientes consolidados (17-nov-2026)

Modelos y análisis:
- [ ] **Bancos y seguros con driver de crecimiento** (colocaciones / reservas reales ← IMACEC):
  repetir la prueba con la senda EEE vintage y decidir (`docs/PLAN_PROYECCIONES_MACRO.md` §D).
- [ ] Modelos específicos de bancos y seguros: naive estacional en bancos h ≥ 9, limpieza de
  atípicos en bancos (gana 58% de pares, empeora a 3 meses).
- [ ] Sectores curados en el modelo estructural: reintentar con más shrinkage (`LAMBDA_SECTOR`).
- [ ] Frontera eficiente (S5, opcional). Costos financieros como deuda × tasa. Narrativa con LLM.
- [ ] Valorización de mercado de la renta fija de seguros (B.1) sin reconciliar: bloquea las
  curvas de crédito por emisor. Clasificaciones de riesgo por emisor.

Datos y fuentes:
- [ ] Derivados: verificar el cierre del 5-nov con el BCCh de nov-2026 (~enero); seguros antes
  de 2024-12 (formato antiguo), futuros de seguros sin cuadrar, objetivo `1512` sin documentar;
  diccionario W/Y y M/N de los códigos de forwards de la SP; forwards SP antes de 2017-08.
- [ ] 11 emisores de la SP sin RUT asignado (1% del monto; lista en `config/sp_emisores.yaml`).
- [ ] RUT 77844703 (BTBD/FNTBD) y 16 empresas sin sector (BK y Vivo ya clasificadas).

Vistas — **rediseño del 17-nov (segunda ronda) cerrado**; quedan detalles menores:
- [x] `/proyecciones/corporativos` abre con la empresa de mayores ingresos con ≥ 24 trimestres
  (Cencosud) y no con la primera alfabética (vacía).
- [x] Formato chileno en todo el sitio (≈ 110 expresiones `"{:,.Nf}"` / `"%.Nf"` → filtro `es`;
  `pct` y `num` también); fechas de Plotly en castellano; ejes sin sufijos SI ingleses ("4T").
- [x] `/fondos-mutuos`: encabezados con estilo y espacio; AGF abreviadas; series con saltos
  imposibles (−90% o ×10 en un mes) fuera del ranking y la mediana, con aviso; etiquetas finales.
- [x] `/banca`: Total destacado con peso y filete (no verde); negativos en rojo; paleta de monedas.
- [x] `/afp` neta: gráfico de pesas (fondo → neta; el tramo es la comisión). Nominal vs real
  con la real en color. Fondos A–E en escala cálido→frío por riesgo. Valores directos en carteras.
- [x] Vista por emisor: bloque AFP con su propia ventana SP (vs 12 meses ya se calcula);
  indicadores en grilla común; último mes destacado.
- [x] Leyendas: capa común `api/static/charts.js` (envuelve `Plotly.newPlot/react`): leyenda
  arriba, automargin, separadores `,.`, paleta validada (CVD/contraste), tooltip oscuro, alto
  interno correcto en `.chart` con alto en línea.
- [x] Campos de texto, fecha y número oscuros como los selectores (`app.css`); menú que se ajusta.
- [ ] Menores: espacio en blanco sobre el resultado en `/proyecciones/*`; la tabla de AFP neta
  pide scroll horizontal a 1.100 px; unidad de los ejes de `/banca` evolutiva (pesos crudos,
  mezcla planes pre/post 2022); revisar `/eeff`, `/comparar`, `/ranking`, `/salud`, `/productos`
  con capturas (no se revisaron en esta ronda, heredan el tema común).

Pendiente que requiere decisión o curaduría humana:

- [ ] **Revisión legal** antes de publicar (PLAN §8: asesoría de inversión — Ley 21.521,
  conflicto de interés, datos personales — Ley 21.719, términos de uso de las fuentes y la
  descarga de la Circular 1.835 que salta el CAPTCHA).
- [ ] **`config/proveedores.yaml`**: URL verificada de cada AGF y AFP (sin eso no hay botón
  "ir al proveedor").
- [x] **RUT de las 7 AFP** en `config/entidades.yaml` (17-nov, desde el XML de la cartera desagregada de la SP).
- [ ] **RUT de China Construction Bank y Bank of China** en `config/entidades.yaml`.
- [ ] **Factor de reparto de dividendos** de fondos mutuos: no hay fuente identificada; los
  fondos afectados se marcan en su ficha y muestran la cifra oficial.
- [ ] **Fondos de inversión públicos (D7), APV y Cuenta 2 (D8), reclamos (D11)**: requieren
  explorar fuentes nuevas de la CMF y la SP.
- [ ] **Cuotas FFMM diarias (D2)**: decidir si vale el volumen (~700 mil filas/año) para
  medir volatilidad intra-mes.
- [ ] **Separar ingesta y servicio** (copia de solo lectura publicada) cuando haya servidor.

---

## 1. Situación actual (lo que ya existe)

### Datos (DuckDB, ~12,9M filas, ~700 MB)
- **BCCh macro** (`observations`): 1975–2026, ~64k filas (PIB, IPC, TPM, UF, IVP, UTM, IMACEC, USD/CLP, cobre, etc.) + **expectativas F089** (EEE mensual + EOF quincenal). Parser decimal corregido; datos validados.
- **CMF empresas** (`cmf_financial_statements`): ~1,5M filas, **45 períodos (201503→202603)** = 10 cierres anuales + trimestres. Orden IFRS verificado.
- **CMF bancos** (`cmf_bank_statements`): ~1,86M filas, balances/resultados mensuales con desglose por moneda, **2019→hoy** (el plan de cuentas CMF cambió en 2022: la serie evolutiva empalma por nombre y homologa unidades millones→pesos).
- **CMF seguros** (`cmf_insurer_statements`): ~581k filas, FECU trimestral **vida + generales**, 2015Q1→2026Q1, plan de cuentas propio (Circulares 2022/2050), en miles de CLP.
- **CMF intermediarios** (`cmf_broker_statements`): ~210k filas, corredores de bolsa + agentes de valores, 2015Q1→2026Q1.
- **Cartera de seguros (Circular 1.835)**: control B.8 (~72k filas) + **renta fija B.1 (~7,9M filas, la tabla más grande del repo)**, mensual 2020-01→2026-06, ambos ramos. Descarga automática.
- **SP pensiones**: cuotas (~209k, 2008+), precios (~120k, tramo 2026), **cartera (198 meses 2010-01→2026-06)** con `section`/`row_order`, y **detalle de la cartera desagregada** (`sp_cartera_detalle`, ~5,5 M filas: derivados desde 2010, emisores/riesgo/plazos desde 2022).

### Capa web (FastAPI + Jinja2 + HTMX + Plotly) — `iniciar_web.bat` / `main.py serve`
- **Panel** `/`: KPIs multi-fuente (PIB/IMACEC var, IPC, USD/CLP; top-5 bancos; rentabilidad 12m AFP + líder por rentabilidad y patrimonio; mercado) + gráficos UF y TPM.
- **Datos Banco Central** `/banco-central`: explorador del macro. Catálogo de las 52 series con cobertura y último dato (dato observado y expectativas EEE/EOF separadas), gráfico por serie con rangos, y comparador de dos series con eje compartido solo si son de la misma magnitud. Variación en puntos porcentuales cuando la serie ya es una tasa.
- **EEFF** `/eeff`: por empresa/período. Reseña del negocio, ratios (ROE/ROA/márgenes/liquidez/deuda), orden IFRS, subsecciones del balance, totales en negrita, gráfico de partidas (con desacumular) y de evolución de ratios.
- **Evolución** `/evolucion`: matriz de un estado × períodos (cierres anuales 5/10, mismo trimestre 5/10 años, 8 trimestres).
- **Comparar** `/comparar`: partida base 100 entre empresas + tabla de ratios.
- **Ranking** `/ranking`: top empresas por indicador.
- **Proyecciones** `/proyecciones`: ingresos y resultado a 1–3 años (modelo híbrido estructural+SARIMAX), fan charts con bandas empíricas, supuestos macro EEE+EOF y trayectoria estructural.
- **Banca** `/banca`: estados con desglose por moneda + gráfico de cuenta + evolución por moneda.
- **AFP** `/afp`: evolución cuota/patrimonio/participación, comparar fondos/AFP, nominal vs real, cartera por categoría (nemotécnicos traducidos con glosario oficial SP), rentabilidad neta de comisiones (con umbral de fondo mínimo).
- **Fondos** `/fondos`: por fondo — retornos del valor cuota multi-horizonte (1M→10A) por AFP + composición de cartera por AFP (secciones apiladas). Selector dinámico desde la BD, preparado para los fondos generacionales.
- **Hubs por clasificación de entidad**: `/eeff` (corporativos, seguros, intermediarios, AGF), `/proyecciones` (dos motores: híbrido para corporativos/AGF, SARIMAX para financieras) e `/inversion-institucional` (cartera de seguros, AFP, fondos).
- **Cartera de seguros** `/seguros/cartera`: asset allocation mensual (panel del período, deriva histórica, comparación entre compañías con HHI, concentración por emisor), con la codificación oficial CMF.
- **Reseñas de empresas**: `config/company_profiles.yaml` (101 curadas) + inferencia por tipo (520) = 70% cobertura; sin info → nombre como placeholder.
- Export estático (`web-preview`, `eeff-export`) en `preview/`.

### Actualización de datos
- **`Actualizar_Datos.exe`** (`actualizar.py`, compilado con `build_actualizador.py`): menú de frescura por fuente + modo desatendido (`--todo`, `--catchup`, `--source`, `--status`).
- **Catch-up por frescura completo**: las **9 fuentes** tienen sonda, incluida la cartera 1.835. El job horario del scheduler las cubre todas.

---

## 2. Decisiones y mejoras pendientes (feedback junio 2026)

### En curso (este lote)
- [x] **Quitar estados no estándar de la vista**: dejar solo **ESF C/NC** (balance), **ERFG** (resultados por función), **ERI** (resultado integral) y **EFMD** (flujo, método directo). Eliminar **ESF OL** (orden de liquidez), **ERNG** (resultados por naturaleza) y **EFMI** (flujo indirecto).
- [x] **Unificar EEFF + Evolución en una sola pestaña** (ver "harto", no poco): la vista de empresa muestra reseña + ratios + matriz evolutiva de cada estado.
- [x] **Comparativo por partida**: (a) **etiquetar** las series (en el gráfico no queda claro cuál es cuál); (b) **desacumular** las partidas de resultado/flujo (el caso BBVA AM se veía plano por mostrar acumulado).
- [x] **EFE sin totalizadores**: revisar/exponer los subtotales del flujo de efectivo (operación/inversión/financiación).

### Próximo (perfeccionar comparativo y análisis)
- [x] **Clusterizar empresas por sector/actividad** para comparaciones por grupo (además de comparación libre). Aprovechar las reseñas/tipos.
- [x] **Mix de indicadores**: combos que deben mirarse en conjunto (p. ej. ROE + deuda/patrimonio + liquidez) para detectar anomalías, "unicornios" y empresas en riesgo. (Añadido Radar de Salud).
- [x] **Ranking**: más indicadores y vistas combinadas (agregado filtrado sectorial en la vista actual).

### Banca
- [x] **Vista evolutiva de bancos** (otra pestaña): cuentas × meses. Conservar el **desglose por moneda de la CMF** mediante un panel de evolución con gráfico interactivo y tabla temporal cruzada.

### AFP
- [x] **Comparar indicadores por AFP y fondo** (no solo cuota/patrimonio).
- [x] **Cartera**: evaluar **desagregar la porción extranjera** (¿el dato SP lo permite a futuro?).

### Fases mayores (6–8)
- [x] **Modelos Predictivos Macrofundados** (primer vertical, jul-2026):
  - [x] **EEE integradas**: 18 series F089 (medianas de IPC/TPM/TC/PIB/IMACEC, horizontes móviles + LP) en `series_catalog.yaml` → `observations`.
  - [x] **Senda macro** (`models/macro_path.py`): historia efectiva + futuro interpolando anclajes EEE (1→36 meses), consistentes como exógenas.
  - [x] **Proyección EEFF** (`models/forecast.py`): SARIMAX(1,0,0)×(0,1,1,4) trimestral desacumulado con exógenas macro; ingresos + resultado neto, bandas 80/95%.
  - [x] **Vista `/proyecciones`**: fan charts + tabla de supuestos EEE + nota metodológica.
  - [x] **Backtest fuera de muestra** (`models/backtest.py`, ver [Backtest-Modelos](Backtest-Modelos)): rolling-origin con vintage EEE. Veredicto: las exógenas macro EMPEORAN el SARIMAX de flujos; el aporte del modelo es solo a 1-2 trimestres. Motiva el rediseño estructural.
  - [x] **Modelo estructural** (`models/structural.py`): activos operacionales ← macro (panel con shrinkage empresa→sector→global) → rotación → margen. A/B ganado desde 2T; robustez muy superior (medias 1.4-1.6 vs 1.8-3.4).
  - [x] **Híbrido de producción** (`models/hybrid.py`): SARIMAX puro h=1 + estructural (ancla last) h≥2, bandas empíricas del backtest. Cableado en `/proyecciones` con tabla de trayectoria estructural.
- [ ] Storytelling con LLM (Anthropic SDK + prompt caching) para explicar las proyecciones.

---

## 3. Backfills/datos pendientes
- [x] Cartera SP: backfill completo 2015-01 → 2026-01 (133 meses, ~341k filas, 132/132 disponibles en la SP, `section` resuelta en todos los años; el esquema pasó de 8 a 9 secciones en 2018 con los activos alternativos).
- [x] **Precios SP**: historia cargada 2016-11 → hoy (verificado 17-nov-2026).
- [x] **FECU anual de seguros**: `report_freq='anual'` cargado (143 mil filas, verificado 17-nov-2026).
- [x] **Cuotas FFMM desde 2018** (D1 del PLAN_MEJORAS): cargadas 2018-01 → hoy. La cartera
  FFMM (y sus derivados) empieza en 2024-07.
- [ ] **Renta fija B.1: montos de valoración de mercado sin reconciliar.** Se cargan solo los campos que validan al 100% (identificación + valor nominal, offsets 0-154). Cerrarlo requiere el catálogo de descriptores (archivo B.9).
- [x] Bancos: historia mensual **2019 → hoy** (backfill jul-2026: 1.920 reportes, 0 fallas, ~977k filas nuevas; 16 bancos × 12 meses/año). La serie evolutiva empalma el cambio de plan de cuentas 2022 (nombres + unidades millones→pesos). Extensible con `fetch-banks --history --year-start N`.

Ver **[Issues-Resueltos](Issues-Resueltos)** para el registro histórico.
