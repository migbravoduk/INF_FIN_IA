"""
Backtest fuera de muestra de las proyecciones macrofundadas (rolling origin).

Para cada (empresa, partida) y cada origen T (trimestre de corte):
  - se ajusta el modelo SOLO con datos hasta T,
  - se proyectan h = 1..H trimestres,
  - las exógenas futuras usan el VINTAGE de la EEE: la senda tal como se veía
    ~2 meses después del cierre de T (cuando el EEFF de T ya estaba publicado
    y el analista contaba con la encuesta de ese mes). Sin fuga de información.

Especificaciones comparadas:
  eee     — SARIMAX(1,0,0)x(0,1,1,4) + exógenas macro (senda EEE vintage)
  noexog  — mismo SARIMAX sin exógenas (¿aporta el macro?)
  snaive  — naive estacional: y[T+h] = y[T+h-4] (benchmark mínimo)

Métricas (por partida, spec y horizonte):
  MASE  — |error| / escala, con escala = media|y_t - y_{t-4}| del tramo de
          entrenamiento del primer origen. <1 = mejor que el naive estacional
          in-sample. Escala-libre: agregable entre empresas y monedas.
  sMAPE — solo informativa para ingresos (el resultado neto cruza cero).

Uso:
  python -m models.backtest --companies 40 --origins 8 --horizon 4 \
      --db data/finanzas_chile.duckdb --out scratch/backtest_results.csv
"""

from __future__ import annotations

import argparse
import logging
import time
import warnings

import numpy as np
import pandas as pd

from models.forecast import (FORECASTABLE_ACCOUNTS, EXOG_VARS, MIN_OBS, ORDER,
                             SEASONAL_ORDER, get_quarterly_deaccumulated)
from models.macro_path import build_macro_path

logger = logging.getLogger(__name__)

PUBLICATION_LAG_MONTHS = 2  # el EEFF del trimestre T se conoce ~2 meses después


def select_companies(db, n: int, origins: int, horizon: int) -> list[tuple[str, str]]:
    """Empresas con historia suficiente de ingresos ERFG, por materialidad (mediana |ingresos|)."""
    need = MIN_OBS + origins + horizon
    df = db.conn.execute("""
        SELECT rut, ANY_VALUE(company_name) AS nombre,
               COUNT(DISTINCT period) AS np,
               MEDIAN(ABS(value)) AS med
        FROM cmf_financial_statements
        WHERE account_name = 'Ingresos de actividades ordinarias'
          AND statement_group = 'ERFG'
        GROUP BY rut
        HAVING COUNT(DISTINCT period) >= ?
        ORDER BY med DESC
        LIMIT ?
    """, [need, n]).fetchdf()
    return list(df[["rut", "nombre"]].itertuples(index=False, name=None))


def _quarter_end(period: str) -> pd.Timestamp:
    y, m = int(period[:4]), int(period[4:])
    return pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)


def _fit_and_forecast(endog_s, exog_hist, exog_fc, steps):
    """SARIMAX con la misma especificación de producción; exógenas opcionales."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            endog_s, exog=exog_hist,
            order=ORDER, seasonal_order=SEASONAL_ORDER,
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, maxiter=200)
        return fitted.get_forecast(steps=steps, exog=exog_fc).predicted_mean


def backtest_series(db, rut: str, nombre: str, account: str,
                    origins: int = 8, horizon: int = 4) -> list[dict]:
    """Backtest rolling-origin de una (empresa, partida). Devuelve filas de resultados."""
    endog = get_quarterly_deaccumulated(db, rut, account)
    if endog.dropna().shape[0] < MIN_OBS + origins + horizon:
        return []

    n = len(endog)
    first_origin = n - horizon - origins  # posición (0-based) del primer origen
    # Escala MASE: naive estacional in-sample sobre el tramo común de entrenamiento
    base = endog.iloc[:first_origin + 1]
    scale = float(np.nanmean(np.abs(base.values[4:] - base.values[:-4])))
    if not np.isfinite(scale) or scale <= 0:
        return []

    rows = []
    for i in range(first_origin, n - horizon):
        train = endog.iloc[:i + 1]
        if train.dropna().shape[0] < MIN_OBS:
            continue
        origin_p = endog.index[i]
        as_of = _quarter_end(origin_p) + pd.DateOffset(months=PUBLICATION_LAG_MONTHS)

        # Senda macro vintage (historia truncada + anclajes EEE al as_of)
        mp = build_macro_path(db, horizon_months=3 * horizon + 9, as_of=as_of)
        full_q = mp.full().resample("QE").mean()
        full_q.index = [f"{d.year}{d.month:02d}" for d in full_q.index]
        full_q = full_q[EXOG_VARS].ffill().bfill()

        targets = list(endog.index[i + 1:i + 1 + horizon])
        if any(p not in full_q.index for p in list(train.index) + targets):
            continue
        exog_hist = full_q.loc[train.index]
        exog_fc = full_q.loc[targets]
        mu, sd = exog_hist.mean(), exog_hist.std().replace(0.0, 1.0)
        ehz, efz = (exog_hist - mu) / sd, (exog_fc - mu) / sd

        endog_scale = max(float(np.nanstd(train.values)), 1.0)
        endog_s = train.values / endog_scale

        preds = {}
        try:
            preds["eee"] = _fit_and_forecast(endog_s, ehz.values, efz.values, horizon) * endog_scale
        except Exception:
            logger.exception("eee falló %s/%s @%s", rut, account, origin_p)
        try:
            preds["noexog"] = _fit_and_forecast(endog_s, None, None, horizon) * endog_scale
        except Exception:
            logger.exception("noexog falló %s/%s @%s", rut, account, origin_p)
        # Naive estacional: y[T+h] = y[T+h-4] (para h<=4 siempre cae dentro del train)
        preds["snaive"] = np.array([endog.iloc[i + 1 + h - 4] for h in range(horizon)])

        for h in range(horizon):
            target_p = targets[h]
            actual = endog.get(target_p, np.nan)
            if not np.isfinite(actual):
                continue
            for spec, arr in preds.items():
                fc = float(arr[h])
                if not np.isfinite(fc):
                    continue
                rows.append({
                    "rut": rut, "company": nombre, "account": account,
                    "origin": origin_p, "h": h + 1, "target": target_p,
                    "spec": spec, "actual": actual, "forecast": fc,
                    "abs_err": abs(actual - fc), "mase": abs(actual - fc) / scale,
                })
    return rows


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """MASE medio y mediano por (partida, spec, horizonte); sMAPE solo para ingresos."""
    g = df.groupby(["account", "spec", "h"])
    out = g.agg(n=("mase", "size"), mase_mean=("mase", "mean"),
                mase_med=("mase", "median")).reset_index()
    rev = df[df["account"] == "Ingresos de actividades ordinarias"].copy()
    if not rev.empty:
        rev["smape"] = 200 * rev["abs_err"] / (rev["actual"].abs() + rev["forecast"].abs())
        sm = rev.groupby(["account", "spec", "h"])["smape"].median().rename("smape_med")
        out = out.merge(sm.reset_index(), on=["account", "spec", "h"], how="left")
    return out.round(3)


def main():
    ap = argparse.ArgumentParser(description="Backtest de proyecciones macrofundadas")
    ap.add_argument("--db", default=None, help="Ruta a la BD DuckDB (default settings)")
    ap.add_argument("--companies", type=int, default=40)
    ap.add_argument("--origins", type=int, default=8)
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--out", default="scratch/backtest_results.csv")
    args = ap.parse_args()

    from db.database import Database
    logging.basicConfig(level=logging.WARNING)
    db = Database(db_path=args.db, read_only=True)

    companies = select_companies(db, args.companies, args.origins, args.horizon)
    print(f"Backtest: {len(companies)} empresas x {len(FORECASTABLE_ACCOUNTS)} partidas "
          f"x {args.origins} orígenes x h1..h{args.horizon}")

    t0, all_rows = time.time(), []
    for k, (rut, nombre) in enumerate(companies, 1):
        for account in FORECASTABLE_ACCOUNTS:
            all_rows.extend(backtest_series(db, rut, nombre, account,
                                            args.origins, args.horizon))
        print(f"[{k}/{len(companies)}] {nombre[:50]:<50} filas acumuladas={len(all_rows)} "
              f"t={time.time()-t0:.0f}s", flush=True)

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("Sin resultados.")
        return
    df.to_csv(args.out, index=False)
    print(f"\nResultados detallados: {args.out} ({len(df)} filas)")
    print("\n=== RESUMEN (MASE: <1 = mejor que naive estacional) ===")
    print(summarize(df).to_string(index=False))


if __name__ == "__main__":
    main()
