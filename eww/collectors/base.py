"""Types shared by every collector module."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FetchResult:
    """What one fetch produced, kept alongside the raw items so the snapshot envelope is complete."""

    items: list[dict]
    requests: list[dict] = field(default_factory=list)  # {"url", "params", "status", "items"} per HTTP call
    http_status: int | None = None  # the last status seen, for collector_run.http_status
    status: str = "ok"  # 'ok' | 'partial' (fallback used or some requests failed)
    error: str | None = None
