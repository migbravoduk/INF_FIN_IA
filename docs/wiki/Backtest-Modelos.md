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

## Ronda 4: resultado no operacional separado (nov-2026)

Plan y detalle: [`docs/PLAN_PROYECCIONES_MACRO.md`](../PLAN_PROYECCIONES_MACRO.md) §B.
Ganancia = operacional + (1−27%)·(reajuste UF + diferencias de cambio); el margen se mide
sobre la operacional y el no operacional se proyecta con la exposición de cada empresa ×
senda de inflación y TC (`models/nonop.py`). Orígenes 202409–202606 (más recientes que las
rondas 1–3: las cifras absolutas no son comparables con las de julio).

| h | estr_last | estr_last_nop | ablación (nop, proyección 0) | snaive |
|---|---|---|---|---|
| 1 | 1.017 | **0.927** | 0.945 | 0.940 |
| 2 | 1.054 | 0.960 | **0.938** | 0.993 |
| 3 | 0.945 | 0.889 | **0.881** | 0.944 |
| 4 | 0.909 | 0.835 | **0.818** | 0.897 |

En pares (1.280): `estr_last_nop` gana a `estr_last` en 53% (Δ media −0.068) y a la ablación
en 56–58% en todos los horizontes. **Producción**: el híbrido usa `nonop=True` en h≥2 y
`BAND_QUANTILES` de ganancia h=2–4 se actualizaron con esta corrida (`scratch/bt_nop.csv`).

## Ronda 5: corrida completa con empresas ERNG (nov-2026)

Plan: [`docs/PLAN_SEMANA.md`](../PLAN_SEMANA.md) S1–S4. Primero cambia la muestra: el modelo
lee el estado de resultados por función (ERFG) o, si la empresa solo informa por naturaleza,
ERNG. 11 de las 40 empresas son nuevas (Enel ×3, Entel, Telefónica ×2, Aguas Andinas…), así
que las cifras no son comparables con las rondas anteriores. 40 empresas × 8 orígenes × h1–4
(320 pares por celda), todos los specs, 136 s (`scratch/bt_full.csv`).

| Partida | h | noexog | eee | estr_last | estr_m4 | estr_last_nop | estr_m4_nop | snaive |
|---|---|---|---|---|---|---|---|---|
| Ingresos | 1 | **0.573** | 0.599 | 0.729 | 1.736 | | | 0.909 |
| Ingresos | 2 | 0.760 | 0.883 | 0.768 | **0.744** | | | 0.909 |
| Ingresos | 3 | 0.827 | 0.935 | 0.746 | **0.698** | | | 0.831 |
| Ingresos | 4 | 0.907 | 1.055 | 0.721 | **0.704** | | | 0.799 |
| Ganancia | 1 | **0.727** | 0.873 | 0.919 | 1.538 | 0.867 | 1.528 | 0.844 |
| Ganancia | 2 | **0.856** | 0.973 | 0.925 | 0.901 | 0.894 | 0.868 | 0.859 |
| Ganancia | 3 | 0.830 | 0.898 | 0.872 | 0.878 | 0.828 | **0.821** | 0.850 |
| Ganancia | 4 | 0.853 | 0.887 | 0.834 | 0.834 | 0.794 | **0.786** | 0.828 |

MASE mediana. El híbrido (noexog h1; `estr_last` / `estr_last_nop` h≥2) se mantiene: noexog
gana h1 por más de 0,02. Pendiente de decidir (no se cambió producción): en h2 `noexog` queda
levemente mejor en mediana, y el ancla `m4` supera a `last` en esta muestra.
`BAND_QUANTILES` regenerado entero con esta corrida.

Comparaciones en pares contra la misma base (`compare_bt`, h2–4; regla en PLAN_SEMANA §2):

| Cambio | Cuenta | Pares ganados | Δ MASE medio | Veredicto |
|---|---|---|---|---|
| Senda de mercado (TPM e inflación implícitas en swaps) | ingresos | 45,7% | 0,000 | no |
| Senda mezcla 50/50 encuestas–mercado | ingresos | 47,0% | −0,005 | no |
| V1 + tasa real swap UF 5a | ingresos | 49,5% | −0,010 | no |
| V2 tasa real 5a en lugar de TPM | ingresos | **54,3%** | **−0,040** | pasa h2–4, falla h1–4 |
| V3 + pendiente 10a−2a | ingresos | 52,1% | +0,036 | no |
| V4 + spread depósito − swap 1a | ingresos | 46,9% | +0,133 | no |

## Bancos y seguros (nov-2026)

Arnés propio: `models/backtest_simple.py` (bancos 16 × 18 orígenes mensuales, h = 1, 3, 6, 12;
seguros 73 × 8 trimestrales, h = 1–4). MASE mediana:

| Clase · partida | h | sarimax | snaive | uf |
|---|---|---|---|---|
| Bancos · resultado | 1 / 3 / 6 / 12 | 0.557 / 0.632 / 0.682 / 0.951 | 0.748 / 0.713 / 0.730 / **0.748** | 0.563 / **0.613** / **0.629** / 0.901 |
| Bancos · activos | 1 / 3 / 6 / 12 | 0.188 / 0.289 / 0.369 / **0.612** | 0.194 / 0.275 / 0.369 / 0.795 | |
| Seguros · resultado | 1 / 2 / 3 / 4 | 0.861 / 0.815 / 0.873 / 0.930 | 0.854 / 0.851 / 0.906 / 0.931 | (solo vida) |
| Seguros · activo | 1 / 2 / 3 / 4 | **0.293** / **0.401** / **0.509** / **0.706** | 0.380 / 0.599 / 0.816 / 1.056 | |

`uf` = resultado − k·π_UF con el SARIMAX + k·π de la senda (k: MCO con intercepto, 36 meses).
Bancos h3/6/12 en pares: 51,7%, Δ −0,021 → **adoptado**. Seguros de vida h2–4: 49,5%, Δ −0,026
→ no adoptado. Bandas de ambas vistas: cuantiles de esta corrida (`BANDAS_SIMPLE`).
