# 🇨🇱 INF_FIN_IA — Información de inversiones de Chile

Repositorio de datos financieros y macroeconómicos de Chile con actualización automática,
controles de calidad, métricas comparables de productos de inversión (fondos mutuos y
fondos de pensiones) y una web local para consultarlos.

> **Guía de desarrollo**: [`CONTEXTO.md`](CONTEXTO.md) — arquitectura, fuentes, decisiones y
> *gotchas*. **Leerla antes de tocar datos.** Plan vigente: [`docs/PLAN_MEJORAS.md`](docs/PLAN_MEJORAS.md)
> y [`docs/wiki/Hoja-de-Ruta.md`](docs/wiki/Hoja-de-Ruta.md).

---

## Qué contiene

| Fuente | Datos | Frecuencia |
|---|---|---|
| Banco Central (API BDE) | Macro 1975→hoy, expectativas EEE/EOF, bonos soberanos, swaps cámara, compensación inflacionaria, depósitos, tasas de captación, IPSA/IGPA e índices globales | diaria / mensual / trimestral |
| CMF | EEFF corporativos IFRS, bancos (con desglose por moneda), seguros e intermediarios (FECU) | mensual / trimestral |
| CMF · Circular 1.835 | Cartera de inversiones de seguros: composición (B.8) y renta fija instrumento a instrumento (B.1) | mensual |
| CMF · fondos mutuos | Valor cuota, patrimonio y partícipes por serie; cartera; comisiones; rentabilidades oficiales | mensual |
| Superintendencia de Pensiones | Valor cuota y patrimonio por AFP y fondo; cartera; cinta de precios | diaria / mensual |

Encima de las fuentes se construyen:

- **Maestra de entidades** (`dim_entidad`): una fila por RUT (emisores, bancos, AGF,
  aseguradoras, intermediarios) con su historia de razones sociales.
- **Catálogo de productos** (`dim_producto`): cada serie de fondo mutuo y cada fondo AFP, con
  su administradora, categoría y clase de inversionista.
- **Métricas** (`fact_producto_metricas`): rentabilidad nominal y real, posición y consistencia
  en la categoría, frente a un depósito a plazo, volatilidad, caída máxima, Sharpe, escala de
  riesgo 1–7, costo anual y en pesos a 10 años, patrimonio, flujo neto, partícipes y, en renta
  fija, duración efectiva.
- **Curvas de tasas** (`curva_ajuste`): swap y soberana, nominal y real, ajustadas a diario desde
  2005 con Nelson-Siegel-Svensson; de ellas salen la inflación implícita, la TPM que descuenta el
  mercado y el spread de los depósitos bancarios.
- **Controles de calidad** (`dq_results`): unicidad, identidad contable, completitud,
  continuidad, rangos y cruces entre tablas.

Base local: `data/finanzas_chile.duckdb` (DuckDB, no versionada): **~15,8 millones de filas,
967 MB** al 16-11-2026.

| Tabla | Filas | Cobertura |
|---|---:|---|
| `cmf_insurer_portfolio_fixed_income` | 8.216.035 | 2020-01 → 2026-09 |
| `cmf_bank_statements` | 1.959.738 | 2019-01 → 2026-09 |
| `cmf_financial_statements` | 1.534.346 | 2015-03 → 2026-06 |
| `sp_instrument_prices` | 1.093.373 | 2016-11 → 2026-11 (diaria los últimos 5 años, semanal antes) |
| `cmf_mutual_fund_portfolio` | 933.087 | 2024-07 → 2026-09 |
| `cmf_insurer_statements` | 736.622 | 2015-03 → 2026-06 (trimestral + anual auditada) |
| `sp_portfolio_holdings` | 356.177 | 2015-01 → 2026-06 |
| `cmf_mutual_fund_quota_values` | 308.973 | 2018-01 → 2026-10 (corte mensual) |
| `cmf_broker_statements` | 213.325 | 2015-03 → 2026-06 |
| `sp_quota_values` | 212.600 | 2008-04 → 2026-11 |
| `observations` (97 series BCCh) | 282.775 | 1975 → 2026-11 |
| `cmf_insurer_portfolio_control` | 75.045 | 2020-01 → 2026-09 |
| `cmf_mutual_fund_fees` | 65.897 | 2024-07 → 2026-09 |
| `cmf_mutual_fund_returns` | 3.963 | cortes 2026-03 y 2026-06 |
| `dim_producto` / `fact_producto_metricas` | 2.868 vigentes | métricas al 31-10-2026 |

---

## Instalación

Requiere Python 3.11. En Windows, `python` suele apuntar al stub de la Microsoft Store:
**usar siempre el intérprete del venv**.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # o requirements.lock para versiones exactas
copy .env.example .env    # completar BCENTRAL_USER y BCENTRAL_PASS
.\.venv\Scripts\python.exe main.py status
```

Credenciales: solo el Banco Central las exige (registro gratuito en
<https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/index.htm>). CMF y SP se leen
de sus portales públicos.

---

## Actualizar los datos

Un solo mecanismo, el **catch-up por frescura**: sondea cada fuente, baja lo que falta (o lo
que quedó incompleto), reconstruye entidades, productos y métricas, y corre los controles.

```powershell
.\.venv\Scripts\python.exe main.py catchup --dry-run     # qué está pendiente y por qué
.\.venv\Scripts\python.exe main.py catchup               # actualizar todo
.\.venv\Scripts\python.exe main.py catchup --source cmf_fm_cuotas
.\.venv\Scripts\python.exe main.py validate              # controles de calidad (sale con 1 si hay errores)
.\.venv\Scripts\python.exe main.py build-productos       # solo reconstruir capas derivadas
```

Para usuarios sin consola: **`Actualizar_Datos.exe`** (menú por fuente; se compila con
`build_actualizador.py`) y `actualizar.py --todo` en modo desatendido.

Con el servidor web corriendo con `--with-scheduler`, el catch-up corre solo cada hora hábil
y una vez al mes se reingestan los dos últimos períodos de la CMF (que corrige datos ya
publicados).

> DuckDB admite **un solo proceso escritor**: no actualizar mientras la web sirve en modo
> lectura desde otro proceso.

Comandos de carga histórica por fuente: `fetch`, `fetch-cmf`, `fetch-banks`, `fetch-seguros`,
`fetch-intermediarios`, `fetch-cartera-seguros`, `fetch-ffmm-cuotas`, `fetch-ffmm`,
`fetch-ffmm-comisiones`, `fetch-ffmm-comparador`, `fetch-sp-cuotas`, `fetch-sp-cartera`,
`fetch-sp-precios`. Cada uno documenta sus opciones con `--help`.

**Eficiencia de disco** (automático):

- Después de cada carga exitosa (catch-up, comandos `fetch-*`/`import-*`, actualizador) se
  **borran las descargas crudas** de `data/*_raw`: ya están en la base y se pueden volver a
  bajar. Si una carga falla, se conservan para reintentar.
- Las capas derivadas y los controles solo se recalculan si llegaron filas nuevas.
- `dq_results` guarda las últimas 30 corridas y `fetch_log` un año.

A mano: `clean-cache [--dry-run]`, `compactar` (recupera el espacio que dejan las reingestas;
cerrar la web antes), `respaldar --keep N` (copia la base a `data/backups/`, conserva N) y
`export-parquet` (renta fija de seguros a Parquet).

---

## Web

```powershell
.\.venv\Scripts\python.exe main.py serve            # http://127.0.0.1:8000
```

o doble clic en `iniciar_web.bat`.

| Ruta | Contenido |
|---|---|
| `/` | Panel multi-fuente y estado de la calidad de datos |
| `/curvas` | Curvas de tasas, inflación implícita, TPM implícita frente a encuestas, spread bancario |
| `/productos` | Explorador de fondos mutuos y AFP por categoría y clase de inversionista |
| `/producto/{id}` | Ficha: rentabilidad, riesgo, costo, flujos, cartera y comparación con su categoría |
| `/productos/comparar` | Hasta 4 productos lado a lado, con traslape de cartera |
| `/metodologia` | Fuentes, unidades, fórmulas, limitaciones y último control de calidad |
| `/banco-central` | Explorador de series macro |
| `/eeff` | EEFF por clasificación: corporativos, AGF, seguros, intermediarios, bancos |
| `/comparar`, `/ranking`, `/salud`, `/proyecciones` | Análisis de EEFF |
| `/inversion-institucional` | Cartera de seguros, AFP, fondos de pensiones y emisores en común |
| `/fondos-mutuos` | Rendimientos y carteras de fondos mutuos |
| `/docs` | API REST (`/api/v1/...`) |

Cada vista indica la fecha de sus datos y avisa si la fuente está atrasada.

**Aviso**: la información es de carácter informativo y no constituye asesoría ni
recomendación de inversión. Ver `docs/PLAN_MEJORAS.md` §8 antes de publicar el sitio.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Los tests que necesitan la base se saltan solos si no existe o si otro proceso la tiene
abierta en escritura. CI: `.github/workflows/tests.yml`.

---

## Estructura

```
main.py               CLI (Click)            actualizar.py   menú / modo desatendido
collectors/           descarga y parseo por fuente
scheduler/sources.py  registro único de fuentes (sonda + descarga + reingesta)
scheduler/jobs.py     catch-up y scheduler
db/database.py        fachada Database; métodos por dominio en db/repos/
db/migrations.py      tablas derivadas y vistas v_* (numeradas)
db/entities.py        maestra de entidades       db/products.py  productos y métricas
db/curves.py          curvas de tasas (NSS)
db/quality.py         controles de calidad       db/cross.py     cruce de carteras
api/                  FastAPI: routers/web/* (HTML por dominio), productos, api_v1
models/               proyecciones de EEFF (híbrido + SARIMAX) y backtest
config/               catálogo BCCh, entidades curadas, canales de proveedores, perfiles
docs/                 plan, wiki, informe de la Circular 1.835
tests/                pytest
```
