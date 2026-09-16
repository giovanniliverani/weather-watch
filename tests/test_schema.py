"""The schema applies on an empty file and init-db is idempotent."""

from eww import db


def test_schema_applies_on_empty_file(tmp_path):
    path = tmp_path / "fresh.sqlite"
    connection = db.connect(path)
    assert db.init_db(connection) is True
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for expected in ("schema_version", "source", "collector_run", "snapshot_ingest", "event", "source_record", "event_geometry", "document", "merge_proposal"):
        assert expected in tables
    assert db.schema_version(connection) == 1
    sources = {row[0]: row for row in connection.execute("SELECT source_id, kind, attribution, terms_url FROM source")}
    assert set(sources) == {"gdacs", "eonet"}
    assert sources["gdacs"]["kind"] == "authority"
    assert "CC BY 4.0" in sources["gdacs"]["attribution"]
    assert sources["eonet"]["terms_url"].startswith("https://")
    connection.close()


def test_init_db_twice_changes_nothing(conn):
    before = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert db.init_db(conn) is False
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == before == 1
    assert conn.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 2


def test_strict_tables_reject_wrong_types(conn):
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES ('two', 'x')")
