# 🇨🇱 INF_FIN_IA — Wiki del Proyecto

Bienvenido a la documentación técnica del **Repositorio de Información Financiera de Chile**.

Este sistema acumula datos financieros y macroeconómicos chilenos de forma continua, aplica procesamiento analítico y los entrega como insumo para análisis, dashboards y reportes narrativos automatizados.

---

## 📚 Índice de páginas

| Página | Descripción |
|--------|-------------|
| **[Arquitectura-General](Arquitectura-General)** | Diseño del sistema, flujo de datos y stack tecnológico |
| **[Fuentes-de-Datos](Fuentes-de-Datos)** | Detalles de cada origen de datos integrado (BCCh, CMF, SP, etc.) |
| **[Integración-SP](Integracion-SP)** | Documentación técnica completa de la integración con la Superintendencia de Pensiones |
| **[CLI-y-Comandos](CLI-y-Comandos)** | Referencia completa de todos los comandos disponibles |
| **[Base-de-Datos](Base-de-Datos)** | Esquema DuckDB, tablas, ejemplos de consultas SQL |
| **[Hoja-de-Ruta](Hoja-de-Ruta)** | Roadmap de fases planificadas y estado actual |
| **[Issues-Resueltos](Issues-Resueltos)** | Registro de problemas técnicos encontrados y resueltos |

---

## 🚀 Estado actual

| Fuente | Estado | Datos disponibles desde |
|--------|--------|------------------------|
| Banco Central de Chile (BCCh) | ✅ Activo | 1991 |
| CMF — Empresas y Mercados | ✅ Activo | 2009 |
| CMF — Bancos e Instituciones Financieras | ✅ Activo | 2020 |
| **Superintendencia de Pensiones (SP)** | ✅ **Activo (nuevo)** | 2002 |
| Bolsa de Santiago | ⏳ Planificado | — |

---

## ⚡ Inicio rápido

```powershell
# Instalar dependencias
C:\Users\mbrav\anaconda3\python.exe -m pip install -r requirements.txt

# Configurar credenciales
copy .env.example .env

# Verificar conexiones
C:\Users\mbrav\anaconda3\python.exe main.py status

# Descargar histórico cuotas AFP (desde 2002)
C:\Users\mbrav\anaconda3\python.exe main.py fetch-sp-cuotas --year-start 2002

# Iniciar scheduler automático
C:\Users\mbrav\anaconda3\python.exe main.py run-scheduler
```
