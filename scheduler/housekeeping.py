"""
scheduler/housekeeping.py — Eficiencia de disco: crudos, bitácoras, respaldos y compactación.

Regla del proyecto (nov-2026): **después de cada recarga se borran los archivos crudos que
la alimentaron** (`data/*_raw`). Todo lo que hay ahí ya está en la base y se puede volver a
descargar; guardarlo llegó a ocupar 1,6 GB frente a 1,0 GB de la base.

Se llama automáticamente:
- al final de `run_catchup` (scheduler y `main.py catchup`) si hubo descargas;
- al final de cualquier comando `fetch-*` / `import-*` exitoso de `main.py`;
- al final de cada operación del actualizador (`actualizar.py` / `Actualizar_Datos.exe`).

Si un comando falla, los crudos se conservan para poder reintentar sin volver a bajar.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BACKUPS = DATA / "backups"

DQ_RUNS_KEEP = 30          # corridas de controles de calidad que se conservan
FETCH_LOG_DAYS = 365       # días de bitácora de ingestas
BACKUPS_KEEP = 1           # respaldos completos de la base


def limpiar_crudos(dry_run: bool = False) -> tuple[int, int]:
    """Borra todos los archivos de data/*_raw. Devuelve (archivos, bytes)."""
    n = size = 0
    for d in DATA.glob("*_raw"):
        for f in d.rglob("*"):
            if f.is_file():
                size += f.stat().st_size
                n += 1
                if not dry_run:
                    try:
                        f.unlink()
                    except OSError as e:  # archivo abierto por otro proceso: se reintenta la próxima vez
                        logger.warning(f"No se pudo borrar {f}: {e}")
    if n:
        logger.info(f"Crudos {'por borrar' if dry_run else 'borrados'}: {n} archivos, {size / 1e6:,.1f} MB")
    return n, size


def podar_bitacoras(db) -> dict:
    """Retención de tablas que crecen con cada corrida (dq_results, fetch_log)."""
    out = {}
    try:
        out["dq_results"] = db.conn.execute(f"""
            DELETE FROM dq_results WHERE run_at NOT IN (
                SELECT DISTINCT run_at FROM dq_results ORDER BY run_at DESC LIMIT {DQ_RUNS_KEEP})
        """).fetchone()[0]
        out["fetch_log"] = db.conn.execute(
            f"DELETE FROM fetch_log WHERE started_at < now() - INTERVAL {FETCH_LOG_DAYS} DAY").fetchone()[0]
    except Exception as e:
        logger.info(f"Poda de bitácoras omitida: {e}")
    return out


def respaldar(db_path: str | Path | None = None, keep: int = BACKUPS_KEEP) -> Path:
    """Copia la base a data/backups/ (con la base cerrada) y deja solo los `keep` más recientes."""
    import datetime as dt
    from config.settings import settings
    src = Path(db_path or ROOT / settings.DB_PATH)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"{src.stem}_{dt.datetime.now():%Y%m%d_%H%M}{src.suffix}"
    shutil.copy2(src, dst)
    rotar_respaldos(keep)
    return dst


def rotar_respaldos(keep: int = BACKUPS_KEEP) -> list[Path]:
    olds = sorted(BACKUPS.glob("*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]
    for p in olds:
        p.unlink()
        logger.info(f"Respaldo antiguo borrado: {p.name}")
    return olds


def compactar(db_path: str | Path | None = None) -> tuple[int, int]:
    """
    Reescribe la base en un archivo nuevo y lo reemplaza. DuckDB no devuelve al disco el
    espacio de las filas borradas: cada reingesta por Delete-then-Insert (cartera de seguros,
    bancos, FFMM) deja bloques libres que solo se recuperan copiando la base. Requiere que
    ningún otro proceso la tenga abierta. Devuelve (bytes_antes, bytes_después).
    """
    # EXPORT/IMPORT y no `COPY FROM DATABASE`: este último copia las tablas en un orden que
    # viola las claves foráneas (observations antes que series) y falla.
    import duckdb
    from config.settings import settings
    src = Path(db_path or ROOT / settings.DB_PATH)
    tmp = src.with_name(src.stem + "_compact" + src.suffix)
    dump = src.parent / "_compact_dump"
    for p in (tmp, dump):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    antes = src.stat().st_size

    def conteos(path):
        c = duckdb.connect(str(path), read_only=True)
        try:
            tablas = [r[0] for r in c.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
            return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
        finally:
            c.close()

    try:
        c = duckdb.connect(str(src), read_only=True)
        c.execute(f"EXPORT DATABASE '{dump.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)")
        c.close()
        c = duckdb.connect(str(tmp))
        c.execute(f"IMPORT DATABASE '{dump.as_posix()}'")
        c.execute("CHECKPOINT")
        c.close()
        if conteos(src) != conteos(tmp):
            raise RuntimeError("Los conteos de filas no coinciden tras compactar; se conserva la base original.")
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    finally:
        if dump.exists():
            shutil.rmtree(dump)
    wal = src.with_suffix(src.suffix + ".wal")
    if wal.exists():
        wal.unlink()  # la base se abrió solo en lectura: el WAL ya estaba aplicado
    tmp.replace(src)
    despues = src.stat().st_size
    logger.info(f"Base compactada: {antes / 1e6:,.0f} MB → {despues / 1e6:,.0f} MB")
    return antes, despues


def despues_de_cargar(db=None) -> None:
    """Rutina estándar tras una recarga exitosa: crudos fuera y bitácoras podadas."""
    limpiar_crudos()
    if db is not None:
        podar_bitacoras(db)
