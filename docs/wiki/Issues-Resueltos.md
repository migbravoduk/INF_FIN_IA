# Issues Resueltos

Registro histórico de problemas técnicos significativos encontrados durante el desarrollo y cómo se resolvieron.

---

## SP-001 — Parámetro `fecconf` requerido para descarga de cuotas

**Tipo:** Bug de integración  
**Fase:** SP — Fondos de Pensiones  
**Fecha:** Junio 2025

### Síntoma
Al intentar descargar el CSV de valores cuota desde `vcfAFPxls.php`, el servidor devolvía una respuesta HTTP 200 pero con cuerpo vacío o con una página HTML de error, sin CSV real.

### Causa raíz
El endpoint requiere el parámetro `fecconf` (fecha del último cierre confirmado en formato `YYYYMMDD`). Este valor **no es estático** — cambia con cada publicación de la SP. Intentar enviarlo hardcodeado o no enviarlo resultaba en respuesta vacía.

### Solución implementada
Se creó el método `fetch_fecconf()` que realiza un GET previo a `vcfAFP.php`, extrae el valor de la fecha "Confirmados hasta" del HTML usando BeautifulSoup, lo convierte de formato `"DD-MMM-YYYY"` (ej: `"31-MAY-2026"`) a `"YYYYMMDD"` y lo pasa como parámetro en la petición del CSV.

```python
fecconf = collector.fetch_fecconf(fund_type="A")  # → "20260531"
```

### Archivos afectados
- `collectors/sp_pensions.py` → `fetch_fecconf()`, `fetch_quota_values()`

---

## SP-002 — Cabecera `Referer` obligatoria

**Tipo:** Bug de integración  
**Fase:** SP — Fondos de Pensiones  
**Fecha:** Junio 2025

### Síntoma
Las descargas de CSV de cuotas y ZIP de carteras devolvían HTTP 404 o páginas de error personalizadas cuando se hacía la petición directamente sin pasar por el flujo normal del navegador.

### Causa raíz
El servidor de la SP valida la cabecera HTTP `Referer`. Si no se incluye la URL del portal de origen como Referer, el servidor rechaza la petición.

### Solución implementada
Se incluye explícitamente la cabecera `Referer` en todas las peticiones de descarga de archivos:

```python
req_headers = {
    **self.headers,
    "Referer": f"{self.BASE_URL}/apps/valoresCuotaFondo/vcfAFP.php?tf={fund_type}"
}
```

### Archivos afectados
- `collectors/sp_pensions.py` → `fetch_quota_values()`, `fetch_portfolio()`, `fetch_daily_prices()`

---

## SP-003 — Glosas XML duplicadas en cartera mensual

**Tipo:** Bug de diseño de esquema  
**Fase:** SP — Fondos de Pensiones  
**Fecha:** Junio 2025

### Síntoma
Al intentar insertar los registros parseados de la cartera de inversión mensual (XML) en DuckDB, se producían violaciones de unicidad en la clave primaria compuesta `(period, afp_name, fund_type, instrument_glosa)`.

### Causa raíz
El XML de carteras usa la misma `<glosa>` para instrumentos de distintas categorías. Por ejemplo, `"ACC"` (acciones) puede aparecer tanto en el bloque de renta variable nacional como en el de renta variable extranjera dentro del mismo fondo, generando registros que comparten las cuatro columnas de la clave compuesta pero representan datos distintos.

### Solución implementada
Se rediseñó la tabla `sp_portfolio_holdings`:
- Se eliminó la clave primaria compuesta basada en glosa.
- Se añadió una columna `id BIGINT` con valor por defecto `nextval('sp_portfolio_seq')` como clave primaria subrogada.
- Para mantener idempotencia, se adoptó **Delete-then-Insert** por período en lugar de `ON CONFLICT`.

```sql
-- DELETE previo al INSERT garantiza idempotencia sin PRIMARY KEY sobre glosa
DELETE FROM sp_portfolio_holdings WHERE period = ?;
INSERT INTO sp_portfolio_holdings (...) VALUES (...);
```

### Archivos afectados
- `db/schema.py` → definición de `sp_portfolio_holdings`
- `db/database.py` → `insert_sp_portfolio_holdings()`

---

## SP-004 — Bloqueo DuckDB en backfill multi-fondo

**Tipo:** Bug de rendimiento / concurrencia  
**Fase:** SP — Fondos de Pensiones  
**Fecha:** Junio 2025

### Síntoma
Al ejecutar `fetch-sp-cuotas --year-start 2002` para todos los fondos (A–E) en un solo proceso, el proceso quedaba colgado indefinidamente al intentar ingresar el Fondo B. El proceso Python mantenía el 100% de CPU y no liberaba la conexión DuckDB. Externamente, DuckDB reportaba `IO Error: Cannot open file... El proceso no tiene acceso al archivo porque está siendo utilizado por otro proceso.`

### Causa raíz
DuckDB no soporta múltiples conexiones de escritura simultáneas. El problema fue que el proceso con la conexión DuckDB del backfill de Fondo A (que ya había terminado su `COMMIT`) mantenía la conexión abierta para el siguiente fondo, y la operación `executemany` con `ON CONFLICT DO UPDATE` sobre ~41.000 filas del Fondo B generó una presión de memoria / lock interno que bloqueó el proceso.

### Solución implementada
Se ejecutan los fondos en **procesos Python separados**, uno a la vez, usando `--fund` para especificar el fondo:

```powershell
python main.py fetch-sp-cuotas --year-start 2002 --fund C
python main.py fetch-sp-cuotas --year-start 2002 --fund D
python main.py fetch-sp-cuotas --year-start 2002 --fund E
```

Los CSV se cachean en `data/sp_raw/`, por lo que los fondos ya descargados no vuelven a consultar el servidor. El único costo es el parseo + inserción en DuckDB, que es rápido y sin conflictos al ejecutarse en procesos aislados.

### Archivos afectados
- `main.py` → `fetch-sp-cuotas` (el flag `--fund` ya existía, ahora se documenta su uso para backfill)

---

## BCCh-001 — Activación de credenciales BDE API (paso manual obligatorio)

**Tipo:** Problema de configuración  
**Fase:** 1 — Macro BCCh  
**Fecha:** 2024

### Síntoma
Las credenciales de la API BDE del Banco Central se registraban correctamente, pero al hacer peticiones a la API devolvía `"Invalid username or password"`.

### Causa raíz
El registro en `si3.bcentral.cl` crea la cuenta pero **no activa** las credenciales para la API REST. Se requiere un segundo paso manual: iniciar sesión en el portal y hacer clic en "Activar credenciales" en la sección API.

### Solución
Documentado en README.md. Siempre hacer clic en "Activar credenciales" en `si3.bcentral.cl/Siete/es/Siete/API` después del registro.
