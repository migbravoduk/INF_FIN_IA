# Hoja de Ruta (Roadmap)

Estado actual del proyecto y planificación de fases futuras.

---

## Estado por fase

| Fase | Estado | Descripción | Completado |
|------|--------|-------------|-----------|
| **1 — Macro BCCh** | ✅ Activa | PIB, IPC, TPM, empleo, tipo de cambio via BDE API | 2024 |
| **2 — Series adicionales BCCh** | ✅ Activa | UF, UTM, IVP, IMACEC | 2024 |
| **3 — CMF: Empresas** | ✅ Activa | EEFF corporativos trimestrales (XBRL plano .txt) | 2024 |
| **4 — CMF: Bancos** | ✅ Activa | Balances y resultados bancarios mensuales (API SBIFv3) | 2024 |
| **SP — Fondos de Pensiones** | ✅ **Activa** | Cuotas, carteras y cinta de precios (SP) | **Junio 2025** |
| **5 — Calendarios y Alertas** | ⏳ Planificada | Fechas de publicaciones, alertas automáticas | — |
| **6 — Análisis y Proyecciones** | ⏳ Planificada | Proyecciones macro, ratios sectoriales, detección de anomalías | — |
| **7 — API + Dashboard** | ⏳ Planificada | FastAPI REST + visualización web (Chart.js / Plotly) | — |
| **8 — Storytelling / LLM** | ⏳ Planificada | Reportes narrativos automáticos generados por LLM | — |

---

## Fase SP — Detalle completado (Junio 2025)

### Nuevos datos disponibles

- **Valores Cuota Diarios (2002–presente):** valor cuota y patrimonio neto de todos los multifondos (A, B, C, D, E) para todas las AFP del sistema (Capital, Cuprum, Habitat, Modelo, PlanVital, Provida, Uno).
- **Cartera de Inversiones Mensual:** distribución porcentual y montos en CLP/USD de los activos de inversión desagregados por tipo de instrumento.
- **Cinta de Precios Diaria:** precios de cierre de todos los instrumentos de renta fija y variable chilenos publicados por la SP (últimos 5 años diarios + 5 años anteriores los miércoles).

### Datos acumulados en BD

| Tabla | Registros | Período |
|-------|-----------|---------|
| `sp_quota_values` | ~207.000 | 2002–2026 (A, B, C completos) |
| `sp_portfolio_holdings` | ~3.000 | Enero 2026 (prueba) |
| `sp_instrument_prices` | ~839 | 2026-01-02 (prueba) |

---

## Próximas prioridades

### Corto plazo (próximas semanas)

- [ ] Completar backfill de precios diarios históricos (`fetch-sp-precios --history`)
- [ ] Backfill de carteras mensuales del último año
- [ ] Validar consistencia de datos entre AFP y fechas (especialmente AFPs antiguas como Magister, Santa María)
- [ ] Añadir `fetch-sp-cartera --backfill-months N` para descargar los últimos N meses en un comando

### Mediano plazo (Fase 5)

- [ ] Calendario de publicaciones: alertar cuando la SP, CMF o BCCh publicarán datos nuevos
- [ ] Dashboard simple de valores cuota con gráficos de series temporales

### Largo plazo (Fases 6–8)

- [ ] Modelos de proyección macro (VAR, ARIMA)
- [ ] API REST (FastAPI) para consumo externo
- [ ] Integración con LLM para reportes narrativos automáticos

---

## Issues conocidos

Ver página **[Issues-Resueltos](Issues-Resueltos)** para el registro histórico de problemas técnicos resueltos.

### Issues abiertos actualmente

| # | Descripción | Prioridad |
|---|-------------|-----------|
| 1 | Backfill de fondos D y E pendiente de completarse | 🟡 Media |
| 2 | Backfill histórico de cinta de precios no ejecutado aún | 🟡 Media |
| 3 | Backfill de carteras mensuales (solo período 202601 cargado) | 🟡 Media |
| 4 | AFPs históricas (Magister, Santa María, Summa Bansander) incluidas en CSV antiguo pero no en el actual — verificar consistencia | 🔵 Baja |
