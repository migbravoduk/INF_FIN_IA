"""
api/routers/api_v1.py — API REST versionada (/api/v1).

Cubre lo que las rutas /api/* originales no exponían: productos y sus métricas, entidades,
cartera de seguros, fondos mutuos y estado de calidad/frescura. Las rutas /api/* antiguas
siguen vivas para no romper consumidores existentes.

Todas las respuestas son JSON de solo lectura; fechas en ISO y NaN → null.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db, records
from db import products as P
from db.database import Database

router = APIRouter()


def _clean(rows: list[dict]) -> list[dict]:
    import math
    out = []
    for r in rows:
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else
                        v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()})
    return out


@router.get("/productos")
def productos(tipo: str = "", categoria: str = "", clase: str = "", moneda: str = "",
              proveedor: str = "", q: str = "", orden: str = Query("rent_12m", enum=list(P.ORDENES)),
              limit: int = Query(100, le=1000), db: Database = Depends(get_db)):
    """Productos vigentes con sus métricas del último corte."""
    rows, total = P.list_products(db, tipo, categoria, clase, moneda, proveedor, q, orden, limit)
    return {"total": total, "items": _clean(rows)}


@router.get("/productos/{producto_id}")
def producto(producto_id: str, db: Database = Depends(get_db)):
    p = P.get_product(db, producto_id)
    if not p:
        raise HTTPException(404, "Producto no encontrado")
    p["m"] = _clean([p["m"]])[0] if p.get("m") else {}
    return _clean([p])[0] | {"serie_mensual": P.product_series(db, producto_id)}


@router.get("/entidades/{rut}")
def entidad(rut: str, db: Database = Depends(get_db)):
    rut = rut.split("-")[0].replace(".", "").lstrip("0")
    df = db.conn.execute("SELECT * FROM dim_entidad WHERE rut = ?", [rut]).fetchdf()
    if df.empty:
        raise HTTPException(404, "Entidad no encontrada")
    alias = db.conn.execute(
        "SELECT nombre, fuente, desde_periodo, hasta_periodo FROM dim_entidad_alias WHERE rut = ?",
        [rut]).fetchdf()
    return records(df)[0] | {"alias": records(alias)}


@router.get("/seguros/cartera")
def seguros_cartera(period: int, ramo: str = Query("vida", enum=["vida", "generales"]),
                    rut: str | None = None, db: Database = Depends(get_db)):
    """Composición por tipo de inversión (B.8), en miles de pesos."""
    return records(db.get_insurer_allocation(period, ramo, rut))


@router.get("/ffmm/cuotas")
def ffmm_cuotas(fund_run: str, series: str = "", db: Database = Depends(get_db)):
    """Valor cuota por corte mensual de un fondo mutuo (y serie)."""
    return db.get_mutual_fund_quote_series(fund_run, series)


@router.get("/calidad")
def calidad(db: Database = Depends(get_db)):
    """Última corrida de controles de calidad y estado de frescura de cada fuente."""
    from db.quality import last_summary
    from scheduler.freshness import probe_all
    return {
        "calidad": last_summary(db),
        "frescura": [{"fuente": s.source, "kind": s.kind, "ultimo": s.latest_have,
                      "esperado": s.expected, "pendiente": s.due, "nota": s.note}
                     for s in probe_all(db)],
    }
