"""UTC time helpers. Every timestamp in the system is an ISO 8601 UTC string ending in 'Z'."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_RELATIVE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    """Format an aware (or naive-as-UTC) datetime as 'YYYY-MM-DDTHH:MM:SSZ'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime(ISO_FORMAT)


def now_iso() -> str:
    return to_iso(now_utc())


def parse_iso(value: str) -> datetime:
    """Parse ISO 8601 ('2026-09-11T21:23:55', '...Z', '...+00:00', or a bare date). Naive input is UTC."""
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalise_iso(value: str | None) -> str | None:
    """Re-emit any parseable timestamp in the canonical form; None stays None, '' becomes None."""
    if value is None or not str(value).strip():
        return None
    return to_iso(parse_iso(str(value)))


def rfc2822_to_iso(value: str | None) -> str | None:
    """RSS pubDate style ('Wed, 16 Sep 2026 11:00:25 GMT') to canonical ISO."""
    if value is None or not value.strip():
        return None
    return to_iso(parsedate_to_datetime(value.strip()))


def parse_when(value: str | datetime | None, now: datetime | None = None) -> datetime | None:
    """Accept ISO 8601 or a relative span ('14d', '36h', '15m') counted back from `now`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    match = _RELATIVE.match(value)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]
        return (now or now_utc()) - delta
    return parse_iso(value)


def slot_for(dt: datetime, hours: int) -> str:
    """The start of the `hours`-long collection slot containing `dt`, e.g. 09:00Z for 09:47Z with 3-hour slots."""
    dt = dt.astimezone(timezone.utc)
    return to_iso(dt.replace(hour=(dt.hour // hours) * hours, minute=0, second=0, microsecond=0))


def snapshot_stamp(dt: datetime) -> str:
    """Filename stamp for a snapshot: 'YYYY-MM-DDTHH-MMZ' (minute precision, filesystem-safe)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%MZ")
