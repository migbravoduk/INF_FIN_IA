"""
db/products.py — Catálogo de productos de inversión y sus métricas comparables.

`dim_producto`: una fila por cosa en la que una persona puede invertir — cada serie de
cada fondo mutuo y cada fondo (A–E) de cada AFP — con su proveedor (RUT → dim_entidad),
categoría y clase de inversionista.

`fact_producto_metricas`: rentabilidad, riesgo, costo, tamaño y flujos por producto y
fecha de corte, calculados sobre una serie MENSUAL de valor cuota en pesos.

Decisiones que no hay que deshacer (ver CONTEXTO.md):
- Rentabilidad por FONDO Y SERIE: cada serie tiene su propio valor cuota.
- Fondos en dólares se convierten a pesos con el dólar observado del día POSTERIOR al
  corte (`Database._ffmm_vc_clp_cte`, validado contra la CMF).
- La remuneración y el gasto operacional YA están dentro del valor cuota: las rentabilidades
  calculadas acá son NETAS de ellos. El costo se informa aparte, nunca se resta otra vez.
- Fondos que reparten beneficios: el valor cuota no incluye el factor de reparto, así que
  su rentabilidad sale subestimada. `ffmm_reparto_sospechoso` los marca para la vista.
- Clase de inversionista: una serie de Alto Patrimonio no es comprable por cualquiera; la
  vista debe filtrar por clase. Si la CMF no la informa, queda NULL (no se adivina).

Tasa libre de riesgo para Sharpe/Sortino: TPM promedio de la ventana (F022.TPM.TIN.D001.NO.Z.D).
Es una aproximación declarada; cuando se incorporen las tasas de captación (D5 del plan) se
puede reemplazar sin tocar el resto.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TPM = "F022.TPM.TIN.D001.NO.Z.D"
# Tasa de captación bancaria 30-89 días en pesos: lo que obtiene un ahorrante en un depósito
# a plazo. Es la tasa libre de riesgo preferida; si no está cargada se usa la TPM.
DEPOSITO = "F022.CAP.TIN.D089.NO.Z.M"
UF = "F073.UFF.PRE.Z.D"

# Escala de riesgo 1–7 por volatilidad anualizada (umbrales de la escala SRRI europea).
RISK_SCALE = [0.005, 0.02, 0.05, 0.10, 0.15, 0.25]


def rebuild_all(db) -> dict:
    """Reconstruye las capas derivadas: entidades, productos, canales y métricas."""
    from db.entities import rebuild_entities
    out = {"entidades": rebuild_entities(db)}
    out["productos"] = rebuild_products(db)
    out["proveedores"] = load_providers(db)
    from db.curves import rebuild_curves
    try:
        out["curvas"] = rebuild_curves(db)
    except Exception as e:  # una falla en las curvas no debe frenar productos ni métricas
        logger.error(f"Curvas: {e}")
        out["curvas"] = 0
    out["metricas"] = compute_metrics(db)
    return out


def risk_scale(vol: float | None) -> int | None:
    if vol is None or not math.isfinite(vol):
        return None
    return 1 + sum(vol >= t for t in RISK_SCALE)


# ============================================================
# Catálogo
# ============================================================

def rebuild_products(db) -> int:
    from db.entities import load_config
    afp_cfg = load_config().get("afp") or {}

    ffmm = db.conn.execute("""
        WITH last_ret AS (
            SELECT fund_run, series, arg_max(familia, fecha) familia,
                   arg_max(clase_inversionista, fecha) clase
            FROM cmf_mutual_fund_returns GROUP BY 1, 2),
        q AS (
            SELECT fund_run, series,
                   arg_max(fund_name, date) nombre, arg_max(agf_rut, date) agf_rut,
                   arg_max(agf_name, date) agf_name, arg_max(fund_type, date) fund_type,
                   arg_max(currency, date) currency, MIN(date) d0, MAX(date) d1
            FROM cmf_mutual_fund_quota_values GROUP BY 1, 2)
        SELECT 'ffmm:' || q.fund_run || ':' || coalesce(q.series, ''), 'ffmm',
               q.agf_rut, q.agf_name, q.fund_run, q.series, q.nombre,
               coalesce(r.familia, t.label), t.label, r.clase,
               CASE q.currency WHEN 'PESOS' THEN 'CLP' WHEN 'DOLAR' THEN 'USD' ELSE q.currency END,
               q.d1 = (SELECT MAX(date) FROM cmf_mutual_fund_quota_values), q.d0, q.d1
        FROM q
        LEFT JOIN last_ret r USING (fund_run, series)
        LEFT JOIN cmf_mutual_fund_types t ON t.code = q.fund_type
    """).fetchall()

    afp = db.conn.execute("""
        SELECT afp_name, fund_type, MIN(date), MAX(date),
               MAX(date) = (SELECT MAX(date) FROM sp_quota_values)
        FROM sp_quota_values GROUP BY 1, 2
    """).fetchall()
    afp_rows = []
    for name, fondo, d0, d1, vig in afp:
        cfg = afp_cfg.get(name, {})
        afp_rows.append((f"afp:{name}:{fondo}", "afp", str(cfg.get("rut", "")).lstrip("0") or None,
                         cfg.get("nombre", f"AFP {name.title()}"), name, fondo,
                         f"Fondo {fondo} — AFP {name.title()}", f"Fondo {fondo}", "Fondo de pensiones",
                         None, "CLP", vig, d0, d1))

    db.conn.execute("BEGIN TRANSACTION")
    try:
        db.conn.execute("DELETE FROM dim_producto")
        db.conn.executemany("INSERT INTO dim_producto VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            list(ffmm) + afp_rows)
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
    n = len(ffmm) + len(afp_rows)
    logger.info(f"dim_producto: {len(ffmm)} series de fondos mutuos + {len(afp_rows)} fondos AFP")
    return n


def load_providers(db) -> int:
    """Carga `proveedor_canal` desde config/proveedores.yaml (curado a mano)."""
    from pathlib import Path
    import yaml
    p = Path(__file__).resolve().parents[1] / "config" / "proveedores.yaml"
    if not p.exists():
        return 0
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = [(str(r["rut"]).lstrip("0"), r.get("nombre"), r.get("url_sitio"), r.get("url_contratacion"),
             r.get("canal"), r.get("notas"), r.get("verificado_en"))
            for r in data.get("proveedores", [])]
    db.conn.execute("DELETE FROM proveedor_canal")
    if rows:
        db.conn.executemany("INSERT INTO proveedor_canal VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


# ============================================================
# Series mensuales en pesos
# ============================================================

def _monthly_panel(db) -> pd.DataFrame:
    """producto_id, fecha (corte mensual), vc_clp, patrimonio_clp_mm, participes."""
    ffmm = db.conn.execute(f"""
        WITH {db._ffmm_vc_clp_cte()}
        SELECT 'ffmm:' || q.fund_run || ':' || coalesce(q.series, '') AS producto_id,
               q.date AS fecha, q.vc_clp,
               CASE WHEN q.currency = 'PESOS' THEN q.net_asset_value
                    ELSE q.net_asset_value * q.vc_clp / nullif(q.quota_value, 0) END AS patrimonio_clp_mm,
               q.participants AS participes
        FROM q WHERE q.vc_clp IS NOT NULL AND q.vc_clp > 0
    """).fetchdf()
    afp = db.conn.execute("""
        SELECT 'afp:' || afp_name || ':' || fund_type AS producto_id,
               arg_max(date, date) AS fecha, arg_max(quota_value, date) AS vc_clp,
               arg_max(equity_value, date) / 1e6 AS patrimonio_clp_mm, NULL::BIGINT AS participes
        FROM sp_quota_values
        GROUP BY afp_name, fund_type, date_trunc('month', date)
    """).fetchdf()
    df = pd.concat([ffmm, afp], ignore_index=True)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["mes"] = df["fecha"].dt.to_period("M")
    # Solo meses CERRADOS: el valor del 13 de noviembre no es el "corte de noviembre". Se
    # descarta el último mes de cada producto si su dato queda a más de 5 días del fin de mes.
    fin_mes = df["mes"].dt.to_timestamp(how="end").dt.normalize()
    ultimo = df.groupby("producto_id")["mes"].transform("max")
    df = df[~((df["mes"] == ultimo) & (df["fecha"] < fin_mes - pd.Timedelta(days=5)))]
    return df.sort_values(["producto_id", "mes"])


def _uf_tpm(db) -> tuple[pd.Series, pd.Series]:
    uf = db.conn.execute(
        f"SELECT date, value FROM observations WHERE series_id = '{UF}' ORDER BY date").fetchdf()
    dep = db.conn.execute(
        f"SELECT date, value FROM observations WHERE series_id = '{DEPOSITO}' ORDER BY date").fetchdf()
    tpm = dep if len(dep) >= 12 else db.conn.execute(
        f"SELECT date, value FROM observations WHERE series_id = '{TPM}' ORDER BY date").fetchdf()
    uf = pd.Series(uf["value"].values, index=pd.to_datetime(uf["date"]))
    tpm = pd.Series(tpm["value"].values, index=pd.to_datetime(tpm["date"]))
    tpm_m = tpm.groupby(tpm.index.to_period("M")).mean() / 100.0
    return uf, tpm_m


# ============================================================
# Métricas
# ============================================================

def _ret(v: pd.Series, k: int) -> float | None:
    if len(v) <= k or v.iloc[-1 - k] <= 0:
        return None
    return float(v.iloc[-1] / v.iloc[-1 - k] - 1)


def _ann(r: float | None, months: int) -> float | None:
    return None if r is None or r <= -1 else (1 + r) ** (12 / months) - 1


# Categorías de renta fija a las que se les estima duración (fondos mutuos de deuda y los
# fondos D y E de pensiones, mayoritariamente renta fija).
_RENTA_FIJA = r"(?i)deuda|^fondo [de]$"


def _duracion(r: pd.Series, infl: pd.Series, factor: pd.DataFrame) -> tuple:
    """
    Duración efectiva por regresión de los últimos 60 meses (mínimo 24):

        r_mes = a + c · inflación_UF_mes + b · Δtasa_mes,    duración = −b

    Δtasa = desplazamiento paralelo de la curva swap en pesos o en UF (db.curves); se queda el
    factor con mayor |t|. La inflación entra como control porque los bonos en UF devengan la
    UF además de su tasa. Devuelve (duración en años, error estándar, 'CLP'|'UF') o Nones.
    """
    mejor = (None, None, None, 0.0)
    for col, nombre in (("clp", "CLP"), ("uf", "UF")):
        if col not in factor:
            continue
        d = pd.concat([r.rename("r"), infl.rename("i"), factor[col].rename("f")], axis=1).dropna().iloc[-60:]
        if len(d) < 24 or d["f"].std() == 0:
            continue
        X = np.column_stack([np.ones(len(d)), d["i"].values, d["f"].values])
        beta, *_ = np.linalg.lstsq(X, d["r"].values, rcond=None)
        res = d["r"].values - X @ beta
        s2 = res @ res / (len(d) - 3)
        try:
            ee = math.sqrt(s2 * np.linalg.inv(X.T @ X)[2, 2])
        except np.linalg.LinAlgError:
            continue
        t = abs(beta[2]) / ee if ee > 0 else 0.0
        if t > mejor[3]:
            mejor = (float(-beta[2]), float(ee), nombre, t)
    # Duración significativamente negativa = la regresión captura otra cosa (tipo de cambio,
    # crédito, derivados), no sensibilidad a tasas: no se informa.
    if mejor[0] is not None and mejor[0] < -2 * mejor[1]:
        return None, None, None
    return mejor[:3]


def _drawdown(v: pd.Series) -> tuple[float | None, int | None]:
    if len(v) < 2:
        return None, None
    peak = v.cummax()
    dd = v / peak - 1
    mdd = float(dd.min())
    if mdd >= 0:
        return 0.0, 0
    trough = int(np.argmin(dd.values))
    peak_idx = int(np.argmax(v.values[: trough + 1]))
    after = np.where(v.values[trough:] >= v.values[peak_idx])[0]
    rec = int(trough + after[0] - peak_idx) if len(after) else None
    return mdd, rec


def compute_metrics(db, fecha_corte=None) -> int:
    """
    Calcula `fact_producto_metricas` por tipo de producto, cada uno en SU último corte
    mensual: las AFP publican a diario y los fondos mutuos se cargan por corte mensual, así
    que un corte común dejaría a uno de los dos sin dato.
    """
    panel = _monthly_panel(db)
    total = 0
    for tipo in ("ffmm", "afp"):
        sub = panel[panel["producto_id"].str.startswith(tipo + ":")]
        if not sub.empty:
            total += _compute_metrics(db, sub, fecha_corte)
    return total


def _compute_metrics(db, panel, fecha_corte=None) -> int:
    if panel.empty:
        return 0
    uf, tpm_m = _uf_tpm(db)
    prods = db.conn.execute(
        "SELECT producto_id, tipo, categoria, clase_inversionista, moneda FROM dim_producto").fetchdf()
    cat = dict(zip(prods["producto_id"], prods["categoria"]))
    corte_mes = (pd.Period(fecha_corte, "M") if fecha_corte else panel["mes"].max())

    fees = db.conn.execute("""
        SELECT 'ffmm:' || fund_run || ':' || series AS producto_id,
               coalesce(remuneracion_unico, remuneracion_max, 0) + coalesce(gasto_operacional, 0) AS costo,
               coalesce(com_colocacion_unico, com_colocacion_max) AS coloc
        FROM cmf_mutual_fund_fees
        WHERE periodo = (SELECT MAX(periodo) FROM cmf_mutual_fund_fees)
    """).fetchdf().set_index("producto_id")

    # Retornos mensuales por producto (índice = mes).
    wide = panel.pivot_table(index="mes", columns="producto_id", values="vc_clp", aggfunc="last")
    wide = wide.loc[:corte_mes]
    rets = wide.pct_change(fill_method=None)
    pat = panel.pivot_table(index="mes", columns="producto_id", values="patrimonio_clp_mm", aggfunc="last").loc[:corte_mes]
    par = panel.pivot_table(index="mes", columns="producto_id", values="participes", aggfunc="last").loc[:corte_mes]

    # Retorno 12m de todos los productos en cada mes → ranking por categoría (consistencia).
    r12 = wide / wide.shift(12) - 1
    cats = pd.Series(cat)

    def pct_rank_row(row: pd.Series) -> pd.Series:
        df = pd.DataFrame({"r": row, "c": cats.reindex(row.index)}).dropna()
        return df.groupby("c")["r"].rank(pct=True)

    r12_recent = r12.iloc[-36:]
    ranks = r12_recent.apply(pct_rank_row, axis=1)

    # Insumos de la duración: inflación UF mensual y cambio de nivel de las curvas swap.
    from db.curves import factor_tasas_mensual
    factor = factor_tasas_mensual(db)
    uf_m = uf.groupby(uf.index.to_period("M")).last()
    infl_m = uf_m / uf_m.shift(1) - 1
    # Solo fondos en pesos: en los fondos en dólares la rentabilidad en pesos la domina el tipo
    # de cambio, y la regresión devuelve "duraciones" negativas sin sentido (medido: −4 años).
    es_rf = (prods.set_index("producto_id")["categoria"].fillna("").str.contains(_RENTA_FIJA, regex=True)
             & prods.set_index("producto_id")["moneda"].eq("CLP"))

    uf_at = lambda m: uf[: m.to_timestamp(how="end")].iloc[-1] if len(uf[: m.to_timestamp(how="end")]) else None

    out = []
    for pid in wide.columns:
        v = wide[pid].dropna()
        if v.empty or v.index[-1] != corte_mes:
            continue  # sin dato en el corte: producto cerrado o sin publicación
        r = rets[pid].dropna()
        n = len(v)
        rec = {"producto_id": pid, "fecha_corte": corte_mes.to_timestamp(how="end").date(), "meses_historia": n}
        for k in (1, 3, 6, 12):
            rec[f"rent_nom_{k}m"] = _ret(v, k)
        rec["rent_nom_36m_ann"] = _ann(_ret(v, 36), 36)
        rec["rent_nom_60m_ann"] = _ann(_ret(v, 60), 60)
        # Real: deflactado por la UF entre las mismas fechas.
        def real(k):
            nom = _ret(v, k)
            if nom is None:
                return None
            u1, u0 = uf_at(v.index[-1]), uf_at(v.index[-1 - k])
            return None if not (u1 and u0) else (1 + nom) / (u1 / u0) - 1
        rec["rent_real_12m"] = real(12)
        rec["rent_real_36m_ann"] = _ann(real(36), 36)

        r36 = r.iloc[-36:]
        vol = float(r36.std(ddof=1) * math.sqrt(12)) if len(r36) >= 12 else None
        rec["volatilidad_ann"] = vol
        rec["caida_maxima"], rec["meses_recuperacion"] = _drawdown(v.iloc[-61:])
        rec["peor_mes"] = float(r36.min()) if len(r36) else None
        rec["pct_meses_negativos"] = float((r36 < 0).mean()) if len(r36) else None
        rf = tpm_m.reindex(r36.index).mean() if len(r36) else None
        if vol and rf is not None and not math.isnan(rf):
            ann = (1 + r36).prod() ** (12 / len(r36)) - 1
            rec["sharpe"] = (ann - rf) / vol
            down = r36[r36 < rf / 12]
            dvol = float(np.sqrt(((down - rf / 12) ** 2).sum() / len(r36)) * math.sqrt(12)) if len(down) else None
            rec["sortino"] = (ann - rf) / dvol if dvol else None
        rec["escala_riesgo"] = risk_scale(vol)
        # Serie anómala: algún mes con variación de más de ±50% del valor cuota. Medido
        # (nov-2026): series de un solo partícipe cuyo valor cuota salta ×10 y ×0,1 entre
        # meses (p. ej. 9414-S). No es rentabilidad real: se excluye de rankings y de las
        # estadísticas de su categoría, y la ficha lo advierte.
        rec["serie_anomala"] = bool((r.iloc[-36:].abs() > 0.5).any())
        # ¿Le ganó al depósito a plazo? Rentabilidad 12m menos la tasa compuesta del período.
        r12 = rec.get("rent_nom_12m")
        rf12 = tpm_m.reindex(v.index[-12:]).dropna()
        if r12 is not None and len(rf12) >= 10:
            rec["exceso_deposito_12m"] = (r12 - (float(np.prod(1 + rf12 / 12)) - 1)) * 100

        if not factor.empty and es_rf.get(pid, False) and not rec["serie_anomala"]:
            rec["duracion_efectiva"], rec["duracion_ee"], rec["duracion_factor"] = _duracion(r, infl_m, factor)

        if pid in fees.index:
            f = fees.loc[pid]
            rec["costo_anual"] = float(f["costo"]) if pd.notna(f["costo"]) else None
            rec["comision_colocacion"] = float(f["coloc"]) if pd.notna(f["coloc"]) else None

        p = pat[pid].dropna() if pid in pat else pd.Series(dtype=float)
        if len(p):
            rec["patrimonio_clp_mm"] = float(p.iloc[-1])
        if len(p) >= 13 and len(r) >= 12:
            # Flujo neto = Δ patrimonio − patrimonio previo × rentabilidad del mes.
            pp = p.iloc[-13:]
            rr = r.reindex(pp.index).iloc[1:]
            flujos = pp.iloc[1:].values - pp.iloc[:-1].values * (1 + rr.values)
            rec["flujo_neto_12m_mm"] = float(np.nansum(flujos))
        q = par[pid].dropna() if pid in par else pd.Series(dtype=float)
        if len(q):
            rec["participes"] = int(q.iloc[-1])
            if len(q) >= 13:
                rec["var_participes_12m"] = int(q.iloc[-1] - q.iloc[-13])
        if pid in ranks.columns:
            rk = ranks[pid].dropna()
            if len(rk):
                rec["consistencia_12m"] = float((rk > 0.5).mean() * 100)
        out.append(rec)

    m = pd.DataFrame(out)
    if m.empty:
        return 0
    cols = [c[0] for c in db.conn.execute("DESCRIBE fact_producto_metricas").fetchall()]
    for c in cols:
        if c not in m:
            m[c] = np.nan
    num = [c for c in cols if c not in ("producto_id", "fecha_corte", "duracion_factor")]
    m[num] = m[num].apply(pd.to_numeric, errors="coerce")
    m["categoria"] = m["producto_id"].map(cat)
    m["serie_anomala"] = m["serie_anomala"].fillna(False).astype(bool)
    # Las series anómalas no entran en mediana, percentiles ni costo relativo.
    m.loc[m["serie_anomala"], "categoria"] = None

    # Posición en la categoría (12m) y costo relativo.
    g = m.groupby("categoria")
    m["exceso_mediana_cat_12m"] = (m["rent_nom_12m"] - g["rent_nom_12m"].transform("median")) * 100
    m["percentil_cat_12m"] = g["rent_nom_12m"].rank(pct=True) * 100
    m["percentil_costo_cat"] = (1 - g["costo_anual"].rank(pct=True)) * 100 + \
        100 / g["costo_anual"].transform("count").clip(lower=1)

    # Costo en pesos a 10 años: supuesto = rentabilidad bruta mediana de la categoría a 36m.
    med36 = g["rent_nom_36m_ann"].transform("median").fillna(g["rent_nom_12m"].transform("median"))

    def costo_10a(row, r_net):
        c = row["costo_anual"]
        if c is None or pd.isna(c) or pd.isna(r_net):
            return None
        bruto = max(r_net + c, 0.0)
        sin = 1e6 * (1 + bruto) ** 10
        con = 1e6 * (1 + bruto - c) ** 10
        coloc = row.get("comision_colocacion")
        con = con * (1 - coloc) if coloc and not pd.isna(coloc) else con
        return sin - con

    m["costo_10a_por_millon"] = [costo_10a(row, med36.iloc[i]) for i, (_, row) in enumerate(m.iterrows())]
    m = m.drop(columns=["categoria"])

    cols = [c[0] for c in db.conn.execute("DESCRIBE fact_producto_metricas").fetchall()]
    for c in cols:
        if c not in m:
            m[c] = None
    m = m[cols].astype(object).where(pd.notna(m[cols]), None)
    fc = m["fecha_corte"].iloc[0]
    tipo = m["producto_id"].iloc[0].split(":")[0]
    db.conn.execute("DELETE FROM fact_producto_metricas WHERE fecha_corte = ? AND producto_id LIKE ?",
                    [fc, tipo + ":%"])
    db.conn.executemany(
        f"INSERT INTO fact_producto_metricas VALUES ({', '.join('?' * len(cols))})",
        m.values.tolist())
    logger.info(f"fact_producto_metricas: {len(m)} productos al {fc}")
    return len(m)


def ffmm_reparto_sospechoso(db) -> set[str]:
    """
    Series cuya rentabilidad propia queda >2 pp BAJO la oficial a 1 año: la firma del factor
    de reparto que no está en la estadística (CONTEXTO: 27 fondos, mediana −4,97 pp, sesgo de
    un solo lado). La vista muestra la cifra oficial para ellas y lo advierte.
    """
    rows = db.conn.execute("""
        WITH r AS (
            SELECT fund_run, series, fecha, rent_nominal_1a FROM cmf_mutual_fund_returns
            WHERE fecha = (SELECT MAX(fecha) FROM cmf_mutual_fund_returns)),
        -- Solo fondos en pesos: la cifra oficial está en pesos y la de un fondo en dólares
        -- sin convertir no es comparable (daría falsos positivos).
        v AS (SELECT fund_run, series, date, quota_value FROM cmf_mutual_fund_quota_values
              WHERE currency = 'PESOS')
        SELECT r.fund_run, r.series, r.rent_nominal_1a,
               (SELECT v1.quota_value FROM v v1 WHERE v1.fund_run = r.fund_run AND v1.series = r.series
                  AND v1.date <= strptime(r.fecha, '%Y%m%d') ORDER BY v1.date DESC LIMIT 1) AS q1,
               (SELECT v0.quota_value FROM v v0 WHERE v0.fund_run = r.fund_run AND v0.series = r.series
                  AND v0.date <= strptime(r.fecha, '%Y%m%d') - INTERVAL 1 YEAR ORDER BY v0.date DESC LIMIT 1) AS q0
        FROM r WHERE r.rent_nominal_1a IS NOT NULL
    """).fetchall()
    out = set()
    for run, ser, oficial, q1, q0 in rows:
        if q1 and q0:
            propia = (q1 / q0 - 1) * 100
            if oficial - propia > 2.0:
                out.add(f"ffmm:{run}:{ser}")
    return out


# ============================================================
# Consultas para la web (/productos, /producto/{id})
# ============================================================

ORDENES = {
    "rent_12m": ("m.rent_nom_12m", "DESC"),
    "rent_real_12m": ("m.rent_real_12m", "DESC"),
    "rent_36m": ("m.rent_nom_36m_ann", "DESC"),
    "percentil": ("m.percentil_cat_12m", "DESC"),
    "consistencia": ("m.consistencia_12m", "DESC"),
    "costo": ("m.costo_anual", "ASC"),
    "volatilidad": ("m.volatilidad_ann", "ASC"),
    "sharpe": ("m.sharpe", "DESC"),
    "patrimonio": ("m.patrimonio_clp_mm", "DESC"),
    "flujo": ("m.flujo_neto_12m_mm", "DESC"),
}


def latest_metrics_date(db):
    r = db.conn.execute("SELECT MAX(fecha_corte) FROM fact_producto_metricas").fetchone()
    return r[0] if r else None


def product_filters(db) -> dict:
    cats = db.conn.execute("""
        SELECT tipo, categoria, COUNT(*) FROM dim_producto WHERE vigente GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    clases = [r[0] for r in db.conn.execute(
        "SELECT DISTINCT clase_inversionista FROM dim_producto WHERE clase_inversionista IS NOT NULL ORDER BY 1").fetchall()]
    provs = db.conn.execute("""
        SELECT rut_proveedor, any_value(coalesce(e.nombre_corto, p.proveedor)), COUNT(*)
        FROM dim_producto p LEFT JOIN dim_entidad e ON e.rut = p.rut_proveedor
        WHERE p.vigente AND p.rut_proveedor IS NOT NULL GROUP BY 1 ORDER BY 2
    """).fetchall()
    return {"categorias": cats, "clases": clases, "proveedores": provs}


def list_products(db, tipo="", categoria="", clase="", moneda="", proveedor="", q="",
                  orden="rent_12m", limit=100) -> tuple[list[dict], int]:
    col, direction = ORDENES.get(orden, ORDENES["rent_12m"])
    where, params = ["p.vigente", "NOT coalesce(m.serie_anomala, false)", "m.fecha_corte = (SELECT MAX(x.fecha_corte) FROM fact_producto_metricas x WHERE x.producto_id = m.producto_id)"], []
    for val, sql in ((tipo, "p.tipo = ?"), (categoria, "p.categoria = ?"), (moneda, "p.moneda = ?"),
                     (proveedor, "p.rut_proveedor = ?")):
        if val:
            where.append(sql); params.append(val)
    if clase == "sin_dato":
        where.append("p.clase_inversionista IS NULL")
    elif clase:
        where.append("p.clase_inversionista = ?"); params.append(clase)
    if q:
        where.append("(p.nombre ILIKE ? OR p.proveedor ILIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    base = f"""FROM dim_producto p JOIN fact_producto_metricas m USING (producto_id)
               LEFT JOIN dim_entidad e ON e.rut = p.rut_proveedor WHERE {w}"""
    total = db.conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    df = db.conn.execute(f"""
        SELECT p.*, coalesce(e.nombre_corto, p.proveedor) AS proveedor_corto, m.*
        {base} ORDER BY {col} {direction} NULLS LAST LIMIT {int(limit)}
    """, params).fetchdf()
    df = df.loc[:, ~df.columns.duplicated()]
    return df.astype(object).where(df.notna(), None).to_dict("records"), total


def get_product(db, producto_id: str) -> dict | None:
    df = db.conn.execute("""
        SELECT p.*, coalesce(e.nombre_corto, p.proveedor) AS proveedor_corto,
               e.nombre_canonico AS proveedor_razon_social, e.tipo AS proveedor_tipo,
               c.url_sitio, c.url_contratacion, c.canal
        FROM dim_producto p
        LEFT JOIN dim_entidad e ON e.rut = p.rut_proveedor
        LEFT JOIN proveedor_canal c ON c.rut_proveedor = p.rut_proveedor
        WHERE p.producto_id = ?""", [producto_id]).fetchdf()
    if df.empty:
        return None
    prod = df.astype(object).where(df.notna(), None).to_dict("records")[0]
    m = db.conn.execute("""SELECT * FROM fact_producto_metricas WHERE producto_id = ?
                           ORDER BY fecha_corte DESC LIMIT 1""", [producto_id]).fetchdf()
    prod["m"] = m.astype(object).where(m.notna(), None).to_dict("records")[0] if not m.empty else {}
    if prod["categoria"]:
        n = db.conn.execute("""
            SELECT COUNT(*) FROM dim_producto p JOIN fact_producto_metricas m USING (producto_id)
            WHERE p.categoria = ? AND m.fecha_corte = (SELECT MAX(x.fecha_corte) FROM fact_producto_metricas x WHERE x.producto_id = m.producto_id)
        """, [prod["categoria"]]).fetchone()[0]
        prod["n_categoria"] = n
    # ¿Tiene EEFF el proveedor? (las AGF reportan IFRS a la CMF)
    if prod.get("rut_proveedor"):
        prod["tiene_eeff"] = bool(db.conn.execute(
            "SELECT 1 FROM cmf_financial_statements WHERE rut = ? LIMIT 1", [prod["rut_proveedor"]]).fetchone())
    return prod


def product_series(db, producto_id: str) -> list[dict]:
    """Serie mensual de valor cuota en pesos, base 100, + mediana de su categoría."""
    panel = _monthly_panel(db)
    cat = db.conn.execute("SELECT categoria FROM dim_producto WHERE producto_id = ?", [producto_id]).fetchone()
    s = panel[panel["producto_id"] == producto_id].set_index("mes")["vc_clp"]
    if s.empty:
        return []
    peers = db.conn.execute("SELECT producto_id FROM dim_producto WHERE categoria = ? AND vigente",
                            [cat[0] if cat else None]).fetchall()
    peer_ids = {r[0] for r in peers}
    wide = panel[panel["producto_id"].isin(peer_ids)].pivot_table(
        index="mes", columns="producto_id", values="vc_clp", aggfunc="last").loc[s.index.min():]
    rets = wide.pct_change(fill_method=None).median(axis=1).fillna(0)
    med = 100 * (1 + rets).cumprod()
    base = 100 * s / s.iloc[0]
    return [{"mes": str(m), "producto": float(base[m]), "mediana": float(med.get(m, float("nan")))}
            for m in base.index]


def product_holdings(db, producto_id: str, limit: int = 15) -> dict:
    """Composición de cartera (FFMM) del último período: por tipo de instrumento y país."""
    if not producto_id.startswith("ffmm:"):
        return {}
    run = producto_id.split(":")[1]
    per = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_holdings WHERE fund_run = ?", [run]).fetchone()[0]
    if not per:
        return {}
    q = lambda col: db.conn.execute(f"""
        SELECT coalesce({col}, '—'), SUM(monto) / SUM(SUM(monto)) OVER () * 100
        FROM v_mutual_fund_holdings WHERE fund_run = ? AND period = ? AND monto > 0
        GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}""", [run, per]).fetchall()
    return {"period": per, "tipo": q("tipo_instrumento"), "pais": q("pais"), "emisor": q("nemotecnico")}


def overlap(db, run_a: str, run_b: str) -> float | None:
    """% de cartera en común entre dos fondos (suma de mínimos de pesos por nemotécnico)."""
    per = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_holdings").fetchone()[0]
    r = db.conn.execute("""
        WITH w AS (
            SELECT fund_run, nemotecnico, SUM(monto) / SUM(SUM(monto)) OVER (PARTITION BY fund_run) AS peso
            FROM v_mutual_fund_holdings
            WHERE period = ? AND fund_run IN (?, ?) AND monto > 0 AND nemotecnico IS NOT NULL
            GROUP BY 1, 2)
        SELECT SUM(least(a.peso, b.peso)) * 100
        FROM w a JOIN w b ON a.nemotecnico = b.nemotecnico AND a.fund_run = ? AND b.fund_run = ?
    """, [per, run_a, run_b, run_a, run_b]).fetchone()
    return float(r[0]) if r and r[0] is not None else (0.0 if per else None)
