"""
api/routers/cmf.py — Estados financieros corporativos (CMF Empresas).
Reusa: Database.get_cmf_companies(), Database.query_cmf_statements().
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, records
from db.database import Database

router = APIRouter()


@router.get("/companies")
def list_companies(db: Database = Depends(get_db)):
    """Empresas (RUT + razón social) presentes en la BD."""
    return records(db.get_cmf_companies())


@router.get("/statements")
def cmf_statements(
    rut: Optional[str] = Query(None),
    company: Optional[str] = Query(None, description="Nombre parcial"),
    period: Optional[int] = Query(None, description="YYYYMM, ej. 202512"),
    limit: int = Query(50, le=1000),
    db: Database = Depends(get_db),
):
    """Estados financieros corporativos filtrados."""
    return records(db.query_cmf_statements(rut=rut, company=company, period=period, limit=limit))
