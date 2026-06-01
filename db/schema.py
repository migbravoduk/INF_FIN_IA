"""
db/schema.py — Inicialización del esquema DuckDB

Tablas:
  - sources        : fuentes de datos registradas
  - series         : metadatos de cada serie (nombre, frecuencia, unidad, etc.)
  - observations   : datos en serie de tiempo (source_id, series_id, fecha, valor)
  - fetch_log      : registro de ejecuciones (éxito/error por serie)
"""

SCHEMA_SQL = """
-- ============================================================
-- Fuentes de datos
-- ============================================================
CREATE TABLE IF NOT EXISTS sources (
    id          VARCHAR PRIMARY KEY,   -- 'bcentral', 'cmf', 'ine', etc.
    name        VARCHAR NOT NULL,
    base_url    VARCHAR,
    notes       VARCHAR,
    created_at  TIMESTAMP DEFAULT now()
);

-- ============================================================
-- Catálogo de series
-- ============================================================
CREATE TABLE IF NOT EXISTS series (
    id              VARCHAR PRIMARY KEY,       -- código externo (ej. 'F073.IPC.IND...')
    source_id       VARCHAR NOT NULL REFERENCES sources(id),
    name            VARCHAR NOT NULL,
    category        VARCHAR,                   -- 'precios', 'actividad', etc.
    frequency       VARCHAR,                   -- 'daily', 'monthly', 'quarterly', 'annual'
    unit            VARCHAR,
    description     VARCHAR,
    first_available DATE,
    last_updated    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT now()
);

-- ============================================================
-- Observaciones (serie de tiempo)
-- ============================================================
CREATE TABLE IF NOT EXISTS observations (
    id          BIGINT PRIMARY KEY,
    series_id   VARCHAR NOT NULL REFERENCES series(id),
    date        DATE NOT NULL,
    value       DOUBLE,
    is_revised  BOOLEAN DEFAULT false,   -- si fue revisado por la fuente
    fetched_at  TIMESTAMP DEFAULT now(),
    UNIQUE(series_id, date)
);

-- ============================================================
-- Log de ejecuciones de fetch
-- ============================================================
CREATE TABLE IF NOT EXISTS fetch_log (
    id          BIGINT PRIMARY KEY,
    series_id   VARCHAR,
    source_id   VARCHAR,
    started_at  TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status      VARCHAR,    -- 'ok', 'error', 'no_data'
    records_new INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    error_msg   VARCHAR
);

-- ============================================================
-- Secuencia para IDs automáticos
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS obs_seq START 1;
CREATE SEQUENCE IF NOT EXISTS log_seq START 1;

-- ============================================================
-- Estados Financieros de la CMF (Fase 3)
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS cmf_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_financial_statements (
    id                BIGINT PRIMARY KEY DEFAULT nextval('cmf_seq'),
    period            INTEGER NOT NULL,          -- Formato YYYYMM (ej. 202512)
    rut               VARCHAR NOT NULL,          -- RUT limpio sin puntos ni guión
    company_name      VARCHAR NOT NULL,          -- Razón social
    report_type       VARCHAR NOT NULL,          -- 'I' (Individual) o 'C' (Consolidado)
    currency          VARCHAR NOT NULL,          -- 'CLP', 'USD'
    account_name      VARCHAR NOT NULL,          -- Glosa / Concepto de la cuenta
    value             DOUBLE NOT NULL,           -- Monto
    taxonomy_code     VARCHAR,                   -- Código taxonomía (ej. 'TAX CI')
    statement_group   VARCHAR,                   -- Grupo (ej. 'ESF C/NC', 'ERFG')
    fetched_at        TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cmf_rut_period ON cmf_financial_statements(rut, period);
CREATE INDEX IF NOT EXISTS idx_cmf_company ON cmf_financial_statements(company_name);
"""

SEED_SOURCES_SQL = """
INSERT OR IGNORE INTO sources (id, name, base_url, notes) VALUES
    ('bcentral', 'Banco Central de Chile', 
     'https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx',
     'BDE API REST — requiere usuario y contraseña'),
    ('cmf', 'Comision para el Mercado Financiero',
     'https://www.cmfchile.cl',
     'Portal web — scraping XBRL/HTML, sin API publica'),
    ('ine', 'Instituto Nacional de Estadisticas',
     'https://www.ine.gob.cl',
     'Datos abiertos — sin autenticacion'),
    ('sii', 'Servicio de Impuestos Internos',
     'https://www.sii.cl',
     'Scraping UF/UTM — sin autenticacion'),
    ('bolsa_stgo', 'Bolsa de Santiago',
     'https://www.bolsadesantiago.com',
     'Precios de acciones y datos de mercado');
"""
