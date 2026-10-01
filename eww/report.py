"""`eww report density`: the M0 density check written to docs/m0.md (the milestone record, config.milestone_doc).

Counts the events observed in the last N days by hazard type and by continent (via
eww/data/countries.csv), counts non-wildfire events in Europe, and judges them against the
bar pre-declared in docs/architecture.md §4. The bar excludes GDACS wildfires below Orange;
that exclusion is applied to every criterion, and the unfiltered numbers are shown next to
the filtered ones. Because M0 has no cross-source merging, a GDACS flood that EONET mirrors
is two events; the report also gives the total after removing those mirrors.
"""

from __future__ import annotations

import csv
import json
import random
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from eww import api, config, countries, labels as labels_mod
from eww import weather as weather_mod
from eww.clock import now_utc, to_iso

GDACS_EVENTID_RE = re.compile(r"gdacs\.org/.*?[?&]eventid=(\d+)", re.IGNORECASE)
LOW_WILDFIRE_THRESHOLD = config.GDACS_SEVERITY["Orange"]


def window_events(conn: sqlite3.Connection, since_iso: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT e.* FROM event e WHERE {api.WINDOW_SQL} ORDER BY e.last_observed_at DESC",
        {"since": since_iso, "until": None},
    ).fetchall()
    events = {row["event_id"]: dict(row, source_ids=set(), gdacs_ids=set()) for row in rows}
    for chunk_start in range(0, len(rows), 400):
        chunk = [r["event_id"] for r in rows[chunk_start : chunk_start + 400]]
        marks = ", ".join("?" * len(chunk))
        for rec in conn.execute(
            f"SELECT event_id, source_id, external_id, payload FROM source_record WHERE event_id IN ({marks})", chunk
        ):
            event = events[rec["event_id"]]
            event["source_ids"].add(rec["source_id"])
            if rec["source_id"] == "gdacs":
                event["gdacs_ids"].add(str(rec["external_id"]))
            elif rec["source_id"] == "eonet":
                payload = json.loads(rec["payload"])
                for source in payload.get("sources") or []:
                    match = GDACS_EVENTID_RE.search(str(source.get("url") or ""))
                    if match:
                        event["gdacs_ids"].add(match.group(1))
    gdacs_present = {gid for e in events.values() if "gdacs" in e["source_ids"] for gid in e["gdacs_ids"]}
    for event in events.values():
        event["continent"] = countries.continent_for(event["country_iso3"]) or "Unknown"
        event["is_gdacs_low_wildfire"] = (
            "gdacs" in event["source_ids"]
            and event["hazard_type"] == "wildfire"
            and (event["severity_score"] or 0.0) < LOW_WILDFIRE_THRESHOLD
        )
        event["mirrors_gdacs"] = "gdacs" not in event["source_ids"] and bool(event["gdacs_ids"] & gdacs_present)
    return list(events.values())


def density(conn: sqlite3.Connection, days: int = 30, now: datetime | None = None) -> dict:
    now = now or now_utc()
    since_iso = to_iso(now - timedelta(days=days))
    events = window_events(conn, since_iso)
    counted = [e for e in events if not e["is_gdacs_low_wildfire"]]
    deduplicated = [e for e in counted if not e["mirrors_gdacs"]]
    bar = config.DENSITY_BAR
    by_hazard_all = Counter(e["hazard_type"] for e in events)
    by_hazard = Counter(e["hazard_type"] for e in counted)
    by_hazard_dedup = Counter(e["hazard_type"] for e in deduplicated)
    by_continent_all = Counter(e["continent"] for e in events)
    by_continent = Counter(e["continent"] for e in counted)
    by_continent_dedup = Counter(e["continent"] for e in deduplicated)
    by_source = Counter("+".join(sorted(e["source_ids"])) for e in events)
    europe_non_wildfire = [e for e in counted if e["continent"] == "Europe" and e["hazard_type"] != "wildfire"]
    hazard_groups = sum(1 for n in by_hazard.values() if n >= bar["min_per_group"])
    continent_groups = sum(1 for name, n in by_continent.items() if name != "Unknown" and n >= bar["min_per_group"])
    criteria = [
        {
            "name": f"at least {bar['min_events']} events after excluding GDACS wildfires below Orange",
            "value": len(counted),
            "target": bar["min_events"],
            "passed": len(counted) >= bar["min_events"],
        },
        {
            "name": f"at least {bar['min_hazard_types']} hazard types with {bar['min_per_group']} or more events each",
            "value": hazard_groups,
            "target": bar["min_hazard_types"],
            "passed": hazard_groups >= bar["min_hazard_types"],
        },
        {
            "name": f"at least {bar['min_continents']} continents with {bar['min_per_group']} or more events each",
            "value": continent_groups,
            "target": bar["min_continents"],
            "passed": continent_groups >= bar["min_continents"],
        },
        {
            "name": f"at least {bar['min_europe_non_wildfire']} non-wildfire events in Europe",
            "value": len(europe_non_wildfire),
            "target": bar["min_europe_non_wildfire"],
            "passed": len(europe_non_wildfire) >= bar["min_europe_non_wildfire"],
        },
    ]
    return {
        "generated_at": to_iso(now),
        "since": since_iso,
        "days": days,
        "events_total": len(events),
        "events_counted": len(counted),
        "events_deduplicated": len(deduplicated),
        "excluded_gdacs_low_wildfires": len(events) - len(counted),
        "cross_source_mirrors": len(counted) - len(deduplicated),
        "by_hazard": {h: (by_hazard.get(h, 0), by_hazard_all.get(h, 0), by_hazard_dedup.get(h, 0)) for h in sorted(by_hazard_all)},
        "by_continent": {c: (by_continent.get(c, 0), by_continent_all.get(c, 0), by_continent_dedup.get(c, 0)) for c in sorted(by_continent_all)},
        "by_source": dict(sorted(by_source.items())),
        "europe_non_wildfire": sorted(
            ({"title": e["title"], "hazard_type": e["hazard_type"], "country_iso3": e["country_iso3"], "sources": "+".join(sorted(e["source_ids"]))} for e in europe_non_wildfire),
            key=lambda e: (e["hazard_type"], e["title"]),
        ),
        "unknown_continent": by_continent.get("Unknown", 0),
        "criteria": criteria,
        "passed": all(c["passed"] for c in criteria),
        "world_passed": all(c["passed"] for c in criteria[:3]),
        "europe_passed": criteria[3]["passed"],
    }


def render_markdown(result: dict) -> str:
    verdict = "PASS" if result["passed"] else "FAIL"
    lines = [
        "# M0 density check",
        "",
        f"Generated {result['generated_at']} by `eww report density --days {result['days']}`. "
        f"Window: events last observed at or after {result['since']} (canonical events with a centroid, "
        "the same definition `eww export` uses).",
        "",
        f"**Verdict: {verdict}** ({sum(c['passed'] for c in result['criteria'])} of {len(result['criteria'])} criteria met).",
        "",
        "## Totals",
        "",
        "| Measure | Events |",
        "|---|---:|",
        f"| Events observed in the window | {result['events_total']} |",
        f"| GDACS wildfires below Orange (excluded from every criterion) | {result['excluded_gdacs_low_wildfires']} |",
        f"| **Counted** (after the exclusion) | **{result['events_counted']}** |",
        f"| EONET events that mirror a GDACS event also present (M0 has no cross-source merging) | {result['cross_source_mirrors']} |",
        f"| Counted after also removing those mirrors | {result['events_deduplicated']} |",
        "",
        "Events by source combination (all events in the window): "
        + ", ".join(f"{k} {v}" for k, v in result["by_source"].items())
        + ".",
        "",
        "## Criteria",
        "",
        "| Criterion | Value | Target | Result |",
        "|---|---:|---:|---|",
    ]
    for c in result["criteria"]:
        lines.append(f"| {c['name']} | {c['value']} | {c['target']} | {'PASS' if c['passed'] else 'FAIL'} |")
    lines += [
        "",
        "## Events by hazard type",
        "",
        "| Hazard type | Counted | All in window | Counted, mirrors removed |",
        "|---|---:|---:|---:|",
    ]
    for hazard, (counted, total, dedup) in result["by_hazard"].items():
        lines.append(f"| {hazard} | {counted} | {total} | {dedup} |")
    lines += [
        "",
        "## Events by continent",
        "",
        "Continent from `event.country_iso3` via `eww/data/countries.csv` (GeoNames countryInfo.txt, CC BY 4.0). "
        "A country belongs to one continent, so an earthquake in Russia's Far East counts as Europe. "
        "`Unknown` means the feed gave no country and none could be read from the title: mostly storms over open ocean "
        "and EONET events whose title carries no country.",
        "",
        "| Continent | Counted | All in window | Counted, mirrors removed |",
        "|---|---:|---:|---:|",
    ]
    for continent, (counted, total, dedup) in result["by_continent"].items():
        lines.append(f"| {continent} | {counted} | {total} | {dedup} |")
    lines += [
        "",
        "## Non-wildfire events in Europe",
        "",
        f"{len(result['europe_non_wildfire'])} events.",
        "",
        "| Hazard type | Title | Country | Sources |",
        "|---|---|---|---|",
    ]
    for e in result["europe_non_wildfire"]:
        lines.append(f"| {e['hazard_type']} | {e['title']} | {e['country_iso3'] or ''} | {e['sources']} |")
    lines += [
        "",
        "## Decision rule (docs/architecture.md §4, M0)",
        "",
    ]
    if result["passed"]:
        lines.append("The world and Europe both pass: the spine is thick enough, M1 proceeds as planned.")
    elif result["world_passed"] and not result["europe_passed"]:
        lines.append("The world passes and Europe fails: Meteoalarm (CC BY 4.0) joins M2 as a warnings layer.")
    else:
        lines.append(
            "The world fails: the orphan-clustering hook in §3 moves into M3 and GDELT becomes a discovery source."
        )
    lines.append("")
    return "\n".join(lines)


def write_density_report(conn: sqlite3.Connection, days: int = 30, out: Path | None = None, now: datetime | None = None) -> tuple[Path, dict]:
    result = density(conn, days, now)
    path = out or config.milestone_doc(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(result), encoding="utf-8")
    return path, result


# ============================================================================= M1: data branch volume
def measure_volume(remote_url: str, branch: str = "data", now: datetime | None = None) -> dict:
    """Clone `branch` fresh into a temporary directory and measure it with `git count-objects -v`."""
    import tempfile

    from eww import gitdata

    now = now or now_utc()
    tmp = Path(tempfile.mkdtemp(prefix="eww-volume-"))
    clone = tmp / "clone"
    try:
        gitdata.clone_branch(remote_url, branch, clone)
        objects = gitdata.count_objects(clone)
        first_snapshot_at = gitdata.first_commit_at(clone, branch, "snapshots")
        first_commit_at = gitdata.first_commit_at(clone, branch)
        snapshot_files = [p for p in gitdata.list_files(branch, ("snapshots",), clone)]
        commits = gitdata.commit_count(clone, branch)
    finally:
        gitdata.rmtree(tmp)
    size_pack = objects.get("size-pack", 0)
    size_loose = objects.get("size", 0)
    start = first_snapshot_at or first_commit_at
    days = max((now - datetime.fromisoformat(start)).total_seconds() / 86400, 0.0) if start else 0.0
    per_day = (size_pack / 1e6) / days if days >= 0.5 else None
    limit_mb = config.VOLUME_PACK_LIMIT_MB
    if per_day is None:
        verdict = "PROVISIONAL (less than half a day of snapshots)"
    elif days < 3:
        verdict = f"PROVISIONAL (only {days:.1f} of the 3 days the exit criterion needs); on track" if size_pack / 1e6 * (3 / days) < limit_mb else f"PROVISIONAL; extrapolates above {limit_mb} MB at 3 days"
    else:
        verdict = "PASS" if size_pack / 1e6 < limit_mb else "FAIL"
    return {
        "generated_at": to_iso(now),
        "remote": remote_url,
        "branch": branch,
        "commits": commits,
        "snapshot_files": len(snapshot_files),
        "first_snapshot_at": first_snapshot_at,
        "first_commit_at": first_commit_at,
        "days": days,
        "size_pack_bytes": size_pack,
        "size_pack_mb": size_pack / 1e6,
        "size_loose_mb": size_loose / 1e6,
        "per_day_mb": round(per_day, 2) if per_day is not None else None,
        "per_year_mb": round(per_day * 365, 0) if per_day is not None else None,
        "limit_mb": limit_mb,
        "daily_limit_mb": config.VOLUME_DAILY_LIMIT_MB,
        "verdict": verdict,
    }


def render_volume_markdown(result: dict) -> str:
    per_day = "n/a" if result["per_day_mb"] is None else f"{result['per_day_mb']:.2f} MB"
    per_year = "n/a" if result["per_year_mb"] is None else f"{result['per_year_mb']:.0f} MB"
    lines = [
        "# M1 data-branch volume",
        "",
        f"Generated {result['generated_at']} by `eww report volume`: a fresh `git clone --branch {result['branch']} --single-branch` "
        f"of `{result['remote']}` measured with `git count-objects -vH`.",
        "",
        f"**Verdict: {result['verdict']}** (exit criterion 5: size-pack below {result['limit_mb']} MB after 3 days of collection).",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Commits on the branch | {result['commits']} |",
        f"| Snapshot files | {result['snapshot_files']} |",
        f"| First snapshot committed | {result['first_snapshot_at'] or 'none yet'} |",
        f"| Days of snapshots measured | {result['days']:.2f} |",
        f"| size-pack (fresh clone) | {result['size_pack_mb']:.2f} MB |",
        f"| loose objects | {result['size_loose_mb']:.2f} MB |",
        f"| Growth per day | {per_day} |",
        f"| Extrapolated per year | {per_year} |",
        "",
        f"Decision rule (docs/architecture.md §1): above {result['daily_limit_mb']} MB per day packed, the collector sink moves from the "
        "`data` branch to a Cloudflare R2 bucket. Re-run `uv run eww report volume` after three days of scheduled runs to replace "
        "a provisional verdict.",
        "",
    ]
    return "\n".join(lines)


def write_volume_report(remote_url: str, branch: str = "data", out: Path | None = None, now: datetime | None = None) -> tuple[Path, dict]:
    result = measure_volume(remote_url, branch, now)
    path = out or config.milestone_doc(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_volume_markdown(result), encoding="utf-8")
    return path, result


# ============================================================================= M2: identity report (docs/m2.md)
DISAGREEMENT_BUCKETS_KM = (5.0, 25.0, 50.0, 100.0, 250.0)
ID_RULES = ("key", "glide")


def _positions_by_source(conn: sqlite3.Connection, event_ids: list[str]) -> dict[str, dict[str, list[tuple[float, float]]]]:
    out: dict[str, dict[str, list[tuple[float, float]]]] = {e: {} for e in event_ids}
    for start in range(0, len(event_ids), 400):
        chunk = event_ids[start : start + 400]
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(f"SELECT event_id, source_id, lat, lon FROM source_record WHERE event_id IN ({marks})", chunk):
            sources = out[row["event_id"]].setdefault(row["source_id"], [])
            if row["lat"] is not None and row["lon"] is not None:
                sources.append((float(row["lat"]), float(row["lon"])))
    return out


def _closest_km(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float | None:
    from eww import geo

    if not a or not b:
        return None
    return min(geo.haversine_km(la, lo, lb, lob) for la, lo in a for lb, lob in b)


def _bucket(value: float) -> str:
    for edge in DISAGREEMENT_BUCKETS_KM:
        if value <= edge:
            return f"<= {edge:g} km"
    return f"> {DISAGREEMENT_BUCKETS_KM[-1]:g} km"


def identity_report(conn: sqlite3.Connection, days: int = 30, now: datetime | None = None) -> dict:
    """Measure what the identity rules did to the database: joins by rule, disagreement between feeds, proposals."""
    from eww import labels as labels_mod

    now = now or now_utc()
    since_iso = to_iso(now - timedelta(days=days))
    ident = config.IDENTITY
    records_by_source = {row[0]: row[1] for row in conn.execute("SELECT source_id, COUNT(*) FROM source_record GROUP BY 1 ORDER BY 1")}
    events_total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    events_merged = conn.execute("SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NOT NULL").fetchone()[0]
    window = conn.execute(f"SELECT e.event_id, e.title, e.hazard_type FROM event e WHERE {api.WINDOW_SQL}", {"since": since_iso, "until": None}).fetchall()
    positions = _positions_by_source(conn, [r["event_id"] for r in window])
    by_combination: Counter = Counter()
    buckets: Counter = Counter()
    widest: list[dict] = []
    multi_source = 0
    for row in window:
        sources = positions[row["event_id"]]
        by_combination["+".join(sorted(sources))] += 1
        if len(sources) < 2:
            continue
        multi_source += 1
        pairs = []
        names = sorted(sources)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                distance = _closest_km(sources[names[i]], sources[names[j]])
                if distance is not None:
                    pairs.append((names[i], names[j], round(distance, 1)))
        if not pairs:
            continue
        top = max(p[2] for p in pairs)
        buckets[_bucket(top)] += 1
        widest.append({"title": row["title"], "hazard_type": row["hazard_type"], "sources": names, "widest_km": top, "pairs": pairs})
    widest.sort(key=lambda w: -w["widest_km"])
    merges: dict[tuple[str, str], list[float | None]] = {}
    for row in conn.execute("SELECT performed_by, evidence FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NULL"):
        evidence = json.loads(row["evidence"]) if row["evidence"] else {}
        merges.setdefault((row["performed_by"], evidence.get("rule") or "?"), []).append(evidence.get("distance_km"))
    merge_rows = []
    for (performed_by, rule), distances in sorted(merges.items()):
        known = sorted(d for d in distances if d is not None)
        merge_rows.append({
            "performed_by": performed_by, "rule": rule, "count": len(distances),
            "min_km": known[0] if known else None, "median_km": known[len(known) // 2] if known else None, "max_km": known[-1] if known else None,
        })
    reverted = conn.execute("SELECT COUNT(*) FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NOT NULL").fetchone()[0]
    merged_targets = {row[0] for row in conn.execute("SELECT to_event_id FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NULL")}
    id_joined = sum(1 for row in window if len(positions[row["event_id"]]) >= 2 and row["event_id"] not in merged_targets)
    proposals_by_status = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM merge_proposal GROUP BY 1")}
    open_rows = conn.execute(
        """
        SELECT p.score, p.evidence, a.title AS title_a, b.title AS title_b, a.hazard_type AS hazard
        FROM merge_proposal p JOIN event a ON a.event_id = p.event_a JOIN event b ON b.event_id = p.event_b
        WHERE p.status = 'open' AND a.merged_into_event_id IS NULL AND b.merged_into_event_id IS NULL
        ORDER BY p.score DESC, p.created_at
        """
    ).fetchall()
    open_proposals = []
    for row in open_rows:
        evidence = json.loads(row["evidence"]) if row["evidence"] else {}
        open_proposals.append({
            "score": row["score"], "rule": evidence.get("rule") or "?", "distance_km": evidence.get("distance_km"), "days_apart": evidence.get("days_apart"),
            "title_a": row["title_a"], "title_b": row["title_b"], "hazard": row["hazard"], "source_a": evidence.get("source_a") or [], "source_b": evidence.get("source_b") or [],
            "keys": evidence.get("keys") or [], "within": evidence.get("within_aggregation_radius"), "radius_km": evidence.get("aggregation_radius_km"),
        })
    gated = [p for p in open_proposals if p["rule"] in ID_RULES or (p["score"] >= config.AUTO_MERGE_THRESHOLD and p["within"] is False)]
    labels_result = None
    if config.LABEL_PAIRS_CSV.exists():
        labels_result = labels_mod.evaluate(conn, labels_mod.read_pairs(config.LABEL_PAIRS_CSV))
    return {
        "generated_at": to_iso(now),
        "days": days,
        "since": since_iso,
        "database": str(config.DB_PATH),
        "identity": ident,
        "records_by_source": records_by_source,
        "events_total": events_total,
        "events_live": events_total - events_merged,
        "events_merged": events_merged,
        "events_in_window": len(window),
        "multi_source_in_window": multi_source,
        "by_combination": dict(sorted(by_combination.items())),
        "disagreement_buckets": {label: buckets.get(label, 0) for label in [f"<= {e:g} km" for e in DISAGREEMENT_BUCKETS_KM] + [f"> {DISAGREEMENT_BUCKETS_KM[-1]:g} km"]},
        "widest": widest[:10],
        "merges": merge_rows,
        "merges_reverted": reverted,
        "id_joined_in_window": id_joined,
        "proposals_by_status": proposals_by_status,
        "open_proposals": open_proposals,
        "gated": gated,
        "labels": labels_result,
        "copernicus_unresolved": conn.execute("SELECT COUNT(*) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NULL").fetchone()[0],
        "ems_events": conn.execute("SELECT COUNT(DISTINCT event_id) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NOT NULL").fetchone()[0],
    }


def render_identity_markdown(result: dict) -> str:
    ident = result["identity"]
    radius = ident["aggregation_radius_km"]

    def km(value):
        return "n/a" if value is None else f"{value:g} km"

    lines = [
        "# M2: one event, one pin",
        "",
        f"Generated {result['generated_at']} by `eww report identity --days {result['days']}` against `{result['database']}`; parameters from `{ident['path']}`. "
        "Re-run after editing identity.yaml and rebuilding the database to see the effect of a change.",
        "",
        "## What the rules are",
        "",
        "A record joins an existing pin by the feed's own id (a new GDACS episode, a new EONET track point), by GLIDE number, or by a "
        "deterministic key one feed states about another (Copernicus `gdacsId`, the GDACS report URL EONET carries); otherwise it is scored "
        "against the live events of other feeds inside its hazard class, blocking radius and time window. **Two feeds' records share a pin "
        "automatically only when their positions lie within the aggregation radius** (closest pair of positions); beyond it the pipeline keeps "
        "two pins and writes a merge proposal, whatever the key or the score says. Every automatic merge is an `event_lineage` row and can be "
        "reverted in the Review tab; a person's accept is not gated.",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| Aggregation radius | default {radius['default']:g} km" + "".join(f"; {h} {v:g} km" for h, v in radius.items() if h != "default") + " |",
        "| Blocking radius / window | " + ", ".join(f"{h} {v[0]:g} km / {v[1]:g} d" for h, v in ident["blocking"].items()) + f"; named storms {ident['named_storm_km']:g} km |",
        f"| Thresholds | auto-merge {ident['auto_merge']:g}, proposal {ident['proposal']:g} |",
        f"| Score | weights spatial {ident['weights']['spatial']:g}, temporal {ident['weights']['temporal']:g}, text {ident['weights']['text']:g}; key {ident['key_equal']:g}, GLIDE {ident['glide_equal']:g}, storm name {ident['storm_name_equal']:g} |",
        "",
        "## Records and events",
        "",
        "| Measure | Value |",
        "|---|---:|",
    ]
    for source_id, n in result["records_by_source"].items():
        lines.append(f"| `source_record` rows from {source_id} | {n} |")
    lines += [
        f"| Events (rows) | {result['events_total']} |",
        f"| Live events | {result['events_live']} |",
        f"| Events merged into another (pointer set, row kept) | {result['events_merged']} |",
        f"| Live events in the last {result['days']} days | {result['events_in_window']} |",
        f"| Of which with two or more sources | {result['multi_source_in_window']} |",
        f"| Of which joined by id or GLIDE alone (no lineage row) | {result['id_joined_in_window']} |",
        f"| Events with a Copernicus EMS activation | {result['ems_events']} |",
        f"| Copernicus records without an event (must be 0) | {result['copernicus_unresolved']} |",
        "",
        "Live events in the window by source combination: " + ", ".join(f"{k} {v}" for k, v in result["by_combination"].items()) + ".",
        "",
        "## How far apart the feeds place one event",
        "",
        "For every live multi-source event in the window: the widest gap between two of its sources (closest pair of their positions).",
        "",
        "| Widest gap | Events |",
        "|---|---:|",
    ]
    for label, n in result["disagreement_buckets"].items():
        lines.append(f"| {label} | {n} |")
    lines += ["", "| Event | Hazard | Sources | Widest gap | Pairs |", "|---|---|---|---:|---|"]
    for w in result["widest"]:
        pairs = ", ".join(f"{a}~{b} {d:g} km" for a, b, d in w["pairs"])
        lines.append(f"| {w['title']} | {w['hazard_type']} | {', '.join(w['sources'])} | {w['widest_km']:g} km | {pairs} |")
    lines += ["", "## Automatic merges (event_lineage)", "", "| Performed by | Rule | Merges | Distance min / median / max |", "|---|---|---:|---|"]
    for m in result["merges"]:
        lines.append(f"| {m['performed_by']} | {m['rule']} | {m['count']} | {km(m['min_km'])} / {km(m['median_km'])} / {km(m['max_km'])} |")
    if not result["merges"]:
        lines.append("| (none) | | | |")
    lines += ["", f"Merges reverted: {result['merges_reverted']}.", "", "## Proposals", ""]
    lines.append("By status: " + (", ".join(f"{k} {v}" for k, v in sorted(result["proposals_by_status"].items())) or "none") + ".")
    lines += [
        "",
        f"**Kept apart by the aggregation radius: {len(result['gated'])}** (an id or a score said one event; the positions said otherwise).",
        "",
        "| Score | Rule | Gap | Days apart | A | B | Note |",
        "|---:|---|---:|---:|---|---|---|",
    ]
    for p in result["open_proposals"][:25]:
        cited = ", ".join(f"{s} {e}" for s, e in p["keys"])
        note = f"cites {cited}; beyond {p['radius_km']:g} km" if p["keys"] else ("beyond the aggregation radius" if p["within"] is False else "grey zone")
        lines.append(
            f"| {p['score']:.2f} | {p['rule']} | {km(p['distance_km'])} | {p['days_apart'] if p['days_apart'] is not None else 'n/a'} | "
            f"{p['title_a']} ({'+'.join(p['source_a'])}) | {p['title_b']} ({'+'.join(p['source_b'])}) | {note} |"
        )
    if len(result["open_proposals"]) > 25:
        lines.append(f"| … | | | | {len(result['open_proposals']) - 25} more | | |")
    lines += ["", "## Hand labels (`data/labels/merge_pairs.csv`)", ""]
    labels_result = result["labels"]
    if labels_result is None:
        lines.append("No labels file yet: run `eww labels candidates`, copy the file to merge_pairs.csv and fill `same_event`.")
    else:
        from eww import labels as labels_mod

        lines += ["```", labels_mod.render_evaluation(labels_result), "```"]
    lines += [
        "",
        "## How to tune",
        "",
        "1. Edit `identity.yaml` (a value per hazard under `aggregation.radius_km`, or the blocking, thresholds and weights).",
        "2. Rebuild the database from the snapshots and swap it in: `uv run python .cursor/skills/playbook/files/rebuild-database.py --swap`.",
        "3. Re-run `uv run eww eval merges` against the hand labels and `uv run eww report identity` for this report.",
        "",
    ]
    return "\n".join(lines)


def write_identity_report(conn: sqlite3.Connection, out: Path | None = None, days: int = 30, now: datetime | None = None) -> tuple[Path, dict]:
    result = identity_report(conn, days, now)
    path = out or config.milestone_doc(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_identity_markdown(result), encoding="utf-8")
    return path, result


# ============================================================================= M3: attachment evaluation (docs/m3.md)
def attachment_report(conn: sqlite3.Connection, evaluation: dict | None, *, now: datetime | None = None) -> dict:
    """What M3 did: coverage, decisions, extraction and geocoding counts, the labelled precision and its errors."""
    from eww import attach, documents, geocode, ratelimit
    from eww import enrich as enrich_mod

    now = now or now_utc()
    since = now - timedelta(days=config.DOCTOR_LOG_DAYS)
    runs = [dict(r) for r in conn.execute(
        "SELECT source_id, COUNT(*) AS events, MAX(queried_at) AS last_run, COALESCE(SUM(documents_new), 0) AS documents "
        "FROM enrichment_run GROUP BY 1 ORDER BY 1"
    )]
    last_call: dict[str, dict] = {}
    for record in ratelimit.read(since, kind="call"):
        last_call[str(record.get("provider"))] = {"at": record.get("at"), "status": record.get("status")}
    providers = []
    for source_id, module in enrich_mod.modules().items():
        ok, reason = module.available(conn)
        row = next((r for r in runs if r["source_id"] == source_id), None)
        providers.append({
            "source_id": source_id,
            "configured": ok,
            "reason": reason,
            "events": (row or {}).get("events", 0),
            "documents": (row or {}).get("documents", 0),
            "last_run": (row or {}).get("last_run"),
            "last_call": last_call.get(source_id),
        })
    return {
        "providers": providers,
        "generated_at": to_iso(now),
        "database": str(config.DB_PATH),
        "identity_path": config.IDENTITY["path"],
        "attachment": config.ATTACHMENT,
        "documents": documents.stats(conn),
        "coverage": attach.coverage(conn, min_severity=config.COVERAGE_MIN_SEVERITY, days=config.ENRICH_ACTIVE_DAYS, min_documents=config.COVERAGE_MIN_DOCUMENTS, now=now),
        "gazetteer_rows": geocode.gazetteer_count(conn),
        "geocode_cache": geocode.cache_stats(conn),
        "geocode_log": ratelimit.geocode_report(since),
        "limits": ratelimit.limits_report(since),
        "log_since": to_iso(since),
        "evaluation": evaluation,
        "sample_path": str(config.ATTACHMENT_SAMPLE_CSV),
    }


def render_attachment_markdown(result: dict) -> str:
    from eww import labels as labels_mod

    att = result["attachment"]
    cov = result["coverage"]
    docs = result["documents"]
    share = "n/a" if cov["share"] is None else f"{100 * cov['share']:.0f}%"
    lines = [
        "# M3: headlines on pins",
        "",
        f"Generated {result['generated_at']} by `eww eval attachments` against `{result['database']}`; attachment numbers from `{result['identity_path']}`.",
        "",
        "## What the rules are",
        "",
        "A document is scored against the live events of its hazard class whose window overlaps its own and that a resolved place "
        "puts within twice the hazard's blocking radius, or that share its country, or whose own query fetched it. "
        f"s = {att['weights']['spatial']:g} spatial + {att['weights']['temporal']:g} temporal + {att['weights']['text']:g} text "
        f"(no resolved place: {att['no_place_weights']['temporal']:g} temporal + {att['no_place_weights']['text']:g} text, text at least {att['no_place_min_text']:g}), "
        f"+{att['query_prior']:g} when the event's query fetched the document; at or above {att['attach_threshold']:g} attached, "
        f"at or above {att['candidate_threshold']:g} a candidate for the Review tab, below that nothing.",
        "",
        "## Enrichment",
        "",
        "| Provider | Events queried | Documents | Last run | State |",
        "|---|---:|---:|---|---|",
    ]
    for provider in result["providers"]:
        call = provider["last_call"]
        if not provider["configured"]:
            state = f"not configured: {provider['reason']}"
        elif call and call.get("status") == 429:
            state = f"refused with HTTP 429, last tried {call['at']}"
        elif call:
            state = f"last call {call['at']} answered {call['status']}"
        else:
            state = "configured, never called"
        lines.append(f"| {provider['source_id']} | {provider['events']} | {provider['documents']} | {provider['last_run'] or 'never'} | {state} |")
    if docs["documents"] == 0:
        lines += [
            "",
            "**No documents have been collected, so every number below is zero and the exit criteria cannot be judged yet.** "
            "The pipeline itself is exercised by the test suite; what is missing is real input from the enrichment providers. "
            "Re-run `uv run eww sync` once a provider answers, then `uv run eww eval attachments` to refresh this record.",
        ]
    lines += [
        "",
        "## Coverage (exit criterion 1)",
        "",
        f"**{cov['covered']} of {cov['events']} events with severity >= {cov['min_severity']:g} active in the last {config.ENRICH_ACTIVE_DAYS} days have >= {cov['min_documents']} attached documents ({share}; target {100 * config.COVERAGE_TARGET:.0f}%).**",
        "",
        "| Event | Hazard | Severity | Country | Attached | Candidates | Queried |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for row in cov["rows"]:
        lines.append(f"| {row['title']} | {row['hazard_type']} | {row['severity_score']:.3f} | {row['country_iso3'] or ''} | {row['attached']} | {row['candidates']} | {row['queried']} |")
    lines += [
        "",
        "## Documents",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Documents | {docs['documents']} |",
    ]
    for key, n in docs["by_source_kind"].items():
        lines.append(f"| ... from {key} | {n} |")
    lines += [
        f"| Longest text_excerpt (limit {config.EXCERPT_MAX_CHARS}) | {docs['longest_excerpt']} |",
        f"| With a thumbnail reference | {docs['with_media']} |",
        f"| Extracted / classified by the lexicon / with a located place | {docs['extracted']} / {docs['classified']} / {docs['located']} |",
        f"| Embedded | {docs['embedded']} |",
        f"| Decisions: attached / candidate / rejected | {docs['decisions'].get('attached', 0)} / {docs['decisions'].get('candidate', 0)} / {docs['decisions'].get('rejected', 0)} |",
        f"| Decided by | {', '.join(f'{k} {v}' for k, v in docs['decided_by'].items()) or 'none'} |",
        f"| Mention geometries | {docs['mentions']} |",
        f"| Unattached documents (purged after {config.PURGE_UNATTACHED_DAYS} days) | {docs['unattached']} |",
        "",
        "## Geocoding and rate limits",
        "",
        f"Gazetteer rows: {result['gazetteer_rows']}. geocode_cache entries: " + (", ".join(f"{p} {v['entries']} ({v['found']} found)" for p, v in result["geocode_cache"].items()) or "none") + ".",
        "",
    ]
    geo_log = result["geocode_log"]
    rate = "n/a" if geo_log["hit_rate"] is None else f"{100 * geo_log['hit_rate']:.0f}%"
    lines.append(f"Geocode lookups logged since {result['log_since']}: {geo_log['lookups']}, answered from the cache: {geo_log['cache_hits']} ({rate}; exit criterion 4 asks for >= {100 * config.CACHE_HIT_RATE_TARGET:.0f}% in the second week).")
    lines += ["", "| Provider | Calls | Max in a minute | Max in an hour | Max in a day | Min spacing | User-Agent carries the contact |", "|---|---:|---:|---:|---:|---:|---|"]
    for provider, r in result["limits"].items():
        spacing = "n/a" if r["min_spacing_s"] is None else f"{r['min_spacing_s']:.1f} s"
        lines.append(f"| {provider} | {r['calls']} | {r['max_per_minute']} | {r['max_per_hour']} | {r['max_per_day']} | {spacing} | {'yes' if r['user_agent_ok'] else 'NO'} |")
    if not result["limits"]:
        lines.append("| (no calls logged) | | | | | | |")
    lines += ["", "## Hand-checked attachments (exit criterion 2)", ""]
    evaluation = result["evaluation"]
    if evaluation is None:
        lines.append(f"No labelled sample yet: run `uv run eww eval attachments --sample 100`, fill `correct` (yes/no) and `cause` in `{result['sample_path']}`, then run `uv run eww eval attachments` again.")
    else:
        lines += ["```", labels_mod.render_attachment_evaluation(evaluation), "```", ""]
        if evaluation["wrong"]:
            lines += ["| Event | Headline | Publisher | Score | Cause |", "|---|---|---|---:|---|"]
            for row in evaluation["wrong"]:
                score = row.get("score") or ""
                try:
                    score = f"{float(score):.2f}"
                except (TypeError, ValueError):
                    pass
                headline = (row.get("title") or "").replace("|", "/").replace("[", "(").replace("]", ")")
                lines.append(f"| {row.get('event_title', '')} | [{headline}]({row.get('url', '')}) | {row.get('publisher', '')} | {score} | {row.get('cause', '')} |")
    lines += [
        "",
        "## How to tune",
        "",
        "1. Edit the `attachment:` section of `identity.yaml` (weights, thresholds, windows) or `eww/data/lexicon.yaml` (terms and negative patterns).",
        "2. Re-decide every pipeline row on a copy of the database: copy `data/eww.sqlite` to `data/copy.sqlite`, then `uv run eww --db data/copy.sqlite attach --rebuild`.",
        "3. Re-run `uv run eww eval attachments --sample 100`, label, and `uv run eww eval attachments` for this report.",
        "",
    ]
    return "\n".join(lines)


def write_attachment_report(conn: sqlite3.Connection, evaluation: dict | None, out: Path | None = None, now: datetime | None = None) -> tuple[Path, dict]:
    result = attachment_report(conn, evaluation, now=now)
    path = out or config.milestone_doc(3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_attachment_markdown(result), encoding="utf-8")
    return path, result


# ============================================================================= M5: posts, videos, weather (docs/m5.md)
_SAMPLE_COLUMNS = ("document_id", "event_id", "event_title", "source_id", "author", "text", "url", "relevant", "cause")


def social_coverage(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """Events active in the last SOCIAL_COVERAGE_DAYS with at least one attached post or video from a free collector."""
    now = now or now_utc()
    since = to_iso(now - timedelta(days=config.SOCIAL_COVERAGE_DAYS))
    marks = ", ".join("?" * len(config.SOCIAL_SOURCES))
    rows = conn.execute(
        f"""
        SELECT e.event_id, e.title, e.hazard_type, e.country_iso3, COALESCE(e.severity_score, 0) AS severity_score,
               (
                 SELECT COUNT(*) FROM event_document ed
                 JOIN document d ON d.document_id = ed.document_id
                 WHERE ed.event_id = e.event_id AND ed.status = 'attached' AND d.removed_at IS NULL
                   AND d.kind IN ('post', 'video') AND d.source_id IN ({marks})
               ) AS attached
        FROM event e
        WHERE e.merged_into_event_id IS NULL AND e.status IN ('active', 'ended')
          AND COALESCE(e.ended_at, e.last_observed_at) >= ?
        ORDER BY attached DESC, severity_score DESC, e.event_id
        """,
        (*config.SOCIAL_SOURCES, since),
    ).fetchall()
    covered = sum(1 for row in rows if row["attached"] >= 1)
    return {
        "events": len(rows),
        "covered": covered,
        "share": (covered / len(rows)) if rows else None,
        "since": since,
        "rows": [dict(row) for row in rows if row["attached"] >= 1],
    }


def youtube_searches_by_day(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Searches per UTC day: collector_run.items_seen for source youtube, and search.list lines in the provider log."""
    days: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        """
        SELECT substr(started_at, 1, 10) AS day, COALESCE(SUM(items_seen), 0) AS n
        FROM collector_run WHERE source_id = 'youtube' GROUP BY 1
        """
    ):
        days.setdefault(row["day"], {"collector_run": 0, "log": 0})["collector_run"] = int(row["n"])
    for record in ratelimit_calls():
        url = str(record.get("url") or "")
        if record.get("provider") != "youtube" or "/youtube/v3/search" not in url:
            continue
        day = str(record.get("at") or "")[:10]
        if not day:
            continue
        days.setdefault(day, {"collector_run": 0, "log": 0})["log"] += 1
    return dict(sorted(days.items()))


def bluesky_min_spacing() -> float | None:
    """Smallest gap between Bluesky calls in the provider log. None when fewer than two calls were logged."""
    from eww import ratelimit

    records = [record for record in ratelimit.read(kind="call") if record.get("provider") == "bluesky"]
    return ratelimit.min_spacing_s(records)


def media_files(root: Path | None = None) -> list[Path]:
    """Image and video files under data/. References belong in media_url; bytes do not belong on disk."""
    folder = root or config.DATA_DIR
    if not folder.exists():
        return []
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in config.MEDIA_SUFFIXES)


def ratelimit_calls():
    from eww import ratelimit

    return ratelimit.read(kind="call")


def _prior_sample(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["document_id"]: row for row in csv.DictReader(handle) if row.get("document_id")}


def social_sample(conn: sqlite3.Connection, *, size: int | None = None, seed: int = 5, path: Path | None = None) -> list[dict]:
    """Up to `size` attached posts, drawn with a fixed seed. A hand-filled relevant/cause cell is kept."""
    size = config.SOCIAL_SAMPLE_SIZE if size is None else size
    rows = conn.execute(
        """
        SELECT d.document_id, ed.event_id, e.title AS event_title, d.source_id, d.author,
               COALESCE(d.text_excerpt, d.title, '') AS text, d.url
        FROM event_document ed
        JOIN document d ON d.document_id = ed.document_id
        JOIN event e ON e.event_id = ed.event_id
        WHERE ed.status = 'attached' AND d.removed_at IS NULL AND d.kind = 'post'
          AND d.source_id IN ({marks})
        ORDER BY d.document_id
        """.format(marks=", ".join("?" * len(config.SOCIAL_SOURCES))),
        config.SOCIAL_SOURCES,
    ).fetchall()
    candidates = [dict(row) for row in rows]
    prior = _prior_sample(path or config.SOCIAL_SAMPLE_CSV)
    # Rows the owner already labelled stay in the sample while they are still attached; the seeded draw
    # only fills the remaining places, so a re-run after new posts arrive never discards a label.
    labelled = [row for row in candidates if (prior.get(row["document_id"]) or {}).get("relevant")][:size]
    kept = {row["document_id"] for row in labelled}
    rest = [row for row in candidates if row["document_id"] not in kept]
    room = size - len(labelled)
    if len(rest) > room:
        rest = random.Random(seed).sample(rest, room)
    chosen = labelled + rest
    for row in chosen:
        earlier = prior.get(row["document_id"]) or {}
        row["relevant"] = earlier.get("relevant") or ""
        row["cause"] = earlier.get("cause") or ""
        row["text"] = (row.get("text") or "").replace("\n", " ")
    chosen.sort(key=lambda row: (row["event_title"] or "", row["document_id"]))
    return chosen


def write_social_sample(rows: list[dict], path: Path | None = None) -> Path:
    path = path or config.SOCIAL_SAMPLE_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SAMPLE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in _SAMPLE_COLUMNS})
    return path


def evaluate_social_sample(rows: list[dict]) -> dict:
    labelled = [row for row in rows if labels_mod.parse_label(row.get("relevant")) is not None]
    relevant = [row for row in labelled if labels_mod.parse_label(row.get("relevant"))]
    return {
        "rows": len(rows),
        "labelled": len(labelled),
        "relevant": len(relevant),
        "target_rows": config.SOCIAL_SAMPLE_SIZE,
        "target_relevant": int(config.SOCIAL_SAMPLE_SIZE * config.SOCIAL_RELEVANCE_TARGET),
    }


def skipped_sources() -> list[str]:
    """Collectors that will not run, each as the one line the run itself logs. A skip is not a reason to pay."""
    lines = []
    if not config.BLUESKY_HANDLE or not config.BLUESKY_APP_PASSWORD:
        lines.append("bluesky: BLUESKY_HANDLE or BLUESKY_APP_PASSWORD is not set")
    if not config.YOUTUBE_API_KEY:
        lines.append("youtube: YOUTUBE_API_KEY is not set; no billing account will be attached")
    if not config.REDDIT_ENABLED:
        lines.append("reddit: EWW_REDDIT_ENABLED is not set")
    return lines


def weather_probe(latitude: float = 28.2, longitude: float = 85.3) -> dict:
    """One live forecast, timed. The report records the number; a failure is written, not hidden."""
    started = time.monotonic()
    try:
        body = weather_mod.forecast(latitude, longitude)
    except Exception as exc:
        return {"ok": False, "elapsed_s": round(time.monotonic() - started, 3), "error": f"{type(exc).__name__}: {exc}"[:200], "rows": 0}
    return {
        "ok": True,
        "elapsed_s": round(time.monotonic() - started, 3),
        "rows": len(body["daily"]),
        "temperature_c": body["current"]["temperature_c"],
        "attribution": body["attribution"],
        "error": None,
    }


def social_report(conn: sqlite3.Connection, *, now: datetime | None = None, probe: dict | None = None, sample_path: Path | None = None) -> dict:
    now = now or now_utc()
    coverage = social_coverage(conn, now=now)
    searches = youtube_searches_by_day(conn)
    busiest = max((max(row["collector_run"], row["log"]) for row in searches.values()), default=0)
    spacing = bluesky_min_spacing()
    media = media_files()
    sample_rows = social_sample(conn, path=sample_path)
    sample = evaluate_social_sample(sample_rows)
    outlook = probe if probe is not None else weather_probe()
    relevance_passed = sample["labelled"] >= config.SOCIAL_SAMPLE_SIZE and sample["relevant"] >= sample["target_relevant"]
    return {
        "generated_at": to_iso(now),
        "database": str(config.DB_PATH),
        "coverage": coverage,
        "coverage_passed": coverage["share"] is not None and coverage["share"] >= config.SOCIAL_COVERAGE_TARGET,
        "youtube_days": searches,
        "youtube_busiest": busiest,
        "youtube_passed": busiest <= config.YOUTUBE_SEARCHES_PER_DAY,
        "bluesky_min_spacing_s": spacing,
        "bluesky_passed": spacing is None or spacing + 1e-9 >= config.BLUESKY_MIN_INTERVAL_S,
        "media_files": len(media),
        "media_passed": len(media) == 0,
        "sample_rows": sample_rows,
        "sample": sample,
        "relevance_passed": relevance_passed,
        "weather": outlook,
        "weather_passed": bool(outlook.get("ok") and outlook.get("elapsed_s", 99) <= 3 and outlook.get("rows") == config.OPEN_METEO_FORECAST_DAYS and outlook.get("attribution") == config.OPEN_METEO_ATTRIBUTION),
        "skipped": skipped_sources(),
    }


def render_social_markdown(result: dict) -> str:
    cov = result["coverage"]
    share = "n/a" if cov["share"] is None else f"{100 * cov['share']:.1f}%"
    sample = result["sample"]
    spacing = "n/a" if result["bluesky_min_spacing_s"] is None else f"{result['bluesky_min_spacing_s']:.3f} s"
    lines = [
        "# M5: posts, videos and weather on click",
        "",
        f"Generated {result['generated_at']} by `eww report social` against `{result['database']}`.",
        "Re-run that command to refresh the numbers. A collector that was skipped because it would cost money is listed and does not fail the coverage bar.",
        "",
        "## Exit criteria",
        "",
        "| Criterion | Measured | Target | Verdict |",
        "|---|---|---|---|",
        f"| Events active in {config.SOCIAL_COVERAGE_DAYS} days with >= 1 attached post or video | {cov['covered']} of {cov['events']} ({share}) | >= {100 * config.SOCIAL_COVERAGE_TARGET:.0f}% | {'PASS' if result['coverage_passed'] else 'FAIL'} |",
        f"| Hand-checked attached posts that are relevant | {sample['relevant']} of {sample['labelled']} labelled ({sample['rows']} in the sample) | >= {sample['target_relevant']} of {sample['target_rows']} | {'PASS' if result['relevance_passed'] else 'FAIL'} |",
        f"| YouTube searches in the busiest UTC day | {result['youtube_busiest']} | <= {config.YOUTUBE_SEARCHES_PER_DAY} | {'PASS' if result['youtube_passed'] else 'FAIL'} |",
        f"| Bluesky minimum spacing | {spacing} | >= {config.BLUESKY_MIN_INTERVAL_S:.0f} s | {'PASS' if result['bluesky_passed'] else 'FAIL'} |",
        f"| Image or video files under data/ | {result['media_files']} | 0 | {'PASS' if result['media_passed'] else 'FAIL'} |",
        f"| Forecast for one pin (28.2 N, 85.3 E) | {_weather_cell(result['weather'])} | <= 3 s, 5 days | {'PASS' if result['weather_passed'] else 'FAIL'} |",
        "",
        "Deleting a test post on Bluesky and seeing `removed_at` after the next `eww sync` is checked by the owner when Bluesky credentials are set. The sweep runs on every sync; the unit tests cover a post the server no longer returns.",
        "",
        "## Collectors that did not run",
        "",
    ]
    if result["skipped"]:
        for line in result["skipped"]:
            lines.append(f"- {line}")
    else:
        lines.append("- none")
    lines += [
        "",
        "## YouTube searches by UTC day",
        "",
        "| Day | collector_run items_seen | search.list lines in the log |",
        "|---|---:|---:|",
    ]
    if result["youtube_days"]:
        for day, row in result["youtube_days"].items():
            lines.append(f"| {day} | {row['collector_run']} | {row['log']} |")
    else:
        lines.append("| (none) | 0 | 0 |")
    lines += ["", "## Events with an attached post or video", ""]
    if cov["rows"]:
        lines += ["| Event | Hazard | Country | Severity | Attached |", "|---|---|---|---:|---:|"]
        for row in cov["rows"][:40]:
            lines.append(f"| {row['title']} | {row['hazard_type']} | {row['country_iso3'] or ''} | {row['severity_score']:.3f} | {row['attached']} |")
        if len(cov["rows"]) > 40:
            lines.append(f"| … {len(cov['rows']) - 40} more | | | | |")
    else:
        lines.append("None.")
    lines += [
        "",
        "## Hand check",
        "",
        f"The sample is `{config.SOCIAL_SAMPLE_CSV}`. Fill `relevant` (yes/no) and `cause`, then run `uv run eww report social` again. Labels already in that file are kept.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _weather_cell(probe: dict) -> str:
    if not probe.get("ok"):
        return probe.get("error") or "failed"
    return f"{probe['elapsed_s']:.3f} s, {probe['rows']} days, {probe.get('temperature_c')} °C"


def write_social_report(conn: sqlite3.Connection, out: Path | None = None, *, now: datetime | None = None, probe: dict | None = None, sample_path: Path | None = None) -> tuple[Path, dict]:
    result = social_report(conn, now=now, probe=probe, sample_path=sample_path)
    sample_path = sample_path or config.SOCIAL_SAMPLE_CSV
    write_social_sample(result["sample_rows"], sample_path)
    path = out or config.milestone_doc(5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_social_markdown(result), encoding="utf-8")
    return path, result
