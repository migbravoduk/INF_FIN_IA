"""
build_actualizador.py — Genera `Actualizar_Datos.exe` (actualizador de fuentes).

El .exe queda en la RAÍZ del proyecto, no en `dist/`, a propósito: `config/settings.py`
resuelve `DB_PATH`, `CATALOG_PATH` y el `.env` con rutas relativas al directorio de trabajo,
así que el ejecutable tiene que vivir junto a `data/`, `config/` y `.env` para actualizar
esta base y no crear una vacía al lado. `actualizar.py` además hace `chdir` a la carpeta del
.exe cuando corre congelado, para que también funcione lanzado desde otra ruta.

No se empaquetan `.env` ni `config/series_catalog.yaml`: se leen del disco para poder
editarlos (credenciales, series nuevas) sin recompilar.

Uso:
    .\\.venv\\Scripts\\python.exe build_actualizador.py
"""

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Forzar UTF-8 en la consola de Windows (evita UnicodeEncodeError con emojis)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
NOMBRE = "Actualizar_Datos"


def _preparar_path_dlls():
    """Agrega las DLLs de la instalación base (Anaconda o estándar) al PATH."""
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    candidatas = [
        os.path.join(base_prefix, "Library", "bin"),
        os.path.join(base_prefix, "DLLs"),
        base_prefix,
    ]
    path_env = os.environ.get("PATH", "")
    for p in candidatas:
        if os.path.exists(p) and p not in path_env:
            path_env = p + os.pathsep + path_env
    os.environ["PATH"] = path_env


def build():
    print(f"🚀 Construyendo {NOMBRE}.exe (actualizador de fuentes)...")
    _preparar_path_dlls()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    work = ROOT / "build" / NOMBRE.lower()
    if work.exists():
        shutil.rmtree(work)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", NOMBRE,
        "--onefile",
        "--console",
        "--noconfirm",
        # El .exe va a la raíz del proyecto; el .spec y los intermedios, a build/.
        "--distpath", str(ROOT),
        "--workpath", str(work),
        "--specpath", str(ROOT / "build"),
        # statsmodels/matplotlib solo los usa models/ (proyecciones), que el actualizador
        # no toca: excluirlos recorta bastante el tamaño del binario.
        "--exclude-module", "statsmodels",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        str(ROOT / "actualizar.py"),
    ]
    subprocess.check_call(cmd)

    exe = ROOT / f"{NOMBRE}.exe"
    tam = exe.stat().st_size / 1024 / 1024
    print(f"\n✅ Listo: {exe}  ({tam:.0f} MB)")
    print("\nUso:")
    print(f"  • Doble clic en {NOMBRE}.exe            → menú interactivo por fuente")
    print(f"  • {NOMBRE}.exe --status                 → qué fuentes tienen datos pendientes")
    print(f"  • {NOMBRE}.exe --catchup                → lleva las 9 fuentes a su período esperado")
    print(f"  • {NOMBRE}.exe --todo                   → catch-up + huecos históricos de cartera")
    print("\n⚠️  Cerrar la web (iniciar_web.bat) antes de actualizar: DuckDB no admite")
    print("    un proceso escribiendo y otro leyendo el mismo archivo.")


if __name__ == "__main__":
    build()
