# Backtest de modelos predictivos

> Primer backtest fuera de muestra de las proyecciones macrofundadas (julio 2026).
> Reproducir: `python -m models.backtest --companies 40 --origins 8 --horizon 4`

## Metodología

- **Rolling origin**: para cada origen T (8 trimestres, re-ajuste en cada uno), el modelo
  solo ve datos hasta T y proyecta h = 1..4 trimestres.
- **Vintage EEE honesto**: las exógenas futuras usan la senda de expectativas tal como se
  veía ~2 meses después del cierre de T (`build_macro_path(as_of=...)`) — la encuesta que
  un analista tenía cuando el EEFF de T recién se publicaba. Sin fuga de información.
- **Universo**: 40 empresas de mayor materialidad (mediana |ingresos|) con historia
  suficiente; partidas ERFG desacumuladas (ingresos y ganancia neta).
- **Métrica**: MASE — |error| / media|y_t − y_{t−4}| del entrenamiento. Escala-libre
  (agregable entre monedas); < 1 = mejor que el naive estacional.
- **Specs**: `eee` (SARIMAX + exógenas macro vintage, el modelo de producción),
  `noexog` (mismo SARIMAX sin exógenas), `snaive` (y[T+h] = y[T+h−4]).

## Resultados (MASE mediana; n=320 por celda)

### Ingresos de actividades ordinarias

| h | eee | noexog | snaive |
|---|------|--------|--------|
| 1 | 0.64 | **0.60** | 0.86 |
| 2 | 0.98 | **0.78** | 0.86 |
| 3 | 1.05 | **0.83** | 0.85 |
| 4 | 1.09 | 0.86 | **0.77** |

### Ganancia (pérdida)

| h | eee | noexog | snaive |
|---|------|--------|--------|
| 1 | 0.99 | **0.80** | 0.94 |
| 2 | 1.11 | **0.87** | 0.99 |
| 3 | 1.09 | **0.94** | 1.00 |
| 4 | 1.14 | **0.93** | 0.99 |

(Las medias son bastante peores que las medianas — 1.3 a 3.4 — por colas pesadas:
en algunas empresas/trimestres el modelo explota.)

## Conclusiones

1. **Las exógenas macro empeoran el modelo**: `eee` pierde contra `noexog` en las 8
   celdas. Con ~40 observaciones por serie, 3 regresores macro añaden varianza de
   estimación sin retorno predictivo. El macrofundamento vía regresión directa sobre
   flujos trimestrales firm-level no funciona.
2. **El SARIMAX aporta solo a corto plazo** (h=1–2 en ingresos). A un año el naive
   estacional es mejor mediana.
3. **La ganancia neta es casi impronosticable** con series de tiempo puras (todo ≈ 1).

## Implicación de diseño (próximo paso)

Redirigir el macrofundamento al **enfoque estructural**: proyectar los **activos
productivos** en función de los factores macro (stocks: suaves, sin desacumular,
relaciones más estables) y derivar los resultados manteniendo las **relaciones de
productividad de activos** (rotación, márgenes). El arnés (`models/backtest.py`)
queda listo para comparar ese enfoque contra estas mismas cifras.

Detalle completo de la corrida: `scratch/backtest_results.csv` (no versionado).
