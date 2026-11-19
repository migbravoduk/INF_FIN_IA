"""
api/routers/curvas.py — Curvas de tasas de Chile: soberana y swap, nominal y real, compensación
inflacionaria, TPM implícita frente a encuestas y spread de depósitos bancarios.

Todo se calcula desde `curva_ajuste` (db/curves.py); la página no toca series crudas salvo los
puntos observados que se dibujan junto a la curva ajustada.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_db, templates
from db import curves as C
from db.database import Database

router = APIRouter()


def _fecha(fecha: str | None):
    if not fecha:
        return None
    try:
        return dt.date.fromisoformat(fecha)
    except ValueError:
        raise HTTPException(400, "fecha debe ser AAAA-MM-DD")


def _tasa_en(curva: dict, t: float):
    """Tasa cero de la curva ajustada al plazo `t` (años): el primer plazo ≥ t, igual que la tabla
    de la página. None si la curva no llega a ese plazo."""
    plazos = curva["plazos"]
    if not plazos or plazos[0] > t + 0.0001:
        return None
    for p, v in zip(plazos, curva["cero"]):
        if p >= t - 0.0001:
            return v
    return None


def _pendiente_pb(curvas: dict):
    """Pendiente 10 años − 2 años de la curva swap en pesos, en puntos base (None si no hay)."""
    c = (curvas or {}).get("swap_clp")
    if not c:
        return None
    a, b = _tasa_en(c, 2), _tasa_en(c, 10)
    return None if a is None or b is None else (b - a) * 100


def _datos(db: Database, f) -> dict:
    return {
        "curvas": C.curvas_en(db, f),
        "bei": C.compensacion_inflacionaria(db, f),
        "tpm": C.tpm_implicita(db, f),
        "spread": C.spread_bancario(db, f),
    }


@router.get("/curvas")
def curvas(request: Request, fecha: str | None = Query(None), db: Database = Depends(get_db)):
    f = _fecha(fecha)
    rango = C.rango_fechas(db)
    return templates.TemplateResponse(request, "curvas.html", {
        **_datos(db, f), "historia": C.historia(db),
        "pendiente_pb": _pendiente_pb(C.curvas_en(db, f)),
        "fecha_min": rango[0], "fecha_max": rango[1], "fecha_sel": f or rango[1],
    })


@router.get("/api/v1/curvas")
def api_curvas(fecha: str | None = Query(None, description="AAAA-MM-DD; por defecto la última"),
               db: Database = Depends(get_db)):
    """Curvas ajustadas (cero y forward, % efectivo anual), compensación inflacionaria,
    TPM implícita y spread bancario en una fecha."""
    return _datos(db, _fecha(fecha))
