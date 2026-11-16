import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def mem_db():
    """Base DuckDB vacía en memoria con el esquema completo del proyecto."""
    from db.database import Database
    db = Database(":memory:")
    yield db
    db.close()


@pytest.fixture(scope="session")
def real_db_readonly():
    """La base real en solo lectura. Se salta si no existe o la tiene tomada un escritor."""
    from config.settings import settings
    from db.database import Database
    path = ROOT / settings.DB_PATH
    if not path.exists():
        pytest.skip("No existe la base de datos local")
    try:
        db = Database(str(path), read_only=True)
    except Exception as e:  # otro proceso escribiendo (DuckDB: un solo escritor)
        pytest.skip(f"Base tomada por otro proceso: {e}")
    yield db
    db.close()
