# Integración SP — Superintendencia de Pensiones

Esta página documenta la arquitectura técnica completa, los desafíos resueltos y las decisiones de diseño de la integración con el portal web de la **Superintendencia de Pensiones (SP) de Chile**.

> **Código fuente:** [`collectors/sp_pensions.py`](../blob/main/collectors/sp_pensions.py)  
> **Documentación técnica extendida:** [`docs/sp_integration.md`](../blob/main/docs/sp_integration.md)

---

## Datos integrados

| Dataset | Origen | Frecuencia | Cobertura histórica |
|---------|--------|------------|---------------------|
| Valores Cuota + Patrimonio | `vcfAFPxls.php` (CSV) | Diaria | Desde 2002 |
| Cartera de Inversión Desagregada | `loadCarInv.php` → ZIP/XML | Mensual | Último año + manual |
| Cinta de Precios de Instrumentos | `GetFile.php` (ZIP/TXT) | Diaria | 5 años diario + 5 años semanal |

---

## Arquitectura de scraping

El portal de la SP **no tiene API pública**. Se implementó scraping dinámico con validación en múltiples capas:

```
Portal Web SP
  │
  ├─► vcfAFP.php ────────── Extraer fecconf (fecha confirmada) ──┐
  │                                                              ▼
  ├─► vcfAFPxls.php ─────── CSV cuotas/patrimonio ──────────────┼──► DuckDB
  │                                                              │
  ├─► loadCarInv.php ─────── HTML → enlace ZIP → XML Carteras ──┤
  │                                                              │
  └─► GetFile.php ─────────── ZIP → TXT Cinta de Precios ───────┘
```

### Parámetro crítico: `fecconf`

El endpoint `vcfAFPxls.php` requiere el parámetro `fecconf` (fecha del último cierre confirmado en formato `YYYYMMDD`). Este valor **cambia diariamente** y debe obtenerse en tiempo real desde el HTML de `vcfAFP.php`. Sin él, el servidor devuelve HTTP 200 con contenido vacío.

```python
# Ejemplo de uso interno
fecconf = collector.fetch_fecconf(fund_type="A")  # → "20260531"
records = collector.fetch_quota_values(2002, 2026, fund_type="A", fecconf=fecconf)
```

### Header `Referer` obligatorio

El servidor de la SP valida la cabecera HTTP `Referer`. Sin ella, el servidor redirige o devuelve un error disfrazado de HTTP 200:

```python
req_headers = {
    **self.headers,
    "Referer": "https://www.spensiones.cl/apps/valoresCuotaFondo/vcfAFP.php?tf=A"
}
```

### Nombres de meses en español (Cinta de Precios)

Los archivos ZIP de precios se nombran con abreviaciones en español: `ene`, `feb`, `mar`, `abr`, `may`, `jun`, `jul`, `ago`, `sep`, `oct`, `nov`, `dic`. El colector mapea automáticamente la fecha solicitada:

```python
namefile = f"{year}/{month_abbr}/p{yyyymmdd}.zip"
# Ejemplo: "2026/ene/p20260102.zip"
```

---

## Esquema de base de datos

### `sp_quota_values`
```sql
CREATE TABLE IF NOT EXISTS sp_quota_values (
    date         DATE NOT NULL,
    afp_name     VARCHAR NOT NULL,   -- 'CAPITAL', 'CUPRUM', 'HABITAT', etc.
    fund_type    VARCHAR NOT NULL,   -- 'A', 'B', 'C', 'D', 'E'
    quota_value  DOUBLE NOT NULL,    -- Valor cuota en pesos
    equity_value DOUBLE NOT NULL,    -- Patrimonio neto en pesos
    fetched_at   TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, afp_name, fund_type)
);
```

### `sp_portfolio_holdings`
```sql
-- Clave primaria por secuencia para manejar glosas duplicadas en el XML
CREATE SEQUENCE IF NOT EXISTS sp_portfolio_seq START 1;

CREATE TABLE IF NOT EXISTS sp_portfolio_holdings (
    id               BIGINT PRIMARY KEY DEFAULT nextval('sp_portfolio_seq'),
    period           VARCHAR NOT NULL,    -- Formato 'YYYY-MM'
    afp_name         VARCHAR NOT NULL,    -- AFP o 'TOTAL'
    fund_type        VARCHAR NOT NULL,    -- 'A' a 'E'
    instrument_glosa VARCHAR NOT NULL,    -- Ej: 'ACC', 'BCU', 'Total Renta Fija'
    monto_pesos      DOUBLE,
    monto_dolares    DOUBLE,
    porcentaje       DOUBLE,
    fetched_at       TIMESTAMP DEFAULT now()
);
```

### `sp_instrument_prices`
```sql
CREATE TABLE IF NOT EXISTS sp_instrument_prices (
    date            DATE NOT NULL,
    instrument_id   VARCHAR NOT NULL,  -- Nemotécnico o RUT del emisor
    instrument_type VARCHAR,           -- 'ACC', 'LH', 'EPA', etc.
    currency        VARCHAR,           -- 'NO' (CLP), 'UF', 'US$'
    price           DOUBLE NOT NULL,
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, instrument_id)
);
```

---

## Comprobaciones de integridad

| Control | Descripción |
|---------|-------------|
| Tamaño mínimo CSV | < 100 bytes → error: respuesta vacía del backend |
| Tamaño mínimo ZIP | < 5000 bytes → error: página HTML de error disfrazada de ZIP |
| Keywords CSV | Debe contener `"Valores Confirmados"` o `"Fecha"` |
| Root XML | Debe contener `<constitucion_cartera_desagregada` |
| Formato numérico | Parser que maneja puntos de miles + coma decimal (formato chileno) |
| Días inhábiles | HTTP ≠ 200 en cinta de precios → se registra y continúa sin error |

---

## Scheduler automático

| Job | Trigger | Acciones |
|-----|---------|----------|
| `sp_daily_fetch` | Lun–Vie 18:30 (Santiago) | Cinta de precios del día + valores cuota del año en curso |
| `sp_monthly_fetch` | Día 15 de cada mes 20:00 | Cartera desagregada del mes anterior |

---

## Problemas resueltos

### 1. Glosas duplicadas en XML de carteras
**Problema:** El XML de carteras contiene etiquetas `<glosa>` idénticas en distintas secciones del mismo fondo (ej: `ACC` para renta variable nacional y `ACC` para renta variable extranjera), causando violaciones en la clave primaria compuesta original `(period, afp_name, fund_type, instrument_glosa)`.

**Solución:** Se reemplazó la clave primaria compuesta por un `id` autoincremental con secuencia DuckDB (`sp_portfolio_seq`) y se adoptó una estrategia **Delete-then-Insert** por período para mantener idempotencia.

### 2. Bloqueo de DuckDB en inserciones masivas
**Problema:** Al ejecutar el backfill completo (todos los fondos A–E desde 2002 en un único proceso), la conexión DuckDB quedaba bloqueada durante el `executemany` del Fondo B (~41.000 filas con `ON CONFLICT DO UPDATE`), posiblemente por la presión de memoria del proceso previo que mantenía la conexión abierta.

**Solución:** Se ejecutaron los fondos de a uno (`--fund D`, `--fund E`) en procesos separados en lugar de en un único proceso que mantiene la conexión abierta por varios fondos consecutivos. Los CSV quedan cacheados localmente, por lo que los fondos ya descargados no vuelven a descargar.

### 3. Mantenimiento del parámetro `fecconf`
**Problema:** El parámetro `fecconf` no es estático: cambia con cada cierre de mercado. El scraper lo obtiene en vivo desde `vcfAFP.php` **una sola vez** por sesión y lo reutiliza para todos los fondos, minimizando peticiones innecesarias.
