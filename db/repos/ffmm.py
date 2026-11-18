"""
db/repos/ffmm.py — Fondos mutuos (CMF): valor cuota, cartera, comisiones y rentabilidades oficiales.

Mixin de `db.database.Database`: los métodos usan `self.conn` (conexión DuckDB) y se
acceden como siempre, `Database().<método>`. Separado de database.py por dominio (Fase 5
del plan) sin cambiar el código de ningún método.
"""

import logging
import re as _re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from db.common import (is_total_account, AGF_SQL_PREDICATE, _agf_where, is_agf_name,
                       compute_ratios, _TOTAL_RE, fast_executemany)

logger = logging.getLogger("db.database")


class FfmmRepo:

    # ============================================================
    # Fondos Mutuos (CMF)
    # ============================================================

    def upsert_mutual_fund_quotas(self, records: list) -> int:
        """
        Carga la estadística diaria de fondos mutuos (patrimonio, partícipes, valor cuota).

        Idempotente por `date`: una descarga trae TODOS los fondos del día, así que el
        Delete-then-Insert por fecha reemplaza el día completo. No hay UNIQUE en la tabla a
        propósito (ver db/schema.py): el índice rompería el DELETE de la reingesta.
        """
        if not records:
            return 0
        cols = ["date", "agf_rut", "agf_name", "fund_run", "fund_name", "fund_type",
                "series", "currency", "quota_value", "net_asset_value", "participants"]
        for fecha in {r["date"] for r in records}:
            self.conn.execute("DELETE FROM cmf_mutual_fund_quota_values WHERE date = ?", [fecha])
        fast_executemany(self.conn, 
            f"INSERT INTO cmf_mutual_fund_quota_values ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            [[r.get(c) for c in cols] for r in records])
        return len(records)

    def upsert_mutual_fund_portfolio(self, records: list[dict]) -> dict:
        """
        Inserta la cartera de fondos mutuos, idempotente por periodo (año, mes) y tipo.
        """
        if not records:
            return {"inserted": 0, "deleted": 0}

        # Agrupar por (period_year, period_month, portfolio_type)
        periods = list(set((r["period_year"], r["period_month"], r["portfolio_type"]) for r in records))
        
        con = self.conn
        deleted = 0
        for y, m, ptype in periods:
            res = con.execute("DELETE FROM cmf_mutual_fund_portfolio WHERE period_year = ? AND period_month = ? AND portfolio_type = ?", [y, m, ptype])
            deleted += res.rowcount

        fast_executemany(con, """
            INSERT INTO cmf_mutual_fund_portfolio 
            (period_year, period_month, portfolio_type, fund_run, fund_name, raw_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (
                r["period_year"], 
                r["period_month"], 
                r["portfolio_type"], 
                r["fund_run"], 
                r["fund_name"], 
                r["raw_data"]
            )
            for r in records
        ])
        
        return {"inserted": len(records), "deleted": deleted}

    def upsert_mutual_fund_types(self, tipos: dict) -> int:
        """Guarda la glosa oficial de cada tipo de fondo (viene al pie del archivo CMF)."""
        if not tipos:
            return 0
        for code, label in tipos.items():
            self.conn.execute("DELETE FROM cmf_mutual_fund_types WHERE code = ?", [int(code)])
            self.conn.execute(
                "INSERT INTO cmf_mutual_fund_types (code, label) VALUES (?, ?)",
                [int(code), str(label)])
        return len(tipos)

    def get_mutual_fund_types_map(self) -> dict:
        """{codigo: glosa} de tipo de fondo."""
        return {int(c): l for c, l in self.conn.execute(
            "SELECT code, label FROM cmf_mutual_fund_types ORDER BY code").fetchall()}

    def get_mutual_fund_quote_dates(self) -> list:
        """Fechas con estadística diaria cargada, de la más reciente hacia atrás."""
        return [str(d) for (d,) in self.conn.execute(
            "SELECT DISTINCT date FROM cmf_mutual_fund_quota_values ORDER BY date DESC"
        ).fetchall()]

    def get_mutual_fund_agfs(self) -> list:
        """Administradoras presentes, con cuántos fondos maneja cada una."""
        rows = self.conn.execute("""
            SELECT agf_name, COUNT(DISTINCT fund_run) AS n_fondos
            FROM cmf_mutual_fund_quota_values
            WHERE agf_name IS NOT NULL AND agf_name <> ''
            GROUP BY agf_name ORDER BY agf_name
        """).fetchall()
        return [{"agf": r[0], "n_fondos": int(r[1])} for r in rows]

    def get_mutual_fund_returns(self, desde: str, hasta: str, agf: str = "",
                                fund_type=None, limit: int = 40) -> list:
        """
        Variación del valor cuota entre dos fechas, por fondo y serie.

        Se compara serie a serie porque el valor cuota es propio de cada serie (comisiones
        distintas), y se exige que la serie exista en AMBAS fechas: si no, la "rentabilidad"
        sería un artefacto de una serie que nació o se cerró dentro de la ventana.

        Es variación NOMINAL del valor cuota en el período pedido, sin anualizar y sin
        descontar inflación. No se rotula como "rentabilidad CMF" porque no se ha verificado
        contra el documento metodológico del regulador.
        """
        where, params = [], [desde, hasta]
        if agf:
            where.append("q.agf_name = ?")
            params.append(agf)
        if fund_type:
            where.append("q.fund_type = ?")
            params.append(int(fund_type))
        filtro = (" AND " + " AND ".join(where)) if where else ""

        rows = self.conn.execute(f"""
            WITH {self._ffmm_vc_clp_cte()},
            v AS (
                SELECT fund_run, series, date, vc_clp AS quota_value, net_asset_value,
                       participants, fund_name, agf_name, fund_type, currency
                FROM q
                WHERE date IN (?, ?) AND vc_clp IS NOT NULL {filtro}
            ),
            ini AS (SELECT * FROM v WHERE date = ?),
            fin AS (SELECT * FROM v WHERE date = ?)
            SELECT f.fund_run, f.fund_name, f.agf_name, f.fund_type, f.series, f.currency,
                   i.quota_value AS vc_ini, f.quota_value AS vc_fin,
                   (f.quota_value / i.quota_value - 1) * 100 AS var_pct,
                   f.net_asset_value, f.participants
            FROM fin f
            JOIN ini i ON i.fund_run = f.fund_run AND i.series = f.series
            WHERE i.quota_value > 0
            ORDER BY var_pct DESC
            LIMIT ?
        """, params + [desde, hasta, int(limit)]).fetchall()
        cols = ["fund_run", "fund_name", "agf_name", "fund_type", "series", "currency",
                "vc_ini", "vc_fin", "var_pct", "patrimonio", "participes"]
        return [dict(zip(cols, r)) for r in rows]

    def get_mutual_fund_paths(self, desde: str, hasta: str, agf: str = "",
                              fund_type=None, top_n: int = 6) -> dict:
        """
        Trayectorias base 100 de los mejores y peores fondos de la ventana, más la MEDIANA
        del grupo filtrado.

        La mediana es lo que convierte el gráfico en una lectura y no en un adorno: sin ella
        una línea que sube solo dice "subió"; contra la mediana dice si le fue mejor o peor
        que a su categoría, que es la pregunta real. Se calcula sobre TODO el universo que
        pasa el filtro, no sobre los que se dibujan.

        Base 100 en el corte inicial: los valores cuota parten de niveles arbitrarios (uno en
        1.200 y otro en 45.000), así que sin rebasar no son comparables en el mismo eje.
        """
        where, params = [], [desde, hasta]
        if agf:
            where.append("agf_name = ?")
            params.append(agf)
        if fund_type:
            where.append("fund_type = ?")
            params.append(int(fund_type))
        filtro = (" AND " + " AND ".join(where)) if where else ""

        filas = self.conn.execute(f"""
            WITH {self._ffmm_vc_clp_cte()}
            SELECT date, fund_run, series, fund_name, agf_name, vc_clp
            FROM q
            WHERE date BETWEEN ? AND ? AND vc_clp IS NOT NULL {filtro}
            ORDER BY date
        """, params).fetchall()
        if not filas:
            return {"paths": [], "mediana": [], "cortes": [], "n_universo": 0}

        cortes = sorted({str(f[0]) for f in filas})
        por_serie = {}
        for fecha, run, serie, nombre, agf_name, vc in filas:
            por_serie.setdefault((run, serie), {"nombre": nombre, "agf": agf_name,
                                                "puntos": {}})["puntos"][str(fecha)] = vc

        # Solo entran las series presentes en AMBOS extremos: si no, la variación mediría
        # una serie que nació o murió dentro de la ventana (mismo criterio que la tabla).
        rebasadas, excluidas = [], []
        for (run, serie), d in por_serie.items():
            p = d["puntos"]
            if cortes[0] not in p or cortes[-1] not in p or not p[cortes[0]]:
                continue
            base = p[cortes[0]]
            curva = [{"x": c, "value": (p[c] / base) * 100} for c in cortes if c in p]
            # Un fondo mutuo no pierde el 90% ni se multiplica por 10 en un mes: un salto así
            # es un error de dato o un cambio de base de la serie (ej. valor cuota en 0), y
            # dibujarlo se lleva el "peor" del gráfico. Queda fuera de ranking y mediana.
            saltos = [b["value"] / a["value"] for a, b in zip(curva, curva[1:]) if a["value"]]
            if any(s <= 0.1 or s >= 10 for s in saltos):
                excluidas.append(f'{d["nombre"]} · {serie}')
                continue
            rebasadas.append({
                "run": run, "series": serie, "nombre": d["nombre"], "agf": d["agf"],
                "curva": curva, "final": curva[-1]["value"] if curva else 100.0,
            })
        if not rebasadas:
            return {"paths": [], "mediana": [], "cortes": cortes, "n_universo": 0}

        # Mediana del universo filtrado, corte a corte.
        mediana = []
        for c in cortes:
            vals = sorted(pt["value"] for r in rebasadas
                          for pt in r["curva"] if pt["x"] == c)
            if vals:
                m = len(vals) // 2
                med = vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
                mediana.append({"x": c, "value": med})

        rebasadas.sort(key=lambda r: r["final"], reverse=True)
        n = max(1, top_n // 2)
        elegidos = rebasadas[:n] + (rebasadas[-n:] if len(rebasadas) > n else [])
        vistos, paths = set(), []
        for r in elegidos:
            k = (r["run"], r["series"])
            if k in vistos:
                continue
            vistos.add(k)
            paths.append({"name": f'{r["nombre"]} · {r["series"]}',
                          "run": r["run"], "series": r["series"],
                          "points": r["curva"], "final": r["final"]})
        return {"paths": paths, "mediana": mediana, "cortes": cortes,
                "n_universo": len(rebasadas), "excluidas": excluidas}

    def get_mutual_fund_quote_series(self, fund_run: str, series: str = "") -> list:
        """Serie temporal del valor cuota de un fondo (una serie, o la de mayor patrimonio)."""
        if not series:
            row = self.conn.execute("""
                SELECT series FROM cmf_mutual_fund_quota_values
                WHERE fund_run = ? AND date = (SELECT MAX(date) FROM cmf_mutual_fund_quota_values)
                ORDER BY net_asset_value DESC NULLS LAST LIMIT 1
            """, [str(fund_run)]).fetchone()
            series = row[0] if row else ""
        rows = self.conn.execute(f"""
            WITH {self._ffmm_vc_clp_cte()}
            SELECT date, vc_clp, net_asset_value, participants
            FROM q
            WHERE fund_run = ? AND series = ? AND vc_clp IS NOT NULL
            ORDER BY date
        """, [str(fund_run), series]).fetchall()
        return [{"date": str(r[0]), "quota_value": r[1],
                 "patrimonio": r[2], "participes": r[3]} for r in rows], series

    def get_mutual_fund_identity(self, fund_run: str) -> dict:
        """Administradora, tipo y nombre de un fondo, del último día cargado."""
        row = self.conn.execute("""
            SELECT fund_name, agf_name, fund_type, currency
            FROM cmf_mutual_fund_quota_values
            WHERE fund_run = ?
            ORDER BY date DESC LIMIT 1
        """, [str(fund_run)]).fetchone()
        if not row:
            return {}
        return {"fund_name": row[0], "agf_name": row[1],
                "fund_type": row[2], "currency": row[3]}

    # ----------------------------------------------------------
    # Fondos mutuos (CMF — archivo de cartera mensual)
    # ----------------------------------------------------------
    #
    # QUÉ HAY Y QUÉ NO. El archivo de cartera describe INSTRUMENTOS, no el fondo: trae
    # `fund_run` + `fund_name` y, por cada posición, 23 campos `FFM_*` del instrumento.
    # NO viene la administradora (AGF) ni la categoría del fondo, así que no se puede
    # filtrar por ellas mientras no se ingeste otra fuente.
    #
    # Los códigos FFM_* se leen solo donde su contenido es autoevidente en los propios datos
    # (país ISO, moneda, tipo de instrumento). El diccionario oficial de la CMF no está
    # transcrito en el repo, así que el MONTO se expone como dato secundario y sin afirmar su
    # unidad: misma regla que las glosas de la Circular 1.835 — no se inventan etiquetas.
    # CADA TIPO DE CARTERA TIENE SU PROPIA NUMERACIÓN DE CAMPOS: nacional 601xxxx,
    # extranjera 602xxxx, opciones 603xxxx, futuros 604xxxx. Aplicar el mapa de una a las
    # otras devuelve NULL en silencio (se vio como "0 tipos, 0 países" en el ranking EXTR).
    #
    # NACI y EXTR son estructuralmente PARALELAS —mismos cuatro últimos dígitos, distinto
    # prefijo— y el contenido lo confirma: 601/6020300 traen países ISO, 601/6020400 códigos
    # de instrumento, 601/6020900 montos. Verificado perfilando 800 registros de cada una.
    #
    # OPCI y FUTU NO siguen ese paralelo (tienen 16 y 11 campos, con subcampos 60x0111-116),
    # así que quedan deliberadamente fuera del mapa: se cuentan sus posiciones pero no se
    # desglosan. Inventar la correspondencia sería adivinar, y el diccionario oficial de
    # campos FFM de la CMF no está transcrito en el repo.
    FFM_CAMPOS = {
        "NACI": {"nemotecnico": "FFM_6010100", "pais": "FFM_6010300",
                 "tipo": "FFM_6010400", "monto": "FFM_6010900", "moneda": "FFM_6011000"},
        "EXTR": {"nemotecnico": "FFM_6020100", "pais": "FFM_6020300",
                 "tipo": "FFM_6020400", "monto": "FFM_6020900", "moneda": "FFM_6021000"},
    }
    FFM_CARTERAS_MAPEADAS = tuple(FFM_CAMPOS)

    @classmethod
    def _carteras_sql(cls) -> str:
        """Tupla SQL de las carteras cuyo layout de campos está mapeado."""
        return "('" + "', '".join(cls.FFM_CAMPOS) + "')"

    @classmethod
    def _ffm(cls, campo: str) -> str:
        """Expresión SQL que lee `campo` con el código que corresponde a cada cartera."""
        ramas = " ".join(
            f"WHEN '{tipo}' THEN json_extract_string(raw_data, '$.{m[campo]}')"
            for tipo, m in cls.FFM_CAMPOS.items()
        )
        return f"(CASE portfolio_type {ramas} ELSE NULL END)"


    def upsert_mutual_fund_returns(self, records: list) -> int:
        """Carga las rentabilidades oficiales. Idempotente por corte (`fecha`)."""
        if not records:
            return 0
        cols = ["fecha", "fund_run", "series", "fund_name", "agf_name", "familia",
                "familia_rar", "clase_inversionista", "serie_digital",
                "rent_nominal_1a", "rent_real_1a", "rent_nominal_3a_ann", "rent_real_3a_ann",
                "rent_nominal_5a_ann", "rent_real_5a_ann", "rent_nominal_10a_ann",
                "rent_real_10a_ann", "rent_nominal_ytd", "rent_real_ytd", "ind_rar"]
        for fecha in {r["fecha"] for r in records}:
            self.conn.execute("DELETE FROM cmf_mutual_fund_returns WHERE fecha = ?", [fecha])
        fast_executemany(self.conn, 
            f"INSERT INTO cmf_mutual_fund_returns ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            [[r.get(c) for c in cols] for r in records])
        return len(records)

    def upsert_mutual_fund_agfs(self, records: list) -> int:
        """Catálogo de administradoras con RUT."""
        for r in records:
            self.conn.execute("DELETE FROM cmf_mutual_fund_agf WHERE agf_rut = ?", [r["agf_rut"]])
            self.conn.execute(
                "INSERT INTO cmf_mutual_fund_agf (agf_rut, agf_name) VALUES (?, ?)",
                [r["agf_rut"], r["agf_name"]])
        return len(records)

    def propagar_agf_rut(self) -> int:
        """
        Rellena `agf_rut` en la estadística diaria cruzando por RUN del fondo.

        No se cruza por nombre: la estadística diaria trae la razón social larga
        ('BANCHILE ADMINISTRADORA GENERAL DE FONDOS S.A.') y el comparador el nombre corto
        ('BANCHILE'). El RUN del fondo es la llave limpia entre ambas.
        """
        self.conn.execute("""
            UPDATE cmf_mutual_fund_quota_values AS q
            SET agf_rut = (
                SELECT a.agf_rut
                FROM cmf_mutual_fund_returns r
                JOIN cmf_mutual_fund_agf a ON a.agf_name = r.agf_name
                WHERE r.fund_run = q.fund_run
                LIMIT 1
            )
            WHERE agf_rut IS NULL
        """)
        # Segundo paso: fondos que no están en el comparador (nuevos o sin cifra oficial).
        # Su administradora SÍ está identificada por la razón social larga en otras filas de
        # la misma tabla que ya tienen RUT; se copia de ahí. Es un cruce por la razón social
        # EXACTA del mismo archivo de la CMF, no un parecido entre nombres distintos.
        self.conn.execute("""
            UPDATE cmf_mutual_fund_quota_values AS q
            SET agf_rut = k.agf_rut
            FROM (SELECT agf_name, MIN(agf_rut) AS agf_rut
                  FROM cmf_mutual_fund_quota_values
                  WHERE agf_rut IS NOT NULL
                  GROUP BY agf_name HAVING COUNT(DISTINCT agf_rut) = 1) AS k
            WHERE q.agf_rut IS NULL AND q.agf_name = k.agf_name
        """)
        return self.conn.execute(
            "SELECT COUNT(*) FROM cmf_mutual_fund_quota_values WHERE agf_rut IS NOT NULL"
        ).fetchone()[0]

    def get_mutual_fund_official_returns(self, fund_run: str, series: str = "") -> dict:
        """Rentabilidades oficiales de la CMF para un fondo/serie, del corte más reciente."""
        where = "WHERE fund_run = ?" + (" AND series = ?" if series else "")
        params = [str(fund_run)] + ([series] if series else [])
        row = self.conn.execute(f"""
            SELECT fecha, series, familia, clase_inversionista, ind_rar,
                   rent_nominal_1a, rent_real_1a, rent_nominal_3a_ann, rent_real_3a_ann,
                   rent_nominal_5a_ann, rent_real_5a_ann, rent_nominal_10a_ann,
                   rent_real_10a_ann, rent_nominal_ytd, rent_real_ytd
            FROM cmf_mutual_fund_returns {where}
            ORDER BY fecha DESC, series LIMIT 1
        """, params).fetchone()
        if not row:
            return {}
        cols = ["fecha", "series", "familia", "clase_inversionista", "ind_rar",
                "nominal_1a", "real_1a", "nominal_3a", "real_3a", "nominal_5a", "real_5a",
                "nominal_10a", "real_10a", "nominal_ytd", "real_ytd"]
        return dict(zip(cols, row))

    def get_mutual_fund_returns_corte(self) -> str:
        """Corte más reciente de las rentabilidades oficiales, o '' si no hay."""
        row = self.conn.execute(
            "SELECT MAX(fecha) FROM cmf_mutual_fund_returns").fetchone()
        return str(row[0]) if row and row[0] else ""

    def upsert_mutual_fund_fees(self, records: list) -> int:
        """Carga la estructura de costos. Idempotente por período."""
        if not records:
            return 0
        cols = ["periodo", "fund_run", "series", "fund_name", "agf_name", "currency",
                "com_colocacion_max", "com_colocacion_min", "com_colocacion_unico",
                "remuneracion_max", "remuneracion_min", "remuneracion_unico",
                "gasto_operacional"]
        for per in {r["periodo"] for r in records}:
            self.conn.execute("DELETE FROM cmf_mutual_fund_fees WHERE periodo = ?", [per])
        fast_executemany(self.conn, 
            f"INSERT INTO cmf_mutual_fund_fees ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            [[r.get(c) for c in cols] for r in records])
        return len(records)

    def get_mutual_fund_fees(self, fund_run: str, series: str = "") -> dict:
        """
        Costos de un fondo/serie, del período más reciente.

        Devuelve los porcentajes ya en escala 0-100 y, por separado, lo que YA está dentro del
        valor cuota (`carga_interna`) y lo que se le cobra al partícipe (`com_colocacion`).
        """
        where = "WHERE fund_run = ?" + (" AND series = ?" if series else "")
        params = [str(fund_run)] + ([series] if series else [])
        row = self.conn.execute(f"""
            SELECT periodo, series, com_colocacion_max, com_colocacion_min,
                   com_colocacion_unico, remuneracion_max, remuneracion_min,
                   remuneracion_unico, gasto_operacional
            FROM cmf_mutual_fund_fees {where}
            ORDER BY periodo DESC LIMIT 1
        """, params).fetchone()
        if not row:
            return {}
        pct = lambda v: None if v is None else v * 100.0
        # Se prefiere el valor "único" cuando existe; si no, el máximo, que es el peor caso
        # para el partícipe y así la cifra nunca queda optimista.
        rem = row[7] if row[7] is not None else row[5]
        com = row[4] if row[4] is not None else row[2]
        return {
            "periodo": row[0], "series": row[1],
            "com_colocacion": pct(com),
            "com_es_unico": row[4] is not None,
            "remuneracion": pct(rem),
            "rem_es_unico": row[7] is not None,
            "gasto_operacional": pct(row[8]),
            "carga_interna": pct((rem or 0) + (row[8] or 0)),
        }

    def _ffmm_vc_clp_cte(self) -> str:
        """
        CTE `q` con el valor cuota EN PESOS de cada fila.

        La metodología de la CMF (Documento Metodológico de Rentabilidades, §3.1) es
        explícita: "Si el fondo está denominado en moneda extranjera, los valores cuotas son
        convertidos a pesos mediante el tipo de cambio del último día del mes actual". Sin
        convertir, un fondo en dólares mide su retorno en dólares y no es comparable con el
        resto — medido contra las cifras oficiales, la desviación era de +2,8 pp de mediana
        en los 298 pares en dólares, mientras que en pesos calzaba exacto.

        OJO CON LA FECHA DEL TIPO DE CAMBIO: se usa la primera observación POSTERIOR al
        corte, no la del mismo día. El "dólar observado" del BCCh publicado el día D refleja
        las transacciones del día D-1, así que el tipo de cambio que corresponde al último día
        del mes es el publicado el día hábil siguiente. Esto no es una interpretación: se
        dedujo de los datos. Con la fecha del mismo día quedaba un residuo constante de
        +1,20 pp en TODOS los fondos en dólares; despejando la razón implícita daba 0,973129,
        que coincide con el par 2025-04-01 → 2026-04-01 hasta el sexto decimal.

        Si no hay dato posterior (el corte más reciente), se cae al último disponible. Si no
        hubiera ninguno, `vc_clp` queda NULL y la fila se descarta: mezclar monedas sería peor
        que perder el dato.
        """
        return f"""
            tc AS (
                SELECT c.date AS corte,
                       COALESCE(
                           (SELECT o.value FROM observations o
                            WHERE o.series_id = '{self.KPI_SERIES["usd_clp"]}'
                              AND o.date > c.date
                            ORDER BY o.date ASC LIMIT 1),
                           (SELECT o.value FROM observations o
                            WHERE o.series_id = '{self.KPI_SERIES["usd_clp"]}'
                              AND o.date <= c.date
                            ORDER BY o.date DESC LIMIT 1)
                       ) AS usd_clp
                FROM (SELECT DISTINCT date FROM cmf_mutual_fund_quota_values) c
            ),
            q AS (
                SELECT v.*,
                       CASE WHEN v.currency = 'PESOS' THEN v.quota_value
                            ELSE v.quota_value * tc.usd_clp END AS vc_clp
                FROM cmf_mutual_fund_quota_values v
                JOIN tc ON tc.corte = v.date
            )
        """

    def get_mutual_fund_periods(self) -> list[tuple]:
        """Períodos (año, mes) con cartera de fondos mutuos, del más reciente hacia atrás."""
        return [(int(y), int(m)) for y, m in self.conn.execute(
            "SELECT DISTINCT period_year, period_month FROM cmf_mutual_fund_portfolio "
            "ORDER BY period_year DESC, period_month DESC").fetchall()]

    def get_mutual_fund_portfolio_types(self, period: tuple) -> list[dict]:
        """Tipos de cartera del período, con cuántos fondos y posiciones tiene cada uno."""
        rows = self.conn.execute("""
            SELECT portfolio_type, COUNT(*) AS n_pos, COUNT(DISTINCT fund_run) AS n_fondos
            FROM cmf_mutual_fund_portfolio
            WHERE period_year = ? AND period_month = ?
            GROUP BY portfolio_type ORDER BY n_pos DESC
        """, [period[0], period[1]]).fetchall()
        return [{"tipo": r[0], "n_pos": int(r[1]), "n_fondos": int(r[2])} for r in rows]

    def get_mutual_funds_ranking(self, period: tuple, portfolio_type=None,
                                 limit: int = 25) -> list[dict]:
        """
        Fondos ordenados por número de posiciones en el período.

        Se ordena por CONTEO y no por monto a propósito: contar filas es inequívoco, mientras
        que la unidad exacta del campo de monto depende del diccionario FFM de la CMF, que no
        está transcrito. El monto se devuelve igual, como referencia secundaria.
        """
        where = "WHERE period_year = ? AND period_month = ?"
        params = [period[0], period[1]]
        if portfolio_type:
            where += " AND portfolio_type = ?"
            params.append(portfolio_type)
        rows = self.conn.execute(f"""
            SELECT fund_run,
                   ANY_VALUE(fund_name) AS fund_name,
                   COUNT(*)             AS n_pos,
                   COUNT(DISTINCT {self._ffm('tipo')}) AS n_tipos,
                   COUNT(DISTINCT {self._ffm('pais')})             AS n_paises,
                   SUM(TRY_CAST({self._ffm('monto')} AS DOUBLE))   AS monto
            FROM cmf_mutual_fund_portfolio
            {where}
            GROUP BY fund_run
            ORDER BY n_pos DESC
            LIMIT ?
        """, params + [int(limit)]).fetchall()
        return [{"fund_run": r[0], "fund_name": r[1], "n_pos": int(r[2]),
                 "n_tipos": int(r[3]), "n_paises": int(r[4]), "monto": r[5]} for r in rows]

    def get_mutual_funds_options(self, period: tuple) -> list[dict]:
        """Fondos del período para poblar el selector, ordenados por nombre."""
        rows = self.conn.execute("""
            SELECT fund_run, ANY_VALUE(fund_name) AS fund_name, COUNT(*) AS n_pos
            FROM cmf_mutual_fund_portfolio
            WHERE period_year = ? AND period_month = ?
            GROUP BY fund_run ORDER BY fund_name
        """, [period[0], period[1]]).fetchall()
        return [{"fund_run": r[0], "fund_name": r[1], "n_pos": int(r[2])} for r in rows]

    def get_mutual_fund_composition(self, fund_run: str, period: tuple) -> dict:
        """
        Composición de la cartera de un fondo: por tipo de cartera, instrumento, país y moneda.

        El % se calcula sobre el NÚMERO DE POSICIONES, que es la magnitud afirmable sin el
        diccionario FFM. El monto acompaña como referencia.
        """
        base = ("FROM cmf_mutual_fund_portfolio "
                "WHERE fund_run = ? AND period_year = ? AND period_month = ?")
        params = [str(fund_run), period[0], period[1]]

        def eje(expr):
            rows = self.conn.execute(f"""
                SELECT CASE WHEN portfolio_type NOT IN {self._carteras_sql()}
                            THEN '(cartera no mapeada)'
                            ELSE COALESCE(NULLIF({self._ffm(expr)}, ''), '(sin dato)') END AS k,
                       COUNT(*) AS n,
                       SUM(TRY_CAST({self._ffm('monto')} AS DOUBLE)) AS monto
                {base} GROUP BY k ORDER BY n DESC
            """, params).fetchall()
            total = sum(int(r[1]) for r in rows) or 1
            return [{"clave": r[0], "n": int(r[1]), "monto": r[2],
                     "pct": int(r[1]) * 100.0 / total} for r in rows]

        cab = self.conn.execute(f"""
            SELECT ANY_VALUE(fund_name), COUNT(*), COUNT(DISTINCT portfolio_type),
                   SUM(TRY_CAST({self._ffm('monto')} AS DOUBLE))
            {base}
        """, params).fetchone()
        if not cab or not cab[1]:
            return {}

        carteras = self.conn.execute(f"""
            SELECT portfolio_type, COUNT(*) AS n,
                   SUM(TRY_CAST({self._ffm('monto')} AS DOUBLE)) AS monto
            {base} GROUP BY portfolio_type ORDER BY n DESC
        """, params).fetchall()
        tot_c = sum(int(r[1]) for r in carteras) or 1

        return {
            "fund_run": str(fund_run),
            "fund_name": cab[0],
            "n_pos": int(cab[1]),
            "n_carteras": int(cab[2] or 0),
            "monto": cab[3],
            "por_cartera": [{"clave": r[0], "n": int(r[1]), "monto": r[2],
                             "pct": int(r[1]) * 100.0 / tot_c} for r in carteras],
            "por_tipo": eje("tipo"),
            "por_pais": eje("pais"),
            "por_moneda": eje("moneda"),
        }

    def get_mutual_fund_positions(self, fund_run: str, period: tuple,
                                  limit: int = 20) -> list[dict]:
        """Posiciones individuales más grandes del fondo, para auditar la composición."""
        rows = self.conn.execute(f"""
            SELECT portfolio_type,
                   {self._ffm('tipo')} AS tipo,
                   {self._ffm('nemotecnico')}      AS nemotecnico,
                   {self._ffm('pais')}             AS pais,
                   {self._ffm('moneda')}           AS moneda,
                   TRY_CAST({self._ffm('monto')} AS DOUBLE) AS monto
            FROM cmf_mutual_fund_portfolio
            WHERE fund_run = ? AND period_year = ? AND period_month = ?
            ORDER BY monto DESC NULLS LAST
            LIMIT ?
        """, [str(fund_run), period[0], period[1], int(limit)]).fetchall()
        cols = ["cartera", "tipo", "nemotecnico", "pais", "moneda", "monto"]
        return [dict(zip(cols, r)) for r in rows]
