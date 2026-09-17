"""One-shot: copy ecc-s4-migration-mapping only. Delete after use."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PREFIX = "skills/ecc-s4-migration-mapping/"


def main() -> int:
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("sync_claude_to_cursor.py")),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    data = json.loads(proc.stdout)
    plans = [
        p
        for p in data["plans"]
        if p["relative_path"].startswith(PREFIX) and p["action"] == "copy_new"
    ]
    copied = 0
    for plan in plans:
        src = ROOT / plan["src"]
        dst = ROOT / plan["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f"copied: {plan['src']} -> {plan['dst']}")
    print(f"done: copied={copied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
