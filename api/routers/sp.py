"""
api/routers/sp.py — Superintendencia de Pensiones (valores cuota, precios, cartera).

Reusa: Database.query_sp_quota_values(), Database.query_sp_instrument_prices().
Pendiente en la capa DB (stub): Database.query_sp_portfolio_holdings() para /cartera.
ESQUELETO: stubs con datos mock.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
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
    """Valores cuota y patrimonio. TODO: db.query_sp_quota_values(...).to_dict(orient='records')."""
    # MOCK
    return [{
        "date": "2026-06-04", "afp_name": "HABITAT", "fund_type": "A",
        "quota_value": 75123.45, "equity_value": 9876543210.0,
    }]


@router.get("/precios")
def sp_precios(
    instrument: Optional[str] = Query(None, description="Nemotécnico o RUT"),
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(50, le=5000),
    db: Database = Depends(get_db),
):
    """Cinta diaria de precios. TODO: db.query_sp_instrument_prices(...).to_dict(orient='records')."""
    # MOCK
    return [{
        "date": "2026-06-04", "instrument_id": "AESANDES",
        "instrument_type": "ACC", "currency": "CLP", "price": 142.5,
    }]


@router.get("/cartera")
def sp_cartera(
    period: Optional[str] = Query(None, description="YYYY-MM"),
    afp: Optional[str] = Query(None),
    fund: Optional[str] = Query(None, description="A | B | C | D | E"),
    limit: int = Query(50, le=5000),
    db: Database = Depends(get_db),
):
    """
    Cartera mensual desagregada.
    TODO: implementar Database.query_sp_portfolio_holdings() y llamarlo aquí.
    """
    # MOCK
    return [{
        "period": "2026-01", "afp_name": "TOTAL", "fund_type": "A",
        "instrument_glosa": "Bonos de gobierno", "monto_pesos": 1.2e12,
        "monto_dolares": None, "porcentaje": 18.4,
    }]
