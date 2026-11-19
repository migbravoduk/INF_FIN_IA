"""
api/routers/banks.py — Estados financieros mensuales de bancos (CMF Bancos).
Reusa: Database.query_bank_statements().
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, records
from db.database import Database

router = APIRouter()


@router.get("/statements")
def bank_statements(
    bank: Optional[str] = Query(None, description="Código SBIF, ej. 001"),
    period: Optional[int] = Query(None, description="YYYYMM"),
    account: Optional[str] = Query(None, description="Código de cuenta, ej. 100000000"),
    report_type: Optional[str] = Query(None, alias="type", description="balance | resultado"),
    limit: int = Query(50, le=1000),
    db: Database = Depends(get_db),
):
    """Balances/resultados bancarios con desglose por moneda."""
    return records(db.query_bank_statements(
        bank_code=bank, period=period, account_code=account,
        report_type=report_type, limit=limit,
    ))
