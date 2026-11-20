"""The Meme Line — database access (libsql: Turso remote or local file).

One code path for both: if TURSO_DATABASE_URL is set we connect remote
(auth token from env); otherwise a local SQLite file via libsql. Tests
use a local temp file. Nothing here ever touches a wallet or a key.
"""
import os

import libsql

from . import config

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def connect(database_path=None, turso_url=None, turso_token=None):
    """Return a libsql connection. Remote Turso wins when a URL is set."""
    url = turso_url if turso_url is not None else config.TURSO_DATABASE_URL
    token = turso_token if turso_token is not None else config.TURSO_AUTH_TOKEN
    if url:
        return libsql.connect(url, auth_token=token)
    path = database_path or config.DATABASE_PATH
    return libsql.connect(path)


def init_db(conn):
    """Create tables if missing (idempotent)."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def rows_to_dicts(cursor):
    """Convert a libsql cursor's rows to a list of dicts."""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetchone_dict(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def fetchall_dicts(conn, sql, params=()):
    return rows_to_dicts(conn.execute(sql, params))
