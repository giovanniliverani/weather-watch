"""The `eww` command line: init-db, collect, ingest, sync, resolve, export, doctor, report, task-scheduler.

Every command is idempotent and safe to re-run. Logs are structured key=value lines on stderr
so that `eww export > events.geojson` stays clean JSON on stdout.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import typer

from eww import api, collectors, config, db, gitdata, heartbeat, ingest, report, resolve, snapshots
from eww.clock import now_utc, parse_iso, parse_when, to_iso
from eww.collect import collect_source, summary_line

app = typer.Typer(
    help="Extreme Weather Watch: collect, resolve and export hazard events.",
    no_args_is_help=True,
    rich_markup_mode=None,  # plain help output; rich's legacy Windows renderer breaks on piped stdout
    pretty_exceptions_enable=False,
)
report_app = typer.Typer(help="Reports written under docs/.", no_args_is_help=True, rich_markup_mode=None)
app.add_typer(report_app, name="report")

log = logging.getLogger("eww")
_state: dict[str, Optional[Path]] = {"db": None}


@app.callback()
def main(
    db_path: Optional[Path] = typer.Option(None, "--db", help="SQLite file (default: EWW_DB_PATH or data/eww.sqlite)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _state["db"] = db_path


def _open():
    conn = db.connect(_state["db"])
    db.init_db(conn)
    return conn


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ----------------------------------------------------------------------------- schema
@app.command("init-db")
def init_db_cmd() -> None:
    """Apply sql/schema.sql when schema_version is empty, run pending migrations, seed the source table."""
    conn = db.connect(_state["db"])
    applied = db.init_db(conn)
    typer.echo(f"database={_state['db'] or config.DB_PATH} schema_version={db.schema_version(conn)} applied_now={applied}")
    for row in conn.execute("SELECT source_id, display_name, attribution FROM source ORDER BY source_id"):
        typer.echo(f"source {row['source_id']}: {row['display_name']} | {row['attribution']}")


# ----------------------------------------------------------------------------- collect
@app.command()
def collect(
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="Source id; repeat for several. Default: every spine source."),
    all_spine: bool = typer.Option(False, "--all-spine", help=f"Every spine source: {', '.join(config.SPINE_SOURCES)}."),
    days: int = typer.Option(config.DEFAULT_COLLECT_DAYS, "--days", help="Window length ending at --until."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO 8601 end of the window (default: now)."),
    out: Optional[Path] = typer.Option(None, "--out", help="Write snapshots/ and runs/ under this directory and touch no database (what GitHub Actions runs)."),
    do_ingest: Optional[bool] = typer.Option(None, "--ingest/--no-ingest", help="Ingest the new snapshots into the database. Default: yes without --out, no with --out."),
) -> None:
    """Fetch each source and write its snapshot and run log; locally, also ingest into source_record.

    Exit code 1 only when every source failed.
    """
    sources = list(config.SPINE_SOURCES) if all_spine or not source else list(source)
    unknown = [s for s in sources if s not in collectors.COLLECTORS]
    if unknown:
        typer.echo(f"unknown source(s): {', '.join(unknown)}; known: {', '.join(sorted(collectors.COLLECTORS))}", err=True)
        raise typer.Exit(code=2)
    ingest_now = (out is None) if do_ingest is None else do_ingest
    data_dir = out if out is not None else config.DATA_DIR
    conn = _open() if ingest_now else None
    now = now_utc()
    until_dt = parse_when(until, now) or now
    since_dt = until_dt - timedelta(days=days)
    results = []
    total_new = total_changed = 0
    for source_id in sources:
        result = collect_source(conn, source_id, since_dt, until_dt, data_dir=data_dir, ingest_into_db=ingest_now)
        results.append(result)
        run = result.run
        if result.failed:
            typer.echo(f"{source_id}: status=failed error={run['error']}")
            continue
        line = f"{source_id}: status={run['status']} items={run['items_seen']} snapshot={run['snapshot_path']}"
        if result.ingest is not None:
            stats = result.ingest
            total_new += stats.new
            total_changed += stats.changed
            line += f" records={stats.records} new={stats.new} changed={stats.changed} unchanged={stats.unchanged} errors={len(stats.errors)}"
        typer.echo(line)
    if conn is not None:
        typer.echo(f"new source_record rows: {total_new} (changed: {total_changed}); events: {_count(conn, 'event')}")
    typer.echo(summary_line(results))
    if results and all(r.failed for r in results):
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------------- ingest and sync
def _echo_ingest_results(results: list[ingest.IngestStats], label: str) -> None:
    processed = [r for r in results if not r.skipped]
    snaps = [r for r in processed if r.kind == "snapshot"]
    runs = [r for r in processed if r.kind == "runs"]
    typer.echo(
        f"{label}: new snapshot files={len(snaps)} (records new={sum(r.new for r in snaps)} changed={sum(r.changed for r in snaps)} "
        f"unchanged={sum(r.unchanged for r in snaps)}) runs files read={len(runs)} (runs inserted={sum(r.new for r in runs)}) "
        f"skipped={sum(1 for r in results if r.skipped)} errors={sum(len(r.errors) for r in results)}"
    )


@app.command("ingest")
def ingest_cmd(
    source: str = typer.Option("all", "--from", help="Where to read snapshots from: local | branch | all."),
    path: Optional[Path] = typer.Option(None, "--path", help="One local snapshot file to (re)ingest."),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if snapshot_ingest already lists the file(s)."),
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="git fetch the data branch first."),
    branch: str = typer.Option(config.DATA_BRANCH, "--branch"),
    repo: Optional[Path] = typer.Option(None, "--repo", help="Git repository holding the data branch (default: this project)."),
) -> None:
    """Replay snapshots and run logs the database has not seen: local data/snapshots, the data branch, or both."""
    if source not in ("local", "branch", "all"):
        typer.echo("--from must be local, branch or all", err=True)
        raise typer.Exit(code=2)
    conn = _open()
    new_files = 0
    if path is not None:
        stats = ingest.ingest_snapshot(conn, path, force=force)
        _echo_ingest_results([stats], f"file {stats.snapshot_path}")
        new_files += 0 if stats.skipped else 1
    if source in ("local", "all"):
        results = ingest.ingest_pending(conn)
        _echo_ingest_results(results, "local")
        new_files += sum(1 for r in results if not r.skipped)
    if source in ("branch", "all"):
        outcome = ingest.ingest_branch(conn, branch=branch, repo=repo, fetch=fetch, force=force)
        if not outcome.found:
            typer.echo(f"branch {branch}: {outcome.error}")
        else:
            fetched = {True: "fetched", False: "fetch failed, using the local copy", None: "not fetched"}[outcome.fetched]
            typer.echo(f"branch {branch} ({fetched}) head={(outcome.head or '')[:12]} files listed={outcome.files_listed}")
            _echo_ingest_results(outcome.results, f"branch {branch}")
            new_files += sum(1 for r in outcome.results if not r.skipped and r.kind == "snapshot")
    typer.echo(f"new snapshot files: {new_files}")


@app.command()
def sync(
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="git fetch the data branch first."),
    refresh_days: int = typer.Option(30, "--refresh-days", help="Also refresh events seen in the last N days."),
    branch: str = typer.Option(config.DATA_BRANCH, "--branch"),
    repo: Optional[Path] = typer.Option(None, "--repo"),
) -> None:
    """git fetch + ingest (data branch and local files) + resolve, then one summary line."""
    started = time.monotonic()
    conn = _open()
    branch_result = ingest.ingest_branch(conn, branch=branch, repo=repo, fetch=fetch)
    local = ingest.ingest_pending(conn)
    stats = resolve.resolve(conn, refresh_days=refresh_days or None)
    beat = heartbeat.summary(conn)
    fetched = {True: "ok", False: "failed", None: "skipped"}[branch_result.fetched]
    snapshots_new = branch_result.snapshots_new + sum(1 for r in local if not r.skipped)
    records_new = branch_result.records_new + sum(r.new for r in local)
    records_changed = branch_result.records_changed + sum(r.changed for r in local)
    typer.echo(
        f"sync: fetch={fetched} branch_head={(branch_result.head or 'none')[:12]} snapshots_new={snapshots_new} "
        f"records_new={records_new} records_changed={records_changed} runs_loaded={branch_result.runs_inserted} "
        f"events_created={stats.events_created} events_changed={stats.events_changed} unresolved={resolve.unresolved_count(conn)} "
        f"missed_runs_7d={beat['missed_runs_7d']}/{beat['expected_runs_7d']} took={time.monotonic() - started:.1f}s"
    )


# ----------------------------------------------------------------------------- resolve and export
@app.command("resolve")
def resolve_cmd(
    refresh_days: int = typer.Option(30, "--refresh-days", help="Also refresh events seen in the last N days (0 = only touched events)."),
) -> None:
    """Attach every unresolved source_record to an event, creating events where needed."""
    conn = _open()
    stats = resolve.resolve(conn, refresh_days=refresh_days or None)
    typer.echo(
        f"records resolved: {stats.records_resolved} (events created: {stats.events_created}, attached to existing: {stats.events_attached}); "
        f"events refreshed: {stats.events_refreshed} (changed: {stats.events_changed}); "
        f"unresolved now: {resolve.unresolved_count(conn)}; events: {_count(conn, 'event')}"
    )


@app.command()
def export(
    since: str = typer.Option(config.DEFAULT_EXPORT_SINCE, "--since", help="ISO 8601 or relative, e.g. 30d."),
    until: Optional[str] = typer.Option(None, "--until"),
    hazard: Optional[list[str]] = typer.Option(None, "--hazard", help="hazard_type value; repeat for several."),
    min_severity: float = typer.Option(0.0, "--min-severity"),
    status: Optional[list[str]] = typer.Option(None, "--status"),
    footprints: bool = typer.Option(False, "--footprints", help="Include footprint/track polygons."),
    limit: int = typer.Option(0, "--limit", help="Maximum number of events; 0 = every event in the window."),
    out: Optional[Path] = typer.Option(None, "--out", help="Write here instead of stdout."),
) -> None:
    """Print the GeoJSON FeatureCollection defined in eww/api.py."""
    conn = _open()
    collection = api.events_geojson(
        since, until, hazard or None, min_severity, status or None, None, footprints, limit, conn=conn
    )
    text = json.dumps(collection, ensure_ascii=True, separators=(",", ":"))
    if out is not None:
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    log.info("export features=%d since=%s until=%s", len(collection["features"]), collection["meta"]["filters_applied"]["since"], collection["meta"]["filters_applied"]["until"])


# ----------------------------------------------------------------------------- doctor
@app.command()
def doctor(days: int = typer.Option(30, "--days", help="Window for the 'events observed' count.")) -> None:
    """Print the invariant queries: duplicates, unresolved records, event counts, last runs, heartbeat."""
    conn = _open()
    now = now_utc()
    typer.echo(f"database: {_state['db'] or config.DB_PATH} (schema_version {db.schema_version(conn)}, sqlite {conn.execute('SELECT sqlite_version()').fetchone()[0]})")
    dupes = ingest.duplicates(conn)
    typer.echo(f"duplicate source_record keys (source_id, external_id, external_episode): {len(dupes)}")
    for row in dupes[:20]:
        typer.echo(f"  {row['source_id']} {row['external_id']} {row['external_episode']!r} x{row['n']}")
    typer.echo(f"unresolved source_record rows (event_id IS NULL): {resolve.unresolved_count(conn)}")
    typer.echo(f"source_record rows: {_count(conn, 'source_record')}")
    for row in conn.execute("SELECT source_id, COUNT(*) AS n FROM source_record GROUP BY 1 ORDER BY 1"):
        typer.echo(f"  {row['source_id']}: {row['n']}")
    typer.echo(f"events: {_count(conn, 'event')}")
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM event GROUP BY 1 ORDER BY 1"):
        typer.echo(f"  status {row['status']}: {row['n']}")
    for row in conn.execute("SELECT hazard_type, COUNT(*) AS n FROM event WHERE merged_into_event_id IS NULL GROUP BY 1 ORDER BY 2 DESC"):
        typer.echo(f"  {row['hazard_type']}: {row['n']}")
    since = to_iso(now - timedelta(days=days))
    typer.echo(f"events observed in the last {days} days (the exporter's window, since {since}): {api.count_in_window(conn, since, now=now)}")
    typer.echo(f"events without a centroid: {conn.execute('SELECT COUNT(*) FROM event WHERE centroid_lat IS NULL').fetchone()[0]}")
    footprints = conn.execute("SELECT COUNT(*) FROM event_geometry WHERE role = 'footprint'").fetchone()[0]
    typer.echo(f"event_geometry rows: {_count(conn, 'event_geometry')} (footprints: {footprints})")
    typer.echo(f"files ingested (snapshot_ingest): {_count(conn, 'snapshot_ingest')}; local snapshots pending: {len(snapshots.pending(conn))}")
    head = gitdata.head_commit()
    if head:
        listed = gitdata.list_files()
        known = {row[0] for row in conn.execute("SELECT snapshot_path FROM snapshot_ingest")}
        typer.echo(f"data branch {config.DATA_BRANCH}: head={head[:12]} files={len(listed)} not yet ingested={sum(1 for p in listed if p not in known)}")
    else:
        typer.echo(f"data branch {config.DATA_BRANCH}: not present locally (run `eww sync` or `git fetch origin {config.DATA_BRANCH}:{config.DATA_BRANCH}`)")
    typer.echo(f"collector runs: {_count(conn, 'collector_run')}; last run per source:")
    for row in heartbeat.last_runs(conn):
        typer.echo(
            f"  {row['source_id']}: {row['started_at']} status={row['status']} http={row['http_status']} "
            f"items={row['items_seen']} snapshot={row['snapshot_path']}" + (f" error={row['error']}" if row["error"] else "")
        )
    beat = heartbeat.summary(conn, now)
    typer.echo(
        f"heartbeat: last successful run={beat['last_collector_run_at']} last run any status={beat['last_run_any_status_at']} "
        f"slots expected 7d={beat['expected_runs_7d']} served={beat['observed_runs_7d']} missed_runs_7d={beat['missed_runs_7d']} "
        f"(a slot is served by an ok run starting within {config.HEARTBEAT_GRACE_MINUTES} minutes of it)"
    )
    missed = heartbeat.missed_slots(conn, now)
    if missed:
        typer.echo("  most recent missed slots: " + ", ".join(missed))


# ----------------------------------------------------------------------------- reports
@report_app.command("density")
def report_density(
    days: int = typer.Option(30, "--days"),
    out: Optional[Path] = typer.Option(None, "--out", help="Default: docs/m0-density.md"),
) -> None:
    """Write the M0 density report and print the verdict."""
    conn = _open()
    path, result = report.write_density_report(conn, days, out)
    typer.echo(f"written {path}")
    for criterion in result["criteria"]:
        typer.echo(f"  {'PASS' if criterion['passed'] else 'FAIL'}  {criterion['name']}: {criterion['value']} (target {criterion['target']})")
    typer.echo(f"verdict: {'PASS' if result['passed'] else 'FAIL'} (events counted: {result['events_counted']}, after removing cross-source mirrors: {result['events_deduplicated']})")


@report_app.command("volume")
def report_volume(
    remote: Optional[str] = typer.Option(None, "--remote", help="Repository URL or path to clone (default: the origin URL)."),
    branch: str = typer.Option(config.DATA_BRANCH, "--branch"),
    out: Optional[Path] = typer.Option(None, "--out", help="Default: docs/m1-volume.md"),
) -> None:
    """Clone the data branch fresh, measure `git count-objects -vH`, extrapolate a year, write docs/m1-volume.md."""
    url = remote or gitdata.remote_url()
    if not url:
        typer.echo("no remote URL; pass --remote", err=True)
        raise typer.Exit(code=2)
    path, result = report.write_volume_report(url, branch, out)
    typer.echo(f"written {path}")
    typer.echo(
        f"size-pack={result['size_pack_mb']:.2f} MB over {result['days']:.1f} days of snapshots "
        f"({result['snapshot_files']} files, {result['commits']} commits); per day={result['per_day_mb']} MB; "
        f"per year={result['per_year_mb']} MB; verdict={result['verdict']}"
    )


# ----------------------------------------------------------------------------- scheduling hints
@app.command("task-scheduler")
def task_scheduler() -> None:
    """Print (do not register) the Windows Task Scheduler commands that run `eww sync` at logon and every 2 hours."""
    uv = shutil.which("uv") or "uv"
    repo = config.PROJECT_ROOT
    log_path = config.DATA_DIR / "sync.log"
    action = f'cmd /c ""{uv}" run --directory "{repo}" eww sync >> "{log_path}" 2>&1"'
    typer.echo("Run these in PowerShell (single quotes are PowerShell syntax; they only create the tasks, nothing runs until logon or the next 2-hour tick):")
    typer.echo("")
    typer.echo(f'schtasks /Create /F /TN "EWW sync at logon" /SC ONLOGON /DELAY 0002:00 /TR \'{action}\'')
    typer.echo(f'schtasks /Create /F /TN "EWW sync every 2 hours" /SC HOURLY /MO 2 /TR \'{action}\'')
    typer.echo("")
    typer.echo('Check with: schtasks /Query /TN "EWW sync every 2 hours" /V /FO LIST')
    typer.echo('Remove with: schtasks /Delete /F /TN "EWW sync at logon"  and  schtasks /Delete /F /TN "EWW sync every 2 hours"')
    typer.echo(f"The log lands in {log_path}; the tasks run as your user, so uv, git and the repo are the ones you use interactively.")


if __name__ == "__main__":
    app()
