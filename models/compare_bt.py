"""
Compara dos specs de backtest en pares (misma empresa, origen y horizonte) y aplica la
REGLA DE ADOPCIÓN del proyecto (docs/PLAN_SEMANA.md §2). Sirve para decidir sin discusión.

Uso:
  python -m models.compare_bt --base scratch/a.csv:estr_last --nuevo scratch/b.csv:estr_last \
      --cuenta ganancia [--horizontes 2,3,4]

`archivo.csv:spec` — los dos pueden venir del mismo archivo (specs distintos) o de corridas
distintas (mismo spec, otra senda). `--cuenta`: ingresos | ganancia | cualquier prefijo del
nombre de la partida (p. ej. "Resultado" para bancos, models/backtest_simple.py).

Regla (las cuatro a la vez, sobre los horizontes pedidos; por defecto 2–4, que es donde manda
el estructural en producción):
  1. gana en ≥ 50% de los pares distintos (empates fuera, test de signo; nov-2026),
  2. ΔMASE medio < 0,
  3. la mediana mejora en promedio entre horizontes,
  4. en ningún horizonte la mediana empeora más de 0,02.
Calibración: el paso B (no operacional, nov-2026) pasa con 51,8% de pares, Δ medio −0,058 y
medianas −0,06 a −0,09. La tasa de pares sola es ruidosa (z≈1 con ~1.000 pares correlacionados):
por eso la regla exige además medias y medianas.
Sale con código 0 si ADOPTAR, 1 si NO ADOPTAR.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

WIN_MIN, MED_TOL = 0.50, 0.02
CUENTAS = {"ingresos": "Ingresos", "ganancia": "Ganancia"}


def _load(arg: str, cuenta: str) -> pd.Series:
    path, spec = arg.rsplit(":", 1)
    df = pd.read_csv(path, dtype={"rut": str, "origin": str})
    df = df[(df["spec"] == spec) & df["account"].str.startswith(CUENTAS.get(cuenta, cuenta))]
    if df.empty:
        sys.exit(f"Sin filas para {arg} / {cuenta}")
    return df.set_index(["rut", "origin", "h"])["mase"]


def comparar(base: pd.Series, nuevo: pd.Series, horizontes: list[int]) -> dict:
    w = pd.concat([base.rename("base"), nuevo.rename("nuevo")], axis=1).dropna()
    w = w[w.index.get_level_values("h").isin(horizontes)]
    d = w["nuevo"] - w["base"]
    # Test de signo: los empates (misma proyección) no informan y se descartan. Importa en
    # cambios que solo tocan parte de las series (limpieza de atípicos, tope): contarlos como
    # derrotas castigaría un cambio por no hacer nada donde no hace falta.
    dist = d[d != 0]
    n = len(dist)
    win = float((dist < 0).mean()) if n else 0.0
    med = w.groupby("h").median()
    cambio = med["nuevo"] - med["base"]
    peor = float(cambio.max())
    return {"n": n, "win": win, "z": (win - 0.5) / np.sqrt(0.25 / n) if n else 0.0,
            "d_media": float(d.mean()), "medianas": med.round(3), "peor_h": peor,
            "d_mediana": float(cambio.mean()),
            "adoptar": bool(win >= WIN_MIN and d.mean() < 0 and cambio.mean() < 0 and peor <= MED_TOL)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--nuevo", required=True)
    ap.add_argument("--cuenta", required=True,
                    help="ingresos | ganancia | cualquier prefijo del nombre de la partida")
    ap.add_argument("--horizontes", default="2,3,4")
    a = ap.parse_args()
    hs = [int(x) for x in a.horizontes.split(",")]
    r = comparar(_load(a.base, a.cuenta), _load(a.nuevo, a.cuenta), hs)
    print(f"{a.cuenta} · h={hs} · {r['n']} pares distintos")
    print(r["medianas"].to_string())
    print(f"gana {r['win']:.1%} (z={r['z']:+.1f}) · dMASE medio {r['d_media']:+.3f} · "
          f"cambio medio de mediana {r['d_mediana']:+.3f} · peor horizonte {r['peor_h']:+.3f}")
    print("VEREDICTO:", "ADOPTAR" if r["adoptar"] else "NO ADOPTAR")
    sys.exit(0 if r["adoptar"] else 1)


if __name__ == "__main__":
    main()
