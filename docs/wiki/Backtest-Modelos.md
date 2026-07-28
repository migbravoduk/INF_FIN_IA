# Backtest de modelos predictivos

> Primer backtest fuera de muestra de las proyecciones macrofundadas (julio 2026).
> Reproducir: `python -m models.backtest --companies 40 --origins 8 --horizon 4`

## Metodología

- **Rolling origin**: para cada origen T (8 trimestres, re-ajuste en cada uno), el modelo
  solo ve datos hasta T y proyecta h = 1..4 trimestres.
- **Vintage de encuestas honesto**: las exógenas futuras usan la senda de expectativas tal
  como se veía ~2 meses después del cierre de T (`build_macro_path(as_of=...)`) — las
  encuestas que un analista tenía cuando el EEFF de T recién se publicaba. Sin fuga de
  información. Desde jul-2026 la senda ancla en **EEE + EOF** (doble encuesta oficial del
  BCCh; ningún factor macro se proyecta internamente — solo se interpola entre medianas
  publicadas).
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

## Ronda 2: A/B contra el modelo ESTRUCTURAL (jul-2026)

Implementado `models/structural.py` (activos operacionales ← macro con shrinkage
empresa→sector→global; resultados ← rotación y margen con reversión suave). Dos
anclas de ratio comparadas: `estr_m4` (móvil 4T) y `estr_last` (último valor).

### Ingresos (MASE mediana)

| h | eee | noexog | snaive | estr_last | estr_m4 |
|---|------|--------|--------|-----------|---------|
| 1 | 0.64 | **0.60** | 0.86 | 0.90 | 1.90 |
| 2 | 0.98 | 0.78 | 0.86 | 0.84 | **0.79** |
| 3 | 1.05 | 0.83 | 0.85 | 0.85 | **0.83** |
| 4 | 1.09 | 0.86 | **0.77** | 0.79 | 0.84 |

**Robustez (MASE media, h2-4)**: estructural 1.4–1.6 vs SARIMAX 1.8–3.4 — la cadena
estructural acota los errores; no explota. En ganancia a 4T, `estr_m4` es el mejor
spec de todos (mediana 0.886).

### Conclusiones ronda 2

1. El ancla `last` ("mantener la relación de productividad" literal) domina a `m4`
   en h=1 (0.90 vs 1.90): el promedio móvil mezcla trimestres pre/post saltos de
   activos (M&A — caso CMPC 202512, +25% de activos operacionales).
2. División del trabajo por horizonte: momentum (SARIMAX puro) a 1T; estructura
   desde 2T.
3. El macrofundamento funciona EN LA ECUACIÓN DE ACTIVOS (estructural), no como
   exógena de flujos (spec `eee`, el peor del cuadro).

## Modelo de PRODUCCIÓN (decisión jul-2026)

`/proyecciones` usa el **híbrido por horizonte** (`models/hybrid.py`):
- h=1 → SARIMAX(1,0,0)×(0,1,1,4) sin exógenas.
- h≥2 → estructural con ancla `last`.
- **Bandas empíricas**: cuantiles 80/95 de |error|/escala medidos en este backtest,
  reescalados por la volatilidad de cada empresa (`BAND_QUANTILES` en hybrid.py —
  regenerar si se re-corre el backtest con specs nuevos).

## Ronda 3: anclaje dual EEE + EOF (jul-2026)

Se añadieron los anclajes de la **Encuesta de Operadores Financieros** (EOF,
quincenal, visión de mercado) a la senda: TPM (RPM/3m/6m/12m/24m), inflación
(12m/13-24m) y TC (28d). Re-corrida completa del A/B: el veredicto se mantiene y
los specs estructurales **mejoran levemente en todas las celdas** (Δ mediana hasta
−0.015; `estr_last` a 4T queda en 0.773). `BAND_QUANTILES` de hybrid.py
actualizados a esta corrida. Nota: `F089.EOF.TC.7MA` está descontinuada (2018) y
no se usa.

Detalle completo de las corridas: `scratch/backtest_results.csv`,
`scratch/backtest_ab.csv` y `scratch/backtest_ab2.csv` (no versionados).
