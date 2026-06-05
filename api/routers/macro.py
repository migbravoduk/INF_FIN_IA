"""
api/routers/macro.py — Series macroeconómicas (BCCh).

Reusa: Database.get_all_series(), Database.get_series().
Patrón de implementación (cuando se codifique):
    df = db.get_series(series_id, from_date, to_date)
    return df.to_dict(orient="records")

ESQUELETO: devuelve datos mock para que el dashboard y /docs funcionen sin lógica real.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from db.database import Database

router = APIRouter()


@router.get("/series")
def list_series(
    source: Optional[str] = Query(None, description="Filtrar por fuente (bcentral, cmf, ...)"),
    db: Database = Depends(get_db),
):
    """Lista las series registradas. TODO: return db.get_all_series(source).to_dict(orient='records')."""
    # MOCK
    return [{"id": "F073.UFF.PRE.Z.D", "name": "Unidad de Fomento", "frequency": "daily"}]


@router.get("/series/{series_id}/observations")
def series_observations(
    series_id: str,
    from_date: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD"),
    db: Database = Depends(get_db),
):
    """
    Observaciones (serie de tiempo) de una serie.
    TODO: return db.get_series(series_id, from_date, to_date).to_dict(orient='records').
    """
    # MOCK
    return {
        "series_id": series_id,
        "observations": [
            {"date": "2026-06-01", "value": 39150.42},
            {"date": "2026-06-02", "value": 39152.10},
        ],
    }
