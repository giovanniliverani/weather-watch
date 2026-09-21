"""One httpx client for the whole package, and (M3) one rate-limited `Provider` per remote service.

`client()`, `get()` and `get_json()` are what the spine collectors use: identifying User-Agent, timeouts,
bounded retries on transport errors, 429 and 5xx.

`Provider` wraps a client for the enrichment and geocoding services (GDELT, ReliefWeb, GeoNames,
Nominatim). Before every request it waits on the provider's limiters (eww.ratelimit), after every
request it appends one line to the structured provider log, and a 429 is never retried: it raises
RateLimitExceeded so the caller stops calling that service for the rest of the run.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

import httpx

from eww import config, ratelimit
from eww.clock import now_utc

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def client(timeout: float | None = None) -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=timeout or config.HTTP_TIMEOUT_S,
        follow_redirects=True,
    )


def get(http: httpx.Client, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    """GET with retries on transport errors, 429 and 5xx. Raises httpx.HTTPStatusError on other 4xx/5xx."""
    last_exc: Exception | None = None
    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            response = http.get(url, params=params)
        except httpx.TransportError as exc:  # timeouts, connection resets, DNS
            last_exc = exc
            log.warning("http transport error url=%s attempt=%d error=%s", url, attempt, exc)
        else:
            if response.status_code in RETRY_STATUSES and attempt < config.HTTP_RETRIES:
                log.warning("http retryable status url=%s status=%d attempt=%d", url, response.status_code, attempt)
                last_exc = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
            else:
                response.raise_for_status()
                return response
        time.sleep(config.HTTP_RETRY_BACKOFF_S * attempt)
    assert last_exc is not None
    raise last_exc


def get_json(http: httpx.Client, url: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """GET and decode JSON. Returns (status, body); body is None for 204 or an empty body."""
    response = get(http, url, params)
    if response.status_code == 204 or not response.content.strip():
        return response.status_code, None
    return response.status_code, response.json()


# ----------------------------------------------------------------------------- rate-limited providers (M3)
class Provider:
    """A remote service with its limiters and its line in the structured log.

    `limiters` are waited on in order before each request and marked after it; a SlidingWindow with a
    budget raises RateLimitExceeded when the budget is spent. Transport errors and 5xx are retried
    `retries` times with a backoff, each attempt waiting on the limiters again, so retries can never
    breach a limit. A 429 raises RateLimitExceeded at once.
    """

    def __init__(
        self,
        name: str,
        *,
        limiters: Iterable = (),
        http: httpx.Client | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        sleep=time.sleep,
    ):
        self.name = name
        self.limiters = list(limiters)
        self._own = http is None
        self.http = http or client(timeout)
        self.retries = config.HTTP_RETRIES if retries is None else retries
        self._sleep = sleep
        self.calls = 0

    def close(self) -> None:
        if self._own:
            self.http.close()

    @property
    def user_agent(self) -> str:
        return str(self.http.headers.get("User-Agent", ""))

    def request(self, method: str, url: str, *, params: dict | None = None, json: Any = None) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            waited = sum(limiter.wait() for limiter in self.limiters)  # RateLimitExceeded when a budget is spent
            started = now_utc()
            t0 = time.monotonic()
            status: int | None = None
            error: str | None = None
            response: httpx.Response | None = None
            try:
                response = self.http.request(method, url, params=params, json=json)
                status = response.status_code
            except httpx.TransportError as exc:
                last_exc = exc
                error = f"{type(exc).__name__}: {exc}"[:200]
            finally:
                for limiter in self.limiters:
                    limiter.mark()
                self.calls += 1
                ratelimit.log_call(self.name, url, status, started, time.monotonic() - t0, self.user_agent, params=params, error=error, waited_s=waited)
            if response is None:
                log.warning("%s transport error attempt=%d error=%s", self.name, attempt, error)
            elif status == 429:
                raise ratelimit.RateLimitExceeded(f"{self.name} answered 429; stopping {self.name} for this run")
            elif status is not None and status >= 500 and attempt < self.retries:
                log.warning("%s status=%d attempt=%d; retrying", self.name, status, attempt)
            else:
                return response
            self._sleep(config.HTTP_RETRY_BACKOFF_S * attempt)
        assert last_exc is not None
        raise last_exc

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        return self.request("GET", url, params=params)

    def get_json(self, url: str, params: dict | None = None) -> tuple[int, Any]:
        """(status, decoded body); body is None for an empty body. Raises ValueError when the body is not JSON."""
        response = self.get(url, params)
        return response.status_code, _decode(response)

    def post_json(self, url: str, *, params: dict | None = None, json: Any = None) -> tuple[int, Any]:
        response = self.request("POST", url, params=params, json=json)
        return response.status_code, _decode(response)


def _decode(response: httpx.Response) -> Any:
    text = response.text
    if response.status_code == 204 or not text.strip():
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"not JSON (status {response.status_code}): {text.strip()[:200]}") from exc
