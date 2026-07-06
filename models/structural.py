"""
Modelo ESTRUCTURAL de proyección de EEFF (activos → productividad → resultados).

Cadena (metodología del usuario, decisiones jul-2026):
  1. STOCK:        Δlog(activos operacionales) = α_i + β_i' · X_macro
                   con β_i estimado por SHRINKAGE de dos niveles:
                   empresa → sector → global (ataca la debilidad de ~40 obs
                   por empresa que el backtest detectó en el SARIMAX directo).
  2. PRODUCTIVIDAD: rotación = ingresos_4T / activos_op  y  margen = resultado_4T
                   / ingresos_4T se proyectan con promedio móvil 4T que revierte
                   suavemente a la media histórica de la empresa (φ^h).
  3. RESULTADOS:   ingresos_4T = activos × rotación; resultado_4T = ingresos × margen.
                   El trimestral se recupera por telescopía de sumas móviles:
                   y(t) = y(t-4) + S4(t) - S4(t-1).

Activos operacionales = PP&E + inventarios corrientes + deudores comerciales
(cada componente entra solo si la empresa lo reporta con regularidad).

Factores macro X_t (historia efectiva / senda EEE vintage-consistente):
  act_yoy (actividad), ipc_yoy (inflación), tpm (tasa), tc_yoy (var. 12m del TC).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models.macro_path import MacroPath, build_macro_path

logger = logging.getLogger(__name__)

OP_ASSET_ACCOUNTS = [
    "Propiedades, planta y equipo",
    "Inventarios corrientes",
    "Deudores comerciales y otras cuentas por cobrar corrientes",
]
FACTORS = ["act_yoy", "ipc_yoy", "tpm", "tc_yoy"]

MIN_COMPONENT_SHARE = 0.6   # un componente entra si se reporta en >=60% de los períodos
DLOG_WINSOR = 0.35          # |Δlog A| > 0.35 trimestral se recorta (M&A, revalorizaciones)
MIN_OBS_COMPANY = 12        # mínimo de Δlog para OLS propio; si no, hereda beta sectorial
LAMBDA_COMPANY = 80.0       # shrinkage empresa → sector (n=40 → peso propio ~1/3)
LAMBDA_SECTOR = 200.0       # shrinkage sector → global
RATIO_SHORT_Q = 4           # ventana del nivel reciente del ratio
RATIO_LONG_Q = 20           # ventana de la media de reversión (5 años)
RATIO_PHI = 0.85            # velocidad de reversión del ratio por trimestre


def _next_quarter(period: str) -> str:
    y, m = int(period[:4]), int(period[4:])
    m += 3
    return f"{y + (m > 12)}{m - 12 if m > 12 else m:02d}"


def _quarterly_factors(macro: MacroPath) -> pd.DataFrame:
    """Factores trimestrales (historia + senda) con tc_yoy derivado de los niveles."""
    full = macro.full().copy()
    full["tc_yoy"] = full["tc"].pct_change(12) * 100.0
    q = full.resample("QE").mean()
    q.index = [f"{d.year}{d.month:02d}" for d in q.index]
    return q[FACTORS].ffill()


# ============================================================
# Panel de datos (se carga UNA vez para todas las empresas)
# ============================================================

@dataclass
class PanelData:
    assets: pd.DataFrame      # index period 'YYYYMM', columns rut → activos_op
    revenue: pd.DataFrame     # ingresos trimestrales desacumulados
    income: pd.DataFrame      # resultado trimestral desacumulado
    sectors: dict = field(default_factory=dict)   # rut → macro_sector
    names: dict = field(default_factory=dict)     # rut → company_name


def load_panel(db) -> PanelData:
    """Carga stocks operacionales y flujos trimestrales de TODAS las empresas."""
    from api.company_profiles import get_profile

    # --- Stocks ESF (point-in-time, sin desacumular) ---
    stocks = db.conn.execute("""
        SELECT rut, ANY_VALUE(company_name) AS nombre, account_name, period,
               MAX(value) AS value
        FROM cmf_financial_statements
        WHERE statement_group LIKE 'ESF%' AND account_name IN (?, ?, ?)
        GROUP BY rut, account_name, period
    """, OP_ASSET_ACCOUNTS).fetchdf()

    # --- Flujos ERFG (acumulados en el año → desacumular) ---
    flows = db.conn.execute("""
        SELECT rut, account_name, period, MAX(value) AS value
        FROM cmf_financial_statements
        WHERE statement_group = 'ERFG'
          AND account_name IN ('Ingresos de actividades ordinarias', 'Ganancia (pérdida)')
        GROUP BY rut, account_name, period
    """).fetchdf()

    periods = sorted(set(stocks["period"].astype(int)) | set(flows["period"].astype(int)))
    pidx = [str(p) for p in periods]

    def pivot_stock(df):
        w = df.pivot_table(index="period", columns="rut", values="value", aggfunc="max")
        w.index = w.index.astype(int).astype(str)
        return w.reindex(pidx)

    # Activos operacionales: suma de componentes regulares (>=60% de cobertura), con ffill
    assets_parts = {}
    for acct in OP_ASSET_ACCOUNTS:
        assets_parts[acct] = pivot_stock(stocks[stocks["account_name"] == acct])
    assets = None
    for acct, w in assets_parts.items():
        keep = w.columns[w.notna().mean() >= MIN_COMPONENT_SHARE]
        wk = w[keep].ffill()
        assets = wk if assets is None else assets.add(wk, fill_value=0.0)

    # Flujos: desacumular intra-anual por columna
    def deaccumulate(w: pd.DataFrame) -> pd.DataFrame:
        years = pd.Series([p[:4] for p in w.index], index=w.index)
        same_year = years.eq(years.shift(1)).values  # (n,)
        prev = w.shift(1)
        vals = np.where(same_year[:, None], w.values - prev.values, w.values)
        return pd.DataFrame(vals, index=w.index, columns=w.columns)

    rev_w = pivot_stock(flows[flows["account_name"] == "Ingresos de actividades ordinarias"])
    inc_w = pivot_stock(flows[flows["account_name"] == "Ganancia (pérdida)"])

    names = dict(stocks.groupby("rut")["nombre"].first())
    sectors = {rut: get_profile(str(rut), str(nm))["macro_sector"]
               for rut, nm in names.items()}

    return PanelData(assets=assets, revenue=deaccumulate(rev_w),
                     income=deaccumulate(inc_w), sectors=sectors, names=names)


# ============================================================
# Estimación: shrinkage de dos niveles (empresa → sector → global)
# ============================================================

@dataclass
class AssetModel:
    betas: dict          # rut → np.array(len(FACTORS))
    alphas: dict         # rut → float
    sigmas: dict         # rut → desvío del residuo Δlog
    sector_betas: dict   # sector → beta (diagnóstico)
    global_beta: np.ndarray = None
    factor_names: list = field(default_factory=lambda: list(FACTORS))


def _ols(y: np.ndarray, X: np.ndarray):
    """OLS con intercepto; devuelve (beta_sin_intercepto, alpha, sigma_resid)."""
    Xc = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ coef
    sigma = float(np.std(resid, ddof=min(len(coef), len(y) - 1)))
    return coef[1:], float(coef[0]), sigma


def estimate_asset_model(panel: PanelData, factors_q: pd.DataFrame,
                         as_of_period: str | None = None) -> AssetModel:
    """
    Estima Δlog(activos_op) ~ X_macro con shrinkage de dos niveles.
    `as_of_period` ('YYYYMM'): usar solo trimestres <= ese período (backtest).
    """
    assets = panel.assets
    if as_of_period is not None:
        assets = assets.loc[[p for p in assets.index if p <= as_of_period]]

    dlog = np.log(assets.where(assets > 0)).diff().clip(-DLOG_WINSOR, DLOG_WINSOR)
    common = [p for p in dlog.index if p in factors_q.index]
    dlog = dlog.loc[common]
    X_all = factors_q.loc[common].values

    # Observaciones apiladas por empresa (para sector y global)
    per_company = {}
    for rut in dlog.columns:
        y = dlog[rut].values
        mask = np.isfinite(y) & np.isfinite(X_all).all(axis=1)
        if mask.sum() >= 4:
            per_company[rut] = (y[mask], X_all[mask])

    def pooled_beta(ruts):
        """Within-OLS: demean por empresa (absorbe α_i) y apila."""
        ys, Xs = [], []
        for r in ruts:
            if r not in per_company:
                continue
            y, X = per_company[r]
            ys.append(y - y.mean())
            Xs.append(X - X.mean(axis=0))
        if not ys or sum(len(v) for v in ys) < 30:
            return None, 0
        Y, XX = np.concatenate(ys), np.vstack(Xs)
        beta, *_ = np.linalg.lstsq(XX, Y, rcond=None)
        return beta, len(Y)

    global_beta, _ = pooled_beta(list(per_company))
    if global_beta is None:
        raise RuntimeError("Panel insuficiente para estimar el modelo de activos")

    # Sector → global
    sector_betas = {}
    by_sector = {}
    for rut, sec in panel.sectors.items():
        by_sector.setdefault(sec, []).append(rut)
    for sec, ruts in by_sector.items():
        b, n = pooled_beta(ruts)
        if b is None:
            sector_betas[sec] = global_beta
        else:
            w = n / (n + LAMBDA_SECTOR)
            sector_betas[sec] = w * b + (1 - w) * global_beta

    # Empresa → sector
    betas, alphas, sigmas = {}, {}, {}
    for rut, (y, X) in per_company.items():
        sec_beta = sector_betas.get(panel.sectors.get(rut), global_beta)
        n = len(y)
        if n >= MIN_OBS_COMPANY:
            b_own, _, _ = _ols(y, X)
            w = n / (n + LAMBDA_COMPANY)
            beta = w * b_own + (1 - w) * sec_beta
        else:
            beta = sec_beta
        # α_i preserva la deriva propia de la empresa; σ del residuo con β final
        alpha = float(y.mean() - beta @ X.mean(axis=0))
        resid = y - (alpha + X @ beta)
        betas[rut], alphas[rut] = beta, alpha
        sigmas[rut] = float(np.std(resid)) if n >= 8 else 0.10

    return AssetModel(betas=betas, alphas=alphas, sigmas=sigmas,
                      sector_betas=sector_betas, global_beta=global_beta)


# ============================================================
# Proyección estructural por empresa
# ============================================================

def _revert_ratio(series: pd.Series, steps: int, anchor: str = "mean4") -> np.ndarray:
    """
    Senda del ratio: nivel reciente que revierte a la media histórica con φ^h.
    anchor='mean4' (promedio móvil 4T) o 'last' (último valor observado —
    'mantener la relación de productividad' literal; robusto a saltos de
    activos por M&A donde el promedio mezcla pre/post quiebre).
    """
    s = series.dropna()
    if s.empty:
        return np.full(steps, np.nan)
    r0 = float(s.iloc[-1]) if anchor == "last" else float(s.tail(RATIO_SHORT_Q).mean())
    mu = float(s.tail(RATIO_LONG_Q).mean())
    return np.array([mu + (r0 - mu) * RATIO_PHI ** (h + 1) for h in range(steps)])


def forecast_structural(db, rut: str, steps: int = 8,
                        panel: PanelData | None = None,
                        model: AssetModel | None = None,
                        macro: MacroPath | None = None,
                        as_of_period: str | None = None,
                        ratio_anchor: str = "mean4") -> dict | None:
    """
    Proyección estructural. Devuelve dict con series trimestrales proyectadas
    para ingresos y ganancia + trayectoria de activos y ratios (narrativa).
    """
    if panel is None:
        panel = load_panel(db)
    if macro is None:
        macro = build_macro_path(db, horizon_months=3 * steps + 9)
    factors_q = _quarterly_factors(macro)
    if model is None:
        model = estimate_asset_model(panel, factors_q, as_of_period)

    rut = str(rut)
    if rut not in panel.assets.columns or rut not in model.betas:
        return None

    cut = (lambda s: s.loc[[p for p in s.index if p <= as_of_period]]) if as_of_period \
        else (lambda s: s)
    a = cut(panel.assets[rut]).dropna()
    rev = cut(panel.revenue[rut] if rut in panel.revenue.columns else pd.Series(dtype=float))
    inc = cut(panel.income[rut] if rut in panel.income.columns else pd.Series(dtype=float))
    if a.shape[0] < 8 or rev.dropna().shape[0] < 8:
        return None

    # Ratios históricos sobre sumas móviles 4T
    rev4 = rev.rolling(4).sum()
    inc4 = inc.rolling(4).sum()
    common = a.index.intersection(rev4.index)
    rot = (rev4.loc[common] / a.loc[common]).replace([np.inf, -np.inf], np.nan)
    mgn = (inc4 / rev4).replace([np.inf, -np.inf], np.nan)

    # Períodos futuros y factores
    fc_periods, p = [], _next_quarter(a.index[-1])
    for _ in range(steps):
        fc_periods.append(p)
        p = _next_quarter(p)
    if any(p not in factors_q.index for p in fc_periods):
        return None
    Xf = factors_q.loc[fc_periods].values

    # Paso 1: trayectoria de activos
    beta, alpha, sigma = model.betas[rut], model.alphas[rut], model.sigmas[rut]
    dlog_path = alpha + Xf @ beta
    a_path = float(a.iloc[-1]) * np.exp(np.cumsum(dlog_path))
    a_lo = float(a.iloc[-1]) * np.exp(np.cumsum(dlog_path) - 1.28 * sigma * np.sqrt(np.arange(1, steps + 1)))
    a_hi = float(a.iloc[-1]) * np.exp(np.cumsum(dlog_path) + 1.28 * sigma * np.sqrt(np.arange(1, steps + 1)))

    # Paso 2: ratios con reversión
    rot_path = _revert_ratio(rot, steps, anchor=ratio_anchor)
    mgn_path = _revert_ratio(mgn, steps, anchor=ratio_anchor)
    if not np.isfinite(rot_path).all():
        return None

    # Paso 3: niveles 4T y telescopía a trimestral
    rev4_path = a_path * rot_path
    inc4_path = rev4_path * (mgn_path if np.isfinite(mgn_path).all() else np.full(steps, np.nan))

    def telescoped(hist_q: pd.Series, hist_4t: pd.Series, s4_path: np.ndarray) -> pd.Series:
        """y(t) = y(t-4) + S4(t) - S4(t-1), recursivo sobre el horizonte."""
        yq = dict(hist_q.dropna().items())
        s4 = dict(hist_4t.dropna().items())
        out = {}
        for h, p_ in enumerate(fc_periods):
            s4[p_] = s4_path[h]
            prev_p = a.index[-1] if h == 0 else fc_periods[h - 1]
            y_lag = out.get(_lag4(p_), yq.get(_lag4(p_), np.nan))
            s4_prev = s4.get(prev_p, np.nan)
            out[p_] = y_lag + s4[p_] - s4_prev
        return pd.Series(out)

    def _lag4(period: str) -> str:
        return f"{int(period[:4]) - 1}{period[4:]}"

    rev_fc = telescoped(rev, rev4, rev4_path)
    inc_fc = telescoped(inc, inc4, inc4_path) if np.isfinite(inc4_path).all() else None

    # Bandas de ingresos: proporcionales a las de activos (v1: incertidumbre del stock;
    # la del ratio se documenta como limitación)
    ratio_band = np.divide(rev_fc.values, a_path * rot_path,
                           out=np.ones(steps), where=(a_path * rot_path) != 0)

    return {
        "rut": rut, "company": panel.names.get(rut, rut),
        "periods": fc_periods,
        "ingresos": rev_fc,
        "ingresos_lo80": pd.Series(a_lo * rot_path * ratio_band, index=fc_periods),
        "ingresos_hi80": pd.Series(a_hi * rot_path * ratio_band, index=fc_periods),
        "ganancia": inc_fc,
        "activos": pd.Series(a_path, index=fc_periods),
        "activos_hist": a, "rotacion_hist": rot, "margen_hist": mgn,
        "rotacion_path": pd.Series(rot_path, index=fc_periods),
        "margen_path": pd.Series(mgn_path, index=fc_periods),
        "beta": dict(zip(FACTORS, np.round(beta, 4))),
        "alpha": round(alpha, 4), "sigma_dlog": round(sigma, 4),
    }
