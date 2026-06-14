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


def _shell(title: str, body: str, css: str, back: bool = True) -> str:
    """Envuelve un fragmento en una página HTML autocontenida (CSS inline, tema oscuro)."""
    nav = '<p style="margin:0 0 1rem"><a href="index.html" style="color:#38bdf8">&larr; Volver al índice</a></p>' if back else ""
    return (
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title><style>{css}</style></head>"
        f"<body><main class='container'>{nav}{body}</main></body></html>"
    )


def render_eeff_static(period: int = None, output_dir: str = "preview/eeff") -> dict:
    """
    Exporta los estados financieros de empresas (CMF) de un período a HTML navegable:
    un index buscable + una página por empresa. Devuelve {period, companies, dir}.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    table_tpl = env.get_template("partials/eeff_table.html")
    css = (_STATIC / "app.css").read_text(encoding="utf-8")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with Database(read_only=True) as db:
        if period is None:
            periods = db.get_cmf_periods()
            period = periods[0] if periods else None
        if period is None:
            return {"period": None, "companies": 0, "dir": str(out.resolve())}

        df = db.query_cmf_statements(period=period, limit=10**9)

    index_rows = []
    for rut, sub in df.groupby("rut", sort=False):
        name = str(sub.iloc[0]["company_name"])
        meta = {
            "company_name": name, "rut": str(rut), "period": int(period),
            "report_type": str(sub.iloc[0]["report_type"]),
            "currency": str(sub.iloc[0]["currency"]),
        }
        groups = [
            {"group": g or "—",
             "rows": [{"account_name": str(r["account_name"]), "value": r["value"]}
                      for _, r in g_sub.iterrows()]}
            for g, g_sub in sub.groupby("statement_group", sort=False)
        ]
        body = table_tpl.render(meta=meta, groups=groups)
        (out / f"{rut}.html").write_text(
            _shell(f"{name} · {period}", body, css), encoding="utf-8"
        )
        index_rows.append((name, str(rut)))

    # Índice buscable (filtro client-side)
    items = "\n".join(
        f'<li data-name="{n.lower()}"><a href="{r}.html">{n}</a> '
        f'<span class="eeff-meta">RUT {r}</span></li>'
        for n, r in sorted(index_rows)
    )
    body = (
        f"<h1>Estados financieros corporativos — período {period}</h1>"
        f'<p class="subtitle">{len(index_rows)} empresas · CMF Empresas y Mercados</p>'
        '<input id="q" class="eeff-search" placeholder="Filtrar empresa…" autocomplete="off">'
        f'<ul class="eeff-index">{items}</ul>'
        "<script>var q=document.getElementById('q'),ls=document.querySelectorAll('.eeff-index li');"
        "q.addEventListener('input',function(){var t=q.value.toLowerCase();"
        "ls.forEach(function(li){li.style.display=li.dataset.name.indexOf(t)>=0?'':'none';});});</script>"
    )
    extra = (".eeff-search{background:#1e293b;color:#e2e8f0;border:1px solid #334155;"
             "border-radius:8px;padding:.5rem .8rem;width:100%;max-width:420px;font-size:.95rem;margin:.5rem 0 1rem}"
             ".eeff-index{list-style:none;padding:0;columns:2;gap:1.5rem}"
             ".eeff-index li{padding:.25rem 0;break-inside:avoid}"
             ".eeff-index a{color:#38bdf8;text-decoration:none}.eeff-index a:hover{text-decoration:underline}")
    (out / "index.html").write_text(
        _shell(f"EEFF {period}", body, css + extra, back=False), encoding="utf-8"
    )

    return {"period": int(period), "companies": len(index_rows), "dir": str(out.resolve())}
