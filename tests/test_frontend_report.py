"""`eww report frontend` against a temporary SQLite file, a fake web/dist and a fake Open-Meteo."""

from __future__ import annotations

import gzip
import json
import stat
import subprocess
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from eww import api, config, db, frontend_report, weather
from eww.cli import app as cli_app
from eww.clock import parse_iso
from eww.serve import create_app
from tests.test_api import prepared

NOW = "2026-09-16T15:10:00Z"  # the fixtures' collection time, so relative windows select rows
FIVE_DAYS = {"attribution": config.OPEN_METEO_ATTRIBUTION, "current": {"temperature_c": 20.0}, "daily": [{}] * config.OPEN_METEO_FORECAST_DAYS}


@pytest.fixture
def ready(conn, data_dir, tmp_path, monkeypatch):
    prepared(conn, data_dir)
    monkeypatch.setattr(api, "now_utc", lambda: parse_iso(NOW))
    monkeypatch.setattr(weather, "forecast", lambda latitude, longitude: FIVE_DAYS)
    return tmp_path / "test.sqlite"


@pytest.fixture
def client(ready) -> TestClient:
    return TestClient(create_app(ready), base_url="http://127.0.0.1:8000")


def fake_dist(web_dir, index_scripts: bool = True):
    assets = web_dir / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index-a1.js").write_text('import "./vendor-b2.js"; const map = () => import("./map-c3.js");' + "x" * 500)
    (assets / "vendor-b2.js").write_text("vendor" * 400)
    (assets / "map-c3.js").write_text("maplibre" * 4000)
    (assets / "unused-d4.js").write_text("never loaded")
    scripts = '<script type="module" src="/assets/index-a1.js"></script><link rel="modulepreload" href="/assets/vendor-b2.js">' if index_scripts else ""
    (web_dir / "dist" / "index.html").write_text(f"<html><head>{scripts}</head></html>")
    return assets


# --------------------------------------------------------------------------- build and bundle size
def test_first_load_follows_index_html_and_imports(tmp_path):
    assets = fake_dist(tmp_path)
    bundle = frontend_report.first_load_js(tmp_path / "dist")
    assert {c["name"] for c in bundle["chunks"]} == {"index-a1.js", "vendor-b2.js", "map-c3.js"}
    expected = sum(len(gzip.compress((assets / name).read_bytes(), compresslevel=9)) for name in ("index-a1.js", "vendor-b2.js", "map-c3.js"))
    assert bundle["gzip_bytes"] == expected
    assert "lazy ones included" in bundle["method"]


def test_without_entry_scripts_every_js_file_is_the_upper_bound(tmp_path):
    fake_dist(tmp_path, index_scripts=False)
    bundle = frontend_report.first_load_js(tmp_path / "dist")
    assert len(bundle["chunks"]) == 4 and "upper bound" in bundle["method"]


def test_build_check_without_dist_says_not_built(tmp_path):
    build = frontend_report.build_check(tmp_path)
    assert build["built"] is False and build["passed"] is False
    assert frontend_report._build_row(build)[1:] == ("not built (no web/dist/index.html)", frontend_report._build_row(build)[2], "FAIL")


def test_bundle_over_budget_fails(tmp_path, monkeypatch):
    fake_dist(tmp_path)
    assert frontend_report.build_check(tmp_path)["passed"] is True
    monkeypatch.setattr(config, "FRONTEND_FIRST_LOAD_JS_KB", 0)
    assert frontend_report.build_check(tmp_path)["passed"] is False


def test_run_build_counts_typescript_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_report.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(frontend_report.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout="src/App.tsx(3,1): error TS2304: Cannot find name 'x'.", stderr=""))
    result = frontend_report.run_build(tmp_path)
    assert result["ok"] is False and result["ts_errors"] == 1 and "exit 2" in result["error"]
    monkeypatch.setattr(frontend_report.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="built in 3s", stderr=""))
    assert frontend_report.run_build(tmp_path)["ok"] is True


def test_missing_npm_is_a_failed_build(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_report.shutil, "which", lambda name: None)
    assert frontend_report.run_build(tmp_path)["error"] == "npm is not on PATH"


# --------------------------------------------------------------------------- parity and the sample pin
@pytest.mark.parametrize(("map_count", "verdict"), [(None, "CHECK BY HAND"), ("same", "PASS"), (-1, "FAIL")])
def test_parity_agrees_across_api_export_and_http(conn, ready, client, map_count, verdict):
    rows = []
    for name, view in frontend_report.views():
        expected = sum(1 for f in api.events_geojson(view["since"], hazard=view.get("hazard"), min_severity=view.get("min_severity", 0.0), limit=0, conn=conn)["features"])
        count = expected if map_count == "same" else map_count
        rows.append(frontend_report.parity(conn, client, name, view, count))
        assert rows[-1]["events_geojson"] == rows[-1]["export"] == rows[-1]["http"] == expected
    assert rows[0]["events_geojson"] > 0 and rows[1]["events_geojson"] > 0
    assert {row["verdict"] for row in rows} == {verdict}


def test_sample_pin_counts_news_and_posts_and_times_the_forecast(client):
    pin = frontend_report.sample_pin(client)
    assert pin["event"]["event_id"] and pin["news"] + pin["posts"] >= 0
    if pin["news"] + pin["posts"] == 0:
        assert "no pin in the default view has documents" in frontend_report._pin_rows(pin)[0][1]
    assert pin["forecast"]["ok"] is True and pin["forecast"]["days"] == config.OPEN_METEO_FORECAST_DAYS


def test_offline_forecast_is_skipped_not_failed(client, monkeypatch):
    def offline(latitude: float, longitude: float) -> dict:
        raise RuntimeError("no network")

    monkeypatch.setattr(weather, "forecast", offline)
    pin = frontend_report.sample_pin(client)
    assert pin["forecast"]["skipped"] is True and "502" in pin["forecast"]["error"]
    assert frontend_report._pin_rows(pin)[1][3] == "SKIPPED"
    assert frontend_report.sample_pin(client, network=False)["forecast"]["error"] == "skipped (--no-network)"


# --------------------------------------------------------------------------- audit and Python scope
def test_audit_verdicts(tmp_path):
    assert frontend_report.read_audit(tmp_path) is None
    audit = {"generated_at": NOW, "serious": 0, "moderate": 2, "minor": 5, "checked_at_375px": True, "notes": ""}
    (tmp_path / "audit.json").write_text(json.dumps(audit))
    assert frontend_report.read_audit(tmp_path)["passed"] is True
    (tmp_path / "audit.json").write_text(json.dumps({**audit, "serious": 1}))
    assert frontend_report.read_audit(tmp_path)["passed"] is False
    (tmp_path / "audit.json").write_text(json.dumps({**audit, "checked_at_375px": False}))
    assert frontend_report.read_audit(tmp_path)["passed"] is False
    (tmp_path / "audit.json").write_text("{not json")
    assert frontend_report._audit_row(frontend_report.read_audit(tmp_path))[3] == "FAIL"


def test_outside_scope_flags_everything_but_the_m7_files():
    files = ["app.py", "eww/api.py", "eww/serve.py", "eww/resolve.py", "tests/test_serve.py"]
    assert frontend_report.outside_scope(files) == ["app.py", "eww/resolve.py"]


def test_changed_python_reads_git(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "x.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    (tmp_path / "eww").mkdir()
    (tmp_path / "eww" / "serve.py").write_text("s = 1\n")
    (tmp_path / "app.py").write_text("a = 1\n")
    (tmp_path / "notes.md").write_text("not python\n")
    git("add", "-A")
    git("commit", "-qm", "change")
    (tmp_path / "eww" / "new_module.py").write_text("n = 1\n")  # untracked, never added
    assert frontend_report.changed_python("HEAD~1", tmp_path) == ["app.py", "eww/new_module.py", "eww/serve.py"]
    assert "could not compare" in frontend_report.changed_python("no-such-ref", tmp_path)


# --------------------------------------------------------------------------- the whole report
def test_report_writes_every_criterion(conn, ready, tmp_path):
    web = tmp_path / "web"
    fake_dist(web)
    out = tmp_path / "m7.md"
    path, result = frontend_report.write_frontend_report(conn, ready, out, web_dir=web, base="HEAD")
    text = path.read_text(encoding="utf-8")
    assert path == out
    for heading in ("# M7", "## Exit criteria", "## First-load JavaScript", "## Checks by hand", "Pin parity: Default view", "Pin parity: flood", "/forecast at the pin", "impeccable audit"):
        assert heading in text
    assert len(frontend_report.criteria(result)) == len(frontend_report.summary_lines(result)) == 10


def test_default_output_is_the_milestone_doc():
    assert config.milestone_doc(7).name == "m7.md"


def test_cli_report_frontend(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WEB_DIR", tmp_path / "web")
    out = tmp_path / "m7.md"
    result = CliRunner().invoke(cli_app, ["--db", str(ready), "report", "frontend", "--out", str(out), "--no-network", "--base", "HEAD", "--map-count-default", "3"])
    assert result.exit_code == 0, result.output
    assert f"written {out}" in result.output and "not built" in result.output
    assert "map 3" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- failure modes found in review
def test_report_runs_against_a_read_only_database_file(ready, tmp_path, monkeypatch):
    with closing(db.connect(ready)) as writer:
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ready.chmod(stat.S_IREAD)
    try:
        monkeypatch.setattr(config, "WEB_DIR", tmp_path / "web")
        out = tmp_path / "m7.md"
        result = CliRunner().invoke(cli_app, ["--db", str(ready), "report", "frontend", "--out", str(out), "--no-network", "--base", "HEAD"])
        assert result.exit_code == 0, result.output
        assert "Pin parity" in out.read_text(encoding="utf-8")
    finally:
        ready.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_export_count_matches_the_cli(conn, ready):
    view = {"since": "36500d", "hazard": ["flood"], "min_severity": 0.0}
    printed = CliRunner().invoke(cli_app, ["--db", str(ready), "export", "--count", "--since", "36500d", "--hazard", "flood"])
    assert int(printed.stdout.strip().splitlines()[-1]) == frontend_report.export_count(conn, view) > 0


class StubClient:
    """Answers /events.geojson and /documents from the real client and /forecast with a fixed status."""

    def __init__(self, client: TestClient, status: int, body: str) -> None:
        self.client, self.status, self.body = client, status, body

    def get(self, url: str, params: dict | None = None):
        if url == "/forecast":
            return httpx.Response(self.status, text=self.body)
        return self.client.get(url, params=params)


@pytest.mark.parametrize(("status", "body", "skipped"), [(502, '{"detail": "Open-Meteo unavailable"}', True), (400, '{"detail": "lat out of range"}', False), (500, "Internal Server Error", False)])
def test_only_a_502_forecast_is_skipped(client, status, body, skipped):
    pin = frontend_report.sample_pin(StubClient(client, status, body))
    assert pin["forecast"]["skipped"] is skipped and pin["forecast"]["ok"] is False
    assert frontend_report._pin_rows(pin)[1][3] == ("SKIPPED" if skipped else "FAIL")


def test_build_without_a_web_folder_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_report.shutil, "which", lambda name: "npm")
    assert "folder" in frontend_report.run_build(tmp_path / "web")["error"]


def test_hung_build_times_out(tmp_path, monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("npm", kwargs["timeout"])

    monkeypatch.setattr(frontend_report.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(frontend_report.subprocess, "run", hang)
    assert "took over" in frontend_report.run_build(tmp_path)["error"]


def test_vite_relative_base_and_css(tmp_path):
    assets = tmp_path / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "index-cMXJ.js").write_text('const MapView = () => import("./MapView-BXSh.js");')
    (assets / "MapView-BXSh.js").write_text("maplibre" * 1000)
    (assets / "index-Dq2.css").write_text("body{}" * 100)
    html = '<script type="module" crossorigin src="./assets/index-cMXJ.js"></script><link rel="stylesheet" href="./assets/index-Dq2.css">'
    (tmp_path / "dist" / "index.html").write_text(html)
    bundle = frontend_report.first_load_js(tmp_path / "dist")
    assert {c["name"] for c in bundle["chunks"]} == {"index-cMXJ.js", "MapView-BXSh.js"}
    assert "lazy ones included" in bundle["method"]


def test_odd_audit_files_fail_without_a_crash(tmp_path):
    (tmp_path / "audit.json").write_text("[1, 2]")
    assert frontend_report._audit_row(frontend_report.read_audit(tmp_path))[3] == "FAIL"
    (tmp_path / "audit.json").write_bytes(b"\xff\xfe\x00bad")
    assert frontend_report._audit_row(frontend_report.read_audit(tmp_path))[3] == "FAIL"


def test_titles_with_a_pipe_keep_the_table_intact():
    pin = {"event": {"event_id": "E1", "title": "Flood | Storm", "lat": 1.0, "lon": 2.0}, "news": 1, "posts": 0, "forecast": {"skipped": True, "error": "x"}}
    assert "Flood \\| Storm" in frontend_report._pin_rows(pin)[0][0]


def test_an_empty_view_is_labelled(conn, client):
    row = frontend_report.parity(conn, client, "flood", {"since": "7d", "hazard": ["tsunami"], "min_severity": 0.99}, 0)
    assert row["events_geojson"] == 0 and "view is empty" in frontend_report._parity_row(row)[1]
