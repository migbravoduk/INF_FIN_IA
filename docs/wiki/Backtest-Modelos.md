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

`/proyecciones` usa el **híbrido por horizonte** (`models/hybrid.py`; versión vigente en la
ronda 6, más abajo):
- h=1 → SARIMAX(1,0,0)×(0,1,1,4) sin exógenas.
- h≥2 → estructural con ancla `last` (desde la ronda 6: promedio con el SARIMAX en h2–3).
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

## Ronda 6: decisiones del dueño y atípicos (17-nov-2026)

Encargo: resolver las decisiones 1–4 del §6 de [`PLAN_SEMANA.md`](../PLAN_SEMANA.md) y aislar
atípicos en la calibración de los modelos generales (los específicos de bancos y seguros
quedan para después).

**Diseño contra el sobreajuste.** La muestra se duplica a 80 empresas (8 orígenes, h1–4,
640 pares por celda): las 40 de siempre ("vistas", sobre las que se formularon las preguntas)
y las 40 siguientes por materialidad ("nuevas", nunca usadas). Además de la regla de
`compare_bt` sobre las 80, un cambio entra solo si el Δ MASE medio tiene el mismo signo en
ambas mitades. Las decisiones se toman una dimensión a la vez y no se elige "la mejor fila"
de la grilla. `compare_bt` pasa a descartar los empates (test de signo): la limpieza y el
tope solo tocan parte de las series, y contar los empates como derrotas los castigaba por no
hacer nada donde no hace falta; no cambia ninguna decisión previa (allí no había empates).

Corridas: `scratch/bt_r6.csv` (todos los specs, factores actuales, 449 s), `bt_r6_v2.csv`
(estructural con V2), `bt_r6_v2m.csv` (ancla de margen separada), `bt_bancos_r6.csv`,
`bt_seguros_r6.csv`; análisis en `scratch/decisiones_r6.py` (no versionados).

**Nuevos componentes** (`models/robusto.py`):

- `limpiar_outliers`: STL robusto; atípico = residuo > 4 desvíos robustos (1,4826·MAD) **y
  aislado** (vecinos < 2 desvíos). Lo segundo distingue un atípico aditivo de un cambio de nivel
  (Chen y Liu 1993): sin esa condición, una fusión hacía marcar los trimestres del quiebre.
  Umbral fijado a priori (≈ `tsclean` de R), no ajustado al backtest.
- `acotar`: si la proyección sale de [mín − rango, máx + rango] de la historia, se reemplaza
  por naive estacional (flujos) o último valor (stocks).
- Ancla `median4` del ratio y `ancla_margen` separada de la de rotación (`structural.py`).

| Decisión | Comparación (h2–4 salvo indicación) | Pares | Δ medio | Vistas / nuevas (Δ) | Veredicto |
|---|---|---|---|---|---|
| 4 · Tope | SARIMAX acotado vs sin tope, h1–4 | se activa en 1 de 640 | — | — | **adoptado** (seguros: MASE máx 6·10⁸ → 10) |
| 1 · V2 (tasa real 5a por TPM) | ingresos, ancla med4 | 53,4% | −0,032 | −0,058 / −0,006 | **adoptado** |
| 1 · V2 | ganancia (otra partida) | 46,5% | +0,001 | +0,002 / +0,001 | dentro de tolerancia |
| 2 · Ancla rotación med4 vs last (con V2) | ingresos | 52,6% | −0,006 | **+0,014** / −0,026 | no (mitades se contradicen) |
| 2 · Ancla margen med4 (rotación last, V2) | ganancia | 51,2% | −0,013 | −0,019 / −0,007 | **adoptado** |
| 3 · h2: promedio SARIMAX + estructural | ingresos, h2 | 59,5% | −0,091 | −0,028 / −0,155 | **adoptado** |
| 3 · h2: promedio | ganancia, h2 | 57,7% | −0,067 | −0,057 / −0,078 | **adoptado** |
| 3 · extensión a h3 | ingresos / ganancia, h3 | 54,2% / 56,7% | −0,075 / −0,069 | consistentes | **adoptado** |
| 3 · extensión a h4 | ingresos / ganancia, h4 | 50,9% / 53,1% | +0,012 / −0,043 | se contradicen | no |
| Atípicos · SARIMAX h1 | ingresos / ganancia, h1 | 51,5% / 51,5% | −0,003 / −0,011 | se contradicen | no |
| Atípicos · estructural | ganancia sobre ancla med4 | 49,2% | −0,011 | +0,017 / −0,040 | no (la mediana ya los cubre) |
| Atípicos · estructural | ingresos sobre V2 | 55,2% | −0,012 | −0,026 / +0,003 | no (h4 +0,029) |
| Atípicos · bancos | resultado (uf), h1/3/6/12 | 57,6% | −0,041 | — | no (h3 +0,037) |
| Atípicos · seguros | resultado, h1–4 | 49,4% | 0,000 | — | no |

**Híbrido nuevo vs anterior** (compuesto con las mismas filas; `scratch/bt_r6_hibridos.csv`):

| Partida | h | anterior (mediana / media) | nuevo (mediana / media) |
|---|---|---|---|
| Ingresos | 1 | 0,570 / 1,040 | 0,570 / 0,960 |
| Ingresos | 2 | 0,837 / 1,280 | **0,647** / 1,081 |
| Ingresos | 3 | 0,806 / 1,288 | **0,682** / 1,176 |
| Ingresos | 4 | 0,786 / 1,318 | **0,746** / 1,271 |
| Ganancia | 1 | 0,690 / 1,085 | 0,690 / 1,065 |
| Ganancia | 2 | 0,856 / 1,352 | **0,714** / 1,201 |
| Ganancia | 3 | 0,802 / 1,309 | **0,701** / 1,218 |
| Ganancia | 4 | 0,792 / 1,324 | 0,788 / 1,346 |

En pares h1–4: ingresos 56,4% (Δ −0,110; vistas −0,142, nuevas −0,077); ganancia 53,5%
(Δ −0,060; −0,039 / −0,080). **ADOPTAR**. `BAND_QUANTILES` regenerado desde el compuesto
(640 pares por celda): las bandas del 95% de ingresos se angostan ~45% (h2: 5,48 → 3,01).
Configuración en `models/hybrid.PRODUCCION`; bancos y seguros en `simple_forecast.ROBUSTO_SIMPLE`
(tope sí, limpieza no). Las vistas marcan los atípicos detectados (✕) como información.
