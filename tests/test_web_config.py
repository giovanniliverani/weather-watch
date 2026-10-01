"""web/src/config.ts repeats a few values that Python owns; these tests fail when the two drift apart."""

from __future__ import annotations

import re
from pathlib import Path

from eww import api, config

# A regex, not a TypeScript parser: the values are plain literals on one line each.
CONFIG_TS = (Path(__file__).resolve().parents[1] / "web" / "src" / "config.ts").read_text(encoding="utf-8")


def test_default_window_matches_the_viewer_default():
    assert int(re.search(r"^export const DEFAULT_DAYS = (\d+)$", CONFIG_TS, re.M).group(1)) == config.DEFAULT_VIEWER_DAYS


def test_page_origins_match_the_cors_origins():
    origins = re.search(r"^export const API_PAGE_ORIGINS = \[(.*)\]$", CONFIG_TS, re.M).group(1)
    assert re.findall(r"'([^']+)'", origins) == config.SERVE_CORS_ORIGINS


def test_every_basemap_credit_is_an_attribution_entry(conn):
    credits = {name: text for name, _, text in re.findall(r"^const (\w+_CREDIT) = (['\"])(.*)\2$", CONFIG_TS, re.M)}
    assert {"OPENFREEMAP_CREDIT", "GIBS_CREDIT", "ESRI_CREDIT"} <= set(credits)
    # Every basemap's credit line is built from those constants, so checking them covers the menu.
    values = [v.strip() for v in re.findall(r"\battribution: (`[^`\n]*`|[^,}\n]+)", CONFIG_TS) if not v.startswith("string")]  # skip the type
    assert len(values) >= 7, values  # five OpenFreeMap styles, Esri, GIBS
    for value in values:
        assert re.fullmatch(r"\w+_CREDIT|`[^`]*\$\{\w+_CREDIT\}`", value), value
    attributions = [entry["attribution"] for entry in api.attributions(conn=conn)]
    for name, text in credits.items():
        assert text in attributions, name
