"""Surrogate keys. Every table uses ULIDs: sortable, generated in Python, no coordination."""

from __future__ import annotations

from ulid import ULID


def new_id() -> str:
    """Return a fresh 26-character ULID string."""
    return str(ULID())
