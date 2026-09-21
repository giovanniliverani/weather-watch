"""SQLite access: one connection helper, the idempotent schema bootstrap, and forward migrations.

`connect()` always sets WAL journaling and foreign-key enforcement. `init_db()` applies
`sql/schema.sql` only when `schema_version` is empty, then applies any `sql/migrations/NNNN_*.sql`
newer than the recorded version, then upserts the seed rows of `source` from `eww.config.SOURCES`.
Safe to run on every start.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from eww import config
from eww.clock import now_iso

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3  # 1: the §3 DDL; 2: the heartbeat view (0002); 3: enrichment_run and document_retrieval (0003, M3)
MIGRATIONS_DIR = config.PROJECT_ROOT / "sql" / "migrations"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite file with WAL mode and foreign keys on."""
    db_path = Path(path) if path is not None else config.DB_PATH
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def schema_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if row is None:
        return False
    return conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] > 0


def schema_version(conn: sqlite3.Connection) -> int | None:
    if not schema_applied(conn):
        return None
    return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]


def apply_schema(conn: sqlite3.Connection, schema_path: Path | None = None) -> None:
    """Fresh install: the full DDL (which already includes every migration's objects)."""
    sql = (schema_path or config.SCHEMA_PATH).read_text(encoding="utf-8")
    conn.executescript(sql)
    with conn:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_iso()),
        )


def migration_files(after: int, upto: int = SCHEMA_VERSION) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        if after < version <= upto:
            found.append((version, path))
    return found


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply every migration newer than the recorded version, in order. Returns the versions applied."""
    current = schema_version(conn) or 0
    applied: list[int] = []
    for version, path in migration_files(current):
        conn.executescript(path.read_text(encoding="utf-8"))
        with conn:
            conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (version, now_iso()))
        applied.append(version)
        log.info("schema migrated version=%s file=%s", version, path.name)
    return applied


def seed_sources(conn: sqlite3.Connection) -> None:
    """Upsert the authoritative sources; re-running refreshes names, terms URLs and attribution."""
    with conn:
        for source_id, meta in config.SOURCES.items():
            conn.execute(
                """
                INSERT INTO source (source_id, kind, display_name, terms_url, attribution)
                VALUES (:source_id, :kind, :display_name, :terms_url, :attribution)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    display_name = excluded.display_name,
                    terms_url = excluded.terms_url,
                    attribution = excluded.attribution
                """,
                {"source_id": source_id, **meta},
            )


def init_db(conn: sqlite3.Connection, schema_path: Path | None = None) -> bool:
    """Apply the schema when `schema_version` is empty, migrate forward, seed `source`.

    Returns True when the schema or a migration was applied by this call.
    """
    changed = False
    if not schema_applied(conn):
        apply_schema(conn, schema_path)
        changed = True
        log.info("schema applied version=%s", SCHEMA_VERSION)
    if apply_migrations(conn):
        changed = True
    seed_sources(conn)
    return changed
