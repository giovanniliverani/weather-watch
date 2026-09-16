"""Read the `data` branch through git plumbing: fetch, list, show. No checkout, no worktree.

Also the fresh-clone measurement behind `eww report volume`. Every call shells out to the `git`
on PATH and works against the repository that holds this package (eww.config.PROJECT_ROOT) unless
another path is given.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from eww import config

log = logging.getLogger(__name__)


class GitError(RuntimeError):
    pass


def _run(args: list[str], repo: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
    proc = subprocess.run(cmd, capture_output=True)
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def default_repo() -> Path:
    return config.PROJECT_ROOT


def fetch_branch(branch: str | None = None, remote: str | None = None, repo: Path | None = None) -> bool:
    """`git fetch <remote> <branch>:<branch>`; False (with a warning) when offline or the branch is missing."""
    branch = branch or config.DATA_BRANCH
    remote = remote or config.GIT_REMOTE
    proc = _run(["fetch", "--quiet", remote, f"{branch}:{branch}"], repo or default_repo(), check=False)
    if proc.returncode != 0:
        log.warning("git fetch failed remote=%s branch=%s error=%s", remote, branch, proc.stderr.decode("utf-8", "replace").strip()[:300])
        return False
    return True


def branch_exists(branch: str | None = None, repo: Path | None = None) -> bool:
    branch = branch or config.DATA_BRANCH
    return _run(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo or default_repo(), check=False).returncode == 0


def head_commit(branch: str | None = None, repo: Path | None = None) -> str | None:
    branch = branch or config.DATA_BRANCH
    proc = _run(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo or default_repo(), check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def list_files(branch: str | None = None, prefixes: tuple[str, ...] = ("snapshots", "runs"), repo: Path | None = None) -> list[str]:
    """`git ls-tree -r --name-only <branch> -- snapshots runs`, sorted. Missing prefixes simply yield nothing."""
    branch = branch or config.DATA_BRANCH
    proc = _run(["ls-tree", "-r", "--name-only", "-z", branch, "--", *prefixes], repo or default_repo())
    return sorted(path for path in proc.stdout.decode("utf-8").split("\0") if path)


def read_text(path: str, branch: str | None = None, repo: Path | None = None) -> str:
    """`git show <branch>:<path>` decoded as UTF-8."""
    branch = branch or config.DATA_BRANCH
    return _run(["show", f"{branch}:{path}"], repo or default_repo()).stdout.decode("utf-8")


def remote_url(remote: str | None = None, repo: Path | None = None) -> str | None:
    proc = _run(["remote", "get-url", remote or config.GIT_REMOTE], repo or default_repo(), check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def clone_branch(url: str, branch: str, dest: Path) -> None:
    _run(["clone", "--quiet", "--branch", branch, "--single-branch", url, str(dest)])


def count_objects(repo: Path) -> dict[str, int]:
    """`git count-objects -v` with every size converted from KiB to bytes."""
    out: dict[str, int] = {}
    for line in _run(["count-objects", "-v"], repo).stdout.decode().splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.isdigit():
            out[key] = int(value) * (1024 if key.startswith("size") else 1)
    return out


def first_commit_at(repo: Path, branch: str, path: str | None = None) -> str | None:
    """Committer date (ISO 8601) of the oldest commit on `branch`, optionally restricted to `path`."""
    args = ["log", "--reverse", "--format=%cI", branch]
    if path:
        args += ["--", path]
    lines = _run(args, repo).stdout.decode().splitlines()
    return lines[0].strip() if lines else None


def commit_count(repo: Path, branch: str) -> int:
    return int(_run(["rev-list", "--count", branch], repo).stdout.decode().strip() or 0)


def rmtree(path: Path) -> None:
    """shutil.rmtree that copes with the read-only pack files git writes on Windows."""

    def clear_readonly(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=clear_readonly)
