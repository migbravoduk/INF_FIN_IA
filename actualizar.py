import sys
import os
from datetime import datetime
import datetime as dt
import argparse

# Forzar UTF-8 en la consola de Windows (evita UnicodeEncodeError con emojis)
import io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Congelado (PyInstaller): settings, la BD y el catálogo se resuelven con rutas RELATIVAS,
# así que el directorio de trabajo tiene que ser la carpeta del .exe (= raíz del proyecto).
# Al hacer doble clic el cwd suele coincidir, pero no si se lanza desde otra carpeta.
# Va antes de importar config.settings, que lee el .env en tiempo de import.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt, IntPrompt

from config.settings import settings
from db.database import Database
from scheduler.freshness import probe_all, FreshnessStatus
from scheduler.jobs import _dispatch_catchup, _SOURCE_OF

console = Console()

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Fuentes cubiertas por el catch-up dirigido por frescura (scheduler/freshness.probe_all).
# 'cmf_insurer' agrupa dos sondas (vida y generales) y 'bcentral' una por serie del catálogo.
CATCHUP_KINDS = [
    "bcentral", "cmf_emp", "cmf_bank", "cmf_insurer", "cmf_broker", "cmf_cartera", "cmf_fm",
    "sp_cuotas", "sp_precios", "sp_cartera",
]


def get_status_summary(statuses: list[FreshnessStatus]) -> dict:
    """Agrupa los estados por tipo de fuente.

    Un `kind` puede traer varias sondas (una por serie en BCCh, una por ramo en seguros):
    `latest` se queda con el período más atrasado del grupo y `expected` con el del primer
    pendiente, que es lo que hay que mirar para saber qué falta.
    """
    summary = {k: {"total": 0, "due": 0, "latest": None, "expected": None}
               for k in CATCHUP_KINDS}

    for s in statuses:
        entry = summary.setdefault(
            s.kind, {"total": 0, "due": 0, "latest": None, "expected": None})
        entry["total"] += 1
        if s.due:
            entry["due"] += 1
            if entry["expected"] is None:
                entry["expected"] = s.expected
        if s.latest_have and (entry["latest"] is None or s.latest_have < entry["latest"]):
            entry["latest"] = s.latest_have

    return summary


def _estado(entry: dict) -> str:
    """Etiqueta coloreada 'Al día' / 'Pendiente: X' para el menú."""
    if not entry["due"]:
        return "[green]Al día[/]"
    detalle = entry["expected"] or ""
    sufijo = f": {detalle}" if detalle else ""
    if entry["total"] > 1:
        return f"[yellow]{entry['due']} de {entry['total']} pendientes{sufijo}[/]"
    return f"[yellow]Pendiente{sufijo}[/]"

def show_diagnostics(statuses: list[FreshnessStatus]):
    """Muestra una tabla detallada de la frescura de datos."""
    t = Table(title="Estado Detallado de Frescura", box=box.ROUNDED, border_style="blue")
    t.add_column("Fuente")
    t.add_column("Frecuencia", justify="center")
    t.add_column("Último en BD", justify="center")
    t.add_column("Siguiente Esperado", justify="center")
    t.add_column("Estado", justify="center")

    for s in statuses:
        estado = "[yellow]Pendiente[/]" if s.due else "[green]Al día[/]"
        t.add_row(s.source, s.frequency, s.latest_have or "—", s.expected or "—", estado)

    console.print(t)
    Prompt.ask("\n[dim]Presiona ENTER para continuar[/]")

def run_catchup_for_kind(db: Database, statuses: list[FreshnessStatus], kind: str) -> int:
    """Ejecuta catchup filtrado por un tipo específico."""
    targets = [s for s in statuses if s.kind == kind and s.due]
    if not targets:
        console.print(f"[green]✓ La fuente '{kind}' ya está al día. Nada que procesar en modo catch-up.[/]")
        return 0
        
    console.print(Panel(f"[bold green]Iniciando actualización inteligente para: {kind}...[/]"))
    total_updated = 0
    for st in targets:
        try:
            status, n = _dispatch_catchup(db, st)
            console.print(f"  • {st.source}: [cyan]{status}[/] (+{n} registros)")
            total_updated += n
        except Exception as e:
            console.print(f"  [bold red]❌ Error en {st.source}:[/] {e}")
            
    return total_updated

def run_global_catchup(db: Database, statuses: list[FreshnessStatus]):
    """Ejecuta catchup para todas las fuentes."""
    due_targets = [s for s in statuses if s.due]
    if not due_targets:
        console.print("[bold green]✓ Todos los datos están completamente al día.[/]")
        return
        
    console.print(Panel(
        f"[bold green]🚀 Iniciando Catch-up Global[/]\n\n"
        f"Procesando [yellow]{len(due_targets)}[/] objetivos pendientes...",
        border_style="green"
    ))
    
    for st in due_targets:
        try:
            status, n = _dispatch_catchup(db, st)
            console.print(f"  • {st.source}: [cyan]{status}[/] (+{n} registros)")
        except Exception as e:
            console.print(f"  [bold red]❌ Error en {st.source}:[/] {e}")
            
    console.print("\n[bold green]✓ Catch-up global completado.[/]")

# ============================================================
# Opciones de Actualización Manual/Forzada
# ============================================================

def manual_bcentral():
    """Actualización manual/forzada del Banco Central."""
    console.print(Panel("[bold cyan]Banco Central (BCCh) - Actualización Manual[/]", border_style="cyan"))
    console.print("[1] Actualizar todas las series del catálogo (Backfill completo)\n"
                  "[2] Actualizar una serie específica por ID (BDE ID)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    
    if opt == "1":
        from scheduler.jobs import run_all_series
        console.print("[bold yellow]Iniciando actualización completa del catálogo BCCh...[/]")
        try:
            run_all_series()
            console.print("[bold green]✓ Catálogo BCCh actualizado.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            
    elif opt == "2":
        series_id = Prompt.ask("Introduce el ID de la serie (ej. F073.IPC.VAR.T.M)")
        from_date = Prompt.ask("Fecha inicio (YYYY-MM-DD) [opcional]", default="")
        from_date = from_date if from_date else None
        
        from collectors.bcentral import BCentralCollector
        from processors.normalizer import normalize_observations
        
        collector = BCentralCollector()
        try:
            console.print(f"[bold yellow]Descargando serie {series_id}...[/]")
            raw = collector.fetch_series(series_id, from_date=from_date)
            clean = normalize_observations(raw, series_id)
            
            with Database() as db:
                db.upsert_series({"id": series_id, "name": series_id, "source_id": "bcentral"})
                new, updated = db.upsert_observations(series_id, clean)
            console.print(f"[bold green]✓ Éxito:[/] {len(clean)} observaciones. +{new} nuevas, {updated} actualizadas.")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

def manual_cmf_empresas():
    """Actualización manual/forzada de CMF Empresas."""
    console.print(Panel("[bold cyan]CMF Empresas - Actualización Manual[/]", border_style="cyan"))
    console.print("[1] Cargar un período específico (YYYYMM, ej: 202603)\n"
                  "[2] Ejecutar carga histórica completa (2018 - 2026)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    
    from collectors.cmf import CMFCollector
    
    if opt == "1":
        period = IntPrompt.ask("Introduce el período (ej. 202603)")
        force = Prompt.ask("¿Forzar la descarga ignorando caché?", choices=["s", "n"], default="n") == "s"
        try:
            collector = CMFCollector()
            console.print(f"[bold yellow]Descargando e ingesando período CMF {period}...[/]")
            records = collector.fetch_period(period, force_download=force)
            if records:
                with Database() as db:
                    inserted = db.insert_cmf_records(period, records)
                console.print(f"[bold green]✓ Éxito:[/] Ingestados {inserted:,} registros para el período {period}.")
            else:
                console.print("[yellow]No se obtuvieron registros para ese período.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            
    elif opt == "2":
        console.print("[bold yellow]Iniciando carga histórica completa (2018-2026)...[/]")
        periods_to_fetch = [
            2018, 2019, 2020, 2021, 2022, 2023, 2024,
            202503, 202506, 202509, 202512, 202603
        ]
        try:
            collector = CMFCollector()
            total = 0
            with Database() as db:
                for p in periods_to_fetch:
                    console.print(f"Procesando {p}...")
                    records = collector.fetch_period(p)
                    if records:
                        inserted = db.insert_cmf_records(p, records)
                        total += inserted
            console.print(f"[bold green]✓ Ingesta CMF Histórica finalizada. Total insertado: {total:,} registros.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

def manual_cmf_bancos():
    """Actualización manual/forzada de CMF Bancos."""
    console.print(Panel("[bold cyan]CMF Bancos (SBIF) - Actualización Manual[/]", border_style="cyan"))
    console.print("[1] Descargar un mes específico (Año y Mes)\n"
                  "[2] Carga histórica mensual para bancos principales\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    
    from collectors.cmf_banks import CMFBankCollector
    
    if opt == "1":
        year = IntPrompt.ask("Año (ej. 2026)")
        month = IntPrompt.ask("Mes (1-12)")
        force = Prompt.ask("¿Forzar descarga ignorando caché?", choices=["s", "n"], default="n") == "s"
        
        collector = CMFBankCollector()
        bank_codes = list(collector.BANKS_CATALOG.keys())
        total = 0
        
        try:
            with Database() as db:
                for b_code in bank_codes:
                    b_name = collector.BANKS_CATALOG.get(b_code, f"BANCO {b_code}")
                    console.print(f"Procesando {b_name} ({b_code}) para {year}-{month:02d}...")
                    for rtype in ("balance", "resultado"):
                        recs = collector.fetch_bank_report(year, month, b_code, report_type=rtype, force_download=force)
                        if recs:
                            total += db.insert_bank_records(year, month, b_code, rtype, recs)
            console.print(f"[bold green]✓ Ingesta bancaria manual finalizada. {total:,} registros procesados.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            
    elif opt == "2":
        year_start = IntPrompt.ask("Año inicio", default=2024)
        try:
            collector = CMFBankCollector()
            bank_codes = list(collector.BANKS_CATALOG.keys())
            today = dt.date.today()
            periods = []
            for y in range(year_start, today.year + 1):
                last_m = (today.month - 1) if y == today.year else 12
                for m in range(1, last_m + 1):
                    periods.append((y, m))
                    
            total = 0
            with Database() as db:
                for y, m in periods:
                    for b_code in bank_codes:
                        for rtype in ("balance", "resultado"):
                            recs = collector.fetch_bank_report(y, m, b_code, report_type=rtype)
                            if recs:
                                total += db.insert_bank_records(y, m, b_code, rtype, recs)
            console.print(f"[bold green]✓ Ingesta histórica bancaria finalizada. {total:,} registros procesados.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

def manual_sp_cuotas():
    """Actualización manual/forzada de SP Valores Cuota."""
    console.print(Panel("[bold cyan]SP Valores Cuota - Actualización Manual[/]", border_style="cyan"))
    year_start = IntPrompt.ask("Año de inicio", default=2002)
    year_end = IntPrompt.ask("Año de término", default=datetime.now().year)
    
    from collectors.sp_pensions import SPPensionCollector
    collector = SPPensionCollector()
    funds = ["A", "B", "C", "D", "E"]
    total = 0
    
    try:
        with Database() as db:
            fecconf = collector.fetch_fecconf()
            for f_type in funds:
                console.print(f"Descargando fondo {f_type}...")
                recs = collector.fetch_quota_values(year_start, year_end, fund_type=f_type, fecconf=fecconf)
                if recs:
                    total += db.insert_sp_quota_values(recs)
        console.print(f"[bold green]✓ Ingesta de Valores Cuota finalizada. Total insertado: {total:,} registros.[/]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")

def manual_sp_precios():
    """Actualización manual/forzada de SP Precios."""
    console.print(Panel("[bold cyan]SP Cinta de Precios - Actualización Manual[/]", border_style="cyan"))
    console.print("[1] Cargar una fecha específica (YYYY-MM-DD)\n"
                  "[2] Ejecutar backfill histórico (últimos 5 años diarios, anteriores 5 semanales)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    
    from collectors.sp_pensions import SPPensionCollector
    collector = SPPensionCollector()
    
    if opt == "1":
        date_str = Prompt.ask("Introduce la fecha (YYYY-MM-DD)", default=dt.date.today().isoformat())
        try:
            console.print(f"[bold yellow]Descargando precios para {date_str}...[/]")
            recs = collector.fetch_daily_prices(date_str)
            if recs:
                with Database() as db:
                    inserted = db.insert_sp_instrument_prices(recs)
                console.print(f"[bold green]✓ Ingesta finalizada. {inserted:,} precios ingresados para {date_str}.[/]")
            else:
                console.print("[yellow]No se encontraron precios para esa fecha (puede ser feriado/fin de semana).[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            
    elif opt == "2":
        console.print("[bold yellow]Iniciando backfill histórico de precios...[/]")
        today = dt.date.today()
        dates_to_fetch = []
        
        # Últimos 5 años diarios
        five_years_ago = today - dt.timedelta(days=5*365)
        curr = five_years_ago
        while curr <= today:
            if curr.weekday() < 5:
                dates_to_fetch.append(curr.strftime("%Y-%m-%d"))
            curr += dt.timedelta(days=1)
            
        # 5 años anteriores semanales (miércoles)
        ten_years_ago = today - dt.timedelta(days=10*365)
        curr = ten_years_ago
        while curr < five_years_ago:
            if curr.weekday() == 2:
                dates_to_fetch.append(curr.strftime("%Y-%m-%d"))
            curr += dt.timedelta(days=1)
            
        total = 0
        try:
            with Database() as db:
                for d_str in dates_to_fetch:
                    console.print(f"Descargando {d_str}...")
                    recs = collector.fetch_daily_prices(d_str)
                    if recs:
                        total += db.insert_sp_instrument_prices(recs)
            console.print(f"[bold green]✓ Ingesta histórica de precios finalizada. Total: {total:,} precios.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

def manual_sp_cartera():
    """Actualización manual/forzada de SP Cartera."""
    console.print(Panel("[bold cyan]SP Cartera Mensual - Actualización Manual[/]", border_style="cyan"))
    period = IntPrompt.ask("Introduce el período YYYYMM (ej. 202601)")
    
    from collectors.sp_pensions import SPPensionCollector
    collector = SPPensionCollector()
    
    try:
        console.print(f"[bold yellow]Descargando cartera para período {period}...[/]")
        recs = collector.fetch_portfolio(period)
        if recs:
            with Database() as db:
                db_period = f"{str(period)[:4]}-{str(period)[4:]}"
                inserted = db.insert_sp_portfolio_holdings(db_period, recs)
            console.print(f"[bold green]✓ Ingesta de cartera finalizada. {inserted:,} activos insertados para {db_period}.[/]")
        else:
            console.print("[yellow]No se obtuvieron registros de cartera para ese período.[/]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")

def _pedir_trimestre() -> tuple[int, int]:
    """Pide año y mes de cierre trimestral (la FECU solo publica 3/6/9/12)."""
    year = IntPrompt.ask("Año (ej. 2026)", default=datetime.now().year)
    month = IntPrompt.ask("Mes de cierre", choices=["3", "6", "9", "12"], default=3)
    return year, int(month)


def manual_seguros():
    """Actualización manual/forzada de EEFF de seguros (FECU, vida y generales)."""
    console.print(Panel("[bold cyan]CMF Seguros (EEFF FECU) - Actualización Manual[/]",
                        border_style="cyan"))
    console.print("[1] Cargar un trimestre específico\n"
                  "[2] Backfill trimestral completo (2015Q1 en adelante)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    if opt == "3":
        return

    ramo = Prompt.ask("Ramo", choices=["vida", "generales", "ambos"], default="ambos")
    ramos = ["vida", "generales"] if ramo == "ambos" else [ramo]

    from collectors.cmf_insurers import CMFInsurerCollector

    if opt == "1":
        year, month = _pedir_trimestre()
        periodos = [(year, month)]
    else:
        year_start = IntPrompt.ask("Año de inicio", default=2015)
        hoy = dt.date.today()
        periodos = [(y, m) for y in range(year_start, hoy.year + 1) for m in (3, 6, 9, 12)
                    if dt.date(y, m, 1) <= hoy]

    total = 0
    try:
        with Database() as db:
            for r in ramos:
                # Una descarga por período trae TODAS las compañías del ramo (no itera por compañía).
                collector = CMFInsurerCollector(insurance_type=r)
                for y, m in periodos:
                    console.print(f"Procesando seguros {r} {y}-{m:02d}...")
                    recs = collector.fetch_period(y, m, freq="trimestral")
                    if recs:
                        total += db.insert_insurer_records(
                            int(f"{y}{m:02d}"), "trimestral", recs, insurance_type=r)
                    else:
                        console.print(f"  [yellow]⚠ Sin datos para {r} {y}-{m:02d}.[/]")
        console.print(f"[bold green]✓ Ingesta de seguros finalizada. {total:,} registros.[/]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")


def manual_intermediarios():
    """Actualización manual/forzada de EEFF de corredores de bolsa y agentes de valores."""
    console.print(Panel("[bold cyan]CMF Intermediarios de valores - Actualización Manual[/]",
                        border_style="cyan"))
    console.print("[1] Cargar un trimestre específico\n"
                  "[2] Backfill trimestral completo (2015Q1 en adelante)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    if opt == "3":
        return

    from collectors.cmf_brokers import CMFBrokerCollector

    if opt == "1":
        periodos = [_pedir_trimestre()]
    else:
        year_start = IntPrompt.ask("Año de inicio", default=2015)
        hoy = dt.date.today()
        periodos = [(y, m) for y in range(year_start, hoy.year + 1) for m in (3, 6, 9, 12)
                    if dt.date(y, m, 1) <= hoy]

    total = 0
    try:
        collector = CMFBrokerCollector()
        with Database() as db:
            for y, m in periodos:
                # Corredores y agentes vienen juntos en la misma descarga.
                console.print(f"Procesando intermediarios {y}-{m:02d}...")
                recs = collector.fetch_period(y, m)
                if recs:
                    total += db.insert_broker_records(int(f"{y}{m:02d}"), recs)
                else:
                    console.print(f"  [yellow]⚠ Sin datos para {y}-{m:02d}.[/]")
        console.print(f"[bold green]✓ Ingesta de intermediarios finalizada. {total:,} registros.[/]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")


def manual_cmf_fondos_mutuos():
    """Actualización manual/forzada de CMF Fondos Mutuos (Valores Cuota)."""
    console.print(Panel("[bold cyan]CMF Fondos Mutuos - Actualización Manual[/]",
                        border_style="cyan"))
    console.print("[1] Cargar para el día de hoy\n"
                  "[2] Cargar una fecha específica (YYYY-MM-DD)\n"
                  "[3] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
    if opt == "3":
        return

    from collectors.cmf_mutual_funds import CMFMutualFundsCollector

    if opt == "1":
        target_date = dt.date.today()
    else:
        date_str = Prompt.ask("Introduce la fecha (YYYY-MM-DD)", default=dt.date.today().isoformat())
        target_date = dt.date.fromisoformat(date_str)

    try:
        collector = CMFMutualFundsCollector()
        console.print(f"[bold yellow]Descargando valores cuota de fondos mutuos para {target_date}...[/]")
        recs = collector.fetch_daily_quotas(target_date)
        if recs:
            with Database() as db:
                res = db.upsert_mutual_fund_quotas(recs)
            console.print(f"[bold green]✓ Ingesta finalizada. {res['inserted']} registros insertados.[/]")
        else:
            console.print("[yellow]No se obtuvieron registros de fondos mutuos para esa fecha.[/]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")



def _ingestar_cartera(db, zip_path) -> tuple[int, int]:
    """Parsea e ingesta un ZIP de cartera: B.8 Control + B.1 Renta Fija."""
    from collectors.seguros_cartera import parse_zip_control, parse_zip_fixed_income

    n_ctrl = n_fi = 0
    recs = parse_zip_control(zip_path)
    if recs:
        n_ctrl = db.insert_insurer_portfolio_control(
            recs[0]["period"], recs[0]["insurance_type"], recs)
    fi = parse_zip_fixed_income(zip_path)
    if fi:
        n_fi = db.insert_insurer_portfolio_fixed_income(
            fi[0]["period"], fi[0]["insurance_type"], fi)
    return n_ctrl, n_fi


def actualizar_cartera_seguros(periodos_por_ramo: dict[str, list[int]]) -> tuple[int, int]:
    """Descarga e ingesta los períodos indicados de cartera de seguros (Circular 1.835)."""
    from collectors.seguros_cartera import download_cartera

    total_ctrl = total_fi = 0
    with Database() as db:
        for ramo, periodos in periodos_por_ramo.items():
            if not periodos:
                console.print(f"  • {ramo}: [green]sin períodos nuevos.[/]")
                continue
            for per in periodos:
                console.print(f"  • {ramo} {per}: descargando...")
                # download_cartera reintenta: la CMF devuelve HTML en vez del ZIP de forma
                # intermitente aunque el período exista.
                zp = download_cartera(per, ramo, "data/seguros_cartera_raw")
                if not zp:
                    console.print(f"    [yellow]⚠ no disponible en la CMF, se omite.[/]")
                    continue
                n_ctrl, n_fi = _ingestar_cartera(db, zp)
                console.print(f"    [green]✓ control {n_ctrl:,} filas · "
                              f"renta fija {n_fi:,} instrumentos.[/]")
                total_ctrl += n_ctrl
                total_fi += n_fi
    return total_ctrl, total_fi


def _periodos_faltantes_cartera(ramos: list[str], desde: int | None = None) -> dict[str, list[int]]:
    """Compara los períodos publicados por la CMF contra los ya cargados, por ramo."""
    from collectors.seguros_cartera import list_remote_periods

    faltantes = {}
    with Database(read_only=True) as db:
        for r in ramos:
            cargados = set(db.get_insurer_portfolio_periods(r))
            remotos = list_remote_periods(r)
            pend = sorted(p for p in remotos if p not in cargados and (not desde or p >= desde))
            faltantes[r] = pend
    return faltantes


def menu_cartera_seguros():
    """Cartera de inversiones de seguros (Circular 1.835) — fuera del catch-up por frescura."""
    console.print(Panel("[bold cyan]Cartera de inversiones de seguros (Circular 1.835)[/]",
                        border_style="cyan"))
    with Database(read_only=True) as db:
        cargados = db.get_insurer_portfolio_periods()
    console.print(f"  Períodos cargados: [bold]{len(cargados)}[/] · "
                  f"último: [bold]{cargados[0] if cargados else 'ninguno'}[/]\n")

    console.print("[1] Descargar los períodos que falten (consulta la CMF)\n"
                  "[2] Descargar solo el último período publicado\n"
                  "[3] Descargar un período específico (YYYYMM)\n"
                  "[4] Volver")
    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3", "4"])
    if opt == "4":
        return

    ramo = Prompt.ask("Ramo", choices=["vida", "generales", "ambos"], default="ambos")
    ramos = ["vida", "generales"] if ramo == "ambos" else [ramo]

    try:
        if opt == "1":
            desde = IntPrompt.ask("Desde el período YYYYMM (0 = todos)", default=202001)
            console.print("[bold yellow]Consultando períodos publicados en la CMF...[/]")
            objetivo = _periodos_faltantes_cartera(ramos, desde or None)
            pendientes = sum(len(v) for v in objetivo.values())
            if not pendientes:
                console.print("[bold green]✓ La cartera de seguros ya está al día.[/]")
                return
            console.print(f"[bold]{pendientes}[/] período(s) por descargar.")
        elif opt == "2":
            from collectors.seguros_cartera import list_remote_periods
            objetivo = {r: list_remote_periods(r)[:1] for r in ramos}
        else:
            per = IntPrompt.ask("Período (ej. 202606)")
            objetivo = {r: [per] for r in ramos}

        ctrl, fi = actualizar_cartera_seguros(objetivo)
        console.print(f"\n[bold green]✓ Cartera actualizada.[/] "
                      f"Control: {ctrl:,} filas · Renta fija: {fi:,} instrumentos.")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")


# ============================================================
# Menú Principal
# ============================================================

# opción → (kind de frescura, título, etiqueta del modo manual, función manual)
MENU_FUENTES = {
    "1": ("bcentral",   "Banco Central",        "Descargas manuales / backfills forzados",   manual_bcentral),
    "2": ("cmf_emp",    "CMF Empresas",         "Ingesta manual / forzar período / historial", manual_cmf_empresas),
    "3": ("cmf_bank",   "CMF Bancos",           "Ingesta bancaria manual / historial",       manual_cmf_bancos),
    "4": ("cmf_insurer", "CMF Seguros",         "Ingesta manual por trimestre / backfill",   manual_seguros),
    "5": ("cmf_broker", "CMF Intermediarios",   "Ingesta manual por trimestre / backfill",   manual_intermediarios),
    "6": ("cmf_cartera", "Cartera de seguros",  "Períodos faltantes / último / uno puntual",  menu_cartera_seguros),
    "7": ("cmf_fm",     "CMF Fondos Mutuos",    "Ingesta manual por fecha (Valores Cuota)",  manual_cmf_fondos_mutuos),
    "8": ("sp_cuotas",  "SP Valores Cuota",     "Descarga manual por rango de años",         manual_sp_cuotas),
    "9": ("sp_precios", "SP Cinta de Precios",  "Descarga manual por fecha / histórico",     manual_sp_precios),
    "10": ("sp_cartera", "SP Cartera AFP",      "Descarga manual de período específico",     manual_sp_cartera),
}


def run_menu():
    while True:
        clear_screen()
        
        # Sondeo de frescura inicial
        with Database(read_only=True) as db:
            statuses = probe_all(db)
            
        summary = get_status_summary(statuses)
        
        # Contar total pendientes
        pendientes_totales = sum(1 for s in statuses if s.due)
        
        console.print(Panel.fit(
            f"[bold white]Menú de Actualización - Repositorio Financiero Chile[/]\n\n"
            f"[0] ⚡ [bold green]Ejecutar Catch-up Completo[/] (Actualiza lo pendiente automáticamente: [yellow]{pendientes_totales}[/] fuentes)\n"
            f"[1] 🏦 [cyan]Banco Central (BCCh)[/] — ({summary['bcentral']['due']} de {summary['bcentral']['total']} series pendientes)\n"
            f"[2] 🏢 [cyan]CMF Empresas (EEFF)[/] — ({_estado(summary['cmf_emp'])})\n"
            f"[3] 🏛️ [cyan]CMF Bancos (SBIF)[/] — ({_estado(summary['cmf_bank'])})\n"
            f"[4] 🛡️ [cyan]CMF Seguros (EEFF FECU)[/] — ({_estado(summary['cmf_insurer'])})\n"
            f"[5] 🤝 [cyan]CMF Intermediarios de valores[/] — ({_estado(summary['cmf_broker'])})\n"
            f"[6] 📊 [cyan]Cartera de seguros (Circ. 1.835)[/] — ({_estado(summary['cmf_cartera'])})\n"
            f"[7] 📈 [cyan]CMF Fondos Mutuos[/] — ({_estado(summary.get('cmf_fm', {'total':0, 'due':0, 'expected':None}))})\n"
            f"[8] 📈 [cyan]SP Valores Cuota AFP[/] — ({_estado(summary['sp_cuotas'])})\n"
            f"[9] 💵 [cyan]SP Cinta de Precios[/] — ({_estado(summary['sp_precios'])})\n"
            f"[10] 📁 [cyan]SP Cartera AFP[/] — ({_estado(summary['sp_cartera'])})\n\n"
            # 'd' y 's' van escapados: Rich los leería como los estilos dim y strike.
            f"\\[d] 🩺 Ver diagnóstico de frescura completo\n"
            f"\\[s] ❌ Salir del programa",
            title="🇨🇱 Actualizador de Fuentes",
            border_style="blue",
            padding=(1, 4)
        ))

        # Alertas de credenciales
        if not settings.has_bcentral_credentials:
            console.print("[bold red]⚠️  ¡ATENCIÓN! No tienes configuradas las credenciales del Banco Central en '.env' (Opción 1 estará limitada).[/]")

        opcion = Prompt.ask("\nSelecciona una opción",
                            choices=[str(i) for i in range(11)] + ["d", "s"])

        if opcion == "s":
            console.print("\n[bold cyan]¡Hasta luego![/]")
            sys.exit(0)

        elif opcion == "0":
            clear_screen()
            with Database() as db:
                run_global_catchup(db, statuses)
            Prompt.ask("\n[dim]Presiona ENTER para volver al menú principal[/]")

        elif opcion == "d":
            clear_screen()
            show_diagnostics(statuses)

        elif opcion in MENU_FUENTES:
            clear_screen()
            kind, titulo, etiqueta_manual, manual_fn = MENU_FUENTES[opcion]
            entry = summary[kind]
            console.print(f"[bold]Estado {titulo}:[/] {_estado(entry)}")
            console.print(f"  Último en BD: {entry['latest'] or 'Ninguno'}")
            console.print(f"  Siguiente esperado: {entry['expected'] or 'Ninguno'}\n")
            console.print("[1] Catch-up inteligente (descargar solo lo pendiente)")
            console.print(f"[2] {etiqueta_manual}")
            console.print("[3] Volver")
            opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
            if opt == "1":
                # La conexión de escritura se abre acá y no antes: el sondeo previo es
                # read_only y DuckDB no admite dos escritores sobre el mismo archivo.
                with Database() as db:
                    run_catchup_for_kind(db, statuses, kind)
                Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
            elif opt == "2":
                manual_fn()

# ============================================================
# Modo Argumentos (CLI no interactivo)
# ============================================================

def run_cli_args(args):
    """Ejecuta en modo CLI directo sin menú interactivo."""
    if args.status:
        with Database(read_only=True) as db:
            statuses = probe_all(db)
        t = Table(box=box.ROUNDED)
        t.add_column("Fuente")
        t.add_column("Frecuencia")
        t.add_column("Último en BD")
        t.add_column("Siguiente")
        t.add_column("Due (Pendiente)")
        for s in statuses:
            t.add_row(s.source, s.frequency, s.latest_have or "—", s.expected or "—", "[yellow]SÍ[/]" if s.due else "NO")
        console.print(t)
        return

    if args.source and args.source not in CATCHUP_KINDS:
        console.print(f"[bold red]Error:[/] Fuente '{args.source}' inválida. "
                      f"Debe ser una de: {', '.join(CATCHUP_KINDS)}")
        sys.exit(1)

    if args.catchup or args.source:
        with Database() as db:
            statuses = probe_all(db)
            if args.catchup:
                run_global_catchup(db, statuses)
            else:
                run_catchup_for_kind(db, statuses, args.source)

    # El catch-up solo baja el período esperado de cada fuente. Esta pasada extra rellena
    # cualquier HUECO histórico de la cartera, que va aparte porque exige consultar a la CMF
    # qué períodos publicó (la sonda de frescura no toca la red).
    if args.cartera_seguros:
        console.print(Panel("[bold green]Cartera de seguros (Circular 1.835) — huecos históricos[/]",
                            border_style="green"))
        console.print("[dim]Consultando períodos publicados en la CMF...[/]")
        objetivo = _periodos_faltantes_cartera(["vida", "generales"], args.cartera_desde)
        if not any(objetivo.values()):
            console.print("[bold green]✓ La cartera de seguros ya está al día.[/]")
        else:
            ctrl, fi = actualizar_cartera_seguros(objetivo)
            console.print(f"[bold green]✓ Cartera actualizada.[/] "
                          f"Control: {ctrl:,} filas · Renta fija: {fi:,} instrumentos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Actualizador de Fuentes del Repositorio Financiero Chile")
    parser.add_argument("--catchup", action="store_true", help="Ejecuta catch-up global (completo) y sale")
    parser.add_argument("--source", type=str, default=None,
                        help=f"Ejecuta catch-up de una fuente específica ({', '.join(CATCHUP_KINDS)}) y sale")
    parser.add_argument("--cartera-seguros", action="store_true",
                        help="Rellena huecos históricos de cartera de seguros consultando a la CMF "
                             "(el período corriente ya lo cubre --catchup)")
    parser.add_argument("--cartera-desde", type=int, default=202001,
                        help="Con --cartera-seguros, período mínimo YYYYMM a considerar (default: 202001)")
    parser.add_argument("--todo", action="store_true",
                        help="Equivale a --catchup --cartera-seguros: actualiza todas las fuentes")
    parser.add_argument("--status", action="store_true", help="Muestra el diagnóstico de frescura y sale")

    args = parser.parse_args()

    if args.todo:
        args.catchup = True
        args.cartera_seguros = True

    if args.catchup or args.source or args.status or args.cartera_seguros:
        run_cli_args(args)
    else:
        try:
            run_menu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Operación cancelada. Saliendo...[/]")
            sys.exit(0)
