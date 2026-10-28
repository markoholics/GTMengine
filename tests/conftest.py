import os, uuid
from pathlib import Path
import psycopg, pytest

ADMIN = os.environ.get("TEST_PG_ADMIN", "postgresql://postgres@/postgres?host=/var/tmp/pgtest&port=5544")
MIG = Path(__file__).parent.parent / "supabase" / "migrations"

def _swap_db(dsn, name):
    base, _, _ = dsn.partition("?")
    head = base.rsplit("/", 1)[0]
    q = dsn.partition("?")[2]
    return f"{head}/{name}" + (f"?{q}" if q else "")

@pytest.fixture
def newdb():
    """Factory for empty throwaway databases. Dropped at teardown."""
    made = []
    def make():
        name = "t_" + uuid.uuid4().hex[:10]
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f"create database {name}")
        made.append(name)
        return _swap_db(ADMIN, name)
    yield make
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for n in made:
            c.execute(f"drop database if exists {n} with (force)")

@pytest.fixture
def migrated(newdb):
    from scripts.migrate import run
    dsn = newdb()
    run(dsn)
    return dsn

@pytest.fixture
def dbready(migrated, monkeypatch):
    """Migrated database wired into core.db."""
    import core.db
    monkeypatch.setattr(core.db, "DSN", migrated)
    return migrated

def apply_files(dsn, names):
    with psycopg.connect(dsn, autocommit=True) as c:
        for n in names:
            c.execute((MIG / n).read_text())
