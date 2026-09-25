"""Extraction, embedding and attachment end to end with a deterministic stand-in encoder and NER (no torch, no spaCy)."""

import json
import re
from datetime import timedelta

import numpy as np
import pytest

from eww import api, attach, config, documents, embed, extract, geocode, matching, resolve, review
from eww.clock import now_utc, parse_iso, to_iso
from tests.conftest import copernicus_item, gdacs_item, ingest_items
from tests.test_geocode import dump  # noqa: F401  (the tiny GeoNames folder)

T0 = "2026-09-16T15:10:00Z"
KNOWN_PLACES = ["Kathmandu", "Nepal", "Bologna", "Emilia-Romagna", "Italy", "Huelva", "Spain", "Paris", "France"]


class BagEncoder:
    """Deterministic unit vectors from word hashes: texts sharing words are close, unrelated ones are not."""

    model = "bag-of-words-test"
    dim = 64

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=embed.DTYPE)
        for i, text in enumerate(texts):
            for word in re.findall(r"[a-z]{3,}", (text or "").lower()):
                out[i, hash(word) % self.dim] += 1.0
            out[i] = embed.unit(out[i])
        return out


def fake_ner(text, language):
    return [name for name in KNOWN_PLACES if re.search(rf"\b{re.escape(name)}\b", text or "")]


@pytest.fixture(autouse=True)
def stand_ins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_LOG", tmp_path / "logs" / "providers.jsonl")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    embed.set_encoder(BagEncoder())
    extract.set_ner(fake_ner)
    yield
    embed.set_encoder(None)
    extract.set_ner(None)


def prepared(conn, data_dir, dump):
    geocode.load_geonames(conn, dump)
    flood = gdacs_item(1104124, "FL", "Flood in Nepal", 27.8, 85.3, "2026-09-10T00:00:00", todate="2026-09-16T00:00:00", alertlevel="Red", iso3="NPL", glide="FL-2026-000124-NPL")
    fire = copernicus_item("EMSR940", "Wildfire in Huelva Province, Spain", "Wildfire", -6.9, 37.3, "2026-09-12T13:00:00", "2026-09-12T16:58:00", countries=("Spain",), closed=False)
    storm = gdacs_item(1000456, "TC", "Tropical Cyclone SAUDEL-26", 20.0, 125.0, "2026-09-12T00:00:00", todate="2026-09-16T00:00:00", alertlevel="Orange", eventname="SAUDEL-26", iso3="PHL")
    ingest_items(conn, data_dir, "gdacs", [flood, storm], T0)
    ingest_items(conn, data_dir, "copernicus", [fire], T0)
    resolve.resolve(conn)
    return {row["title"]: row for row in conn.execute("SELECT * FROM event")}


def add_document(conn, url, title, published_at, event_id=None, source_id="gdelt", excerpt=None, publisher=None, media_url=None, language="English"):
    result = documents.upsert_document(conn, source_id=source_id, kind="article" if source_id == "gdelt" else "report", url=url, title=title, text_excerpt=excerpt, language=language, publisher=publisher, published_at=published_at, media_url=media_url, media_kind="image" if media_url else "none", payload={"title": title})
    if event_id:
        documents.record_retrieval(conn, result.document_id, event_id, source_id)
    conn.commit()
    return result.document_id


def pipeline(conn, **kwargs):
    extract.run(conn, geocoder=geocode.Geocoder(conn, tiers=["gazetteer"]))
    embed.embed_documents(conn)
    return attach.run(conn, **kwargs)


def test_extract_classifies_locates_and_records_places(conn, data_dir, dump):
    events = prepared(conn, data_dir, dump)
    nepal = events["Flood in Nepal"]["event_id"]
    located = add_document(conn, "https://a.example.com/1", "Floods kill 12 as rivers burst their banks in Kathmandu, Nepal", "2026-09-14T10:00:00Z", nepal)
    metaphor = add_document(conn, "https://a.example.com/2", "A flood of complaints over Kathmandu bus fares", "2026-09-14T10:00:00Z", nepal)
    unlocated = add_document(conn, "https://a.example.com/3", "Flooding leaves thousands stranded, aid agencies say", "2026-09-14T10:00:00Z", nepal)
    italian = add_document(conn, "https://a.example.com/4", "Alluvione a Bologna: l'Emilia-Romagna chiede lo stato di emergenza", "2026-09-14T10:00:00Z", language="Italian")
    stats = extract.run(conn, geocoder=geocode.Geocoder(conn, tiers=["gazetteer"]))
    assert (stats.documents, stats.classified, stats.located) == (4, 3, 2)
    row = extract.read_extraction(conn, located)
    assert row["hazard_type"] == "flood" and row["method"] == "lexicon+ner" and row["event_date"] == "2026-09-14T10:00:00Z"
    places = json.loads(row["places"])
    assert [p["name"] for p in places] == ["Kathmandu", "Nepal"]
    assert places[0]["precision"] == "city" and places[0]["provider"] == "gazetteer" and places[0]["country_iso3"] == "NPL" and places[0]["country_hint"] == "NPL"
    assert places[1]["precision"] == "country"
    assert extract.read_extraction(conn, metaphor)["hazard_type"] is None
    assert json.loads(extract.read_extraction(conn, unlocated)["places"]) == []  # no NER hit and no country in the text
    italian_row = extract.read_extraction(conn, italian)
    assert italian_row["hazard_type"] == "flood" and {p["name"] for p in json.loads(italian_row["places"])} == {"Bologna", "Emilia-Romagna"}
    assert extract.run(conn, geocoder=geocode.Geocoder(conn, tiers=["gazetteer"])).documents == 0  # idempotent


def test_attach_end_to_end_with_mentions_news_and_rebuild(conn, data_dir, dump):
    events = prepared(conn, data_dir, dump)
    nepal, spain = events["Flood in Nepal"]["event_id"], events["Wildfire in Huelva Province, Spain"]["event_id"]
    d1 = add_document(conn, "https://a.example.com/1", "Nepal floods: rivers burst their banks in Kathmandu, 12 dead", "2026-09-14T10:00:00Z", nepal, publisher="a.example.com", media_url="https://img.example.com/1.jpg")
    d2 = add_document(conn, "https://b.example.com/1", "Nepal floods: rivers burst their banks in Kathmandu, 12 dead", "2026-09-14T11:00:00Z", nepal, publisher="b.example.com")  # a syndicated copy
    d3 = add_document(conn, "https://c.example.com/1", "Wildfire near Huelva forces evacuations in Spain", "2026-09-13T08:00:00Z", spain, publisher="c.example.com")
    d4 = add_document(conn, "https://d.example.com/1", "A flood of complaints over Kathmandu bus fares", "2026-09-14T10:00:00Z", nepal)
    d5 = add_document(conn, "https://e.example.com/1", "Kathmandu marathon draws record crowd despite the flood season", "2026-09-14T10:00:00Z", nepal)
    stats = pipeline(conn)
    assert stats.no_hazard == 0  # documents without a hazard never reach attach
    decisions = {row["document_id"]: dict(row) for row in conn.execute("SELECT * FROM event_document")}
    assert decisions[d1]["event_id"] == nepal and decisions[d1]["status"] == "attached" and decisions[d1]["method"] == "query" and decisions[d1]["decided_by"] == "pipeline"
    assert decisions[d2]["status"] == "attached" and decisions[d3]["event_id"] == spain and decisions[d3]["status"] == "attached"
    assert d4 not in decisions
    parts = json.loads(decisions[d1]["score_parts"])
    assert parts["spatial"] == 1.0 and parts["temporal"] == 1.0 and parts["query_prior"] == 0.1 and parts["place"] == "Kathmandu" and parts["hazard"] == "flood"
    assert decisions[d1]["score"] == pytest.approx(min(1.0, 0.45 + 0.25 + 0.30 * parts["text"] + 0.10), abs=1e-3)
    mentions = conn.execute("SELECT * FROM event_geometry WHERE role = 'mention' ORDER BY document_id").fetchall()
    assert {m["document_id"] for m in mentions} >= {d1, d2, d3} and all(m["event_id"] in (nepal, spain) and m["is_primary"] == 0 for m in mentions)
    kath = next(m for m in mentions if m["document_id"] == d1 and m["precision"] == "city")
    assert json.loads(kath["geojson"])["coordinates"] == [85.3206, 27.70169] and kath["source_id"] == "gdelt"
    # the GeoJSON contract counts them and the News tab groups the syndicated copies into one line
    feature = next(f for f in api.events_geojson("2026-01-01T00:00:00Z", conn=conn)["features"] if f["properties"]["event_id"] == nepal)
    assert feature["properties"]["doc_count"] >= 2 and feature["properties"]["thumbnail_url"] == "https://img.example.com/1.jpg"
    news = api.event_documents(nepal, conn=conn)
    copy = next(item for item in news if item["document_id"] in (d1, d2))
    assert copy["copies"] == 2 and copy["publishers"] == ["a.example.com", "b.example.com"] and copy["media_url"] == "https://img.example.com/1.jpg"
    assert all(item["decided_by"] == "pipeline" for item in news)
    # a second incremental run changes nothing; a rebuild reproduces the same rows
    assert attach.run(conn).documents == 0
    before = [tuple(r) for r in conn.execute("SELECT event_id, document_id, status, score, score_parts, method, decided_by FROM event_document ORDER BY 1, 2")]
    rebuilt = attach.run(conn, rebuild=True)
    assert rebuilt.rebuilt_rows_deleted == len(before) and rebuilt.documents >= len(before)
    after = [tuple(r) for r in conn.execute("SELECT event_id, document_id, status, score, score_parts, method, decided_by FROM event_document ORDER BY 1, 2")]
    assert after == before
    coverage = attach.coverage(conn, min_severity=0.66, days=14, min_documents=2, now=parse_iso("2026-09-17T00:00:00Z"))
    assert coverage["events"] == 3 and coverage["covered"] == 1


def test_candidates_go_to_review_and_human_decisions_survive_a_rebuild(conn, data_dir, dump, monkeypatch):
    events = prepared(conn, data_dir, dump)
    nepal = events["Flood in Nepal"]["event_id"]
    # thresholds raised so the same Kathmandu headline lands in the grey zone
    monkeypatch.setitem(config.ATTACHMENT, "attach_threshold", 0.99)
    monkeypatch.setitem(config.ATTACHMENT, "candidate_threshold", 0.55)
    d1 = add_document(conn, "https://a.example.com/1", "Nepal floods: rivers burst their banks in Kathmandu, 12 dead", "2026-09-14T10:00:00Z", nepal)
    d2 = add_document(conn, "https://b.example.com/1", "Kathmandu residents count the cost of the Nepal flooding", "2026-09-15T10:00:00Z", nepal)
    stats = pipeline(conn)
    assert stats.candidates == 2 and stats.attached == 0
    listed = review.candidate_attachments(conn)
    assert [c["document_id"] for c in listed] and all(c["event_id"] == nepal and c["parts"]["place"] == "Kathmandu" for c in listed)
    assert review.counts(conn)["candidate_attachments"] == 2
    review.accept_attachment(nepal, d1, conn)
    review.reject_attachment(nepal, d2, conn)
    rows = {r["document_id"]: dict(r) for r in conn.execute("SELECT * FROM event_document")}
    assert rows[d1]["status"] == "attached" and rows[d1]["decided_by"] == "human" and rows[d1]["method"] == "human"
    assert rows[d2]["status"] == "rejected" and rows[d2]["decided_by"] == "human"
    assert conn.execute("SELECT COUNT(*) FROM event_geometry WHERE role = 'mention' AND document_id = ?", (d1,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM event_geometry WHERE role = 'mention' AND document_id = ?", (d2,)).fetchone()[0] == 0
    assert [item["document_id"] for item in api.event_documents(nepal, conn=conn)] == [d1]
    rebuilt = attach.run(conn, rebuild=True)
    assert rebuilt.rebuilt_rows_deleted == 0
    again = {r["document_id"]: dict(r) for r in conn.execute("SELECT * FROM event_document")}
    assert again == rows  # human decisions are data, not derived
    with pytest.raises(ValueError):
        attach.human_decide(conn, nepal, "nope", "attached")


def test_no_place_rule_needs_similar_text_and_unrelated_headlines_stay_off(conn, data_dir, dump):
    events = prepared(conn, data_dir, dump)
    nepal = events["Flood in Nepal"]["event_id"]
    similar = add_document(conn, "https://a.example.com/1", "Flood in Nepal: flood waters rise, Nepal flood toll grows", "2026-09-14T10:00:00Z", nepal)
    weak = add_document(conn, "https://b.example.com/1", "Flash floods reported after heavy rain, officials say", "2026-09-14T10:00:00Z", nepal)
    stale = add_document(conn, "https://c.example.com/1", "Flood in Nepal: flood waters rise, Nepal flood toll grows", "2026-11-14T10:00:00Z", nepal)  # two months later
    pipeline(conn)
    rows = {r["document_id"]: dict(r) for r in conn.execute("SELECT * FROM event_document")}
    assert similar in rows and json.loads(rows[similar]["score_parts"])["no_place"] is False  # "Nepal" resolves to the country row
    assert weak not in rows  # no place, text below the 0.60 floor
    assert stale not in rows  # outside every event window


def test_purge_keeps_attached_and_candidate_documents(conn, data_dir, dump):
    events = prepared(conn, data_dir, dump)
    nepal = events["Flood in Nepal"]["event_id"]
    old = to_iso(now_utc() - timedelta(days=90))
    kept = add_document(conn, "https://a.example.com/1", "Nepal floods: rivers burst their banks in Kathmandu, 12 dead", "2026-09-14T10:00:00Z", nepal)
    gone = add_document(conn, "https://b.example.com/1", "Rain forecast for Kathmandu markets", old, nepal)
    conn.execute("UPDATE document SET fetched_at = ? WHERE document_id = ?", (old, gone))
    conn.commit()
    pipeline(conn)
    stats = documents.purge_unattached(conn, 60)
    assert stats.documents == 1
    assert {r[0] for r in conn.execute("SELECT document_id FROM document")} == {kept}


def test_embedding_title_similarity_hook(monkeypatch):
    monkeypatch.setattr(config, "TITLE_SIMILARITY", "embedding")
    assert matching.title_similarity("Flood in Nepal", "Flood in Nepal 1104124") == 1.0
    assert matching.title_similarity("Flood in Nepal", "Wildfire in Spain") < 0.6
    assert matching.title_similarity("", "x") == 0.0
    vectors = {"a": np.array([1.0, 0.0], dtype=embed.DTYPE), "b": np.array([0.999, 0.04], dtype=embed.DTYPE), "c": np.array([0.0, 1.0], dtype=embed.DTYPE)}
    assert embed.similarity_groups(vectors, 0.95) == [["a", "b"], ["c"]]
    assert embed.from_blob(embed.to_blob(vectors["b"]), 2).tolist() == pytest.approx([0.999, 0.04], abs=1e-6)
