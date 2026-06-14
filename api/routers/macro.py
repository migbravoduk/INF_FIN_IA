"""
api/routers/macro.py — Series macroeconómicas (BCCh).
Reusa: Database.get_all_series(), Database.get_series().
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db, records
from db.database import Database

router = APIRouter()


@router.get("/series")
def list_series(
    source: Optional[str] = Query(None, description="Filtrar por fuente (bcentral, cmf, ...)"),
    db: Database = Depends(get_db),
):
    """Lista las series registradas en la BD."""
    return records(db.get_all_series(source_id=source))


@router.get("/series/{series_id}/observations")
def series_observations(
    series_id: str,
    from_date: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD"),
    db: Database = Depends(get_db),
):
    """Observaciones (serie de tiempo) de una serie."""
    df = db.get_series(series_id, from_date=from_date, to_date=to_date)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"Sin datos para la serie '{series_id}'")
    return {"series_id": series_id, "observations": records(df)}
