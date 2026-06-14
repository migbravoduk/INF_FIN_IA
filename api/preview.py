"""
api/preview.py — Exporta el panel a un HTML estático autocontenido.

Renderiza overview.html con datos reales (read_only) e inyecta el CSS inline, de modo
que el archivo se pueda abrir directamente en el navegador (file://) sin levantar el
servidor. Útil para examinar las vistas rápidamente. Los gráficos (Plotly) y HTMX
cargan por CDN, así que requiere conexión a internet para verse completo.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from db.database import Database
from api.routers.views import build_overview_context

_BASE = Path(__file__).parent
_TEMPLATES = _BASE / "templates"
_STATIC = _BASE / "static"


def render_static(output_path: str = "preview/overview.html") -> str:
    """Genera el HTML estático del panel y devuelve la ruta absoluta del archivo."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    with Database(read_only=True) as db:
        ctx = build_overview_context(db)

    html = env.get_template("overview.html").render(**ctx)

    # Inline del CSS para que el archivo sea autocontenido.
    css = (_STATIC / "app.css").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="/static/app.css">',
        f"<style>\n{css}\n</style>",
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out.resolve())
