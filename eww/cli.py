"""The `eww` command line: init-db, collect, ingest, resolve, export, doctor, report density.

Every command is idempotent and safe to re-run. Logs are structured key=value lines on stderr
so that `eww export > events.geojson` stays clean JSON on stdout.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import typer

from eww import api, config, db, heartbeat, ingest, report, resolve, snapshots
from eww.clock import now_utc, parse_when, to_iso
from eww.collect import collect_source

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


@app.command("init-db")
def init_db_cmd() -> None:
    """Apply sql/schema.sql when schema_version is empty and seed the source table."""
    conn = db.connect(_state["db"])
    applied = db.init_db(conn)
    typer.echo(f"database={_state['db'] or config.DB_PATH} schema_version={db.schema_version(conn)} applied_now={applied}")
    for row in conn.execute("SELECT source_id, display_name, attribution FROM source ORDER BY source_id"):
        typer.echo(f"source {row['source_id']}: {row['display_name']} | {row['attribution']}")


@app.command()
def collect(
    source: list[str] = typer.Option(["gdacs", "eonet"], "--source", "-s", help="Source id; repeat for several."),
    days: int = typer.Option(config.DEFAULT_COLLECT_DAYS, "--days", help="Window length ending at --until."),
    until: Optional[str] = typer.Option(None, "--until", help="ISO 8601 end of the window (default: now)."),
) -> None:
    """Fetch each source, write its snapshot and run log, then ingest into source_record."""
    conn = _open()
    now = now_utc()
    until_dt = parse_when(until, now) or now
    since_dt = until_dt - timedelta(days=days)
    total_new = total_changed = 0
    failed = 0
    for source_id in source:
        result = collect_source(conn, source_id, since_dt, until_dt)
        run = result.run
        if result.ingest is None:
            failed += 1
            typer.echo(f"{source_id}: status={run['status']} error={run['error']}")
            continue
        stats = result.ingest
        total_new += stats.new
        total_changed += stats.changed
        typer.echo(
            f"{source_id}: status={run['status']} items={stats.items_seen} records={stats.records} "
            f"new={stats.new} changed={stats.changed} unchanged={stats.unchanged} errors={len(stats.errors)} "
            f"snapshot={run['snapshot_path']}"
        )
    typer.echo(f"new source_record rows: {total_new} (changed: {total_changed}); events: {_count(conn, 'event')}")
    if failed:
        raise typer.Exit(code=1)


@app.command("ingest")
def ingest_cmd(
    path: Optional[Path] = typer.Option(None, "--path", help="One snapshot file to (re)ingest; default: every pending file."),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if snapshot_ingest already lists the file."),
) -> None:
    """Replay snapshot files under data/snapshots that have not been ingested yet."""
    conn = _open()
    if path is not None:
        results = [ingest.ingest_snapshot(conn, path, force=force)]
    else:
        results = ingest.ingest_pending(conn)
    for stats in results:
        state = "skipped" if stats.skipped else f"records={stats.records} new={stats.new} changed={stats.changed} unchanged={stats.unchanged}"
        typer.echo(f"{stats.snapshot_path}: {state}")
    typer.echo(f"snapshots processed: {len(results)}; new source_record rows: {sum(s.new for s in results)}")


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
    typer.echo(f"snapshots ingested: {_count(conn, 'snapshot_ingest')}; pending on disk: {len(snapshots.pending(conn))}")
    typer.echo("last run per source:")
    for row in heartbeat.last_runs(conn):
        typer.echo(
            f"  {row['source_id']}: {row['started_at']} status={row['status']} http={row['http_status']} "
            f"items={row['items_seen']} snapshot={row['snapshot_path']}" + (f" error={row['error']}" if row["error"] else "")
        )
    beat = heartbeat.summary(conn, now)
    typer.echo(
        f"heartbeat: last_collector_run_at={beat['last_collector_run_at']} "
        f"slots_expected_7d={beat['expected_runs_7d']} slots_served={beat['observed_runs_7d']} missed_runs_7d={beat['missed_runs_7d']}"
    )


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


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    app()
