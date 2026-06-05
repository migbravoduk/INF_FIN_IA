# Integración de Datos de la Superintendencia de Pensiones (SP)

Este documento detalla la arquitectura de integración, decisiones de diseño, esquema de base de datos y desafíos técnicos resueltos al incorporar los datos de la **Superintendencia de Pensiones (SP) de Chile** al Repositorio de Información Financiera.

---

## 1. Arquitectura de Ingestión y Scraping

El portal de la Superintendencia de Pensiones no dispone de una API pública JSON. Por lo tanto, se diseñó e implementó un colector de scraping dinámico (`collectors/sp_pensions.py`) estructurado en tres flujos principales:

```
[Portal Web SP]
       │
       ├─► vcfAFP.php ───────► Parseo 'fecconf' (Fecha Confirmada) ──┐
       │                                                             ▼
       ├─► vcfAFPxls.php ────► Descarga CSV Cuotas ──────────────────┼─► [Base de Datos DuckDB]
       │                                                             │
       ├─► loadCarInv.php ───► Descarga ZIP ──► XML Carteras ────────┤
       │                                                             │
       └─► GetFile.php ──────► Descarga ZIP ──► TXT Cinta Precios ───┘
```

### 1.1. Valores Cuota Diarios (Multifondos)
* **Origen:** `https://www.spensiones.cl/apps/valoresCuotaFondo/vcfAFPxls.php`
* **Mapeo de Parámetros:** Requiere enviar el tipo de fondo (`tf`), rango de años (`aaaaini`, `aaaafin`) y el parámetro de control dinámico `fecconf`.
* **Extracción de `fecconf`:** Antes de descargar el archivo, el sistema realiza una petición GET a `vcfAFP.php` para extraer por scraping (utilizando BeautifulSoup) el valor de la fecha más reciente de valores confirmados (ej: `"31-MAY-2026"` convertida a `"20260531"`).
* **Consistencia HTTP:** El servidor de la SP valida la cabecera `Referer`. Si no se incluye `Referer: https://www.spensiones.cl/apps/valoresCuotaFondo/vcfAFP.php?tf={fondo}`, el servidor retorna un error HTTP 404 o una redirección vacía.

### 1.2. Cartera de Inversión Mensual Desagregada
* **Origen:** `https://www.spensiones.cl/apps/loadCarteras/loadCarInv.php`
* **Mapeo de Parámetros:** Requiere el parámetro `periodo` en formato `YYYYMM`.
* **Flujo de Descarga:** 
  1. Se consulta la página HTML del período para extraer el enlace de descarga cifrado de `GetFile_v2.0.php`.
  2. Se descarga el archivo ZIP correspondiente.
  3. Se extrae el archivo XML interno (`cartera_desagregada{YYYYMM}.xml`).
  4. Se parsea el listado XML utilizando `xml.etree.ElementTree`.
* **Optimización de Espacio:** Dado que los archivos ZIP descargados pueden pesar varios megabytes y el XML extraído pesa hasta 260MB, el archivo ZIP se elimina inmediatamente después de la extracción para no acumular basura en disco y respetar el límite de almacenamiento de 1GB en descargas.

### 1.3. Cinta de Precios Diaria
* **Origen:** `https://www.spensiones.cl/apps/GetFile.php`
* **Fórmula de Consulta:** Se compone el parámetro `namefile` dinámicamente con la estructura `{año}/{mes_abreviado_es}/p{fecha_yyyymmdd}.zip`.
* **Nombres de Meses en Español:** La SP almacena los archivos utilizando abreviaciones en español de tres letras en minúsculas (ej: `ene`, `feb`, `mar`, `abr`, `may`, `jun`, `jul`, `ago`, `sep`, `oct`, `nov`, `dic`). El colector mapea la fecha solicitada a estas abreviaciones para construir la URL correcta.
* **Flujo:** Descarga el ZIP, descomprime el archivo plano `p{yyyymmdd}.txt` delimitado por punto y coma, y elimina el ZIP.

---

## 2. Esquema de Base de Datos (DuckDB)

Los datos se integran en el archivo DuckDB local (`data/finanzas_chile.duckdb`). El esquema de base de datos se extendió en `db/schema.py` de la siguiente forma:

### 2.1. Tabla `sp_quota_values`
Registra el valor de cuota diario y patrimonio neto por AFP y Multifondo.
* **Clave Primaria:** Compuesta por `(date, afp_name, fund_type)` para evitar duplicación.
```sql
CREATE TABLE IF NOT EXISTS sp_quota_values (
    date            DATE NOT NULL,
    afp_name        VARCHAR NOT NULL, -- Ej: 'CAPITAL', 'PROVIDA'
    fund_type       VARCHAR NOT NULL,  -- 'A', 'B', 'C', 'D', 'E'
    quota_value     DOUBLE NOT NULL,
    equity_value    DOUBLE NOT NULL,
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, afp_name, fund_type)
);
```

### 2.2. Tabla `sp_portfolio_holdings`
Almacena la distribución mensual consolidada de la cartera de inversiones previsional.
* **Desafío de Glosas Duplicadas:** El XML de la SP contiene etiquetas `<glosa>` idénticas en múltiples categorías del portafolio (ej. `ACC` puede aparecer en renta variable nacional y en renta variable extranjera). Una clave primaria basada en `(period, afp_name, fund_type, instrument_glosa)` generaba violaciones de unicidad.
* **Resolución:** Se diseñó la tabla utilizando una clave primaria subrogada autoincremental `id` mediante una secuencia DuckDB (`sp_portfolio_seq`). Para evitar duplicaciones en re-ejecuciones, el colector utiliza una estrategia **Delete-then-Insert** por período.
```sql
CREATE SEQUENCE IF NOT EXISTS sp_portfolio_seq START 1;

CREATE TABLE IF NOT EXISTS sp_portfolio_holdings (
    id              BIGINT PRIMARY KEY DEFAULT nextval('sp_portfolio_seq'),
    period          VARCHAR NOT NULL,  -- Formato 'YYYY-MM'
    afp_name        VARCHAR NOT NULL,  -- AFP o 'TOTAL'
    fund_type       VARCHAR NOT NULL,  -- 'A' a 'E'
    instrument_glosa VARCHAR NOT NULL, -- Ej: 'ACC', 'BCU', 'Total Renta Fija'
    monto_pesos     DOUBLE,
    monto_dolares   DOUBLE,
    porcentaje      DOUBLE,
    fetched_at      TIMESTAMP DEFAULT now()
);
```

### 2.3. Tabla `sp_instrument_prices`
Contiene la cinta de precios de valorización local diaria.
* **Clave Primaria:** Compuesta por `(date, instrument_id)`.
```sql
CREATE TABLE IF NOT EXISTS sp_instrument_prices (
    date            DATE NOT NULL,
    instrument_id   VARCHAR NOT NULL, -- Nemotécnico o RUT del emisor
    instrument_type VARCHAR,          -- Categoría de instrumento (ej: 'ACC', 'LH')
    currency        VARCHAR,          -- Moneda (ej: 'NO' = CLP, 'UF', 'US$')
    price           DOUBLE NOT NULL,
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, instrument_id)
);
```

---

## 3. Comprobaciones de Integridad y Robustez

Debido a que el portal web de la SP puede cambiar de estructura o responder con páginas de error personalizadas con códigos HTTP 200, se implementaron controles estrictos de calidad en `sp_pensions.py`:

1. **Validación de Tamaño de Descarga:**
   * Archivos CSV de cuotas de tamaño `< 100` bytes son rechazados (indica error del backend).
   * Archivos ZIP de carteras con peso `< 5000` bytes son rechazados (indica archivo corrupto o página de error HTML disfrazada de ZIP).
2. **Validación de Cabeceras Internas:**
   * Antes de parsear el CSV de cuotas, el colector verifica que el contenido incluya palabras clave obligatorias como `"Valores Confirmados"` o `"Fecha"`.
   * El XML de carteras se verifica buscando la etiqueta raíz `<constitucion_cartera_desagregada>`.
3. **Manejo de Formato Numérico Chileno:**
   * Se implementó un parser de cantidades (`_parse_amount`) que elimina espacios en blanco, remueve puntos de miles y traduce la coma decimal a punto antes de convertir a `float`. Filtra valores no numéricos como `"NaN"`, `"N/A"`, `"nd"` o `"-"`.
4. **Política de Backfill Amigable (Sleep Intervals):**
   * Al ejecutar cargas masivas históricas de precios (que involucran cientos de peticiones HTTP), se introdujo una pausa de `0.15s` (`time.sleep(0.15)`) entre descargas para evitar saturar el servidor de la SP y mitigar riesgos de bloqueos de IP.

---

## 4. Planificación del Scheduler (Trabajo Automático)

Los procesos automáticos de la SP se acoplan al programador existente (`scheduler/jobs.py`):
1. **`sp_daily_fetch` (Frecuencia Diaria):**
   * Se ejecuta de **Lunes a Viernes a las 18:30** (Santiago).
   * Descarga la cinta de precios del día actual (si está disponible) e ingresa los valores cuota del año en curso para reflejar las últimas variaciones confirmadas.
2. **`sp_monthly_fetch` (Frecuencia Mensual):**
   * Se ejecuta el **día 15 de cada mes a las 20:00**.
   * Calcula el período del mes anterior (ej: si es junio, calcula `202605`) y descarga su respectiva cartera de inversión desagregada consolidada.
