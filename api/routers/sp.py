"""
api/routers/sp.py — Superintendencia de Pensiones (valores cuota, precios, cartera).
Reusa: query_sp_quota_values(), query_sp_instrument_prices(), query_sp_portfolio_holdings().
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, records
from db.database import Database

router = APIRouter()


@router.get("/cuotas")
def sp_cuotas(
    afp: Optional[str] = Query(None, description="ej. CAPITAL, HABITAT"),
    fund: Optional[str] = Query(None, description="A | B | C | D | E"),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    limit: int = Query(50, le=5000),
    db: Database = Depends(get_db),
):
    """Valores cuota y patrimonio."""
    return records(db.query_sp_quota_values(
        afp=afp, fund=fund, from_date=from_date, to_date=to_date, limit=limit,
    ))


@router.get("/precios")
def sp_precios(
    instrument: Optional[str] = Query(None, description="Nemotécnico o RUT"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(50, le=5000),
    db: Database = Depends(get_db),
):
    """Cinta diaria de precios."""
    return records(db.query_sp_instrument_prices(instrument_id=instrument, date=date, limit=limit))


@router.get("/cartera")
def sp_cartera(
    period: Optional[str] = Query(None, description="YYYY-MM"),
    afp: Optional[str] = Query(None),
    fund: Optional[str] = Query(None, description="A | B | C | D | E"),
    limit: int = Query(50, le=5000),
    db: Database = Depends(get_db),
):
    """Cartera mensual desagregada."""
    return records(db.query_sp_portfolio_holdings(period=period, afp=afp, fund=fund, limit=limit))
