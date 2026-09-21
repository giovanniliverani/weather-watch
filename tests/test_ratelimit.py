"""Limiters with an injected clock, the structured provider log and what `eww doctor` computes from it."""

import json

import pytest

from eww import config, ratelimit
from eww.clock import now_utc, parse_iso, to_iso
from datetime import timedelta


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_min_interval_waits_from_the_previous_mark():
    clock = Clock()
    limiter = ratelimit.MinInterval(5, clock=clock, sleep=clock.sleep)
    assert limiter.wait() == 0.0
    limiter.mark()
    clock.now += 1.5
    assert limiter.wait() == pytest.approx(3.5)
    assert clock.slept == [pytest.approx(3.5)]
    limiter.mark()
    clock.now += 6
    assert limiter.wait() == 0.0


def test_sliding_window_never_exceeds_the_limit_and_honours_a_budget():
    clock = Clock()
    limiter = ratelimit.SlidingWindow(4, 60, clock=clock, sleep=clock.sleep, name="nominatim")
    for _ in range(4):
        limiter.wait()
        limiter.mark()
        clock.now += 1
    pause = limiter.wait()  # the 5th call waits until the first one is 60 s old
    assert pause == pytest.approx(56)
    assert limiter.count() == 3
    budgeted = ratelimit.SlidingWindow(1000, 3600, budget=2, clock=clock, sleep=clock.sleep, name="geonames")
    budgeted.preload([10.0, 20.0, 5000.0])  # two recent calls inside the hour; the third is too old to count
    assert budgeted.count() == 2
    with pytest.raises(ratelimit.RateLimitExceeded):
        budgeted.wait()


def test_log_round_trip_and_reports(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "providers.jsonl"
    monkeypatch.setattr(config, "PROVIDER_LOG", path)
    now = now_utc()
    ua = config.USER_AGENT
    for i, offset in enumerate((0, 5, 12, 40)):
        ratelimit.log_call("gdelt", "https://api.gdeltproject.org/api/v2/doc/doc", 200, now + timedelta(seconds=offset), 0.5, ua, params={"query": "x", "username": "secret"})
    for offset in (0, 10, 20, 30, 70):
        ratelimit.log_call("nominatim", "https://nominatim.openstreetmap.org/search", 200, now + timedelta(seconds=offset), 0.2, ua)
    ratelimit.log_call("geonames", "http://api.geonames.org/searchJSON", 200, now, 0.2, "python-httpx/0.28")
    ratelimit.log_geocode("gazetteer", "bologna", "ITA", hit=True, cached=False, at=now)
    ratelimit.log_geocode("gazetteer", "bologna", "ITA", hit=True, cached=True, at=now)
    ratelimit.log_geocode("nominatim", "nowhere", "", hit=False, cached=False, at=now)
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 13 and lines[0]["params"]["username"] == "<set>"
    report = ratelimit.limits_report(now - timedelta(minutes=1))
    assert report["gdelt"]["calls"] == 4 and report["gdelt"]["min_spacing_s"] == 5.0 and report["gdelt"]["user_agent_ok"]
    assert report["nominatim"]["max_per_minute"] == 4 and report["nominatim"]["max_per_hour"] == 5
    assert report["geonames"]["user_agent_ok"] is False
    geo = ratelimit.geocode_report(now - timedelta(minutes=1))
    assert geo["lookups"] == 3 and geo["cache_hits"] == 1 and geo["hit_rate"] == pytest.approx(1 / 3)
    assert geo["by_provider"]["gazetteer"] == {"lookups": 2, "cached": 1, "found": 2}
    ages = ratelimit.recent_call_ages("gdelt", 3600, now=now + timedelta(seconds=100))
    assert len(ages) == 4 and all(0 <= a < 3600 for a in ages)
    assert ratelimit.read(now + timedelta(days=1)) == []
    assert ratelimit.max_calls_in_window([], 60) == 0 and ratelimit.min_spacing_s([]) is None
