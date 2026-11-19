# Plan: proyecciones macrofundadas con curvas de tasas

> Iniciado el 2026-11-16, después de incorporar las curvas (`db/curves.py`, `/curvas`).
> Objetivo: fortalecer el componente macro de las proyecciones de EEFF (`models/`) usando
> curvas, inflación implícita y spreads, **solo donde el backtest fuera de muestra lo
> justifique** (regla de julio 2026: nada entra a producción sin ganar en el backtest).

Referencias: [`docs/wiki/Backtest-Modelos.md`](wiki/Backtest-Modelos.md) (resultados por ronda),
[`CONTEXTO.md`](../CONTEXTO.md) (gotchas), [`docs/wiki/Hoja-de-Ruta.md`](wiki/Hoja-de-Ruta.md) §0
(pendientes consolidados del proyecto).

Cómo se mide: `python -m models.backtest --solo-estructural --companies 40 --origins 8 --horizon 4`
(40 empresas con más ingresos, 8 orígenes trimestrales, h = 1–4, senda macro *vintage*). MASE:
error / error del naive estacional dentro de muestra; < 1 es mejor que repetir el dato de hace un año.

---

## Estado

| Paso | Qué | Estado |
|---|---|---|
| B | Ganancia = operacional + no operacional (reajuste UF, diferencias de cambio) | **Hecho, en producción** |
| A | Anclas de mercado en la senda (TPM implícita, inflación implícita) | Medido, **no adoptado** (`fuente=` en `build_macro_path`; mercado y mezcla < 50% de pares) |
| C | Tasa real y pendiente como factores de la ecuación de activos | **Tasa real 5a en lugar de la TPM: adoptada** (ronda 6, 17-nov); pendiente y spread no |
| D | Bancos y aseguradoras | Bandas calibradas en ambos; **bancos: exposición a la UF adoptada** (52% de pares, Δ −0,021); seguros de vida no pasa |
| B′ | Empresas que informan por naturaleza (ERNG) | **Hecho**: panel 402 → 483 empresas |

Resultados: [`docs/wiki/Backtest-Modelos.md`](wiki/Backtest-Modelos.md) rondas 5 y "Bancos y
seguros"; resumen en [`docs/INFORME_SEMANA.md`](INFORME_SEMANA.md).

**Ejecución de lo pendiente:** [`docs/PLAN_SEMANA.md`](PLAN_SEMANA.md) (bloques S1–S6, con la
regla de adopción programada en `models/compare_bt.py`).

**Matiz sobre B:** restringido a los horizontes donde manda en producción (2–4 trimestres),
B gana en 51,8% de los pares (z ≈ 1,1), con Δ medio −0,058 y medianas −0,06 a −0,09. La
evidencia es moderada y descansa en medias y medianas; se revalida en la corrida completa
del bloque S3.

---

## B · Resultado no operacional (hecho)

**Qué se hizo** (`models/nonop.py`, `forecast_structural(nonop=True)`):

- Ganancia = operacional + (1 − 27%) × (reajuste por UF + diferencias de cambio). Ambas
  partidas son antes de impuestos; la ganancia es después.
- El margen de la cadena estructural se mide sobre la ganancia **operacional**.
- Reajuste ≈ k_UF × inflación UF del trimestre; diferencias de cambio ≈ k_TC × Δlog(dólar).
  k = MCO sin intercepto con los últimos 12 trimestres hasta el origen (mínimo 6).
- Inflación trimestral futura: tendencia de la senda de inflación a 12 meses + estacionalidad
  promedio de 5 años; la UF sigue al IPC con un mes de rezago. (Se probó la recursión exacta
  con efecto base y se descartó: repetía cada año los shocks puntuales del último.)
- Cuenta de diferencias de cambio: "Diferencias de cambio" (2015–2019) y "Ganancias
  (pérdidas) de cambio en moneda extranjera" (2020→) se suman.
- `/proyecciones` muestra la exposición estimada y el no operacional proyectado por trimestre.

**Resultado** (ganancia, 40 empresas × 8 orígenes 202409–202606, 1.280 pares):

| h | estr_last | **estr_last_nop** | sin macro (ablación) | snaive |
|---|---|---|---|---|
| 1 | 1,017 | **0,927** | 0,945 | 0,940 |
| 2 | 1,054 | **0,960** | 0,938 | 0,993 |
| 3 | 0,945 | **0,889** | 0,881 | 0,944 |
| 4 | 0,909 | **0,835** | 0,818 | 0,897 |

MASE mediana. En pares: `nop` gana al estructural simple en 53% de los casos (error medio
−0,068); y la proyección con inflación/dólar gana a "limpiar el margen y proyectar cero" en
56–58% de los casos en **todos** los horizontes (≈ 4 desvíos de la moneda al aire). Las medianas
de la ablación se ven parecidas porque la mediana de una distribución no es la mediana de las
diferencias. Bandas empíricas de ganancia h ≥ 2 recalculadas (`BAND_QUANTILES`): 80% más
angostas (2,20 vs 2,35 en h = 2).

**Pendiente de B**

- [ ] **Costos financieros** (596 empresas): hoy quedan dentro del margen operacional. Medirlos
      como deuda × tasa (curva UF + spread propio) aporta poco mientras la deuda sea a tasa fija;
      probarlo junto con C.
- [x] **Empresas con estado de resultados por naturaleza (ERNG)**: hecho el 16-nov (ERFG o
      ERNG, prefiere ERFG); +81 empresas en el panel, Aguas Andinas incluida.
- [x] Re-correr el SARIMAX (h = 1) en la misma muestra: hecho (Ronda 5); el híbrido se mantiene.

---

## A · Anclas de mercado en la senda (pendiente)

Idea: agregar a `models/macro_path.py` la TPM implícita (`db.curves.tpm_implicita`) y la
inflación implícita (`compensacion_inflacionaria`), corregidas por el premio por plazo medio
(diferencia histórica mercado − encuesta), como anclas adicionales o alternativas.
Comparar en backtest: solo encuestas / solo mercado / mezcla. Las curvas no se revisan, así que
el *vintage* es limpio.

## C · Factores financieros en la ecuación de activos (pendiente)

Candidatos: tasa real swap UF 5 años (costo del capital), pendiente nominal 10a − 2a (indicador
adelantado), spread bancario (condiciones de crédito). Su senda futura sale de las forwards de
la propia curva. A/B en ingresos h = 2–4.

## D · Bancos y aseguradoras (pendiente)

`/proyecciones/bancos` y `/seguros` son SARIMAX sin macro. Margen bancario ← inflación (descalce
UF) y pendiente; aseguradoras de vida ← tasa de reinversión UF larga. Requiere su propio backtest.

### D · Modelos de bancos y seguros con driver de crecimiento (análisis 17-nov-2026, sin código en producción)

**Idea:** colocaciones por cartera y moneda (bancos) y reservas técnicas (seguros), en variación
real 12m, impulsadas por el IMACEC (historia) empalmado con la expectativa de PIB de la EEE
(futuro, `act_yoy` de `models/macro_path.py`). Colocaciones / reservas arrastran el resto del
balance y el resultado.

**Datos hallados**
- BCCh, colocaciones reales (IPC ref. 2023) desde 1986: `F022.CEM.STO.TP.Z.P96R23.M` (comercial),
  `F022.CON.STO.TP.Z.P96R23.M` (consumo), `F022.VIV.STO.TP.Z.P96R23.M` (vivienda),
  `F022.CTO.STO.TP.Z.P96R23.M` (total). No están en el catálogo
  ni en la base: bajadas a `scratch/coloc_reales_bcch.pkl` con `scratch/baja_coloc.py`.
- CMF bancos: colocaciones por cartera (cuentas `1302000/1304000/1305000` hasta 202112 en millones;
  `145000000/146000000/148000000` desde 202201 en pesos) y por moneda (`val_clp_no_reaj`,
  `val_clp_reaj_ipc`, `val_clp_reaj_tc` + `val_extranjera`). Empalman sin salto (`scratch/explora_colocaciones.py`).
- Seguros: reservas técnicas `5.21.31.00` trimestral 2015-03→2026-06, 72 compañías. No probado.

**Pruebas (sistema, fuera de muestra, orígenes 2012-01→2025-09, `scratch/oos_sistema*.py`)**
1. IMACEC rezagado 6 meses explica 40–60% del crecimiento real de colocaciones comerciales,
   consumo y total en 2010–2019 (elasticidad ≈ 1). Con 2020–21 (retiros de AFP, FOGAPE) casi no
   hay relación (R² 0,01–0,12). Vivienda crece ≈ 8% real sin ciclo.
2. Modelo de nivel (g = a + b·IMACEC, con o sin AR del residuo) PIERDE contra persistencia del
   crecimiento: MAE total 4,0 vs 0,75 pp a 1 mes; 4,3 vs 2,7 a 12 meses. El IMACEC mensual es ruidoso.
3. Corrección sobre persistencia (g_{t+h} = g_t + b·Δ IMACEC suavizado 6m, rezago 6m, b estimado
   sin 2020–21): MAE vs persistencia, total −7% (h3), −14% (h6), 0% (h12); comercial −5/−10/0;
   consumo −6/−5/−15; vivienda 0. Ganancia modesta.
   **Sesgo a favor (revisión 17-nov):** la prueba usa el IMACEC EFECTIVO de t+h−6, no la senda EEE
   que habría existido en el origen. Para h ≤ 6 eso es casi justo (solo ~1 mes de rezago de
   publicación del IMACEC); para h = 12 es mirar el futuro, así que la mejora a 12 meses
   (consumo −15%) está sobrestimada. Repetir con la senda vintage (`build_macro_path(as_of=...)`)
   antes de decidir.

**Diseño propuesto (pendiente de decisión del usuario)**
- Banco × cartera × moneda: crec. real = el actual + b·ΔIMACEC suavizado (b por cartera; vivienda
  solo tendencia) + desviación propia del banco vs sistema que se desvanece; FX en USD + tc de la senda.
- Total activos = colocaciones ÷ razón colocaciones/activos. Resultado = mismo mes del año anterior
  × crecimiento del balance (+ exposición UF ya adoptada).
- Seguros: igual con reservas técnicas reales.
- Decide el backtest con `models/compare_bt.py` (16 bancos × 18 orígenes; base `sarimax`/`uf`).
- Ganancia esperada: 5–15% de error a 3–6 meses; nula a 12 en colocaciones totales.
