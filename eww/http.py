"""One httpx client for the whole package: identifying User-Agent, timeouts, bounded retries."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from eww import config

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=config.HTTP_TIMEOUT_S,
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
