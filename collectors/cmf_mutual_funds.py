import logging
import json
import urllib.request
import urllib.parse
from datetime import date
import io

from collectors.cmf_fecu import fix_mojibake

logger = logging.getLogger(__name__)

class CMFMutualFundsCollector:
    """
    Recolector de información de Fondos Mutuos desde la CMF (Comisión para el Mercado Financiero).
    """

    BASE_URL = "https://www.cmfchile.cl/institucional/estadisticas/ffm_download.php"
    
    # Diferentes tipos de cartera según el formulario de la CMF
    CARTERAS = {
        "NACI": "Nacional",
        "EXTR": "Extranjera",
        "OPCI": "Opciones",
        "FUTU": "Futuros",
        "OPLA": "Opciones Lanzador"
    }

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def fetch_monthly_portfolio(self, year: int, month: int) -> list[dict]:
        """
        Descarga y parsea los archivos de cartera mensual para todos los tipos disponibles.
        """
        mm_str = f"{month:02d}"
        aa_str = str(year)
        
        all_records = []
        
        for cartera_id, cartera_name in self.CARTERAS.items():
            logger.info(f"Descargando cartera {cartera_name} ({cartera_id}) para {mm_str}/{aa_str}...")
            
            data = urllib.parse.urlencode({
                'mm': mm_str, 
                'aa': aa_str, 
                'cartera': cartera_id, 
                'btnConsulta': 'GENERAR ARCHIVO'
            }).encode('utf-8')
            
            req = urllib.request.Request(self.BASE_URL, data=data, headers=self.headers)
            try:
                with urllib.request.urlopen(req) as response:
                    raw = response.read()
                # El archivo viene en UTF-8. Decodificarlo como iso-8859-1 producía mojibake
                # en los nombres ('BI ESTRATÃ‰GICO' por 'BI ESTRATÉGICO'); se intenta UTF-8
                # primero y solo se cae a latin-1 si el archivo realmente no es UTF-8.
                try:
                    content = raw.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw.decode('iso-8859-1', errors='replace')
            except Exception as e:
                logger.error(f"Error descargando cartera {cartera_id}: {e}")
                continue
                
            lines = content.strip().split('\n')
            if len(lines) <= 1:
                logger.warning(f"Cartera {cartera_id} para {mm_str}/{aa_str} sin datos o archivo inválido.")
                continue
                
            headers = [h.strip() for h in lines[0].split(';')]
            
            for line in lines[1:]:
                if not line.strip():
                    continue
                cols = [c.strip() for c in line.split(';')]
                if len(cols) < 2:
                    continue
                    
                fund_run = cols[0]
                # fix_mojibake es idempotente: solo actúa si los bytes latin-1 del texto
                # son UTF-8 válido, que es la firma del doble encoding.
                fund_name = fix_mojibake(cols[1])
                
                # Crear diccionario con los datos raw
                raw_data = {}
                for i in range(2, min(len(headers), len(cols))):
                    raw_data[headers[i]] = cols[i]
                    
                all_records.append({
                    "period_year": year,
                    "period_month": month,
                    "portfolio_type": cartera_id,
                    "fund_run": fund_run,
                    "fund_name": fund_name,
                    "raw_data": json.dumps(raw_data)
                })
                
        logger.info(f"Total registros obtenidos: {len(all_records)}")
        return all_records

    # ------------------------------------------------------------------
    # Estadística diaria: patrimonio, partícipes y VALOR CUOTA por fondo/serie
    # ------------------------------------------------------------------
    #
    # Es la fuente que permite calcular RENDIMIENTOS, y además es la única que enlaza cada
    # fondo con su ADMINISTRADORA y su TIPO DE FONDO — el archivo de cartera no los trae.
    #
    # Una sola descarga con `admins=0` devuelve TODOS los fondos del día (mismo patrón que
    # la FECU de seguros: no hay que iterar por administradora).
    BPR_URL = "https://www.cmfchile.cl/institucional/estadisticas/fm.fm_bpr_dia.php"

    # El .xls es OLE2 legado y NO declara codepage, así que xlrd cae a cp1252 y revienta con
    # el byte 0x81. Hay que forzar latin-1 explícitamente.
    XLS_ENCODING = "latin-1"

    def fetch_daily_stats(self, fecha: date) -> dict:
        """
        Descarga la estadística de un día: una fila por (fondo, serie).

        Devuelve {"records": [...], "fund_types": {codigo: glosa}}. Las glosas de tipo de
        fondo NO se inventan: vienen en el pie del propio archivo de la CMF.
        """
        import xlrd

        data = urllib.parse.urlencode({
            "admins": "0",          # todas las administradoras
            "tipofondo": "0",       # todos los tipos
            "moneda": "0",          # todas las monedas
            "dia_select": str(fecha.day),
            "mes_peri": f"{fecha.month:02d}",
            "anio_peri": str(fecha.year),
            "out": "excel",         # es el ÚNICO formato que el endpoint sirve de verdad
            "lang": "es",
        }).encode("utf-8")

        req = urllib.request.Request(self.BPR_URL, data=data, headers=self.headers)
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read()

        # Un día sin publicación devuelve una página de error, no un .xls (magic OLE2).
        if not raw.startswith(b"\xd0\xcf\x11\xe0"):
            logger.warning(f"{fecha}: la CMF no devolvió un Excel (¿feriado o fin de semana?).")
            return {"records": [], "fund_types": {}}

        sheet = xlrd.open_workbook(file_contents=raw,
                                   encoding_override=self.XLS_ENCODING).sheet_by_index(0)

        def celda(i, j):
            return str(sheet.cell_value(i, j)).strip() if j < sheet.ncols else ""

        # La cabecera no está en una fila fija: se localiza por el rótulo 'RUN'.
        fila_cab = next((i for i in range(min(40, sheet.nrows))
                         if celda(i, 1).upper() == "RUN"), None)
        if fila_cab is None:
            logger.warning(f"{fecha}: no se encontró la cabecera del archivo.")
            return {"records": [], "fund_types": {}}

        registros, tipos = [], {}
        for i in range(fila_cab + 1, sheet.nrows):
            nombre, run = celda(i, 0), celda(i, 1)
            # El bloque de datos termina donde empiezan las notas al pie.
            if nombre.startswith("(") or (not nombre and not run):
                break
            if not run:
                continue
            registros.append({
                "date": fecha.isoformat(),
                "fund_name": fix_mojibake(nombre),
                "fund_run": self.normalizar_run(run),
                "fund_type": self._entero(celda(i, 2)),
                "agf_name": fix_mojibake(celda(i, 3)),
                "series": celda(i, 4),
                "currency": celda(i, 5),
                # El pie del archivo dice: "(1) Cifras en millones de pesos o de la moneda
                # que corresponda". Se guarda tal cual viene, en millones.
                "net_asset_value": self._numero(celda(i, 6)),
                "participants": self._entero(celda(i, 7)),
                "quota_value": self._numero(celda(i, 8)),
            })

        # Leyenda oficial del tipo de fondo, al pie ("1: FM DE INV.EN INST.DE DEUDA...").
        for i in range(max(0, sheet.nrows - 30), sheet.nrows):
            txt = fix_mojibake(celda(i, 0))
            if ":" in txt and txt.split(":", 1)[0].strip().isdigit():
                cod, glosa = txt.split(":", 1)
                tipos[int(cod.strip())] = glosa.strip().rstrip(",").strip()

        logger.info(f"{fecha}: {len(registros)} filas fondo/serie, {len(tipos)} tipos de fondo.")
        return {"records": registros, "fund_types": tipos}

    # ------------------------------------------------------------------
    # Estructura de comisiones, remuneraciones y gastos
    # ------------------------------------------------------------------
    #
    # QUÉ ES CADA COSTO Y DÓNDE ESTÁ YA DESCONTADO — importa para no restar dos veces:
    #
    #   · Remuneración de la AGF y gasto operacional: la nota (2) del propio archivo dice que
    #     "se devengará diariamente" sobre el patrimonio del fondo. O sea que YA están dentro
    #     del valor cuota: la rentabilidad que sale del valor cuota es NETA de estos dos.
    #   · Comisión de colocación: se le cobra al partícipe al entrar o al rescatar, NO sale del
    #     patrimonio. Esta sí hay que restarla aparte para saber qué le queda al inversionista.
    #
    # La nota (3) agrega que la comisión mínima y máxima dependen de los días de permanencia,
    # y la (1) que todas incluyen IVA.
    COMISIONES_URL = "https://www.cmfchile.cl/institucional/estadisticas/fm.fm_comision.php"

    def fetch_comisiones(self, year: int, month: int) -> list[dict]:
        """
        Descarga la estructura de costos de un mes: una fila por (fondo, serie).

        A diferencia del archivo de "costos asociados", este trae el RUN del fondo, que es lo
        que permite cruzarlo con el resto sin adivinar por nombre (79,9% de cobertura por RUN
        contra 41,4% por nombre).

        Los porcentajes vienen como FRACCIÓN (0,0119 = 1,19% anual), no como número de 0 a 100.
        """
        import xlrd

        q = urllib.parse.urlencode({
            "admins": "0", "tipofondo": "0", "moneda": "0",
            "mes": f"{month:02d}", "anio": str(year), "out": "excel", "lang": "es",
        })
        req = urllib.request.Request(f"{self.COMISIONES_URL}?{q}", headers=self.headers)
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read()

        if not raw.startswith(b"\xd0\xcf\x11\xe0"):
            logger.warning(f"Comisiones {year}-{month:02d}: la CMF no devolvió un Excel.")
            return []

        sheet = xlrd.open_workbook(file_contents=raw,
                                   encoding_override=self.XLS_ENCODING).sheet_by_index(0)

        def celda(i, j):
            return str(sheet.cell_value(i, j)).strip() if j < sheet.ncols else ""

        fila_cab = next((i for i in range(min(40, sheet.nrows))
                         if celda(i, 1).upper() == "RUN"), None)
        if fila_cab is None:
            logger.warning(f"Comisiones {year}-{month:02d}: no se encontró la cabecera.")
            return []

        periodo = f"{year}{month:02d}"
        registros = []
        for i in range(fila_cab + 1, sheet.nrows):
            nombre, run = celda(i, 0), celda(i, 1)
            if nombre.startswith("(") or (not nombre and not run):
                break
            if "-" not in run:          # las notas al pie no traen RUN con DV
                continue
            registros.append({
                "periodo": periodo,
                "fund_run": self.normalizar_run(run),
                "series": celda(i, 4),
                "fund_name": fix_mojibake(nombre),
                "agf_name": fix_mojibake(celda(i, 3)),
                "currency": celda(i, 5),
                "com_colocacion_max": self._numero(celda(i, 6)),
                "com_colocacion_min": self._numero(celda(i, 7)),
                "com_colocacion_unico": self._numero(celda(i, 8)),
                "remuneracion_max": self._numero(celda(i, 9)),
                "remuneracion_min": self._numero(celda(i, 10)),
                "remuneracion_unico": self._numero(celda(i, 11)),
                "gasto_operacional": self._numero(celda(i, 12)),
                # El plazo de pago de rescates (col. 13) NO se ingiere: viene corrupto DESDE
                # LA CMF — sus bytes son EF BF BD, o sea el carácter de reemplazo ya
                # codificado ("UN D<?> H<?>BIL"), así que el texto original se perdió antes de
                # generarse el archivo y no hay forma de reconstruirlo.
            })

        logger.info(f"Comisiones {year}-{month:02d}: {len(registros)} filas fondo/serie.")
        return registros

    # ------------------------------------------------------------------
    # Comparador de rentabilidad (rentabilidades OFICIALES de la CMF)
    # ------------------------------------------------------------------
    #
    # La CMF publica, junto a un documento metodológico, una base con las rentabilidades que
    # ella misma calcula por fondo y serie a 1/3/5/10 años, nominal y real, más el indicador
    # ajustado por riesgo y la clasificación por familia y clase de inversionista.
    #
    # Es un archivo por corte (columna `fm_fecha`), no una serie: se reemplaza entero en cada
    # ingesta. La URL lleva un `?ts=` que cambia en cada publicación, así que se descubre
    # leyendo la página en vez de fijarla.
    COMPARADOR_URL = ("https://www.cmfchile.cl/portal/estadisticas/626/"
                      "w4-article-91848.html")
    COMPARADOR_BASE = "https://www.cmfchile.cl/portal/estadisticas/626/"

    def fetch_comparador(self) -> dict:
        """
        Descarga y parsea la base del Comparador de rentabilidad.

        Devuelve {"returns": [...], "agfs": [...], "fecha": 'YYYYMMDD'}.
        """
        import openpyxl
        import re

        req = urllib.request.Request(self.COMPARADOR_URL, headers=self.headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = re.search(r'href="(articles-\d+_document_2\.xlsx[^"]*)"', html)
        if not m:
            logger.error("No se encontró el enlace de la base del comparador en la página.")
            return {"returns": [], "agfs": [], "fecha": None}

        url = self.COMPARADOR_BASE + m.group(1).replace("&amp;", "&")
        with urllib.request.urlopen(urllib.request.Request(url, headers=self.headers),
                                    timeout=120) as r:
            raw = r.read()
        logger.info(f"Comparador descargado: {len(raw):,} bytes")

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)

        def hoja(nombre):
            filas = list(wb[nombre].iter_rows(values_only=True))
            cab = [str(c).strip() if c is not None else "" for c in filas[0]]
            return [dict(zip(cab, f)) for f in filas[1:]], cab

        # --- Rentabilidades por serie de fondo ---
        rent, _ = hoja("rentabilidades_por_serie_fondo")
        num = lambda v: None if v in (None, "") else float(v)
        returns, fecha = [], None
        for f in rent:
            run = self.normalizar_run(str(f.get("fo_run") or ""))
            if not run:
                continue
            fecha = fecha or (str(f.get("fm_fecha")) if f.get("fm_fecha") else None)
            returns.append({
                "fecha": str(f.get("fm_fecha") or ""),
                "fund_run": run,
                "series": str(f.get("fm_serie") or "").strip(),
                "fund_name": fix_mojibake(str(f.get("nombre_fondo") or "")),
                "agf_name": fix_mojibake(str(f.get("nombre_agf") or "")),
                "familia": fix_mojibake(str(f.get("familia_visualizador") or "")),
                "familia_rar": fix_mojibake(str(f.get("familia_rar") or "")),
                "clase_inversionista": str(f.get("clase_inversionista") or "").strip(),
                "serie_digital": (1 if str(f.get("serie_digital") or "") == "1" else 0),
                "rent_nominal_1a": num(f.get("rent_nominal_1a")),
                "rent_real_1a": num(f.get("rent_real_1a")),
                "rent_nominal_3a_ann": num(f.get("rent_nominal_3a_ann")),
                "rent_real_3a_ann": num(f.get("rent_real_3a_ann")),
                "rent_nominal_5a_ann": num(f.get("rent_nominal_5a_ann")),
                "rent_real_5a_ann": num(f.get("rent_real_5a_ann")),
                "rent_nominal_10a_ann": num(f.get("rent_nominal_10a_ann")),
                "rent_real_10a_ann": num(f.get("rent_real_10a_ann")),
                "rent_nominal_ytd": num(f.get("rent_nominal_ytd")),
                "rent_real_ytd": num(f.get("rent_real_ytd")),
                "ind_rar": num(f.get("ind_rar_pond")),
            })

        # --- RUT de las administradoras (solo está en la hoja de benchmark por AGF) ---
        bench, _ = hoja("benchmark_fam_inversionista_agf")
        agfs = {}
        for f in bench:
            nombre = fix_mojibake(str(f.get("nombre_agf") or "")).strip()
            rut = str(f.get("rut_agf") or "").strip()
            if nombre and rut:
                agfs[nombre] = rut

        logger.info(f"Comparador: {len(returns)} series, {len(agfs)} AGF, corte {fecha}")
        return {"returns": returns,
                "agfs": [{"agf_name": n, "agf_rut": r} for n, r in sorted(agfs.items())],
                "fecha": fecha}

    def fetch_month_end_stats(self, year: int, month: int, max_tries: int = 8):
        """
        Corte de fin de mes: estadística del último día del mes CON publicación.

        No basta con el último día hábil: los feriados chilenos (18-19 de septiembre, 31 de
        diciembre en algunos años) caen en día hábil y ese día la CMF no publica. Se retrocede
        hasta encontrar datos, con tope para no barrer el mes entero.

        Devuelve (fecha, {"records": [...], "fund_types": {...}}); fecha es None si no hubo datos.
        """
        import datetime as _dt
        d = _dt.date(year + (month // 12), (month % 12) + 1, 1) - _dt.timedelta(days=1)
        for _ in range(max_tries):
            if d.weekday() < 5:
                res = self.fetch_daily_stats(d)
                if res["records"]:
                    return d, res
            d -= _dt.timedelta(days=1)
        return None, {"records": [], "fund_types": {}}

    @staticmethod
    def normalizar_run(run: str) -> str:
        """
        RUN sin dígito verificador ni puntos.

        El archivo de estadística lo trae CON DV ('8011-K') y el de cartera SIN él ('8011').
        Sin normalizar, las dos tablas no cruzan y el zoom al portafolio queda vacío.
        """
        base = (run or "").strip().split("-")[0]
        return base.replace(".", "").strip()

    @staticmethod
    def _numero(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _entero(v):
        try:
            return int(float(str(v).replace(",", "").strip()))
        except (TypeError, ValueError):
            return None


