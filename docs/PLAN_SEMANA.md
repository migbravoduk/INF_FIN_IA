# Plan de la semana — pendientes y upgrades (17–21 nov 2026)

> Escrito el 2026-11-16 por quien lideró las fases anteriores, para que **Opus 5.5 en esfuerzo
> medio** lo ejecute sin supervisión durante la semana. Las decisiones de diseño ya están
> tomadas aquí: quien ejecuta **no rediseña, no explora alternativas y no pregunta**; sigue los
> pasos, aplica la regla de adopción y deja registro. Lo que no esté en este plan se anota en
> "Para el regreso" (§6) y no se hace.

Rama: `feature/web-api-skeleton` (nunca `main`). Intérprete: `.\.venv\Scripts\python.exe`,
siempre con `PYTHONIOENCODING=utf-8`.

---

## 0. Protocolo (leer en cada sesión; es corto a propósito)

**Una sesión nueva por bloque.** Al empezar se lee solo: este §0, el §2 y el bloque del día.
No leer `CONTEXTO.md` entero: cada bloque cita lo que hace falta.

Ahorro de créditos (obligatorio):

1. Sin subagentes, sin `/code-review`, sin capturas de pantalla salvo que el bloque lo pida.
2. Backtests siempre con `--solo-estructural` (2 minutos). El SARIMAX completo se corre **una
   sola vez** en toda la semana (bloque S3), en segundo plano.
3. `pytest -q` una vez, al cerrar el bloque (12 s). No después de cada edición.
4. Scripts de más de una línea: escribirlos con Write en `scratch/` (ignorado por git) o usar
   Edit. **Nunca heredoc con comillas triples**: se rompe en Git Bash.
5. No releer archivos ya editados ni volver a medir lo que este plan ya da como dato.
6. **Tope de intentos:** dos intentos fallidos sobre el mismo error → anotar en la bitácora
   (§7) y pasar al siguiente paso o bloque. Las variantes a probar son las listadas, ni una más.

Seguridad:

- La web (`main.py serve`) debe estar cerrada para todo lo que escriba en la base. Los
  backtests abren en solo lectura.
- Antes de un bloque que escriba datos o migre: `main.py respaldar --keep 1`.
- No se borra nada salvo `data/*_raw` (lo hace el sistema solo). No se publica nada fuera del repo.

Cierre de cada sesión: (a) marcar el bloque en la tabla del §1, (b) tres a seis líneas en la
bitácora (§7) con cifras, (c) un commit por bloque y `git push origin feature/web-api-skeleton`.

---

## 1. Estado y calendario

| Bloque | Qué | Día | Sesiones | Estado |
|---|---|---|---|---|
| S1 | A · anclas de mercado en la senda macro | lun | 1 | **hecho 16-nov** · no adoptado |
| S2 | C · factores de curva en la ecuación de activos | mar | 1–2 | **hecho 16-nov** · no adoptado (V2 pasa h2–4, falla h1) |
| S3 | Cobertura ERNG + corrida completa del híbrido y bandas | mié | 1 | **hecho 16-nov** · adoptado |
| S4 | D · bancos y aseguradoras: backtest, bandas calibradas, inflación | jue–vie | 2 | **hecho 16-nov** · bandas + UF en bancos adoptados |
| S6 | Cierre: documentación, validación, informe | vie | 0,5 | **hecho 16-nov** |
| S5 | Frontera eficiente v1 (**opcional**, solo si S1–S4 y S6 están cerrados) | — | 2 | pendiente (no iniciado) |

Ejecutado todo en una sola sesión el 16-nov (no en cinco): los bloques compartían archivos,
así que va en un solo commit. Informe: [`docs/INFORME_SEMANA.md`](INFORME_SEMANA.md).

Orden fijo: S3 cambia la muestra del backtest (entran empresas nuevas), por eso va **después**
de S1 y S2. Si la semana se acorta, se sacrifica S5, luego la mitad "seguros" de S4. S6 no se salta.

Ya hecho (no rehacer): curvas y `/curvas` (commit `bea8040`); paso B, resultado no operacional
en producción (`46be45d`); `models/market_path.py` y `models/compare_bt.py` (commit de este plan).

---

## 2. Regla de adopción (pre-acordada; se aplica mecánicamente)

Nada entra a producción sin ganar fuera de muestra. La regla está programada:

```
python -m models.compare_bt --base scratch/X.csv:SPEC --nuevo scratch/Y.csv:SPEC --cuenta ingresos|ganancia
```

Dice `ADOPTAR` solo si, en los horizontes 2–4: gana ≥ 50% de los pares, el ΔMASE medio es < 0,
la mediana mejora en promedio y en ningún horizonte empeora más de 0,02.

- Spec a comparar: `estr_last` para ingresos, `estr_last_nop` para ganancia (son los de producción).
- **Un cambio se adopta si da `ADOPTAR` en su cuenta objetivo y en la otra cuenta el ΔMASE
  medio no supera +0,005.** Si no, queda el código detrás de un parámetro con el valor por
  defecto actual, y se documenta el resultado. No adoptar es un resultado válido y esperado
  en parte de estos bloques.
- Si hay varias variantes que pasan, gana la de menor ΔMASE medio en la cuenta objetivo, y
  además debe pasar con `--horizontes 1,2,3,4`.
- Honestidad sobre la vara: el paso B pasó con 51,8% de pares (z ≈ 1), Δ medio −0,058 y
  medianas −0,06 a −0,09. Es evidencia moderada, no aplastante; por eso la regla mira medias
  y medianas, no solo la tasa de pares.

Línea base vigente: `scratch/base_encuestas.csv` (40 empresas × 8 orígenes × h1–4). Si no
existe (la carpeta no se versiona), se regenera en 2 minutos:

```
python -m models.backtest --solo-estructural --companies 40 --origins 8 --horizon 4 --out scratch/base_encuestas.csv
```

---

## S1 · Anclas de mercado en la senda (lunes, 1 sesión)

**Entregable:** la senda macro puede anclarse a precios de mercado; veredicto de backtest;
si pasa, `/proyecciones` la usa.

**Hecho:** `models/market_path.py` → `market_path(db, as_of, meses, ipc_actual)` devuelve la
senda mensual de `tpm` e `ipc_yoy` implícita en swaps, ya corregida por el premio medio
mercado − EOF de 3 años, con datos hasta `as_of`. Probado a mano en tres fechas (0,3 s).

**Falta:**

1. `models/macro_path.py`: parámetro `fuente: str = "encuestas"` en `build_future` y
   `build_macro_path` (valores `encuestas | mercado | mezcla`). Después del bucle de anclajes,
   si `fuente != "encuestas"`:
   - fecha = `as_of` o `db.curves.ultima_fecha(db)`;
   - `ipc_actual` = último valor de `_monthly_series(db, HIST_SERIES["ipc_yoy"])` ≤ fecha;
   - `mp, det = market_path(db, fecha, horizon_months, ipc_actual)`; si `mp` viene vacío,
     `logger.warning` y se queda con encuestas;
   - alineación **posicional**: fila *i* de `future` ← `mp` con h = *i* (el desfase de días
     dentro del mes se acepta y se documenta);
   - `mercado` reemplaza `tpm` e `ipc_yoy`; `mezcla` promedia 50/50. `tc` y `act_yoy` no cambian;
   - `used_anchors["mercado"] = det`.
2. `models/backtest.py`: `_StructuralCache(db, fuente)` lo pasa a `build_macro_path`; opción
   CLI `--senda`. De paso: cuando `solo_estructural`, saltar el `build_macro_path` por serie
   (líneas ~129–141, solo sirve al SARIMAX) — ahorra tiempo.
3. Dos corridas → `scratch/senda_mercado.csv`, `scratch/senda_mezcla.csv`; cuatro
   comparaciones contra la base (2 variantes × 2 cuentas).
4. Decisión por §2. Si pasan las dos, se adopta **mezcla** (más conservadora). Adoptar =
   `forecast_company_hybrid` llama `build_macro_path(..., fuente=...)` y la nota metodológica de
   `api/templates/partials/proyecciones_chart.html` lo dice en una frase.
5. `tests/test_market_path.py`: 24 filas; `tpm` entre 0 y 20; `ipc_yoy` entre −2 y 15; con
   `as_of='2023-02-28'` la `fecha_curva` del detalle es ≤ esa fecha.

**Expectativa realista:** efecto chico. La senda solo entra por las elasticidades de activos
(muy contraídas) y por la inflación del reajuste. Si no pasa, no se insiste: se documenta y sigue S2.

---

## S2 · Factores de curva en la ecuación de activos (martes, 1–2 sesiones)

**Entregable:** veredicto sobre tres factores financieros en `Δlog(activos) ~ macro`.

Hechos útiles: `estimate_asset_model` y la proyección ya usan **todas** las columnas de
`factors_q` (son genéricos); lo único atado a la lista fija es `_quarterly_factors`
(`return q[FACTORS]`) y el `dict(zip(FACTORS, …))` del resultado en `forecast_structural`.

1. `db/curves.py`: `tasas_cero(db, curva, plazos, hasta=None) -> DataFrame` diario (índice
   fecha, una columna por plazo, % efectivo). Es `historia()` sin el muestreo semanal;
   refactorizar `historia()` para que la use.
2. `models/curve_factors.py`: `curve_factors(db, as_of, meses) -> DataFrame` mensual,
   historia + futuro, con datos ≤ `as_of`:
   - `tasa_real5`: cero swap UF a 5 años. Futuro: forward implícita
     `[z(h+5)·(h+5) − z(h)·h] / 5` de la curva `swap_uf` del día (llega a 20 años: alcanza).
   - `pendiente`: cero swap pesos 10 años − 2 años. Futuro: **último valor constante** (la
     curva en pesos termina en 10 años; no hay forward).
   - `spread_banc`: depósito 360 días (`F022.TDB.TIS.AN01.NO.Z.D`) − cero swap pesos 1 año.
     Futuro: último valor constante.
3. `models/structural.py`: `_quarterly_factors(macro, extra=None, factores=None)` une `extra`
   (promedio trimestral) y selecciona `factores`; el `beta` del resultado usa
   `model.factor_names`. `models/backtest.py`: opción `--factores` (lista separada por comas).
4. Cuatro corridas, ni una más (más una quinta solo si dos pasan: la combinación de ambas):
   - V1 `act_yoy,ipc_yoy,tpm,tc_yoy,tasa_real5`
   - V2 `act_yoy,ipc_yoy,tasa_real5,tc_yoy` (tasa real en lugar de TPM)
   - V3 `act_yoy,ipc_yoy,tpm,tc_yoy,pendiente`
   - V4 `act_yoy,ipc_yoy,tpm,tc_yoy,spread_banc`
5. Cuenta objetivo: **ingresos**. Decisión por §2 (con la condición extra de pasar en h1–4).
   Adoptar = nuevo valor por defecto de la lista de factores + frase en la nota metodológica.
6. Test: `curve_factors` no tiene nulos en el horizonte y respeta `as_of`.

Usar la senda que haya quedado vigente tras S1. La base para comparar es la corrida de S1
adoptada, o `base_encuestas.csv` si no se adoptó nada.

---

## S3 · Cobertura ERNG + corrida completa (miércoles, 1 sesión)

**Entregable:** ~57 empresas más con proyección (las que informan resultados "por naturaleza",
p. ej. Aguas Andinas, RUT 61808000); cuadro del híbrido completo y **todas** las bandas
recalculadas en una misma muestra.

Dato: 23 RUT tienen ERFG y ERNG a la vez → se prefiere ERFG.

1. Cambiar `statement_group = 'ERFG'` por la lectura de ambos, prefiriendo ERFG, en:
   `models/structural.py:98`, `models/nonop.py:49`, `models/forecast.py:68`,
   `models/backtest.py:57`. Patrón:
   ```sql
   SELECT rut, account_name, period,
          coalesce(MAX(value) FILTER (WHERE statement_group = 'ERFG'),
                   MAX(value) FILTER (WHERE statement_group = 'ERNG')) AS value
   FROM cmf_financial_statements
   WHERE statement_group IN ('ERFG', 'ERNG') AND account_name IN (...)
   GROUP BY rut, account_name, period
   ```
2. Verificar: `GET /proyecciones/chart?company=AGUAS ANDINAS` devuelve gráficos; contar
   columnas de `panel.revenue` antes y después (anotar en bitácora). Revisar que el selector de
   empresas de `/proyecciones` (`api/routers/web/proyecciones.py:29`) las incluya.
3. `main.py catchup` (pone los datos al día antes de la corrida larga).
4. Corrida completa **en segundo plano** (incluye SARIMAX; duración no medida, estimar 20–40 min):
   `python -m models.backtest --companies 40 --origins 8 --horizon 4 --out scratch/bt_full.csv`
   más los flags `--senda` / `--factores` que hayan quedado adoptados.
5. Con `bt_full.csv`: (a) cuadro MASE mediana por spec y horizonte → "Ronda 5" en
   `docs/wiki/Backtest-Modelos.md`; (b) regenerar **todo** `BAND_QUANTILES` en
   `models/hybrid.py`: cuantiles 0,80 y 0,95 de `mase` de `noexog` en h = 1, de `estr_last`
   (ingresos) y `estr_last_nop` (ganancia) en h = 2–4; (c) confirmar en esa tabla que el
   híbrido sigue siendo la mejor división por horizonte. Si `noexog` ya no gana en h = 1 por
   más de 0,02 de mediana, **no cambiar producción**: anotarlo en §6.
6. Regenerar `scratch/base_encuestas.csv` (la muestra cambió) para S4/S5.

---

## S4 · Bancos y aseguradoras (jueves–viernes, 2 sesiones)

**Entregable mínimo (vale aunque la macro no pase):** bandas **calibradas fuera de muestra**
en `/proyecciones/bancos` y `/proyecciones/seguros` (hoy son gaussianas sin calibrar, y la vista
lo admite). **Entregable objetivo:** resultado bancario separado en base + exposición a la UF.

Hechos: 16 bancos, mensual 201901–202609; seguros 41 vida + 32 generales, trimestral 2015–2026.
Partidas y specs en `_PROY_SIMPLE` (`api/routers/web/_shared.py:312`) y
`models/simple_forecast.py` (`forecast_series`, `deaccumulate_ytd`). La carga de cada serie
está en `proyecciones_simple_chart` (`api/routers/web/proyecciones.py:70–150`): reutilizarla
moviéndola a una función, no duplicarla.

Sesión 1 — arnés y bandas:

1. `models/backtest_simple.py`: mismo esquema de salida que `models/backtest.py` (`rut`,
   `company`, `account`, `origin`, `h`, `target`, `spec`, `actual`, `forecast`, `abs_err`,
   `mase`). Bancos: 18 orígenes mensuales, horizontes 1, 3, 6 y 12. Seguros: 8 orígenes
   trimestrales, horizontes 1–4. Specs: `sarimax` (el de producción) y `snaive`.
2. `models/compare_bt.py`: que `--cuenta` acepte cualquier prefijo de nombre de partida.
3. Bandas empíricas por (clase, partida, horizonte) como en `hybrid._empirical_bands`;
   las vistas dejan de decir "no calibradas". Horizontes intermedios: usar el cuantil del
   horizonte medido inmediatamente superior.

Sesión 2 — inflación en el resultado bancario (los bancos tienen más activos que pasivos en UF):

4. `models/nonop.py`: extraer de `drivers()` la inflación **mensual** (serie `m`, con el
   rezago de la UF) a una función `inflacion_uf_mensual(db, macro)`.
5. Spec `uf` solo para la partida de resultado de bancos: `resultado_m = a + k·π_UF,m` (MCO
   **con** intercepto, ventana 36 meses hasta el origen); se proyecta `resultado − k·π` con el
   SARIMAX de producción y se suma `k·π` de la senda. Publicación: 1 mes de rezago.
6. Decisión por §2 con `--horizontes 3,6,12` y base `sarimax`. Adoptar = spec por defecto para
   resultado de bancos + una línea en la vista con la exposición estimada.
7. Seguros de vida: probar el mismo spec `uf` **una vez**; si no pasa, queda solo con las
   bandas calibradas. No investigar más factores para seguros esta semana.

Tope: si al terminar la sesión 1 el arnés no produce el cuadro, se documenta dónde quedó y se
salta a S6.

---

## S6 · Cierre (viernes, media sesión — no se salta)

1. `pytest -q`; `main.py validate` (anotar errores/avisos frente a 0/81 del 16-nov).
2. Documentación: tabla de estado de `docs/PLAN_PROYECCIONES_MACRO.md`; sección de
   proyecciones de `CONTEXTO.md` (decisiones medidas de la semana, en el mismo estilo que
   las de curvas); `README.md` si cambió algo visible; `docs/wiki/Hoja-de-Ruta.md` §0.
3. `Actualizar_Datos.exe`: recompilar **solo** si cambió algo en `db/`, `scheduler/`,
   `collectors/`, `main.py` o `actualizar.py` (los modelos no van en el .exe).
4. `docs/INFORME_SEMANA.md` (una página, para quien vuelve de vacaciones): por bloque, qué
   se adoptó con sus cifras, qué no pasó y por qué, y la lista de §6. Sin adjetivos: cifras.
5. Commit y push.

---

## S5 · Frontera eficiente v1 (opcional; 2 sesiones; solo con todo lo anterior cerrado)

**Entregable:** página `/portafolios` **fuera de la barra de navegación** (se llega por URL y
desde `/metodologia`) hasta la revisión legal. Es una ilustración con supuestos explícitos, no
una recomendación: el texto de la página lo dice arriba, no solo al pie.

Diseño cerrado:

- `db/portfolios.py`. Clases de activo = productos que se pueden comprar: mediana mensual de
  rentabilidad por categoría de fondos mutuos (series no anómalas; categorías con ≥ 60 meses y
  ≥ 10 series) + fondos A–E (promedio entre AFP). Insumo: `db.products._monthly_panel`.
- Covarianza: muestral de 60 meses contraída 30% hacia correlación constante
  (`Σ* = 0,7·S + 0,3·F`). `sklearn` no está instalado: se hace a mano; `scipy` sí está.
- Retornos esperados: **no** los promedios históricos. `μ_i = tasa de depósito + 0,3·σ_i`
  (mismo Sharpe para todas las clases; supuesto declarado en la página).
- Frontera: mínima varianza para 20 retornos objetivo, solo posiciones largas, tope 40% por
  clase (`scipy.optimize.minimize`, SLSQP). Cinco puntos marcados por volatilidad objetivo
  (2, 4, 6, 9 y 12% anual), sin llamarlos "perfiles".
- Control fuera de muestra: mínima varianza con ventana móvil de 36 meses frente a pesos
  iguales, volatilidad realizada; se muestra el resultado, sea cual sea.
- Gráfico: dispersión riesgo–retorno de las clases + línea de frontera, con la paleta ya
  validada en `api/templates/curvas.html`. Una captura de pantalla para revisar el diseño.

---

## 6. Para el regreso (necesita al dueño del proyecto; el ejecutor no lo toca)

Decisiones y curaduría pendientes desde antes:

- Revisión legal antes de publicar (asesoría de inversión, conflicto de interés, datos
  personales, términos de uso de las fuentes). Ahora incluye `/portafolios` si se construyó.
- `config/proveedores.yaml`: URL verificada de cada AGF y AFP.
- RUT de China Construction Bank y Bank of China en `config/entidades.yaml` (las 7 AFP: hecho 17-nov).
- Factor de reparto de dividendos de fondos mutuos: sin fuente identificada.
- Fondos de inversión públicos, APV y Cuenta 2, reclamos: explorar fuentes nuevas.
- Cuotas diarias de fondos mutuos: decidir si vale el volumen.
- Separar ingesta y servicio cuando haya servidor.

Estacionado a propósito (no cabe en la semana o depende de lo anterior):

- Curvas de crédito por emisor: exige reconciliar la valorización de la renta fija de seguros
  (falta el catálogo de campos B.9) o una fuente pagada. Varias sesiones, resultado incierto.
- Clasificaciones de riesgo por emisor (formato de la CMF sin explorar).
- Costos financieros como deuda × tasa: aporta poco con deuda a tasa fija; revisar si S2 adopta
  la tasa real.

El ejecutor agrega aquí lo que encuentre y no pueda resolver, con una línea cada cosa.

Encontrado en la ejecución (16-nov). **Resueltos el 17-nov (ronda 6):** V2 adoptado, ancla del
margen = mediana 4T, h2–3 = promedio SARIMAX + estructural, tope al SARIMAX explosivo; detalle
en `docs/wiki/Backtest-Modelos.md` (Ronda 6). Sigue abierto: `snaive` en bancos h ≥ 9 (va con
el modelo específico de bancos, igual que la limpieza de atípicos de bancos, que ganó 58% de
pares pero empeora h3).

- **Decidir S2-V2** (tasa real 5a en lugar de TPM): en ingresos h2–4 gana 54,3% de pares
  (z = 2,6), Δ medio −0,040, medianas −0,03; falla solo la condición extra de h1–4 porque la
  mediana de h1 empeora +0,033. En producción h1 lo cubre el SARIMAX, no el estructural, así
  que la condición extra puede ser más estricta de lo necesario. Ganancia: Δ +0,001 (dentro de
  la tolerancia). Activarlo = `--factores act_yoy,ipc_yoy,tasa_real5,tc_yoy` como valor por defecto.
- **h2 en la corrida completa:** `noexog` (0,760 ingresos; 0,856 ganancia) queda levemente bajo
  el estructural (0,768; 0,894). En h3–4 manda el estructural. No se cambió producción.
- **`estr_m4` vs `estr_last`** en la muestra nueva (con ERNG): `estr_m4` tiene mejor mediana en
  ingresos h2–4 (0,744/0,698/0,704 vs 0,768/0,746/0,721) y `estr_m4_nop` en ganancia h3–4.
  Merece una comparación en pares con `compare_bt` antes de cambiar el ancla.
- **SARIMAX que explota** en seguros: CONTEMPORA origen 202403, MASE 6·10⁸ en h4
  (`enforce_stationarity=False`). La vista de producción podría mostrar algo así; conviene un
  tope (p. ej. volver a `snaive` si la proyección sale 10× fuera del rango histórico).
- **Bancos, resultado h12:** `snaive` (0,748) le gana al SARIMAX (0,951) y al `uf` (0,901).
  Para la vista de 24 meses, considerar `snaive` desde h ≥ 9.
- **Diferencia leve entre corridas de la misma spec:** la base guardada al preparar el plan y la
  regenerada al empezar S1 difieren < 1% en los pronósticos (mediana por origen 0,00–1,7%);
  causa no investigada (probable: datos actualizados entre medio). Por eso S1/S2 se compararon
  contra una base corrida en el mismo momento (`scratch/senda_encuestas.csv`).

---

## 7. Bitácora (la llena quien ejecuta: fecha, bloque, cifras, veredicto)

- 2026-11-16 · preparación · `market_path` probado (3 fechas, 0,3 s; premio medio TPM a 24
  meses ≈ +0,42 pp, inflación ≈ +0,05 a +0,19 pp). `compare_bt` probado contra el paso B:
  ADOPTAR (51,8% de pares, Δ medio −0,058) y contra un control `estr_m4` vs `estr_last` en
  ingresos: NO ADOPTAR. Base guardada en `scratch/base_encuestas.csv`.
- 2026-11-16 · S1 · `build_macro_path(fuente=encuestas|mercado|mezcla)`, `--senda`; el
  backtest `--solo-estructural` ya no construye la senda del SARIMAX. Contra `senda_encuestas`:
  mercado ingresos 45,7% pares, Δ 0,000 (medianas −0,01 a −0,02); ganancia 50,6%, Δ +0,004,
  h2 +0,032. Mezcla ingresos 47,0%, Δ −0,005; ganancia 51,2%, Δ +0,001, h2 +0,041.
  **NO ADOPTAR** ninguna: queda `fuente="encuestas"` por defecto.
- 2026-11-16 · S2 · `db.curves.tasas_cero` (historia() la usa), `models/curve_factors.py`,
  `_quarterly_factors(extra, factores)`, `--factores`. Ingresos h2–4 contra la base: V1 49,5%,
  Δ −0,010; **V2 54,3%, Δ −0,040, medianas −0,03 → ADOPTAR en h2–4 pero NO en h1–4** (h1
  +0,033); V3 52,1%, Δ +0,036; V4 46,9%, Δ +0,133. Ganancia: todas |Δ| ≤ 0,004. Por la regla
  (§2, condición extra h1–4) **ninguna se adopta**; V2 queda para decisión del dueño (§6). Sin
  quinta corrida (no pasaron dos).
- 2026-11-16 · S3 · ERFG o ERNG (prefiere ERFG) en structural/nonop/forecast/backtest:
  panel de ingresos 402 → 483 empresas; Aguas Andinas con proyección; 11 de las 40 del
  backtest son nuevas (Enel ×3, Entel, Telefónica ×2, Aguas Andinas…). `catchup` OK.
  Corrida completa (con SARIMAX): **136 s**, no 20–40 min. noexog sigue mejor en h1 (0,573 vs
  0,599 eee y 0,729 estr_last; ganancia 0,727 vs 0,867). `BAND_QUANTILES` regenerado entero.
  Ronda 5 en `docs/wiki/Backtest-Modelos.md`. Base regenerada.
- 2026-11-16 · S4 · `models/backtest_simple.py` (bancos 16 × 18 orígenes, seguros 73 × 8);
  `cargar_serie` compartida con la vista; `compare_bt --cuenta` acepta cualquier prefijo.
  Bandas calibradas en `BANDAS_SIMPLE`; la vista ya no dice "no calibradas" para bancos y seguros.
  `nonop.inflacion_uf_mensual`. Spec `uf` bancos h3/6/12: 51,7% pares, Δ −0,021, medianas
  −0,02/−0,05/−0,05 → **ADOPTAR** (también en h1–12: 52,0%, Δ −0,023). Seguros de vida h2–4:
  49,5%, Δ −0,026, medianas −0,05 a −0,09 → **NO ADOPTAR** (pares < 50%).
- 2026-11-16 · S6 · pytest 50 pasan. Validación y documentación: ver INFORME_SEMANA.
- 2026-11-17 · ronda 6 (encargo del dueño: decisiones 1–4 + atípicos) · 80 empresas, criterio
  de mitades vistas/nuevas, `compare_bt` sin empates. Híbrido nuevo vs anterior h1–4: ingresos
  56,4% de pares, Δ −0,110; ganancia 53,5%, Δ −0,060 → **ADOPTAR**. Limpieza de atípicos no
  adoptada en ningún modelo (solo se marcan en las vistas). pytest 54 pasan.
- 2026-11-17 · institucionales · inversión por emisor, sectores curados (commit 938b69c); monitor
  de derivados (PLAN_DERIVADOS fases 1–3: BCCh 75 series, FFMM FUTU/OPCI, seguros B.7 57 meses,
  213 mil contratos; commits 5cc8c7a, a449368). pytest 64.
- 2026-11-17 · emisores en común con montos MM$; corregido doble conteo (CTE de tipo de cambio
  con el período repetido cuando seguros y FFMM cierran el mismo mes: montos ×2). Commit f3fb457.
- 2026-11-17 · bancos/IMACEC (análisis, sin código en producción): ver PLAN_PROYECCIONES_MACRO §D.
  El modelo de nivel pierde contra persistencia; la corrección por ΔIMACEC gana 5–15% a 3–6 meses
  y la cifra a 12 meses está sesgada (usa IMACEC efectivo futuro). Decisión pendiente del dueño.
- 2026-11-17 · SP cartera desagregada (PLAN_DERIVADOS fase 4): `sp_cartera_detalle` (migración
  013), 2010-01→2026-06, 5,5 M filas; swaps por AFP × fondo × contraparte, forwards USD (C/V
  validado vs BCCh), AFP en la vista por emisor (171/182 emisores por nombre, 99% del monto),
  RUT de las 7 AFP. Commit 7221970. pytest 70; CI verde.
- 2026-11-17 · revisión visual con capturas (Chrome headless, servidor de prueba en :8001):
  corregidos ejes desalineados entre gráficos vecinos (CSS `.chart-grid`), leyendas abajo que
  pisaban las fechas (monitor de derivados: leyenda arriba, márgenes iguales), small multiples
  de sectores que se desbordaban, hitos superpuestos (ahora numerados), etiquetas cortadas,
  colores repetidos en la deriva de seguros, rentabilidad neta negativa de AFP en verde,
  enlaces en azul por defecto, texto "las AFP no aparecen". Pendientes visuales: Hoja de Ruta §0.
- 2026-11-17 · rediseño de vistas (pendientes visuales 15–21): tema común de gráficos
  `api/static/charts.js` (leyenda arriba, automargin, formato y fechas en castellano, paleta
  validada con el script de dataviz sobre #1e293b), formato chileno en todas las plantillas,
  verde solo para variaciones positivas, campos oscuros; proyecciones por defecto Cencosud y en
  miles de millones con línea "hoy"; AFP neta como gráfico de pesas; FFMM sin series rotas
  (salto −90%/×10 en un mes); bloque AFP del emisor con ventana SP. Verificado con capturas.
- 2026-11-18 · vista `/derivados` (pestaña propia): derivados de toda la economía. 376 series
  F099.DER nuevas (bancos por contraparte, instrumento, tipo de swap y plazo). Mercado, industrias,
  riesgos (+10% dólar, +1 pp UF, DV01 de swap cámara), coberturas por AFP × fondo, compañía de
  seguros y fondo mutuo, alertas. Identidades por sector cuadran exacto (testeadas). Detalle en
  `docs/PLAN_DERIVADOS.md`.
- 2026-11-18 · fechas de corte en derivados: global `corte(fecha, fuente)` y filtro `mes_es`
  (`api/deps.py`); "dato a sep 2026 · Banco Central" en cada sección, tarjeta, gráfico y alerta de
  `/derivados` y del monitor de AFP, más una franja de fechas que explica los rezagos distintos.
  `/eeff/corporativos` abre con Cencosud. Herramientas para el rediseño: `scripts/ui_capturas.py`
  (escritorio + celular real de 390 px vía `api/static/ui_movil.html`, chequeo de desborde y letra
  < 12 px) y `scripts/ui_guard.py` (ids, names, hx-* y destinos de gráficos). Plan estético para
  ejecutar con Sonnet: `docs/PLAN_ESTETICA.md`.

---

## 8. Texto para iniciar cada sesión (copiar y pegar)

> Ejecuta el bloque **S_** de `docs/PLAN_SEMANA.md`. Lee solo el §0, el §2 y ese bloque. Sigue
> los pasos tal como están, aplica la regla de adopción sin reinterpretarla, y al terminar
> actualiza la tabla del §1 y la bitácora del §7, haz un commit y push a
> `feature/web-api-skeleton`. No hagas nada fuera del bloque: lo que encuentres, anótalo en el §6.
