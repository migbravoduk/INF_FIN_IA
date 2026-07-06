# Hoja de Ruta (Roadmap)

> Situación actual y plan. Última actualización: junio 2026 (rama `feature/web-api-skeleton`).

---

## 1. Situación actual (lo que ya existe)

### Datos (DuckDB, ~1,9M filas, ~130 MB)
- **BCCh macro** (`observations`): 1975–2026, ~53k filas (PIB, IPC, TPM, UF, IVP, UTM, IMACEC, USD/CLP, cobre, etc.). Parser decimal corregido; datos validados.
- **CMF empresas** (`cmf_financial_statements`): ~1,5M filas, **45 períodos (201503→202603)** = 10 cierres anuales + trimestres. Orden IFRS verificado.
- **CMF bancos** (`cmf_bank_statements`): balances/resultados mensuales con desglose por moneda.
- **SP pensiones**: cuotas (~207k, 2008+), precios (~96k), cartera (1 período: 2026-01).

### Capa web (FastAPI + Jinja2 + HTMX + Plotly) — `iniciar_web.bat` / `main.py serve`
- **Panel** `/`: KPIs multi-fuente (PIB/IMACEC var, IPC, USD/CLP; top-5 bancos; rentabilidad 12m AFP + líder por rentabilidad y patrimonio; mercado) + gráficos UF y TPM.
- **EEFF** `/eeff`: por empresa/período. Reseña del negocio, ratios (ROE/ROA/márgenes/liquidez/deuda), orden IFRS, subsecciones del balance, totales en negrita, gráfico de partidas (con desacumular) y de evolución de ratios.
- **Evolución** `/evolucion`: matriz de un estado × períodos (cierres anuales 5/10, mismo trimestre 5/10 años, 8 trimestres).
- **Comparar** `/comparar`: partida base 100 entre empresas + tabla de ratios.
- **Ranking** `/ranking`: top empresas por indicador.
- **Banca** `/banca`: estados con desglose por moneda + gráfico de cuenta.
- **AFP** `/afp`: evolución cuota/patrimonio/participación, comparar fondos/AFP, nominal vs real, cartera por categoría.
- **Reseñas de empresas**: `config/company_profiles.yaml` (101 curadas) + inferencia por tipo (520) = 70% cobertura; sin info → nombre como placeholder.
- Export estático (`web-preview`, `eeff-export`) en `preview/`.

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
  - [ ] Ampliar partidas (márgenes intermedios), backtesting fuera de muestra, y evaluar VARX/ML.
- [ ] Storytelling con LLM (Anthropic SDK + prompt caching) para explicar las proyecciones.

---

## 3. Backfills/datos pendientes
- [ ] Cartera SP: solo 2026-01 cargado → backfill de meses para series de cartera.
- [ ] Precios SP: nivelado hasta ~2026-06 (tope de 10 días hábiles por corrida del catch-up).
- [x] Bancos: historia mensual completa ingestada y disponible para las vistas evolutivas.

Ver **[Issues-Resueltos](Issues-Resueltos)** para el registro histórico.
