# Plan: monitor de posiciones institucionales en derivados

> 17-nov-2026, revisado el mismo día. Pregunta: ¿cómo están posicionados los institucionales
> en derivados, en qué dirección y con qué tipo de instrumento? Objetivo: **monitorear**
> esas posiciones. El caso que lo motiva son los swaps de tasa de EE.UU. de las AFP en 2026,
> que llevaron al Fondo E a su peor año. Alcance aprobado: las tres fases.

## Estado de ejecución (17-nov-2026): fases 1–4 hechas

Vista: `/inversion-institucional/derivados` (tarjeta "Derivados" del hub institucional).

| Fase | Hecho | Validación |
|---|---|---|
| 1 · Banco Central + monitor | 73 series `F099.DER` (categoría `derivados_sector`) + bono 10 años y tasa Fed de EE.UU., cargadas desde 2022 (5.498 obs.) y en el catch-up. `db/derivados.py`: monitor (nocional/activos, neto en tasas extranjeras y cámara, saldo vs. flujo, alertas, estado del cierre), sectores, detalle AFP (dirección, plazos, contraparte, cobertura cambiaria 27,3%). Hitos en `config/hitos_derivados.yaml`. | 8 identidades cuadran en 57 meses (desvío máx. 0,1%) y se testean. Signo confirmado con el Informe Mensual del Mercado de Derivados del BCCh. Alertas con reglas fijas: marcan abr–sep 2026 (19 a 64 desvíos) y dejan 11 falsas alarmas chicas en 2023–2025, que se muestran. |
| 2 · Fondos mutuos | Vista `v_mutual_fund_derivatives` (migración 011) sobre FUTU/OPCI con subyacente normalizado; `db/derivados_ffmm.py`. 125 fondos con derivados en sep-2026. | Neto USD con el mismo signo que el sector AGF del BCCh en los 27 meses (30–60% del sectorial: AGF incluye fondos de inversión). |
| 3 · Seguros (B.7) | Parser `parse_zip_derivatives` (layout reconciliado), tabla `cmf_insurer_derivatives` (migración 012), carga en el catch-up y `main.py backfill-derivados-seguros`: 213 mil contratos, ene-2022 → sep-2026, ambos ramos. `db/derivados_seguros.py`. | Valor razonable = B.8 M10 exacto (±0,1%) en 994 de 1.064 compañía-mes (93%), ±5% en 98%. Posiciones solo desde dic-2024 (layout nuevo; antes el registro tiene otro orden). Contratos con precio implícito incoherente (±20% de UF/dólar del cierre) se excluyen de las posiciones: 15 de 6.110 en sep-2026. Neto B.7 −16.050 MMUSD vs. BCCh sector seguros −18.779: mismo signo y tendencia. |
| 4 · Por AFP (exploración) | No hay fuente estructurada por AFP ni por fondo. El detalle está en los estados financieros de cada fondo (formato no uniforme) y en declaraciones de la SP. Queda como pendiente. | — |

Pendientes de este plan: detalle por AFP/fondo (fase 4); verificar el cierre del 5-nov con el
dato de nov-2026 del BCCh (llega ~enero 2027; la vista lo dirá sola); futuros de seguros sin
valor reconciliado; posiciones de seguros antes de dic-2024 (layout antiguo sin reconciliar);
código de objetivo `1512` sin documentar.

## Respuesta corta

Se puede, con tres fuentes complementarias:

| Fuente | Quién | Granularidad | Dirección | Tipo / subyacente | Desde | Rezago | Estado en el proyecto |
|---|---|---|---|---|---|---|---|
| **Banco Central (BDE), familia `F099.DER`** | AFP (sistema), seguros, AGF, corredoras, empresas, no residentes, bancos | **Sector** (no por entidad) | comprador / vendedor / neto | forward y FX swap, cross currency swap, NDF, swap cámara, swaps sobre tasas extranjeras, opciones; USD-CLP, UF-CLP, pares de monedas | ene-2022 (mensual; algunas diarias) | ~2 meses (sep-2026 al 17-nov) | **no cargado**; se carga con el colector actual |
| **CMF · cartera de fondos mutuos, archivos FUTU y OPCI** | cada fondo mutuo | **Fondo** (y su AGF) | C / V por contrato | forward, swap, futuros, opciones; subyacente, vencimiento, nocional, precio pactado, valor de mercado | 2024 | ~1 mes | **ya cargado** (`cmf_mutual_fund_portfolio`), sin usar |
| **CMF · Circular 1.835, archivo B.7** "Inversiones en instrumentos derivados y pactos" | cada compañía de seguros | **Compañía** | por contrato | por contrato | según ZIP histórico | ~1–2 meses | **viene en el ZIP que ya bajamos**, no se parsea |

Lo que **no** hay en estas fuentes: el detalle **por AFP ni por fondo A–E**. El Banco Central
agrega todo el sistema de pensiones. Ver la fase 4 (fuentes a explorar).

## Swaps de tasa de las AFP: los nocionales son reales

La primera versión de este plan dudaba de dos series de AFP que parecían desproporcionadas
frente a los ~US$ 232 mil millones de activos del sistema (sep-2026): "total derivados"
(US$ 873 mil millones) y "swaps sobre tasas extranjeras, neto" (−US$ 212 mil millones). La
revisión muestra que **no hay error de unidades: son nocionales brutos, consistentes entre sí**.

**Identidades verificadas** (sep-2026, millones de USD):

| Componente | Igual a |
|---|---|
| Total **872.735** | tasa 641.483 + monedas 167.630 + inflación UF-CLP 63.621 |
| Tasa 641.483 | swaps cámara 139.847 + swaps sobre tasas extranjeras 501.636 |
| Monedas 167.630 | moneda local vs. extranjera 75.360 + pares de monedas extranjeras 92.271 |
| Swaps cámara | 136.016 miles de millones de pesos = US$ 139.847 millones al tipo de cambio |

Además, en tasas extranjeras, total = comprador + vendedor y neto = comprador − vendedor.

**Cómo se armó la posición en tasas extranjeras.** Swaps "tasas extranjeras" de los fondos de
pensiones; nocional vigente en millones de USD; neto = compra − venta de tasa variable.

| Mes | Comprador | Vendedor | Total | **Neto** | Total / activos del sistema |
|---|---|---|---|---|---|
| ene-2022 | 5.256 | 3.286 | 8.542 | +1.970 | 0,05× (activos US$ 168 mil mill.) |
| ene-2024 | 23.060 | 22.674 | 45.734 | +386 | 0,26× (177) |
| ene-2025 | 146.977 | 145.189 | 292.166 | +1.788 | 1,56× (188) |
| mar-2026 | 323.639 | 320.455 | 644.093 | +3.184 | 2,77× (233) |
| abr-2026 | 340.189 | 356.522 | 696.711 | **−16.333** | |
| may-2026 | 289.849 | 361.938 | 651.787 | **−72.088** | |
| jun-2026 | 215.591 | 363.757 | 579.348 | **−148.167** | |
| jul-2026 | 172.260 | 362.064 | 534.324 | **−189.805** | |
| ago-2026 | 156.912 | 368.493 | 525.405 | **−211.581** | |
| sep-2026 | 144.545 | 357.091 | 501.636 | **−212.547** | 2,16× (232); neto −0,92× |

Activos = patrimonio de los cinco fondos de las siete AFP (`sp_quota_values`) al dólar
observado del cierre de cada mes.

Hay dos hechos distintos:
1. **El nocional bruto se multiplicó por ~80 en cuatro años**, de US$ 8,5 mil millones a casi
   US$ 700 mil millones en abril de 2026. Hasta marzo de 2026 iba calzado: compras y ventas
   casi iguales, con neto cercano a cero.
2. **Desde abril de 2026 el libro se descalza.** Las compras de tasa variable caen de 340 a
   145 mil millones mientras las ventas se mantienen en ~360. El neto pasa de ~0 a −212 mil
   millones en cinco meses. Los **flujos** del mismo período (montos transados netos) son
   positivos: +65.562 en abril, +12.897 en mayo, etc. El salto del saldo no viene de ventas
   nuevas, sino de que vencieron o se cerraron las patas compradoras.

**Coincide con el episodio público del Fondo E** (prensa, oct–nov 2026):
- **Qué fue:** apuestas "en manada" de las siete AFP en swaps de tasas de EE.UU.
- **Caída del Fondo E:** real de 10,39% entre enero y septiembre (SP).
- **Atribución:** los swaps explicarían ~5,9 de los 7,4 puntos de pérdida nominal.
- **Valor de mercado de los swaps al 30-sep:** −US$ 10.064 millones en todos los fondos y
  −US$ 1.074 millones en el Fondo E. A fines de octubre, tras el repunte de los bonos de
  EE.UU., la pérdida bajaba a ~US$ 2.900 millones.
- **Hitos regulatorios:** la SP dictó límites al riesgo de derivados (resolución del 9 de
  abril), inició un proceso sancionatorio contra las siete AFP (20 de octubre) y fijó un
  plazo de cierre de contratos al 4–5 de noviembre.

Fuentes al final.

**Chequeo de coherencia** (grueso, no para publicar como cifra):
- **Coherencia del signo:** "neto vendedor de tasa variable" = paga variable y recibe fija,
  o sea gana si las tasas bajan. El bono a 10 años de EE.UU. subió de 4,13% (feb) a 4,99%
  (sep), +86 pb en promedios mensuales.
- **Duración implícita:** la pérdida reportada (US$ 10 mil millones) es 4,7% del neto, lo que
  equivale a ~5,5 años. Es lo esperable para swaps de 5 a 10 años. El signo y el orden de
  magnitud calzan.
- **Lo que confirmaría:** la convención de signo hay que confirmarla con la nota metodológica
  del Banco Central antes de rotular el gráfico.
- **Fondo E por AFP:** en los valores cuota que ya tenemos, la caída es pareja entre AFP. En
  julio todas pierden entre −3,1% y −3,6%, y en septiembre entre −4,1% y −4,8%. Es
  consistente con una posición tomada por todas, no por una.

**Lo que el Banco Central no dice**, y la vista debe aclarar:
- Qué fondo (A–E) ni qué AFP tiene la posición. La prensa la atribuye sobre todo al E y
  también a otros fondos.
- Plazo ni duración de los swaps sobre tasas extranjeras: la serie no viene por plazo.
- Valor de mercado ni pérdida: solo nocional.
- Contraparte: hay series por bancos locales y no residentes para dólar-peso y swap cámara,
  no para tasas extranjeras.

**Swap cámara (tasa en pesos)** muestra un giro parecido, más chico: el neto hasta 2 años
pasó de +13.911 (sep-2025) a −32.435 miles de millones de pesos (sep-2026).

## Otras posiciones (sep-2026)

Montos vigentes (nocional), millones de dólares. Neto = comprador − vendedor de dólares
(verificado: AFP 16.502 − 58.562 = −42.060; seguros 339 − 19.117 = −18.778).

| Sector | Comprador USD | Vendedor USD | **Neto** | Neto hace 12 meses | Lectura |
|---|---|---|---|---|---|
| Fondos de pensiones (USD-CLP) | 16.502 | 58.562 | **−42.059** | −25.498 | venden dólares a plazo: cubren su inversión extranjera; +65% en un año |
| Compañías de seguros (moneda extranjera vs. peso) | 339 | 19.117 | **−18.779** | −15.936 | casi solo venden: cobertura de bonos en dólares |
| AGF (fondos mutuos y de inversión) | — | — | **−1.572** | −820 | chico, también vendedor |
| Empresas sector real (vs. bancos) | — | — | **+10.151** | +9.156 | compran dólares a plazo (importadores, deuda en USD) |
| No residentes (vs. bancos) | — | — | **+27.098** | +12.763 | compran dólares contra peso: posición corta en peso, se duplicó |

Convención para dólar-peso: neto negativo = vendedor de dólares a plazo = protegido contra
una **caída** del dólar (lo hace quien tiene activos en dólares y quiere fijar su valor en pesos).

Las AFP tienen además:
- UF-CLP neto +718.742 miles de UF (ganan inflación vía derivados).
- Pares de monedas extranjeras por US$ 92 mil millones: USD-EUR, JPY, GBP, BRL, MXN y CNY.
- Perfil de vencimientos del dólar-peso por plazo residual. Más de un año suma solo −3.462.

Fondos mutuos (FUTU, sep-2026): 43–51 fondos con forwards de dólar a cada lado, swaps en UF y
pesos, futuros sobre índices (Nasdaq) y opciones (S&P 500, SQM). El subyacente viene rotulado
de forma inconsistente (`PROM`, `DOLAR`, `USD` y `FORWARD PROM` son lo mismo); hay que
normalizarlo. Seguros: el archivo de control (B.8) solo trae el valor neto de derivados y
pactos (vida: 678 MMM$), sin dirección ni tipo; el detalle está en el B.7.

## Vista: `/inversion-institucional/derivados`

### Bloque 1 · Monitor de riesgo (lo primero de la página)

Responde: ¿está creciendo una posición, cambió de dirección, cuánto pesa frente a los
activos, y se está cumpliendo el cierre exigido?

| Indicador | Fuente | Por qué |
|---|---|---|
| Nocional bruto de swaps de tasa / activos del sistema | BCCh (tasa, cámara, extranjeras) ÷ patrimonio SP (`sp_quota_values`, a USD) | apalancamiento nocional: 0,23× en ene-2022 → 3,56× en mar-2026 → 2,77× en sep-2026 |
| Neto de tasas extranjeras y de swap cámara | BCCh | dirección de la apuesta; el descalce de abril-2026 es la señal que se buscaba |
| Saldo vs. flujos del mes | BCCh `STO` vs. `FLU` | distingue posición nueva de patas que vencen o se cierran |
| Contexto de mercado: bono 10 años de EE.UU. (`F019.TBG.TAS.10.M`) y tasa Fed (`F019.TPM.TIN.10.D`) | BCCh | contra qué movimiento se gana o se pierde; panel aparte con el mismo eje de tiempo, nunca doble eje |
| Retorno mensual de los fondos E y D por AFP, y dispersión entre AFP | SP (ya cargado) | efecto en el afiliado; dispersión baja = posición tomada por todas ("manada") |
| Hitos: resolución SP del 9-abr-2026, proceso sancionatorio 20-oct-2026, plazo de cierre 4–5-nov-2026 | curados en `config/hitos_derivados.yaml`, con fuente | anclar la lectura en el tiempo |

**Alertas** (texto destacado en la vista, no notificaciones):
- variación mensual del neto mayor que N desvíos de su historia 2022–2026;
- cambio de signo del neto;
- nocional bruto / activos sobre un umbral.

Se calibran sobre la historia. Como el episodio de 2026 ya se conoce, verificar que lo
habrían marcado es solo un control de cordura, no una validación. Los umbrales se fijan
antes de mirarlo y se anotan.

**Seguimiento del cierre:** el Banco Central publica con ~2 meses de rezago. Los datos de
oct–nov 2026 llegarán en diciembre–enero y deberían mostrar el neto de tasas extranjeras
yendo a cero después del 5-nov. La vista dice explícitamente si eso ocurrió o si el último
dato es anterior al plazo.

### Bloque 2 · Quién está de qué lado del dólar

Neto dólar-peso mensual desde 2022 por sector (AFP, seguros, AGF, empresas, no residentes),
en small multiples con el mismo eje.

### Bloque 3 · AFP en detalle

- Comprador vs. vendedor.
- Perfil de vencimientos (barras por plazo residual).
- Contraparte: bancos locales vs. no residentes.
- UF-CLP, swaps cámara por plazo y pares de monedas.
- **Tasa de cobertura cambiaria** = venta neta USD / inversión extranjera (filas en MMU$ de
  la cartera SP).

### Bloque 4 · Compañías de seguros

Agregado del Banco Central y, con la fase 3, por compañía (B.7): quién cubre, cuánto y con
qué instrumento.

### Bloque 5 · Fondos mutuos por fondo y AGF

FUTU/OPCI: neto en dólares, UF y tasa; mayores posiciones; fondos que usan derivados para
invertir (futuros de índice, opciones) y no solo para cubrir.

## Fases

| Fase | Qué | Esfuerzo | Validación antes de mostrar |
|---|---|---|---|
| **1 · Banco Central + monitor** | ~60 series mensuales en `config/series_catalog.yaml`, categoría `derivados_sector`: AFP completo (resumen, tasas extranjeras, cámara por plazo, dólar-peso por plazo y contraparte, UF-CLP, pares; saldos y flujos), seguros, AGF, empresas, no residentes, corredoras; bono 10 años y tasa Fed de EE.UU. Hitos curados. Bloques 1–3. | 2 días | Identidades de la sección anterior como **test automático** (total = suma de componentes; neto = compra − venta; suma por plazo = total). Convención de signo de "compra tasa variable" confirmada con la nota metodológica del Banco Central. |
| **2 · Fondos mutuos** | Vista `v_mutual_fund_derivatives` sobre FUTU/OPCI, subyacente normalizado, montos a pesos (regla de moneda del fondo de `db/cross.py`). Bloque 5. | 1 día | Suma de fondos vs. sector AGF del Banco Central: mismo signo y orden de magnitud (AGF incluye fondos de inversión, no calza exacto). |
| **3 · Seguros, archivo B.7** | Parser del archivo `p` del ZIP de la Circular 1.835 y tabla nueva. Bloque 4 por compañía. Volver a bajar los ZIP históricos: bajar, parsear, borrar (regla de disco). | 2–3 días | Reconciliar campo a campo contra el total de derivados del B.8 (M10/M20). Dos layouts: los anexos cambiaron el 1-dic-2024. |
| **4 · Por AFP y fondo (exploración)** | Buscar fuentes con el detalle que el Banco Central no da: estados financieros de los Fondos de Pensiones (notas de derivados por fondo), informes y oficios de la SP del proceso sancionatorio. | ½ día de búsqueda; el resto depende de lo que exista | Solo si la fuente cuadra con el agregado del Banco Central. |

Orden: 1 → 2 → 3, y la 4 en paralelo como búsqueda.

## Límites que la vista debe decir

- **Nocional no es pérdida ni riesgo.** Un swap de US$ 100 de nocional no arriesga US$ 100;
  su sensibilidad depende del plazo. El monitor muestra dirección y tamaño, no valor de mercado.
- **AFP: solo el sistema** en el Banco Central, sin AFP ni fondo A–E (hasta la fase 4).
- **Rezago:** ~2 meses; la serie empieza en ene-2022.
- **Sector según el Banco Central:** "AGF" incluye fondos mutuos y de inversión; "otros
  sectores" es residual.
- **Cifras de prensa:** los hitos y montos de pérdida citados vienen de prensa y de la SP
  según la prensa; se muestran como contexto, con su fuente, y no se mezclan con los datos.

## Decisiones

1. ~~¿Qué fases?~~ Las tres, más la exploración de la 4.
2. Incluir empresas y no residentes como contexto (por defecto sí).
3. Series diarias: no hay diarias para swaps de tasas extranjeras; para el monitor bastan las
   mensuales (por defecto, solo mensuales).

## Referencias

**Datos.**
- Catálogo BDE descargado con `SearchSeries`: 1.877 series diarias, 9.321 mensuales y
  8.782 trimestrales; 1.708 series `F099.DER` vigentes.
- Estructura del código:
  `F099.DER.{STO|FLU}.Z.{sector}.{R|N}.{contraparte}.{COM|VTA|NET|TOT}.{instrumento}.{unidad}.{subyacente}.{R|C}.{plazo}.0.{M|D}`.
  - Sectores: 38 AGF · 39 corredoras · 40 bancos · 42 fondos de pensiones · 44 seguros ·
    50 otros · 55A empresas · 63 residentes no bancos · NR no residentes.
  - `STO` = monto vigente; `FLU` = monto transado.
  - Subyacentes: `EXT` = tasas extranjeras, `SPC` = swap cámara, `IRT`/`FXT` = totales de
    tasa y de monedas.
- Series del monitor de AFP:
  - total `F099.DER.STO.Z.42.Z.Z.TOT.Z.MMUSD.Z.Z.Z.0.M`;
  - tasas extranjeras `F099.DER.STO.Z.42.Z.Z.{COM|VTA|NET|TOT}.SWP.MMUSD.EXT.Z.Z.0.M`;
  - swap cámara `F099.DER.STO.Z.42.Z.Z.{COM|VTA|NET|TOT}.SWP.MMMCLP.SPC.R.{HA02|MA02}.0.M`.

**Episodio de los swaps (prensa, oct–nov 2026).**
- BioBioChile, [La ruta del descalabro: informes reconstruyen cómo las AFP apostaron en manada en swaps en EEUU](https://www.biobiochile.cl/especial/bbcl-investiga/noticias/articulos/2026/11/01/la-ruta-del-descalabro-informes-reconstruyen-como-las-afp-apostaron-en-manada-en-swaps-en-eeuu.shtml)
- BioBioChile, [Superintendencia de Pensiones inicia proceso contra las siete AFP](https://www.biobiochile.cl/noticias/economia/actualidad-economica/2026/10/20/superintendencia-de-pensiones-inicia-proceso-contra-las-siete-afp-por-el-desplome-del-fondo-e.shtml)
- Emol, [AFP reducen en más de 60% sus pérdidas por swaps](https://www.emol.com/noticias/Economia/2026/11/11/1216739/afp-recuperacion-perdidas-fondo-e.html)
- Emol, [Swaps, "efecto manada" y deber fiduciario](https://www.emol.com/noticias/Economia/2026/10/20/1214603/perdidas-afp-fondo-e-conceptos.html)
- DF, [La apuesta de las AFP que gatilló la crisis de rentabilidad del fondo E](https://www.df.cl/senal-df/senales-financieras/la-apuesta-de-las-afp-que-gatillo-la-crisis-de-rentabilidad-del-fondo-e)
- Tesis de Barrio, [Fondo E: cómo funcionan los swaps de tasa de las AFP](https://tesisdebarrio.substack.com/p/fondo-e-como-funcionan-los-swaps)

**Normativa.**
- CMF, [Anexos Técnicos Circular N°1835](https://www.cmfchile.cl/sitio/seil/software-manual/sgsci/Anexos_Tecnicos_Circular_N1835.pdf)
  (archivo B.7) e [Informe normativo jul-2024](https://www.cmfchile.cl/sitio/aplic/serdoc/ver_sgd.php?s567=631dd8ea4a7729eb27dabd59ef94d965VFdwQmVVNUVRVE5OUkUwMVRXcFpOVTFuUFQwPQ%3D%3D&secuencia=-1&t=1722396046)
  (registro B.14, garantías de derivados).
- SP, [Operaciones con instrumentos derivados](https://www.spensiones.cl/portal/institucional/594/w3-article-6474.html)
  (régimen de inversión).

## Fuente nueva: cartera desagregada de la SP (hallada 17-nov-2026, sin cargar)

`https://www.spensiones.cl/apps/loadCarteras/loadCarInv.php?menu=sci&menuN1=estfinfp&menuN2=NOID&orden=10&periodo=AAAAMM&ext=.php`
→ enlace `GetFile_v2.0.php?param=…` a un ZIP (~3,7 MB) con un XML (~284 MB, se lee por streaming
desde el ZIP, sin descomprimir). Formato: https://www.spensiones.cl/xml/doc/apps/finmes/ultima_version_cartera_desagregada/

- 29 cuadros por tipo de fondo (A–E) y por AFP. Cuadros 26–29: **valorización de swaps** (MMUSD,
  con signo) por AFP × fondo × contraparte × tipo (tasas / moneda; garantías bilaterales / cámara).
  Cuadros 22–25: forwards en unidades del activo objeto por contraparte y AFP (glosas con código,
  p. ej. "WNMV": falta el diccionario). Cuadros 11 y 21: cartera por emisor e instrumento
  (21 = extranjero con ISIN). Trae el RUT de las 7 AFP.
- Jun-2026 (cuadrado contra las filas TOTAL): swaps −2.411 MMUSD extranjeros (−2.419 de tasas con
  garantías bilaterales) y −438 nacionales. Por AFP: Capital −701, Cuprum −580, Provida −543,
  Habitat −536, Modelo −315, PlanVital −130, Uno −45.
- Disponibilidad: mensual desde al menos 2010-12 hasta 2026-06. Jul–oct 2026 devuelven un archivo
  vacío al 17-nov (jun se publicó el 9-jul): no es un rezago fijo de ~40 días; causa sin confirmar.
- Peso estimado (medido con jun-2026: 261 mil celdas, ~15 bytes por celda en DuckDB): todo ≈ 4 MB/mes;
  sin cuadros redundantes (transpuestos por AFP y filas "Total") ≈ 1,5–2 MB/mes; solo derivados
  (22–29) ≈ 0,2 MB/mes.
