"""`eww report density`: the M0 density check written to docs/m0-density.md.

Counts the events observed in the last N days by hazard type and by continent (via
eww/data/countries.csv), counts non-wildfire events in Europe, and judges them against the
bar pre-declared in docs/architecture.md §4. The bar excludes GDACS wildfires below Orange;
that exclusion is applied to every criterion, and the unfiltered numbers are shown next to
the filtered ones. Because M0 has no cross-source merging, a GDACS flood that EONET mirrors
is two events; the report also gives the total after removing those mirrors.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from eww import api, config, countries
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
    path = out or (config.DOCS_DIR / "m0-density.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(result), encoding="utf-8")
    return path, result
