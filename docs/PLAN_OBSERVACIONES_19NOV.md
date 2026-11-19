# Plan de observaciones del 19-nov-2026 — emisores, ranking, derivados y panel

Redactado el 19-nov-2026 (Opus) a partir de 13 observaciones del usuario. Ejecutor previsto:
Sonnet, fase por fase. Cada fase trae su diagnóstico ya verificado contra la base. **No lo
vuelvas a investigar desde cero**: confírmalo con las consultas de control indicadas y ejecuta.

## Estado (actualízalo al cerrar cada fase)

| Fase | Contenido | Estado | Commit |
|---|---|---|---|
| F0 | Preparación y línea base | hecha (sin cambios de código) | — |
| F1 | Proyecciones: línea real/proyectado dentro del gráfico | hecha | 11588d3 |
| F2 | Curvas: mini explicación bajo cada gráfico | hecha | 8f4fced |
| — | (Nombres y emisor canónico: hecho por Opus antes del plan, ver sección «Ya hecho») | hecha | d5e4b73 |
| F4 | Concentración por emisor de seguros: monto expuesto y filas expandibles | hecha | 939d71b |
| F5 | Tipo de instrumento en la exposición por emisor | pendiente | — |
| F6 | Emisores en común con AFP | pendiente | — |
| F7 | Cobertura de derivados de seguros: medida por monto | pendiente | — |
| F8 | Ranking: 15 peores e indicadores de salud (Z de Altman) | pendiente | — |
| F9 | Vincular ranking y exposición por emisor | pendiente | — |
| F10 | Panel de portada: indicadores de mercado | pendiente | — |
| F11 | Investigación: valor de mercado de B.1 (con tope de tiempo) | pendiente | — |
| F12 | Cierre: documentación y registros | pendiente | — |

Si se acaba el tiempo, el orden ya prioriza lo visible y barato (F1–F2). F11 es opcional.
No hay F3: lo hizo Opus antes del plan (sección «Ya hecho»); la numeración se conserva.

---

## 0. Reglas

1. **Rama:** solo `feature/web-api-skeleton`, nunca `main`. Un commit por fase con push.
   El primer commit (F0) incluye este archivo.
2. **Servidor:** el del usuario corre en `:8000`; **no lo toques ni lo reinicies**. El tuyo va
   en `:8001`, en segundo plano:
   `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn api.main:app --port 8001`.
   - Reinícialo después de cambiar cualquier `.py`.
   - Las plantillas y el CSS se leen en cada request.
   - Mátalo al terminar.
3. **Base de datos:**
   - La web la abre en solo lectura en cada request.
   - La única escritura de este plan es `import-cartera-seguros`, y solo si F11 prospera. Si cambias `config/entidades.yaml`, corre además `main.py build-productos`. Son comandos que abren, escriben y cierran.
   - Mientras escriben, las web pueden responder 503 unos segundos; es normal.
   - **No borres ni reescribas datos crudos de las tablas**: las correcciones se aplican al leer.
4. **Datos de origen:** si se descarga algo a `data/*_raw`, se borra después de cargarlo
   (preferencia del usuario: ahorrar disco).
5. **Contrato de plantillas:** `scripts/ui_guard.py check` debe decir «Contrato intacto».
   - Si una fase agrega ids, `name=` o `hx-*` nuevos, regenera la línea base con `scripts/ui_guard.py snapshot` en esa misma fase y dilo en el commit.
   - No se borra ni se renombra nada existente.
6. **Tests:** `pytest -q` debe pasar entero. Hoy son 111; cada fase suma los suyos. Puedes
   editar `tests/`. Los tests que leen la base real usan el fixture `real_db_readonly`, que se
   salta si la base está tomada.
7. **Presentación:**
   - Formato chileno: filtro `es` en Jinja, `INF.fmt` en JS.
   - Verde y rojo solo para signo o para mejora y deterioro.
   - Leyenda arriba.
   - Cada cifra nueva lleva fecha y fuente: `ui.stat(..., fecha=, fuente=)`, `ui.viz(..., fuente=)`, `ui.sec_head(..., fecha=, fuente=)`.
   - Textos en castellano de Chile, oración simple.
8. **Nada inventado:**
   - Las glosas de códigos (tipos de instrumento, columnas SP) se escriben solo cuando son inequívocas. Si no, se muestra el código crudo con «(sin glosa)».
   - Los RUT nuevos llevan evidencia en `config/entidades.yaml`, siguiendo su regla de cabecera.
9. **Verificación al cerrar cada fase:**
   - Corre `pytest -q`, `ui_guard.py check` y `scripts/ui_capturas.py --label fN --solo <urls>`.
   - Lee los recortes de escritorio **y** de celular.
   - Corre las consultas de control de la fase.
   - No hagas commit con un 500 o con una regresión vista.
10. **Consultas ad hoc:** usa un script en el scratchpad que abra la base con
    `duckdb.connect(DB, read_only=True)`. No uses heredocs largos en Bash; escribe el `.py` o
    `.sql` con la herramienta de escritura.
11. Responde al usuario en castellano.

---

## Respuestas a las preguntas del usuario (diagnóstico verificado el 19-nov)

Estas respuestas van también en el mensaje final al usuario. Las fases que corrigen cada
punto están indicadas.

**«¿Por qué en la cartera de EuroAmerica Vida aparecen contratos de leasing de la propia
compañía como emisor y dos valores nominales?»**

- **No es un error.**
  - El tipo `CLEAS` es un contrato de leasing sobre bienes raíces (Circular 1.835, Anexo §3): la compañía compra el inmueble y lo arrienda con opción de compra.
  - El «emisor» es quien origina el contrato, y en un leasing propio es la misma aseguradora.
  - En 202609, 7.279 de los 12.399 contratos `CLEAS` del mercado llevan como emisor el RUT de la propia compañía.
  - EuroAmerica (99279000) tiene 53.
- **Los «dos valores nominales» son dos monedas.**
  - 52 contratos en UF suman 3.839.347 UF.
  - 1 contrato en pesos (feb-2026) suma $6.792 millones.
  - La tabla abre el nominal por moneda porque no se suma UF con pesos.
- **Hay un problema real detrás.**
  - B.1 trae dos campos: `valor_nominal` (monto de origen) y `valor_nominal_vig` (saldo vigente).
  - Para **todos** los `CLEAS`, `LH`, `BR`, `AFROIND` y casi todos los `BNEE`, `BB`, `BU`, `BTU` y `BTP`, el vigente viene en 0.
  - En esos casos el sitio usa el monto de origen, que **sobrestima** lo expuesto en instrumentos que amortizan (leasing, letras).
  - F4 lo señala en pantalla y F11 intenta el valor de mercado.

**«¿Cómo estamos midiendo el porcentaje de cobertura de derivados para seguros?»**

Hay dos medidas distintas con el mismo nombre, y ninguna es buena:

1. `/inversion-institucional/derivados`, columna «% cobertura» (`db/derivados_seguros.py`):
   - Es el % del **número** de contratos cuyo objetivo declarado no es «INV» (inversión).
   - No pondera por monto: un forward chico pesa lo mismo que un swap enorme.
2. `/derivados`, «Coberturas por entidad» (`db/derivados_global.coberturas_seguros`):
   - Es −(posición neta en moneda extranjera vía forwards y swaps, en USD) ÷ (inversión **en el extranjero** según B.8, en USD).
   - El denominador mide geografía, no moneda: deja fuera los bonos locales en dólares (unidad `PROM` en B.1; p. ej. los `BNEE`), que también se cubren.
   - Los contratos con patas incoherentes se excluyen sin que se diga cuánto pesan.

F7 unifica la medida (por monto, contra activos en moneda extranjera) y renombra la otra.

**«Empresas CMPC no emite bonos (los emite Inversiones CMPC). ¿Es un error que EuroAmerica
aparezca expuesta en renta fija a Empresas CMPC?»**

- **Sí, es un error, y viene de la fuente.**
  - EuroAmerica informó el bono `BCMPC-S` (120.000 UF) con el RUT de Empresas CMPC (90222000).
  - Las otras 4 compañías que tienen ese bono, y todos los fondos mutuos, lo informan con el RUT de Inversiones CMPC (96596540).
  - La SP lo confirma: separa «EMPRESAS CMPC S.A.» (solo `ACC`, acciones) de «INVERSIONES CMPC S.A.» (`DEB`, bonos).
- **No es un caso aislado.**
  - En 202609 hay **169 nemotécnicos** (sin contar mutuos ni leasing) que distintas compañías informan con RUT de emisor distinto.
  - Una parte son errores de digitación, como el `BTP0600433`, que figura a nombre de la Tesorería y del Banco Central.
  - Otra parte son RUT de bancos absorbidos que siguen informándose: `DES…` con 97051000 y 97018000, `UBBV…` con 97032000 y 97018000, `USECJ…` con 97053000 y 97080000.
- **Ya corregido** (sección «Ya hecho»): cada nemotécnico se asigna a un emisor canónico (el RUT mayoritario).

**«¿Por qué en los emisores en común no ponemos los fondos de pensiones?»**

- La página `/inversion-institucional/emisores` cruza por **nemotécnico**.
- Cuando se hizo, la cartera de la SP venía agregada, sin instrumento.
- Desde el 17-nov la cartera desagregada por emisor (`sp_cartera_detalle`, tabla `emisor_nac`, 220 emisores) ya está cargada y se cruza por **nombre → RUT**: la vista por emisor ya la usa.
- La página de emisores en común no se actualizó. F6 la rehace cruzando por RUT, con las AFP como tercera fuente.

---

## F0 — Preparación y línea base

1. `git status` limpio y en `feature/web-api-skeleton`.
2. Levanta `:8001`. Corre `pytest -q` (111 tests) y `ui_guard.py check`.
3. Toma la línea base:

   ```
   scripts/ui_capturas.py --label base19 --solo /,/curvas,/proyecciones/corporativos,/proyecciones/bancos,/ranking,/seguros/cartera,/inversion-institucional/emisor?rut=90222000,/inversion-institucional/emisores,/inversion-institucional/derivados,/derivados
   ```

4. El plan ya está en el repositorio. Si la línea base no cambia nada, no hace falta commit en F0.

---

## F1 — Proyecciones: la línea real/proyectado queda fuera del gráfico

**Diagnóstico.**

- `INF.hoy` (`api/static/charts.js`, cerca de la línea 194) dibuja la línea vertical en el último período histórico.
- Pone el rótulo «proyección →» **a la derecha** de la línea (`xanchor: "left"`).
- La zona proyectada es angosta: 8 trimestres de 46, y en celular unos 60 px. El rótulo se sale del área del gráfico y queda cortado sobre el borde. Se ve en `/proyecciones/corporativos` a 390 px.
- Lo usan `partials/proyecciones_chart.html` y `partials/proyecciones_simple.html`.

**Cambios.**

1. En `INF.hoy`, el rótulo pasa a ser **vertical, pegado a la línea y dentro del gráfico**:
   - `textangle: -90`, `xanchor: "right"`, `xshift: -3`, `yanchor: "top"`, `y: 1`, `yref: "paper"`;
   - texto «real | proyección» si se pasa `texto` vacío, o el texto recibido;
   - fuente de 11 px en `MUTED`.

   Así no depende del ancho de la zona proyectada.
2. En las dos plantillas, llama `INF.hoy(lastP, "proyección")`.
3. En celular, cuando `document.getElementById(el).clientWidth < 520`, muestra solo los
   últimos 24 períodos de historia con `xaxis.range`, para que la proyección no quede
   aplastada:
   - en ejes de categoría el rango va en índices: `[allCats.length - 24 - fx.length - 0.5, allCats.length - 0.5]`;
   - cuida que el índice no baje de −0,5.
4. Sube `margin.r` de 10 a 16 en ambas.

**Control.**

- Capturas de `/proyecciones/corporativos` y `/proyecciones/bancos` a 1.440 y 390 px.
- Además, el horizonte máximo de cada selector. Pruébalo con `hx-get` directo, por ejemplo `/proyecciones/chart?...&h=<máximo>`; lee los parámetros del formulario.
- El rótulo y la línea deben verse enteros dentro del área del gráfico.

---

## F2 — Curvas: mini explicación de qué señala cada curva

**Diagnóstico.** `/curvas` tiene tres gráficos: `ch-curvas`, `ch-tpm` y `ch-hist`. Ninguno dice qué
señala la forma de la curva.

**Cambios.**

1. En `partials/_ui.html`, agrega a `ui.viz` un parámetro `lee=none`.
   - Si viene, se pinta **debajo del cuerpo del gráfico y antes del pie**: `<p class="viz-lee">{{ lee }}</p>`.
   - En `app.css`: `.viz-lee { font-size: var(--fs-xs); color: var(--muted); margin: var(--sp-2) 0 0; max-width: 75ch; }`. El tamaño mínimo de 12 px ya lo garantiza el token.
2. Textos, en `curvas.html`:
   - `ch-curvas`: «Cómo leerla: cada punto es la tasa que exige hoy el mercado por prestar a ese plazo. Si sube con el plazo, el mercado espera tasas más altas o cobra premio por plazo; si baja (curva invertida), anticipa recortes. La distancia entre pesos y UF es la inflación que descuenta el mercado.»
   - `ch-tpm`: «Cómo leerla: si la línea va sobre los puntos, el mercado espera una TPM más alta que los economistas encuestados (o cobra premio por plazo); si va bajo los puntos, espera más recortes.»
   - `ch-hist`: «Cómo leerla: si la tasa a 10 años sube más que la de 2, la curva se empina (más crecimiento o inflación esperados, o más premio por plazo); si la de 2 años supera a la de 10, la curva está invertida, señal típica de recortes por venir.»
3. Frase con el dato del día, solo con valores que la vista **ya calcula**:
   - Revisa en `api/routers/web/_shared.py` (o donde se arme `/curvas`) si están las tasas swap pesos a 2 y 10 años del día. Las series `F022.SPC.TIN.AN02.NO.Z.D` y `F022.SPC.TIN.AN10.NO.Z.D` están en la base.
   - Si están, agrega al final de `lee` de `ch-curvas`: «Hoy: pendiente 10 años − 2 años en pesos = {{ pb|es(0, True) }} pb.».
   - Si no están, no inventes el cálculo en la plantilla: agrégalo en el router desde la misma fuente que alimenta el gráfico.

**Control.** Captura de `/curvas` a 1.440 y 390 px. Texto de 12 px o más (chequeo `letra_chica=0`).

---

## Ya hecho antes de este plan (Opus, 19-nov): nombres y emisor canónico

**No lo rehagas.** Úsalo en las fases siguientes.

**Nombres.**
- En `config/entidades.yaml` (sección `emisores:`):
  - 96549050 se llama «Seguros de Vida SURA S.A.»;
  - 99514870, «MetLife Chile Administradora de Mutuos Hipotecarios S.A.», con un tipo nuevo, `administradora_mutuos`, agregado en `db/entities._PRIORIDAD` y `db/cross.TIPO_LABEL`.
- `dim_entidad` se reconstruyó con `main.py build-productos`.
- De las aseguradoras con nombre corto en `dim_entidad`, solo SURA tenía la razón social completa en el B.8. Las demás no tienen evidencia de un nombre mejor y quedan como están: CORPSEGUROS, BBVA, HDI, CHUBB, PENTA, HUELEN GENERALES, BCIVIDA, PENTA VIDA, EVEREST.

**Emisor canónico** (en `db/cross.py`).
- Funciones:
  - `emisor_canonico_cte(periods_sql, rut=None)`: CTE `canon(period, nemo, rut_canon)`;
  - `canon_join(alias="fi")`: `LEFT JOIN` de B.1 contra `canon`;
  - `emisor_efectivo(alias="fi")`: `coalesce(canon.rut_canon, ltrim(fi.rut_emisor,'0'))`.
- **Uso:** `WITH {emisor_canonico_cte(...)}, ...`, luego `FROM cmf_insurer_portfolio_fixed_income fi {canon_join()}`, y como RUT del emisor `{emisor_efectivo()}`.
- **Regla:** por (período, nemotécnico), gana el RUT informado en más posiciones de B.1. En empate decide el RUT de la cartera NACI de fondos mutuos; si sigue el empate, el menor RUT. `MHA`, `MHB` y `CLEAS` no se reasignan.
- **Rendimiento:** solo resuelve los nemotécnicos con más de un RUT (169 en 202609). Las consultas de un mes completo tardan menos de 2 s; mantén ese nivel.
- **Ya aplicado en:**
  - `issuer_options`, `issuer_holdings` (bloque seguros) y `common_issuers`;
  - `get_insurer_issuer_exposure` y `count_insurer_issuers` (`db/repos/fecu.py`).
- `issuer_holdings` devuelve `reasignados` (`entran_*` / `salen_*` del último mes de seguros). `partials/institucional_emisor_panel.html` lo dice en una nota.
- Tests en `tests/test_institucional.py`: regla de mayoría, empate por fondos mutuos, originadores, nombres curados, caso CMPC, fusiones y originadores con nombre. En total 107 tests.

**Medición (202609).**
- Se reasignan 612 posiciones de 177 nemotécnicos: 1,23 billones de pesos a valor par, el 2,4% de los 52 billones de B.1.
- Los pares más grandes (RUT informado → canónico):

  | Informado | Canónico | Monto | Lectura |
  |---|---|---|---|
  | 97023000 Itaú | 76645030 | 209.839 MM$ | Itaú–Corpbanca, ya unificado por fusión |
  | 76645030 | 97023000 Itaú | 149.428 MM$ | ídem |
  | 97080000 BICE | 97053000 Security | 143.292 MM$ | BICE–Security, ya unificado por fusión |
  | 89900400 | 76000739 ESVAL | 104.125 MM$ | |
  | 96920010 | 76496130 Costanera Norte | 95.253 MM$ | |
  | 96579330 | 76833300 ESSBIO | 75.381 MM$ | |
  | 97032000 (ex BBVA) | 97018000 Scotiabank | 62.530 MM$ | ya unificado por fusión |
  | 76411321 CGE | 90042000 CGE | 42.751 MM$ | los dos RUT de CGE |
  | 97018000 Scotiabank | 97051000 y 97032000 | 29.206 y 25.866 MM$ | bonos `DES…` y `UBBV…`; ya unificados por fusión |

- **Fusiones** (`config/entidades.yaml`, sección `fusiones:`, RUT absorbido → sucesor):
  - 76645030 y 97041000 → 97023000 (Itaú–Corpbanca; sobrevive Itaú Chile, confirmado por el usuario el 19-nov). Itaú suma 113 posiciones de seguros (539.870 MM$) que venían con los RUT antiguos.
  - 97053000 → 97080000 (BICE absorbió a Security, nov-2025). BICE suma 810 posiciones (286.775 MM$).
  - Se aplican en `emisor_efectivo` y también en la votación de mayoría, en los fondos mutuos (`issuer_options`, `issuer_holdings`) y en el cruce de nombres de la SP (`mapa_emisores_sp`).
  - Funciones: `fusiones()`, `fusion_sql(expr)` y `ruts_equivalentes(rut)`.
  - El panel de un RUT absorbido remite al sucesor; el del sucesor dice qué RUT absorbidos incluye.
  - 97032000 (ex BBVA) y 97051000 (ex Banco del Desarrollo) → 97018000 Scotiabank (confirmado por el usuario el 19-nov). Scotiabank suma 394 posiciones de seguros (207.086 MM$).

**Originadores de mutuos: ya tienen nombre.**
- Los 25 RUT de `MHA`/`MHB` que no tenían nombre se cargaron en `config/entidades.yaml` con la razón social que entregó el usuario el 19-nov. Por ejemplo: BICE Hipotecaria, Hipotecaria Security, Hipotecaria La Construcción, Evoluciona, Creditú, Penta Hipotecario y Citibank N.A. (`MHB`, tipo banco).
- Tipo `administradora_mutuos`.
- En 202609 no queda ningún originador de mutuos ni de leasing sin nombre.
- Los emisores de B.1 con nombre suben de 207 a 232 de 330.

---|---|---|
| 96777060 | 14.413 | 11 |
| 96538310 | 8.980 | 15 |
| 96546470 | 3.682 | 9 |
| 76090605 | 2.944 | 4 |
| 96786870 | 1.557 | 1 |
| 76697522 | 1.312 | 9 |
| 96559030 | 1.288 | 5 |
| 76594721 | 1.149 | 5 |
| 96778070 | 876 | 5 |
| 96542350 | 873 | 2 |

Hay además 97008000 (`MHB`, 36 posiciones).

---

## F4 — Concentración por emisor de seguros: monto expuesto y filas expandibles

**Diagnóstico.**

- `/seguros/cartera`, sección de concentración por emisor (`seguros_cartera_emisores`, `get_insurer_issuer_exposure`).
- Ordena por **número de instrumentos** y muestra el nominal por moneda, sin sumar.
- El usuario quiere el **monto expuesto** y poder abrir cada emisor para ver quién tiene qué monto.

**Medida de monto** (la misma que ya usa `db/cross.py`, se centraliza):

- `monto_par_pesos` = nominal × tipo de cambio del cierre del mes:
  - el nominal es `valor_nominal_vig` si es mayor que 0; si no, `valor_nominal`;
  - `UF` × UF, `$$` × 1 y `PROM` × dólar observado;
  - otras monedas: `NULL`, se cuentan pero no se suman.
- Va en MM$, con `/1e6`.
- `origen` = `'vigente'` o `'nominal_origen'`, según qué campo se usó.

**Cambios.**

1. En `db/cross.py`:
   - `monto_par_sql(alias="fi")`: devuelve la expresión SQL;
   - `_fx_cte`: reutilízalo;
   - refactoriza `issuer_holdings` y `common_issuers` para que usen la función, sin cambiar sus cifras.
   - Test: las cifras de `issuer_holdings("97004000")` antes y después del refactor son iguales.
2. `get_insurer_issuer_exposure` (`db/repos/fecu.py`):
   - Ordena por `SUM(monto_par_pesos)` descendente.
   - Devuelve por emisor:
     - `mm` (MM$ par) y `pct` (% del total de la renta fija del alcance);
     - `posiciones` y `companias` (compañías tenedoras distintas, en el alcance de mercado);
     - `tipo_principal`, el de mayor monto, ya no el de mayor conteo;
     - `pct_origen`: % del monto que salió de `nominal_origen`;
     - `sin_fx`.
   - El desglose por moneda queda como detalle.
   - Ya usa el emisor canónico (sección «Ya hecho»); consérvalo al reescribir la consulta.
3. Nuevo endpoint `GET /seguros/cartera/emisores/detalle?ramo=&period=&rut=&emisor=`
   (`api/routers/web/institucional.py`) y parcial `partials/seguros_cartera_emisor_detalle.html`:
   - **Vista de mercado** (`rut` vacío): las compañías que tienen ese emisor, con MM$, % del emisor, posiciones y tipo principal, ordenadas por MM$.
   - **Vista de una compañía**: sus instrumentos de ese emisor, uno por nemotécnico (nemotécnico, tipo, moneda, nominal en su unidad, MM$, origen del nominal).
4. En `partials/seguros_cartera_emisores.html`:
   - Cada fila de emisor lleva en la celda del nombre un `<details>` con `<summary>` (nombre del emisor).
   - Dentro, un `<div hx-get="/seguros/cartera/emisores/detalle?..." hx-trigger="toggle from:closest details once" hx-swap="innerHTML">Cargando…</div>`.
   - Prueba que el evento `toggle` dispare en HTMX 2.0.4. Si no dispara, usa `hx-trigger="click from:closest summary once"`.
   - Columnas: Emisor (expandible) · Tipo principal · MM$ par · % de la renta fija · Posiciones · Compañías (solo en mercado).
   - El nominal por moneda pasa al detalle.
5. Rótulos y notas:
   - Columna «MM$ (valor par)».
   - Nota: «Valor par: saldo vigente cuando la compañía lo informa; si no, monto de origen, que sobrestima lo expuesto en leasing y letras hipotecarias porque ya amortizaron. No es valor de mercado.»
   - Si `pct_origen > 50` en una fila, una marca `ⓘ` con `title=` que lo diga.
   - Leasing propio: si `emisor == rut` de la compañía y el tipo es `CLEAS`, la fila dice «Leasing inmobiliario propio (la compañía es la arrendadora)».
6. Sube el top de 15 a 20 y agrega «ver los N emisores» como enlace que recarga con `limit=200`.
   - Valida `limit` en el router con un máximo de 500.

**Tests.**

- La suma de `mm` del top con `limit=500` coincide (±0,5%) con la suma directa de `monto_par_pesos` del alcance.
- El endpoint de detalle responde 200 en mercado y en compañía, y su total coincide con la fila madre.

**Control.** Captura `/seguros/cartera` (escritorio y celular). Abre un `<details>` en la
captura con un `?abrir=` de prueba o, mejor, con un fragmento HTMX directo.

---

## F5 — Tipo de instrumento en la exposición por emisor

**Diagnóstico.**

- `/inversion-institucional/emisor` muestra por fuente (seguros, fondos mutuos, AFP) cuánto hay invertido, pero no en qué.
- Los datos ya lo tienen:
  - **Seguros (B.1):** `tipo_instrumento`. B.1 es **solo renta fija**: las acciones de las compañías no están en este archivo, y el título del bloque debe decirlo.
  - **Fondos mutuos (NACI):** `FFM_6010400` en `raw_data`. Códigos de 202609: `BB`, `BE`, `DPC`, `ACC`, `BTU`, `BTP`, `PDBC`, `CFM`, `LH`, `BU`, `CFI`, `PE`, `DPL`, `BNEE`, `BS`…
  - **AFP (SP, `emisor_nac`):** la columna lleva el código del instrumento, con `metrica='porcentaje'` (% del fondo); el monto total está en `columna='total'`. Ejemplo: Empresas CMPC solo `ACC` e Inversiones CMPC solo `DEB`.

**Cambios.**

1. Nuevo `config/tipos_instrumento.yaml`, con familia por código y evidencia.
   - Solo los inequívocos; el resto con `familia: null` (se muestra el código crudo).
   - Propuesta inicial. Confirma cada uno con un nemotécnico real de la base: el prefijo debe calzar con la familia.

   | Código | Fuente | Familia |
   |---|---|---|
   | `BE`, `DEB` | B.1/FFMM, SP | Bono corporativo |
   | `BB`, `BS`, `BEF`, `BSF` | B.1/FFMM, SP | Bono bancario |
   | `BTU`, `BTP`, `BCU`, `BCP`, `PDBC` | todas | Deuda del Fisco o del Banco Central |
   | `LH`, `LHF` | B.1/FFMM, SP | Letra hipotecaria |
   | `DPC`, `DPL`, `DPF` | B.1/FFMM, SP | Depósito a plazo |
   | `MHA`, `MHB` | B.1 | Mutuo hipotecario endosable (glosas oficiales ya en `api/insurer_glossary.py`) |
   | `CLEAS` | B.1 | Leasing inmobiliario (oficial) |
   | `BNEE`, `BBNEE` | B.1/FFMM | Bono emitido en el exterior |
   | `ACC` | FFMM, SP | Acción |
   | `CFI`, `CFM` | FFMM | Cuota de fondo de inversión / de fondo mutuo |

   Si un código no calza con su prefijo, déjalo `null`. Los de la SP que no están en la tabla
   (`WNMC`, `YNMV`, `PFI`, `CC2`, `SNT`…) quedan `null`: su diccionario está pendiente en la
   hoja de ruta.
2. En `db/cross.issuer_holdings`, cada bloque (`seguros`, `ffmm`, `afp`) gana `por_tipo`: una
   lista `{codigo, familia, mm, pct}` del último período.
   - Seguros: suma `monto_par_pesos` por `tipo_instrumento`.
   - Fondos mutuos: suma la valorización por `FFM_6010400`.
   - AFP: reparte el `monto_pesos` total de cada (AFP, fondo, emisor) según el peso relativo de sus columnas de `porcentaje`. Es un reparto proporcional: el reparto exacto no existe en la fuente, y la nota debe decirlo.
   - Cada tenedor (fila de la tabla) gana `tipos`: los códigos, separados por coma.
3. En `partials/institucional_emisor_panel.html`:
   - Cada bloque muestra una barra apilada horizontal 100% con `INF.PALETA`, leyenda arriba y familias, en una fila bajo el gráfico de la serie. Si es más simple, una tabla compacta «Tipo · MM$ · %».
   - Los títulos de bloque quedan así:
     - «Compañías de seguros · renta fija (B.1 no incluye acciones)»
     - «Fondos mutuos · cartera nacional»
     - «AFP · cartera nacional».
   - Columna «Instrumentos» en la tabla de tenedores.

**Tests.**

- Para 90222000, el bloque AFP tiene `ACC` como único tipo, o el de mayor peso.
- Para 96596540, el bloque seguros tiene `BE` como tipo principal.
- La suma de `por_tipo.mm` coincide con el total del bloque (±0,5%).

**Control.** Captura `?rut=90222000`, `?rut=96596540` y `?rut=97004000`.

---

## F6 — Emisores en común con AFP

**Diagnóstico.**

- `common_issuers` cruza seguros con fondos mutuos por nemotécnico. La AFP no entra.
- Además, `cias_seg` y `fondos_ffm` se suman por nemotécnico, así que una compañía con dos bonos cuenta dos veces; la nota lo reconoce.

**Cambios.**

1. Reescribe `common_issuers` para cruzar **por RUT del emisor**:
   - **Seguros:** emisor canónico (`emisor_efectivo`, ya hecho) y monto par (F4). Excluye `MHA`, `MHB` y `CLEAS`: son originadores, no emisores de mercado.
   - **Fondos mutuos:** `FFM_6010211`.
   - **AFP:** `mapa_emisores_sp`, último período de `emisor_nac`, `columna='total'`, `metrica='monto_pesos'` y `afp <> 'TOTAL'`.
   - Por emisor:
     - MM$ por fuente;
     - nº de tenedores **distintos** por fuente: compañías, fondos, AFP;
     - `fuentes` (1–3);
     - total.
   - Parámetro `min_fuentes` con valor por defecto 2: «en común» = presente en al menos 2 de las 3.
2. En `institucional_emisores.html`:
   - Columnas Seguros / Fondos mutuos / AFP, con MM$ y tenedores (en `.sub`), más el total.
   - Un selector «presente en: 2 o más / las 3 fuentes». Lleva `name="min_fuentes"` y es un `hx-get` de la misma página que reemplaza la tabla. Es un id nuevo: regenera el snapshot.
   - Cada emisor enlaza a `/inversion-institucional/emisor?rut=…`.
   - Período por fuente en la nota. La SP llega con ~4 meses de rezago: «AFP: {{ per_afp }}».
   - Borra la nota de doble conteo, que ya no aplica, y explica las tres medidas: seguros a valor par, y fondos mutuos y AFP a valor de mercado.
   - Agrega la cobertura del cruce AFP: «N de M emisores de la SP con RUT identificado (X% del monto)».

**Tests.**

- Los tenedores son distintos: para un emisor, `cias_seg` ≤ número de compañías del período.
- Banco de Chile (97004000) aparece con las 3 fuentes.
- El MM$ de AFP de un emisor coincide con el bloque AFP de `issuer_holdings` para el mismo RUT y período.

**Control.** Captura `/inversion-institucional/emisores`.

---

## F7 — Cobertura de derivados de seguros: medida por monto

**Diagnóstico.** Ver la respuesta a la pregunta: hay dos «coberturas» distintas y las dos
tienen defectos.

**Definición nueva** (una sola función y un solo nombre):

- **Cobertura cambiaria de la compañía (%)** = venta neta de moneda extranjera a plazo ÷ activos en moneda extranjera × 100.
  - **Numerador:** −`me_usd` de forwards y swaps válidos del mes (ya calculado en `db/derivados_seguros._frame`). Se muestra aparte cuánto viene de contratos con objetivo `CBE` (cobertura explícita).
  - **Denominador:** activos en moneda extranjera, en USD:
    - inversiones en el extranjero de B.8 (ya calculadas en `coberturas_seguros` con `describe(code)["geo"] == "extranjero"`);
    - **más** la renta fija nacional de B.1 en monedas extranjeras (`unidad_monetaria NOT IN ('UF','$$')`), a monto par con el dólar del cierre (F4).
    - Evita el doble conteo: los códigos de B.8 «extranjero» no incluyen la renta fija nacional en dólares. Verifícalo comparando, en una compañía, el total `PROM` de B.1 con los códigos B.8 nacionales vs. extranjeros. Si hay solapamiento, documenta y usa solo B.8 más la parte de B.1 cuyo `pais = 'CL'`.
- **Calidad:** % del valor bruto excluido por patas incoherentes (`~valido`), siempre visible.
- **Contratos declarados de cobertura (% del número):** la medida vieja de `/inversion-institucional/derivados`, con ese nombre exacto, como dato secundario.

**Cambios.**

1. En `db/derivados_global.coberturas_seguros`:
   - el denominador nuevo (`ext_usd` pasa a ser `activos_me_usd`; deja también `ext_b8_usd` y `me_b1_usd`);
   - `pct_excluido`;
   - `cbe_usd`.
2. En `db/derivados_seguros.resumen_seguros`, renombra la clave `pct_cobertura` a `pct_contratos_cobertura`. Agrega `pct_cobertura_monto` con la misma fórmula de (1), por compañía. Extrae la fórmula a una función compartida; no la copies.
3. En `institucional_derivados.html` y `derivados.html`:
   - la columna «% cobertura» pasa a llamarse «% cubierto (monto)» y se agrega «% contratos declarados de cobertura»;
   - un `ui.metodo` explica la fórmula en tres líneas: numerador, denominador y qué se excluye.
4. Las alertas de `derivados_global.alertas` que usan `cobertura > 120` siguen con la medida nueva. Revisa que los textos sigan siendo ciertos.

**Tests.**

- La cobertura del total del sector está entre 0 y 300%.
- `activos_me_usd ≥ ext_b8_usd` por compañía.
- Las claves renombradas existen en las plantillas (el test de render 200 cubre `/derivados` y `/inversion-institucional/derivados`).

**Control.** Captura las dos páginas. Anota en la nota de cierre cómo cambió la cobertura del
sector, antes vs. después.

---

## F8 — Ranking: 15 peores e indicadores de salud financiera

**Diagnóstico.**

- `/ranking` muestra solo las 15 empresas con el valor **más alto**.
- Ordena descendente para todos los indicadores, aunque en «Deuda / Patrimonio» más alto es peor.
- `is_pct` está duplicado y no coincide: el router incluye `margen_ebit` y `margen_ebitda`; `get_ratios_ranking` (`db/repos/eeff.py:220`) no. Por eso el filtro de |valor| ≤ 150 no se aplica a esos márgenes.
- Los ratios de flujo (ROE, ROA, márgenes) usan el **acumulado del año**: en 202606 el ROE es de medio año, y no se compara con el de diciembre.

**Cambios.**

1. **Dirección y tipo de cada indicador**, en un solo diccionario en `api/routers/web/_shared.py`, junto a `_RATIO_LABELS`:
   - `RATIO_META = {clave: {"label", "pct": bool, "mejor": "alto"|"bajo"}}`.
   - `endeudamiento` y `pasivo_activo` van con `mejor="bajo"`; el resto, con `"alto"`.
   - El router y `get_ratios_ranking` lo leen: borra las dos listas `is_pct`.
2. **Flujos anualizados (últimos 12 meses):**
   - en `db/common.py`, `ltm(df_actual, df_dic_anterior, df_mismo_trimestre_anterior)`, o una función en el repo que arme el LTM por cuenta de flujo:
     - LTM = acumulado actual + diciembre anterior − acumulado del mismo trimestre del año anterior;
     - en diciembre, LTM = el anual.
   - `get_ratios_ranking(period, metric, n, sector, ltm=True)` usa LTM para las cuentas del estado de resultados: ingresos, ganancia, EBIT, costos financieros y depreciación.
   - Si falta un período para el LTM, la empresa queda fuera de los indicadores de flujo, pero no del balance.
   - La tabla lo dice: «Flujos de los últimos 12 meses».
3. **Indicadores de salud nuevos**, en `compute_ratios` o en una función `salud(...)` que reciba balance y LTM:
   - **Z'' de Altman (versión para no manufactureras y mercados emergentes):**
     - Z'' = 6,56·X1 + 3,26·X2 + 6,72·X3 + 1,05·X4;
     - X1 = (activos corrientes − pasivos corrientes) / activos totales;
     - X2 = «Ganancias (pérdidas) acumuladas» / activos totales;
     - X3 = EBIT LTM («Ganancias (pérdidas) de actividades operacionales») / activos totales;
     - X4 = «Patrimonio total» / «Total de pasivos».
     - Zonas: **> 2,6 segura · 1,1–2,6 gris · < 1,1 peligro**.
     - Cobertura en 202512: 402 de 564 empresas traen ganancias acumuladas. Las que no, quedan sin Z.
     - **Se excluyen** los sectores «Servicios Financieros» y «Holdings e Inversiones» (`api/company_profiles.get_profile(...)["macro_sector"]`), y también las AGF: el Z no está calibrado para ellos. La tabla lo dice.
   - **Deuda financiera neta / EBITDA LTM:**
     - (Otros pasivos financieros corrientes + no corrientes + pasivos por arrendamientos corrientes + no corrientes − efectivo y equivalentes) / EBITDA LTM;
     - `mejor="bajo"`;
     - si el EBITDA es ≤ 0 se muestra «EBITDA negativo» y se ordena como el peor.
   - `cobertura_intereses` ya existe: pasa a LTM.
   - Agrega `z_altman` y `deuda_neta_ebitda` a `RATIO_META` y a los selectores.
4. **Tabla de ranking** (`partials/ranking_table.html`):
   - Dos tablas lado a lado en escritorio (apiladas en celular): «15 mejores» y «15 peores», según `mejor`.
   - Columna fija **«Salud (Z'')»** en ambas, sea cual sea el indicador elegido: el valor con una insignia de zona (segura / gris / peligro). La insignia es solo texto y forma: el color rojo se reserva para «peligro», que es deterioro.
   - Nota: «N empresas con dato; M excluidas por sector financiero o falta de cuentas».
   - En el router: `get_ratios_ranking` devuelve la lista completa ordenada (o `n=None`) y se cortan las dos puntas, así no se recalcula dos veces.
   - Es lento hoy (~1 s por render). Agrega un cache por proceso con clave `(period, sector)` que guarde todos los ratios del período, como `_OPCIONES_EMISOR`.
5. En `metodologia.html`, un apartado «Z de Altman y salud financiera» con la fórmula, las zonas, las exclusiones y la referencia (Altman, 1995/2005, versión Z'' para mercados emergentes). Va en un `ui.metodo` si la página los usa.

**Tests.**

- Con `mem_db` y valores sintéticos conocidos, `z_altman` da el número esperado (calculado a mano en el test).
- LTM con tres períodos sintéticos da la suma correcta.
- `/ranking/table?metric=endeudamiento` pone en «mejores» valores menores que en «peores».
- `/ranking/table?metric=z_altman` responde 200 y no incluye empresas del sector financiero.

**Control.** Captura `/ranking` con 3 indicadores (ROE, endeudamiento, Z'') usando el fragmento
`/ranking/table?...` directo si la captura no cambia el selector.

---

## F9 — Vincular ranking y exposición por emisor

**Objetivo del usuario.** Los mejores y peores del ranking son los emisores de mayor interés:
desde el ranking se debe poder ver quién los tiene, y desde el selector de emisor se debe poder
llegar a ellos.

**Cambios.**

1. En `db/cross.py`, `ruts_con_tenencia(db) -> set[str]`: los RUT con posiciones en el último período de alguna de las tres fuentes. Reutiliza `issuer_options` y el cache `_OPCIONES_EMISOR`; no hagas otra consulta pesada.
2. En `partials/ranking_table.html`, si el RUT de la empresa está en ese conjunto, agrega junto al nombre un enlace «¿quién la tiene?» a `/inversion-institucional/emisor?rut={{ r.rut }}`. Usa el ícono Lucide existente más cercano (`ui.icono` si existe) y `aria-label`.
3. **Emisores de interés** en `institucional_emisor.html`: reemplaza los 5 atajos fijos por dos listas cortas de enlaces, generadas en el router desde el cache del ranking (F8) cruzado con `ruts_con_tenencia`:
   - «Mayor riesgo con inversión institucional»: hasta 8 empresas en **zona de peligro del Z''**, ordenadas por MM$ institucional total, de mayor a menor;
   - «Mejor salud con inversión institucional»: hasta 8 con mayor Z''.
   - Deja también Fisco, Banco Central y Banco de Chile como «Atajos».
   - El MM$ total por RUT sale de `issuer_options`; agrégale el monto si hoy solo trae conteos, usando `monto_par_sql` y la valorización de los fondos mutuos.
4. En el panel del emisor (`institucional_emisor_panel.html`), si el RUT tiene Z'', muestra una línea con su valor, su zona y el período, enlazada a `/ranking?metric=z_altman`. Solo se muestra si la empresa está en el ranking.

**Tests.**

- `/inversion-institucional/emisor` responde 200 y muestra al menos un enlace de cada lista cuando hay datos.
- `/ranking/table` contiene `href="/inversion-institucional/emisor?rut=` para al menos una fila.

**Control.** Captura `/ranking` y `/inversion-institucional/emisor`.

---

## F10 — Panel de portada: indicadores de mercado

**Estudio hecho (19-nov).**

- Hoy el panel muestra 4 tarjetas (PIB, IMACEC, IPC, dólar), las tasas BCP/BCU y de colocación, las AFP, la banca y los gráficos de UF y TPM.
- Las tarjetas **no muestran fecha ni fuente**, aunque la bajada lo promete.
- Las series siguientes **ya están en la base** (`observations`) y no requieren fuentes nuevas. Elegidas por relevancia para un lector financiero:

| Indicador | Serie | Frecuencia, último dato | Cómo se muestra |
|---|---|---|---|
| TPM y lo que espera el mercado a 12 meses | `F022.TPM.TIN.D001.NO.Z.D`, `F089.EOF.TPM.12MS.D` | diaria / quincenal (nov-2026) | «4,50% · EOF 12m: X%» |
| Pendiente de la curva swap pesos (10a − 2a) | `F022.SPC.TIN.AN10.NO.Z.D` − `F022.SPC.TIN.AN02.NO.Z.D` | diaria | pb con signo |
| Inflación esperada: compensación swap 2 años y forward 5 en 5 | `F022.SWSP.TAS.AN02.Z.Z.D`, `F022.SWFW.TAS.E5X5.Z.Z.D` | diaria | % frente a la meta 3% (delta en pb vs 3%, `invertir=true`) |
| Expectativa de inflación de la EEE a 11 meses | `F089.IPC.V12.14.M` | mensual | % |
| Cobre | `F019.PPB.PRE.40.M` (USD/lb) | mensual (oct-2026) | USD/lb + var. 12m |
| IPSA | `F013.IBC.IND.N.7.LAC.CL.CLP.BLO.M` | **mensual** (sep-2026) | var. 12m, con la fecha bien visible |
| Bono del Tesoro EE.UU. a 10 años y spread soberano | `F019.TBG.TAS.10.M`; BCP 10a − UST 10a | mensual (oct-2026) | % y pb |
| Fed | `F019.TPM.TIN.10.D` | diaria | % |
| Desempleo | `F049.DES.TAS.INE.10.M` | mensual (sep-2026) | % + delta 12m (`invertir=true`) |
| Spread depósitos 90 días vs swap | `F022.TDB.TIS.D090.NO.Z.D` − `F022.SPC.TPR.D090.NO.Z.D` | diaria | pb (riesgo bancario) |

Descartados por ahora porque requieren una fuente nueva, y quedan en la hoja de ruta: IPSA
diario, petróleo, EMBI o CDS de Chile, VIX. No los agregues en este plan.

**Cambios.**

1. En `db/repos/panel.py`:
   - agrega las series a `KPI_SERIES`;
   - un bloque `mercado2` en `get_overview_kpis`: `{valor, fecha, delta, fuente}` por indicador;
   - un helper `latest_with_date(sid)` que devuelve valor y fecha. `get_latest_value` probablemente ya trae `date`: revísalo.
2. Todas las tarjetas, **también las 4 existentes**, pasan `fecha=` y `fuente=` a `ui.stat`.
3. En `partials/kpi_cards.html`:
   - Una segunda fila de tarjetas, «Tasas y expectativas»: TPM + EOF 12m, pendiente 10–2, compensación 2a y 5y5 vs meta, EEE 11m.
   - Una tercera fila, «Externo y riesgo»: cobre, IPSA, UST 10a + spread, Fed, desempleo, spread depósitos–swap.
   - Rejilla de 4 columnas en escritorio, 2 en celular. Usa las clases de rejilla existentes de F3 del plan estético; no crees otras.
   - Los deltas siguen la regla «verde solo signo o mejora».
4. El botón «Actualizar indicadores» (`/api/kpi/overview/cards`) debe seguir funcionando: es el mismo parcial. Si el endpoint JSON de la API pública expone `get_overview_kpis`, agregar claves no rompe nada; no quites ninguna.

**Tests.** Render 200 de `/` y de `/api/kpi/overview/cards`. Las 4 tarjetas originales muestran
una fecha (`class="corte"` aparece al menos 4 veces más que antes).

**Control.** Captura `/` a 1.440 y 390 px. Sin desborde y sin letra chica.

---

## F11 — Investigación: valor de mercado de la renta fija de seguros (opcional, tope 2 horas)

**Por qué.**

- F4 y F6 usan el valor par, que en leasing y letras se sobrestima.
- El registro B.1 de 970 caracteres trae montos de valorización después de la posición 154 que nunca se reconciliaron: el spec publicado suma 878 caracteres.
- Está anotado en `docs/wiki/Hoja-de-Ruta.md` §3.

**Pasos.**

1. Descarga **un** período de vida, el más reciente, con `collectors.seguros_cartera.download_cartera`, a `data/seguros_cartera_raw`.
2. Lee los registros tipo 2 del archivo B.1 (prefijo `i`) de 3 compañías grandes y estudia las posiciones 154–970: busca campos numéricos de ancho fijo.
3. **Criterio de reconciliación (obligatorio para adoptar un campo):**
   - para cada compañía, la suma del campo candidato en sus posiciones de renta fija coincide (±1%) con la suma del B.8 de los códigos de inversión de renta fija (`api/insurer_glossary.describe(code)["clase"]` en `rf_*`) del mismo período;
   - debe cumplirse en ≥ 90% de las compañías;
   - prueba con y sin decimales implícitos, y en M$ o en $.
4. **Si reconcilia:**
   - agrega el campo a `B1_FIELDS` con el comentario de la reconciliación;
   - agrega la columna en `db/schema.py` con migración;
   - recarga solo ese período con `import-cartera-seguros`;
   - deja el backfill documentado como comando, sin correrlo;
   - el monto de F4 usa el valor de mercado cuando existe.
5. **Si no reconcilia en 2 horas:** documenta lo probado en la hoja de ruta y no cambies nada.
6. En los dos casos, **borra el ZIP descargado**.

---

## F12 — Cierre

1. Este archivo: tabla de estado con commits y una «Nota de cierre» con:
   - la cobertura de seguros antes y después (F7);
   - el número de empresas con Z'' (F8);
   - el resultado de F11.
2. `docs/wiki/Hoja-de-Ruta.md`: marca lo hecho y agrega lo pendiente:
   - emisor canónico, fusiones (Itaú, BICE, Scotiabank) y originadores ya nombrados (sección «Ya hecho»), como hechos;
   - diccionario de códigos SP;
   - fuentes descartadas en F10.
3. `api/templates/metodologia.html`:
   - emisor canónico (regla de la sección «Ya hecho»);
   - valor par (F4);
   - cobertura cambiaria (F7);
   - Z'' (F8).
4. Prueba completa:
   - `pytest -q`;
   - `ui_guard.py check`;
   - `scripts/ui_capturas.py --label final19` sobre todas las vistas, con 0 desbordes y 0 letra chica;
   - barrido de errores de JavaScript, como en la revisión estética. Si no existe como script, abre cada página tocada en Chrome headless y revisa la consola.
5. Mata el servidor de `:8001`. Commit y push.
