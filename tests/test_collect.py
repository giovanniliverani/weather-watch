"""`eww collect --out`: snapshots and run log on disk, failures logged, exit code only when all fail."""

import json

import httpx
from typer.testing import CliRunner

from eww import db
from eww.cli import app
from eww.collect import collect_source, summary_line
from eww.collectors import eonet, gdacs
from eww.collectors.base import FetchResult
from eww.clock import now_utc
from tests.conftest import eonet_events, gdacs_features


def fake_gdacs(since, until, **kwargs):
    return FetchResult(items=gdacs_features(), requests=[{"url": "x", "params": {}, "status": 200, "items": 3}], http_status=200)


def fake_eonet_failure(since, until, **kwargs):
    raise httpx.ConnectError("boom")


def read_runs(out_dir):
    files = sorted((out_dir / "runs").glob("*.jsonl"))
    lines = []
    for path in files:
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines


def test_collect_out_writes_files_and_touches_no_database(tmp_path, monkeypatch):
    monkeypatch.setattr(gdacs, "fetch", fake_gdacs)
    monkeypatch.setattr(eonet, "fetch", fake_eonet_failure)
    out = tmp_path / "data-branch"
    now = now_utc()
    ok = collect_source(None, "gdacs", now, now, data_dir=out)
    failed = collect_source(None, "eonet", now, now, data_dir=out)
    assert not ok.failed and ok.ingest is None
    assert failed.failed and failed.snapshot is None
    snapshot_files = list((out / "snapshots" / "gdacs").glob("*.json"))
    assert len(snapshot_files) == 1 and not (out / "snapshots" / "eonet").exists()
    envelope = json.loads(snapshot_files[0].read_text(encoding="utf-8"))
    assert envelope["format"] == "eww.snapshot/1" and envelope["items"] == gdacs_features()
    assert envelope["scheduled_for"].endswith(":00:00Z")
    runs = read_runs(out)
    assert [r["source_id"] for r in runs] == ["gdacs", "eonet"]
    assert runs[0]["status"] == "ok" and runs[0]["snapshot_path"] == f"snapshots/gdacs/{snapshot_files[0].name}"
    assert runs[1]["status"] == "failed" and runs[1]["snapshot_path"] is None and "ConnectError" in runs[1]["error"]
    assert set(runs[0]) == {"run_id", "source_id", "scheduled_for", "started_at", "finished_at", "status", "http_status", "items_seen", "snapshot_path", "error"}
    assert not list(out.glob("*.sqlite"))
    assert summary_line([ok, failed]) == "collect summary: gdacs=3 eonet=failed"


def test_cli_exit_code_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(gdacs, "fetch", fake_gdacs)
    monkeypatch.setattr(eonet, "fetch", fake_eonet_failure)
    runner = CliRunner()
    out = tmp_path / "out"
    result = runner.invoke(app, ["--db", str(tmp_path / "unused.sqlite"), "collect", "--all-spine", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[-1] == "collect summary: gdacs=3 eonet=failed"
    assert not (tmp_path / "unused.sqlite").exists()  # --out means no database

    monkeypatch.setattr(gdacs, "fetch", fake_eonet_failure)
    result = runner.invoke(app, ["--db", str(tmp_path / "unused.sqlite"), "collect", "--all-spine", "--out", str(out)])
    assert result.exit_code == 1
    assert "collect summary: gdacs=failed eonet=failed" in result.stdout
    assert len(read_runs(out)) == 4  # every attempt is logged, failed ones included


def test_local_collect_ingests_and_records_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(gdacs, "fetch", fake_gdacs)
    monkeypatch.setattr(eonet, "fetch", lambda since, until, **kw: FetchResult(items=eonet_events(), http_status=200))
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path / "data")
    runner = CliRunner()
    db_path = tmp_path / "local.sqlite"
    result = runner.invoke(app, ["--db", str(db_path), "collect"])
    assert result.exit_code == 0, result.output
    assert "new source_record rows: 16" in result.stdout
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM collector_run").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM snapshot_ingest").fetchone()[0] == 2
    assert (tmp_path / "data" / "runs").exists()
    conn.close()
