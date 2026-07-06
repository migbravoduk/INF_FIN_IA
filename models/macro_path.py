"""
Senda macro esperada a partir de la Encuesta de Expectativas Económicas (EEE, BCCh).

Construye, para las variables core (inflación, TPM, tipo de cambio, actividad):
  - la HISTORIA mensual desde `observations` (datos efectivos), y
  - la SENDA FUTURA mensual interpolando los anclajes de horizonte móvil de la EEE
    (en el mes / 2m / 5m / 11m / 17m / 23m / largo plazo, medianas de la encuesta).

Ambas comparten definición de variable, de modo que puedan usarse como regresores
exógenos consistentes (historia para ajustar, futuro para proyectar) en los modelos
SARIMAX de `models/forecast.py`.

Variables producidas (mensuales):
  ipc_yoy  — inflación 12 meses (%)
  tpm      — tasa de política monetaria (%, promedio del mes)
  tc       — tipo de cambio CLP/USD (promedio del mes)
  act_yoy  — actividad: variación 12m del IMACEC (historia) / expectativa PIB (futuro)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --- Series efectivas (historia) ---
HIST_SERIES = {
    "ipc_yoy": "G073.IPC.V12.2023.M",        # inflación 12m efectiva
    "tpm": "F022.TPM.TIN.D001.NO.Z.D",       # TPM diaria -> promedio mensual
    "tc": "F073.TCO.PRE.HIST.M",             # TC promedio mensual
    "act_idx": "F032.ICF.IND.Z.Z.EP18.Z.Z.0.M",  # IMACEC índice -> var 12m
}

# --- Anclajes de encuestas: variable -> [(horizonte_en_meses, series_id)] ---
# Doble anclaje oficial del BCCh: EEE (economistas, mensual) + EOF (operadores
# financieros / mercado, quincenal). NO se proyecta ningún factor macro propio:
# la senda solo interpola linealmente entre las medianas publicadas. El horizonte
# es relativo al mes de la encuesta; "LP" (largo plazo) se ancla a 36m.
# (F089.EOF.TC.7MA está descontinuada desde 2018 — no usar.)
LP_MONTH = 36
EEE_ANCHORS = {
    "ipc_yoy": [(11, "F089.IPC.V12.14.M"), (23, "F089.IPC.V12.15.M"),
                (LP_MONTH, "F089.IPC.V12.LP.M"),
                (12, "F089.EOF.VII.12MS.D"), (24, "F089.EOF.VII.S12M.D")],
    "tpm": [(0, "F089.TPM.TAS.11.M"), (5, "F089.TPM.TAS.26.M"),
            (11, "F089.TPM.TAS.14.M"), (17, "F089.TPM.TAS.30.M"),
            (23, "F089.TPM.TAS.15.M"), (LP_MONTH, "F089.TPM.TAS.LP.M"),
            (1, "F089.EOF.TPM.MA.D"), (3, "F089.EOF.TPM.3MS.D"),
            (6, "F089.EOF.TPM.6MS.D"), (12, "F089.EOF.TPM.12MS.D"),
            (24, "F089.EOF.TPM.24MS.D")],
    # OJO: F089.TCN.V12.LP.M NO es un nivel sino la variación 12m de largo plazo (%);
    # se usa como pendiente de extensión más allá del anclaje de 23 meses.
    "tc": [(2, "F089.TCN.PRE.13.M"), (11, "F089.TCN.PRE.14.M"),
           (23, "F089.TCN.PRE.15.M"),
           (1, "F089.EOF.TC.28DA.D")],
    "act_yoy": [(0, "F089.PIB.V12.33.M"), (LP_MONTH, "F089.PIB.V12.LP.M")],
}
TC_LP_VAR_SERIES = "F089.TCN.V12.LP.M"  # % anual, pendiente del TC tras 23m


@dataclass
class MacroPath:
    """Historia + senda futura mensual de las variables macro core."""
    history: pd.DataFrame          # index mensual (fin de mes implícito: 1° de mes), cols ipc_yoy/tpm/tc/act_yoy
    future: pd.DataFrame           # idem, meses posteriores a la encuesta
    survey_month: pd.Timestamp     # mes de la encuesta EEE usada
    anchors: dict = field(default_factory=dict)  # {variable: [(mes, valor)]} para trazabilidad

    def full(self) -> pd.DataFrame:
        return pd.concat([self.history, self.future])

    def quarterly(self, which: str = "full") -> pd.DataFrame:
        """Agrega a trimestres calendario (promedio del trimestre)."""
        df = {"history": self.history, "future": self.future}.get(which, self.full())
        q = df.resample("QE").mean()
        # Índice como período de cierre trimestral YYYYMM (marzo=03, junio=06, ...)
        q.index = [f"{d.year}{d.month:02d}" for d in q.index]
        return q


def _monthly_series(db, series_id: str) -> pd.Series:
    df = db.conn.execute(
        "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date",
        [series_id],
    ).fetchdf()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["value"].astype(float)
    # Series diarias -> promedio mensual; mensuales quedan igual
    s = s.resample("MS").mean()
    return s


def build_history(db) -> pd.DataFrame:
    """Historia mensual efectiva de las 4 variables core."""
    ipc = _monthly_series(db, HIST_SERIES["ipc_yoy"])
    tpm = _monthly_series(db, HIST_SERIES["tpm"])
    tc = _monthly_series(db, HIST_SERIES["tc"])
    act_idx = _monthly_series(db, HIST_SERIES["act_idx"])
    act_yoy = act_idx.pct_change(12) * 100.0

    hist = pd.DataFrame({"ipc_yoy": ipc, "tpm": tpm, "tc": tc, "act_yoy": act_yoy})
    hist = hist.dropna(how="all")
    return hist


def build_future(db, horizon_months: int = 24,
                 as_of: pd.Timestamp | str | None = None) -> tuple[pd.DataFrame, pd.Timestamp, dict]:
    """
    Senda futura mensual interpolando los anclajes EEE de la última encuesta.
    `as_of` (vintage): usar solo observaciones disponibles hasta esa fecha — permite
    reconstruir la senda "tal como se veía" en un mes pasado (backtesting honesto).
    Devuelve (df_future, mes_encuesta, anclajes_usados).
    """
    conn = db.conn
    cutoff = pd.Timestamp(as_of).date().isoformat() if as_of is not None else None

    def latest(series_id: str):
        if cutoff is None:
            row = conn.execute(
                "SELECT date, value FROM observations WHERE series_id = ? "
                "ORDER BY date DESC LIMIT 1", [series_id]
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT date, value FROM observations WHERE series_id = ? AND date <= ? "
                "ORDER BY date DESC LIMIT 1", [series_id, cutoff]
            ).fetchone()
        return (pd.Timestamp(row[0]), float(row[1])) if row else (None, None)

    # Mes de la encuesta vigente: el más reciente entre EEE y EOF (la EOF es
    # quincenal, así que entre publicaciones de la EEE suele ser la más fresca).
    # Normalizado a inicio de mes para que la malla mensual quede alineada.
    survey_month = max(
        d for d, _ in (latest("F089.TPM.TAS.11.M"), latest("F089.IPC.V12.14.M"),
                       latest("F089.EOF.TPM.MA.D"))
        if d is not None
    ).to_period("M").to_timestamp()

    idx = pd.date_range(survey_month + pd.DateOffset(months=1),
                        periods=horizon_months, freq="MS")
    future = pd.DataFrame(index=idx, columns=list(EEE_ANCHORS), dtype=float)
    used_anchors: dict[str, list] = {}

    for var, anchor_defs in EEE_ANCHORS.items():
        pts = []  # (mes_offset, valor)
        for offset, sid in anchor_defs:
            d, v = latest(sid)
            if v is None:
                continue
            # Solo usar anclajes de la encuesta vigente (o del mes anterior para LP,
            # que a veces se publica con rezago de un mes).
            if d is not None and (survey_month - d).days > 62:
                logger.warning("Anclaje %s desactualizado (última obs %s); se omite", sid, d.date())
                continue
            pts.append((offset, v))
        if not pts:
            logger.warning("Sin anclajes vigentes para %s; senda vacía", var)
            continue
        # Ordenar por horizonte (EEE y EOF se intercalan) y promediar si dos
        # encuestas anclan exactamente el mismo mes.
        agg: dict[int, list[float]] = {}
        for off, v in pts:
            agg.setdefault(off, []).append(v)
        pts = sorted((off, float(np.mean(vs))) for off, vs in agg.items())
        used_anchors[var] = pts

        offsets = np.array([p[0] for p in pts], dtype=float)
        values = np.array([p[1] for p in pts], dtype=float)
        months_ahead = np.arange(1, horizon_months + 1, dtype=float)
        # Interpolación lineal entre anclajes; extremos planos (np.interp satura)
        future[var] = np.interp(months_ahead, offsets, values)

    # TC más allá del último anclaje (23m): crecer a la variación LP anual
    _, tc_lp_var = latest(TC_LP_VAR_SERIES)
    if tc_lp_var is not None and "tc" in used_anchors:
        last_off = max(o for o, _ in used_anchors["tc"])
        last_val = dict(used_anchors["tc"])[last_off]
        monthly_rate = (1.0 + tc_lp_var / 100.0) ** (1.0 / 12.0) - 1.0
        for i, m in enumerate(np.arange(1, horizon_months + 1)):
            if m > last_off:
                future.iloc[i, future.columns.get_loc("tc")] = (
                    last_val * (1.0 + monthly_rate) ** (m - last_off)
                )
        used_anchors["tc_lp_var_anual_pct"] = [(LP_MONTH, tc_lp_var)]

    return future, survey_month, used_anchors


def build_macro_path(db, horizon_months: int = 24,
                     as_of: pd.Timestamp | str | None = None) -> MacroPath:
    """
    Construye historia + senda futura consistentes para los modelos.
    Con `as_of`, tanto la historia como los anclajes EEE se truncan a esa fecha
    (vintage: la foto que un analista habría tenido en ese momento).
    """
    history = build_history(db)
    if as_of is not None:
        history = history[history.index <= pd.Timestamp(as_of)]
    future, survey_month, anchors = build_future(db, horizon_months, as_of=as_of)
    # La historia no debe traslapar la senda futura
    history = history[history.index < future.index.min()]
    return MacroPath(history=history, future=future,
                     survey_month=survey_month, anchors=anchors)
