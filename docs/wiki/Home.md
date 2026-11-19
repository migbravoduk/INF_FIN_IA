# 🇨🇱 INF_FIN_IA — Wiki del Proyecto

Bienvenido a la documentación técnica del **Repositorio de Información Financiera de Chile**.

Este sistema acumula datos financieros y macroeconómicos chilenos de forma continua, aplica procesamiento analítico y los entrega como insumo para análisis, dashboards y reportes narrativos automatizados.

---

## 📚 Índice de páginas

| Página | Descripción |
|--------|-------------|
| **[Integración-SP](Integracion-SP)** | Documentación técnica completa de la integración con la Superintendencia de Pensiones |
| **[Backtest-Modelos](Backtest-Modelos)** | Metodología, resultados y decisiones del backtest de las proyecciones de EEFF |
| **[Hoja-de-Ruta](Hoja-de-Ruta)** | Roadmap de fases planificadas y estado actual |
| **[Issues-Resueltos](Issues-Resueltos)** | Registro de problemas técnicos encontrados y resueltos |

> La guía de usuario completa (instalación, CLI, capa web, esquema DuckDB) está en el
> **[README](../../README.md)**; la guía de desarrollo (arquitectura, gotchas) en **[CLAUDE.md](../../CLAUDE.md)**.

---

## 🚀 Estado actual

| Fuente | Estado | Datos disponibles desde |
|--------|--------|------------------------|
| Banco Central de Chile (BCCh) | ✅ Activo | 1975 |
| CMF — Empresas y Mercados | ✅ Activo | 2015 |
| CMF — Bancos e Instituciones Financieras | ✅ Activo | 2019 |
| Superintendencia de Pensiones (SP) | ✅ Activo | 2002 (cuotas) · 2015 (cartera) |
| Expectativas macro (EEE + EOF, BCCh) | ✅ Activo | 2001 |
| Bolsa de Santiago | ⏳ Planificado | — |

**Capa analítica**: proyecciones de EEFF en producción (`/proyecciones`) — ver
[Backtest-Modelos](Backtest-Modelos).

---

## ⚡ Inicio rápido

> Usar siempre el intérprete del entorno virtual del proyecto (`.\.venv\Scripts\python.exe`),
> no una instalación global. En Windows, un clic en `iniciar_web.bat` prepara todo y levanta la web.

```powershell
# Instalar dependencias
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Configurar credenciales
copy .env.example .env

# Verificar conexiones
.\.venv\Scripts\python.exe main.py status

# Descargar histórico cuotas AFP (desde 2002)
.\.venv\Scripts\python.exe main.py fetch-sp-cuotas --year-start 2002

# Iniciar scheduler automático
.\.venv\Scripts\python.exe main.py run-scheduler
```
