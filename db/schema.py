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

-- ============================================================
-- Estados Financieros Mensuales de Bancos (Fase 4)
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS bank_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_bank_statements (
    id                  BIGINT PRIMARY KEY DEFAULT nextval('bank_seq'),
    year                INTEGER NOT NULL,
    month               INTEGER NOT NULL,
    period              INTEGER NOT NULL,              -- Formato YYYYMM (ej. 202512)
    bank_code           VARCHAR NOT NULL,              -- Ficha / Código SBIF (ej. '001')
    bank_name           VARCHAR NOT NULL,              -- Nombre del Banco
    report_type         VARCHAR NOT NULL,              -- 'balance' o 'resultado'
    account_code        VARCHAR NOT NULL,              -- Código de cuenta (ej. '100000000')
    account_name        VARCHAR NOT NULL,              -- Nombre/Glosa de la cuenta
    val_clp_no_reaj     DOUBLE,                        -- Moneda Chilena No Reajustable
    val_clp_reaj_ipc    DOUBLE,                        -- Moneda Reajustable por IPC
    val_clp_reaj_tc     DOUBLE,                        -- Moneda Reajustable por Tipo de Cambio (Dólar)
    val_extranjera      DOUBLE,                        -- Moneda Extranjera
    val_total           DOUBLE NOT NULL,               -- Moneda Total Consolidada
    fetched_at          TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bank_code_period ON cmf_bank_statements(bank_code, period);
CREATE INDEX IF NOT EXISTS idx_bank_account_code ON cmf_bank_statements(account_code);

-- ============================================================
-- Estados Financieros de Compañías de Seguros (FECU CMF) — vida y generales
-- Fuente: cmfchile.cl (descarga Excel de la consulta "TODOS"). Plan de cuentas de
-- seguros (Circulares N°2022 y N°2050), distinto al IFRS corporativo y al bancario.
-- Frecuencia trimestral (03,06,09,12); December tiene además versión anual auditada.
-- UNIDADES: cifras en MILES de pesos (según la nota al pie del propio reporte).
-- `insurance_type` distingue el ramo: 'vida' | 'generales'.
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS insurer_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_insurer_statements (
    id              BIGINT PRIMARY KEY DEFAULT nextval('insurer_seq'),
    insurance_type  VARCHAR NOT NULL DEFAULT 'vida', -- 'vida' | 'generales'
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    period          INTEGER NOT NULL,             -- Formato YYYYMM (ej. 202603)
    report_freq     VARCHAR NOT NULL,             -- 'trimestral' | 'anual'
    rut             VARCHAR NOT NULL,             -- RUT sin dígito verificador (ej. '96656410')
    company_name    VARCHAR NOT NULL,             -- Razón social (glosa corta del reporte)
    statement_group VARCHAR NOT NULL,             -- 'ESF' | 'ERI' | 'EFE'
    account_code    VARCHAR NOT NULL,             -- Código FECU (ej. '5.10.00.00')
    account_name    VARCHAR NOT NULL,             -- Glosa de la cuenta
    value           DOUBLE,                       -- Saldo en MILES de CLP (NULL si la cuenta viene vacía)
    fetched_at      TIMESTAMP DEFAULT now()
);

-- Migración idempotente: DBs creadas antes de sumar generales no tienen la columna;
-- ADD COLUMN IF NOT EXISTS la agrega y rellena las filas existentes (vida) con el default.
ALTER TABLE cmf_insurer_statements ADD COLUMN IF NOT EXISTS insurance_type VARCHAR DEFAULT 'vida';

CREATE INDEX IF NOT EXISTS idx_insurer_rut_period ON cmf_insurer_statements(rut, period);
CREATE INDEX IF NOT EXISTS idx_insurer_account_code ON cmf_insurer_statements(account_code);
CREATE INDEX IF NOT EXISTS idx_insurer_type_period ON cmf_insurer_statements(insurance_type, period);

-- ============================================================
-- Cartera de inversiones de Compañías de Seguros (Circular 1.835 — archivos SGSCI)
-- Fuente: ZIP mensual de la CMF, descargado A MANO (la descarga tiene CAPTCHA) y dejado
-- en data/seguros_cartera_raw/. Cada ZIP trae 12 tipos de archivo × compañía, de ancho
-- fijo y UTF-8. Esta tabla corresponde al archivo B.8 "Información de Control": los
-- TOTALES por tipo de inversión de cada compañía (composición de cartera).
-- Layout validado contra los datos: el registro de detalle suma exactamente 138 chars.
-- UNIDADES: miles de pesos (M$), igual que el resto de la información de seguros.
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS insurer_portfolio_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_insurer_portfolio_control (
    id                BIGINT PRIMARY KEY DEFAULT nextval('insurer_portfolio_seq'),
    insurance_type    VARCHAR NOT NULL,        -- 'vida' | 'generales'
    period            INTEGER NOT NULL,        -- YYYYMM
    rut               VARCHAR NOT NULL,        -- RUT sin dígito verificador
    company_name      VARCHAR NOT NULL,
    investment_code   VARCHAR NOT NULL,        -- Tipo de inversión (codificación SEIL, ej. 'A00')
    valor_final       DOUBLE,                  -- Valor de los instrumentos al cierre (M$)
    repr_rt_pr        DOUBLE,                  -- Inversiones representativas de (RT + PR)
    no_repr_rt_pr     DOUBLE,                  -- Inversiones NO representativas
    costo_amortizado  DOUBLE,                  -- Clasificados 'CA'
    valor_razonable   DOUBLE,                  -- Clasificados 'VR'
    efectivo_equiv    DOUBLE,                  -- Clasificados 'EE'
    cui_apv           DOUBLE,                  -- Instrumentos CUI / APV
    otras_clasif      DOUBLE,                  -- Clasificados 'OTRCLA'
    soc_filiales      DOUBLE,                  -- Participaciones en sociedades filiales
    coligadas         DOUBLE,                  -- Participaciones en sociedades coligadas
    fetched_at        TIMESTAMP DEFAULT now()
);

-- NOTA: sin índices secundarios (period / rut) a propósito. En DuckDB 1.5.1 estos índices ART
-- rompen el patrón Delete-then-Insert de la reingesta por período ("Failed to delete all rows
-- from index. Only deleted N out of M rows", que además invalida la base): las claves están
-- muy duplicadas (574 filas comparten el mismo period). El filtrado por period/rut se resuelve
-- por scan con zonemaps, más que suficiente para el tamaño de estas tablas.

-- Detalle B.1 = Instrumentos de Renta Fija. Solo los campos RECONCILIADOS campo a campo
-- contra los datos (validación al 100% sobre 205K registros de ambos meses): identificación
-- del instrumento + valor nominal. Los montos de valoración de mercado (costo amortizado /
-- valor razonable, al final del registro) NO se cargan aún: el spec publicado no cuadra ahí
-- (declara 878 chars vs 970 reales) y no hay total interno que los valide; requieren el
-- catálogo de descriptores (B.9). Ver informe de inconsistencias y CONTEXTO.md.
CREATE SEQUENCE IF NOT EXISTS insurer_fixinc_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_insurer_portfolio_fixed_income (
    id                 BIGINT PRIMARY KEY DEFAULT nextval('insurer_fixinc_seq'),
    insurance_type     VARCHAR NOT NULL,     -- 'vida' | 'generales'
    period             INTEGER NOT NULL,     -- YYYYMM
    rut                VARCHAR NOT NULL,     -- RUT de la compañía informante (sin DV)
    codigo_operacion   VARCHAR,              -- CDT, CRV, … (compra definitiva / retroventa)
    folio_operacion    VARCHAR,
    item_operacion     VARCHAR,
    fecha_compra       VARCHAR,              -- AAAAMMDD
    fecha_pago         VARCHAR,              -- AAAAMMDD
    rut_emisor         VARCHAR,              -- RUT del emisor del instrumento (sin DV)
    tipo_instrumento   VARCHAR,              -- codificación SEIL (ej. 'BB', 'BE')
    nemotecnico        VARCHAR,              -- nemotécnico / ISIN
    fecha_emision      VARCHAR,              -- AAAAMMDD
    num_inscripcion    VARCHAR,
    fecha_inscripcion  VARCHAR,
    serie              VARCHAR,
    pais               VARCHAR,              -- código de país (ej. 'CL')
    valor_nominal      DOUBLE,               -- 4 decimales implícitos, en la unidad monetaria
    valor_nominal_vig  DOUBLE,               -- valor nominal vigente
    unidad_monetaria   VARCHAR,              -- UF, $$, EUR, PROM, …
    fetched_at         TIMESTAMP DEFAULT now()
);

-- Sin índices secundarios, por el mismo bug de DuckDB 1.5.1 que en la tabla de control (ver
-- arriba). Aquí la duplicación de period es aún mayor (~100K filas por período).

-- ============================================================
-- Estados Financieros de Intermediarios de Valores (FECU IFRS CMF)
-- Corredores de bolsa y agentes de valores (`broker_type`), una sola descarga por período.
-- Plan de cuentas propio de intermediarios (códigos tipo '11.01.00'), con 14 secciones que
-- se preservan en `section` además del grupo normalizado `statement_group` (ESF/ERI/EFE).
-- Solo trimestral y solo estándar IFRS (desde dic-2010).
-- UNIDADES: cifras en MILES de pesos.
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS broker_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_broker_statements (
    id              BIGINT PRIMARY KEY DEFAULT nextval('broker_seq'),
    broker_type     VARCHAR NOT NULL,             -- 'CORREDORES' | 'AGENTES'
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    period          INTEGER NOT NULL,             -- Formato YYYYMM (ej. 202603)
    rut             VARCHAR NOT NULL,             -- RUT sin dígito verificador
    company_name    VARCHAR NOT NULL,
    statement_group VARCHAR NOT NULL,             -- 'ESF' | 'ERI' | 'EFE'
    section         VARCHAR,                      -- Rótulo original (ej. 'Resultado por intermediación')
    account_code    VARCHAR NOT NULL,             -- Código FECU (ej. '11.01.00')
    account_name    VARCHAR NOT NULL,
    value           DOUBLE,                       -- Saldo en MILES de CLP (NULL si viene vacía)
    fetched_at      TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broker_rut_period ON cmf_broker_statements(rut, period);
CREATE INDEX IF NOT EXISTS idx_broker_type_period ON cmf_broker_statements(broker_type, period);
CREATE INDEX IF NOT EXISTS idx_broker_account_code ON cmf_broker_statements(account_code);

-- ============================================================
-- ** Superintendencia de Pensiones (SP)
-- ============================================================
CREATE TABLE IF NOT EXISTS sp_quota_values (
    date            DATE NOT NULL,
    afp_name        VARCHAR NOT NULL,
    fund_type       VARCHAR NOT NULL,
    quota_value     DOUBLE NOT NULL,
    equity_value    DOUBLE NOT NULL,
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, afp_name, fund_type)
);

CREATE INDEX IF NOT EXISTS idx_sp_quota_date ON sp_quota_values(date);
CREATE INDEX IF NOT EXISTS idx_sp_quota_afp_fund ON sp_quota_values(afp_name, fund_type);

CREATE SEQUENCE IF NOT EXISTS sp_portfolio_seq START 1;

CREATE TABLE IF NOT EXISTS sp_portfolio_holdings (
    id              BIGINT PRIMARY KEY DEFAULT nextval('sp_portfolio_seq'),
    period          VARCHAR NOT NULL,  -- Formato YYYY-MM
    afp_name        VARCHAR NOT NULL,
    fund_type       VARCHAR NOT NULL,
    instrument_glosa VARCHAR NOT NULL,
    row_order       INTEGER,           -- orden original de la fila en el XML (atributo `numero`)
    section         VARCHAR,           -- totalizador de sección que agrupa la fila (ej. 'TOTAL EXTRANJERO')
    monto_pesos     DOUBLE,
    monto_dolares   DOUBLE,
    porcentaje      DOUBLE,
    fetched_at      TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sp_portfolio_period ON sp_portfolio_holdings(period);

CREATE TABLE IF NOT EXISTS sp_instrument_prices (
    date            DATE NOT NULL,
    instrument_id   VARCHAR NOT NULL, -- Nemotécnico o RUT
    instrument_type VARCHAR,
    currency        VARCHAR,
    price           DOUBLE NOT NULL,
    fetched_at      TIMESTAMP DEFAULT now(),
    PRIMARY KEY (date, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_sp_prices_date ON sp_instrument_prices(date);
CREATE INDEX IF NOT EXISTS idx_sp_prices_instrument ON sp_instrument_prices(instrument_id);

-- ============================================================
-- Fondos Mutuos (CMF API v3)
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS mf_quota_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_mutual_fund_quota_values (
    id              BIGINT PRIMARY KEY DEFAULT nextval('mf_quota_seq'),
    date            DATE NOT NULL,
    agf_rut         VARCHAR,                      -- el archivo diario NO trae RUT de la AGF
    agf_name        VARCHAR,                      -- Administradora (viene en el archivo)
    fund_run        VARCHAR NOT NULL,             -- RUN SIN dígito verificador (ver gotchas)
    fund_name       VARCHAR NOT NULL,
    fund_type       INTEGER,                      -- 1..8, glosa oficial al pie del archivo
    series          VARCHAR,                      -- Serie de las cuotas (A, B, CLASICA...)
    currency        VARCHAR,                      -- moneda en que contabiliza el fondo
    quota_value     DOUBLE,                       -- Valor cuota
    net_asset_value DOUBLE,                       -- Patrimonio, en MILLONES (lo dice el pie)
    participants    INTEGER,                      -- Número de partícipes
    fetched_at      TIMESTAMP DEFAULT now()
);
-- SIN índices ni UNIQUE, aunque (date, fund_run, series) sí es llave natural (verificado:
-- 0 duplicados en 2.864 filas). El motivo es el mismo gotcha de la cartera de seguros: un
-- índice ART cuya columna líder está masivamente duplicada —acá 2.864 filas comparten `date`—
-- hace FALLAR el DELETE de la reingesta por fecha e invalida la base. Idempotencia por
-- Delete-then-Insert por `date`.

-- Glosa oficial del tipo de fondo. NO se transcribe a mano: viene al pie del propio
-- archivo de la CMF ("(2) Tipo de fondo: 1: FM DE INV.EN INST.DE DEUDA...") y se guarda
-- en cada ingesta, igual que el resto de las glosas del regulador.
CREATE TABLE IF NOT EXISTS cmf_mutual_fund_types (
    code    INTEGER PRIMARY KEY,
    label   VARCHAR NOT NULL
);

-- Rentabilidades OFICIALES de la CMF (base del Comparador de rentabilidad).
-- Es una foto por corte (`fecha`), no una serie histórica: cada ingesta reemplaza el corte
-- completo. Los porcentajes vienen ya calculados por el regulador según su Documento
-- Metodológico, así que NO se recalculan acá: son la referencia contra la cual se valida
-- nuestro propio cálculo (ver CONTEXTO.md).
CREATE TABLE IF NOT EXISTS cmf_mutual_fund_returns (
    fecha                VARCHAR NOT NULL,     -- AAAAMMDD del corte
    fund_run             VARCHAR NOT NULL,     -- SIN dígito verificador, como el resto
    series               VARCHAR NOT NULL,
    fund_name            VARCHAR,
    agf_name             VARCHAR,              -- nombre CORTO ('BANCHILE'), no la razón social
    familia              VARCHAR,              -- familia del visualizador (11 categorías)
    familia_rar          VARCHAR,              -- familia fina, usada para el indicador de riesgo
    clase_inversionista  VARCHAR,              -- Retail | Alto Patrimonio | APV
    serie_digital        INTEGER,
    rent_nominal_1a      DOUBLE,
    rent_real_1a         DOUBLE,
    rent_nominal_3a_ann  DOUBLE,               -- anualizada
    rent_real_3a_ann     DOUBLE,
    rent_nominal_5a_ann  DOUBLE,
    rent_real_5a_ann     DOUBLE,
    rent_nominal_10a_ann DOUBLE,
    rent_real_10a_ann    DOUBLE,
    rent_nominal_ytd     DOUBLE,
    rent_real_ytd        DOUBLE,
    ind_rar              DOUBLE,               -- rentabilidad ajustada por riesgo (ponderada)
    fetched_at           TIMESTAMP DEFAULT now()
);

-- Administradoras con su RUT. El RUT solo aparece en la hoja de benchmark por AGF del
-- comparador; la estadística diaria trae la razón social larga y ningún identificador.
CREATE TABLE IF NOT EXISTS cmf_mutual_fund_agf (
    agf_rut   VARCHAR PRIMARY KEY,
    agf_name  VARCHAR NOT NULL
);

-- Estructura de costos por fondo y serie (CMF, mensual).
--
-- OJO CON EL DOBLE DESCUENTO: la remuneración de la AGF y el gasto operacional se devengan
-- DIARIAMENTE sobre el patrimonio del fondo (nota (2) del archivo), así que ya están dentro
-- del valor cuota: la rentabilidad calculada desde el valor cuota YA es neta de ambos.
-- La comisión de colocación, en cambio, se le cobra al partícipe y NO sale del patrimonio:
-- esa sí hay que restarla aparte. Todos los valores son FRACCIÓN anual (0,0119 = 1,19%).
CREATE TABLE IF NOT EXISTS cmf_mutual_fund_fees (
    periodo              VARCHAR NOT NULL,     -- AAAAMM
    fund_run             VARCHAR NOT NULL,     -- SIN dígito verificador
    series               VARCHAR NOT NULL,
    fund_name            VARCHAR,
    agf_name             VARCHAR,
    currency             VARCHAR,
    com_colocacion_max   DOUBLE,               -- al partícipe; min/max varían por permanencia
    com_colocacion_min   DOUBLE,
    com_colocacion_unico DOUBLE,
    remuneracion_max     DOUBLE,               -- ya descontada del valor cuota
    remuneracion_min     DOUBLE,
    remuneracion_unico   DOUBLE,
    gasto_operacional    DOUBLE,               -- ya descontado del valor cuota
    fetched_at           TIMESTAMP DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS mf_portfolio_seq START 1;

CREATE TABLE IF NOT EXISTS cmf_mutual_fund_portfolio (
    id              BIGINT PRIMARY KEY DEFAULT nextval('mf_portfolio_seq'),
    period_year     INTEGER NOT NULL,
    period_month    INTEGER NOT NULL,
    portfolio_type  VARCHAR NOT NULL,             -- NACI, EXTR, etc.
    fund_run        VARCHAR NOT NULL,
    fund_name       VARCHAR NOT NULL,
    raw_data        JSON,                         -- Todo el registro restante de las columnas FFM_*
    fetched_at      TIMESTAMP DEFAULT now()
);
-- SIN llave natural a propósito: un fondo puede tener DOS posiciones idénticas del mismo
-- instrumento (dos compras con las mismas características) y el archivo de la CMF no trae
-- columna que las distinga. Medido sobre 2026-06: 1.506 grupos con 3.006 filas repetidas,
-- que son posiciones reales, no basura. La idempotencia se logra con Delete-then-Insert por
-- (period_year, period_month, portfolio_type), como en el resto de los cargadores del repo.
--
-- SIN índices secundarios, por la misma razón que la cartera de seguros (ver CONTEXTO.md):
-- en DuckDB un índice ART sobre una columna con claves muy duplicadas —acá TODAS las filas
-- comparten período— hace FALLAR el DELETE de la reingesta e invalida la base para todo el
-- proceso. DuckDB es columnar con zonemaps: el filtro por período no necesita ese índice.
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
     'Precios de acciones y datos de mercado'),
    ('sp', 'Superintendencia de Pensiones',
     'https://www.spensiones.cl',
     'Portal web — scraping de valores cuota, carteras y precios');
"""
