#!/usr/bin/env python3
"""Discover .claude harness trees and plan/apply a mirror into sibling .cursor dirs.

Default mode is plan-only (no writes). Use --apply only after the user confirms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".cursor",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".uv",
    }
)

# Mirrored by default (Claude Code harness → Cursor project harness).
DEFAULT_MIRROR_DIRS = ("skills", "rules", "hooks", "agents", "commands")

# Often incompatible across tools — listed in the plan, copied only with --include-settings.
OPTIONAL_ROOT_FILES = ("settings.json", "settings.local.json")


@dataclass
class FilePlan:
    claude_root: str
    cursor_root: str
    relative_path: str
    category: str  # skills | rules | hooks | agents | commands | settings
    action: str  # copy_new | conflict | identical | skip_settings
    src: str
    dst: str
    src_mtime: str | None
    dst_mtime: str | None
    src_size: int | None
    dst_size: int | None
    fresher: str  # claude | cursor | same | n/a
    note: str


def _repo_root_from_script() -> Path:
    # .../.cursor/skills/sync-claude-to-cursor/scripts/this.py → repo root
    return Path(__file__).resolve().parents[4]


def find_claude_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        # Mutate dirnames in place to prune the walk.
        keep: list[str] = []
        for name in dirnames:
            if name in SKIP_DIR_NAMES:
                continue
            if name == ".claude":
                found.append(Path(dirpath) / name)
                continue
            keep.append(name)
        dirnames[:] = keep
    return sorted(found)


def _iso_mtime(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fresher(src: Path, dst: Path) -> str:
    src_m = src.stat().st_mtime
    dst_m = dst.stat().st_mtime
    if abs(src_m - dst_m) < 1.0:
        return "same"
    return "claude" if src_m > dst_m else "cursor"


def _category_for(rel: Path) -> str:
    if rel.parts and rel.parts[0] in DEFAULT_MIRROR_DIRS:
        return rel.parts[0]
    if rel.name in OPTIONAL_ROOT_FILES and len(rel.parts) == 1:
        return "settings"
    return "other"


def iter_mirror_files(
    claude_root: Path,
    mirror_dirs: tuple[str, ...],
    include_settings: bool,
) -> list[Path]:
    files: list[Path] = []
    for dirname in mirror_dirs:
        base = claude_root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part == "__pycache__" or part.endswith(".pyc") for part in path.parts):
                continue
            files.append(path)

    if include_settings:
        for name in OPTIONAL_ROOT_FILES:
            path = claude_root / name
            if path.is_file():
                files.append(path)
    else:
        # Still surface settings in the plan as skip_settings.
        for name in OPTIONAL_ROOT_FILES:
            path = claude_root / name
            if path.is_file():
                files.append(path)
    return files


def build_plan(
    root: Path,
    mirror_dirs: tuple[str, ...],
    include_settings: bool,
) -> list[FilePlan]:
    plans: list[FilePlan] = []
    for claude_root in find_claude_dirs(root):
        cursor_root = claude_root.parent / ".cursor"
        for src in iter_mirror_files(claude_root, mirror_dirs, include_settings=True):
            rel = src.relative_to(claude_root)
            category = _category_for(rel)
            dst = cursor_root / rel

            def _rel(path: Path) -> str:
                return path.relative_to(root).as_posix()

            claude_rel = _rel(claude_root)
            cursor_rel = _rel(cursor_root)
            src_rel = _rel(src)
            dst_rel = _rel(dst)
            rel_posix = rel.as_posix()

            if category == "settings" and not include_settings:
                plans.append(
                    FilePlan(
                        claude_root=claude_rel,
                        cursor_root=cursor_rel,
                        relative_path=rel_posix,
                        category=category,
                        action="skip_settings",
                        src=src_rel,
                        dst=dst_rel,
                        src_mtime=_iso_mtime(src),
                        dst_mtime=_iso_mtime(dst) if dst.is_file() else None,
                        src_size=src.stat().st_size,
                        dst_size=dst.stat().st_size if dst.is_file() else None,
                        fresher=_fresher(src, dst) if dst.is_file() else "n/a",
                        note="Settings formats often differ; omitted unless --include-settings",
                    )
                )
                continue

            if not dst.exists():
                plans.append(
                    FilePlan(
                        claude_root=claude_rel,
                        cursor_root=cursor_rel,
                        relative_path=rel_posix,
                        category=category,
                        action="copy_new",
                        src=src_rel,
                        dst=dst_rel,
                        src_mtime=_iso_mtime(src),
                        dst_mtime=None,
                        src_size=src.stat().st_size,
                        dst_size=None,
                        fresher="claude",
                        note="Destination missing",
                    )
                )
                continue

            if not dst.is_file():
                plans.append(
                    FilePlan(
                        claude_root=claude_rel,
                        cursor_root=cursor_rel,
                        relative_path=rel_posix,
                        category=category,
                        action="conflict",
                        src=src_rel,
                        dst=dst_rel,
                        src_mtime=_iso_mtime(src),
                        dst_mtime=None,
                        src_size=src.stat().st_size,
                        dst_size=None,
                        fresher="n/a",
                        note="Destination exists but is not a file",
                    )
                )
                continue

            same_hash = _file_sha256(src) == _file_sha256(dst)
            fresher = _fresher(src, dst)
            if same_hash:
                action = "identical"
                note = "Same content (sha256)"
            else:
                action = "conflict"
                note = f"Different content; mtime fresher={fresher}"

            plans.append(
                FilePlan(
                    claude_root=claude_rel,
                    cursor_root=cursor_rel,
                    relative_path=rel_posix,
                    category=category,
                    action=action,
                    src=src_rel,
                    dst=dst_rel,
                    src_mtime=_iso_mtime(src),
                    dst_mtime=_iso_mtime(dst),
                    src_size=src.stat().st_size,
                    dst_size=dst.stat().st_size,
                    fresher=fresher if not same_hash else "same",
                    note=note,
                )
            )
    return plans


def format_human_report(root: Path, plans: list[FilePlan]) -> str:
    claude_dirs = find_claude_dirs(root)
    lines: list[str] = []
    lines.append(f"Repo root: {root}")
    lines.append(f"Found {len(claude_dirs)} .claude director{'y' if len(claude_dirs) == 1 else 'ies'}:")
    for d in claude_dirs:
        lines.append(
            f"  - {d.relative_to(root).as_posix()} -> "
            f"{(d.parent / '.cursor').relative_to(root).as_posix()}"
        )
    lines.append("")

    by_action: dict[str, list[FilePlan]] = {}
    for p in plans:
        by_action.setdefault(p.action, []).append(p)

    order = ("conflict", "copy_new", "identical", "skip_settings")
    labels = {
        "conflict": "CONFLICTS (ask before overwrite)",
        "copy_new": "NEW (safe to copy)",
        "identical": "IDENTICAL (no copy needed)",
        "skip_settings": "SETTINGS skipped (use --include-settings to consider)",
    }

    for action in order:
        items = by_action.get(action, [])
        lines.append(f"## {labels[action]} ({len(items)})")
        if not items:
            lines.append("(none)")
            lines.append("")
            continue
        for p in items:
            lines.append(f"- `{p.src}` -> `{p.dst}`")
            lines.append(f"  category={p.category}  fresher={p.fresher}")
            lines.append(
                f"  claude mtime={p.src_mtime} size={p.src_size} | "
                f"cursor mtime={p.dst_mtime} size={p.dst_size}"
            )
            if p.note:
                lines.append(f"  note: {p.note}")
        lines.append("")

    conflicts = by_action.get("conflict", [])
    if conflicts:
        lines.append("## Fresher rundown (conflicts only)")
        for p in conflicts:
            if p.fresher == "claude":
                verdict = "Claude copy looks newer by mtime"
            elif p.fresher == "cursor":
                verdict = (
                    "Cursor copy looks newer by mtime - "
                    "overwriting may lose Cursor edits"
                )
            elif p.fresher == "same":
                verdict = "mtimes ~equal but content differs - inspect manually"
            else:
                verdict = "could not judge freshness"
            lines.append(f"- `{p.relative_path}` @ `{p.claude_root}`: {verdict}")
        lines.append("")

    lines.append(
        "Plan only - no files written. Re-run with --apply after confirmation "
        "(and optional --overwrite-conflicts / --include-settings)."
    )
    return "\n".join(lines)


def apply_plan(
    root: Path,
    plans: list[FilePlan],
    *,
    overwrite_conflicts: bool,
    include_settings: bool,
) -> tuple[int, int, int]:
    copied = 0
    skipped = 0
    errors = 0
    for p in plans:
        if p.action == "identical":
            skipped += 1
            continue
        if p.action == "skip_settings":
            skipped += 1
            continue
        if p.action == "conflict" and not overwrite_conflicts:
            skipped += 1
            continue
        if p.category == "settings" and not include_settings:
            skipped += 1
            continue
        if p.action not in {"copy_new", "conflict"}:
            skipped += 1
            continue

        src = root / p.src
        dst = root / p.dst
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            print(f"copied: {p.src} -> {p.dst}")
        except OSError as exc:
            errors += 1
            print(f"error: {p.src} -> {p.dst}: {exc}", file=sys.stderr)
    return copied, skipped, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or apply mirroring of .claude → sibling .cursor harness trees."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (default: inferred from this script location)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON plan instead of the human report",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform copies (default is plan-only). Still skips conflicts unless "
        "--overwrite-conflicts is set.",
    )
    parser.add_argument(
        "--overwrite-conflicts",
        action="store_true",
        help="When applying, overwrite conflicting destination files with .claude versions",
    )
    parser.add_argument(
        "--include-settings",
        action="store_true",
        help="Include settings.json / settings.local.json in copy consideration",
    )
    parser.add_argument(
        "--dirs",
        nargs="+",
        default=list(DEFAULT_MIRROR_DIRS),
        help=f"Subdirectories to mirror (default: {' '.join(DEFAULT_MIRROR_DIRS)})",
    )
    args = parser.parse_args(argv)

    root = (args.root or _repo_root_from_script()).resolve()
    if not root.is_dir():
        print(f"error: root is not a directory: {root}", file=sys.stderr)
        return 2

    mirror_dirs = tuple(args.dirs)
    plans = build_plan(root, mirror_dirs, include_settings=args.include_settings)

    if args.json:
        payload = {
            "root": str(root),
            "claude_dirs": [
                d.relative_to(root).as_posix() for d in find_claude_dirs(root)
            ],
            "plans": [asdict(p) for p in plans],
            "counts": {
                action: sum(1 for p in plans if p.action == action)
                for action in sorted({p.action for p in plans} | {"conflict", "copy_new"})
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_human_report(root, plans))

    if not args.apply:
        return 0

    if any(p.action == "conflict" for p in plans) and not args.overwrite_conflicts:
        print(
            "\nNote: conflicts will be skipped. Pass --overwrite-conflicts only after "
            "the user explicitly approved overwrites.",
            file=sys.stderr,
        )

    copied, skipped, errors = apply_plan(
        root,
        plans,
        overwrite_conflicts=args.overwrite_conflicts,
        include_settings=args.include_settings,
    )
    print(f"\nApply done: copied={copied} skipped={skipped} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
