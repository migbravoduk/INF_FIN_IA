"""
api/routers/productos.py — Productos de inversión: explorador, ficha, comparador, metodología
y salida al proveedor.

Primer router por dominio (Fase 5 del plan): no agrega más código a `views.py`.

Criterio editorial (ver docs/PLAN_MEJORAS.md §8): se muestran datos y comparaciones
objetivas con metodología publicada, NO recomendaciones personalizadas. El orden de los
listados depende solo de métricas reproducibles; ningún proveedor puede pagar por posición.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from api.deps import get_db, templates
from db import products as P
from db.database import Database

logger = logging.getLogger(__name__)
router = APIRouter()

EVENTOS_DB = Path(__file__).resolve().parents[2] / "data" / "eventos.sqlite"

ORDEN_LABELS = {
    "rent_12m": "Rentabilidad 12 meses", "rent_real_12m": "Rentabilidad real 12 meses",
    "rent_36m": "Rentabilidad 3 años (anual)", "percentil": "Posición en su categoría",
    "consistencia": "Consistencia", "costo": "Menor costo", "volatilidad": "Menor volatilidad",
    "sharpe": "Sharpe", "patrimonio": "Patrimonio", "flujo": "Flujo neto 12 meses",
}


@router.get("/productos")
def productos(request: Request, db: Database = Depends(get_db)):
    return templates.TemplateResponse(request, "productos.html", {
        "filtros": P.product_filters(db), "ordenes": ORDEN_LABELS,
        "corte": P.latest_metrics_date(db),
    })


@router.get("/productos/tabla")
def productos_tabla(request: Request, tipo: str = Query(""), categoria: str = Query(""),
                    clase: str = Query("Retail"), moneda: str = Query(""), proveedor: str = Query(""),
                    q: str = Query(""), orden: str = Query("rent_12m"),
                    db: Database = Depends(get_db)):
    rows, total = P.list_products(db, tipo, categoria, clase, moneda, proveedor, q, orden)
    return templates.TemplateResponse(request, "partials/productos_tabla.html", {
        "rows": rows, "total": total, "orden": orden, "orden_label": ORDEN_LABELS.get(orden, orden),
        "clase": clase,
    })


@router.get("/producto/{producto_id}")
def producto(request: Request, producto_id: str, db: Database = Depends(get_db)):
    prod = P.get_product(db, producto_id)
    if not prod:
        raise HTTPException(404, "Producto no encontrado")
    reparto = producto_id in P.ffmm_reparto_sospechoso(db) if prod["tipo"] == "ffmm" else False
    oficial = db.get_mutual_fund_official_returns(prod["codigo_fuente"], prod["serie"] or "") \
        if prod["tipo"] == "ffmm" else {}
    return templates.TemplateResponse(request, "producto.html", {
        "p": prod, "m": prod["m"], "serie": P.product_series(db, producto_id),
        "cartera": P.product_holdings(db, producto_id), "reparto": reparto, "oficial": oficial,
    })


@router.get("/productos/comparar")
def productos_comparar(request: Request, ids: str = Query(""), db: Database = Depends(get_db)):
    lista = [i for i in ids.split(",") if i][:4]
    prods = [p for p in (P.get_product(db, i) for i in lista) if p]
    series = {p["producto_id"]: P.product_series(db, p["producto_id"]) for p in prods}
    traslape = []
    ffmm = [p for p in prods if p["tipo"] == "ffmm"]
    for i, a in enumerate(ffmm):
        for b in ffmm[i + 1:]:
            if a["codigo_fuente"] != b["codigo_fuente"]:
                traslape.append((a["nombre"], b["nombre"], P.overlap(db, a["codigo_fuente"], b["codigo_fuente"])))
    clases = {p["clase_inversionista"] for p in prods}
    return templates.TemplateResponse(request, "productos_comparar.html", {
        "prods": prods, "series": series, "traslape": traslape,
        "mezcla_clases": len(clases) > 1, "ids": ",".join(lista),
    })


@router.get("/metodologia")
def metodologia(request: Request, db: Database = Depends(get_db)):
    datasets = db.conn.execute("SELECT * FROM dim_dataset ORDER BY fuente, tabla").fetchdf().to_dict("records")
    try:
        from db.quality import last_summary
        dq = last_summary(db)
    except Exception:
        dq = None
    return templates.TemplateResponse(request, "metodologia.html", {"datasets": datasets, "dq": dq})


def _registrar_salida(producto_id: str, rut: str | None, origen: str) -> None:
    """
    Evento ANÓNIMO de salida al proveedor, en un SQLite aparte: la base analítica la abre
    la web en solo lectura y DuckDB admite un único escritor. No se guarda IP, cookie ni
    ningún dato personal (ver PLAN §8, protección de datos).
    """
    try:
        EVENTOS_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(EVENTOS_DB) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS evento_salida (
                ts TEXT NOT NULL, producto_id TEXT, rut_proveedor TEXT, origen TEXT)""")
            con.execute("INSERT INTO evento_salida VALUES (?, ?, ?, ?)",
                        [dt.datetime.now().isoformat(timespec="seconds"), producto_id, rut, origen])
    except Exception as e:
        logger.warning(f"No se pudo registrar la salida: {e}")


@router.get("/ir/{producto_id}")
def ir_al_proveedor(producto_id: str, origen: str = Query("ficha"), db: Database = Depends(get_db)):
    prod = P.get_product(db, producto_id)
    if not prod or not (prod.get("url_contratacion") or prod.get("url_sitio")):
        raise HTTPException(404, "Este proveedor aún no tiene un canal verificado")
    _registrar_salida(producto_id, prod.get("rut_proveedor"), origen)
    return RedirectResponse(prod.get("url_contratacion") or prod["url_sitio"], status_code=302)
