"""The `eww` command line: init-db, collect, ingest, sync, resolve, enrich, extract, embed, attach, purge, geonames,
export, doctor, identity, report, labels, eval, task-scheduler.

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

from eww import api, collectors, config, db, documents, gitdata, heartbeat, ingest, labels, llm, ratelimit, report, resolve, snapshots
from eww import attach as attach_mod
from eww import embed as embed_mod
from eww import enrich as enrich_mod
from eww import extract as extract_mod
from eww import geocode as geocode_mod
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
labels_app = typer.Typer(help="Hand-labelling of cross-source merge pairs (data/labels/).", no_args_is_help=True, rich_markup_mode=None)
app.add_typer(labels_app, name="labels")
eval_app = typer.Typer(help="Evaluate the pipeline against hand labels.", no_args_is_help=True, rich_markup_mode=None)
app.add_typer(eval_app, name="eval")
geonames_app = typer.Typer(help="The local GeoNames gazetteer (tier 1 of the geocoder).", no_args_is_help=True, rich_markup_mode=None)
app.add_typer(geonames_app, name="geonames")

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
    do_enrich: bool = typer.Option(True, "--enrich/--no-enrich", help="Query GDELT and ReliefWeb for the active events (laptop only)."),
    do_attach: bool = typer.Option(True, "--attach/--no-attach", help="Extract, embed and attach the new documents."),
    max_events: Optional[int] = typer.Option(None, "--max-events", help=f"Events per provider per run (default {config.ENRICH_MAX_EVENTS_PER_RUN})."),
) -> None:
    """git fetch + ingest + resolve, then enrich + extract + embed + attach, then the model (ambiguous documents and summaries)."""
    started = time.monotonic()
    conn = _open()
    branch_result = ingest.ingest_branch(conn, branch=branch, repo=repo, fetch=fetch)
    local = ingest.ingest_pending(conn)
    stats = resolve.resolve(conn, refresh_days=refresh_days or None)
    enrich_line = "enrich=skipped"
    if do_enrich:
        try:
            enrich_stats = enrich_mod.run(conn, max_events=max_events)
            enrich_line = "enrich=" + ",".join(f"{k}:{v.documents_new}new/{v.events_queried}ev" + ("(stopped)" if v.stopped else "") for k, v in enrich_stats.items())
        except Exception as exc:  # the spine must never be held back by an enrichment failure
            log.error("enrich failed error=%s: %s", type(exc).__name__, exc)
            enrich_line = f"enrich=failed({type(exc).__name__})"
    attach_line = "attach=skipped"
    if do_attach:
        try:
            x = extract_mod.run(conn)
            v = embed_mod.embed_documents(conn)
            a = attach_mod.run(conn)
            model = llm.run(conn)
            again = attach_mod.run(conn)
            if model.pulled:
                typer.echo(f"pulled {config.LLM_OLLAMA_MODEL} (it was not installed)")
            attach_line = (
                f"extracted={x.documents} embedded={v.documents} attached={a.attached}+{again.attached} candidates={a.candidates} "
                f"model={model.documents} summarised={model.summarised} llm={model.backend} llm_usd={model.cost_usd:.4f}"
                + (" budget_cap" if model.fell_back else "")
            )
        except Exception as exc:
            log.error("attach stage failed error=%s: %s", type(exc).__name__, exc)
            attach_line = f"attach=failed({type(exc).__name__})"
    beat = heartbeat.summary(conn)
    fetched = {True: "ok", False: "failed", None: "skipped"}[branch_result.fetched]
    snapshots_new = branch_result.snapshots_new + sum(1 for r in local if not r.skipped)
    records_new = branch_result.records_new + sum(r.new for r in local)
    records_changed = branch_result.records_changed + sum(r.changed for r in local)
    typer.echo(
        f"sync: fetch={fetched} branch_head={(branch_result.head or 'none')[:12]} snapshots_new={snapshots_new} "
        f"records_new={records_new} records_changed={records_changed} runs_loaded={branch_result.runs_inserted} "
        f"events_created={stats.events_created} linked={stats.events_linked} merged={stats.events_merged} proposed={stats.proposals_created} gated={stats.records_gated} "
        f"events_changed={stats.events_changed} unresolved={resolve.unresolved_count(conn)} "
        f"{enrich_line} {attach_line} "
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
        f"records resolved: {stats.records_resolved} (events created: {stats.events_created}, attached to existing: {stats.events_attached}, "
        f"of which by cross-source key: {stats.events_linked}; auto-merged: {stats.events_merged}; proposals written: {stats.proposals_created}; "
        f"kept apart by the aggregation radius: {stats.records_gated}); "
        f"events refreshed: {stats.events_refreshed} (changed: {stats.events_changed}); "
        f"unresolved now: {resolve.unresolved_count(conn)}; events: {_count(conn, 'event')} "
        f"(live: {conn.execute('SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NULL').fetchone()[0]})"
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
    count: bool = typer.Option(False, "--count", help="Print only the number of pins (Point features) the filters select."),
) -> None:
    """Print the GeoJSON FeatureCollection defined in eww/api.py."""
    conn = _open()
    collection = api.events_geojson(
        since, until, hazard or None, min_severity, status or None, None, footprints, limit, conn=conn
    )
    if count:
        typer.echo(sum(1 for f in collection["features"] if f["geometry"]["type"] == "Point"))
        return
    text = json.dumps(collection, ensure_ascii=True, separators=(",", ":"))
    if out is not None:
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    log.info("export features=%d since=%s until=%s", len(collection["features"]), collection["meta"]["filters_applied"]["since"], collection["meta"]["filters_applied"]["until"])


# ----------------------------------------------------------------------------- doctor
@app.command()
def doctor(
    days: int = typer.Option(30, "--days", help="Window for the 'events observed' count."),
    log_days: int = typer.Option(config.DOCTOR_LOG_DAYS, "--log-days", help="How far back to read the provider log for cache hit rate and rate-limit maxima."),
) -> None:
    """Print the invariant queries: duplicates, unresolved records, event counts, last runs, heartbeat."""
    conn = _open()
    now = now_utc()
    typer.echo(f"database: {_state['db'] or config.DB_PATH} (schema_version {db.schema_version(conn)}, sqlite {conn.execute('SELECT sqlite_version()').fetchone()[0]})")
    typer.echo(
        f"identity: {config.IDENTITY['path']}; aggregation radius default {config.aggregation_radius_km(None):g} km; "
        f"auto-merge {config.AUTO_MERGE_THRESHOLD:g}, proposal {config.PROPOSAL_THRESHOLD:g}"
    )
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
    merged = conn.execute("SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NOT NULL").fetchone()[0]
    merges = conn.execute("SELECT COUNT(*) FROM event_lineage WHERE action = 'merge'").fetchone()[0]
    reverted = conn.execute("SELECT COUNT(*) FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NOT NULL").fetchone()[0]
    by_actor = ", ".join(f"{row[0]} {row[1]}" for row in conn.execute("SELECT performed_by, COUNT(*) FROM event_lineage WHERE action = 'merge' GROUP BY 1 ORDER BY 1"))
    typer.echo(f"events merged into another: {merged}; merges in event_lineage: {merges} ({by_actor or 'none'}), reverted: {reverted}")
    proposals = ", ".join(f"{row[0]} {row[1]}" for row in conn.execute("SELECT status, COUNT(*) FROM merge_proposal GROUP BY 1 ORDER BY 1"))
    typer.echo(f"merge proposals: {proposals or 'none'}")
    ems_events = conn.execute("SELECT COUNT(DISTINCT event_id) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NOT NULL").fetchone()[0]
    ems_unresolved = conn.execute("SELECT COUNT(*) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NULL").fetchone()[0]
    typer.echo(f"events with a Copernicus EMS activation: {ems_events}; copernicus records without an event (must be 0): {ems_unresolved}")
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
    _doctor_m3(conn, now, log_days)


def _doctor_m3(conn, now, log_days: int) -> None:
    """Documents, extraction, attachment coverage, geocoding and the rate limits (M3 exit criteria 1, 3, 4, 5)."""
    d = documents.stats(conn)
    typer.echo(
        f"documents: {d['documents']} (" + (", ".join(f"{k} {v}" for k, v in d["by_source_kind"].items()) or "none") + f"); "
        f"longest text_excerpt {d['longest_excerpt']} chars (limit {config.EXCERPT_MAX_CHARS}: {'PASS' if d['longest_excerpt'] <= config.EXCERPT_MAX_CHARS else 'FAIL'}); with a thumbnail reference {d['with_media']}"
    )
    typer.echo(
        f"extracted {d['extracted']} (classified {d['classified']}, with a located place {d['located']}); embedded {d['embedded']}; "
        f"retrieval rows {d['retrievals']}; event_document: " + (", ".join(f"{k} {v}" for k, v in d["decisions"].items()) or "none")
        + "; decided by: " + (", ".join(f"{k} {v}" for k, v in d["decided_by"].items()) or "none") + f"; mention geometries {d['mentions']}; unattached documents {d['unattached']}"
    )
    runs = conn.execute("SELECT source_id, COUNT(*) AS n, MAX(queried_at) AS last FROM enrichment_run GROUP BY 1 ORDER BY 1").fetchall()
    typer.echo("enrichment runs per provider: " + (", ".join(f"{r['source_id']} {r['n']} events (last {r['last']})" for r in runs) or "none yet"))
    cov = attach_mod.coverage(conn, min_severity=config.COVERAGE_MIN_SEVERITY, days=config.ENRICH_ACTIVE_DAYS, min_documents=config.COVERAGE_MIN_DOCUMENTS, now=now)
    share = "n/a" if cov["share"] is None else f"{100 * cov['share']:.0f}%"
    verdict = "n/a" if cov["share"] is None else ("PASS" if cov["share"] >= config.COVERAGE_TARGET else "FAIL")
    typer.echo(
        f"coverage: {cov['covered']} of {cov['events']} events with severity >= {cov['min_severity']:g} active in the last {config.ENRICH_ACTIVE_DAYS} days "
        f"have >= {cov['min_documents']} attached documents ({share}; target {100 * config.COVERAGE_TARGET:.0f}%: {verdict})"
    )
    for row in cov["rows"]:
        typer.echo(f"  {row['attached']:3d} attached {row['candidates']:3d} candidates  {row['hazard_type']:17s} {row['severity_score']:.3f} {row['title']}")
    typer.echo(f"gazetteer_place rows: {geocode_mod.gazetteer_count(conn)}; geocode_cache: " + (", ".join(f"{p} {v['entries']} ({v['found']} found)" for p, v in geocode_mod.cache_stats(conn).items()) or "empty"))
    since = now - timedelta(days=log_days)
    geo_log = ratelimit.geocode_report(since)
    rate = "n/a" if geo_log["hit_rate"] is None else f"{100 * geo_log['hit_rate']:.0f}%"
    rate_verdict = "n/a" if geo_log["hit_rate"] is None else ("PASS" if geo_log["hit_rate"] >= config.CACHE_HIT_RATE_TARGET else "below target")
    typer.echo(f"geocode lookups since {to_iso(since)}: {geo_log['lookups']} (cache hits {geo_log['cache_hits']}, misses {geo_log['cache_misses']}; hit rate {rate}, target {100 * config.CACHE_HIT_RATE_TARGET:.0f}%: {rate_verdict})")
    limits = ratelimit.limits_report(since)
    typer.echo(f"provider calls since {to_iso(since)} ({ratelimit.log_path()}):")
    if not limits:
        typer.echo("  none logged")
    for provider, r in limits.items():
        spacing = "n/a" if r["min_spacing_s"] is None else f"{r['min_spacing_s']:.1f}s"
        typer.echo(
            f"  {provider}: calls={r['calls']} statuses={r['statuses']} max/minute={r['max_per_minute']} max/hour={r['max_per_hour']} max/day={r['max_per_day']} "
            f"min spacing={spacing} user-agent carries {config.CONTACT!r}: {'yes' if r['user_agent_ok'] else 'NO'}"
        )
    checks = [
        ("nominatim <= 4 requests in any minute", limits.get("nominatim", {}).get("max_per_minute", 0) <= config.NOMINATIM_PER_MINUTE),
        ("geonames <= 1000 requests in any hour", limits.get("geonames", {}).get("max_per_hour", 0) <= config.GEONAMES_HOURLY_LIMIT),
        ("gdelt spacing >= 5 s", (limits.get("gdelt", {}).get("min_spacing_s") or config.GDELT_MIN_INTERVAL_S) >= config.GDELT_MIN_INTERVAL_S),
        ("every User-Agent carries the contact", all(r["user_agent_ok"] for r in limits.values())),
    ]
    typer.echo("rate limits (M3 exit criterion 3): " + "; ".join(f"{name}: {'PASS' if ok else 'FAIL'}" for name, ok in checks))
    typer.echo(f"model cache: {config.MODEL_DIR} ({'present' if config.MODEL_DIR.exists() else 'not downloaded yet'}); logs: {config.LOG_DIR}")
    typer.echo(f"TLS: certificates verified against {config.CA_BUNDLE or 'the bundled certifi roots'}"
               + ("" if config.CA_BUNDLE else "; set EWW_CA_BUNDLE or drop ca-bundle.pem at the repository root if a proxy inspects TLS"))
    for line in llm.doctor_lines(conn, now):
        typer.echo(line)


# ----------------------------------------------------------------------------- M3: enrichment, extraction, embeddings, attachment
@app.command()
def enrich(
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help=f"Provider; repeat for several. Default: {', '.join(config.ENRICH_SOURCES)}."),
    days: int = typer.Option(config.ENRICH_ACTIVE_DAYS, "--days", help="Events observed or ended in the last N days are queried."),
    max_events: int = typer.Option(config.ENRICH_MAX_EVENTS_PER_RUN, "--max-events", help="Events per provider per run, best severity first (0 = every active event)."),
) -> None:
    """Query GDELT (and ReliefWeb when RELIEFWEB_APPNAME is set) for the active events; write document rows. Laptop only."""
    unknown = [x for x in (source or []) if x not in config.ENRICH_SOURCES]
    if unknown:
        typer.echo(f"unknown provider(s): {', '.join(unknown)}; known: {', '.join(config.ENRICH_SOURCES)}", err=True)
        raise typer.Exit(code=2)
    conn = _open()
    started = time.monotonic()
    stats = enrich_mod.run(conn, source or None, days=days, max_events=max_events or None)
    for source_id, st in stats.items():
        typer.echo(
            f"{source_id}: events considered={st.events_considered} queried={st.events_queried} skipped={st.events_skipped} "
            f"items={st.items_seen} documents seen={st.documents_seen} new={st.documents_new} errors={st.errors}" + (f" stopped: {st.stopped}" if st.stopped else "")
        )
    typer.echo(f"{enrich_mod.summary_line(stats)} took={time.monotonic() - started:.1f}s; documents: {_count(conn, 'document')}")


@app.command()
def extract(
    limit: Optional[int] = typer.Option(None, "--limit", help="At most N documents this run."),
    remote: bool = typer.Option(True, "--remote/--no-remote", help="Allow the GeoNames web service and Nominatim tiers (else gazetteer only)."),
) -> None:
    """Classify (lexicon) and locate (NER + geocoder) every document without an extraction row."""
    conn = _open()
    if geocode_mod.gazetteer_count(conn) == 0:
        typer.echo("the gazetteer is empty: run `eww geonames load` first (tier 1 of the geocoder)", err=True)
    started = time.monotonic()
    stats = extract_mod.run(conn, limit=limit, remote=remote)
    typer.echo(
        f"extracted {stats.documents} documents: classified={stats.classified} located={stats.located} place names={stats.names}; "
        f"geocode lookups={stats.geocode_lookups} from cache={stats.cache_hits} remote calls={stats.remote_calls} took={time.monotonic() - started:.1f}s"
    )


@app.command("embed")
def embed_cmd(limit: Optional[int] = typer.Option(None, "--limit", help="At most N documents this run.")) -> None:
    """Encode every document without a vector with the local sentence-transformers model (CPU)."""
    conn = _open()
    started = time.monotonic()
    stats = embed_mod.embed_documents(conn, limit=limit)
    typer.echo(f"embedded {stats.documents} documents model={stats.model} dim={stats.dim} took={time.monotonic() - started:.1f}s; vectors: {_count(conn, 'document_embedding')}")


@app.command("attach")
def attach_cmd(
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete every pipeline decision and re-decide all documents from scratch (run it on a copy)."),
    days: int = typer.Option(config.ATTACH_RETRY_DAYS, "--days", help="Without --rebuild: re-score undecided documents younger than N days."),
) -> None:
    """Score documents against events (docs/architecture.md §3, step 2) and write event_document rows."""
    if rebuild and _state["db"] is None:
        typer.echo("--rebuild re-decides every pipeline row: run it on a copy (`eww --db data/copy.sqlite attach --rebuild`)", err=True)
        raise typer.Exit(code=2)
    conn = _open()
    started = time.monotonic()
    stats = attach_mod.run(conn, rebuild=rebuild, days=days)
    typer.echo(
        f"attach: documents={stats.documents} attached={stats.attached} candidates={stats.candidates} none={stats.none} no_candidates={stats.no_candidates} "
        f"mentions={stats.mentions}" + (f" deleted_pipeline_rows={stats.rebuilt_rows_deleted}" if rebuild else "") + f" took={time.monotonic() - started:.1f}s"
    )
    for row in conn.execute("SELECT status, decided_by, COUNT(*) AS n FROM event_document GROUP BY 1, 2 ORDER BY 1, 2"):
        typer.echo(f"  event_document {row['status']} by {row['decided_by']}: {row['n']}")


@app.command()
def purge(
    days: int = typer.Option(config.PURGE_UNATTACHED_DAYS, "--days", help="Documents older than this (published, else fetched) without an attached or candidate row."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count only."),
) -> None:
    """Delete unattached documents older than N days with their extraction, embedding, retrieval and mention rows."""
    conn = _open()
    stats = documents.purge_unattached(conn, days, dry_run=dry_run)
    verb = "would delete" if dry_run else "deleted"
    typer.echo(
        f"purge cutoff={stats.cutoff}: {verb} documents={stats.documents} extractions={stats.extractions} embeddings={stats.embeddings} "
        f"retrievals={stats.retrievals} mentions={stats.mentions} rejected links={stats.rejected_links}; documents left: {_count(conn, 'document')}"
    )


@geonames_app.command("load")
def geonames_load(
    source_dir: Optional[Path] = typer.Option(None, "--from", help="Folder holding cities500.zip, admin1CodesASCII.txt and countryInfo.txt (default: download them)."),
) -> None:
    """Download the GeoNames dump (CC BY 4.0) into the gazetteer_place table and rebuild gazetteer_fts. Idempotent."""
    import tempfile

    conn = _open()
    started = time.monotonic()
    if source_dir is None:
        tmp = Path(tempfile.mkdtemp(prefix="eww-geonames-"))
        try:
            geocode_mod.download_dump(tmp)
            stats = geocode_mod.load_geonames(conn, tmp)
        finally:
            gitdata.rmtree(tmp)
    else:
        stats = geocode_mod.load_geonames(conn, source_dir)
    typer.echo(f"gazetteer loaded: cities={stats.cities} admin1={stats.admin1} countries={stats.countries} skipped lines={stats.skipped} rows={geocode_mod.gazetteer_count(conn)} took={time.monotonic() - started:.1f}s")
    typer.echo("attribution: place names and coordinates from GeoNames (geonames.org), CC BY 4.0")


@eval_app.command("attachments")
def eval_attachments(
    sample: Optional[int] = typer.Option(None, "--sample", help="Write a CSV of N random attached rows to label (default path in config)."),
    seed: Optional[int] = typer.Option(None, "--seed", help="Seed for the random sample."),
    out: Optional[Path] = typer.Option(None, "--out", help=f"Sample CSV path (default {config.ATTACHMENT_SAMPLE_CSV})."),
    labels_path: Optional[Path] = typer.Option(None, "--labels", help="The filled CSV to evaluate (default: the sample path)."),
    report_path: Optional[Path] = typer.Option(None, "--report", help="Default: docs/m3.md (the milestone record)."),
) -> None:
    """--sample N writes attached rows for hand-checking; without it, computes precision from the filled file and writes docs/m3.md.

    Fill `correct` with yes or no and, for the wrong ones, `cause`. Exit code 1 when precision is below the target.
    """
    conn = _open()
    if sample is not None:
        rows = labels.attachment_sample(conn, sample, seed)
        path = labels.write_attachment_sample(rows, out)
        typer.echo(f"written {path}: {len(rows)} attached rows to label (fill `correct` yes/no and `cause`), then run `eww eval attachments`")
        return
    path = labels_path or out or config.ATTACHMENT_SAMPLE_CSV
    evaluation = None
    if path.exists():
        evaluation = labels.evaluate_attachments(labels.read_attachment_labels(path))
        typer.echo(labels.render_attachment_evaluation(evaluation))
    else:
        typer.echo(f"{path} does not exist; run `eww eval attachments --sample 100` first (the report is written without the precision section)", err=True)
    report_file, result = report.write_attachment_report(conn, evaluation, report_path)
    cov = result["coverage"]
    share = "n/a" if cov["share"] is None else f"{100 * cov['share']:.0f}%"
    typer.echo(f"written {report_file}; coverage {cov['covered']}/{cov['events']} ({share}) of severe events with >= {cov['min_documents']} attached documents")
    if evaluation is not None and not evaluation["passed"]:
        raise typer.Exit(code=1)


@eval_app.command("extraction")
def eval_extraction(
    backend: Optional[str] = typer.Option(None, "--backend", help="local (default), ollama, or anthropic. Anthropic still stops at the budget cap."),
    report_path: Optional[Path] = typer.Option(None, "--report", help="Default: docs/m4.md (the milestone record)."),
) -> None:
    """Score the golden set (tests/golden/) and write docs/m4.md. Exit code 1 when a bar is missed."""
    if backend is not None and backend not in ("local", "ollama", "anthropic"):
        typer.echo("backend must be local, ollama or anthropic", err=True)
        raise typer.Exit(code=2)
    conn = _open()
    name = backend or config.LLM_BACKEND
    # Same cap check as eww sync: a cap of 0 never constructs the cloud client.
    document_rows = llm.load_jsonl(config.GOLDEN_DOCUMENTS)
    event_rows = llm.load_jsonl(config.GOLDEN_EVENTS)
    payloads = [llm.user_extract(row, row.get("candidates") or []) for row in document_rows]
    payloads += [llm.user_summary(row, row.get("authority_text") or "", row.get("documents") or []) for row in event_rows]
    estimate = llm.estimate_batch_usd(llm.system_prefix("extract"), payloads)
    extractor, fell_back = llm.make_extractor(conn, estimate, backend=name)
    if fell_back:
        typer.echo("budget cap reached; scoring with the local extractor")
    result = llm.evaluate(conn, extractor)
    extractor.close()
    if extractor.pulled:
        typer.echo(f"pulled {config.LLM_OLLAMA_MODEL} (it was not installed)")
    path = llm.write_eval_report(conn, result, str(_state["db"] or config.DB_PATH), report_path)
    typer.echo(
        f"hazard {100 * result['hazard_accuracy']:.1f}%  places {100 * result['place_resolution']:.1f}%  "
        f"figures {100 * result['figures_exact']:.1f}%  span violations {result['span_violations']}  "
        f"cost ${result['cost_usd']:.4f}"
    )
    typer.echo(f"written {path}")
    if not result["passed"]:
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------------- reports
@report_app.command("density")
def report_density(
    days: int = typer.Option(30, "--days"),
    out: Optional[Path] = typer.Option(None, "--out", help="Default: docs/m0.md (the milestone record)."),
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
    out: Optional[Path] = typer.Option(None, "--out", help="Default: docs/m1.md (the milestone record)."),
) -> None:
    """Clone the data branch fresh, measure `git count-objects -vH`, extrapolate a year, write docs/m1.md."""
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


@report_app.command("identity")
def report_identity(
    days: int = typer.Option(30, "--days", help="Window for the event counts and the feed-disagreement table."),
    out: Optional[Path] = typer.Option(None, "--out", help="Default: docs/m2.md (the milestone record)."),
) -> None:
    """Write the M2 identity report: parameters in force, joins by rule, how far apart the feeds place one event, open proposals, labels."""
    conn = _open()
    path, result = report.write_identity_report(conn, out, days=days)
    typer.echo(f"written {path}")
    typer.echo(
        f"live events in {days} days={result['events_in_window']} multi-source={result['multi_source_in_window']} "
        f"merges={sum(m['count'] for m in result['merges'])} open proposals={len(result['open_proposals'])} "
        f"kept apart by the aggregation radius={len(result['gated'])}"
    )


# ----------------------------------------------------------------------------- identity parameters
@app.command("identity")
def identity_cmd() -> None:
    """Print the identity parameters in force (identity.yaml): aggregation radius, blocking, thresholds, score."""
    ident = config.IDENTITY
    typer.echo(f"identity file: {ident['path']}")
    typer.echo("aggregation radius (km): " + ", ".join(f"{k}={v:g}" for k, v in ident["aggregation_radius_km"].items()))
    typer.echo("blocking (km/days): " + ", ".join(f"{h}={km:g}/{d:g}" for h, (km, d) in ident["blocking"].items()) + f"; named storms {ident['named_storm_km']:g} km")
    typer.echo(f"thresholds: auto-merge {ident['auto_merge']:g}, proposal {ident['proposal']:g}")
    typer.echo(
        f"score: weights spatial {ident['weights']['spatial']:g} temporal {ident['weights']['temporal']:g} text {ident['weights']['text']:g}; "
        f"key {ident['key_equal']:g}, glide {ident['glide_equal']:g}, storm name {ident['storm_name_equal']:g}"
    )
    att = ident["attachment"]
    typer.echo(
        f"attachment: weights spatial {att['weights']['spatial']:g} temporal {att['weights']['temporal']:g} text {att['weights']['text']:g}; "
        f"no place: temporal {att['no_place_weights']['temporal']:g} text {att['no_place_weights']['text']:g} (text >= {att['no_place_min_text']:g}); "
        f"query prior {att['query_prior']:g}; attach >= {att['attach_threshold']:g}, candidate >= {att['candidate_threshold']:g}; "
        f"windows: document -{att['doc_before_days']:g}/+{att['doc_after_days']:g} d, event -{att['event_before_days']:g}/+{att['event_after_days']:g} d, decay {att['decay_days']:g} d; "
        f"syndication cosine {att['syndication_cosine']:g}; title similarity {config.TITLE_SIMILARITY}"
    )


# ----------------------------------------------------------------------------- labels and evaluation
@labels_app.command("candidates")
def labels_candidates(
    days: int = typer.Option(30, "--days", help="Pairs among records observed in the last N days."),
    out: Optional[Path] = typer.Option(None, "--out", help=f"Default: {config.LABEL_CANDIDATES_CSV}"),
) -> None:
    """Write every cross-source pair that blocks (same hazard class, within R and T) with its evidence and an empty `same_event` column.

    Copy the file to data/labels/merge_pairs.csv and fill `same_event` with yes or no by hand; `eww eval merges` reads it.
    """
    conn = _open()
    rows = labels.candidate_pairs(conn, days)
    path = labels.write_candidates(rows, out)
    verdicts = {}
    for row in rows:
        verdicts[row["pipeline"]] = verdicts.get(row["pipeline"], 0) + 1
    typer.echo(
        f"written {path}: {len(rows)} candidate pairs, {sum(1 for r in rows if r['linked'] == 'yes')} carrying a deterministic key; "
        "pipeline verdicts: " + (", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())) or "none")
    )


@eval_app.command("merges")
def eval_merges(
    pairs: Optional[Path] = typer.Option(None, "--pairs", help=f"Default: {config.LABEL_PAIRS_CSV}"),
) -> None:
    """Precision and recall of the auto-merge rule against the hand labels; lists every true pair neither merged nor proposed.

    Exit code 1 when a labelled non-pair sits on one event (a false merge) or a true pair is missing altogether.
    """
    path = pairs or config.LABEL_PAIRS_CSV
    if not path.exists():
        typer.echo(f"{path} does not exist; run `eww labels candidates`, copy the file there and fill `same_event`", err=True)
        raise typer.Exit(code=2)
    conn = _open()
    result = labels.evaluate(conn, labels.read_pairs(path))
    typer.echo(labels.render_evaluation(result))
    if result["false_merges"] or result["missed"]:
        raise typer.Exit(code=1)


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
