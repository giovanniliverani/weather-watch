#!/usr/bin/env python3
"""Structural invariant checks for the architecture document.

Usage:
    python .cursor/skills/update-architecture-doc/scripts/check_doc.py [path]

Path defaults to docs/architecture.md next to the repository root.

FAIL lines mark structural breakage and set exit code 1. INFO lines are numbers a
human has to judge; the checker deliberately does not assert on them, because a
false alarm costs more attention here than it saves.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EXPECTED_SECTIONS = [
    "## 0. Summary",
    "## 1. Decisions",
    "## 1b. Monthly cost",
    "## 2. Architecture",
    "## 3. Data model",
    "## 4. Milestones",
    "## 5. Not in v1",
    "## 6. Implementation prompts",
    "## 7. Open questions",
]
MAX_SUMMARY_WORDS = 200
MAX_MILESTONES = None  # the owner lifted the seven-milestone ceiling on 2026-09-22 when adding M7 (React frontend)

# Narrow on purpose: "3 consecutive days" in an exit criterion is legitimate,
# "3 days of work" is the banned estimate.
TIME_ESTIMATE = re.compile(
    r"\b\d+\s*(?:[-–]\s*\d+\s*)?(?:hours?|days?|weeks?|months?)\s+"
    r"(?:of\s+)?(?:work|effort|coding|development|dev\b)",
    re.I,
)

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def split_sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """[(heading, start, end)] for '## ' headings, ignoring fenced code blocks."""
    heads: list[tuple[str, int]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            heads.append((line.strip(), i))
    out = []
    for n, (head, start) in enumerate(heads):
        end = heads[n + 1][1] if n + 1 < len(heads) else len(lines)
        out.append((head, start, end))
    return out


def body(lines: list[str], sections, heading: str) -> list[str]:
    for head, start, end in sections:
        if head == heading:
            return lines[start + 1 : end]
    return []


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/architecture.md")
    if not path.exists():
        print(f"FAIL  {path} not found (run from the repository root)")
        return 1
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections = split_sections(lines)
    found = [h for h, _, _ in sections]

    # 1. Sections present, in order. A trailing '## Revision log' is allowed.
    if found[: len(EXPECTED_SECTIONS)] != EXPECTED_SECTIONS:
        fail("section headings missing, renamed or reordered")
        for want, got in zip(EXPECTED_SECTIONS, found + [""] * len(EXPECTED_SECTIONS)):
            if want != got:
                print(f"      expected {want!r}, found {got!r}")
    extra = found[len(EXPECTED_SECTIONS) :]
    if extra and extra != ["## Revision log"]:
        fail(f"unexpected top-level sections after §7: {extra}")

    # 2. Summary length.
    summary_words = len(" ".join(body(lines, sections, "## 0. Summary")).split())
    if summary_words > MAX_SUMMARY_WORDS:
        fail(f"§0 summary is {summary_words} words, limit is {MAX_SUMMARY_WORDS}")

    # 3. Milestones: count, numbering, exit criteria.
    ms_lines = body(lines, sections, "## 4. Milestones")
    ms_heads = [
        (int(m.group(1)), i)
        for i, line in enumerate(ms_lines)
        if (m := re.match(r"### M(\d+)\b", line))
    ]
    ids = [n for n, _ in ms_heads]
    if MAX_MILESTONES is not None and len(ids) > MAX_MILESTONES:
        fail(f"{len(ids)} milestones, ceiling is {MAX_MILESTONES}")
    if ids != sorted(set(ids)):
        fail(f"milestone ids are not unique and ascending: {ids}")
    for pos, (num, start) in enumerate(ms_heads):
        stop = ms_heads[pos + 1][1] if pos + 1 < len(ms_heads) else len(ms_lines)
        if not re.search(r"exit criteri", "\n".join(ms_lines[start:stop]), re.I):
            fail(f"milestone M{num} has no exit criteria")

    # 4. One prompt per milestone.
    prompt_ids = [
        int(m.group(1))
        for line in body(lines, sections, "## 6. Implementation prompts")
        if (m := re.match(r"### Prompt M(\d+)\b", line))
    ]
    if set(prompt_ids) != set(ids):
        fail(
            f"§6 prompts {sorted(set(prompt_ids))} do not match §4 milestones {sorted(set(ids))}"
        )

    # 5. Code fences balanced.
    fences = sum(1 for line in lines if line.startswith("```"))
    if fences % 2:
        fail(f"{fences} code-fence markers: one is unclosed")

    # 6. No time estimates where sizes belong.
    for label, heading in (("§1", "## 1. Decisions"), ("§4", "## 4. Milestones")):
        for hit in TIME_ESTIMATE.findall("\n".join(body(lines, sections, heading))):
            fail(f"{label} contains a time estimate ({hit!r}); use small/medium/large")

    # --- numbers for a human to judge -------------------------------------
    note(f"{len(text.split())} words, {len(lines)} lines, {len(found)} top-level sections")
    note(f"§0 summary: {summary_words}/{MAX_SUMMARY_WORDS} words")
    note(f"§4 milestones: {len(ids)} ({', '.join('M%d' % n for n in ids)}); no ceiling since 2026-09-22")

    dec = body(lines, sections, "## 1. Decisions")
    rows = [
        l for l in dec[: next((i for i, x in enumerate(dec) if x.startswith("### ")), len(dec))]
        if l.startswith("| ") and not l.startswith("| ---") and not l.startswith("| Decision")
    ]
    note(f"§1 decision rows: {len(rows)}")

    total = [l for l in body(lines, sections, "## 1b. Monthly cost") if l.startswith("| **Total**")]
    note("§1b total row: " + (total[0].strip() if total else "MISSING - check the ceiling by hand"))

    q = [l for l in body(lines, sections, "## 7. Open questions") if re.match(r"\d+\.\s", l)]
    note(f"§7 open questions: {len(q)}")

    marks = [(i + 1, l.strip()) for i, l in enumerate(lines) if "**verify**" in l.lower()]
    note(f"**verify** markers: {len(marks)} - each needs a matching item in §7")
    for ln, content in marks:
        note(f"    line {ln}: {content[:96]}")

    log = [l for l in body(lines, sections, "## Revision log") if l.startswith("- **")]
    note("revision log: " + (log[-1][:96] if log else "NO ENTRIES - add one for this update"))

    for msg in notes:
        print(("INFO  " + msg) if not msg.startswith("    ") else msg)
    print()
    for msg in failures:
        print(f"FAIL  {msg}")
    print("PASS  all structural checks" if not failures else f"{len(failures)} structural failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
