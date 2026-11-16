"""
db/migrations.py — Migraciones idempotentes y numeradas del esquema.

`SCHEMA_SQL` (db/schema.py) crea las tablas de origen tal como llegan de cada fuente. Lo
que se construye ENCIMA (dimensiones, metadatos, vistas normalizadas, tablas de métricas)
va acá, numerado, y queda registrado en `schema_migrations`. Se aplica al abrir la base en
modo escritura (`Database._init_schema`). Cada migración debe poder correr sobre una base
nueva y sobre una existente.

Regla del proyecto (CONTEXTO): NO crear índices secundarios en tablas con reingesta
masiva por período: en DuckDB rompen el DELETE de la reingesta.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 001 · controles de calidad
# ------------------------------------------------------------------
M001 = """
CREATE SEQUENCE IF NOT EXISTS dq_seq START 1;
CREATE TABLE IF NOT EXISTS dq_results (
    id        BIGINT PRIMARY KEY DEFAULT nextval('dq_seq'),
    run_at    TIMESTAMP NOT NULL,
    check_id  VARCHAR NOT NULL,
    tabla     VARCHAR NOT NULL,
    periodo   VARCHAR,
    estado    VARCHAR NOT NULL,      -- ok | warn | error
    n_fallas  BIGINT,
    detalle   VARCHAR
);
"""

# ------------------------------------------------------------------
# 002 · metadatos de cada dataset: unidad, moneda, frecuencia, llave, fuente
# Documenta en la base lo que antes solo estaba en prosa en CONTEXTO.md; alimenta la
# página de metodología y parametriza controles.
# ------------------------------------------------------------------
M002 = """
CREATE TABLE IF NOT EXISTS dim_dataset (
    tabla          VARCHAR PRIMARY KEY,
    titulo         VARCHAR NOT NULL,
    fuente         VARCHAR NOT NULL,
    url_fuente     VARCHAR,
    frecuencia     VARCHAR,          -- diaria | mensual | trimestral | foto por corte
    unidad         VARCHAR,          -- unidad de los montos
    moneda         VARCHAR,
    llave_natural  VARCHAR,
    kind           VARCHAR,          -- fuente en scheduler/sources.SOURCES
    notas          VARCHAR
);
DELETE FROM dim_dataset;
INSERT INTO dim_dataset VALUES
 ('observations', 'Series macroeconómicas', 'Banco Central de Chile', 'https://si3.bcentral.cl/siete',
  'según serie', 'según serie (ver series.unit)', 'según serie', 'series_id, date', 'bcentral',
  'Cobre en US$/libra. Expectativas EEE/EOF son medianas de encuestas.'),
 ('cmf_financial_statements', 'EEFF corporativos IFRS', 'CMF', 'https://www.cmfchile.cl',
  'trimestral', 'unidades de la moneda de reporte (NO miles)', 'currency (CLP | USD; NODEF* = no informada)',
  'period, rut, report_type, statement_group, account_name (con repetidos XBRL)', 'cmf_emp',
  'El archivo plano repite cuentas bajo contextos distintos; api/eeff_format las colapsa.'),
 ('cmf_bank_statements', 'Estados financieros bancarios', 'CMF (API SBIFv3)', 'https://api.sbif.cl',
  'mensual', 'MILLONES de pesos antes de 202201, PESOS desde 202201 (usar v_bank_statements)', 'CLP',
  'period, bank_code, report_type, account_code', 'cmf_bank',
  'Cambio de plan de cuentas en 2022: códigos de 7 a 9 dígitos y nombres distintos.'),
 ('cmf_insurer_statements', 'FECU de seguros (vida y generales)', 'CMF', 'https://www.cmfchile.cl',
  'trimestral', 'MILES de pesos', 'CLP', 'period, insurance_type, report_freq, rut, statement_group, account_code',
  'cmf_insurer', 'Plan de cuentas propio (Circulares 2022/2050).'),
 ('cmf_broker_statements', 'FECU de intermediarios de valores', 'CMF', 'https://www.cmfchile.cl',
  'trimestral', 'MILES de pesos', 'CLP', 'period, rut, statement_group, section, account_code',
  'cmf_broker', 'Corredores de bolsa y agentes de valores.'),
 ('cmf_insurer_portfolio_control', 'Cartera de inversiones de seguros (B.8)', 'CMF · Circular 1.835',
  'https://www.cmfchile.cl', 'mensual', 'MILES de pesos', 'CLP', 'period, insurance_type, rut, investment_code',
  'cmf_cartera', 'Derivados pasivos con signo negativo. Mix de valorización sobre lo clasificado (~86%).'),
 ('cmf_insurer_portfolio_fixed_income', 'Cartera de renta fija de seguros (B.1)', 'CMF · Circular 1.835',
  'https://www.cmfchile.cl', 'mensual', 'valor nominal en la unidad_monetaria del instrumento (NO sumable entre monedas)',
  'unidad_monetaria (UF, $$, EUR, PROM…)', 'sin llave natural', 'cmf_cartera',
  'Valoración de mercado sin reconciliar: no se carga.'),
 ('sp_quota_values', 'Valor cuota y patrimonio de fondos de pensiones', 'Superintendencia de Pensiones',
  'https://www.spensiones.cl', 'diaria', 'pesos', 'CLP', 'date, afp_name, fund_type', 'sp_cuotas', NULL),
 ('sp_portfolio_holdings', 'Cartera de fondos de pensiones', 'Superintendencia de Pensiones',
  'https://www.spensiones.cl', 'mensual', 'monto_pesos en MM$, monto_dolares en MMUS$', 'CLP / USD',
  'period, afp_name, fund_type, section, row_order', 'sp_cartera', 'Clasificar por section, no por código.'),
 ('sp_instrument_prices', 'Cinta de precios de instrumentos', 'Superintendencia de Pensiones',
  'https://www.spensiones.cl', 'diaria', 'precio en la moneda del instrumento', 'currency',
  'date, instrument_id', 'sp_precios', NULL),
 ('cmf_mutual_fund_quota_values', 'Valor cuota, patrimonio y partícipes de fondos mutuos', 'CMF',
  'https://www.cmfchile.cl', 'corte mensual (la fuente es diaria)', 'patrimonio en MILLONES de la moneda del fondo',
  'currency (PESOS | DOLAR)', 'date, fund_run, series', 'cmf_fm_cuotas', 'fund_run sin dígito verificador.'),
 ('cmf_mutual_fund_portfolio', 'Cartera de fondos mutuos', 'CMF', 'https://www.cmfchile.cl',
  'mensual', 'monto en la moneda del instrumento', 'según instrumento', 'sin llave natural', 'cmf_fm_cartera',
  'raw_data JSON con campos FFM_* cuya numeración depende del tipo de cartera.'),
 ('cmf_mutual_fund_fees', 'Comisiones y gastos de fondos mutuos', 'CMF', 'https://www.cmfchile.cl',
  'mensual', 'fracción anual con IVA (0,0119 = 1,19%)', NULL, 'periodo, fund_run, series', 'cmf_fm_comisiones',
  'Remuneración y gasto operacional ya están dentro del valor cuota; solo la comisión de colocación se cobra aparte.'),
 ('cmf_mutual_fund_returns', 'Rentabilidades oficiales de fondos mutuos', 'CMF · Comparador de rentabilidad',
  'https://www.cmfchile.cl', 'foto por corte (trimestral)', 'porcentaje', NULL, 'fecha, fund_run, series',
  'cmf_fm_comparador', '3, 5 y 10 años anualizados; real descuenta UF.');
"""

# ------------------------------------------------------------------
# 003 · maestra de entidades y alias (se puebla con db/entities.rebuild_entities)
# ------------------------------------------------------------------
M003 = """
CREATE TABLE IF NOT EXISTS dim_entidad (
    rut              VARCHAR PRIMARY KEY,   -- sin DV ni ceros a la izquierda
    dv               VARCHAR,
    nombre_canonico  VARCHAR NOT NULL,      -- razón social más reciente
    nombre_corto     VARCHAR,
    tipo             VARCHAR NOT NULL,      -- banco | agf | afp | aseguradora_vida | aseguradora_generales
                                            -- | corredora | agente_valores | emisor | otro
    bank_code        VARCHAR,               -- código SBIF (solo bancos)
    grupo            VARCHAR,               -- grupo financiero (curado, opcional)
    vigente          BOOLEAN,
    ultimo_periodo   INTEGER,
    fuentes          VARCHAR                -- tablas donde aparece, separadas por coma
);
CREATE TABLE IF NOT EXISTS dim_entidad_alias (
    rut            VARCHAR NOT NULL,
    nombre         VARCHAR NOT NULL,
    fuente         VARCHAR NOT NULL,
    desde_periodo  INTEGER,
    hasta_periodo  INTEGER
);
"""

# ------------------------------------------------------------------
# 004 · productos de inversión, canal del proveedor y métricas (db/products.py)
# ------------------------------------------------------------------
M004 = """
CREATE TABLE IF NOT EXISTS dim_producto (
    producto_id          VARCHAR PRIMARY KEY,  -- 'ffmm:8011:A' | 'afp:HABITAT:C'
    tipo                 VARCHAR NOT NULL,     -- ffmm | afp
    rut_proveedor        VARCHAR,              -- → dim_entidad
    proveedor            VARCHAR,
    codigo_fuente        VARCHAR,              -- RUN del fondo / AFP
    serie                VARCHAR,
    nombre               VARCHAR,
    categoria            VARCHAR,              -- familia del comparador CMF / fondo A-E
    tipo_fondo           VARCHAR,              -- glosa oficial del tipo de FFMM
    clase_inversionista  VARCHAR,              -- Retail | Alto Patrimonio | APV | NULL (sin dato)
    moneda               VARCHAR,
    vigente              BOOLEAN,
    primera_fecha        DATE,
    ultima_fecha         DATE
);
CREATE TABLE IF NOT EXISTS proveedor_canal (
    rut_proveedor     VARCHAR PRIMARY KEY,
    nombre            VARCHAR,
    url_sitio         VARCHAR,
    url_contratacion  VARCHAR,
    canal             VARCHAR,
    notas             VARCHAR,
    verificado_en     DATE
);
CREATE TABLE IF NOT EXISTS fact_producto_metricas (
    producto_id            VARCHAR NOT NULL,
    fecha_corte            DATE NOT NULL,
    meses_historia         INTEGER,
    rent_nom_1m DOUBLE, rent_nom_3m DOUBLE, rent_nom_6m DOUBLE, rent_nom_12m DOUBLE,
    rent_nom_36m_ann DOUBLE, rent_nom_60m_ann DOUBLE,
    rent_real_12m DOUBLE, rent_real_36m_ann DOUBLE,
    exceso_mediana_cat_12m DOUBLE,     -- pp sobre la mediana de su categoría
    percentil_cat_12m      DOUBLE,     -- 0-100, 100 = mejor de su categoría
    consistencia_12m       DOUBLE,     -- % de ventanas de 12m en la mitad superior de su categoría
    volatilidad_ann        DOUBLE,     -- desviación de retornos mensuales × √12
    caida_maxima           DOUBLE,     -- máxima caída desde un máximo (negativa)
    meses_recuperacion     INTEGER,    -- meses desde el máximo previo hasta recuperarlo (NULL = no recupera)
    peor_mes               DOUBLE,
    pct_meses_negativos    DOUBLE,
    sharpe                 DOUBLE,     -- (rent_12m_ann − rf) / volatilidad, 36m
    sortino                DOUBLE,
    escala_riesgo          INTEGER,    -- 1-7 por volatilidad (ver db/products.RISK_SCALE)
    costo_anual            DOUBLE,     -- remuneración + gasto operacional (fracción)
    comision_colocacion    DOUBLE,     -- máximo informado (fracción, cobro aparte)
    costo_10a_por_millon   DOUBLE,     -- $ pagados en costos por $1.000.000 a 10 años
    percentil_costo_cat    DOUBLE,     -- 0-100, 100 = el más barato de su categoría
    patrimonio_clp_mm      DOUBLE,     -- millones de pesos
    flujo_neto_12m_mm      DOUBLE,     -- millones de pesos, aportes − rescates estimados
    participes             BIGINT,
    var_participes_12m     BIGINT,
    PRIMARY KEY (producto_id, fecha_corte)
);
"""

# ------------------------------------------------------------------
# 005 · vistas con convenciones homogéneas (período INTEGER AAAAMM y fechas DATE)
# Las tablas de origen guardan el período en seis formatos distintos; las vistas `v_*` lo
# uniforman sin reescribir 8 millones de filas. Lectores nuevos: usar las vistas.
# ------------------------------------------------------------------
M005 = """
CREATE OR REPLACE VIEW v_bank_statements AS
SELECT *, CASE WHEN period < 202201 THEN val_total * 1e6 ELSE val_total END AS val_total_clp
FROM cmf_bank_statements;

CREATE OR REPLACE VIEW v_sp_portfolio_holdings AS
SELECT *, CAST(replace(period, '-', '') AS INTEGER) AS period_int FROM sp_portfolio_holdings;

CREATE OR REPLACE VIEW v_mutual_fund_fees AS
SELECT *, CAST(periodo AS INTEGER) AS period FROM cmf_mutual_fund_fees;

CREATE OR REPLACE VIEW v_mutual_fund_returns AS
SELECT *, CAST(strptime(fecha, '%Y%m%d') AS DATE) AS fecha_date FROM cmf_mutual_fund_returns;

CREATE OR REPLACE VIEW v_mutual_fund_portfolio AS
SELECT *, period_year * 100 + period_month AS period FROM cmf_mutual_fund_portfolio;

CREATE OR REPLACE VIEW v_mutual_fund_quota_values AS
SELECT *, year(date) * 100 + month(date) AS period FROM cmf_mutual_fund_quota_values;

CREATE OR REPLACE VIEW v_insurer_fixed_income AS
SELECT *,
       CAST(try_strptime(fecha_compra, '%Y%m%d') AS DATE)      AS fecha_compra_d,
       CAST(try_strptime(fecha_pago, '%Y%m%d') AS DATE)        AS fecha_pago_d,
       CAST(try_strptime(fecha_emision, '%Y%m%d') AS DATE)     AS fecha_emision_d,
       CAST(try_strptime(fecha_inscripcion, '%Y%m%d') AS DATE) AS fecha_inscripcion_d
FROM cmf_insurer_portfolio_fixed_income;

CREATE OR REPLACE VIEW v_financial_statements AS
SELECT *, CASE WHEN currency LIKE 'NODEF%' THEN NULL ELSE currency END AS currency_norm
FROM cmf_financial_statements;
"""

# ------------------------------------------------------------------
# 006 · cartera de fondos mutuos tipada (NACI y EXTR; OPCI/FUTU sin mapear a propósito)
# ------------------------------------------------------------------
M006 = """
CREATE OR REPLACE VIEW v_mutual_fund_holdings AS
SELECT period_year * 100 + period_month AS period, portfolio_type, fund_run, fund_name,
       json_extract_string(raw_data, '$.' || CASE portfolio_type WHEN 'NACI' THEN 'FFM_6010100' ELSE 'FFM_6020100' END) AS nemotecnico,
       json_extract_string(raw_data, '$.' || CASE portfolio_type WHEN 'NACI' THEN 'FFM_6010300' ELSE 'FFM_6020300' END) AS pais,
       json_extract_string(raw_data, '$.' || CASE portfolio_type WHEN 'NACI' THEN 'FFM_6010400' ELSE 'FFM_6020400' END) AS tipo_instrumento,
       TRY_CAST(json_extract_string(raw_data, '$.' || CASE portfolio_type WHEN 'NACI' THEN 'FFM_6010900' ELSE 'FFM_6020900' END) AS DOUBLE) AS monto,
       json_extract_string(raw_data, '$.' || CASE portfolio_type WHEN 'NACI' THEN 'FFM_6011000' ELSE 'FFM_6021000' END) AS moneda
FROM cmf_mutual_fund_portfolio
WHERE portfolio_type IN ('NACI', 'EXTR');
"""


# ------------------------------------------------------------------
# 007 · exceso sobre depósito a plazo (pp), con la tasa de captación 30-89 días
# ------------------------------------------------------------------
M007 = """
ALTER TABLE fact_producto_metricas ADD COLUMN IF NOT EXISTS exceso_deposito_12m DOUBLE;
"""


MIGRATIONS: list[tuple[str, str, str]] = [
    ("001", "dq_results: controles de calidad", M001),
    ("002", "dim_dataset: metadatos de unidades, frecuencia y fuente", M002),
    ("003", "dim_entidad + alias: maestra de entidades por RUT", M003),
    ("004", "dim_producto, proveedor_canal, fact_producto_metricas", M004),
    ("005", "vistas v_* con período INTEGER y fechas DATE", M005),
    ("006", "v_mutual_fund_holdings: cartera FFMM tipada", M006),
    ("007", "fact_producto_metricas.exceso_deposito_12m", M007),
]

# Migraciones que se reaplican siempre (contenido de referencia o vistas): son idempotentes.
ALWAYS = {"002", "005", "006"}


def run_migrations(conn) -> list[str]:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        id VARCHAR PRIMARY KEY, descripcion VARCHAR, applied_at TIMESTAMP DEFAULT now())""")
    done = {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}
    applied = []
    for mid, desc, sql in MIGRATIONS:
        if mid in done and mid not in ALWAYS:
            continue
        conn.execute(sql)
        if mid not in done:
            conn.execute("INSERT INTO schema_migrations (id, descripcion) VALUES (?, ?)", [mid, desc])
            applied.append(mid)
            logger.info(f"Migración {mid} aplicada: {desc}")
    return applied
