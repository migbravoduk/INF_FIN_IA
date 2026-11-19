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

UF_SERIE = "F073.UFF.PRE.Z.D"
USD_SERIE = "F073.TCO.PRE.Z.D"

# Cartera nacional de fondos mutuos (NACI): RUT del emisor y valorización al cierre. La
# valorización viene en MILES de la moneda del FONDO (no del instrumento): verificado en
# 202609 contra el patrimonio informado — la suma por fondo da ~1.000 × patrimonio en MM.
_FFM_RUT = "ltrim(json_extract_string(p.raw_data, '$.FFM_6010211'), '0')"
_FFM_VAL = "try_cast(json_extract_string(p.raw_data, '$.FFM_6011200') AS DOUBLE)"
_FFM_NEMO = "json_extract_string(p.raw_data, '$.FFM_6010100')"

TIPO_LABEL = {"estado": "Estado", "banco": "Banco", "emisor": "Emisor con EEFF",
              "agf": "Administradora de fondos", "aseguradora_vida": "Seguros de vida",
              "aseguradora_generales": "Seguros generales", "corredora": "Corredora",
              "agente_valores": "Agente de valores", "afp": "AFP",
              "administradora_mutuos": "Administradora de mutuos hipotecarios",
              "fondo_mutuo": "Fondo mutuo"}


def _fx_cte(periods_sql: str) -> str:
    """CTE `fx(period, uf, usd)`: UF y dólar observado al último dato de cada mes."""
    return f"""
        meses AS ({periods_sql}),
        fx AS (
            SELECT m.period,
                   (SELECT arg_max(o.value, o.date) FROM observations o WHERE o.series_id = '{UF_SERIE}'
                      AND o.date <= last_day(make_date(m.period // 100, m.period % 100, 1))) AS uf,
                   (SELECT arg_max(o.value, o.date) FROM observations o WHERE o.series_id = '{USD_SERIE}'
                      AND o.date <= last_day(make_date(m.period // 100, m.period % 100, 1))) AS usd
            FROM meses m)"""


# ---------------------------------------------------------------------------------------
# Emisor canónico por nemotécnico (B.1).
#
# Las compañías no siempre informan el mismo RUT de emisor para un mismo instrumento: en
# 202609 había 169 nemotécnicos con RUT contradictorio. Son errores de digitación (EuroAmerica
# informó el bono BCMPC-S con el RUT de Empresas CMPC; las otras 4 tenedoras y los fondos
# mutuos, con el de Inversiones CMPC, que es la emisora) y RUT de bancos absorbidos que
# algunas carteras siguen usando. La tabla se conserva tal cual; la corrección se aplica al leer.
#
# Regla, por (período, nemotécnico): gana el RUT informado en MÁS posiciones de B.1 (las dos
# ramas juntas); en empate, el que usa la cartera nacional de fondos mutuos para ese
# nemotécnico en el mismo mes; si sigue el empate, el menor RUT. Los mutuos hipotecarios
# (MHA, MHB) y el leasing (CLEAS) quedan fuera: su nemotécnico es propio de cada
# originador y el RUT informado se respeta.
# ---------------------------------------------------------------------------------------
TIPOS_ORIGINADOR = ("MHA", "MHB", "CLEAS")
_TIPOS_ORIG_SQL = "('MHA', 'MHB', 'CLEAS')"


def emisor_canonico_cte(periods_sql: str, rut: str | None = None) -> str:
    """
    CTE `canon(period, nemo, rut_canon)` con el emisor canónico de cada nemotécnico de B.1
    en los períodos de `periods_sql` (una consulta que devuelve la columna `period`).

    `rut` (solo dígitos) restringe el cálculo a los nemotécnicos que alguna compañía informó
    con ese RUT: el canónico siempre es uno de los RUT informados, así que basta para saber
    qué posiciones entran o salen de ese emisor, y evita recorrer todo el mes.
    """
    filtro = ""
    if rut:
        if not str(rut).isdigit():
            raise ValueError(f"RUT inválido: {rut!r}")
        filtro = f"""AND upper(trim(fi.nemotecnico)) IN (
                SELECT upper(trim(x.nemotecnico)) FROM cmf_insurer_portfolio_fixed_income x
                WHERE x.period IN (SELECT period FROM canon_p) AND ltrim(x.rut_emisor, '0') = '{rut}')"""
    return f"""
        canon_p AS ({periods_sql}),
        canon_n AS (
            SELECT fi.period, upper(trim(fi.nemotecnico)) AS nemo, ltrim(fi.rut_emisor, '0') AS rut,
                   COUNT(*) AS n
            FROM cmf_insurer_portfolio_fixed_income fi
            WHERE fi.period IN (SELECT period FROM canon_p)
              AND fi.tipo_instrumento NOT IN {_TIPOS_ORIG_SQL}
              AND fi.rut_emisor IS NOT NULL AND fi.nemotecnico IS NOT NULL AND trim(fi.nemotecnico) <> ''
              {filtro}
            GROUP BY 1, 2, 3),
        -- Solo hace falta resolver los nemotécnicos con más de un RUT informado: en el resto el
        -- canónico es el informado (y `emisor_efectivo` cae a él). Mantiene la consulta liviana.
        canon_m AS (
            SELECT * FROM canon_n
            WHERE (period, nemo) IN (SELECT period, nemo FROM canon_n GROUP BY 1, 2 HAVING COUNT(*) > 1)),
        canon_f AS (
            SELECT p.period_year * 100 + p.period_month AS period, upper(trim({_FFM_NEMO})) AS nemo,
                   {_FFM_RUT} AS rut, COUNT(*) AS n_ffm
            FROM cmf_mutual_fund_portfolio p
            WHERE p.portfolio_type = 'NACI'
              AND p.period_year * 100 + p.period_month IN (SELECT DISTINCT period FROM canon_m)
              AND upper(trim({_FFM_NEMO})) IN (SELECT DISTINCT nemo FROM canon_m)
            GROUP BY 1, 2, 3),
        canon AS (
            SELECT period, nemo, rut AS rut_canon FROM (
                SELECT n.period, n.nemo, n.rut,
                       ROW_NUMBER() OVER (PARTITION BY n.period, n.nemo
                                          ORDER BY n.n DESC, coalesce(f.n_ffm, 0) DESC, n.rut) AS rk
                FROM canon_m n LEFT JOIN canon_f f USING (period, nemo, rut))
            WHERE rk = 1)"""


def canon_join(alias: str = "fi") -> str:
    """LEFT JOIN de B.1 (`alias`) contra `canon`; usar con `emisor_efectivo(alias)`."""
    return (f"LEFT JOIN canon ON canon.period = {alias}.period "
            f"AND canon.nemo = upper(trim({alias}.nemotecnico)) "
            f"AND {alias}.tipo_instrumento NOT IN {_TIPOS_ORIG_SQL}")


def emisor_efectivo(alias: str = "fi") -> str:
    """RUT del emisor que se usa al leer B.1: el canónico si existe, si no el informado."""
    return f"coalesce(canon.rut_canon, ltrim({alias}.rut_emisor, '0'))"


def issuer_options(db, limit: int = 600) -> list[dict]:
    """
    Emisores para el selector: los que tienen posiciones en el último período de seguros
    (B.1) o de fondos mutuos, con nombre cuando se conoce. Ordenados por presencia.
    """
    per_seg = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    per_ffm = db.conn.execute(
        "SELECT MAX(period_year * 100 + period_month) FROM cmf_mutual_fund_portfolio").fetchone()[0]
    if not per_seg and not per_ffm:
        return []
    rows = db.conn.execute(f"""
        WITH {emisor_canonico_cte(f"SELECT {int(per_seg or 0)} AS period")},
        seg AS (
            SELECT {emisor_efectivo()} rut, COUNT(*) n FROM cmf_insurer_portfolio_fixed_income fi
            {canon_join()}
            WHERE fi.period = ? AND fi.rut_emisor IS NOT NULL GROUP BY 1),
        ffm AS (
            SELECT {_FFM_RUT} rut, COUNT(*) n FROM cmf_mutual_fund_portfolio p
            WHERE p.portfolio_type = 'NACI' AND p.period_year * 100 + p.period_month = ?
            GROUP BY 1),
        fondos AS (SELECT fund_run rut, arg_max(fund_name, date) nombre
                   FROM cmf_mutual_fund_quota_values GROUP BY 1),
        u AS (SELECT rut, SUM(n) n FROM (SELECT * FROM seg UNION ALL SELECT * FROM ffm)
              WHERE rut IS NOT NULL AND rut NOT IN ('', '0') GROUP BY 1)
        SELECT u.rut, coalesce(e.nombre_corto, e.nombre_canonico,
                               CASE WHEN f.nombre IS NOT NULL THEN 'Fondo mutuo ' || f.nombre END) nombre,
               u.n
        FROM u LEFT JOIN dim_entidad e ON e.rut = u.rut LEFT JOIN fondos f ON f.rut = u.rut
        ORDER BY (nombre IS NULL), u.n DESC
        LIMIT ?
    """, [per_seg or 0, per_ffm or 0, limit]).fetchall()
    return [{"rut": r[0], "nombre": r[1], "n": int(r[2])} for r in rows]


def issuer_holdings(db, rut: str, n_periods: int = 13) -> dict:
    """
    Cuánto tienen invertido los institucionales en un emisor, mes a mes.

    - Seguros (B.1, renta fija): nominal VIGENTE convertido a pesos con la UF y el dólar
      del cierre de cada mes. Es valor par, no de mercado (B.1 no trae valorización
      reconciliada); sirve para ver tamaño y variación. Instrumentos en otras monedas sin
      tipo de cambio cargado (euro) se cuentan pero no se suman.
    - Fondos mutuos (cartera nacional): valorización de mercado al cierre, en MM$; los
      fondos en dólares se pasan a pesos con el dólar observado del mes.
    - AFP: cartera desagregada de la SP (valor de mercado, MM$), cruzada por nombre de
      emisor. La SP publica con ~4 meses de rezago, así que este bloque usa SUS últimos
      meses y no la ventana de seguros y fondos mutuos (si no, el "vs 12 meses" quedaba
      vacío y los últimos meses en blanco).

    Devuelve {rut, nombre, tipo, periodos, seguros: {serie, por_tenedor}, ffmm: {...}}.
    """
    rut = str(rut).strip().replace(".", "").split("-")[0].lstrip("0")
    if not rut.isdigit():
        return {"rut": rut, "nombre": None, "periodos": [], "seguros": None, "ffmm": None}
    ent = db.conn.execute("""SELECT coalesce(nombre_corto, nombre_canonico), nombre_canonico, tipo
                             FROM dim_entidad WHERE rut = ?""", [rut]).fetchone()
    fondo = db.conn.execute("""SELECT arg_max(fund_name, date), arg_max(agf_name, date)
                               FROM cmf_mutual_fund_quota_values WHERE fund_run = ?""", [rut]).fetchone()
    if ent:
        nombre, razon, tipo = ent
    elif fondo and fondo[0]:
        nombre, razon, tipo = f"Fondo mutuo {fondo[0]}", fondo[1], "fondo_mutuo"
    else:
        nombre, razon, tipo = None, None, None

    seg_per = [r[0] for r in db.conn.execute(
        "SELECT DISTINCT period FROM cmf_insurer_portfolio_fixed_income ORDER BY 1 DESC LIMIT ?",
        [n_periods]).fetchall()]
    ffm_per = [r[0] for r in db.conn.execute(
        """SELECT DISTINCT period_year * 100 + period_month FROM cmf_mutual_fund_portfolio
           ORDER BY 1 DESC LIMIT ?""", [n_periods]).fetchall()]
    periodos = sorted(set(seg_per) | set(ffm_per))[-n_periods:]
    if not periodos:
        return {"rut": rut, "nombre": nombre, "periodos": [], "seguros": None, "ffmm": None}
    lista = ",".join(str(int(p)) for p in periodos)
    meses = f"SELECT unnest([{lista}]) AS period"

    # --- Seguros: por compañía y mes ---------------------------------------------------
    # Posiciones donde este RUT es el emisor informado o el canónico (F3 del plan del 19-nov):
    # `efe` es el que se usa; `inf` es lo que escribió la compañía.
    seg_all = db.conn.execute(f"""
        WITH {_fx_cte(meses)},
        {emisor_canonico_cte(meses, rut=rut)},
        b AS (
            SELECT fi.period, fi.rut AS tenedor, fi.insurance_type AS ramo,
                   fi.unidad_monetaria AS mon, upper(trim(fi.nemotecnico)) AS nemo,
                   ltrim(fi.rut_emisor, '0') AS inf, {emisor_efectivo()} AS efe,
                   CASE WHEN coalesce(fi.valor_nominal_vig, 0) > 0 THEN fi.valor_nominal_vig
                        ELSE fi.valor_nominal END AS nominal
            FROM cmf_insurer_portfolio_fixed_income fi
            {canon_join()}
            WHERE fi.period IN ({lista})
              AND (ltrim(fi.rut_emisor, '0') = ? OR canon.rut_canon = ?))
        SELECT b.period, b.tenedor, b.ramo, b.mon, b.nemo, b.inf, b.efe,
               CASE b.mon WHEN 'UF' THEN b.nominal * fx.uf
                          WHEN '$$' THEN b.nominal
                          WHEN 'PROM' THEN b.nominal * fx.usd END / 1e6 AS mm
        FROM b JOIN fx USING (period)
    """, [rut, rut]).fetchdf()
    seg_efe = seg_all[seg_all.efe == rut]
    if seg_efe.empty:
        seg = seg_efe.assign(pos=0, sin_fx=0)[["period", "tenedor", "ramo", "pos", "mm", "sin_fx"]]
    else:
        seg = (seg_efe.assign(sin_fx=~seg_efe.mon.isin(["UF", "$$", "PROM"]))
               .groupby(["period", "tenedor"], as_index=False)
               .agg(ramo=("ramo", "first"), pos=("mon", "size"), mm=("mm", "sum"), sin_fx=("sin_fx", "sum"))
               .sort_values(["period", "tenedor"]))

    # Reasignaciones del último mes de seguros: lo que las compañías informaron con otro RUT y la
    # regla atribuye a este emisor (entran), y lo informado con este RUT que va a otro (salen).
    reasignados = None
    if not seg_all.empty:
        ult_seg = int(seg_all.period.max())
        u = seg_all[seg_all.period == ult_seg]
        ent_ = u[(u.efe == rut) & (u.inf != rut)]
        sal_ = u[(u.inf == rut) & (u.efe != rut)]
        if len(ent_) or len(sal_):
            destinos = sal_.groupby("efe").mm.sum().sort_values(ascending=False)
            nombres_d = dict(db.conn.execute(
                "SELECT rut, coalesce(nombre_corto, nombre_canonico) FROM dim_entidad").fetchall())
            reasignados = {
                "period": ult_seg,
                "entran_pos": int(len(ent_)), "entran_mm": float(ent_.mm.sum()),
                "entran_nemos": sorted(ent_.nemo.unique().tolist())[:8],
                "salen_pos": int(len(sal_)), "salen_mm": float(sal_.mm.sum()),
                "salen_nemos": sorted(sal_.nemo.unique().tolist())[:8],
                "salen_a": [{"rut": r, "nombre": nombres_d.get(r), "mm": float(v)}
                            for r, v in destinos.head(3).items()],
            }

    # --- Fondos mutuos: por fondo y mes ----------------------------------------------------
    ffm = db.conn.execute(f"""
        WITH {_fx_cte(meses)},
        cur AS (SELECT fund_run, arg_max(currency, date) currency, arg_max(agf_name, date) agf
                FROM cmf_mutual_fund_quota_values GROUP BY 1),
        h AS (
            SELECT p.period_year * 100 + p.period_month AS period, p.fund_run, p.fund_name,
                   {_FFM_VAL} / 1e3 AS valor_mm_fondo
            FROM cmf_mutual_fund_portfolio p
            WHERE p.portfolio_type = 'NACI' AND {_FFM_RUT} = ?
              AND p.period_year * 100 + p.period_month IN ({lista}))
        SELECT h.period, h.fund_run AS tenedor, any_value(h.fund_name) fondo, any_value(cur.agf) agf,
               COUNT(*) pos,
               SUM(h.valor_mm_fondo * CASE WHEN cur.currency = 'DOLAR' THEN fx.usd ELSE 1 END) mm
        FROM h JOIN fx USING (period) LEFT JOIN cur USING (fund_run)
        GROUP BY 1, 2 ORDER BY 1, 2
    """, [rut]).fetchdf()

    # --- AFP: por AFP y mes (cartera desagregada SP, por nombre de emisor) -------------------
    glosas = [g for g, r in mapa_emisores_sp(db).items() if r == rut]
    afp, afp_per = None, []
    if glosas:
        afp_per = [r[0] for r in db.conn.execute(
            """SELECT DISTINCT period FROM sp_cartera_detalle WHERE tabla = 'emisor_nac'
               ORDER BY 1 DESC LIMIT ?""", [n_periods]).fetchall()][::-1]
        lista_afp = ",".join(str(int(p)) for p in afp_per) or "0"
        afp = db.conn.execute(f"""
            SELECT period, afp AS tenedor, string_agg(DISTINCT fondo, ', ' ORDER BY fondo) AS fondos,
                   COUNT(*) AS pos, SUM(valor) AS mm
            FROM sp_cartera_detalle
            WHERE tabla = 'emisor_nac' AND columna = 'total' AND metrica = 'monto_pesos'
              AND afp <> 'TOTAL' AND period IN ({lista_afp})
              AND glosa IN ({','.join('?' * len(glosas))})
            GROUP BY 1, 2 ORDER BY 1, 2""", glosas).fetchdf()

    from api.insurer_glossary import clean_company_name
    nombres_cias = dict(db.conn.execute("""
        SELECT rut, arg_max(company_name, period) FROM cmf_insurer_portfolio_control
        WHERE company_name <> '' GROUP BY 1""").fetchall())

    def bloque(df, etiqueta, periodos=periodos):
        if df.empty:
            return None
        tot = df.groupby("period").agg(mm=("mm", "sum"), pos=("pos", "sum"),
                                       tenedores=("tenedor", "nunique"))
        serie = [{"period": int(p), "mm": float(tot.mm.get(p, 0.0)) if p in tot.index else None,
                  "pos": int(tot.pos.get(p, 0)) if p in tot.index else 0,
                  "tenedores": int(tot.tenedores.get(p, 0)) if p in tot.index else 0}
                 for p in periodos]
        ult = int(df.period.max())
        prev = max((p for p in periodos if p < ult), default=None)
        hace12 = ult - 100 if (ult - 100) in periodos else None
        w = df.pivot_table(index="tenedor", columns="period", values="mm", aggfunc="sum")
        filas = []
        for t, r in w.iterrows():
            v1 = r.get(ult)
            v0 = r.get(prev) if prev else None
            v12 = r.get(hace12) if hace12 else None
            v1 = None if v1 != v1 else v1
            v0 = None if v0 != v0 else v0
            v12 = None if v12 != v12 else v12
            if v1 is None and v0 is None:
                continue
            meta = df[df.tenedor == t].iloc[-1]
            if etiqueta == "seguros":
                nom = clean_company_name(nombres_cias.get(t, ""), str(t)) or f"RUT {t}"
                sub = "vida" if meta.ramo == "vida" else "generales"
            elif etiqueta == "afp":
                nom, sub = f"AFP {str(t).title()}", f"fondos {meta.fondos}"
            else:
                nom = str(meta.fondo).strip().title()
                agf = " ".join(str(meta.agf).upper().split()) if meta.agf == meta.agf and meta.agf else ""
                sub = (agf.replace("ADMINISTRADORA GENERAL DE FONDOS", "AGF").title()
                       .replace("Agf", "AGF").replace("S.A.", "S.A."))
            filas.append({"tenedor": str(t), "nombre": nom, "sub": sub, "ult": v1, "prev": v0,
                          "hace12": v12,
                          "d_prev": (v1 or 0) - (v0 or 0) if prev else None,
                          "d_12": (v1 or 0) - (v12 or 0) if hace12 else None})
        filas.sort(key=lambda f: -(f["ult"] or 0))
        total_ult = sum(f["ult"] or 0 for f in filas)
        for f in filas:
            f["peso"] = (f["ult"] or 0) / total_ult * 100 if total_ult else None
        return {"serie": serie, "tenedores": filas, "ult": ult, "prev": prev, "hace12": hace12,
                "sin_fx": int(df["sin_fx"].sum()) if "sin_fx" in df else 0}

    return {"rut": rut, "nombre": nombre, "razon": razon, "tipo": tipo,
            "tipo_label": TIPO_LABEL.get(tipo or "", tipo),
            "periodos": periodos, "seguros": bloque(seg, "seguros"), "ffmm": bloque(ffm, "ffmm"),
            "afp": bloque(afp, "afp", afp_per) if afp is not None else None, "afp_glosas": glosas,
            "reasignados": reasignados}


def common_issuers(db, limit: int = 40) -> dict:
    """
    Emisores con instrumentos a la vez en seguros (B.1) y fondos mutuos (NACI), con el monto
    expuesto en esos instrumentos, en MM$ al cierre de cada fuente:
    seguros = nominal vigente a pesos con la UF y el dólar del cierre (valor par, no de mercado);
    fondos = valorización de mercado, los fondos en dólares a pesos con el dólar del mes.
    Instrumentos de seguros en otras monedas sin tipo de cambio (euro) no se suman.
    """
    per_seg = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    per_ffm = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_holdings").fetchone()[0]
    if not per_seg or not per_ffm:
        return {"rows": [], "per_seg": per_seg, "per_ffm": per_ffm}
    # DISTINCT: seguros y fondos suelen cerrar el mismo mes; con el período repetido, `fx` tendría
    # dos filas iguales y el cruce duplicaría montos y posiciones.
    rows = db.conn.execute(f"""
        WITH {_fx_cte("SELECT DISTINCT unnest([?, ?]) AS period")},
        {emisor_canonico_cte(f"SELECT {int(per_seg)} AS period")},
        seg AS (
            SELECT upper(trim(fi.nemotecnico)) nemo, {emisor_efectivo()} rut,
                   COUNT(*) pos_seg, COUNT(DISTINCT fi.rut) cias,
                   SUM(CASE fi.unidad_monetaria WHEN 'UF' THEN nom * fx.uf WHEN '$$' THEN nom
                                                WHEN 'PROM' THEN nom * fx.usd END) / 1e6 AS mm_seg
            FROM (SELECT *, CASE WHEN coalesce(valor_nominal_vig, 0) > 0 THEN valor_nominal_vig
                                 ELSE valor_nominal END AS nom
                  FROM cmf_insurer_portfolio_fixed_income) fi
            JOIN fx ON fx.period = fi.period
            {canon_join()}
            WHERE fi.period = ? AND fi.nemotecnico IS NOT NULL AND trim(fi.nemotecnico) <> ''
            GROUP BY 1, 2),
        cur AS (SELECT fund_run, arg_max(currency, date) currency
                FROM cmf_mutual_fund_quota_values GROUP BY 1),
        ffm AS (
            SELECT upper(trim({_FFM_NEMO})) nemo, COUNT(*) pos_ffm, COUNT(DISTINCT p.fund_run) fondos,
                   SUM({_FFM_VAL} / 1e3 * CASE WHEN cur.currency = 'DOLAR' THEN fx.usd ELSE 1 END) AS mm_ffm
            FROM cmf_mutual_fund_portfolio p
            JOIN fx ON fx.period = p.period_year * 100 + p.period_month
            LEFT JOIN cur ON cur.fund_run = p.fund_run
            WHERE p.period_year * 100 + p.period_month = ? AND p.portfolio_type = 'NACI'
              AND {_FFM_NEMO} IS NOT NULL
            GROUP BY 1)
        SELECT seg.rut, coalesce(e.nombre_corto, e.nombre_canonico) AS emisor, e.tipo,
               COUNT(DISTINCT seg.nemo) instrumentos, SUM(seg.cias) cias_seg, SUM(ffm.fondos) fondos_ffm,
               SUM(seg.pos_seg) pos_seg, SUM(ffm.pos_ffm) pos_ffm,
               SUM(seg.mm_seg) mm_seg, SUM(ffm.mm_ffm) mm_ffm
        FROM seg JOIN ffm USING (nemo)
        LEFT JOIN dim_entidad e ON e.rut = seg.rut
        GROUP BY 1, 2, 3
        ORDER BY coalesce(SUM(seg.mm_seg), 0) + coalesce(SUM(ffm.mm_ffm), 0) DESC
        LIMIT ?
    """, [per_seg, per_ffm, per_seg, per_ffm, limit]).fetchall()
    cols = ["rut", "emisor", "tipo", "instrumentos", "cias_seg", "fondos_ffm", "pos_seg", "pos_ffm",
            "mm_seg", "mm_ffm"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["mm_total"] = (d["mm_seg"] or 0) + (d["mm_ffm"] or 0)
        out.append(d)
    return {"rows": out, "per_seg": per_seg, "per_ffm": per_ffm}


# ---------------------------------------------------------------------------------------
# AFP: la cartera desagregada de la SP (sp_cartera_detalle, tabla 'emisor_nac') identifica al
# emisor solo por NOMBRE. Se cruza con dim_entidad / dim_entidad_alias por nombre normalizado,
# y config/sp_emisores.yaml fija a mano los que no calzan (Fisco, Banco Central, …).
# ---------------------------------------------------------------------------------------
_ABREV = {"CIA": "COMPANIA", "SOC": "SOCIEDAD", "ADM": "ADMINISTRADORA", "GRAL": "GENERAL",
          "ASIG": "ASIGNACION", "FAM": "FAMILIAR", "CONC": "CONCESIONARIA"}
_SUFIJOS = {"SA", "S", "A", "SPA", "LTDA", "LIMITADA", "Y", "CIA"}
_MAPA_SP: dict = {}


def _norm_emisor(nombre: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nombre or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s.replace("Ñ", "N"))
    tok = [_ABREV.get(t, t) for t in s.split()]
    while tok and tok[-1] in _SUFIJOS:
        tok.pop()
    return " ".join(tok)


def mapa_emisores_sp(db) -> dict[str, str]:
    """{glosa de emisor en la SP: rut} (cache por período más reciente)."""
    clave = db.conn.execute("SELECT MAX(period) FROM sp_cartera_detalle WHERE tabla = 'emisor_nac'").fetchone()[0]
    if _MAPA_SP.get("clave") == clave:
        return _MAPA_SP["mapa"]
    import yaml
    from pathlib import Path
    nombres = db.conn.execute("""
        SELECT rut, nombre_canonico FROM dim_entidad WHERE nombre_canonico IS NOT NULL
        UNION SELECT rut, nombre_corto FROM dim_entidad WHERE nombre_corto IS NOT NULL
        UNION SELECT rut, nombre FROM dim_entidad_alias""").fetchall()
    por_nombre: dict[str, set] = {}
    for rut, nom in nombres:
        por_nombre.setdefault(_norm_emisor(nom), set()).add(str(rut))
    manual = {}
    f = Path("config/sp_emisores.yaml")
    if f.exists():
        manual = {_norm_emisor(k): str(v) for k, v in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items()}
    glosas = [r[0] for r in db.conn.execute(
        "SELECT DISTINCT glosa FROM sp_cartera_detalle WHERE tabla = 'emisor_nac'").fetchall()]
    mapa = {}
    for g in glosas:
        n = _norm_emisor(g)
        if n in manual:
            mapa[g] = manual[n]
        elif len(por_nombre.get(n, ())) == 1:      # nombre ambiguo (dos RUT): no se asigna
            mapa[g] = next(iter(por_nombre[n]))
    _MAPA_SP.update(clave=clave, mapa=mapa)
    return mapa
