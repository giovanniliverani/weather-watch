"""Seed the hand-labelled merge pairs file from `eww labels candidates` output, using only facts in the feeds.

Every label this script writes follows from something a source itself asserts, never from the
pipeline's score, so the seeded rows can serve as a first evaluation set while the reviewer works
through the rest:

    yes  identical normalised storm name in both feeds (rule storm_name, name_match yes)
    yes  one feed cites the other's id explicitly (linked yes: EONET sources[].url, Copernicus gdacsId)
    no   two named storms with different names in the same class and week (numbered depressions
         such as TWO-C are left out: they may be the same system before it was named)
    no   one feed cites a *different* id of the other feed for the paired record (key_conflict yes)

Open proposals are copied with an empty `same_event` for the reviewer. Every seeded row carries a
`notes` column with its reason and a `labelled_by` column, so the labels can be audited and replaced.
The output file is hand-maintained afterwards: the script refuses to overwrite it without --force.

    uv run python .cursor/skills/playbook/files/seed-merge-labels.py [--candidates CSV] [--out CSV]
        [--labelled-by TEXT] [--max-yes N] [--max-no N] [--per-country N] [--force]

Filled example (M2, 2026-09-17: 24 yes, 24 no, 11 proposals left blank):

    uv run eww labels candidates --days 30
    uv run python .cursor/skills/playbook/files/seed-merge-labels.py --labelled-by "starter set (Claude, 2026-09-17): review and overwrite"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
}
STORM_WORDS = {"tropical", "cyclone", "hurricane", "typhoon", "storm", "depression", "severe", "super", "post", "subtropical"}
DEFAULT_CANDIDATES = Path("data/labels/merge_candidates.csv")
DEFAULT_OUT = Path("data/labels/merge_pairs.csv")


def storm_name(title: str) -> str | None:
    """'Tropical Cyclone TWENTYFOUR-26' -> 'twentyfour', 'Hurricane Karina' -> 'karina'; None when unnamed."""
    words = [w for w in re.split(r"[^a-z]+", re.sub(r"-\d{2}$", "", title.strip()).lower()) if w and w not in STORM_WORDS]
    name = "".join(words)
    return name if len(name) >= 3 else None


def is_numbered(name: str | None) -> bool:
    """Depression numbers (TWO-C, FIFTEEN-E, TWENTYTHREE) rather than names."""
    if not name:
        return True
    for word in sorted(NUMBER_WORDS, key=len, reverse=True):
        if name.startswith(word) and (len(name) == len(word) or len(name) <= len(word) + 1 or name[len(word):] in NUMBER_WORDS):
            return True
    return False


def spread(rows: list[dict], n: int) -> list[dict]:
    """n rows spread over the list: the first ones, the middle ones and the last ones."""
    if len(rows) <= n:
        return rows
    head, tail = n // 3, n // 3
    middle = n - head - tail
    start = max(0, len(rows) // 2 - middle // 2)
    return rows[:head] + rows[start : start + middle] + rows[len(rows) - tail :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--labelled-by", default=f"seeded from feed facts ({date.today().isoformat()}): review and overwrite")
    parser.add_argument("--max-yes", type=int, default=24, help="cap on seeded true pairs (storm names first, then cited ids spread by distance)")
    parser.add_argument("--max-no", type=int, default=24, help="cap on seeded non-pairs (differently named storms first, then key conflicts)")
    parser.add_argument("--max-storm-no", type=int, default=8, help="of which at most this many differently named storm pairs, so key conflicts get their share")
    parser.add_argument("--per-country", type=int, default=2, help="key-conflict non-pairs per country, closest first")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --out")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"{args.out} exists and is hand-maintained; pass --force to replace it", file=sys.stderr)
        return 2
    with args.candidates.open(encoding="utf-8-sig", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    if not candidates:
        print(f"{args.candidates} has no rows", file=sys.stderr)
        return 2

    rows: list[dict] = []

    def take(row: dict, label: str, note: str) -> None:
        rows.append({**row, "same_event": label, "notes": note, "labelled_by": args.labelled_by})

    storms_yes = [r for r in candidates if r["rule"] == "storm_name" and r["name_match"] == "yes"]
    for r in storms_yes[: args.max_yes]:
        take(r, "yes", f"same storm name in both feeds, {r['days_apart']} days apart; tracks meet ({r['distance_km']} km)")
    linked = sorted((r for r in candidates if r["linked"] == "yes"), key=lambda r: float(r["distance_km"] or 0))
    for r in spread(linked, max(0, args.max_yes - len(rows))):
        take(r, "yes", f"{r['source_b']} record cites {r['source_a']} id {r['external_id_a']} explicitly; {r['distance_km']} km apart")

    storm_no = []
    for r in candidates:
        if r["hazard_a"] not in ("tropical_cyclone", "severe_storm") or r["name_match"] == "yes" or r["pipeline"] == "merged":
            continue
        a, b = storm_name(r["title_a"]), storm_name(r["title_b"])
        if a and b and a != b and not is_numbered(a) and not is_numbered(b):
            storm_no.append(r)
    storm_no.sort(key=lambda r: -float(r["score"]))
    for r in storm_no[: min(args.max_no, args.max_storm_no)]:
        take(r, "no", "two differently named storms in the same basin and week")
    conflicts = sorted((r for r in candidates if r["key_conflict"] == "yes"), key=lambda r: float(r["distance_km"] or 0))
    per_country: dict[str, int] = {}
    for r in conflicts:
        if sum(1 for x in rows if x["same_event"] == "no") >= args.max_no:
            break
        country = r["title_a"].split(" in ", 1)[-1]
        if per_country.get(country, 0) >= args.per_country:
            continue
        per_country[country] = per_country.get(country, 0) + 1
        take(r, "no", f"{r['source_b']} record mirrors a different {r['source_a']} event ({r['title_b'].split()[-1]}), {r['distance_km']} km away: the source keeps them apart")

    seeded = {(r["source_a"], r["external_id_a"], r["source_b"], r["external_id_b"]) for r in rows}
    for r in candidates:
        if r["pipeline"] == "proposed" and (r["source_a"], r["external_id_a"], r["source_b"], r["external_id_b"]) not in seeded:
            rows.append({**r, "same_event": "", "notes": "open proposal: decide here or in the Review tab", "labelled_by": ""})

    columns = list(candidates[0].keys()) + ["notes", "labelled_by"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    yes = sum(1 for r in rows if r["same_event"] == "yes")
    no = sum(1 for r in rows if r["same_event"] == "no")
    print(f"written {args.out}: {len(rows)} rows, yes={yes} no={no} unlabelled={len(rows) - yes - no}")
    if yes < 20 or no < 20:
        print("fewer than 20 on one side: the feeds' own facts do not give more, the rest is yours to label", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
