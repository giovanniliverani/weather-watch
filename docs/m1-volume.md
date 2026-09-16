# M1 data-branch volume

Generated 2026-09-16T15:07:22Z by `eww report volume`: a fresh `git clone --branch data --single-branch` of `https://github.com/giovanniliverani/weather-watch.git` measured with `git count-objects -vH`.

**Verdict: PROVISIONAL (less than half a day of snapshots)** (exit criterion 5: size-pack below 15 MB after 3 days of collection).

| Measure | Value |
|---|---:|
| Commits on the branch | 2 |
| Snapshot files | 2 |
| First snapshot committed | 2026-09-16T15:06:33Z |
| Days of snapshots measured | 0.00 |
| size-pack (fresh clone) | 0.91 MB |
| loose objects | 0.00 MB |
| Growth per day | n/a |
| Extrapolated per year | n/a |

Decision rule (docs/architecture.md §1): above 5 MB per day packed, the collector sink moves from the `data` branch to a Cloudflare R2 bucket. Re-run `uv run eww report volume` after three days of scheduled runs to replace a provisional verdict.
