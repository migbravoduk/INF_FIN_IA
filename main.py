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

import sys
import io

# Forzar UTF-8 en la consola de Windows (evita UnicodeEncodeError con emojis)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
from processors.normalizer import normalize_observations
from scheduler.jobs import run_all_series, run_fetch_by_frequency, create_scheduler

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

    # Credenciales
    console.print()
    bcentral_ok = "✅" if settings.has_bcentral_credentials else "❌"
    cmf_ok = "✅" if settings.has_cmf_credentials else "⚠️  (Fase 2)"
    console.print(f"  {bcentral_ok} Banco Central API")
    console.print(f"  {cmf_ok} CMF API")


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
    # Si el usuario ejecuta sin comandos (ej. doble clic en ejecutable)
    if len(sys.argv) == 1:
        try:
            from interactive import run_menu
            run_menu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Saliendo...[/]")
            sys.exit(0)
    else:
        cli()

