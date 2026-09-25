"""M4: validation, the budget cap, the batch id match, summaries, and the golden-set score."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from eww import config, documents, llm, ratelimit, resolve
from eww.clock import to_iso
from eww.geocode import Place
from tests.conftest import gdacs_item, ingest_items


class StubGeo:
    def lookup(self, name, hint, remote=True):
        if name.casefold() == "bologna":
            return Place("Bologna", 44.49, 11.34, "city", "gazetteer", "ITA", "Bologna, IT")
        return None

    def close(self):
        return None


def _extraction(**overrides):
    payload = {
        "hazard_type": "flood",
        "places": [{"name": "Bologna", "country_hint": "ITA"}],
        "event_date": None,
        "figures": {"dead": {"value": 3, "evidence_span": "3 dead"}, "injured": None, "missing": None, "displaced": None},
        "confidence": 0.8,
    }
    payload.update(overrides)
    return llm.Extraction.model_validate(payload)


def test_cost_matches_the_architecture_example():
    # 1,500 cached + 800 uncached input + 150 output, batch 0.5, cache read 0.1: $0.0017
    got = llm.cost_usd(input_tokens=800, output_tokens=150, cache_read_tokens=1500, cache_write_tokens=0)
    assert got == pytest.approx(0.0017)
    write = llm.cost_usd(input_tokens=0, output_tokens=0, cache_write_tokens=1000)
    assert write == pytest.approx(1000 * 2 / 1_000_000 * 1.25 * 0.5)


def test_cached_prefix_clears_the_sonnet_minimum():
    assert llm.estimate_tokens(llm.system_prefix("extract")) >= config.LLM_PREFIX_MIN_TOKENS
    assert llm.estimate_tokens(llm.system_prefix("summarise")) >= config.LLM_PREFIX_MIN_TOKENS


def test_a_figure_without_a_verbatim_span_becomes_null():
    raw = _extraction(figures={"dead": {"value": 3, "evidence_span": "three people died"}, "injured": None, "missing": None, "displaced": None})
    accepted = llm.accept_extraction(
        raw, title="Flood in Bologna", excerpt="The flood in Bologna left 3 dead.",
        lexicon_hazard="flood", lexicon_confidence=0.9, candidates=[{"hazard_type": "flood", "lat": 44.49, "lon": 11.34}],
        geocoder=StubGeo(),
    )
    assert accepted.figures["dead"] is None
    assert accepted.hazard_type == "flood"


def test_a_place_is_kept_only_inside_twice_the_blocking_radius():
    raw = _extraction()
    near = [{"hazard_type": "flood", "lat": 44.49, "lon": 11.34}]
    far = [{"hazard_type": "flood", "lat": 0.0, "lon": 0.0}]
    kept = llm.accept_extraction(raw, title="t", excerpt="The flood in Bologna left 3 dead.", lexicon_hazard=None, lexicon_confidence=None, candidates=near, geocoder=StubGeo())
    dropped = llm.accept_extraction(raw, title="t", excerpt="The flood in Bologna left 3 dead.", lexicon_hazard=None, lexicon_confidence=None, candidates=far, geocoder=StubGeo())
    assert kept.places and kept.places[0]["name"] == "Bologna"
    assert dropped.places == []
    assert kept.figures["dead"]["value"] == 3
    assert kept.figures["dead"]["evidence_span"] == "3 dead"


def test_a_lexicon_hazard_is_kept_when_the_model_disagrees():
    raw = _extraction(hazard_type="earthquake")
    accepted = llm.accept_extraction(
        raw, title="Flood", excerpt="The flood in Bologna left 3 dead.",
        lexicon_hazard="flood", lexicon_confidence=0.9, candidates=[], geocoder=StubGeo(),
    )
    assert accepted.hazard_type == "flood"


def test_numbered_summary_sentences_without_a_span_are_dropped():
    raw = llm.Summary.model_validate({
        "sentences": [
            {"text": "The flood left 3 dead.", "evidence_span": "not in the source"},
            {"text": "Copernicus mapped the flood.", "evidence_span": None},
        ],
        "figures": {"dead": {"value": 3, "evidence_span": "3 dead"}, "injured": None, "missing": None, "displaced": None},
    })
    accepted = llm.accept_summary(raw, ["GDACS reports the flood left 3 dead."])
    assert [sentence.text for sentence in accepted.sentences] == ["Copernicus mapped the flood."]
    assert accepted.figures.dead.value == 3


def test_cap_zero_never_constructs_a_cloud_call(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_BUDGET_USD", 0)

    class Sentinel:
        def __getattr__(self, name):
            raise AssertionError(f"cloud client used: {name}")

    extractor, fell_back = llm.make_extractor(conn, 1.0, backend="anthropic", anthropic_client=Sentinel(), ollama=object())
    assert fell_back and isinstance(extractor, llm.LocalExtractor)


def test_an_estimate_over_the_cap_uses_the_local_model(conn, monkeypatch, caplog):
    monkeypatch.setattr(config, "LLM_BUDGET_USD", 10)
    llm.write_call(conn, llm.Call("extract", "anthropic", "claude-sonnet-5", 10, 10, cost_usd=9), when="2026-09-02T00:00:00Z")
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert llm.budget_allows(conn, 2, now) is False
    with caplog.at_level("WARNING"):
        extractor, fell_back = llm.make_extractor(conn, 2, backend="anthropic", now=now)
    assert fell_back and isinstance(extractor, llm.LocalExtractor)
    assert "budget cap reached" in caplog.text


class _Batches:
    def __init__(self, bodies: dict[str, str]):
        self.bodies = bodies
        self.requests = []

    def create(self, requests):
        self.requests = requests
        self.ids = [item["custom_id"] for item in requests]
        return SimpleNamespace(id="msgbatch_test", processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        for custom_id in reversed(self.ids):
            usage = SimpleNamespace(input_tokens=800, output_tokens=150, cache_read_input_tokens=1500, cache_creation_input_tokens=0)
            message = SimpleNamespace(content=[SimpleNamespace(type="text", text=self.bodies[custom_id])], usage=usage)
            yield SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


def test_claude_batch_matches_custom_id_and_caches_the_prefix():
    bologna = json.dumps({
        "hazard_type": "flood", "places": [{"name": "Bologna", "country_hint": "ITA"}], "event_date": None,
        "figures": {"dead": {"value": 3, "evidence_span": "3 dead"}, "injured": None, "missing": None, "displaced": None},
        "confidence": 0.9,
    })
    lyon = json.dumps({
        "hazard_type": None, "places": [], "event_date": None,
        "figures": {"dead": None, "injured": None, "missing": None, "displaced": None}, "confidence": 0.1,
    })
    batches = _Batches({})
    extractor = llm.ClaudeBatchExtractor(SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    jobs = [
        llm.ExtractJob("doc-bologna", {"document_id": "doc-bologna", "title": "Flood in Bologna", "text_excerpt": "left 3 dead"}, []),
        llm.ExtractJob("doc-lyon", {"document_id": "doc-lyon", "title": "Library budget in Lyon", "text_excerpt": "approved"}, []),
    ]
    batches.bodies = {llm.custom_id("extract", job.document_id): body for job, body in zip(jobs, (bologna, lyon), strict=True)}
    found = extractor.extract_many(jobs)
    assert found["doc-bologna"].hazard_type == "flood"
    assert found["doc-lyon"].hazard_type is None
    params = batches.requests[0]["params"]
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["messages"] == [{"role": "user", "content": params["messages"][0]["content"]}]
    assert "budget_tokens" not in json.dumps(params)
    assert all(message["role"] != "assistant" for message in params["messages"])
    assert extractor.calls[0].cost_usd == pytest.approx(0.0017)


def _ambiguous(conn, url, title, excerpt):
    result = documents.upsert_document(conn, source_id="gdelt", kind="article", url=url, title=title, text_excerpt=excerpt, published_at="2026-09-01T00:00:00Z")
    conn.execute(
        "INSERT INTO document_extraction (document_id, method, places, extracted_at, hazard_type) VALUES (?, 'lexicon+ner', '[]', '2026-09-01T00:00:00Z', NULL)",
        (result.document_id,),
    )
    return result.document_id


def test_the_model_runs_only_for_ambiguous_documents_and_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "local")
    document_id = _ambiguous(conn, "https://news.example.com/bologna", "Flood in Bologna", "The flood in Bologna left 3 dead.")
    stats = llm.run(conn, geocoder=StubGeo(), remote=False)
    assert stats.documents == 1 and stats.backend == "local" and stats.cost_usd == 0
    row = conn.execute("SELECT method, hazard_type, figures, cost_usd FROM document_extraction WHERE document_id = ?", (document_id,)).fetchone()
    assert row["method"] == f"llm:{config.LLM_LOCAL_MODEL}"
    assert row["hazard_type"] == "flood"
    assert json.loads(row["figures"])["dead"]["evidence_span"] == "3 dead"
    assert conn.execute("SELECT backend FROM llm_call").fetchone()[0] == "local"
    again = llm.run(conn, geocoder=StubGeo(), remote=False)
    assert again.documents == 0
    assert conn.execute("SELECT COUNT(*) FROM llm_call WHERE backend = 'anthropic'").fetchone()[0] == 0


def test_cap_zero_sync_path_writes_no_anthropic_rows(conn, monkeypatch):
    monkeypatch.setattr(config, "LLM_BUDGET_USD", 0)
    _ambiguous(conn, "https://news.example.com/cap", "Flood in Bologna", "The flood in Bologna left 3 dead.")
    stats = llm.run(conn, geocoder=StubGeo(), backend="anthropic", remote=False)
    assert stats.fell_back and stats.backend == "local"
    assert conn.execute("SELECT COUNT(*) FROM llm_call WHERE backend = 'anthropic'").fetchone()[0] == 0


def test_a_summary_needs_three_new_documents_and_a_day(conn, data_dir, monkeypatch):
    monkeypatch.setattr(ratelimit, "log_geocode", lambda *args, **kwargs: None)
    ingest_items(conn, data_dir, "gdacs", [gdacs_item(1, "FL", "Flood in Nepal left 3 dead", 28.2, 85.3, "2026-09-01T00:00:00Z", iso3="NPL")], "2026-09-01T00:00:00Z")
    resolve.resolve(conn)
    event_id = conn.execute("SELECT event_id FROM event").fetchone()[0]
    ids = []
    for n in range(3):
        result = documents.upsert_document(conn, source_id="gdelt", kind="article", url=f"https://news.example.com/n{n}", title="Flood in Nepal", text_excerpt="The flood in Nepal left 3 dead.")
        ids.append(result.document_id)
        conn.execute(
            "INSERT INTO event_document (event_id, document_id, status, score, method, decided_at) VALUES (?, ?, 'attached', ?, 'embedding', '2026-09-02T00:00:00Z')",
            (event_id, result.document_id, 0.9 - n / 100),
        )
    stats = llm.run(conn, geocoder=StubGeo(), remote=False)
    assert stats.summarised == 1
    event = conn.execute("SELECT summary, summary_method, summary_updated_at, summary_evidence FROM event WHERE event_id = ?", (event_id,)).fetchone()
    assert "3 dead" in event["summary"]
    assert event["summary_method"] == f"llm:{config.LLM_LOCAL_MODEL}"
    assert set(json.loads(event["summary_evidence"])["document_ids"]) == set(ids)
    assert llm.evidence_violations(conn) == []
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    # the summary was just written; inside 24 hours nothing is due
    assert llm.events_to_summarise(conn, now) == []
    later = now + timedelta(hours=25)
    conn.execute("UPDATE event SET summary_updated_at = ? WHERE event_id = ?", (to_iso(now), event_id))
    assert llm.events_to_summarise(conn, later) == []  # the three documents are already part of the summary


def test_doctor_counts_spend_stale_documents_and_bad_spans(conn):
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    llm.write_call(conn, llm.Call("extract", "ollama", config.LLM_OLLAMA_MODEL, 5, 5, cost_usd=0), when="2026-09-02T00:00:00Z")
    old = to_iso(now - timedelta(hours=48))
    documents.upsert_document(conn, source_id="gdelt", kind="article", url="https://news.example.com/old", title="Old", fetched_at=old, published_at=old)
    assert llm.awaiting_extraction(conn, now) == 1
    result = documents.upsert_document(conn, source_id="gdelt", kind="article", url="https://news.example.com/bad", title="Flood", text_excerpt="no numbers here")
    conn.execute(
        "INSERT INTO document_extraction (document_id, method, places, figures, extracted_at) VALUES (?, 'llm:test', '[]', ?, '2026-09-02T00:00:00Z')",
        (result.document_id, json.dumps({"dead": {"value": 3, "evidence_span": "3 dead"}})),
    )
    assert len(llm.evidence_violations(conn)) == 1
    lines = "\n".join(llm.doctor_lines(conn, now))
    assert "awaiting extraction" in lines and "violations: 1" in lines


def test_the_local_extractor_meets_the_golden_bars(conn, monkeypatch):
    monkeypatch.setattr(ratelimit, "log_geocode", lambda *args, **kwargs: None)
    result = llm.evaluate(conn, llm.LocalExtractor())
    assert result["hazard_accuracy"] >= config.EVAL_HAZARD_ACCURACY
    assert result["place_resolution"] >= config.EVAL_PLACE_RESOLUTION
    assert result["figures_exact"] >= config.EVAL_FIGURES_EXACT
    assert result["span_violations"] == 0 and result["passed"]
    assert result["cost_usd"] == 0 and result["backend"] == "local"


def test_golden_set_scores_a_faithful_model(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ratelimit, "log_geocode", lambda *args, **kwargs: None)

    class Faithful:
        backend = "ollama"
        pulled = False

        def __init__(self):
            self.calls = []

        def extract_many(self, jobs):
            found = {}
            for job in jobs:
                expected = job.document["expected"]
                figures = {}
                for key, value in expected["figures"].items():
                    figures[key] = None if value is None else {"value": value, "evidence_span": f"{value} {key}"}
                found[job.document_id] = llm.Extraction.model_validate({
                    "hazard_type": expected["hazard_type"],
                    "places": [{"name": place["name"], "country_hint": place["country_hint"]} for place in job.document.get("places") or []],
                    "figures": figures,
                    "confidence": 0.9,
                })
                self.calls.append(llm.Call("extract", "ollama", config.LLM_OLLAMA_MODEL, 10, 10, document_id=job.document_id))
            return found

        def summarise_many(self, jobs):
            found = {}
            for job in jobs:
                expected = job.event["expected"]["figures"]
                figures = {key: (None if value is None else {"value": value, "evidence_span": f"{value} {key}"}) for key, value in expected.items()}
                found[job.event_id] = llm.Summary.model_validate({
                    "sentences": [{"text": job.authority_text, "evidence_span": job.authority_text}],
                    "figures": figures,
                })
                self.calls.append(llm.Call("summarise", "ollama", config.LLM_OLLAMA_MODEL, 10, 10, event_id=job.event_id))
            return found

        def close(self):
            return None

    result = llm.evaluate(conn, Faithful())
    assert result["documents"] == 30 and result["events"] == 10
    assert result["hazard_accuracy"] >= config.EVAL_HAZARD_ACCURACY
    assert result["place_resolution"] >= config.EVAL_PLACE_RESOLUTION
    assert result["figures_exact"] >= config.EVAL_FIGURES_EXACT
    assert result["span_violations"] == 0 and result["passed"]
    assert result["cost_usd"] == 0
    path = llm.write_eval_report(conn, result, "memory", path=tmp_path / "m4.md")
    text = path.read_text(encoding="utf-8")
    assert "Hazard accuracy" in text and "PASS" in text
