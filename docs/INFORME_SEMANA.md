# Informe de la semana — upgrades de proyecciones (ejecutado el 16-nov-2026)

Plan: [`PLAN_SEMANA.md`](PLAN_SEMANA.md). Bloques S1–S4 y S6 ejecutados en una sesión; S5
(frontera eficiente, opcional) no iniciado. Regla de adopción: `models/compare_bt.py` (§2 del
plan). Cifras: pares (misma empresa, origen y horizonte), h2–4 salvo indicación.

## Qué cambió en producción

| Cambio | Evidencia | Dónde se ve |
|---|---|---|
| Empresas que informan resultados por naturaleza (ERNG) entran al modelo | Panel 402 → 483 empresas; 11 de las 40 del backtest son nuevas | `/proyecciones` (p. ej. Aguas Andinas, Enel, Entel) |
| Bandas del híbrido regeneradas en la muestra nueva | 320 pares por celda, corrida completa de 136 s | `/proyecciones` |
| Bandas calibradas fuera de muestra en bancos y seguros | Bancos 16 × 18 orígenes; seguros 73 × 8 | `/proyecciones/bancos`, `/seguros` (ya no dicen "no calibradas") |
| Resultado de bancos = base + exposición a la UF | 51,7% de pares, Δ MASE −0,021, medianas −0,02 a −0,05 (h3/6/12) | `/proyecciones/bancos`: línea "exposición a la UF" |

## Qué se midió y no pasó

| Cambio | Resultado | Estado del código |
|---|---|---|
| Senda anclada a swaps (TPM e inflación implícitas) | Ingresos 45,7% de pares, Δ 0,000; ganancia 50,6%, Δ +0,004 | `build_macro_path(fuente=...)`, `backtest --senda` |
| Senda mezcla 50/50 | Ingresos 47,0%, Δ −0,005; ganancia h2 +0,041 | ídem |
| Tasa real swap UF 5a en lugar de TPM (V2) | Ingresos **54,3%, Δ −0,040**, pero h1 empeora +0,033 → falla la condición extra h1–4 | `backtest --factores`; **decisión tuya** |
| Tasa real 5a además de TPM (V1) | 49,5%, Δ −0,010 | ídem |
| Pendiente 10a − 2a (V3) | 52,1%, Δ +0,036 | ídem |
| Spread depósito − swap 1a (V4) | 46,9%, Δ +0,133 | ídem |
| Exposición a la UF en seguros de vida | 49,5% de pares, Δ −0,026 (medianas mejoran) | spec `uf` en `backtest_simple` |

## Actualización 17-nov: decisiones 1–4 resueltas (ronda 6)

Medido en 80 empresas (las 40 de siempre + 40 nuevas como confirmación). El híbrido de
producción cambió a: SARIMAX en h1 (con tope si explota) · promedio SARIMAX + estructural en
h2–3 · estructural en h4+, con la tasa real swap UF 5a en lugar de la TPM y el margen anclado en
la mediana de 4 trimestres. Contra el anterior gana en 56% de los pares en ingresos (MASE −0,11)
y 54% en ganancia (−0,06), en ambas mitades de la muestra. Bandas regeneradas (más angostas).
Atípicos: se detectan y se marcan en las vistas, pero eliminarlos antes de calibrar no pasó la
regla en ningún modelo. Detalle: `docs/wiki/Backtest-Modelos.md`, Ronda 6.

## Lo que necesitaba tu decisión al 16-nov (puntos 1–4 resueltos arriba)

1. **V2 (tasa real en lugar de TPM).** Pasa en h2–4 con la evidencia más fuerte de la semana
   (z = 2,6). Falla solo en h1, que en producción cubre el SARIMAX. Activarlo es un cambio de
   una línea.
2. **Ancla del ratio:** en la muestra nueva `estr_m4` tiene mejor mediana que `estr_last` en
   ingresos h2–4 (0,744/0,698/0,704 frente a 0,768/0,746/0,721). Falta la comparación en pares.
3. **h2:** `noexog` quedó levemente mejor que el estructural en mediana (ingresos 0,760 vs
   0,768; ganancia 0,856 vs 0,894). No se tocó el híbrido.
4. **Tope para SARIMAX que explota** (un caso en seguros con MASE de 10⁸) y `snaive` en
   bancos desde h ≥ 9 (le gana al SARIMAX en h12: 0,748 vs 0,951).
5. Pendientes previos sin cambio: revisión legal, URL de proveedores, RUT de AFP y bancos
   extranjeros, factor de dividendos, fuentes nuevas, ingesta/servicio.

## Validación

`pytest`: 50 pasan (46 + 4 nuevos). `main.py validate`: 0 errores, 81 avisos (igual que el
16-nov). Datos al día (`catchup`); respaldo previo en `data/backups/`.
`Actualizar_Datos.exe` recompilado (cambió `db/curves.py`).
