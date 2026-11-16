"""
db/cross.py — Lectura cruzada de carteras institucionales.

¿En qué emisores coinciden las compañías de seguros y los fondos mutuos? Ambos informan
sus instrumentos con nemotécnico: la renta fija de seguros (Circular 1.835, B.1) y la
cartera de fondos mutuos (NACI/EXTR). El nemotécnico enlaza las dos y el RUT del emisor
sale de B.1 → dim_entidad.

Límites (se dicen en pantalla):
- La cartera de AFP que publica la SP viene agregada por categoría, sin instrumento, así
  que las AFP no entran en el cruce.
- B.1 es solo renta fija y el monto es nominal en la unidad del instrumento: se cuentan
  posiciones, no se suman montos entre monedas.
"""

from __future__ import annotations


def common_issuers(db, limit: int = 40) -> dict:
    per_seg = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    per_ffm = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_holdings").fetchone()[0]
    if not per_seg or not per_ffm:
        return {"rows": [], "per_seg": per_seg, "per_ffm": per_ffm}
    rows = db.conn.execute("""
        WITH seg AS (
            SELECT upper(trim(nemotecnico)) nemo, ltrim(rut_emisor, '0') rut,
                   COUNT(*) pos_seg, COUNT(DISTINCT rut) cias
            FROM cmf_insurer_portfolio_fixed_income
            WHERE period = ? AND nemotecnico IS NOT NULL AND trim(nemotecnico) <> ''
            GROUP BY 1, 2),
        ffm AS (
            SELECT upper(trim(nemotecnico)) nemo, COUNT(*) pos_ffm, COUNT(DISTINCT fund_run) fondos,
                   SUM(monto) FILTER (WHERE moneda IN ('$$', 'PROM', 'CLP')) monto_clp
            FROM v_mutual_fund_holdings
            WHERE period = ? AND nemotecnico IS NOT NULL
            GROUP BY 1)
        SELECT seg.rut, coalesce(e.nombre_corto, e.nombre_canonico) AS emisor, e.tipo,
               COUNT(DISTINCT seg.nemo) instrumentos, SUM(seg.cias) cias_seg, SUM(ffm.fondos) fondos_ffm,
               SUM(seg.pos_seg) pos_seg, SUM(ffm.pos_ffm) pos_ffm
        FROM seg JOIN ffm USING (nemo)
        LEFT JOIN dim_entidad e ON e.rut = seg.rut
        GROUP BY 1, 2, 3
        ORDER BY fondos_ffm + cias_seg DESC
        LIMIT ?
    """, [per_seg, per_ffm, limit]).fetchall()
    cols = ["rut", "emisor", "tipo", "instrumentos", "cias_seg", "fondos_ffm", "pos_seg", "pos_ffm"]
    return {"rows": [dict(zip(cols, r)) for r in rows], "per_seg": per_seg, "per_ffm": per_ffm}
