"""
main.py -- CLI principal del Repositorio Financiero Chile

Uso:
    python main.py fetch --all              # backfill completo
    python main.py fetch --series IPC       # busca y descarga por nombre
    python main.py fetch --id F073.IPC...   # descarga por código exacto
    python main.py query --series IPC       # muestra últimos datos
    python main.py status                   # estado del sistema y log reciente
    python main.py run-scheduler            # arranca el scheduler (bloqueante)
    python main.py search --term "cobre"    # busca series en la BDE
    python main.py list                     # lista series en la BD local
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en la salida (evita UnicodeEncodeError con emojis en la consola de Windows).
# Se usa reconfigure() y NO reasignar sys.stdout a un TextIOWrapper nuevo: al redirigir la
# salida a un pipe/archivo, el wrapper nuevo quedaba con su buffer sin vaciar al terminar el
# proceso (Python solo hace flush de los streams ORIGINALES), y toda la salida se perdía.
for _stream in (sys.stdout, sys.stderr):
    enc = getattr(_stream, "encoding", None)
    if enc and enc.lower() not in ("utf-8", "utf8") and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

# Setup básico de logging antes de importar módulos propios
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from config.settings import settings
from db.database import Database
from collectors.bcentral import BCentralCollector
from collectors.cmf import CMFCollector
from collectors.cmf_banks import CMFBankCollector
from collectors.cmf_insurers import CMFInsurerCollector
from collectors.cmf_brokers import CMFBrokerCollector
from collectors.sp_pensions import SPPensionCollector
from processors.normalizer import normalize_observations
from scheduler.jobs import run_all_series, run_fetch_by_frequency, create_scheduler, run_catchup

console = Console()


# ============================================================
# Helpers de visualización
# ============================================================

def _setup_file_logging():
    log_file = Path(settings.LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))


def _load_catalog() -> list[dict]:
    with open(settings.CATALOG_PATH, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)
    return catalog.get("bcentral", [])


def _find_series_by_name(name: str) -> list[dict]:
    """Busca series en el catálogo local por nombre (case-insensitive)."""
    catalog = _load_catalog()
    term = name.lower()
    return [s for s in catalog if term in s.get("name", "").lower() or term in s.get("id", "").lower()]


# ============================================================
# CLI
# ============================================================

@click.group()
@click.version_option("1.0.0", prog_name="FinRepo Chile")
def cli():
    """
    Repositorio de Informacion Financiera -- Chile

    Sistema de ingestion y consulta de datos macroeconomicos y financieros.
    """
    _setup_file_logging()


# ----------------------------------------------------------
# fetch
# ----------------------------------------------------------

@cli.command()
@click.option("--all", "fetch_all", is_flag=True, help="Descarga todas las series del catálogo")
@click.option("--series", "-s", default=None, help="Nombre parcial de la serie a descargar")
@click.option("--id", "series_id", default=None, help="Código exacto de la serie (BDE ID)")
@click.option("--from-date", default=None, help="Fecha inicio YYYY-MM-DD")
@click.option("--to-date", default=None, help="Fecha fin YYYY-MM-DD")
def fetch(fetch_all, series, series_id, from_date, to_date):
    """Descarga datos desde el Banco Central de Chile."""

    if not settings.has_bcentral_credentials:
        console.print(Panel(
            "[bold red]Credenciales del Banco Central no configuradas.[/]\n\n"
            "1. Copia [cyan].env.example[/] → [cyan].env[/]\n"
            "2. Regístrate en [link=https://si3.bcentral.cl]si3.bcentral.cl[/link]\n"
            "3. Completa [yellow]BCENTRAL_USER[/] y [yellow]BCENTRAL_PASS[/] en tu .env",
            title="⚠️  Configuración requerida",
            border_style="red",
        ))
        sys.exit(1)

    if fetch_all:
        console.print("[bold green]🚀 Descargando todas las series del catálogo...[/]")
        run_all_series()
        return

    if series_id:
        # Fetch por código exacto
        catalog = _load_catalog()
        meta = next((s for s in catalog if s["id"] == series_id), None)
        if not meta:
            # Crear metadata mínima si no está en el catálogo
            meta = {"id": series_id, "name": series_id, "source_id": "bcentral"}

        collector = BCentralCollector()
        raw = collector.fetch_series(series_id, from_date=from_date, to_date=to_date)
        clean = normalize_observations(raw, series_id)

        with Database() as db:
            db.upsert_series({**meta, "source_id": "bcentral"})
            new, updated = db.upsert_observations(series_id, clean)

        console.print(f"✅ [green]{len(clean)}[/] observaciones. +{new} nuevas, {updated} actualizadas.")
        return

    if series:
        matches = _find_series_by_name(series)
        if not matches:
            console.print(f"[yellow]No se encontró '{series}' en el catálogo local.[/]")
            console.print("Prueba: [cyan]python main.py search --term <término>[/] para buscar en la BDE")
            sys.exit(1)

        if len(matches) > 1:
            console.print(f"[yellow]Se encontraron {len(matches)} series coincidentes:[/]")
            for m in matches:
                console.print(f"  • [{m['id']}] {m['name']}")
            console.print("[dim]Usa --id <codigo> para especificar una.[/]")
            sys.exit(0)

        meta = matches[0]
        collector = BCentralCollector()
        raw = collector.fetch_series(meta["id"], from_date=from_date, to_date=to_date)
        clean = normalize_observations(raw, meta["id"])

        with Database() as db:
            db.upsert_series({**meta, "source_id": "bcentral"})
            new, updated = db.upsert_observations(meta["id"], clean)

        console.print(f"✅ [green]{meta['name']}[/]: {len(clean)} obs. +{new} nuevas, {updated} actualizadas.")
        return

    console.print("[red]Especifica --all, --series o --id[/]")
    sys.exit(1)


# ----------------------------------------------------------
# query
# ----------------------------------------------------------

@cli.command()
@click.option("--series", "-s", required=True, help="Nombre o ID de la serie a consultar")
@click.option("--from-date", default=None, help="Fecha inicio YYYY-MM-DD")
@click.option("--to-date", default=None, help="Fecha fin YYYY-MM-DD")
@click.option("--limit", "-n", default=20, help="Número de registros a mostrar (default: 20)")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "csv", "json"]), help="Formato de salida")
def query(series, from_date, to_date, limit, output_format):
    """Consulta datos almacenados en la base de datos local."""

    # Encontrar la serie
    matches = _find_series_by_name(series)
    if not matches:
        # Intentar búsqueda directa por ID en la BD
        with Database() as db:
            df = db.get_series(series, from_date=from_date, to_date=to_date)
        series_name = series
    else:
        meta = matches[0]
        with Database() as db:
            df = db.get_series(meta["id"], from_date=from_date, to_date=to_date)
        series_name = meta["name"]

    if df.empty:
        console.print(f"[yellow]No hay datos para '{series}'. Ejecuta primero:[/]")
        console.print(f"  python main.py fetch --series {series}")
        return

    # Mostrar últimos N registros
    df_show = df.tail(limit)

    if output_format == "csv":
        console.print(df_show.to_csv(index=False))
        return

    if output_format == "json":
        console.print(df_show.to_json(orient="records", date_format="iso"))
        return

    # Tabla rich
    table = Table(
        title=f"📊 {series_name}",
        box=box.ROUNDED,
        border_style="blue",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Fecha", style="dim", min_width=12)
    table.add_column("Valor", justify="right", style="bold green")

    for _, row in df_show.iterrows():
        table.add_row(str(row["date"]), f"{row['value']:,.4f}")

    console.print(table)
    console.print(f"\n[dim]Total en BD: {len(df)} registros | Mostrando últimos {len(df_show)}[/]")

    # Estadísticas básicas
    latest = df.iloc[-1]
    oldest = df.iloc[0]
    console.print(
        f"  Último:  [bold]{latest['value']:,.4f}[/] ({latest['date']})\n"
        f"  Mínimo:  {df['value'].min():,.4f} | Máximo: {df['value'].max():,.4f}\n"
        f"  Período: {oldest['date']} → {latest['date']}"
    )


# ----------------------------------------------------------
# status
# ----------------------------------------------------------

@cli.command()
def status():
    """Muestra el estado del sistema y el log de fetches recientes."""

    console.print(Panel(
        "[bold]Repositorio Financiero Chile[/] — Estado del sistema",
        subtitle=f"[dim]{datetime.now():%Y-%m-%d %H:%M:%S}[/]",
        border_style="blue",
    ))

    with Database() as db:
        # Resumen de series
        series_df = db.get_all_series()
        console.print(f"\n[bold]Series registradas:[/] {len(series_df)}")

        if not series_df.empty:
            t = Table(box=box.SIMPLE, header_style="bold")
            t.add_column("ID")
            t.add_column("Nombre")
            t.add_column("Frecuencia")
            t.add_column("Última actualización")

            for _, row in series_df.iterrows():
                latest = db.get_latest_value(row["id"])
                last_val = f"{latest['date']}" if latest else "—"
                t.add_row(
                    row["id"][:30] + "..." if len(row["id"]) > 30 else row["id"],
                    row["name"][:35] + "..." if len(str(row["name"])) > 35 else str(row["name"]),
                    row.get("frequency", "—"),
                    last_val,
                )
            console.print(t)

        # Log reciente
        log_df = db.get_fetch_history(limit=10)
        if not log_df.empty:
            console.print("\n[bold]Últimas 10 ejecuciones:[/]")
            t2 = Table(box=box.SIMPLE, header_style="bold")
            t2.add_column("Fecha")
            t2.add_column("Serie")
            t2.add_column("Estado")
            t2.add_column("+Nuevas")
            t2.add_column("Error")

            for _, row in log_df.iterrows():
                status_style = "green" if row["status"] == "ok" else "red"
                t2.add_row(
                    str(row["started_at"])[:16],
                    str(row["series_id"])[:30] if row["series_id"] else "—",
                    f"[{status_style}]{row['status']}[/]",
                    str(int(row["records_new"])) if row["records_new"] else "0",
                    str(row["error_msg"] or "")[:40],
                )
            console.print(t2)
        else:
            console.print("[dim]Sin historial de fetches aún. Ejecuta: python main.py fetch --all[/]")

    # Estado de fuentes de datos
    console.print()
    bcentral_ok = "[green]OK[/]" if settings.has_bcentral_credentials else "[red]Sin configurar[/]"
    console.print(f"  Banco Central (BDE API): {bcentral_ok}")
    console.print(f"  CMF (portal web scraping): [green]OK[/] [dim](sin credenciales requeridas)[/]")
    console.print(f"  SII (scraping publico):    [green]OK[/] [dim](sin credenciales requeridas)[/]")


# ----------------------------------------------------------
# search
# ----------------------------------------------------------

@cli.command()
@click.option("--term", "-t", required=True, help="Término de búsqueda")
def search(term):
    """Busca series disponibles en la BDE del Banco Central."""

    if not settings.has_bcentral_credentials:
        console.print("[red]Credenciales del Banco Central requeridas.[/]")
        sys.exit(1)

    console.print(f"🔍 Buscando '[yellow]{term}[/]' en la BDE...")
    collector = BCentralCollector()
    results = collector.search_series(term)

    if not results:
        console.print("[yellow]Sin resultados.[/]")
        return

    t = Table(title=f"Resultados para '{term}'", box=box.ROUNDED, border_style="cyan")
    t.add_column("ID / Código")
    t.add_column("Descripción")

    for r in results[:30]:
        t.add_row(r.get("id", ""), r.get("description", ""))

    console.print(t)
    if len(results) > 30:
        console.print(f"[dim]... y {len(results) - 30} más[/]")


# ----------------------------------------------------------
# list
# ----------------------------------------------------------

@cli.command(name="list")
@click.option("--source", default=None, help="Filtrar por fuente (bcentral, cmf, etc.)")
def list_series(source):
    """Lista las series registradas en la base de datos local."""

    with Database() as db:
        df = db.get_all_series(source_id=source)

    if df.empty:
        console.print("[yellow]No hay series registradas. Ejecuta: python main.py fetch --all[/]")
        return

    t = Table(title="Series en la base de datos", box=box.ROUNDED, border_style="blue")
    t.add_column("Fuente", style="dim")
    t.add_column("Categoría")
    t.add_column("Nombre")
    t.add_column("Freq.")
    t.add_column("ID")

    for _, row in df.iterrows():
        t.add_row(
            row.get("source_id", ""),
            row.get("category", ""),
            row.get("name", ""),
            row.get("frequency", ""),
            row.get("id", "")[:40],
        )

    console.print(t)


# ----------------------------------------------------------
# fetch-cmf
# ----------------------------------------------------------

@cli.command(name="fetch-cmf")
@click.option("--period", "-p", required=False, type=int, default=None, help="Período YYYYMM (ej. 202512) o YYYY (ej. 2024)")
@click.option("--history", is_flag=True, help="Descarga e ingesta todo el historial completo (2018 a 2026)")
@click.option("--force", "-f", is_flag=True, help="Fuerza la descarga del archivo web ignorando caché")
def fetch_cmf(period, history, force):
    """Descarga e ingesta estados financieros trimestrales de la CMF."""

    if not period and not history:
        console.print("[bold red]Error: Debes especificar un período (--period) o activar la carga histórica (--history).[/]")
        sys.exit(1)

    if history:
        # Períodos históricos organizados por la forma en que los publica la CMF:
        # Años 2018-2024 se publican anualmente (un solo archivo contiene los 4 trimestres).
        # A partir de 2025, se publican como trimestres individuales.
        periods_to_fetch = [
            2018, 2019, 2020, 2021, 2022, 2023, 2024,
            202503, 202506, 202509, 202512, 202603
        ]
        info_msg = "Carga histórica completa (2018-2026)"
    else:
        periods_to_fetch = [period]
        info_msg = f"Período específico: {period}"

    console.print(Panel(
        f"[bold green]🚀 Iniciando ingesta de Estados Financieros CMF[/]\n\n"
        f"  • Modo: [cyan]{info_msg}[/]\n"
        f"  • Forzar descarga: [yellow]{force}[/]",
        title="📥 Ingestador CMF",
        border_style="green",
    ))

    try:
        collector = CMFCollector()
        total_inserted = 0

        with Database() as db:
            for p in periods_to_fetch:
                console.print(f"\n[bold cyan]⏳ Procesando período: {p}...[/]")
                records = collector.fetch_period(p, force_download=force)

                if not records:
                    console.print(f"[yellow]⚠️  Omitiendo {p}: Sin registros parseados en el archivo.[/]")
                    continue

                inserted = db.insert_cmf_records(p, records)
                console.print(f"[green]✓ Ingestados {inserted:,} registros para el período {p}.[/]")
                total_inserted += inserted

        console.print(f"\n[bold green]✅ Ingesta CMF finalizada con éxito![/]")
        console.print(f"  • Total registros procesados e insertados: [bold]{total_inserted:,}[/]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta CMF:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# query-cmf
# ----------------------------------------------------------

@cli.command(name="query-cmf")
@click.option("--rut", "-r", default=None, help="RUT de la empresa (ej. 60503000)")
@click.option("--company", "-c", default=None, help="Nombre parcial de la empresa")
@click.option("--period", "-p", default=None, type=int, help="Período YYYYMM (ej. 202512)")
@click.option("--limit", "-n", default=50, help="Límite de filas (default: 50)")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "csv", "json"]), help="Formato de salida")
def query_cmf(rut, company, period, limit, output_format):
    """Consulta estados financieros corporativos de la CMF localmente."""

    with Database() as db:
        df = db.query_cmf_statements(rut=rut, company=company, period=period, limit=limit)

    if df.empty:
        console.print("[yellow]No se encontraron estados financieros con los filtros especificados.[/]")
        console.print("[dim]Asegúrate de haber descargado los datos con: python main.py fetch-cmf --period <YYYYMM>[/]")
        return

    if output_format == "csv":
        console.print(df.to_csv(index=False))
        return

    if output_format == "json":
        # Asegurar UTF-8 en la salida del JSON
        console.print(df.to_json(orient="records", force_ascii=False, indent=2))
        return

    # Formato de tabla estilizada Rich
    table = Table(
        title=f"💼 Estados Financieros CMF (Muestra de {len(df)} filas)",
        box=box.ROUNDED,
        border_style="green",
        show_header=True,
        header_style="bold green",
    )

    table.add_column("Período", style="dim", justify="center")
    table.add_column("Empresa", max_width=35)
    table.add_column("RUT", justify="center")
    table.add_column("T.", justify="center", style="dim")
    table.add_column("Mon.", justify="center", style="dim")
    table.add_column("Grupo", style="cyan")
    table.add_column("Cuenta/Concepto", max_width=45)
    table.add_column("Monto", justify="right", style="bold yellow")

    for _, row in df.iterrows():
        # Formatear montos con miles según moneda
        try:
            val_formatted = f"{row['value']:,.0f}" if row['currency'] == 'CLP' else f"{row['value']:,.2f}"
        except Exception:
            val_formatted = str(row['value'])

        table.add_row(
            str(row["period"]),
            str(row["company_name"]),
            str(row["rut"]),
            str(row["report_type"]),
            str(row["currency"]),
            str(row["statement_group"] or "—"),
            str(row["account_name"]),
            val_formatted
        )

    console.print(table)
    console.print(f"\n[dim]Filtros aplicados: RUT={rut or 'Todos'} | Empresa={company or 'Todas'} | Período={period or 'Todos'}[/]")


# ----------------------------------------------------------
# fetch-banks
# ----------------------------------------------------------

@cli.command(name="fetch-banks")
@click.option("--year", "-y", type=int, default=None, help="Año de consulta (ej. 2025)")
@click.option("--month", "-m", type=int, default=None, help="Mes de consulta (1 a 12)")
@click.option("--bank", "-b", default=None, help="Código SBIF de un banco específico (ej. '001')")
@click.option("--history", is_flag=True, help="Carga histórica mensual completa para los bancos principales")
@click.option("--year-start", type=int, default=2024, help="Primer año del backfill con --history (default 2024)")
@click.option("--year-end", type=int, default=None, help="Último año del backfill con --history (default: año actual)")
@click.option("--force", "-f", is_flag=True, help="Fuerza la descarga desde la API ignorando la caché local")
def fetch_banks(year, month, bank, history, year_start, year_end, force):
    """Descarga e ingesta estados financieros mensuales de bancos de la CMF."""

    if not history and (not year or not month):
        console.print("[bold red]Error: Debes especificar año (--year) y mes (--month) o activar la carga histórica (--history).[/]")
        sys.exit(1)

    collector = CMFBankCollector()

    # Definir bancos a descargar
    if bank:
        bank_codes = [str(bank).strip().zfill(3)]
    else:
        # Si no se especifica banco, descargar todos los bancos del catálogo principal
        bank_codes = list(collector.BANKS_CATALOG.keys())

    # Definir períodos a descargar
    if history:
        # Backfill histórico mensual [year_start, year_end]; el año final se
        # trunca al mes anterior al actual (el mes en curso aún no publica).
        from datetime import date as _date
        today = _date.today()
        y1 = year_end or today.year
        periods = []
        for y in range(year_start, y1 + 1):
            last_m = (today.month - 1) if y == today.year else 12
            for m in range(1, last_m + 1):
                periods.append((y, m))
        info_msg = f"Carga histórica mensual ({year_start}-{y1}) para bancos"
    else:
        periods = [(year, month)]
        info_msg = f"Período específico: {year}-{month:02d}"

    console.print(Panel(
        f"[bold green]🚀 Iniciando ingesta de Estados Financieros de Bancos (API CMF)[/]\n\n"
        f"  • Modo: [cyan]{info_msg}[/]\n"
        f"  • Cantidad de bancos catalogados: [yellow]{len(bank_codes)}[/]\n"
        f"  • Forzar descarga: [yellow]{force}[/]",
        title="📥 Ingestador Bancario CMF",
        border_style="green",
    ))

    try:
        total_inserted = 0
        with Database() as db:
            for y, m in periods:
                for b_code in bank_codes:
                    b_name = collector.BANKS_CATALOG.get(b_code, f"BANCO {b_code}")
                    console.print(f"\n[bold cyan]⏳ Procesando {b_name} ({b_code}) para {y}-{m:02d}...[/]")

                    # 1. Descargar e Ingestar Balance (report_type='balance')
                    records_bal = collector.fetch_bank_report(y, m, b_code, report_type="balance", force_download=force)
                    if records_bal:
                        inserted_bal = db.insert_bank_records(y, m, b_code, "balance", records_bal)
                        console.print(f"    [green]✓ Balance: {inserted_bal:,} registros insertados.[/]")
                        total_inserted += inserted_bal
                    else:
                        console.print(f"    [yellow]⚠ Balance: Sin datos devueltos por la API.[/]")

                    # 2. Descargar e Ingestar Estado de Resultados (report_type='resultado')
                    records_res = collector.fetch_bank_report(y, m, b_code, report_type="resultado", force_download=force)
                    if records_res:
                        inserted_res = db.insert_bank_records(y, m, b_code, "resultado", records_res)
                        console.print(f"    [green]✓ Resultados: {inserted_res:,} registros insertados.[/]")
                        total_inserted += inserted_res
                    else:
                        console.print(f"    [yellow]⚠ Resultados: Sin datos devueltos por la API.[/]")

        console.print(f"\n[bold green]✅ Ingesta bancaria finalizada con éxito![/]")
        console.print(f"  • Total registros procesados e insertados en DuckDB: [bold]{total_inserted:,}[/]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta bancaria:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# fetch-seguros
# ----------------------------------------------------------

@cli.command(name="fetch-seguros")
@click.option("--ramo", type=click.Choice(["vida", "generales", "ambos"]), default="ambos",
              help="Ramo de seguros a cargar (default: ambos)")
@click.option("--year", "-y", type=int, default=None, help="Año de consulta (ej. 2026)")
@click.option("--month", "-m", type=int, default=None, help="Mes de cierre trimestral (3, 6, 9 o 12)")
@click.option("--freq", type=click.Choice(["trimestral", "anual"]), default="trimestral",
              help="Versión de la FECU (default trimestral)")
@click.option("--history", is_flag=True, help="Carga histórica trimestral completa de todas las compañías")
@click.option("--year-start", type=int, default=2015, help="Primer año del backfill con --history (default 2015)")
@click.option("--year-end", type=int, default=None, help="Último año del backfill con --history (default: año actual)")
@click.option("--force", "-f", is_flag=True, help="Fuerza la descarga ignorando la caché local")
def fetch_seguros(ramo, year, month, freq, history, year_start, year_end, force):
    """Descarga e ingesta EEFF de compañías de seguros de la CMF (FECU, todas de una vez)."""

    if not history and (not year or not month):
        console.print("[bold red]Error: Debes especificar año (--year) y mes (--month) o activar --history.[/]")
        sys.exit(1)

    ramos = ["vida", "generales"] if ramo == "ambos" else [ramo]

    # Períodos trimestrales (03, 06, 09, 12) hasta el último cierre ya publicado.
    if history:
        from datetime import date as _date
        today = _date.today()
        y1 = year_end or today.year
        last_q_month = ((today.month - 1) // 3) * 3  # último cierre trimestral posible
        periods = []
        for y in range(year_start, y1 + 1):
            for mm in (3, 6, 9, 12):
                if y == today.year and mm > last_q_month:
                    continue
                periods.append((y, mm))
        info_msg = f"Carga histórica trimestral ({year_start}-{y1})"
    else:
        periods = [(year, month)]
        info_msg = f"Período específico: {year}-{month:02d} ({freq})"

    console.print(Panel(
        f"[bold green]🚀 Ingesta de EEFF de Compañías de Seguros (FECU CMF)[/]\n\n"
        f"  • Ramo(s): [cyan]{', '.join(ramos)}[/]\n"
        f"  • Modo: [cyan]{info_msg}[/]\n"
        f"  • Frecuencia: [yellow]{freq}[/]\n"
        f"  • Forzar descarga: [yellow]{force}[/]",
        title="📥 Ingestador de Seguros CMF", border_style="green",
    ))

    try:
        total_inserted = 0
        with Database() as db:
            for ins_type in ramos:
                collector = CMFInsurerCollector(insurance_type=ins_type)
                for y, mm in periods:
                    console.print(f"\n[bold cyan]⏳ Procesando seguros {ins_type} {y}-{mm:02d} ({freq})...[/]")
                    recs = collector.fetch_period(y, mm, freq=freq, force_download=force)
                    if recs:
                        n = db.insert_insurer_records(int(f"{y}{mm:02d}"), freq, recs, insurance_type=ins_type)
                        comps = len({r["rut"] for r in recs})
                        console.print(f"    [green]✓ {n:,} registros · {comps} compañías.[/]")
                        total_inserted += n
                    else:
                        console.print(f"    [yellow]⚠ Sin datos para {ins_type} {y}-{mm:02d}.[/]")

        console.print(f"\n[bold green]✅ Ingesta de seguros finalizada![/]")
        console.print(f"  • Total registros insertados en DuckDB: [bold]{total_inserted:,}[/]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta de seguros:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# import-cartera-seguros
# ----------------------------------------------------------

def _ingest_cartera_zip(db, zp):
    """Parsea e ingesta un ZIP de cartera (B.8 Control + B.1 Renta Fija). Devuelve (n_ctrl, n_fi)."""
    from collectors.seguros_cartera import parse_zip_control, parse_zip_fixed_income

    n_ctrl = n_fi = 0
    recs = parse_zip_control(zp)
    if recs:
        per, itype = recs[0]["period"], recs[0]["insurance_type"]
        n_ctrl = db.insert_insurer_portfolio_control(per, itype, recs)
        cias = len({r["rut"] for r in recs})
        codes = len({r["investment_code"] for r in recs})
        console.print(f"    [green]✓ Control: {n_ctrl:,} filas · {cias} compañías · "
                      f"{codes} tipos de inversión.[/]")
    else:
        console.print(f"    [yellow]⚠ Sin registros de control.[/]")
    fi = parse_zip_fixed_income(zp)
    if fi:
        per, itype = fi[0]["period"], fi[0]["insurance_type"]
        n_fi = db.insert_insurer_portfolio_fixed_income(per, itype, fi)
        emis = len({r["rut_emisor"] for r in fi if r["rut_emisor"]})
        console.print(f"    [green]✓ Renta fija: {n_fi:,} instrumentos · "
                      f"{emis} emisores distintos.[/]")
    return n_ctrl, n_fi


@cli.command(name="fetch-cartera-seguros")
@click.option("--period", "-p", type=int, default=None,
              help="Descargar e ingestar un período YYYYMM (ej. 202606)")
@click.option("--latest", is_flag=True, help="Solo el período más reciente publicado en la CMF")
@click.option("--history", is_flag=True, help="Todos los períodos disponibles (2016-01 en adelante)")
@click.option("--since", type=int, default=None,
              help="Con --history, limita al rango desde este período YYYYMM (ej. 202001)")
@click.option("--ramo", type=click.Choice(["vida", "generales", "ambos"]), default="ambos",
              help="Ramo de seguros a cargar (default: ambos)")
@click.option("--raw-dir", default="data/seguros_cartera_raw",
              help="Carpeta donde se guardan los ZIP descargados")
@click.option("--force", "-f", is_flag=True, help="Fuerza la descarga ignorando la caché local")
def fetch_cartera_seguros(period, latest, history, since, ramo, raw_dir, force):
    """Descarga e ingesta la cartera de inversiones de seguros (Circular 1.835) desde la CMF.

    El CAPTCHA de la página de descarga no se valida del lado servidor, así que la baja es
    automática. Usa --period YYYYMM, --latest o --history (acotable con --since). Cubre
    ambos ramos (--ramo).
    """
    from collectors.seguros_cartera import (download_cartera, list_remote_periods)

    if not (period or latest or history):
        console.print("[bold red]Error: indica --period YYYYMM, --latest o --history.[/]")
        sys.exit(1)

    ramos = ["vida", "generales"] if ramo == "ambos" else [ramo]
    modo = "histórico" + (f" desde {since}" if since else "") if history \
        else ("último" if latest else f"período {period}")

    console.print(Panel(
        f"[bold green]🚀 Cartera de inversiones de seguros (Circular 1.835) — descarga + ingesta[/]\n\n"
        f"  • Ramos: [cyan]{', '.join(ramos)}[/]\n"
        f"  • Modo: [cyan]{modo}[/]\n"
        f"  • Forzar descarga: [yellow]{force}[/]",
        title="📥 Colector de Cartera de Seguros", border_style="green",
    ))

    try:
        total_ctrl = total_fi = ok = attempted = 0
        with Database() as db:
            for r in ramos:
                if history:
                    periods = list_remote_periods(r)
                    if since:
                        periods = [p for p in periods if p >= since]
                elif latest:
                    periods = list_remote_periods(r)[:1]
                else:
                    periods = [period]
                console.print(f"\n[bold]— Ramo {r}: {len(periods)} período(s) —[/]")
                for per in periods:
                    attempted += 1
                    console.print(f"[bold cyan]⏳ {r} {per}: descargando...[/]")
                    zp = download_cartera(per, r, raw_dir, force=force)
                    if not zp:
                        console.print(f"    [yellow]⚠ {r} {per} no disponible en la CMF, se omite.[/]")
                        continue
                    n_ctrl, n_fi = _ingest_cartera_zip(db, zp)
                    total_ctrl += n_ctrl
                    total_fi += n_fi
                    ok += 1

        console.print(f"\n[bold green]✅ Colección finalizada![/] "
                      f"{ok}/{attempted} períodos.")
        console.print(f"  • Control: [bold]{total_ctrl:,}[/] filas · "
                      f"Renta fija: [bold]{total_fi:,}[/] instrumentos "
                      f"[dim](valores en miles de pesos / unidad del instrumento)[/]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la colección:[/] {e}")
        sys.exit(1)


@cli.command(name="import-cartera-seguros")
@click.option("--raw-dir", default="data/seguros_cartera_raw",
              help="Carpeta con los ZIP mensuales ya presentes localmente")
@click.option("--period", "-p", type=int, default=None,
              help="Importar solo un período YYYYMM (por defecto: todos los ZIP presentes)")
def import_cartera_seguros(raw_dir, period):
    """Importa la cartera de seguros (Circular 1.835) desde ZIP YA presentes en la carpeta.

    Fallback offline de `fetch-cartera-seguros`: no descarga nada, solo lee lo que ya está
    en `raw_dir` (útil si se bajó un ZIP a mano o para reingestar sin volver a la red).
    """
    from collectors.seguros_cartera import list_available_zips

    zips = list_available_zips(raw_dir)
    if period:
        zips = [z for z in zips if z.name.startswith(str(period))]
    if not zips:
        console.print(f"[bold red]No hay ZIP válidos en '{raw_dir}'[/] "
                      f"(se esperan archivos tipo 202605v.zip).")
        sys.exit(1)

    console.print(Panel(
        f"[bold green]🚀 Importando cartera de inversiones de seguros (Circular 1.835)[/]\n\n"
        f"  • Carpeta: [cyan]{raw_dir}[/]\n"
        f"  • ZIP a procesar: [yellow]{', '.join(z.name for z in zips)}[/]\n"
        f"  • Archivos: [cyan]B.8 Control[/] (composición) + [cyan]B.1 Renta Fija[/] "
        f"(detalle reconciliado)",
        title="📥 Importador de Cartera de Seguros", border_style="green",
    ))

    try:
        total_ctrl = total_fi = 0
        with Database() as db:
            for zp in zips:
                console.print(f"\n[bold cyan]⏳ Procesando {zp.name}...[/]")
                n_ctrl, n_fi = _ingest_cartera_zip(db, zp)
                total_ctrl += n_ctrl
                total_fi += n_fi

        console.print(f"\n[bold green]✅ Importación finalizada![/]")
        console.print(f"  • Control: [bold]{total_ctrl:,}[/] filas · "
                      f"Renta fija: [bold]{total_fi:,}[/] instrumentos "
                      f"[dim](valores en miles de pesos / unidad del instrumento)[/]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la importación:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# fetch-intermediarios
# ----------------------------------------------------------

@cli.command(name="fetch-intermediarios")
@click.option("--year", "-y", type=int, default=None, help="Año de consulta (ej. 2026)")
@click.option("--month", "-m", type=int, default=None, help="Mes de cierre trimestral (3, 6, 9 o 12)")
@click.option("--history", is_flag=True, help="Carga histórica trimestral completa")
@click.option("--year-start", type=int, default=2015, help="Primer año del backfill con --history (default 2015)")
@click.option("--year-end", type=int, default=None, help="Último año del backfill con --history (default: año actual)")
@click.option("--force", "-f", is_flag=True, help="Fuerza la descarga ignorando la caché local")
def fetch_intermediarios(year, month, history, year_start, year_end, force):
    """Descarga e ingesta EEFF de corredores de bolsa y agentes de valores (FECU IFRS CMF)."""

    if not history and (not year or not month):
        console.print("[bold red]Error: Debes especificar año (--year) y mes (--month) o activar --history.[/]")
        sys.exit(1)

    collector = CMFBrokerCollector()

    if history:
        from datetime import date as _date
        today = _date.today()
        y1 = year_end or today.year
        last_q_month = ((today.month - 1) // 3) * 3
        periods = []
        for y in range(year_start, y1 + 1):
            for mm in (3, 6, 9, 12):
                if y == today.year and mm > last_q_month:
                    continue
                periods.append((y, mm))
        info_msg = f"Carga histórica trimestral ({year_start}-{y1})"
    else:
        periods = [(year, month)]
        info_msg = f"Período específico: {year}-{month:02d}"

    console.print(Panel(
        f"[bold green]🚀 Ingesta de EEFF de Intermediarios de Valores (FECU IFRS CMF)[/]\n\n"
        f"  • Alcance: [cyan]corredores de bolsa + agentes de valores[/]\n"
        f"  • Modo: [cyan]{info_msg}[/]\n"
        f"  • Forzar descarga: [yellow]{force}[/]",
        title="📥 Ingestador de Intermediarios CMF", border_style="green",
    ))

    try:
        total_inserted = 0
        with Database() as db:
            for y, mm in periods:
                console.print(f"\n[bold cyan]⏳ Procesando intermediarios {y}-{mm:02d}...[/]")
                recs = collector.fetch_period(y, mm, force_download=force)
                if recs:
                    n = db.insert_broker_records(int(f"{y}{mm:02d}"), recs)
                    ents = len({r["rut"] for r in recs})
                    tipos = {}
                    for r in recs:
                        tipos[r["broker_type"]] = tipos.get(r["broker_type"], 0) + 1
                    console.print(f"    [green]✓ {n:,} registros · {ents} entidades · {list(tipos)}[/]")
                    total_inserted += n
                else:
                    console.print(f"    [yellow]⚠ Sin datos para {y}-{mm:02d}.[/]")

        console.print(f"\n[bold green]✅ Ingesta de intermediarios finalizada![/]")
        console.print(f"  • Total registros insertados en DuckDB: [bold]{total_inserted:,}[/]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta de intermediarios:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# query-banks
# ----------------------------------------------------------

@cli.command(name="query-banks")
@click.option("--bank", "-b", default=None, help="Código SBIF o Razón Social del Banco")
@click.option("--period", "-p", default=None, type=int, help="Período YYYYMM (ej. 202512)")
@click.option("--account", "-a", default=None, help="Código de cuenta contable (ej. 100000000 para Total Activos)")
@click.option("--type", "report_type", default=None, type=click.Choice(["balance", "resultado"]), help="Tipo de reporte (balance o resultado)")
@click.option("--limit", "-n", default=50, help="Límite de filas a mostrar (default: 50)")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "csv", "json"]), help="Formato de salida")
def query_banks(bank, period, account, report_type, limit, output_format):
    """Consulta estados financieros mensuales de bancos desde DuckDB."""

    with Database() as db:
        df = db.query_bank_statements(
            bank_code=bank, period=period, account_code=account, report_type=report_type, limit=limit
        )

    if df.empty:
        console.print("[yellow]No se encontraron registros bancarios con los filtros especificados.[/]")
        console.print("[dim]Prueba descargando los datos con: python main.py fetch-banks --year 2025 --month 12[/]")
        return

    if output_format == "csv":
        console.print(df.to_csv(index=False))
        return

    if output_format == "json":
        console.print(df.to_json(orient="records", force_ascii=False, indent=2))
        return

    # Tabla Rich para consola
    table = Table(
        title=f"🏦 Balances y Resultados Bancarios CMF (Muestra de {len(df)} filas)",
        box=box.ROUNDED,
        border_style="green",
        show_header=True,
        header_style="bold green",
    )

    table.add_column("Período", style="dim", justify="center")
    table.add_column("Banco", max_width=25)
    table.add_column("Código", justify="center", style="dim")
    table.add_column("Cuenta", justify="center")
    table.add_column("Glosa / Concepto", max_width=35)
    table.add_column("CLP No Reaj.", justify="right", style="cyan")
    table.add_column("Reaj. IPC (UF)", justify="right", style="cyan")
    table.add_column("Reaj. TC (USD)", justify="right", style="cyan")
    table.add_column("M. Extranjera", justify="right", style="cyan")
    table.add_column("Monto Total", justify="right", style="bold yellow")

    for _, row in df.iterrows():
        # Estandarizar montos de desgloses de moneda
        def fmt_val(v):
            if v is None or v == 0.0:
                return "—"
            return f"{v:,.0f}"

        # Reajuste por TC solo aplica a balances
        reaj_tc = fmt_val(row["val_clp_reaj_tc"]) if row["report_type"] == "balance" else "N/A"

        table.add_row(
            str(row["period"]),
            str(row["bank_name"]),
            str(row["bank_code"]),
            str(row["account_code"]),
            str(row["account_name"]),
            fmt_val(row["val_clp_no_reaj"]),
            fmt_val(row["val_clp_reaj_ipc"]),
            reaj_tc,
            fmt_val(row["val_extranjera"]),
            f"{row['val_total']:,.0f}"
        )

    console.print(table)
    console.print(f"\n[dim]Filtros aplicados: Banco={bank or 'Todos'} | Período={period or 'Todos'} | Cuenta={account or 'Todas'} | Tipo={report_type or 'Todos'}[/]")


# ----------------------------------------------------------
# fetch-sp-cuotas
# ----------------------------------------------------------

@cli.command(name="fetch-sp-cuotas")
@click.option("--year-start", "-s", type=int, default=2002, help="Año de inicio (default: 2002)")
@click.option("--year-end", "-e", type=int, default=None, help="Año de término (default: año actual)")
@click.option("--fund", "-f", type=click.Choice(["A", "B", "C", "D", "E"]), default=None, help="Tipo de fondo (A a E) o None para todos")
def fetch_sp_cuotas(year_start, year_end, fund):
    """Descarga e ingesta valores cuota de multifondos de la SP."""
    if not year_end:
        year_end = datetime.now().year

    collector = SPPensionCollector()
    funds = [fund] if fund else ["A", "B", "C", "D", "E"]

    console.print(Panel(
        f"[bold green]🚀 Iniciando ingesta de Valores Cuota SP[/]\n\n"
        f"  • Rango de años: [cyan]{year_start} - {year_end}[/]\n"
        f"  • Fondos a procesar: [yellow]{', '.join(funds)}[/]",
        title="📥 Ingestador Cuotas SP",
        border_style="green",
    ))

    try:
        total_inserted = 0
        with Database() as db:
            fecconf = collector.fetch_fecconf()
            for f_type in funds:
                console.print(f"\n[bold cyan]⏳ Descargando fondo {f_type}...[/]")
                records = collector.fetch_quota_values(year_start, year_end, fund_type=f_type, fecconf=fecconf)
                
                if not records:
                    console.print(f"[yellow]⚠️ No se obtuvieron registros para el fondo {f_type}.[/]")
                    continue

                inserted = db.insert_sp_quota_values(records)
                console.print(f"[green]✓ Ingestados {inserted:,} registros para el fondo {f_type}.[/]")
                total_inserted += inserted

        console.print(f"\n[bold green]✅ Ingesta de Valores Cuota finalizada con éxito![/]")
        console.print(f"  • Total registros procesados: [bold]{total_inserted:,}[/]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta de cuotas SP:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# fetch-sp-cartera
# ----------------------------------------------------------

@cli.command(name="fetch-sp-cartera")
@click.option("--period", "-p", required=True, type=int, help="Período YYYYMM (ej. 202601)")
def fetch_sp_cartera(period):
    """Descarga e ingesta la cartera mensual desagregada de la SP."""
    collector = SPPensionCollector()

    console.print(Panel(
        f"[bold green]🚀 Iniciando ingesta de Cartera Desagregada SP[/]\n\n"
        f"  • Período: [cyan]{period}[/]",
        title="📥 Ingestador Carteras SP",
        border_style="green",
    ))

    try:
        records = collector.fetch_portfolio(period)
        if not records:
            console.print(f"[yellow]⚠️ No se obtuvieron registros de cartera para el período {period} (puede no estar disponible).[/]")
            return

        with Database() as db:
            db_period = f"{str(period)[:4]}-{str(period)[4:]}"
            inserted = db.insert_sp_portfolio_holdings(db_period, records)

        console.print(f"\n[bold green]✅ Ingesta de Cartera finalizada con éxito![/]")
        console.print(f"  • Total registros de activos ingresados: [bold]{inserted:,}[/]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta de la cartera SP:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# fetch-sp-precios
# ----------------------------------------------------------

@cli.command(name="fetch-sp-precios")
@click.option("--date", "date_val", default=None, help="Fecha específica YYYY-MM-DD")
@click.option("--history", is_flag=True, help="Ejecuta backfill completo: 5 años diarios y anteriores 5 años semanales")
def fetch_sp_precios(date_val, history):
    """Descarga e ingesta precios diarios de instrumentos financieros de la SP."""
    if not date_val and not history:
        console.print("[bold red]Error: Debes especificar una fecha (--date) o activar el backfill histórico (--history).[/]")
        sys.exit(1)

    collector = SPPensionCollector()
    dates_to_fetch = []

    if history:
        import datetime as dt_mod
        today = dt_mod.date.today()
        
        # Últimos 5 años diarios
        five_years_ago = today - dt_mod.timedelta(days=5*365)
        curr = five_years_ago
        while curr <= today:
            if curr.weekday() < 5:
                dates_to_fetch.append(curr.strftime("%Y-%m-%d"))
            curr += dt_mod.timedelta(days=1)
            
        # Anteriores 5 años (semanal - miércoles)
        ten_years_ago = today - dt_mod.timedelta(days=10*365)
        curr = ten_years_ago
        while curr < five_years_ago:
            if curr.weekday() == 2:
                dates_to_fetch.append(curr.strftime("%Y-%m-%d"))
            curr += dt_mod.timedelta(days=1)
            
        info_msg = f"Backfill histórico ({len(dates_to_fetch)} fechas planificadas)"
    else:
        dates_to_fetch = [date_val]
        info_msg = f"Fecha única: {date_val}"

    console.print(Panel(
        f"[bold green]🚀 Iniciando ingesta de Cinta de Precios Diaria SP[/]\n\n"
        f"  • Modo: [cyan]{info_msg}[/]",
        title="📥 Ingestador Precios SP",
        border_style="green",
    ))

    import time
    try:
        total_inserted = 0
        with Database() as db:
            for idx, d_str in enumerate(dates_to_fetch):
                if history and idx > 0 and idx % 20 == 0:
                    console.print(f"[dim]Procesados {idx}/{len(dates_to_fetch)} días...[/]")
                
                records = collector.fetch_daily_prices(d_str)
                if records:
                    inserted = db.insert_sp_instrument_prices(records)
                    total_inserted += inserted
                    if history:
                        time.sleep(0.15)
                else:
                    if not history:
                        console.print(f"[yellow]⚠️ No hay cinta de precios disponible para la fecha {d_str}.[/]")

        console.print(f"\n[bold green]✅ Ingesta de precios finalizada con éxito![/]")
        console.print(f"  • Total registros de instrumentos ingresados: [bold]{total_inserted:,}[/]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Error durante la ingesta de precios SP:[/] {e}")
        sys.exit(1)


# ----------------------------------------------------------
# query-sp-cuotas
# ----------------------------------------------------------

@cli.command(name="query-sp-cuotas")
@click.option("--afp", default=None, help="Nombre de la AFP (ej. CAPITAL, HABITAT)")
@click.option("--fund", "-f", type=click.Choice(["A", "B", "C", "D", "E"]), default=None, help="Tipo de fondo")
@click.option("--from-date", default=None, help="Fecha inicio YYYY-MM-DD")
@click.option("--to-date", default=None, help="Fecha fin YYYY-MM-DD")
@click.option("--limit", "-n", default=20, help="Límite de filas (default: 20)")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "csv", "json"]), help="Formato de salida")
def query_sp_cuotas(afp, fund, from_date, to_date, limit, output_format):
    """Consulta valores cuota y patrimonio de la SP desde DuckDB."""
    with Database() as db:
        df = db.query_sp_quota_values(
            afp=afp, fund=fund, from_date=from_date, to_date=to_date, limit=limit
        )

    if df.empty:
        console.print("[yellow]No se encontraron registros de valores cuota con los filtros especificados.[/]")
        return

    if output_format == "csv":
        console.print(df.to_csv(index=False))
        return

    if output_format == "json":
        console.print(df.to_json(orient="records", force_ascii=False, indent=2))
        return

    table = Table(
        title="📈 Valores Cuota y Patrimonio (Superintendencia de Pensiones)",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Fecha", style="dim", justify="center")
    table.add_column("AFP")
    table.add_column("Fondo", justify="center", style="bold yellow")
    table.add_column("Valor Cuota ($)", justify="right", style="green")
    table.add_column("Patrimonio ($)", justify="right", style="cyan")

    for _, row in df.iterrows():
        table.add_row(
            str(row["date"]),
            str(row["afp_name"]),
            str(row["fund_type"]),
            f"{row['quota_value']:,.2f}",
            f"{row['equity_value']:,.0f}"
        )

    console.print(table)
    console.print(f"\n[dim]Mostrando últimos {len(df)} registros[/]")


# ----------------------------------------------------------
# query-sp-precios
# ----------------------------------------------------------

@cli.command(name="query-sp-precios")
@click.option("--instrument", "-i", default=None, help="Nemotécnico o RUT del emisor (ej. AESANDES, ENELAM)")
@click.option("--date", "-d", default=None, help="Fecha específica YYYY-MM-DD")
@click.option("--limit", "-n", default=20, help="Límite de filas (default: 20)")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "csv", "json"]), help="Formato de salida")
def query_sp_precios(instrument, date, limit, output_format):
    """Consulta la cinta diaria de precios de instrumentos financieros de la SP."""
    with Database() as db:
        df = db.query_sp_instrument_prices(
            instrument_id=instrument, date=date, limit=limit
        )

    if df.empty:
        console.print("[yellow]No se encontraron registros de precios para los filtros especificados.[/]")
        return

    if output_format == "csv":
        console.print(df.to_csv(index=False))
        return

    if output_format == "json":
        console.print(df.to_json(orient="records", force_ascii=False, indent=2))
        return

    table = Table(
        title="💵 Cinta de Precios Diaria de Instrumentos (SP)",
        box=box.ROUNDED,
        border_style="magenta",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Fecha", style="dim", justify="center")
    table.add_column("Instrumento/Ticker", style="bold yellow")
    table.add_column("Tipo", justify="center", style="cyan")
    table.add_column("Moneda", justify="center")
    table.add_column("Precio de Cierre", justify="right", style="green")

    for _, row in df.iterrows():
        table.add_row(
            str(row["date"]),
            str(row["instrument_id"]),
            str(row["instrument_type"]),
            str(row["currency"]),
            f"{row['price']:,.4f}"
        )

    console.print(table)
    console.print(f"\n[dim]Mostrando últimos {len(df)} registros[/]")


# ----------------------------------------------------------
# catchup
# ----------------------------------------------------------

@cli.command(name="catchup")
@click.option("--dry-run", is_flag=True, help="Solo muestra el estado de frescura; no descarga nada.")
def catchup(dry_run):
    """Ingesta dirigida por frescura: descarga datos nuevos en cuanto se publican."""

    console.print(Panel(
        "[bold green]🩺 Catch-up por frescura[/]\n\n"
        + ("[yellow]Modo dry-run: solo diagnóstico, sin descargas.[/]" if dry_run
           else "[cyan]Descargando lo que falte dentro de su ventana de publicación...[/]"),
        title="📡 Ingesta al publicarse",
        border_style="green",
    ))

    statuses = run_catchup(dry_run=dry_run)

    t = Table(title="Estado de frescura por fuente", box=box.ROUNDED, border_style="blue")
    t.add_column("Fuente")
    t.add_column("Freq.", justify="center", style="dim")
    t.add_column("Último que tengo", justify="center")
    t.add_column("Esperado", justify="center")
    t.add_column("Estado", justify="center")

    for s in statuses:
        estado = "[yellow]pendiente[/]" if s.due else "[green]al día[/]"
        t.add_row(s.source, s.frequency, s.latest_have or "—", s.expected or "—", estado)

    console.print(t)

    pend = sum(1 for s in statuses if s.due)
    if dry_run:
        console.print(f"\n[dim]{pend} objetivo(s) pendiente(s). Ejecuta sin --dry-run para descargar.[/]")
    else:
        console.print(f"\n[green]✓ Catch-up finalizado.[/] [dim]{pend} objetivo(s) estaban pendientes y se procesaron.[/]")


# ----------------------------------------------------------
# web-preview
# ----------------------------------------------------------

@cli.command(name="web-preview")
@click.option("--output", "-o", default="preview/overview.html", help="Ruta de salida del HTML")
def web_preview(output):
    """Genera un HTML local autocontenido del panel para examinar las vistas sin servidor."""
    from api.preview import render_static
    try:
        path = render_static(output)
    except Exception as e:
        console.print(f"[bold red]❌ No se pudo generar la vista:[/] {e}")
        console.print("[dim]¿Está la BD abierta por otro proceso (escritor)? El preview usa read_only.[/]")
        sys.exit(1)

    console.print(Panel(
        f"[bold green]✓ Vista generada[/]\n\n[cyan]{path}[/]\n\n"
        "[dim]Ábrela con doble clic. Requiere internet (Plotly/HTMX por CDN).[/]",
        title="🖼️  Preview del dashboard",
        border_style="green",
    ))


# ----------------------------------------------------------
# eeff-export
# ----------------------------------------------------------

@cli.command(name="eeff-export")
@click.option("--period", "-p", type=int, default=None, help="Período YYYYMM (default: el más reciente)")
@click.option("--output", "-o", default="preview/eeff", help="Carpeta de salida")
def eeff_export(period, output):
    """Exporta los estados financieros de empresas a HTML navegable (índice + página por empresa)."""
    from api.preview import render_eeff_static
    try:
        res = render_eeff_static(period, output)
    except Exception as e:
        console.print(f"[bold red]❌ No se pudo exportar:[/] {e}")
        console.print("[dim]¿Está la BD abierta por otro proceso (escritor)? El export usa read_only.[/]")
        sys.exit(1)

    if not res.get("companies"):
        console.print("[yellow]No hay estados financieros para exportar (¿período sin datos?).[/]")
        return

    console.print(Panel(
        f"[bold green]✓ EEFF exportados[/]\n\n"
        f"  • Período: [cyan]{res['period']}[/]\n"
        f"  • Empresas: [cyan]{res['companies']}[/]\n"
        f"  • Índice: [cyan]{res['dir']}\\index.html[/]\n\n"
        "[dim]Abre index.html con doble clic; busca y entra a cada empresa.[/]",
        title="🗂️  Export EEFF navegable",
        border_style="green",
    ))


# ----------------------------------------------------------
# serve
# ----------------------------------------------------------

@cli.command(name="serve")
@click.option("--host", default="127.0.0.1", help="Host de escucha")
@click.option("--port", "-p", default=8000, type=int, help="Puerto (default 8000)")
@click.option("--with-scheduler", is_flag=True,
              help="Arranca el scheduler embebido (proceso único; abre la BD en lectura/escritura).")
@click.option("--open/--no-open", "open_browser", default=True,
              help="Abrir el navegador automáticamente (default: sí).")
def serve(host, port, with_scheduler, open_browser):
    """Levanta la API REST + dashboard web (opcionalmente con el scheduler embebido)."""
    import uvicorn

    if with_scheduler:
        settings.RUN_SCHEDULER_IN_APP = True
        console.print("[cyan]Modo proceso único:[/] scheduler embebido + API en R/W.")

    url = f"http://{host}:{port}/"
    console.print(Panel(
        f"[bold green]🌐 Entorno web local iniciado[/]\n\n"
        f"  • Panel:    [cyan]{url}[/]\n"
        f"  • API docs: [cyan]{url}docs[/]\n"
        f"  • Vistas:   panel · EEFF · evolución · comparar · ranking · banca · AFP\n"
        f"  • Scheduler embebido: [yellow]{'sí' if with_scheduler else 'no'}[/]\n\n"
        "[dim]Ctrl+C para detener.[/]",
        title="🚀 INF_FIN_IA Web",
        border_style="green",
    ))

    if open_browser:
        import webbrowser, threading
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    from api.main import app
    uvicorn.run(app, host=host, port=port)


# ----------------------------------------------------------
# run-scheduler
# ----------------------------------------------------------

@cli.command(name="run-scheduler")
def run_scheduler():
    """Inicia el scheduler automático (bloqueante — corre indefinidamente)."""

    console.print(Panel(
        "[bold green]Scheduler iniciado[/]\n\n"
        "Jobs configurados:\n"
        "  • [cyan]Diario[/]      → Lunes–Viernes a las " + settings.DAILY_FETCH_TIME + "\n"
        "  • [cyan]Mensual[/]     → Día 6 de cada mes a las 09:00\n"
        "  • [cyan]Trimestral[/]  → Día 10 de ene/abr/jul/oct a las 09:30\n"
        "  • [cyan]Anual[/]       → 15 de febrero a las 10:00\n\n"
        "[dim]Presiona Ctrl+C para detener.[/]",
        title="⏰ Scheduler Financiero Chile",
        border_style="green",
    ))

    scheduler = create_scheduler(blocking=True)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler detenido.[/]")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    cli()
