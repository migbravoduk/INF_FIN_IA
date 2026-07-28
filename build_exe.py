import os
import shutil
import subprocess
import sys
import io

# Forzar UTF-8 en la consola de Windows (evita UnicodeEncodeError con emojis)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def build():
    print("🚀 Preparando construcción del ejecutable de Finanzas Chile...")
    
    # Agregar rutas de DLLs de la base (Anaconda o standard) al PATH para PyInstaller
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    dll_paths = [
        os.path.join(base_prefix, "Library", "bin"),
        os.path.join(base_prefix, "DLLs"),
        base_prefix
    ]
    path_env = os.environ.get("PATH", "")
    for p in dll_paths:
        if os.path.exists(p) and p not in path_env:
            path_env = p + os.pathsep + path_env
    os.environ["PATH"] = path_env

    # Asegúrate de instalar pyinstaller primero
    try:
        import PyInstaller
    except ImportError:
        print("Instalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Borramos build previo si existe
    if os.path.exists("dist"):
        shutil.rmtree("dist")
    if os.path.exists("build"):
        shutil.rmtree("build")

    # Comando de build de pyinstaller
    # Compilaremos main.py en un único ejecutable.
    # No empaquetaremos el yaml ni el .env dentro del ejecutable
    # para que sea fácil de modificar por el usuario sin recompilar.
    
    cmd_cli = [
        sys.executable,
        "-m", "PyInstaller",
        "--name", "Finanzas_Chile_CLI",
        "--onefile",
        "--console",
        "--add-data", "api/templates;api/templates",
        "--add-data", "api/static;api/static",
        "main.py"
    ]

    cmd_upd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name", "Actualizar_Fuentes",
        "--onefile",
        "--console",
        "actualizar.py"
    ]
    
    print("📦 Corriendo PyInstaller para CLI principal...")
    subprocess.check_call(cmd_cli)

    print("📦 Corriendo PyInstaller para Actualizador de Fuentes...")
    subprocess.check_call(cmd_upd)

    # Movemos config y .env al dist para que esté listo para usar
    print("📂 Copiando archivos de configuración a la carpeta final...")
    dist_dir = "dist"
    
    # Copiar config/series_catalog.yaml
    os.makedirs(os.path.join(dist_dir, "config"), exist_ok=True)
    shutil.copyfile(
        os.path.join("config", "series_catalog.yaml"), 
        os.path.join(dist_dir, "config", "series_catalog.yaml")
    )
    
    # Copiar .env.example como .env
    shutil.copyfile(".env.example", os.path.join(dist_dir, ".env"))

    print("\n✅ ¡Ejecutable generado con éxito!")
    print(f"👉 Tu versión lista para usar está en la carpeta '{os.path.abspath(dist_dir)}'.")
    print("Solo entra ahí, edita el .env con tus credenciales y haz doble clic en 'Finanzas_Chile_CLI.exe'.")

if __name__ == "__main__":
    build()
