"""The hazard lexicon: English and Italian terms, negative patterns, confidence, country hints."""

import pytest

from eww import config, lexicon


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Alluvione in Emilia-Romagna: Bologna sott'acqua, esondato il Reno", "flood"),
        ("Flash floods sweep away homes in Assam after days of rain", "flood"),
        ("Hurricane Norbert makes landfall in Mexico as a Category 3 storm", "tropical_cyclone"),
        ("Il tifone Saudel si abbatte sulle Filippine", "tropical_cyclone"),
        ("Wildfire forces evacuation of 2,000 near Huelva", "wildfire"),
        ("Incendio boschivo a Brac: 300 ettari bruciati", "wildfire"),
        ("Terremoto di magnitudo 5.8 in Turchia", "earthquake"),
        ("Deadly landslide buries village after heavy rains", "landslide"),
        ("Etna in eruzione: nube di cenere su Catania", "volcano"),
        ("Drought emergency declared across three counties", "drought"),
        ("Ondata di calore: bollino rosso in dieci città", "heatwave"),
        ("Blizzard shuts highways across the plains", "coldwave"),
        ("Tsunami warning lifted after waves hit the coast", "tsunami"),
    ],
)
def test_strong_terms_classify(text, expected):
    result = lexicon.classify(text)
    assert result.hazard_type == expected, result
    assert result.confidence >= 0.9


@pytest.mark.parametrize(
    "text",
    [
        "A flood of complaints hits the council",
        "Minister weathers a storm of criticism over the budget",
        "Landslide victory for the opposition in Sunday's vote",
        "Political earthquake as coalition collapses",
        "A tsunami of lawsuits follows the recall",
        "Striker ends his goal drought with a hat-trick",
        "Terremoto politico nel partito dopo le dimissioni",
        "Under fire: the coach faces the press",
    ],
)
def test_negative_patterns_veto_metaphors(text):
    result = lexicon.classify(text)
    assert result.hazard_type is None, result
    assert result.vetoed


def test_weak_terms_give_low_confidence_and_prefer_breaks_ties():
    weak = lexicon.classify("Heavy rain forecast for the weekend")
    assert weak.hazard_type == "flood" and weak.confidence == config.LEXICON_CONFIDENCE["weak"]
    tie = lexicon.classify("Storm damage and flooding reported along the coast")
    assert tie.hazard_type in ("flood", "severe_storm")
    assert lexicon.classify("Storm damage and flooding reported along the coast", prefer="severe_storm").hazard_type == "severe_storm"
    assert lexicon.classify("Storm damage and flooding reported along the coast", prefer="flood").hazard_type == "flood"
    mixed = lexicon.classify("Tsunami warning lifted after 7.1 quake")
    assert mixed.hazard_type == "tsunami" and mixed.confidence == 0.8  # a strong term of another class costs the ambiguity penalty
    assert lexicon.classify("").hazard_type is None and lexicon.classify(None).confidence == 0.0


def test_country_hints_in_both_languages():
    assert lexicon.country_hints("Italian firefighters battle a wildfire near Huelva, Spain; Nepali officials watch the rivers") == ["ITA", "ESP", "NPL"]
    assert lexicon.country_hints("Alluvione in Emilia-Romagna: la Protezione civile italiana in campo") == ["ITA"]
    assert lexicon.country_hints("Nothing geographic here") == []
    assert lexicon.country_hints("Floods in the Democratic Republic of Congo and in Congo") == ["COD", "COG"]


def test_every_hazard_has_query_terms_and_known_names():
    lex = lexicon.load()
    for hazard in lex.hazards():
        assert hazard in config.HAZARD_TYPES
        assert lex.query_terms(hazard), hazard
    assert "other" not in lex.hazards()
    assert lexicon.fold("Siccità à Bologna ’26") == "siccita a bologna '26"
