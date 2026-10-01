"""`eww report frontend`: the M7 exit criteria, measured and written to docs/m7.md (config.milestone_doc(7)).

It reads web/dist and web/audit.json, compares pin counts across events_geojson(), `eww export --count`
and GET /events.geojson (in-process through create_app), and times one /forecast call, which needs the
network. What only a person can check (the map's own pin count, reloading a URL) is printed as a step.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import sqlite3
import subprocess
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

from eww import api, config
from eww.clock import now_utc, to_iso

PASS, FAIL, BY_HAND, SKIPPED = "PASS", "FAIL", "CHECK BY HAND", "SKIPPED"
NEWS_KINDS = {"article", "report"}  # the News tab; every other kind goes to Posts, as app.py splits them
TS_ERROR_RE = re.compile(r"error TS\d+")


# --------------------------------------------------------------------------- 1. build and bundle size
def _chunk_refs(html: str) -> list[str]:
    """JavaScript files index.html loads: <script src> and <link rel=modulepreload href>."""
    return [ref for ref in re.findall(r"""(?:src|href)=["']([^"']+\.js)["']""", html) if "://" not in ref]


def first_load_js(dist: Path) -> dict:
    """Gzipped size of the JavaScript the default view loads: index.html's chunks and every chunk they import."""
    scripts = {path.name: path for path in dist.rglob("*.js")}
    index = dist / "index.html"
    entries = [Path(ref).name for ref in _chunk_refs(index.read_text(encoding="utf-8"))] if index.exists() else []
    entries = [name for name in entries if name in scripts]
    if entries:
        # Lazy chunks are followed too: the map library is lazy-loaded but the default view always needs it.
        seen: set[str] = set()
        pending = list(entries)
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            text = scripts[name].read_text(encoding="utf-8", errors="replace")
            pending += [other for other in scripts if other not in seen and other in text]
        method = "index.html's chunks and every chunk they import, lazy ones included"
    else:
        seen = set(scripts)
        method = "every JavaScript file in web/dist (index.html names none, so this is an upper bound)"
    chunks = sorted(({"name": name, "gzip_bytes": len(gzip.compress(scripts[name].read_bytes(), compresslevel=9))} for name in seen), key=lambda c: -c["gzip_bytes"])
    return {"chunks": chunks, "gzip_bytes": sum(c["gzip_bytes"] for c in chunks), "method": method}


def run_build(web_dir: Path) -> dict:
    """Run `npm run build` in web/ and time it; a non-zero exit or a TypeScript error is a failed build."""
    npm = shutil.which("npm")
    if npm is None:
        return {"ran": True, "ok": False, "seconds": None, "ts_errors": None, "error": "npm is not on PATH"}
    started = time.monotonic()
    proc = subprocess.run([npm, "run", "build"], cwd=web_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    output = proc.stdout + proc.stderr
    ts_errors = len(TS_ERROR_RE.findall(output))
    tail = "; ".join(line.strip() for line in output.strip().splitlines()[-3:])
    ok = proc.returncode == 0 and ts_errors == 0
    return {"ran": True, "ok": ok, "seconds": round(time.monotonic() - started, 1), "ts_errors": ts_errors, "error": None if ok else f"exit {proc.returncode}: {tail}"[:300]}


def build_check(web_dir: Path, build: bool = False) -> dict:
    """Whether web/dist exists (the build runs tsc first, so a dist means zero TypeScript errors) and its size."""
    result = run_build(web_dir) if build else {"ran": False, "ok": None, "seconds": None, "ts_errors": None, "error": None}
    dist = web_dir / "dist"
    if not (dist / "index.html").exists():
        return {**result, "built": False, "built_at": None, "bundle": None, "passed": False}
    built_at = to_iso(datetime.fromtimestamp((dist / "index.html").stat().st_mtime, tz=UTC))
    bundle = first_load_js(dist)
    passed = result["ok"] is not False and bundle["gzip_bytes"] <= config.FRONTEND_FIRST_LOAD_JS_KB * 1024
    return {**result, "built": True, "built_at": built_at, "bundle": bundle, "passed": passed}


# --------------------------------------------------------------------------- 2. parity
def _points(collection: dict) -> int:
    return sum(1 for feature in collection["features"] if feature["geometry"]["type"] == "Point")


def export_count(db_path: Path | None, view: dict) -> int:
    """The pin count `eww export --count` prints for `view`."""
    from typer.testing import CliRunner

    from eww.cli import app  # here, not at the top: eww.cli imports this module

    args = [*(["--db", str(db_path)] if db_path else []), "export", "--count", "--limit", "0", "--since", view["since"]]
    args += [arg for hazard in view.get("hazard", []) for arg in ("--hazard", hazard)]
    args += ["--min-severity", str(view.get("min_severity", 0.0))]
    result = CliRunner().invoke(app, args)
    if result.exit_code != 0:
        raise RuntimeError(f"eww export failed: {result.output.strip()[-200:]}")
    return int(result.stdout.strip().splitlines()[-1])


def parity(conn: sqlite3.Connection, client, db_path: Path | None, name: str, view: dict, map_count: int | None) -> dict:
    """One view's pin count from events_geojson(), `eww export --count` and GET /events.geojson?limit=0."""
    from_api = _points(api.events_geojson(view["since"], hazard=view.get("hazard"), min_severity=view.get("min_severity", 0.0), limit=0, conn=conn))
    response = client.get("/events.geojson", params={**view, "limit": 0})
    response.raise_for_status()
    from_http = _points(response.json())
    from_export = export_count(db_path, view)
    agree = from_api == from_http == from_export
    if not agree or (map_count is not None and map_count != from_api):
        verdict = FAIL
    else:
        verdict = PASS if map_count is not None else BY_HAND
    return {"name": name, "view": view, "events_geojson": from_api, "http": from_http, "export": from_export, "map": map_count, "verdict": verdict}


def views() -> list[tuple[str, dict]]:
    """The two parity views: the map's default and flood / 7 d / 0.66."""
    flood = config.FRONTEND_PARITY_FLOOD
    return [
        (f"Default view ({config.DEFAULT_VIEWER_DAYS} d, every hazard, severity >= 0)", {"since": f"{config.DEFAULT_VIEWER_DAYS}d"}),
        (f"{flood['hazard']}, {flood['since'][:-1]} d, severity >= {flood['min_severity']}", {"since": flood["since"], "hazard": [flood["hazard"]], "min_severity": flood["min_severity"]}),
    ]


# --------------------------------------------------------------------------- 3. one sample pin
def sample_pin(client, network: bool = True) -> dict:
    """The default view's pin with the most documents, its News and Posts counts, and one timed /forecast call."""
    features = client.get("/events.geojson", params={"since": f"{config.DEFAULT_VIEWER_DAYS}d", "limit": 0}).json()["features"]
    pins = [f for f in features if f["geometry"]["type"] == "Point"]
    if not pins:
        return {"event": None}
    pin = max(pins, key=lambda f: (f["properties"]["doc_count"] or 0) + (f["properties"]["post_count"] or 0) + (f["properties"]["video_count"] or 0))
    props = pin["properties"]
    lon, lat = pin["geometry"]["coordinates"]
    items = client.get(f"/events/{props['event_id']}/documents").json()
    news = sum(1 for item in items if item["kind"] in NEWS_KINDS)
    result = {"event": {"event_id": props["event_id"], "title": props["title"], "lat": lat, "lon": lon}, "news": news, "posts": len(items) - news}
    if not network:
        return {**result, "forecast": {"ok": False, "skipped": True, "error": "skipped (--no-network)"}}
    started = time.monotonic()
    response = client.get("/forecast", params={"lat": lat, "lon": lon})
    elapsed = round(time.monotonic() - started, 3)
    if response.status_code != 200:
        # A 502 is Open-Meteo or the network being unreachable: recorded, not a verdict on the frontend.
        return {**result, "forecast": {"ok": False, "skipped": True, "error": f"HTTP {response.status_code}: {response.json().get('detail', '')}"[:200]}}
    body = response.json()
    days = len(body.get("daily") or [])
    ok = elapsed <= config.FRONTEND_FORECAST_S and days == config.OPEN_METEO_FORECAST_DAYS
    return {**result, "forecast": {"ok": ok, "skipped": False, "seconds": elapsed, "days": days, "error": None}}


# --------------------------------------------------------------------------- 5. audit
def read_audit(web_dir: Path) -> dict | None:
    """web/audit.json as impeccable's audit leaves it, or None when the audit has not run."""
    path = web_dir / "audit.json"
    if not path.exists():
        return None
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"audit.json is not JSON: {exc}", "passed": False}
    serious = audit.get("serious")
    return {**audit, "passed": serious == 0 and audit.get("checked_at_375px") is True}


# --------------------------------------------------------------------------- 6. Python scope
def changed_python(base: str, root: Path | None = None) -> list[str] | str:
    """Python files changed since the merge base with `base`, committed or not; a message when git cannot tell."""
    root = root or config.PROJECT_ROOT
    try:
        merge_base = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        out = subprocess.run(["git", "diff", "--name-only", merge_base, "--", "*.py"], cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"git could not compare with {base}: {getattr(exc, 'stderr', '') or exc}".strip()[:200]
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def outside_scope(files: list[str]) -> list[str]:
    """The changed Python files outside eww serve, eww.api, the CLI, config, this report and tests/."""
    return [name for name in files if name not in config.FRONTEND_PYTHON_SCOPE and not name.startswith("tests/")]


# --------------------------------------------------------------------------- the report
def frontend_report(
    conn: sqlite3.Connection,
    db_path: Path | None = None,
    *,
    web_dir: Path | None = None,
    map_counts: tuple[int | None, int | None] = (None, None),
    build: bool = False,
    network: bool = True,
    base: str = config.FRONTEND_DIFF_BASE,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Measure every M7 exit criterion that a command can measure."""
    with warnings.catch_warnings():  # starlette's TestClient warns about its httpx transport on import
        warnings.simplefilter("ignore", DeprecationWarning)
        from fastapi.testclient import TestClient

    from eww.serve import create_app

    web_dir = web_dir or config.WEB_DIR
    client = TestClient(create_app(db_path), base_url=f"http://{config.SERVE_HOST}:{config.SERVE_PORT}")
    changed = changed_python(base, root)
    return {
        "generated_at": to_iso(now or now_utc()),
        "database": str(db_path or config.DB_PATH),
        "build": build_check(web_dir, build),
        "parity": [parity(conn, client, db_path, name, view, count) for (name, view), count in zip(views(), map_counts)],
        "pin": sample_pin(client, network),
        "audit": read_audit(web_dir),
        "base": base,
        "changed": changed,
        "outside": outside_scope(changed) if isinstance(changed, list) else None,
    }



def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def _build_row(build: dict) -> tuple[str, str, str, str]:
    target = f"built, zero TypeScript errors, first load <= {config.FRONTEND_FIRST_LOAD_JS_KB} KB gzipped"
    if build["ran"] and not build["ok"]:
        return ("`npm run build`", f"failed: {build['error']}", target, FAIL)
    if not build["built"]:
        return ("`npm run build`", "not built (no web/dist/index.html)", target, FAIL)
    timing = f"{build['seconds']} s" if build["ran"] else "time not measured (run with --build)"
    measured = f"built {build['built_at']}, {timing}; first load {_kb(build['bundle']['gzip_bytes'])} gzipped"
    return ("`npm run build`", measured, target, PASS if build["passed"] else FAIL)


def _parity_row(row: dict) -> tuple[str, str, str, str]:
    map_count = "check by hand" if row["map"] is None else str(row["map"])
    measured = f"events_geojson {row['events_geojson']}, export {row['export']}, API {row['http']}, map {map_count}"
    return (f"Pin parity: {row['name']}", measured, "all equal", row["verdict"])


def _pin_rows(pin: dict) -> list[tuple[str, str, str, str]]:
    if pin.get("event") is None:
        return [("Sample pin", "no pin in the default view", "a pin with News, Posts and Weather", FAIL)]
    event = pin["event"]
    measured = f"News {pin['news']}, Posts {pin['posts']} from /events/{{id}}/documents"
    if pin["news"] + pin["posts"] == 0:
        measured += " (no pin in the default view has documents; the tabs should say so)"
    rows = [(f"Sample pin: {event['title']} (`{event['event_id']}`)", measured, "the tabs list the same", BY_HAND)]
    forecast = pin["forecast"]
    target = f"<= {config.FRONTEND_FORECAST_S:g} s, {config.OPEN_METEO_FORECAST_DAYS} days"
    criterion = f"/forecast at the pin ({event['lat']:.2f}, {event['lon']:.2f})"
    if forecast["skipped"]:
        rows.append((criterion, f"not measured: {forecast['error']}", target, SKIPPED))
    else:
        rows.append((criterion, f"{forecast['seconds']:.3f} s, {forecast['days']} days", target, PASS if forecast["ok"] else FAIL))
    return rows


def _audit_row(audit: dict | None) -> tuple[str, str, str, str]:
    criterion, target = "impeccable audit (web/audit.json)", "0 serious, checked at 375 px"
    if audit is None:
        return (criterion, "not run", target, FAIL)
    if "error" in audit:
        return (criterion, audit["error"], target, FAIL)
    measured = f"serious {audit.get('serious')}, moderate {audit.get('moderate')}, minor {audit.get('minor')}; 375 px {'checked' if audit.get('checked_at_375px') else 'not checked'}"
    return (criterion, measured, target, PASS if audit["passed"] else FAIL)


def _scope_row(result: dict) -> tuple[str, str, str, str]:
    criterion, target = f"Python files changed against {result['base']}", "only eww serve, eww.api, db, cli, config, this report, tests"
    if result["outside"] is None:
        return (criterion, result["changed"], target, BY_HAND)
    measured = f"{len(result['changed'])} changed; outside the scope: {', '.join(f'`{n}`' for n in result['outside']) or 'none'}"
    return (criterion, measured, target, FAIL if result["outside"] else PASS)


def criteria(result: dict) -> list[tuple[str, str, str, str]]:
    """The exit-criteria table as (criterion, measured, target, verdict) rows."""
    return [
        _build_row(result["build"]),
        *(_parity_row(row) for row in result["parity"]),
        *_pin_rows(result["pin"]),
        ("Filters and the selected pin live in the URL", "manual", "reloading restores them", BY_HAND),
        _audit_row(result["audit"]),
        _scope_row(result),
        ("`uv run pytest` passes", "manual", "passes", BY_HAND),
        ("`uv run streamlit run app.py` still runs", "manual", "runs", BY_HAND),
    ]


def summary_lines(result: dict) -> list[str]:
    """One line per criterion for the terminal."""
    return [f"{verdict:<13}  {criterion.replace('`', '')}: {measured}" for criterion, measured, _, verdict in criteria(result)]


def render_frontend_markdown(result: dict) -> str:
    """docs/m7.md from a frontend_report() result."""
    build = result["build"]
    lines = [
        "# M7: a real map, React on the GeoJSON boundary",
        "",
        f"Generated {result['generated_at']} by `eww report frontend` against `{result['database']}`.",
        "Re-run that command to refresh the numbers; rows marked CHECK BY HAND are steps for a person, listed under the table.",
        "",
        "## Exit criteria",
        "",
        "| Criterion | Measured | Target | Verdict |",
        "|---|---|---|---|",
        *(f"| {criterion} | {measured} | {target} | {verdict} |" for criterion, measured, target, verdict in criteria(result)),
        "",
        f"Parity calls the API with `limit=0`, so the map must ask for `limit=0` too; without it the API stops at {api.DEFAULT_LIMIT} pins.",
        "",
    ]
    if build["built"]:
        lines += ["## First-load JavaScript", "", f"Counted: {build['bundle']['method']}; gzip level 9.", "", "| Chunk | Gzipped |", "|---|---:|"]
        lines += [f"| `{chunk['name']}` | {_kb(chunk['gzip_bytes'])} |" for chunk in build["bundle"]["chunks"]]
        lines.append("")
    if isinstance(result["changed"], list):
        lines += [f"## Python files changed against {result['base']}", ""]
        lines += [f"- `{name}`{' (outside the scope)' if name in result['outside'] else ''}" for name in result["changed"]] or ["- none"]
        lines += ["", "`eww/api.py` may change only by the read-only additions §4 M7 lists; read its diff to confirm.", ""]
    pin = result["pin"].get("event")
    lines += [
        "## Checks by hand",
        "",
        f"Start `uv run eww serve` and, in `web/`, `npm run dev`; then open {config.WEB_DEV_URL}.",
        "",
        (
            "1. Count the pins on the map in the default view and with flood, 7 days, severity >= 0.66, then re-run "
            "`uv run eww report frontend --map-count-default N --map-count-flood N`."
        ),
        (
            f"2. Click {('`' + pin['title'] + '`') if pin else 'a pin'}: Details shows title, hazard, severity label, started, last observed, "
            "country, sources and the detail link; News and Posts list the counts above; Weather shows current conditions and 5 days."
        ),
        "3. Set the flood filters, select that pin, copy the address bar, reload: the filters and the open pin must come back.",
        "4. Run `uv run pytest` and `uv run streamlit run app.py`.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_frontend_report(conn: sqlite3.Connection, db_path: Path | None = None, out: Path | None = None, **kwargs) -> tuple[Path, dict]:
    """Measure and write docs/m7.md (or `out`)."""
    result = frontend_report(conn, db_path, **kwargs)
    path = out or config.milestone_doc(7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontend_markdown(result), encoding="utf-8")
    return path, result
