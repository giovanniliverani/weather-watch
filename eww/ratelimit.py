"""Rate limits and the structured provider log (M3).

Every external call of the enrichment and geocoding stages goes through one `Provider` per remote
service (eww.http.Provider), and every Provider waits on the limiters here before it sends anything:

    MinInterval(seconds)            at least this long between two calls (GDELT: 5 s, Nominatim: 1 s)
    SlidingWindow(max_calls, period) at most `max_calls` inside any window of `period` seconds
                                    (Nominatim 4 per minute, GeoNames 1,000 per hour and 10,000 per day,
                                    ReliefWeb 1,000 per day); calls from earlier runs are preloaded from
                                    the log so a budget survives a restart

Each call, and each geocode lookup, is appended as one JSON line to config.PROVIDER_LOG. `eww doctor`
reads that file back to prove the limits held (exit criterion 3 of M3) and to compute the geocode
cache hit rate (criterion 4). The clock and the sleep function are injectable so tests run instantly.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from eww import config
from eww.clock import parse_iso, to_iso

log = logging.getLogger(__name__)


class RateLimitExceeded(RuntimeError):
    """The remote service answered 429, or a hard budget is spent: stop calling it for this run."""


# ----------------------------------------------------------------------------- limiters
class MinInterval:
    """At least `seconds` between the end of one call and the start of the next."""

    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.seconds = float(seconds)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self.waited_s = 0.0

    def wait(self) -> float:
        if self._last is not None:
            due = self._last + self.seconds
            now = self._clock()
            if now < due:
                self._sleep(due - now)
                self.waited_s += due - now
                return due - now
        return 0.0

    def mark(self) -> None:
        self._last = self._clock()


class SlidingWindow:
    """At most `max_calls` in any `period_s`-second window; waits for the oldest call to age out.

    `budget` (optional) is a hard stop below the service's own limit: once that many calls sit inside
    the window, `wait()` raises RateLimitExceeded instead of sleeping, so a run never burns the whole
    daily allowance of a shared free tier.
    """

    def __init__(
        self,
        max_calls: int,
        period_s: float,
        *,
        budget: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        name: str = "",
    ):
        self.max_calls = int(max_calls)
        self.period_s = float(period_s)
        self.budget = budget
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self.waited_s = 0.0

    def _prune(self, now: float) -> None:
        while self._calls and self._calls[0] <= now - self.period_s:
            self._calls.popleft()

    def preload(self, ages_s: Iterable[float]) -> None:
        """Register calls made `age` seconds ago (from the provider log), so budgets survive restarts."""
        now = self._clock()
        for age in sorted(ages_s, reverse=True):
            if 0 <= age < self.period_s:
                self._calls.append(now - age)
        self._prune(now)

    def count(self) -> int:
        self._prune(self._clock())
        return len(self._calls)

    def wait(self) -> float:
        now = self._clock()
        self._prune(now)
        if self.budget is not None and len(self._calls) >= self.budget:
            raise RateLimitExceeded(f"{self.name or 'provider'}: {len(self._calls)} calls in the last {self.period_s:g} s reach the budget of {self.budget}")
        if len(self._calls) >= self.max_calls:
            pause = self._calls[0] + self.period_s - now
            if pause > 0:
                self._sleep(pause)
                self.waited_s += pause
                self._prune(self._clock())
                return pause
        return 0.0

    def mark(self) -> None:
        self._calls.append(self._clock())


# ----------------------------------------------------------------------------- the structured log
def log_path() -> Path:
    return config.PROVIDER_LOG


def append(record: dict) -> None:
    """One JSON line; the file is created on first use. Never raises: logging must not stop a run."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:  # pragma: no cover
        log.warning("provider log not written error=%s", exc)


def log_call(provider: str, url: str, status: int | None, started_at: datetime, elapsed_s: float, user_agent: str, *, params: dict | None = None, error: str | None = None, waited_s: float = 0.0) -> None:
    record = {
        "kind": "call",
        "provider": provider,
        "at": to_iso(started_at),
        "url": url,
        "status": status,
        "elapsed_s": round(elapsed_s, 3),
        "waited_s": round(waited_s, 3),
        "user_agent": user_agent,
        "params": _public_params(params),
        "error": error,
    }
    append(record)
    log.info("provider call provider=%s status=%s elapsed_s=%.2f waited_s=%.1f url=%s", provider, status, elapsed_s, waited_s, url)


def log_geocode(provider: str, query_norm: str, country_hint: str, *, hit: bool, cached: bool, at: datetime | None = None) -> None:
    append({
        "kind": "geocode",
        "provider": provider,
        "at": to_iso(at or datetime.now(timezone.utc)),
        "query": query_norm,
        "country_hint": country_hint,
        "hit": bool(hit),
        "cached": bool(cached),
    })


def _public_params(params: dict | None) -> dict | None:
    if not params:
        return None
    return {k: ("<set>" if k.lower() in config.SECRET_PARAMS else v) for k, v in params.items()}


def read(since: datetime | None = None, kind: str | None = None) -> list[dict]:
    """Every record in the log (optionally since a time and of one kind), oldest first."""
    path = log_path()
    if not path.exists():
        return []
    out: list[dict] = []
    since_iso = to_iso(since) if since else None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind and record.get("kind") != kind:
                continue
            if since_iso and str(record.get("at", "")) < since_iso:
                continue
            out.append(record)
    return out


def recent_call_ages(provider: str, period_s: float, now: datetime | None = None) -> list[float]:
    """Ages in seconds of the provider's logged calls inside the last `period_s` seconds (for preload)."""
    now = now or datetime.now(timezone.utc)
    ages: list[float] = []
    for record in read(now - timedelta(seconds=period_s), kind="call"):
        if record.get("provider") != provider or not record.get("at"):
            continue
        age = (now - parse_iso(record["at"])).total_seconds()
        if 0 <= age < period_s:
            ages.append(age)
    return ages


# ----------------------------------------------------------------------------- what `eww doctor` proves
def max_calls_in_window(records: list[dict], period_s: float) -> int:
    """The largest number of calls inside any `period_s`-second window (sorted sweep)."""
    times = sorted(parse_iso(r["at"]).timestamp() for r in records if r.get("at"))
    best = 0
    start = 0
    for end, stamp in enumerate(times):
        while times[start] <= stamp - period_s:
            start += 1
        best = max(best, end - start + 1)
    return best


def min_spacing_s(records: list[dict]) -> float | None:
    """The smallest gap in seconds between the starts of two consecutive calls; None below two calls."""
    times = sorted(parse_iso(r["at"]).timestamp() for r in records if r.get("at"))
    if len(times) < 2:
        return None
    return round(min(b - a for a, b in zip(times, times[1:])), 3)


def limits_report(since: datetime | None = None) -> dict:
    """Per provider: calls, the maxima the exit criterion asks for, and whether every User-Agent carried the contact."""
    calls = read(since, kind="call")
    by_provider: dict[str, list[dict]] = {}
    for record in calls:
        by_provider.setdefault(str(record.get("provider")), []).append(record)
    out: dict[str, dict] = {}
    for provider, records in sorted(by_provider.items()):
        statuses: dict[str, int] = {}
        for record in records:
            key = str(record.get("status"))
            statuses[key] = statuses.get(key, 0) + 1
        out[provider] = {
            "calls": len(records),
            "statuses": statuses,
            "max_per_minute": max_calls_in_window(records, 60),
            "max_per_hour": max_calls_in_window(records, 3600),
            "max_per_day": max_calls_in_window(records, 86400),
            "min_spacing_s": min_spacing_s(records),
            "user_agent_ok": all(config.CONTACT in str(r.get("user_agent", "")) for r in records),
            "first_at": records[0].get("at"),
            "last_at": records[-1].get("at"),
        }
    return out


def geocode_report(since: datetime | None = None) -> dict:
    """Cache hits and misses of the geocoder since a date (a lookup answered from geocode_cache is a hit)."""
    records = read(since, kind="geocode")
    hits = sum(1 for r in records if r.get("cached"))
    misses = len(records) - hits
    by_provider: dict[str, dict[str, int]] = {}
    for record in records:
        entry = by_provider.setdefault(str(record.get("provider")), {"lookups": 0, "cached": 0, "found": 0})
        entry["lookups"] += 1
        entry["cached"] += 1 if record.get("cached") else 0
        entry["found"] += 1 if record.get("hit") else 0
    return {
        "lookups": len(records),
        "cache_hits": hits,
        "cache_misses": misses,
        "hit_rate": (hits / len(records)) if records else None,
        "by_provider": by_provider,
    }
