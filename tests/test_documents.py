"""Canonical URLs, idempotent document rows, retrieval provenance and the 60-day purge (M3)."""

import pytest

from eww import documents
from eww.clock import now_utc, to_iso


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://WWW.Example.com/News/Item?utm_source=x&utm_medium=y&id=42#top", "https://www.example.com/News/Item?id=42"),
        ("https://example.com:443/a/b/?fbclid=abc", "https://example.com/a/b/"),
        ("https://example.com:8443/a?b=1&gclid=2&c=3", "https://example.com:8443/a?b=1&c=3"),
        ("example.com/path", "https://example.com/path"),
        ("https://Example.com", "https://example.com/"),
        ("https://example.com/x?ref=twitter&mc_cid=1&keep=yes", "https://example.com/x?keep=yes"),
        ("  https://example.com/x?  ", "https://example.com/x"),
    ],
)
def test_canonical_url(raw, expected):
    assert documents.canonical_url(raw) == expected


def test_excerpt_is_cut_to_the_limit():
    assert documents.excerpt("  a   b \n c ") == "a b c"
    assert documents.excerpt(None) is None and documents.excerpt("   ") is None
    assert len(documents.excerpt("x" * 5000)) == 2000


def insert(conn, url, title="Floods hit Bologna", **kwargs):
    return documents.upsert_document(conn, source_id="gdelt", kind="article", url=url, title=title, publisher=kwargs.pop("publisher", None), payload={"url": url}, **kwargs)


def test_upsert_is_idempotent_on_the_canonical_url(conn):
    first = insert(conn, "http://News.example.com/story?utm_source=rss")
    again = insert(conn, "https://news.example.com/story", title="Floods hit Bologna, 3 dead")
    assert first.created and not again.created and first.document_id == again.document_id
    row = conn.execute("SELECT * FROM document").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 1
    assert row["url_canonical"] == "https://news.example.com/story"
    assert row["title"] == "Floods hit Bologna, 3 dead"  # metadata refreshed
    assert row["publisher"] == "news.example.com"  # the domain when the source gives none
    assert row["media_kind"] == "none" and row["media_url"] is None
    other_source = documents.upsert_document(conn, source_id="reliefweb", kind="report", url="https://news.example.com/story", title="same url, other source")
    assert other_source.created  # UNIQUE is per (source_id, url_canonical)
    with pytest.raises(ValueError):
        insert(conn, "not a url at all")
    with pytest.raises(ValueError):
        documents.upsert_document(conn, source_id="gdelt", kind="tweet", url="https://x.example.com/1", title="x")


def test_excerpt_never_exceeds_2000_characters(conn):
    result = documents.upsert_document(conn, source_id="reliefweb", kind="report", url="https://reliefweb.int/report/1", title="Report", text_excerpt="word " * 1000)
    length = conn.execute("SELECT length(text_excerpt) FROM document WHERE document_id = ?", (result.document_id,)).fetchone()[0]
    assert length == 2000


def test_retrieval_rows_and_purge(conn, data_dir):
    from eww import resolve
    from tests.conftest import gdacs_item, ingest_items

    ingest_items(conn, data_dir, "gdacs", [gdacs_item(1104124, "FL", "Flood in Nepal", 28.2, 85.3, "2026-08-25T00:00:00", iso3="NPL")], "2026-09-01T00:00:00Z")
    resolve.resolve(conn)
    event_id = conn.execute("SELECT event_id FROM event").fetchone()[0]
    old = to_iso(now_utc().replace(year=now_utc().year - 1))
    attached = insert(conn, "https://a.example.com/1", fetched_at=old, published_at=old)
    candidate = insert(conn, "https://a.example.com/2", fetched_at=old, published_at=old)
    rejected = insert(conn, "https://a.example.com/3", fetched_at=old, published_at=old)
    plain = insert(conn, "https://a.example.com/4", fetched_at=old, published_at=old)
    fresh = insert(conn, "https://a.example.com/5")
    for result in (attached, candidate, rejected, plain, fresh):
        assert documents.record_retrieval(conn, result.document_id, event_id, "gdelt")
    assert not documents.record_retrieval(conn, plain.document_id, event_id, "gdelt")  # INSERT OR IGNORE
    assert documents.retrieval_events(conn, plain.document_id) == [event_id]
    for result in (attached, candidate, rejected, plain, fresh):
        conn.execute("INSERT INTO document_extraction (document_id, method, places, extracted_at) VALUES (?, 'lexicon+ner', '[]', '2026-09-01T00:00:00Z')", (result.document_id,))
        conn.execute("INSERT INTO document_embedding (document_id, model, dim, vector) VALUES (?, 'test', 1, ?)", (result.document_id, b"\x00\x00\x00\x00"))
    for result, status in ((attached, "attached"), (candidate, "candidate"), (rejected, "rejected")):
        conn.execute("INSERT INTO event_document (event_id, document_id, status, method, decided_at) VALUES (?, ?, ?, 'embedding', '2026-09-01T00:00:00Z')", (event_id, result.document_id, status))
    conn.commit()
    preview = documents.purge_unattached(conn, 60, dry_run=True)
    assert preview.documents == 2 and preview.extractions == 2 and preview.embeddings == 2 and preview.retrievals == 2
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 5
    stats = documents.purge_unattached(conn, 60)
    assert stats.documents == 2 and stats.rejected_links == 1
    left = {row[0] for row in conn.execute("SELECT document_id FROM document")}
    assert left == {attached.document_id, candidate.document_id, fresh.document_id}
    assert conn.execute("SELECT COUNT(*) FROM document_extraction").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM document_retrieval").fetchone()[0] == 3
    again = documents.purge_unattached(conn, 60)
    assert again.documents == 0
    summary = documents.stats(conn)
    assert summary["documents"] == 3 and summary["decisions"] == {"attached": 1, "candidate": 1} and summary["unattached"] == 1
