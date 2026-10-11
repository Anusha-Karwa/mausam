"""Neon PostgreSQL access with psycopg 3.

We use the *pooled* Neon endpoint (PgBouncer in transaction mode), so:
- prepared statements are switched off (prepare_threshold=None),
- every statement runs in autocommit unless we open an explicit transaction.
One connection is reused across warm serverless invocations and re-opened if
Neon has closed it while idle.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from .config import config

_conn = None
_lock = threading.RLock()


class DatabaseNotConfigured(RuntimeError):
    pass


def _connect():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.numeric import FloatLoader

    if not config.DATABASE_URL:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    conn = psycopg.connect(
        config.DATABASE_URL,
        row_factory=dict_row,
        autocommit=True,
        prepare_threshold=None,
        connect_timeout=10,
    )
    # NUMERIC -> float everywhere: all our maths is float-based and JSON friendly.
    conn.adapters.register_loader("numeric", FloatLoader)
    if config.DB_SCHEMA:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{config.DB_SCHEMA}"')
        conn.execute(f'SET search_path TO "{config.DB_SCHEMA}", public')
    return conn


def get_conn():
    global _conn
    with _lock:
        if _conn is None or _conn.closed or getattr(_conn, "broken", False):
            _conn = _connect()
        return _conn


def reset() -> None:
    global _conn
    with _lock:
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn = None


def _adapt(params):
    """Wrap dicts/lists destined for JSONB columns."""
    from psycopg.types.json import Jsonb

    if params is None:
        return None

    def conv(v):
        if isinstance(v, dict):
            # default=str: dates and Decimals inside JSON become text instead of crashing
            return Jsonb(v, dumps=lambda obj: json.dumps(obj, default=str))
        return v

    if isinstance(params, dict):
        return {k: conv(v) for k, v in params.items()}
    return [conv(v) for v in params]


def _run(fn):
    """Run fn(conn); if Neon dropped the idle connection, reconnect once and retry."""
    import psycopg

    with _lock:
        try:
            return fn(get_conn())
        except (psycopg.OperationalError, psycopg.InterfaceError):
            reset()
            return fn(get_conn())


def query(sql: str, params: Sequence | dict | None = None) -> list[dict]:
    def fn(conn):
        with conn.cursor() as cur:
            cur.execute(sql, _adapt(params))
            return cur.fetchall() if cur.description else []

    return _run(fn)


def one(sql: str, params: Sequence | dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence | dict | None = None) -> Any:
    row = one(sql, params)
    return next(iter(row.values())) if row else None


def execute(sql: str, params: Sequence | dict | None = None) -> int:
    def fn(conn):
        with conn.cursor() as cur:
            cur.execute(sql, _adapt(params))
            return cur.rowcount

    return _run(fn)


def executemany(sql: str, rows: Iterable[Sequence]) -> int:
    rows = [_adapt(r) for r in rows]
    if not rows:
        return 0

    def fn(conn):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
                return cur.rowcount

    return _run(fn)


def run_script(sql_text: str) -> None:
    def fn(conn):
        conn.execute(sql_text)

    _run(fn)


@contextmanager
def transaction():
    with _lock:
        conn = get_conn()
        with conn.transaction():
            yield conn


def to_json(value: Any) -> str:
    return json.dumps(value, default=str)
