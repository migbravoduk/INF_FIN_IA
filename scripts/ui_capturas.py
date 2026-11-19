"""
scripts/ui_capturas.py — Capturas de las vistas (escritorio y celular) + chequeo de diseño.

Uso (con un servidor de prueba ya levantado en :8001, NUNCA el del usuario en :8000):
    .venv/Scripts/python.exe -m uvicorn api.main:app --port 8001      (en segundo plano)
    .venv/Scripts/python.exe scripts/ui_capturas.py --label antes
    .venv/Scripts/python.exe scripts/ui_capturas.py --label fase3 --solo /,/derivados

Por cada URL deja en <out>/<label>/:
    <slug>_escritorio.png, <slug>_movil.png      captura completa (1440 px y 390 px de ancho)
    <slug>_escritorio_0.png, _1.png …            recortes de 1100 px de alto, legibles con Read
y escribe <out>/<label>/chequeo.txt con, por URL y ancho:
    desborde=true|false   la página es más ancha que la ventana (scroll horizontal)
    culpables=…           primeros elementos que se salen por la derecha
    letra_chica=N         elementos con texto de menos de 12 px (fuera de los gráficos)
El chequeo lo calcula base.html cuando la URL lleva ?chequeo_ui=1.

Requiere Google Chrome instalado (headless). No usa playwright.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# (url, alto de la captura de escritorio en px). El alto de celular se calcula ×2,2.
VISTAS = [
    ("/", 2200), ("/productos", 2000), ("/producto/PRIMERO", 2600), ("/curvas", 2600),
    ("/banco-central", 2800), ("/derivados", 7200), ("/eeff", 1100), ("/eeff/corporativos", 3200),
    ("/eeff/seguros", 2600), ("/eeff/intermediarios", 2400), ("/banca", 3000),
    ("/comparar", 1800), ("/ranking", 1500), ("/salud", 1500), ("/proyecciones", 1100),
    ("/proyecciones/corporativos", 3000), ("/proyecciones/bancos", 2400),
    ("/inversion-institucional", 1200), ("/inversion-institucional/derivados", 7000),
    ("/inversion-institucional/emisor?rut=97004000", 2800),
    ("/inversion-institucional/emisores", 2400), ("/seguros/cartera", 3400), ("/afp", 5200),
    ("/fondos", 2600), ("/fondos-mutuos", 4600), ("/metodologia", 3400),
]


def slug(url: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")
    return s or "panel"


def primer_producto(base: str) -> str | None:
    """La ficha de producto necesita un id real: toma el primero de /productos."""
    import urllib.request
    try:
        html = urllib.request.urlopen(base + "/productos/tabla", timeout=60).read().decode("utf-8", "ignore")
    except Exception:
        return None
    m = re.search(r'href="(/producto/[^"]+)"', html)
    return m.group(1) if m else None


def chrome(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                           "--virtual-time-budget=25000", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="ignore",
                          timeout=timeout)


def recortar(png: Path, ancho: int | None = None) -> None:
    from PIL import Image
    im = Image.open(png)
    if ancho and im.size[0] > ancho:                 # vista móvil: quita el margen del marco
        im = im.crop((0, 0, ancho, im.size[1]))
        im.save(png)
    w, h = im.size
    gris = im.convert("L")
    y = h
    while y > 200 and max(gris.crop((0, y - 20, w, y)).getdata()) < 40:   # quita el fondo vacío final
        y -= 20
    for i, t in enumerate(range(0, y, 1100)):
        im.crop((0, t, w, min(t + 1100, y))).save(png.with_name(f"{png.stem}_{i}.png"))


def movil(base: str, url: str, alto: int) -> str:
    """Chrome sin ventana no baja de 500 px: la vista móvil va en un iframe de 390 px."""
    from urllib.parse import quote
    return f"{base}/static/ui_movil.html?w=390&h={alto}&src={quote(url, safe='')}"


def chequeo(url: str, ancho: int) -> str:
    """`url` ya trae ?chequeo_ui=1 (escritorio) o es el marco móvil."""
    r = chrome([f"--window-size={ancho},1200", "--dump-dom", url])
    body = re.search(r"<body[^>]*>", r.stdout or "")
    if not body:
        return "sin respuesta"
    tag = body.group(0)
    def attr(n):
        m = re.search(n + r'="([^"]*)"', tag)
        return m.group(1) if m else "?"
    return (f"ancho={attr('data-ui-ancho')} desborde={attr('data-ui-desborde')} "
            f"letra_chica={attr('data-ui-letra-chica')} culpables={attr('data-ui-culpables') or '-'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="nombre de la carpeta (antes, fase3, final…)")
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "inf_capturas"))
    ap.add_argument("--solo", default="", help="lista de URLs separadas por coma (por defecto, todas)")
    ap.add_argument("--sin-movil", action="store_true")
    a = ap.parse_args()

    if not Path(CHROME).exists():
        print("No encuentro Chrome en", CHROME)
        return 2
    out = Path(a.out) / a.label
    out.mkdir(parents=True, exist_ok=True)
    vistas = VISTAS
    if a.solo:
        pedidas = [u.strip() for u in a.solo.split(",") if u.strip()]
        # Git Bash convierte "/afp" en "C:/Program Files/Git/afp": se deshace.
        pedidas = [re.sub(r"^[A-Za-z]:/.*?/Git(?=/)", "", u) for u in pedidas]
        vistas = [(u, dict(VISTAS).get(u, 2400)) for u in pedidas]
    lineas = []
    for url, alto in vistas:
        if "PRIMERO" in url:
            url = primer_producto(a.base) or "/productos"
        full = a.base + url
        sep = "&" if "?" in url else "?"
        for nombre, ancho, h in (("escritorio", 1440, alto), ("movil", 390, int(alto * 2.2))):
            if nombre == "movil" and a.sin_movil:
                continue
            png = out / f"{slug(url)}_{nombre}.png"
            if nombre == "movil":
                destino = movil(a.base, url, h)
                chrome([f"--window-size=500,{h}", f"--screenshot={png}", destino])
                if png.exists():
                    recortar(png, 390)
                res = chequeo(movil(a.base, url, 1600), 500)
            else:
                chrome([f"--window-size={ancho},{h}", f"--screenshot={png}", full])
                if png.exists():
                    recortar(png)
                res = chequeo(full + sep + "chequeo_ui=1", ancho)
            lineas.append(f"{url:48s} {nombre:10s} {res}")
            print(lineas[-1], flush=True)
    (out / "chequeo.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\nCapturas en", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
