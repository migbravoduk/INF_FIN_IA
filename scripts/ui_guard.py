"""
scripts/ui_guard.py — Guarda de contrato de las plantillas durante el rediseño estético.

El rediseño puede cambiar clases, estructura y textos, pero NO puede romper lo que conecta
la página con el servidor y con los gráficos: ids (destino de HTMX y de Plotly), nombres de
campos de formulario (parámetros de las consultas) y atributos hx-*.

    .venv/Scripts/python.exe scripts/ui_guard.py snapshot      # antes de empezar
    .venv/Scripts/python.exe scripts/ui_guard.py check         # después de cada fase

`snapshot` guarda la línea base en %TEMP%/inf_ui_guard.json. `check` falla (código 1) si
falta algún id, name o atributo hx-* que existía, y muestra métricas de deuda de estilo
(estilos en línea, colores fijos, text-align en línea) para seguir su baja.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "api" / "templates"
BASE = Path(os.environ.get("TEMP", ".")) / "inf_ui_guard.json"

PAT = {
    "id": re.compile(r'\bid="([^"]+)"'),
    "name": re.compile(r'\bname="([^"]+)"'),
    "hx": re.compile(r'\b(hx-[a-z-]+)="([^"]*)"'),
    "js_id": re.compile(r'getElementById\(\s*["\']([^"\']+)["\']'),
    "plot": re.compile(r'Plotly\.(?:newPlot|react)\(\s*["\']([^"\']+)["\']'),
}


def extraer() -> dict:
    out = {}
    for f in sorted(TPL.rglob("*.html")):
        s = f.read_text(encoding="utf-8")
        rel = f.relative_to(TPL).as_posix()
        out[rel] = {
            "id": sorted(set(PAT["id"].findall(s))),
            "name": sorted(set(PAT["name"].findall(s))),
            "hx": sorted({f"{k}={v}" for k, v in PAT["hx"].findall(s)}),
            "js_id": sorted(set(PAT["js_id"].findall(s))),
            "plot": sorted(set(PAT["plot"].findall(s))),
        }
    return out


def deuda() -> dict:
    estilos = colores = alinear = 0
    for f in TPL.rglob("*.html"):
        s = f.read_text(encoding="utf-8")
        estilos += len(re.findall(r'\bstyle="', s))
        alinear += len(re.findall(r'style="\s*text-align:\s*(?:right|left)\s*;?\s*"', s))
        colores += len(re.findall(r"#[0-9a-fA-F]{6}\b", s))
    css = (ROOT / "api" / "static" / "app.css").read_text(encoding="utf-8")
    chicas = re.findall(r"font-size:\s*0?\.(6\d|7[0-4])\d*rem", css)
    return {"estilos_en_linea": estilos, "text_align_en_linea": alinear,
            "colores_fijos_en_plantillas": colores, "font_size_css_menor_a_12px": len(chicas)}


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) > 1 else "check"
    actual = extraer()
    if modo == "snapshot":
        BASE.write_text(json.dumps({"plantillas": actual, "deuda": deuda()}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print("Línea base guardada en", BASE)
        print("Deuda de estilo:", deuda())
        return 0
    if not BASE.exists():
        print("Falta la línea base: corre primero `ui_guard.py snapshot`.")
        return 2
    base = json.loads(BASE.read_text(encoding="utf-8"))
    falta = []
    # Un id/name/hx puede mudarse de archivo (p. ej. a un partial nuevo): se exige globalmente.
    global_actual = {k: set() for k in PAT}
    for d in actual.values():
        for k in PAT:
            global_actual[k] |= set(d[k])
    for archivo, d in base["plantillas"].items():
        for k in PAT:
            for v in d[k]:
                if v not in global_actual[k]:
                    falta.append(f"{archivo}: {k} «{v}» ya no existe en ninguna plantilla")
    print("Deuda de estilo  antes:", base["deuda"])
    print("                ahora:", deuda())
    if falta:
        print(f"\n✗ {len(falta)} elementos de contrato perdidos:")
        print("\n".join("  " + x for x in falta))
        return 1
    print("\n✓ Contrato intacto: ids, names, hx-* y destinos de gráficos presentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
