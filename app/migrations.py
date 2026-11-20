"""Apply the SQL files in db/migrations/, in name order.

db/init/ only runs when the database volume is brand new, so changes to an existing
database go here instead. Each file is written to be safe to run again (IF NOT EXISTS),
so this simply runs them all every time. A larger project would use a tool like Alembic,
which also records which migrations have already been applied.
"""
from pathlib import Path

from app.db import transaction

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def run_migrations() -> list[str]:
    applied = []
    with transaction() as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied
