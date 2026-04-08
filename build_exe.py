import os
import shutil
import subprocess
import sys

def build():
    print("🚀 Preparando construcción del ejecutable de Finanzas Chile...")
    
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
    
    cmd = [
        "pyinstaller",
        "--name", "Finanzas_Chile_CLI",
        "--onefile",
        "--console",
        "main.py"
    ]
    
    print("📦 Corriendo PyInstaller...")
    subprocess.check_call(cmd)

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
