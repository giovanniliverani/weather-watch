# Extreme Weather Watch: the `data` branch

Raw archive of the spine collectors and the system of record for *what was observed, when*.
Written by the `collect` workflow on `main` (`.github/workflows/collect.yml`, cron `7 */3 * * *` UTC)
and occasionally by `eww collect --all-spine --out <dir>` from a laptop. The laptop rebuilds its
SQLite database from these files with `eww sync`; nothing downstream is needed to reproduce it.

## Layout

```
snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json    one file per (run, source): the raw feed items, verbatim
runs/<YYYY-MM-DD>.jsonl                          one JSON line per (run, source), appended by every run
```

A snapshot file is a small envelope around the raw items exactly as the feed returned them:

```
{"format": "eww.snapshot/1", "source_id": "gdacs", "run_id": "<ULID>", "scheduled_for": "...",
 "started_at": "...", "finished_at": "...", "since": "...", "until": "...", "status": "ok",
 "http_status": 200, "error": null, "requests": [...], "items_seen": 2192, "items": [ ... ]}
```

A run-log line has exactly these keys:

```
{run_id, source_id, scheduled_for, started_at, finished_at, status, http_status, items_seen, snapshot_path, error}
```

`status` is `ok`, `partial` (a fallback was used or some requests failed) or `failed` (no snapshot;
`snapshot_path` is null and `error` says why). `scheduled_for` is the 3-hour slot the run served: the
UTC hour rounded down to a multiple of 3, e.g. `2026-09-16T15:00:00Z`. All timestamps are UTC ISO 8601.

## Rules

- Append-only. Files are never rewritten or deleted; a bad run is a `failed` line, not a missing one.
- Sources: gdacs (JSON SEARCH API, RSS fallback) and eonet (NASA EONET v3); copernicus joins later.
- Attribution: Global Disaster Alert and Coordination System (GDACS), European Union, CC BY 4.0;
  NASA Earth Observatory Natural Event Tracker (EONET), public domain.
