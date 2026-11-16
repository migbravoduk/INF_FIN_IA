"""
db/curves.py — Curvas de tasas de Chile ajustadas a diario (Nelson-Siegel / Svensson).

Insumos (Banco Central, BDE, series diarias en `observations`):

  swap_clp   nominal en pesos: TIB 1 día, swap promedio cámara 90/180/360 días y 2/3/4/5/10 años
  swap_uf    real en UF:       swap promedio cámara UF 1/2/3/4/5/10/20 años
  bono_clp   soberana pesos:   TIB 1 día (ancla corta) + bonos BCP/BTP 2/5/10 años
  bono_uf    soberana UF:      bonos BCU/BTU 2/5/10/20/30 años

Se guarda SOLO los parámetros (`curva_ajuste`, una fila por fecha y curva); los puntos,
forwards, compensación inflacionaria, TPM implícita y spread bancario se calculan al vuelo.

Convenciones y aproximaciones declaradas (ver /metodologia):
- Tasas ≤ 1 año (TIB, cámara 90/180/360, depósitos) se tratan como simples base 360 → se
  convierten a tasa continua equivalente. Tasas ≥ 1 año (swaps, TIR de bonos) se tratan como
  efectivas anuales base 365.
- Las tasas de swaps ≥ 2 años y las TIR de bonos son tasas PAR (con cupón), no cero cupón. Se
  ajustan como si fueran cero cupón: con curvas de pendiente moderada la diferencia es de pocos
  puntos base. Validación: la compensación inflacionaria que sale de nuestras curvas swap se
  compara con la que publica el BCCh (F022.SWSP), y la diferencia se muestra en la página.
- Modelo: Nelson-Siegel-Svensson con ≥ 6 puntos; Nelson-Siegel con 4–5; sin ajuste con < 4.
  Los τ se eligen por grilla y los β por mínimos cuadrados (con una cresta mínima para que β
  no se disparen cuando dos factores son casi colineales).
- La curva no se extrapola: se dibuja solo entre el plazo mínimo y máximo observado ese día.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TIB = "F022.TIB.TIP.D001.NO.Z.D"

# (serie, plazo en días si es tasa simple base 360 | plazo en años si es efectiva, convención)
CURVAS: dict[str, dict] = {
    "swap_clp": {
        "titulo": "Swap cámara en pesos (nominal)", "moneda": "CLP", "tipo": "nominal",
        "puntos": [(TIB, 1, "s360"),
                   ("F022.SPC.TPR.D090.NO.Z.D", 90, "s360"),
                   ("F022.SPC.TPR.D180.NO.Z.D", 180, "s360"),
                   ("F022.SPC.TPR.D360.NO.Z.D", 360, "s360"),
                   ("F022.SPC.TIN.AN02.NO.Z.D", 2, "a360"),
                   ("F022.SPC.TIN.AN03.NO.Z.D", 3, "a360"),
                   ("F022.SPC.TIN.AN04.NO.Z.D", 4, "a360"),
                   ("F022.SPC.TIN.AN05.NO.Z.D", 5, "a360"),
                   ("F022.SPC.TIN.AN10.NO.Z.D", 10, "a360")],
    },
    "swap_uf": {
        "titulo": "Swap cámara en UF (real)", "moneda": "UF", "tipo": "real",
        "puntos": [("F022.SPC.TIN.AN01.UF.Z.D", 1, "a360"),
                   ("F022.SPC.TIN.AN02.UF.Z.D", 2, "a360"),
                   ("F022.SPC.TIN.AN03.UF.Z.D", 3, "a360"),
                   ("F022.SPC.TIN.AN04.UF.Z.D", 4, "a360"),
                   ("F022.SPC.TIN.AN05.UF.Z.D", 5, "a360"),
                   ("F022.SPC.TIN.AN10.UF.Z.D", 10, "a360"),
                   ("F022.SPC.TIN.AN20.UF.Z.D", 20, "a360")],
    },
    "bono_clp": {
        "titulo": "Bonos soberanos en pesos (BCP/BTP)", "moneda": "CLP", "tipo": "nominal",
        "puntos": [(TIB, 1, "s360"),
                   ("F022.BCLP.TIS.AN02.NO.Z.D", 2, "efe"),
                   ("F022.BCLP.TIS.AN05.NO.Z.D", 5, "efe"),
                   ("F022.BCLP.TIS.AN10.NO.Z.D", 10, "efe")],
    },
    "bono_uf": {
        "titulo": "Bonos soberanos en UF (BCU/BTU)", "moneda": "UF", "tipo": "real",
        "puntos": [("F022.BUF.TIS.AN02.UF.Z.D", 2, "efe"),
                   ("F022.BUF.TIS.AN05.UF.Z.D", 5, "efe"),
                   ("F022.BUF.TIS.AN10.UF.Z.D", 10, "efe"),
                   ("F022.BUF.TIS.AN20.UF.Z.D", 20, "efe"),
                   ("F022.BUF.TIS.AN30.UF.Z.D", 30, "efe")],
    },
}

# Compensación inflacionaria que publica el BCCh con swaps: sirve para validar nuestras curvas.
BEI_BCCH = {1: "F022.SWSP.TAS.AN01.Z.Z.D", 2: "F022.SWSP.TAS.AN02.Z.Z.D",
            5: "F022.SWSP.TAS.AN05.Z.Z.D", 10: "F022.SWSP.TAS.AN10.Z.Z.D"}
BEI_FWD_BCCH = {"1 en 1": "F022.SWFW.TAS.E1X1.Z.Z.D", "3 en 2": "F022.SWFW.TAS.E3X2.Z.Z.D",
                "5 en 5": "F022.SWFW.TAS.E5X5.Z.Z.D"}

# Depósitos bancarios transados en bolsa (pesos): spread de crédito bancario sobre swap.
DEPOSITOS = [("F022.TDB.TIS.D030.NO.Z.D", 30), ("F022.TDB.TIS.D090.NO.Z.D", 90),
             ("F022.TDB.TIS.D180.NO.Z.D", 180), ("F022.TDB.TIS.AN01.NO.Z.D", 360)]

# Expectativas de TPM: EOF (quincenal, horizonte en meses) y EEE (mensual).
EXPECTATIVAS_TPM = [
    ("EOF", "F089.EOF.TPM.3MS.D", 3), ("EOF", "F089.EOF.TPM.6MS.D", 6),
    ("EOF", "F089.EOF.TPM.12MS.D", 12), ("EOF", "F089.EOF.TPM.24MS.D", 24),
    ("EEE", "F089.TPM.TAS.13.M", 2), ("EEE", "F089.TPM.TAS.26.M", 5),
    ("EEE", "F089.TPM.TAS.14.M", 11), ("EEE", "F089.TPM.TAS.30.M", 17),
    ("EEE", "F089.TPM.TAS.15.M", 23),
]
TPM = "F022.TPM.TIN.D001.NO.Z.D"

DESDE = "2005-01-01"
TAU1 = np.geomspace(0.25, 8.0, 24)
TAU2 = np.geomspace(1.0, 25.0, 18)
RIDGE = 1e-6


# ============================================================
# Matemática del modelo (tasas continuas, en decimales)
# ============================================================

def _l(t, tau):
    x = np.asarray(t, dtype=float) / tau
    return np.where(x < 1e-8, 1.0, (1 - np.exp(-x)) / np.where(x < 1e-8, 1.0, x))


def _basis(t, tau1, tau2=None):
    t = np.asarray(t, dtype=float)
    l1, e1 = _l(t, tau1), np.exp(-t / tau1)
    cols = [np.ones_like(t), l1, l1 - e1]
    if tau2 is not None:
        cols.append(_l(t, tau2) - np.exp(-t / tau2))
    return np.column_stack(cols)


def zero_rate(p: dict, t) -> np.ndarray:
    """Tasa cero continua (decimal) a plazo t (años)."""
    t = np.maximum(np.asarray(t, dtype=float), 1e-6)
    tau2 = p.get("tau2") if p.get("b3") is not None and not _nan(p.get("b3")) else None
    X = _basis(t, p["tau1"], tau2)
    b = [p["b0"], p["b1"], p["b2"]] + ([p["b3"]] if tau2 is not None else [])
    return X @ np.array(b)


def forward_rate(p: dict, t) -> np.ndarray:
    """Forward instantánea continua (decimal) a plazo t."""
    t = np.maximum(np.asarray(t, dtype=float), 1e-6)
    e1 = np.exp(-t / p["tau1"])
    f = p["b0"] + p["b1"] * e1 + p["b2"] * (t / p["tau1"]) * e1
    if p.get("b3") is not None and not _nan(p.get("b3")):
        e2 = np.exp(-t / p["tau2"])
        f = f + p["b3"] * (t / p["tau2"]) * e2
    return f


def _z(p: dict, t: float) -> float:
    """Tasa cero en % efectivo anual a un plazo escalar."""
    return float(efectiva(zero_rate(p, [t])[0]))


def _f(x) -> float | None:
    """Número JSON-seguro: NaN/None → None."""
    return None if x is None or (isinstance(x, (float, np.floating)) and math.isnan(x)) else float(x)


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def efectiva(c):
    """Continua (decimal) → efectiva anual (%)."""
    return (np.exp(np.asarray(c, dtype=float)) - 1) * 100


def overnight_360(c):
    """Continua (decimal) → tasa overnight simple base 360 (%), comparable con la TPM."""
    return (np.exp(np.asarray(c, dtype=float) / 365) - 1) * 360 * 100


def _a_continua(valor_pct, plazo, conv):
    """Tasa observada (%) → (plazo en años, tasa continua decimal)."""
    r = np.asarray(valor_pct, dtype=float) / 100
    if conv == "s360":
        t = plazo / 365
        return t, np.log1p(r * plazo / 360) / t
    if conv == "a360":
        # Swaps cámara: tasa fija base actual/360 → equivalente base 365. Medido (2015–2026):
        # con esta conversión la compensación inflacionaria a 5 y 10 años queda a ~2 pb de la
        # del BCCh; tratándolas como base 365, a ~4–5 pb.
        return float(plazo), np.log1p(r * 365 / 360)
    return float(plazo), np.log1p(r)


# ============================================================
# Ajuste
# ============================================================

def _insumos(db, series: list[str], desde: str = DESDE) -> pd.DataFrame:
    ids = ", ".join(f"'{s}'" for s in sorted(set(series)))
    df = db.conn.execute(f"""
        SELECT date, series_id, value FROM observations
        WHERE series_id IN ({ids}) AND date >= '{desde}' AND value IS NOT NULL
    """).fetchdf()
    if df.empty:
        return df
    w = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last")
    w.index = pd.to_datetime(w.index)
    # Los bonos largos no transan todos los días: cada punto vale hasta 5 días hábiles.
    return w.sort_index().ffill(limit=5)


def _ajustar_curva(nombre: str, spec: dict, w: pd.DataFrame) -> pd.DataFrame:
    """Ajusta una curva para todas las fechas. Devuelve un DataFrame de parámetros."""
    pts = [p for p in spec["puntos"] if p[0] in w.columns]
    if len(pts) < 3:
        return pd.DataFrame()
    # El ancla corta (TIB) no define por sí sola un día: se exige al menos 3 puntos de mercado
    # además de ella, para no "ajustar" días en que solo se publicó la tasa interbancaria.
    plazos, Y = [], []
    for sid, plazo, conv in pts:
        t, y = _a_continua(w[sid].values, plazo, conv)
        plazos.append(t)
        Y.append(y)
    t = np.array(plazos)
    Y = np.column_stack(Y)                      # fechas × puntos
    mask = ~np.isnan(Y)
    no_ancla = np.array([sid != TIB for sid, _, _ in pts])
    ok = (mask & no_ancla).sum(1) >= 3

    out = []
    patrones = pd.Series([m.tobytes() for m in mask])
    grupos = sorted(patrones[ok].groupby(patrones[ok]).groups.values(),
                    key=lambda ix: -int(mask[ix[0]].sum()))
    taus_vistos: list[float] = []
    for idx in grupos:
        idx = np.asarray(list(idx))
        m = mask[idx[0]]
        tt, yy = t[m], Y[idx][:, m].T            # puntos × días
        nss = m.sum() >= 6
        # Con 3 puntos Nelson-Siegel queda exactamente identificado para cualquier τ: se fija τ
        # en la mediana de los días con más puntos de la misma curva (p. ej. bonos UF cuando
        # el de 20 y 30 años no transan).
        exacto = m.sum() == 3
        grilla = [float(np.median(taus_vistos))] if exacto and taus_vistos else TAU1
        best_sse = np.full(len(idx), np.inf)
        best = np.full((len(idx), 6), np.nan)    # b0..b3, tau1, tau2
        for tau1 in grilla:
            for tau2 in (TAU2 if nss else [None]):
                if tau2 is not None and tau2 <= tau1 * 1.2:
                    continue
                X = _basis(tt, tau1, tau2)
                k = X.shape[1]
                A = X.T @ X + RIDGE * np.eye(k)
                B = np.linalg.solve(A, X.T @ yy)            # k × días
                sse = ((yy - X @ B) ** 2).sum(0)
                # Nivel de largo plazo b0 plausible (−5% a 25% continuo).
                sse = np.where((B[0] > -0.05) & (B[0] < 0.25), sse, np.inf)
                mejor = sse < best_sse
                if mejor.any():
                    best_sse[mejor] = sse[mejor]
                    best[mejor, :k] = B[:, mejor].T
                    best[mejor, 4] = tau1
                    best[mejor, 5] = tau2 if tau2 is not None else np.nan
        n = int(m.sum())
        rmse = np.sqrt(best_sse / n) * 1e4
        if not exacto:
            taus_vistos.extend(best[np.isfinite(best_sse), 4].tolist())
        modelo = "nss" if nss else ("ns_tau_fijo" if exacto else "ns")
        for j, i in enumerate(idx):
            if not np.isfinite(best_sse[j]):
                continue
            out.append((w.index[i].date(), nombre, modelo, *best[j], n,
                        None if exacto else float(rmse[j]), float(tt.min()), float(tt.max())))
    cols = ["fecha", "curva", "modelo", "b0", "b1", "b2", "b3", "tau1", "tau2",
            "n_puntos", "rmse_pb", "plazo_min", "plazo_max"]
    return pd.DataFrame(out, columns=cols)


def rebuild_curves(db) -> int:
    """Reajusta todas las curvas desde 2005 y reescribe `curva_ajuste`. Toma pocos segundos."""
    series = [s for c in CURVAS.values() for s, _, _ in c["puntos"]]
    w = _insumos(db, series)
    if w.empty:
        return 0
    partes = [_ajustar_curva(n, spec, w) for n, spec in CURVAS.items()]
    df = pd.concat([p for p in partes if not p.empty], ignore_index=True)
    if df.empty:
        return 0
    df = df.sort_values(["curva", "fecha"])
    df = df.astype(object).where(pd.notna(df), None)
    db.conn.execute("BEGIN TRANSACTION")
    try:
        db.conn.execute("DELETE FROM curva_ajuste")
        from db.common import fast_executemany
        fast_executemany(db.conn, f"INSERT INTO curva_ajuste ({', '.join(df.columns)}) VALUES "
                                  f"({', '.join('?' * len(df.columns))})", df.values.tolist())
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
    logger.info(f"curva_ajuste: {len(df)} ajustes ({df.groupby('curva').size().to_dict()})")
    return len(df)


# ============================================================
# Consultas para la web y la API
# ============================================================

def _params(db, fecha=None) -> dict[str, dict]:
    """Parámetros de cada curva en `fecha` (o el último día ≤ fecha)."""
    # Máximo 10 días de antigüedad: si una curva no tiene ajuste reciente (p. ej. bonos en pesos
    # en 2022–2023, sin BCP a 2 años) no se muestra, en vez de dibujar una curva vieja.
    ref = fecha or db.conn.execute("SELECT MAX(fecha) FROM curva_ajuste").fetchone()[0]
    df = db.conn.execute("""
        SELECT * FROM curva_ajuste WHERE fecha <= ? AND fecha >= ?::DATE - INTERVAL 10 DAY
        QUALIFY row_number() OVER (PARTITION BY curva ORDER BY fecha DESC) = 1
    """, [ref, ref]).fetchdf()
    return {r["curva"]: r for r in df.to_dict("records")}


def ultima_fecha(db):
    r = db.conn.execute("SELECT MAX(fecha) FROM curva_ajuste").fetchone()
    return r[0] if r else None


def rango_fechas(db):
    return db.conn.execute("SELECT MIN(fecha), MAX(fecha) FROM curva_ajuste").fetchone()


def _obs_en(db, series: list[str], fecha) -> dict[str, float]:
    ids = ", ".join(f"'{s}'" for s in series)
    rows = db.conn.execute(f"""
        SELECT series_id, arg_max(value, date) FROM observations
        WHERE series_id IN ({ids}) AND date <= ? AND date > ?::DATE - INTERVAL 7 DAY
          AND value IS NOT NULL GROUP BY 1
    """, [fecha, fecha]).fetchall()
    return dict(rows)


def curvas_en(db, fecha=None) -> dict:
    """Curvas ajustadas + puntos observados en una fecha, en % efectivo anual."""
    ps = _params(db, fecha)
    out = {}
    for nombre, spec in CURVAS.items():
        p = ps.get(nombre)
        if not p:
            continue
        f = p["fecha"]
        grid = np.geomspace(max(p["plazo_min"], 1 / 365), p["plazo_max"], 80)
        obs = _obs_en(db, [s for s, _, _ in spec["puntos"]], f)
        puntos = []
        for sid, plazo, conv in spec["puntos"]:
            if sid in obs:
                t, c = _a_continua(obs[sid], plazo, conv)
                puntos.append({"plazo": float(t), "tasa": float(efectiva(c)),
                               "ajustada": _z(p, t), "serie": sid})
        out[nombre] = {
            "titulo": spec["titulo"], "tipo": spec["tipo"], "fecha": str(f)[:10], "modelo": p["modelo"],
            "rmse_pb": _f(p["rmse_pb"]), "plazos": grid.round(4).tolist(),
            "cero": efectiva(zero_rate(p, grid)).round(4).tolist(),
            "forward": efectiva(forward_rate(p, grid)).round(4).tolist(),
            "puntos": puntos,
            "params": {k: _f(p[k]) for k in ("b0", "b1", "b2", "b3", "tau1", "tau2")},
        }
    return out


def compensacion_inflacionaria(db, fecha=None) -> list[dict]:
    """Inflación implícita por plazo: swap pesos vs swap UF, y bonos BCP vs BCU."""
    ps = _params(db, fecha)
    f = (ps.get("swap_clp") or {}).get("fecha")
    pub = _obs_en(db, list(BEI_BCCH.values()), f) if f else {}
    out = []
    for t in (1, 2, 3, 5, 10):
        fila = {"plazo": t}
        for par, (n, r) in {"swap": ("swap_clp", "swap_uf"), "bono": ("bono_clp", "bono_uf")}.items():
            pn, pr = ps.get(n), ps.get(r)
            if pn and pr and pn["plazo_min"] <= t <= pn["plazo_max"] and pr["plazo_min"] <= t <= pr["plazo_max"]:
                fila[par] = float(efectiva(zero_rate(pn, [t])[0] - zero_rate(pr, [t])[0]))
        if t in BEI_BCCH and BEI_BCCH[t] in pub:
            fila["bcch"] = pub[BEI_BCCH[t]]
        out.append(fila)
    return out


def tpm_implicita(db, fecha=None, meses: int = 24) -> dict:
    """
    Trayectoria de TPM que descuenta el mercado: forward a 1 mes de la curva swap en pesos,
    expresada como tasa overnight base 360 (la convención de la TPM). Incluye premio por
    plazo: no es una expectativa pura.
    """
    p = _params(db, fecha).get("swap_clp")
    if not p:
        return {}
    f = p["fecha"]
    h = np.arange(0, meses + 1) / 12
    a, b = np.maximum(h, 1 / 365), h + 1 / 12
    fwd = (zero_rate(p, b) * b - zero_rate(p, a) * a) / (b - a)
    tpm = db.conn.execute(f"SELECT arg_max(value, date) FROM observations WHERE series_id='{TPM}' AND date <= ?",
                          [f]).fetchone()[0]
    encuestas = []
    for fuente, sid, mes in EXPECTATIVAS_TPM:
        r = db.conn.execute("SELECT arg_max(value, date), MAX(date) FROM observations "
                            "WHERE series_id = ? AND date <= ? AND date > ?::DATE - INTERVAL 70 DAY",
                            [sid, f, f]).fetchone()
        if r and r[0] is not None:
            encuestas.append({"fuente": fuente, "meses": mes, "tpm": float(r[0]), "fecha": str(r[1])})
    return {"fecha": str(f)[:10], "tpm_actual": tpm, "meses": list(range(meses + 1)),
            "mercado": overnight_360(fwd).round(3).tolist(), "encuestas": encuestas}


def spread_bancario(db, fecha=None) -> list[dict]:
    """Depósitos bancarios en bolsa (pesos) frente al swap cámara al mismo plazo, en pb."""
    p = _params(db, fecha).get("swap_clp")
    if not p:
        return []
    obs = _obs_en(db, [s for s, _ in DEPOSITOS], p["fecha"])
    out = []
    for sid, dias in DEPOSITOS:
        if sid in obs:
            t, c = _a_continua(obs[sid], dias, "s360")
            out.append({"dias": dias, "deposito": float(efectiva(c)),
                        "swap": _z(p, t),
                        "spread_pb": float((c - zero_rate(p, [t])[0]) * 1e4)})
    return out


def historia(db, desde: str = "2010-01-01") -> dict:
    """Serie semanal de tasas clave (cero, % efectivo) calculadas desde los parámetros."""
    df = db.conn.execute("""
        SELECT * FROM curva_ajuste WHERE fecha >= ?
        QUALIFY row_number() OVER (PARTITION BY curva, date_trunc('week', fecha) ORDER BY fecha DESC) = 1
        ORDER BY fecha
    """, [desde]).fetchdf()
    out: dict[str, dict] = {}
    for curva, g in df.groupby("curva"):
        g = g.reset_index(drop=True)
        tau2 = g["tau2"].fillna(1.0).values
        b3 = g["b3"].fillna(0.0).values
        res = {"fecha": [str(d)[:10] for d in g["fecha"]]}
        for t in (1, 2, 5, 10):
            X1, e1 = _l(t, g["tau1"].values), np.exp(-t / g["tau1"].values)
            X2 = _l(t, tau2) - np.exp(-t / tau2)
            c = g["b0"].values + g["b1"].values * X1 + g["b2"].values * (X1 - e1) + b3 * X2
            dentro = (g["plazo_min"].values <= t + 1e-9) & (g["plazo_max"].values >= t - 1e-9)
            res[f"{t}a"] = [round(float(v), 3) if ok else None for v, ok in zip(efectiva(c), dentro)]
        out[curva] = res
    return out


def factor_tasas_mensual(db) -> pd.DataFrame:
    """
    Cambio mensual de nivel de tasas (desplazamiento paralelo, en decimales): promedio de las
    variaciones de 2, 5 y 10 años de las curvas swap en pesos (`clp`) y en UF (`uf`). Insumo de
    la duración efectiva de los fondos de renta fija.
    """
    s = {"clp": ["F022.SPC.TIN.AN02.NO.Z.D", "F022.SPC.TIN.AN05.NO.Z.D", "F022.SPC.TIN.AN10.NO.Z.D"],
         "uf": ["F022.SPC.TIN.AN02.UF.Z.D", "F022.SPC.TIN.AN05.UF.Z.D", "F022.SPC.TIN.AN10.UF.Z.D"]}
    w = _insumos(db, s["clp"] + s["uf"], "2008-01-01")
    if w.empty:
        return pd.DataFrame()
    m = w.groupby(w.index.to_period("M")).last() / 100
    return pd.DataFrame({k: m[[c for c in v if c in m]].diff().mean(axis=1) for k, v in s.items()})
