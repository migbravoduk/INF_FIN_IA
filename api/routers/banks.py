"""
api/routers/banks.py — Estados financieros mensuales de bancos (CMF Bancos).

Reusa: Database.query_bank_statements().
ESQUELETO: stub con datos mock.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
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
    """
    Balances/resultados bancarios con desglose por moneda.
    TODO: db.query_bank_statements(bank, period, account, report_type, limit).to_dict(orient='records').
    """
    # MOCK
    return [{
        "period": 202512, "bank_code": "001", "bank_name": "BANCO DE CHILE",
        "account_code": "100000000", "account_name": "TOTAL ACTIVOS",
        "val_clp_no_reaj": 1000.0, "val_clp_reaj_ipc": 200.0,
        "val_clp_reaj_tc": 50.0, "val_extranjera": 80.0, "val_total": 1330.0,
    }]
