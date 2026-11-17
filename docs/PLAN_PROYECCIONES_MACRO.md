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
| C | Tasa real y pendiente como factores de la ecuación de activos | Medido, **no adoptado**; la tasa real en lugar de la TPM pasa en h2–4 (54% de pares, Δ −0,040) y falla en h1: decisión del dueño |
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
