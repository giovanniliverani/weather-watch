"""Replaying the `data` branch through git plumbing: fetch, list, show; idempotent on file paths."""

import json
import subprocess
from pathlib import Path

import pytest

from eww import gitdata, ingest
from tests.conftest import envelope, eonet_events, gdacs_features


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return proc.stdout.decode("utf-8", "replace")


def make_remote_with_data_branch(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'origin' whose `data` branch holds README + one gdacs snapshot + one runs file; plus a work clone."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "--quiet", "--bare")
    work = tmp_path / "work"
    git(tmp_path, "clone", "--quiet", str(origin), str(work))
    git(work, "config", "user.email", "t@example.org")
    git(work, "config", "user.name", "t")
    git(work, "checkout", "--quiet", "--orphan", "data")
    (work / "README.md").write_text("# data\n", encoding="utf-8")
    snap = envelope("gdacs", gdacs_features(), "2026-09-16T15:07:00Z", run_id="01BRANCHRUN00000000000000A")
    (work / "snapshots" / "gdacs").mkdir(parents=True)
    (work / "snapshots" / "gdacs" / "2026-09-16T15-07Z.json").write_text(json.dumps(snap), encoding="utf-8")
    (work / "runs").mkdir()
    run_line = {k: snap[k] for k in ("run_id", "source_id", "scheduled_for", "started_at", "finished_at", "status", "http_status", "items_seen")}
    run_line.update(snapshot_path="snapshots/gdacs/2026-09-16T15-07Z.json", error=None)
    (work / "runs" / "2026-09-16.jsonl").write_text(json.dumps(run_line) + "\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "seed")
    git(work, "push", "--quiet", "origin", "data")
    return origin, work


def make_laptop_clone(tmp_path: Path, origin: Path) -> Path:
    """The laptop repository: main checked out, no local `data` branch yet."""
    laptop = tmp_path / "laptop"
    git(tmp_path, "clone", "--quiet", "--no-checkout", str(origin), str(laptop))
    return laptop


def test_branch_ingest_reads_without_checkout_and_is_idempotent(conn, tmp_path):
    origin, work = make_remote_with_data_branch(tmp_path)
    laptop = make_laptop_clone(tmp_path, origin)
    assert not gitdata.branch_exists("data", laptop)

    first = ingest.ingest_branch(conn, branch="data", repo=laptop, fetch=True)
    assert first.fetched is True and first.found is True
    assert first.files_listed == 2
    assert first.snapshots_new == 1 and first.records_new == 3
    assert first.runs_inserted == 1
    assert not (laptop / "snapshots").exists()  # nothing was checked out
    assert conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0] == 3
    assert conn.execute("SELECT status, items_seen FROM collector_run WHERE run_id = '01BRANCHRUN00000000000000A'").fetchone()[0] == "ok"
    assert {r[0] for r in conn.execute("SELECT snapshot_path FROM snapshot_ingest")} == {
        "snapshots/gdacs/2026-09-16T15-07Z.json",
        "runs/2026-09-16.jsonl",
    }

    second = ingest.ingest_branch(conn, branch="data", repo=laptop, fetch=True)
    assert second.snapshots_new == 0 and second.records_new == 0 and second.runs_inserted == 0
    assert all(r.skipped for r in second.results)

    # Actions adds an EONET snapshot and appends to the same runs file; the next fetch picks both up
    snap = envelope("eonet", eonet_events(), "2026-09-16T18:07:00Z", run_id="01BRANCHRUN00000000000000B")
    (work / "snapshots" / "eonet").mkdir()
    (work / "snapshots" / "eonet" / "2026-09-16T18-07Z.json").write_text(json.dumps(snap), encoding="utf-8")
    with (work / "runs" / "2026-09-16.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": snap["run_id"], "source_id": "eonet", "scheduled_for": snap["scheduled_for"], "started_at": snap["started_at"], "finished_at": snap["finished_at"], "status": "ok", "http_status": 200, "items_seen": snap["items_seen"], "snapshot_path": "snapshots/eonet/2026-09-16T18-07Z.json", "error": None}) + "\n")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "collect 2")
    git(work, "push", "--quiet", "origin", "data")

    third = ingest.ingest_branch(conn, branch="data", repo=laptop, fetch=True)
    assert third.files_listed == 3
    assert third.snapshots_new == 1 and third.records_new == 13
    assert third.runs_inserted == 1  # only the appended line
    assert conn.execute("SELECT items_seen FROM snapshot_ingest WHERE snapshot_path = 'runs/2026-09-16.jsonl'").fetchone()[0] == 2
    assert ingest.duplicates(conn) == []


def test_branch_ingest_without_a_branch_or_remote(conn, tmp_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    git(lonely, "init", "--quiet")
    outcome = ingest.ingest_branch(conn, branch="data", repo=lonely, fetch=True)
    assert outcome.fetched is False and outcome.found is False
    assert "not found" in outcome.error
    assert outcome.results == []


def test_gitdata_helpers(tmp_path):
    origin, work = make_remote_with_data_branch(tmp_path)
    files = gitdata.list_files("data", repo=work)
    assert files == ["runs/2026-09-16.jsonl", "snapshots/gdacs/2026-09-16T15-07Z.json"]
    assert gitdata.list_files("data", ("snapshots",), repo=work) == ["snapshots/gdacs/2026-09-16T15-07Z.json"]
    text = gitdata.read_text("runs/2026-09-16.jsonl", "data", repo=work)
    assert json.loads(text.splitlines()[0])["source_id"] == "gdacs"
    with pytest.raises(gitdata.GitError):
        gitdata.read_text("runs/missing.jsonl", "data", repo=work)
    objects = gitdata.count_objects(work)
    assert "size" in objects and "size-pack" in objects
    assert gitdata.commit_count(work, "data") == 1
    assert gitdata.first_commit_at(work, "data", "snapshots") is not None
