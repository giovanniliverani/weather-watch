"""M5 collectors: Bluesky, Mastodon, YouTube and Reddit through a mock transport, plus the forecast helper."""

import json
from datetime import timedelta

import httpx
import pytest

from eww import api, config, documents, enrich, heartbeat, llm, ratelimit, report
from eww.clock import now_utc, parse_iso, slot_for, to_iso
from eww.enrich import bluesky, mastodon, queries, reddit, youtube
from eww.ids import new_id
from tests.test_enrich import mock_client, prepared

T0 = "2026-09-16T15:10:00Z"


@pytest.fixture(autouse=True)
def provider_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_LOG", tmp_path / "logs" / "providers.jsonl")
    monkeypatch.setattr(config, "BLUESKY_HANDLE", None)
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", None)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", None)
    monkeypatch.setattr(config, "REDDIT_ENABLED", False)


def _attach(conn, event_id, document_id, now):
    with conn:
        conn.execute(
            """
            INSERT INTO event_document (event_id, document_id, status, score, method, decided_at, decided_by)
            VALUES (?, ?, 'attached', 0.8, 'query', ?, 'pipeline')
            """,
            (event_id, document_id, to_iso(now)),
        )


def test_social_query_and_mentions():
    terms = queries.EventTerms(hazard_type="flood", countries_en=["Nepal"], countries_it=["Nepal"])
    assert queries.social_query(terms, "en") == "Nepal flood"
    assert queries.social_query(terms, "it") == "Nepal alluvione"
    assert queries.social_query(queries.EventTerms(hazard_type="flood"), "en") is None
    assert queries.mentions("Severe flood in Nepal", ["Nepal"])
    assert not queries.mentions("Flooding around Paris", ["Nepal"])
    assert queries.mentions("Flooding in Mexico today", ["Mexico"])
    assert not queries.mentions("Flash flood warning for New Mexico", ["Mexico"])
    assert queries.match_terms(terms) == ["Nepal"]


def test_bluesky_stores_a_post_and_does_not_log_the_password(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "watcher.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-password-secret")
    posts = [
        {
            "uri": "at://did:plc:abc/app.bsky.feed.post/rkey1",
            "author": {"handle": "alice.bsky.social"},
            "record": {"text": "Flooding in Nepal today", "createdAt": "2026-09-15T12:00:00.000Z"},
            "embed": {"$type": "app.bsky.embed.images#view", "images": [{"fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did/abc@jpeg"}]},
        },
        {
            "uri": "at://did:plc:def/app.bsky.feed.post/rkey2",
            "author": {"handle": "bob.bsky.social"},
            "record": {"text": "The rivers in Nepal are still rising", "createdAt": "2026-09-15T13:00:00Z"},
        },
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("createSession"):
            assert b"app-password-secret" in request.content
            return httpx.Response(200, json={"accessJwt": "access-jwt-value", "refreshJwt": "refresh-jwt-value"})
        if request.url.path.endswith("searchPosts"):
            assert request.headers["Authorization"] == "Bearer access-jwt-value"
            assert request.url.params["sort"] == "latest" and request.url.params["limit"] == "100"
            return httpx.Response(200, json={"posts": posts})
        return httpx.Response(404, json={})

    slept = []
    http = mock_client(handler)
    collector = bluesky.BlueskyCollector(http=http, sleep=slept.append)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    since, until = enrich.window_for(conn, event, "bluesky", now)
    result = collector.enrich_event(conn, event, since, until, now)
    assert result.documents_new == 2 and result.documents_seen == 2
    assert "Nepal flood" in result.query and "Nepal alluvione" in result.query
    row = conn.execute("SELECT * FROM document WHERE external_id LIKE '%rkey1'").fetchone()
    assert row["kind"] == "post" and row["author"] == "alice.bsky.social"
    assert row["url"] == "https://bsky.app/profile/alice.bsky.social/post/rkey1"
    assert row["text_excerpt"] == "Flooding in Nepal today" and row["published_at"] == "2026-09-15T12:00:00Z"
    assert row["media_url"].endswith("abc@jpeg") and row["media_kind"] == "image"
    again = collector.enrich_event(conn, event, since, until, now)
    assert again.documents_new == 0 and conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 2
    assert slept and min(slept) >= 0.9
    logged = config.PROVIDER_LOG.read_text(encoding="utf-8")
    assert "app-password-secret" not in logged and "access-jwt-value" not in logged
    collector.close()
    http.close()


def test_bluesky_sweep_hides_a_deleted_post(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "watcher.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-password-secret")
    kept = "at://did:plc:abc/app.bsky.feed.post/kept"
    gone = "at://did:plc:abc/app.bsky.feed.post/gone"
    stored = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "access-jwt-value", "refreshJwt": "refresh-jwt-value"})
        if request.url.path.endswith("searchPosts"):
            return httpx.Response(200, json={"posts": [
                {"uri": kept, "author": {"handle": "alice.bsky.social"}, "record": {"text": "Nepal flood one", "createdAt": "2026-09-15T12:00:00Z"}},
                {"uri": gone, "author": {"handle": "alice.bsky.social"}, "record": {"text": "Nepal flood two", "createdAt": "2026-09-15T12:30:00Z"}},
            ]})
        if request.url.path.endswith("getPosts"):
            assert request.url.params.get_list("uris")
            return httpx.Response(200, json={"posts": [{"uri": kept}]})
        return httpx.Response(404, json={})

    http = mock_client(handler)
    collector = bluesky.BlueskyCollector(http=http, sleep=lambda seconds: None)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    since, _ = enrich.window_for(conn, event, "bluesky", now)
    collector.enrich_event(conn, event, since, now, now)
    for row in conn.execute("SELECT document_id, external_id FROM document"):
        stored[row["external_id"]] = row["document_id"]
        _attach(conn, event["event_id"], row["document_id"], now)
    assert collector.sweep(conn, now) == 1
    assert conn.execute("SELECT removed_at FROM document WHERE document_id = ?", (stored[gone],)).fetchone()[0] == to_iso(now)
    assert conn.execute("SELECT removed_at FROM document WHERE document_id = ?", (stored[kept],)).fetchone()[0] is None
    shown = api.event_documents(event["event_id"], conn=conn)
    assert [item["document_id"] for item in shown] == [stored[kept]]
    collector.close()
    http.close()


def test_youtube_skips_without_a_key_and_stops_when_billing_is_required(conn, data_dir, monkeypatch):
    ok, reason = youtube.available(conn)
    assert ok is False and "YOUTUBE_API_KEY" in reason and "billing" in reason
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "youtube-key-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "a billing account is required", "errors": [{"reason": "billingNotEnabled"}]}})

    http = mock_client(handler)
    now = parse_iso(T0)
    stats = enrich.run(conn, ["youtube"], now=now, max_events=1, http=http)
    assert stats["youtube"].stopped and "billing account" in stats["youtube"].stopped
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 0
    assert "youtube-key-secret" not in config.PROVIDER_LOG.read_text(encoding="utf-8")
    http.close()


def test_youtube_stops_at_100_searches_before_calling(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "youtube-key-secret")
    now = parse_iso(T0)
    with conn:
        for index in range(config.YOUTUBE_SEARCHES_PER_DAY):
            heartbeat.record_run(conn, {"run_id": new_id(), "source_id": "youtube", "scheduled_for": "2026-09-16T00:00:00Z", "started_at": to_iso(now), "finished_at": to_iso(now), "status": "ok", "http_status": 200, "items_seen": 1, "snapshot_path": None, "error": None})
    calls = []
    http = mock_client(lambda request: calls.append(request) or httpx.Response(500, json={}))
    collector = youtube.YouTubeCollector(http=http, sleep=lambda seconds: None)
    with pytest.raises(ratelimit.RateLimitExceeded):
        collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=1), now, now)
    assert calls == []
    collector.close()
    http.close()


def test_youtube_stores_a_video_without_the_description_and_does_not_search_twice_in_a_day(conn, data_dir, monkeypatch):
    prepared(conn, data_dir)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "youtube-key-secret")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["key"] == "youtube-key-secret"
        assert request.url.params["type"] == "video" and request.url.params["order"] == "date"
        return httpx.Response(200, json={"items": [{
            "id": {"videoId": "abc123"},
            "snippet": {
                "title": "Nepal flood video",
                "description": "DO-NOT-STORE",
                "channelTitle": "News Channel",
                "publishedAt": "2026-09-15T12:00:00Z",
                "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/abc123/mqdefault.jpg"}},
            },
        }]})

    http = mock_client(handler)
    now = parse_iso(T0)
    stats = enrich.run(conn, ["youtube"], now=now, max_events=1, http=http)
    assert stats["youtube"].documents_new == 1 and stats["youtube"].stopped is None
    row = conn.execute("SELECT * FROM document").fetchone()
    assert row["kind"] == "video" and row["external_id"] == "abc123"
    assert row["url"] == "https://www.youtube.com/watch?v=abc123"
    assert row["author"] == "News Channel" and row["media_url"].endswith("mqdefault.jpg")
    assert "DO-NOT-STORE" not in (row["payload"] or "")
    assert conn.execute("SELECT COALESCE(SUM(items_seen), 0) FROM collector_run WHERE source_id = 'youtube'").fetchone()[0] == 1
    logged = ratelimit.read(kind="call")
    assert logged[0]["params"]["key"] == "<set>"
    again = enrich.run(conn, ["youtube"], now=now, max_events=1, http=http)
    assert again["youtube"].events_queried == 0 and again["youtube"].events_skipped == 1
    assert len(calls) == 1
    http.close()


def test_youtube_sweep_removes_a_video_the_api_no_longer_returns(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "youtube-key-secret")
    now = parse_iso(T0)
    old = to_iso(now - timedelta(days=config.YOUTUBE_REFRESH_DAYS + 1))
    event_id = events["Flood in Nepal"]["event_id"]
    kept = documents.upsert_document(conn, source_id="youtube", kind="video", url="https://www.youtube.com/watch?v=keepme", external_id="keepme", title="Still there", author="Channel", fetched_at=old)
    gone = documents.upsert_document(conn, source_id="youtube", kind="video", url="https://www.youtube.com/watch?v=goneme", external_id="goneme", title="Gone", author="Channel", fetched_at=old)
    _attach(conn, event_id, kept.document_id, now)
    _attach(conn, event_id, gone.document_id, now)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url).split("?", 1)[0])
        return httpx.Response(200, json={"items": [{"id": "keepme", "snippet": {"title": "Still there", "channelTitle": "Channel", "publishedAt": "2026-09-01T00:00:00Z", "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/keepme/mqdefault.jpg"}}}}]})

    http = mock_client(handler)
    collector = youtube.YouTubeCollector(http=http, sleep=lambda seconds: None)
    assert collector.sweep(conn, now) == 1
    assert calls and all(url.endswith("/videos") for url in calls)
    assert conn.execute("SELECT removed_at FROM document WHERE document_id = ?", (gone.document_id,)).fetchone()[0] == to_iso(now)
    refreshed = conn.execute("SELECT removed_at, fetched_at FROM document WHERE document_id = ?", (kept.document_id,)).fetchone()
    assert refreshed["removed_at"] is None and refreshed["fetched_at"] == to_iso(now)
    assert conn.execute("SELECT COUNT(*) FROM collector_run WHERE source_id = 'youtube'").fetchone()[0] == 0
    collector.close()
    http.close()


def test_mastodon_keeps_a_matching_post_and_fetches_each_tag_once(conn, data_dir):
    events = prepared(conn, data_dir)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[
            {
                "id": "100",
                "url": "https://mastodon.social/@watcher/100",
                "content": "<p>Severe <b>flood</b> in Nepal</p>",
                "created_at": "2026-09-15T12:00:00.000Z",
                "language": "en",
                "account": {"acct": "watcher"},
                "media_attachments": [{"preview_url": "https://files.mastodon.social/preview.jpg"}],
            },
            {
                "id": "200",
                "url": "https://mastodon.social/@watcher/200",
                "content": "<p>Flooding around Paris</p>",
                "created_at": "2026-09-15T12:00:00Z",
                "account": {"acct": "other"},
                "media_attachments": [],
            },
        ])

    http = mock_client(handler)
    collector = mastodon.MastodonCollector(http=http, sleep=lambda seconds: None)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    since, _ = enrich.window_for(conn, event, "mastodon", now)
    result = collector.enrich_event(conn, event, since, now, now)
    assert result.documents_new == 1
    row = conn.execute("SELECT * FROM document").fetchone()
    assert row["kind"] == "post" and row["text_excerpt"] == "Severe flood in Nepal"
    assert row["author"] == "watcher" and row["media_url"].endswith("preview.jpg")
    assert "<p>" not in (row["payload"] or "")
    assert conn.execute("SELECT COUNT(*) FROM document_retrieval WHERE event_id = ?", (event["event_id"],)).fetchone()[0] == 1
    first = len(calls)
    collector.enrich_event(conn, event, since, now, now)
    assert len(calls) == first == 2  # flood and alluvione, then the cache
    collector.close()
    http.close()


def test_reddit_stays_off_until_enabled_and_stores_only_the_allowed_fields(conn, data_dir, monkeypatch):
    monkeypatch.setattr(config, "REDDIT_CLIENT_ID", "client-id")
    ok, reason = reddit.available(conn)
    assert ok is False and "EWW_REDDIT_ENABLED" in reason
    monkeypatch.setattr(config, "REDDIT_ENABLED", True)
    monkeypatch.setattr(config, "REDDIT_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "REDDIT_USERNAME", "ewwuser")
    monkeypatch.setattr(config, "REDDIT_PASSWORD", "reddit-password-secret")
    events = prepared(conn, data_dir)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith(f"windows:eww:v{config.VERSION}")
        assert config.CONTACT in request.headers["User-Agent"]
        if request.url.path.endswith("access_token"):
            assert b"reddit-password-secret" in request.content
            return httpx.Response(200, json={"access_token": "reddit-token-value"})
        return httpx.Response(200, json={"data": {"children": [{"data": {
            "name": "t3_abc123",
            "title": "Nepal flood thread",
            "permalink": "/r/nepal/comments/abc123/flood/",
            "subreddit": "nepal",
            "created_utc": 1750000000,
            "selftext": "do-not-store-selftext",
            "preview": {"images": [{"source": {"url": "https://preview.redd.it/x.jpg?width=320&amp;auto=webp"}}]},
        }}]}})

    http = mock_client(handler)
    collector = reddit.RedditCollector(http=http, sleep=lambda seconds: None)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    since, _ = enrich.window_for(conn, event, "reddit", now)
    result = collector.enrich_event(conn, event, since, now, now)
    assert result.documents_new == 1
    row = conn.execute("SELECT * FROM document").fetchone()
    assert row["url"] == "https://www.reddit.com/r/nepal/comments/abc123/flood/"
    assert row["external_id"] == "t3_abc123" and row["title"] == "Nepal flood thread"
    assert row["author"] is None and row["text_excerpt"] is None and row["publisher"] == "nepal"
    assert "do-not-store-selftext" not in (row["payload"] or "")
    assert row["media_url"] == "https://preview.redd.it/x.jpg?width=320&auto=webp"
    assert "reddit-password-secret" not in config.PROVIDER_LOG.read_text(encoding="utf-8")
    assert "reddit-token-value" not in config.PROVIDER_LOG.read_text(encoding="utf-8")
    collector.close()
    http.close()


def test_open_meteo_forecast_uses_the_public_host_and_writes_nothing(tmp_path, monkeypatch):
    from eww import weather

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["forecast_days"] == "5"
        assert request.url.params["current"] == "temperature_2m,precipitation,weather_code,wind_speed_10m"
        return httpx.Response(200, json={
            "current": {"time": "2026-09-26T09:00", "temperature_2m": 18.2, "precipitation": 0.0, "weather_code": 61, "wind_speed_10m": 12.4},
            "daily": {
                "time": ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30"],
                "temperature_2m_max": [22, 21, 20, 19, 18],
                "temperature_2m_min": [14, 13, 12, 11, 10],
                "precipitation_sum": [3.2, 0, 1, 0, 0],
                "weather_code": [61, 0, 3, 1, 2],
            },
        })

    http = mock_client(handler)
    body = weather.forecast(28.2134, 85.2678, http=http)
    assert len(calls) == 1
    assert calls[0].url.params["latitude"] == "28.2134" and calls[0].url.params["longitude"] == "85.2678"
    assert body["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert body["current"]["temperature_c"] == 18.2 and body["current"]["conditions"] == "Slight rain"
    assert len(body["daily"]) == 5 and body["daily"][0]["high_c"] == 22
    assert not list(tmp_path.glob("*.sqlite"))
    monkeypatch.setattr(config, "OPEN_METEO_URL", "https://customer-api.open-meteo.com/v1/forecast")
    with pytest.raises(ValueError):
        weather.forecast(1, 2, http=http)
    assert len(calls) == 1
    http.close()


def test_posts_are_not_sent_to_a_model(conn):
    article = documents.upsert_document(conn, source_id="gdelt", kind="article", url="https://news.example.com/nepal", title="Rivers rise")
    post = documents.upsert_document(conn, source_id="mastodon", kind="post", url="https://mastodon.social/@a/1", title=None, text_excerpt="flood in nepal")
    with conn:
        for document_id in (article.document_id, post.document_id):
            conn.execute(
                "INSERT INTO document_extraction (document_id, method, hazard_type, places, extracted_at) VALUES (?, ?, NULL, '[]', ?)",
                (document_id, config.EXTRACT_METHOD, T0),
            )
    found = {row["document_id"] for row in llm.ambiguous_documents(conn)}
    assert article.document_id in found and post.document_id not in found


def test_youtube_runs_do_not_serve_a_spine_slot(conn):
    now = now_utc()
    with conn:
        heartbeat.record_run(conn, {"run_id": new_id(), "source_id": "youtube", "scheduled_for": now.strftime("%Y-%m-%dT00:00:00Z"), "started_at": to_iso(now), "finished_at": to_iso(now), "status": "ok", "http_status": 200, "items_seen": 1, "snapshot_path": None, "error": None})
    assert conn.execute("SELECT COUNT(*) FROM heartbeat").fetchone()[0] == 0
    old = to_iso(now - timedelta(days=2))
    with conn:
        heartbeat.record_run(conn, {"run_id": new_id(), "source_id": "gdacs", "scheduled_for": slot_for(parse_iso(old), 3), "started_at": old, "finished_at": old, "status": "ok", "http_status": 200, "items_seen": 1, "snapshot_path": None, "error": None})
    current = slot_for(now, 3)
    served = conn.execute("SELECT served FROM heartbeat WHERE scheduled_for = ?", (current,)).fetchone()
    assert served is not None and served[0] == 0


def test_social_report_counts_attached_posts_and_keeps_hand_labels(conn, data_dir, tmp_path):
    events = prepared(conn, data_dir)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    stored = documents.upsert_document(conn, source_id="mastodon", kind="post", url="https://mastodon.social/@a/9", title=None, text_excerpt="Nepal flood", author="watcher", fetched_at=to_iso(now))
    _attach(conn, event["event_id"], stored.document_id, now)
    sample = tmp_path / "social_posts.csv"
    out = tmp_path / "m5.md"
    probe = {"ok": True, "elapsed_s": 0.2, "rows": 5, "temperature_c": 18.2, "attribution": config.OPEN_METEO_ATTRIBUTION, "error": None}
    path, result = report.write_social_report(conn, out, now=now, probe=probe, sample_path=sample)
    assert path == out and result["coverage"]["covered"] == 1 and result["coverage"]["events"] == 3
    assert result["coverage_passed"] is True and result["relevance_passed"] is False
    assert result["weather_passed"] is True
    text = sample.read_text(encoding="utf-8")
    sample.write_text(text.replace(",,", ",yes,on topic"), encoding="utf-8")
    _, again = report.write_social_report(conn, out, now=now, probe=probe, sample_path=sample)
    assert again["sample"]["relevant"] == 1 and again["relevance_passed"] is False
    assert "1 of 3" in out.read_text(encoding="utf-8")


def test_mentions_keeps_the_country_when_new_mexico_is_also_named():
    assert queries.mentions("Floods across Mexico, and New Mexico too", ["Mexico"])
    assert not queries.mentions("New Mexico flash flood", ["Mexico"])


def test_bluesky_token_is_sent_per_request_and_never_set_on_a_shared_client(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "watcher.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-password-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "access-jwt-value", "refreshJwt": "refresh-jwt-value"})
        assert request.headers["Authorization"] == "Bearer access-jwt-value"
        return httpx.Response(200, json={"posts": []})

    http = mock_client(handler)
    collector = bluesky.BlueskyCollector(http=http, sleep=lambda seconds: None)
    now = parse_iso(T0)
    collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=1), now, now)
    assert "Authorization" not in http.headers
    collector.close()
    http.close()


def test_youtube_counts_a_search_that_failed_after_reaching_google(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "youtube-key-secret")
    http = mock_client(lambda request: httpx.Response(500, json={}))
    collector = youtube.YouTubeCollector(http=http, sleep=lambda seconds: None)
    now = parse_iso(T0)
    with pytest.raises(ValueError):
        collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=1), now, now)
    assert youtube.searches_today(conn, now) == 1
    collector.close()
    http.close()


def test_social_sample_keeps_labelled_rows_when_new_posts_arrive(conn, data_dir, tmp_path, monkeypatch):
    events = prepared(conn, data_dir)
    now = parse_iso(T0)
    event = events["Flood in Nepal"]
    monkeypatch.setattr(config, "SOCIAL_SAMPLE_SIZE", 2)
    first = []
    for index in range(2):
        stored = documents.upsert_document(conn, source_id="mastodon", kind="post", url=f"https://mastodon.social/@a/{index}", title=None, text_excerpt="Nepal flood", author="watcher", fetched_at=to_iso(now))
        _attach(conn, event["event_id"], stored.document_id, now)
        first.append(stored.document_id)
    sample = tmp_path / "social_posts.csv"
    report.write_social_sample(report.social_sample(conn, path=sample), sample)
    sample.write_text(sample.read_text(encoding="utf-8").replace(",,", ",yes,on topic"), encoding="utf-8")
    for index in range(2, 12):
        stored = documents.upsert_document(conn, source_id="mastodon", kind="post", url=f"https://mastodon.social/@a/{index}", title=None, text_excerpt="Nepal flood", author="watcher", fetched_at=to_iso(now))
        _attach(conn, event["event_id"], stored.document_id, now)
    rows = report.social_sample(conn, path=sample)
    assert sorted(row["document_id"] for row in rows) == sorted(first)
    assert all(row["relevant"] == "yes" for row in rows)


def test_viewer_imports_only_api_and_review():
    source = (config.PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    imports = {line.strip() for line in source.splitlines() if line.startswith(("from eww", "import eww"))}
    assert imports == {"from eww import api, review"}
    assert api.FORECAST_ATTRIBUTION == config.OPEN_METEO_ATTRIBUTION
