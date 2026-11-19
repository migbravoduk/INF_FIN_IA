"""
Backtest rolling-origin de las proyecciones SARIMAX de bancos y seguros
(`models/simple_forecast.py`, vistas /proyecciones/bancos y /seguros).

Mismo esquema de salida que `models/backtest.py`, más `clase`. Specs:
  sarimax  el de producción (`forecast_series`).
  snaive   flujos: valor del mismo período del año anterior; stocks: último valor.
  uf       (solo flujos de bancos y seguros de vida, --uf) resultado = a + k·π_UF (MCO con
           intercepto, 36 meses / 12 trimestres hasta el origen); se proyecta resultado − k·π
           con el SARIMAX de producción y se suma k·π de la senda de inflación vintage.

Uso:
  python -m models.backtest_simple --clase bancos --out scratch/bt_bancos.csv [--uf]
  python -m models.backtest_simple --clase seguros --out scratch/bt_seguros.csv [--uf]
  python -m models.backtest_simple --bandas scratch/bt_bancos.csv scratch/bt_seguros.csv
"""

from __future__ import annotations

import argparse
import logging
import time

import numpy as np
import pandas as pd

from models.simple_forecast import (cargar_serie, escala_mase, forecast_series, forecast_series_uf,
                                    inflacion_uf_periodos, next_periods)

# Spec de producción por (clase, partida); el resto usa "sarimax". Las bandas salen de este spec.
SPEC_PRODUCCION = {("bancos", "Resultado de los propietarios"): "uf"}

logger = logging.getLogger(__name__)

CONF = {
    "bancos": {"monthly": True, "origins": 18, "horizons": [1, 3, 6, 12], "lag": 1},
    "seguros": {"monthly": False, "origins": 8, "horizons": [1, 2, 3, 4], "lag": 2},
}


def _cfg_simple():
    from api.routers.web._shared import _PROY_SIMPLE
    return _PROY_SIMPLE


def entidades(db, clase: str) -> list[tuple[str, str, str | None]]:
    if clase == "bancos":
        return [(str(r["bank_code"]), r["bank_name"], None) for _, r in db.get_bank_list().iterrows()]
    return [(r["rut"], r["company_name"], r["insurance_type"]) for _, r in db.get_insurer_list().iterrows()]


def _as_of(period: int, monthly: bool, lag_months: int) -> pd.Timestamp:
    y, m = period // 100, period % 100
    return pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) + pd.DateOffset(months=lag_months)


class _Inflacion:
    """π_UF por período (mensual o trimestral) con senda vintage, cacheada por origen."""

    def __init__(self, db):
        self.db = db
        self._c: dict = {}

    def get(self, origin: int, monthly: bool, lag_months: int) -> pd.Series:
        key = (origin, monthly)
        if key not in self._c:
            self._c[key] = inflacion_uf_periodos(self.db, monthly, 30,
                                                 as_of=_as_of(origin, monthly, lag_months))
        return self._c[key]


def backtest_clase(db, clase: str, uf: bool = False, limite: int | None = None) -> list[dict]:
    c = CONF[clase]
    monthly, hmax = c["monthly"], max(c["horizons"])
    cfg = _cfg_simple()[clase]
    infl = _Inflacion(db) if uf else None
    rows = []
    ents = entidades(db, clase)[:limite] if limite else entidades(db, clase)
    t0 = time.time()
    for n, (eid, nombre, kind) in enumerate(ents, 1):
        for acc in cfg["accounts"]:
            s = cargar_serie(db, clase, eid, acc, kind)
            if s is None:
                continue
            s = s.dropna().sort_index()
            s.index = s.index.astype(int)
            if len(s) < 16 + c["origins"] + hmax:
                continue
            first = len(s) - hmax - c["origins"]
            scale = escala_mase(s.iloc[:first + 1], monthly)
            if not np.isfinite(scale) or scale <= 0:
                continue
            for i in range(first, len(s) - hmax):
                train = s.iloc[:i + 1]
                origin = int(s.index[i])
                targets = next_periods(origin, hmax, monthly)
                preds = {}
                for sfx, lim, aco in (("", False, False), ("_acot", False, True),
                                      ("_lim", True, False), ("_lim_acot", True, True)):
                    if lim and acc["stock"]:
                        continue   # en stocks la limpieza no aplica (forecast_series)
                    fc = forecast_series(train, hmax, monthly=monthly, is_stock=acc["stock"],
                                         limpiar=lim, acotar=aco)
                    if fc is not None:
                        preds["sarimax" + sfx] = fc["mean"].values
                if acc["stock"]:
                    preds["snaive"] = np.full(hmax, float(train.iloc[-1]))
                else:
                    preds["snaive"] = np.array([_snaive(train, p) for p in targets])
                if uf and not acc["stock"] and (clase == "bancos" or kind == "vida"):
                    pi = infl.get(origin, monthly, c["lag"])
                    for sfx, lim, aco in (("", False, False), ("_acot", False, True),
                                          ("_lim", True, False), ("_lim_acot", True, True)):
                        fu = forecast_series_uf(train, hmax, monthly, pi, limpiar=lim, acotar=aco)
                        if fu is not None:
                            preds["uf" + sfx] = fu["mean"].values
                for h in c["horizons"]:
                    p = targets[h - 1]
                    actual = s.get(p, np.nan)
                    if not np.isfinite(actual):
                        continue
                    for spec, arr in preds.items():
                        f = float(arr[h - 1])
                        if not np.isfinite(f):
                            continue
                        rows.append({"clase": clase, "rut": eid, "company": nombre,
                                     "account": acc["label"], "origin": str(origin), "h": h,
                                     "target": str(p), "spec": spec, "actual": float(actual),
                                     "forecast": f, "abs_err": abs(actual - f),
                                     "mase": abs(actual - f) / scale, "scale": scale})
        print(f"[{n}/{len(ents)}] {str(nombre)[:45]:<45} filas={len(rows)} t={time.time()-t0:.0f}s",
              flush=True)
    return rows


def _snaive(train: pd.Series, p: int) -> float:
    """Mismo período del año anterior (o dos años atrás si aún no se observa)."""
    for k in (1, 2):
        v = train.get(p - 100 * k, np.nan)
        if np.isfinite(v):
            return float(v)
    return np.nan


def bandas(paths: list[str]) -> dict:
    df = pd.concat([pd.read_csv(p, dtype={"rut": str, "origin": str}) for p in paths])
    prod = [SPEC_PRODUCCION.get((c, a), "sarimax") for c, a in zip(df["clase"], df["account"])]
    df = df[df["spec"] == pd.Series(prod, index=df.index)]
    q = df.groupby(["clase", "account", "h"])["mase"].quantile([0.80, 0.95]).unstack()
    return {(c, a, int(h)): (round(float(r[0.80]), 3), round(float(r[0.95]), 3))
            for (c, a, h), r in q.iterrows()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--clase", choices=list(CONF))
    ap.add_argument("--out", default=None)
    ap.add_argument("--uf", action="store_true")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--bandas", nargs="+", default=None)
    a = ap.parse_args()
    if a.bandas:
        for k, v in bandas(a.bandas).items():
            print(f"    {k!r}: {v},")
        return
    from db.database import Database
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("models.simple_forecast").setLevel(logging.CRITICAL)
    db = Database(read_only=True)
    df = pd.DataFrame(backtest_clase(db, a.clase, uf=a.uf, limite=a.limite))
    if df.empty:
        print("Sin resultados.")
        return
    df.to_csv(a.out or f"scratch/bt_{a.clase}.csv", index=False)
    s = df.groupby(["account", "spec", "h"])["mase"].agg(["size", "median", "mean"]).round(3)
    print(s.to_string())


if __name__ == "__main__":
    main()
