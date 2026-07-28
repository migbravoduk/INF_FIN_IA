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

def get_status_summary(statuses: list[FreshnessStatus]) -> dict:
    """Agrupa los estados por tipo de fuente."""
    summary = {
        "bcentral": {"total": 0, "due": 0},
        "cmf_emp": {"total": 0, "due": 0, "latest": None, "expected": None},
        "cmf_bank": {"total": 0, "due": 0, "latest": None, "expected": None},
        "sp_cuotas": {"total": 0, "due": 0, "latest": None, "expected": None},
        "sp_precios": {"total": 0, "due": 0, "latest": None, "expected": None},
        "sp_cartera": {"total": 0, "due": 0, "latest": None, "expected": None},
    }
    
    for s in statuses:
        if s.kind == "bcentral":
            summary["bcentral"]["total"] += 1
            if s.due:
                summary["bcentral"]["due"] += 1
        elif s.kind in summary:
            summary[s.kind]["total"] += 1
            if s.due:
                summary[s.kind]["due"] += 1
            summary[s.kind]["latest"] = s.latest_have
            summary[s.kind]["expected"] = s.expected
            
    return summary

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

# ============================================================
# Menú Principal
# ============================================================

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
            f"[2] 🏢 [cyan]CMF Empresas (EEFF)[/] — (" + ("[yellow]Pendiente: " + (summary["cmf_emp"]["expected"] or "") + "[/]" if summary["cmf_emp"]["due"] else "[green]Al día[/]") + ")\n"
            f"[3] 🏛️ [cyan]CMF Bancos (SBIF)[/] — (" + ("[yellow]Pendiente: " + (summary["cmf_bank"]["expected"] or "") + "[/]" if summary["cmf_bank"]["due"] else "[green]Al día[/]") + ")\n"
            f"[4] 📈 [cyan]SP Valores Cuota AFP[/] — (" + ("[yellow]Pendiente[/]" if summary["sp_cuotas"]["due"] else "[green]Al día[/]") + ")\n"
            f"[5] 💵 [cyan]SP Cinta de Precios[/] — (" + ("[yellow]Pendiente[/]" if summary["sp_precios"]["due"] else "[green]Al día[/]") + ")\n"
            f"[6] 📁 [cyan]SP Cartera AFP[/] — (" + ("[yellow]Pendiente: " + (summary["sp_cartera"]["expected"] or "") + "[/]" if summary["sp_cartera"]["due"] else "[green]Al día[/]") + ")\n\n"
            f"[7] 🩺 Ver diagnóstico de frescura completo\n"
            f"[8] ❌ Salir del programa",
            title="🇨🇱 Actualizador de Fuentes",
            border_style="blue",
            padding=(1, 4)
        ))
        
        # Alertas de credenciales
        if not settings.has_bcentral_credentials:
            console.print("[bold red]⚠️  ¡ATENCIÓN! No tienes configuradas las credenciales del Banco Central en '.env' (Opción 1 estará limitada).[/]")

        opcion = Prompt.ask("\nSelecciona una opción", choices=[str(i) for i in range(9)])
        
        if opcion == "8":
            console.print("\n[bold cyan]¡Hasta luego![/]")
            sys.exit(0)
            
        elif opcion == "0":
            clear_screen()
            with Database() as db:
                run_global_catchup(db, statuses)
            Prompt.ask("\n[dim]Presiona ENTER para volver al menú principal[/]")
            
        elif opcion == "7":
            clear_screen()
            show_diagnostics(statuses)
            
        else:
            clear_screen()
            with Database() as db:
                if opcion == "1":
                    # Banco Central
                    console.print(f"[bold]Estado:[/] {summary['bcentral']['due']} series pendientes de {summary['bcentral']['total']}.\n")
                    console.print("[1] Catch-up inteligente (Solo descargar lo pendiente)")
                    console.print("[2] Descargas manuales / Backfills forzados")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "bcentral")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_bcentral()
                        
                elif opcion == "2":
                    # CMF Empresas
                    console.print(f"[bold]Estado CMF Empresas:[/] " + ("[yellow]Pendiente[/]" if summary["cmf_emp"]["due"] else "[green]Al día[/]"))
                    console.print(f"  Último que tenemos: {summary['cmf_emp']['latest'] or 'Ninguno'}")
                    console.print(f"  Siguiente esperado: {summary['cmf_emp']['expected'] or 'Ninguno'}\n")
                    console.print("[1] Catch-up inteligente (Descargar siguiente esperado)")
                    console.print("[2] Ingesta manual / Forzar período / Historial")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "cmf_emp")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_cmf_empresas()
                        
                elif opcion == "3":
                    # CMF Bancos
                    console.print(f"[bold]Estado CMF Bancos:[/] " + ("[yellow]Pendiente[/]" if summary["cmf_bank"]["due"] else "[green]Al día[/]"))
                    console.print(f"  Último que tenemos: {summary['cmf_bank']['latest'] or 'Ninguno'}")
                    console.print(f"  Siguiente esperado: {summary['cmf_bank']['expected'] or 'Ninguno'}\n")
                    console.print("[1] Catch-up inteligente (Descargar siguiente mes esperado)")
                    console.print("[2] Ingesta bancaria manual / Historial")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "cmf_bank")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_cmf_bancos()
                        
                elif opcion == "4":
                    # SP Cuotas
                    console.print(f"[bold]Estado Valores Cuota:[/] " + ("[yellow]Pendiente[/]" if summary["sp_cuotas"]["due"] else "[green]Al día[/]"))
                    console.print(f"  Último en BD: {summary['sp_cuotas']['latest'] or 'Ninguno'}")
                    console.print(f"  Siguiente esperado: {summary['sp_cuotas']['expected'] or 'Ninguno'}\n")
                    console.print("[1] Catch-up inteligente (Actualizar hasta el último día hábil)")
                    console.print("[2] Descarga manual por rango de años")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "sp_cuotas")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_sp_cuotas()
                        
                elif opcion == "5":
                    # SP Precios
                    console.print(f"[bold]Estado Cinta de Precios:[/] " + ("[yellow]Pendiente[/]" if summary["sp_precios"]["due"] else "[green]Al día[/]"))
                    console.print(f"  Último en BD: {summary['sp_precios']['latest'] or 'Ninguno'}")
                    console.print(f"  Siguiente esperado: {summary['sp_precios']['expected'] or 'Ninguno'}\n")
                    console.print("[1] Catch-up inteligente (Descargar cinta de precios faltante)")
                    console.print("[2] Descarga manual por fecha / Histórico completo")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "sp_precios")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_sp_precios()
                        
                elif opcion == "6":
                    # SP Cartera
                    console.print(f"[bold]Estado Cartera SP:[/] " + ("[yellow]Pendiente[/]" if summary["sp_cartera"]["due"] else "[green]Al día[/]"))
                    console.print(f"  Último en BD: {summary['sp_cartera']['latest'] or 'Ninguno'}")
                    console.print(f"  Siguiente esperado: {summary['sp_cartera']['expected'] or 'Ninguno'}\n")
                    console.print("[1] Catch-up inteligente (Descargar cartera faltante)")
                    console.print("[2] Descarga manual de período específico")
                    console.print("[3] Volver")
                    opt = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"])
                    if opt == "1":
                        run_catchup_for_kind(db, statuses, "sp_cartera")
                        Prompt.ask("\n[dim]Presiona ENTER para volver al menú[/]")
                    elif opt == "2":
                        manual_sp_cartera()

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

    with Database() as db:
        statuses = probe_all(db)
        if args.catchup:
            run_global_catchup(db, statuses)
        elif args.source:
            valid_kinds = ["bcentral", "cmf_emp", "cmf_bank", "sp_cuotas", "sp_precios", "sp_cartera"]
            if args.source not in valid_kinds:
                console.print(f"[bold red]Error:[/] Fuente '{args.source}' inválida. Debe ser una de: {', '.join(valid_kinds)}")
                sys.exit(1)
            run_catchup_for_kind(db, statuses, args.source)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Actualizador de Fuentes del Repositorio Financiero Chile")
    parser.add_argument("--catchup", action="store_true", help="Ejecuta catch-up global (completo) y sale")
    parser.add_argument("--source", type=str, default=None, help="Ejecuta catch-up de una fuente específica (bcentral, cmf_emp, cmf_bank, sp_cuotas, sp_precios, sp_cartera) and sale")
    parser.add_argument("--status", action="store_true", help="Muestra el diagnóstico de frescura y sale")
    
    args = parser.parse_args()
    
    if args.catchup or args.source or args.status:
        run_cli_args(args)
    else:
        try:
            run_menu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Operación cancelada. Saliendo...[/]")
            sys.exit(0)
