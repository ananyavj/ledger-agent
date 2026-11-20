"""The only module that opens database connections.

Reads go through `query()`, writes through `transaction()`, so connection handling
lives in one place. Swapping in a connection pool later means changing this file only.
"""
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """A connection for writes. Everything inside the `with` block commits together,
    or, if anything raises, rolls back together."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn
