import sys
import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt

from db.database import Database
from scheduler.jobs import run_all_series
from config.settings import settings

console = Console()

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def show_status():
    clear_screen()
    console.print(Panel(
        "[bold cyan]Estado Actual de las Series Financieras[/]",
        subtitle="Últimos datos guardados localmente",
        border_style="cyan",
    ))

    with Database() as db:
        series_df = db.get_all_series()

        if series_df.empty:
            console.print("[yellow]La base de datos está vacía. Selecciona 'Actualizar todas las series' en el menú principal.[/]")
        else:
            t = Table(box=box.ROUNDED, header_style="bold blue")
            t.add_column("Categoría")
            t.add_column("Nombre")
            t.add_column("Última Fecha", justify="center")
            t.add_column("Último Valor", justify="right")

            for _, row in series_df.iterrows():
                latest = db.get_latest_value(row["id"])
                
                fecha = str(latest["date"]) if latest else "Sin datos"
                valor = f"{latest['value']:,.2f}" if latest else "—"
                
                t.add_row(
                    str(row.get("category", "")).replace("_", " ").title(),
                    str(row.get("name", "")).split("(")[0].strip(),
                    fecha,
                    f"[bold green]{valor}[/]" if latest else valor,
                )
            console.print(t)
            
    Prompt.ask("\n[dim]Presiona ENTER para volver al menú principal[/]")


def fetch_all():
    clear_screen()
    console.print(Panel("[bold green]🚀 Iniciando actualización de series desde el Banco Central...[/]", border_style="green"))
    try:
        run_all_series()
        console.print("\n[bold green]✅ Actualización completada satisfactoriamente.[/]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Hubo un error durante la actualización:[/]\n{e}")
    Prompt.ask("\n[dim]Presiona ENTER para volver al menú principal[/]")


def run_menu():
    while True:
        clear_screen()
        console.print(Panel.fit(
            "[bold white]Menú Principal - Finanzas Chile[/]\n\n"
            "[1] 📊 Ver estado y últimos datos de cada serie\n"
            "[2] 🔄 Actualizar todas las series (Recomendado probar primero)\n"
            "[3] ❌ Salir del programa",
            title="🇨🇱 Repositorio Financiero",
            border_style="blue",
            padding=(1, 4)
        ))

        # Revisa credenciales básico
        if not settings.has_bcentral_credentials:
            console.print("\n[bold red]⚠️  ¡ATENCIÓN! No tienes configuradas las credenciales del Banco Central en '.env'[/]")

        opcion = Prompt.ask("\nSelecciona una opción", choices=["1", "2", "3"])

        if opcion == "1":
            show_status()
        elif opcion == "2":
            fetch_all()
        elif opcion == "3":
            console.print("\n[bold cyan]¡Hasta luego![/]")
            sys.exit(0)

if __name__ == "__main__":
    try:
        run_menu()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operación cancelada. Saliendo...[/]")
        sys.exit(0)
