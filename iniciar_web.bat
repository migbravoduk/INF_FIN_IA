@echo off
chcp 65001 >nul
cd /d "%~dp0"
title INF_FIN_IA - Entorno web local

echo ============================================================
echo   INF_FIN_IA - Entorno web local
echo ============================================================
echo.

rem Primera vez: crear el entorno virtual e instalar dependencias.
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creando entorno virtual e instalando dependencias...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    echo.
)

echo Iniciando servidor... se abrira el navegador en http://127.0.0.1:8000/
echo Cierra esta ventana o presiona Ctrl+C para detener.
echo.

".venv\Scripts\python.exe" main.py serve

echo.
echo Servidor detenido.
pause
