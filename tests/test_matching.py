"""Storm-name normalisation, title similarity and the scoring rules on synthetic entities."""

import pytest

from eww import config, geo, matching
from eww.matching import Entity


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NORBERT-26", "norbert"),
        ("Tropical Cyclone NORBERT-26", "norbert"),
        ("Hurricane Norbert", "norbert"),
        ("Cyclone Norbert", "norbert"),
        ("Tropical Storm Bang-Lang", "banglang"),
        ("FIFTEEN-E-26", "fifteene"),
        ("Typhoon Saudel", "saudel"),
        ("Tropical Cyclone GEZANI-26", "gezani"),
        ("Post-Tropical Cyclone Lee", "lee"),
        ("", None),
        (None, None),
        ("TS", None),
    ],
)
def test_normalise_storm_name(raw, expected):
    assert matching.normalise_storm_name(raw) == expected


def test_title_tokens_ignore_numbers_and_stopwords():
    assert matching.tokens("Flood in Croatia 1104153") == {"flood", "croatia"}
    assert matching.title_similarity("Flood in Croatia", "Flood in Croatia 1104153") == 1.0
    assert matching.title_similarity("Tropical Cyclone NORBERT-26", "Hurricane Norbert") == 0.25
    assert matching.title_similarity("", "Flood in Croatia") == 0.0
    assert matching.jaccard(set(), set()) == 0.0


def test_title_similarity_hook_rejects_unknown_methods(monkeypatch):
    monkeypatch.setattr(config, "TITLE_SIMILARITY", "levenshtein")
    with pytest.raises(NotImplementedError):
        matching.title_similarity("a", "b")


def entity(hazard, lat, lon, started, title, **kwargs):
    return Entity(hazard_type=hazard, started_at=started, lat=lat, lon=lon, title=title, **kwargs)


def test_hazard_classes_and_blocking():
    assert matching.hazard_class("tropical_cyclone") == matching.hazard_class("severe_storm") == "storm"
    assert matching.hazard_class("flood") == "flood" and matching.hazard_class("other") is None
    assert set(matching.class_members("severe_storm")) == {"tropical_cyclone", "severe_storm"}
    assert matching.blocking_for("flood") == (250.0, 5.0) and matching.blocking_for("other") is None


def test_weighted_score_matches_the_formula():
    a = entity("flood", 48.2, 14.3, "2026-09-01T00:00:00Z", "Flood in Austria", source_ids=frozenset({"gdacs"}))
    b = entity("flood", 48.9, 15.3, "2026-09-03T00:00:00Z", "Flood in Austria 999", source_ids=frozenset({"eonet"}))
    s = matching.score(a, b)
    distance = geo.haversine_km(48.2, 14.3, 48.9, 15.3)
    expected = 0.5 * (1 - distance / 250) + 0.3 * (1 - 2 / 5) + 0.2 * 1.0
    assert s.rule == "weighted" and abs(s.score - expected) < 1e-3
    assert s.distance_km == round(distance, 1) and s.days_apart == 2.0 and s.text_sim == 1.0
    assert config.PROPOSAL_THRESHOLD <= s.score < config.AUTO_MERGE_THRESHOLD
    evidence = matching.evidence(a, b, s)
    assert evidence["title_a"] == "Flood in Austria" and evidence["source_b"] == ["eonet"] and evidence["radius_km"] == 250.0


def test_named_storm_rule_and_glide_rule():
    a = entity("tropical_cyclone", 20.5, -143.1, "2026-09-09T21:00:00Z", "Tropical Cyclone NORBERT-26", storm_name="norbert")
    b = entity("severe_storm", 21.0, -142.0, "2026-09-10T06:00:00Z", "Cyclone Norbert", storm_name="norbert")
    s = matching.score(a, b)
    assert s.rule == "storm_name" and s.score == config.SCORE_STORM_NAME_EQUAL and s.name_match
    late = entity("tropical_cyclone", 21.0, -142.0, "2026-09-25T06:00:00Z", "Cyclone Norbert", storm_name="norbert")
    assert not matching.score(a, late).name_match  # outside T: names alone do not decide
    g1 = entity("flood", 10, 10, "2026-09-01T00:00:00Z", "Flood A", glide_number="FL-2026-000001-XXX")
    g2 = entity("flood", 10.5, 10.5, "2026-09-02T00:00:00Z", "Flood B", glide_number="FL-2026-000001-XXX")
    assert matching.score(g1, g2).rule == "glide" and matching.score(g1, g2).score == 1.0


def test_distance_is_the_closest_pair_of_positions_and_named_storms_block_wide():
    track = entity("tropical_cyclone", 23.0, -145.7, "2026-08-27T18:00:00Z", "Hurricane Karina", storm_name="karina", positions=((10.8, -111.5), (23.0, -145.7)))
    point = entity("tropical_cyclone", 23.1, -146.2, "2026-08-27T21:00:00Z", "Tropical Cyclone KARINA-26", storm_name="karina")
    assert matching.min_distance_km(track, point) < 60
    assert matching.blocking_radius(track) == config.STORM_NAME_BLOCK_KM
    assert matching.blocking_radius(entity("tropical_cyclone", 0, 0, None, "Tropical Cyclone TWENTYNINE-26")) == 500.0
    assert matching.blocking_radius(entity("flood", 0, 0, None, "Flood", storm_name="karina")) == 250.0  # names only matter in the storm class
    assert matching.blocking_radius(entity("other", 0, 0, None, "x")) is None


def test_key_conflict():
    gdacs_fire = entity("wildfire", 60.0, 100.0, "2026-09-01T00:00:00Z", "Forest fires in Russian Federation", source_ids=frozenset({"gdacs"}), external_ids=frozenset({("gdacs", "1031202")}))
    mirror_of_other = entity("wildfire", 60.01, 100.01, "2026-09-01T05:00:00Z", "Wildfire in Russian Federation 1031315", source_ids=frozenset({"eonet"}), linked_ids=frozenset({("gdacs", "1031315")}))
    mirror_of_same = entity("wildfire", 60.01, 100.01, "2026-09-01T05:00:00Z", "Wildfire in Russian Federation 1031202", source_ids=frozenset({"eonet"}), linked_ids=frozenset({("gdacs", "1031202")}))
    unlinked = entity("wildfire", 60.01, 100.01, "2026-09-01T05:00:00Z", "Wildfire Snow, Custer, Montana", source_ids=frozenset({"eonet"}))
    assert matching.key_conflict(gdacs_fire, mirror_of_other) and matching.key_conflict(mirror_of_other, gdacs_fire)
    assert not matching.key_conflict(gdacs_fire, mirror_of_same) and not matching.key_conflict(gdacs_fire, unlinked)


def test_different_classes_score_nothing():
    a = entity("flood", 10, 10, "2026-09-01T00:00:00Z", "Flood in X")
    b = entity("wildfire", 10, 10, "2026-09-01T00:00:00Z", "Flood in X")
    s = matching.score(a, b)
    assert s.rule == "none" and s.score == 0.0


def test_far_or_late_pairs_score_low():
    a = entity("flood", 10, 10, "2026-09-01T00:00:00Z", "Flood in X")
    far = entity("flood", 12.5, 10, "2026-09-01T00:00:00Z", "Flood in Y")  # ~278 km > R
    assert matching.score(a, far).spatial == 0.0
    late = entity("flood", 10, 10, "2026-09-09T00:00:00Z", "Flood in X")
    assert matching.score(a, late).temporal == 0.0 and matching.score(a, late).score == pytest.approx(0.5 + 0.2, abs=1e-6)
