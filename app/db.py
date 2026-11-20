"""The only module that opens database connections.

Every query goes through `query()`, so connection handling lives in one place.
Later, swapping in a connection pool means changing this file only.
"""
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import DATABASE_URL


def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only SELECT and return rows as dictionaries.

    read_only=True makes Postgres itself reject INSERT/UPDATE/DELETE in this
    transaction, so a bug in a read tool can never change data.
    """
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        conn.read_only = True
        return conn.execute(sql, params).fetchall()
