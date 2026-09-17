# Extreme Weather Watch: architecture and sequencing

*Written 2026-09-16, last updated 2026-09-17 after M0, the M1 build and the M2 build, against the README and constraints in `docs/planning-prompt.md`. Facts about third-party services were checked against their official pages on those dates; anything marked **verify** is either unconfirmed or likely to change.*

## 0. Summary

**What you are building first.** A pin map of hazard events on your laptop, drawn by Streamlit and Leaflet from GeoJSON and fed by free authoritative feeds (GDACS, NASA EONET, Copernicus EMS) carrying ids, coordinates and severity. A GitHub Actions cron collects every three hours and commits raw snapshots to a repo branch, so history accumulates while the laptop is off. When the laptop is on, one Python command replays snapshots into SQLite, attaches free news and social links, geocodes with a local gazetteer and summarises with a local model. Recurring cost: €0; a cloud model sits behind a hard cap of about €9 a month.

**The most consequential thing you are probably getting wrong.** The README makes news and social media the *source* of events and treats clustering them as the core problem. That ordering is expensive and unreliable. Structured feeds should be the source of event identity; news and posts should be *attached* to events already known. That turns open-ended clustering into bounded assignment, removes most model spend and all terms-of-service exposure. The price is coverage below humanitarian-alert thresholds. M0 measured it on 2026-09-16: 703 distinct events in 30 days, 5 hazard types, 6 continents; the bet holds.

## 1. Decisions

| Decision | Choice | Why | What would change my mind |
|---|---|---|---|
| Language and runtime | Python 3.12 with uv; one package `eww` with a CLI (`uv run eww <command>`) | It is the language you have, and every component has a mature Python library. uv is already on your machine and gives a lockfile without Docker. | Nothing in v1. |
| Database | SQLite in WAL mode with STRICT tables, one file; geometry as GeoJSON text plus bbox columns; embeddings as float32 BLOBs compared in numpy | Zero install, zero ops; a single writer matches a single user; the file is rebuildable from the data branch. At tens of thousands of rows it needs neither a spatial nor a vector index. | A second writer (contributions), a hosted API with concurrent writes, or polygon predicates → Postgres + PostGIS + pgvector on Neon (0.5 GB and 100 compute-hours free, verified) using the portable DDL in §3. |
| Local development setup | uv project; `.env` holding the three optional secrets (Bluesky app password, YouTube key, Anthropic key); `uv run eww sync` then `uv run streamlit run app.py`; no Docker, no services, no Postgres | Two commands and one file to back up; everything runs as your user; Ollama is already installed. | Never for v1. |
| Scheduling and the laptop problem | GitHub Actions cron at `7 */3 * * *` runs the keyless spine collectors (GDACS, EONET, Copernicus EMS) and commits snapshots plus a run log to an orphan `data` branch using `GITHUB_TOKEN`; the laptop replays unseen snapshots and runs the enrichment collectors with time-range backfill | Free (about 360 of the 2,000 included minutes), no server, no secrets stored in GitHub, and the snapshots are the raw archive. Every enrichment source accepts a time range, so laptop gaps are recoverable. | Snapshot growth above about 5 MB/day packed → switch the sink to Cloudflare R2. Wanting an always-fresh hosted database → Actions writing to Neon. |
| Freshness target | Spine no more than 3 h stale; enrichment within 24 h of the laptop being on; both shown in the UI | GDACS updates a few times a day. A hurricane that is on the news and missing from the map for half a day is what "dead" feels like; anything under an hour buys nothing at this volume. | Sub-hour only matters with warning feeds (NWS, Meteoalarm), which are v2. |
| Map rendering and viewer | Streamlit + streamlit-folium (Leaflet), one process; MarkerCluster, coloured circle markers by hazard, footprint polygons, LocateControl for "centre on me"; details in the sidebar on click; filters as Streamlit widgets | Zero JavaScript and zero CSS. Click handling, clustering and browser geolocation come from plugins; current versions (folium 0.20, streamlit-folium 0.27, verified) support partial re-render so the map does not reset on every widget change. | Sluggish beyond a few thousand markers → pydeck, still Python. You decide to learn JavaScript → MapLibre reading the same GeoJSON. |
| Interface boundary | A GeoJSON FeatureCollection from `eww.api.events_geojson(filters)`, exported by CLI from M0 and served by FastAPI in M6 (§2 has the contract) | Any web map can read it; it forces all logic below the boundary; it is testable in geojson.io with no code. | None. |
| Source spine | GDACS (JSON SEARCH API, RSS as fallback), NASA EONET v3 and, since M2, Copernicus EMS Rapid Mapping activations (the public activations list, about 8 per 30 days) create events; a Copernicus activation joins the GDACS event its `gdacsId` names when it has one and sets the EMS severity flag either way | Free, keyless, typed hazard codes, stable ids with episodes, coordinates, alert levels, GLIDE numbers, and permissive licences (GDACS CC BY 4.0, NASA public domain, Copernicus open by EU regulation, all verified; the licence page cited in the `source` seed is marked **verify** in §7). Deterministic discovery is what makes the budget work; 8 of 9 activations in the 30-day window of 2026-09-17 had no GDACS twin, so an activation must be allowed to be an event. | M0's density check fails for weather hazards → bring orphan clustering forward and add Meteoalarm (CC BY 4.0, verified) as a warnings layer. |
| Enrichment sources | GDELT DOC 2.0 with event-driven queries at one request per 5 s; ReliefWeb reports after a free appname approval; Bluesky authenticated search; YouTube Data API; Mastodon tag timelines. Links and metadata only | Free; terms permit personal non-commercial use; all support time-range queries so the laptop can backfill. GDELT supplies `socialimage`, a free thumbnail reference for every headline. | A source's terms change the way the Guardian's did (AI clause, 24-hour retention) → drop it. GDELT outage → publisher RSS feeds, chosen one by one on their terms. |
| Dropped sources | X, Instagram, TikTok, Facebook, Telegram, Google News RSS, Guardian Open Platform, NewsAPI, NYT, browser automation of any kind | X is pay-per-read only; Instagram and TikTok need business accounts or institutional approval; Google News RSS is licensed for personal feed readers only; the Guardian bars AI use and retention over 24 h; NewsAPI's free tier is development-only with a 24 h delay; browser automation is where terms-of-service exposure lives. | X at $0.005 per read for at most 1,000 reads a month, later, if you decide the value is there. |
| Geocoding | Tier 0: coordinates from the feeds. Tier 1: local GeoNames `cities500` gazetteer with full-text search. Tier 2: GeoNames web service (10,000 credits/day with a free username, verified). Tier 3: Nominatim at 4 requests/minute, which is its stated limit for scripts run on a schedule (verified), for streets and landmarks only. Every hit and every miss cached forever | Deterministic, free, and offline for most place names in news about disasters (towns, provinces, countries). Nominatim's scheduled-script limit rules it out as the primary. | Miss rate above 20% on the review sample → load GeoNames `allCountries` locally (about 1.5 GB) or self-host Photon. |
| Clustering approach | Anchored identity (§3): feed ids create events; deterministic cross-source keys (Copernicus `gdacsId`, the GDACS URL EONET cites) join before any score; cross-source merge by hazard-class blocking, hazard-specific radius and time window, GLIDE and storm-name equality; auto-merge at 0.90, review queue 0.60–0.90; **two feeds' records share a pin automatically only inside the aggregation radius of `identity.yaml`** (25 km by default, 200 km for cyclones, 100 km for severe storms, 250 km for drought and temperature extremes; closest pair of positions), a shared id or GLIDE included, otherwise a proposal; a named storm blocks basin-wide (5,000 km) and merges on its name, two differently named storms never merge automatically, a pair one feed keys apart is never scored, an event is re-scored when a later record of its own arrives, and a merge a person reverted is never repeated by the pipeline; documents attach at 0.75 on a spatial, temporal and embedding score and never create events | Bounded, deterministic where it matters, every signal free. The two asymmetries (§3) are handled by conservatism and reversibility rather than by a smarter model. A cited id is strong evidence but not proof: on 2026-09-17 GDACS had reused wildfire ids, so EONET items citing them pointed at fires on other continents; the radius is what caught it. Measured the same day after the gate: 913 records joined by key inside the radius, 14 automatic merges, 39 proposals of which 27 kept apart by the radius, 0 false merges and 83% recall on the 48 labelled pairs. | Any wrong auto-merge on the labelled pairs → raise the threshold or shrink the radius. Recall below 60% → lower the review threshold, never the auto threshold. Two same-named storms inside 10 days and 5,000 km → require the basin to match or lower `named_storm_km`. Correct pairs piling up as proposals for one hazard → widen that hazard's radius in `identity.yaml`, never the default. |
| Embeddings | Local `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, about 470 MB on disk, CPU) through sentence-transformers; brute-force cosine in numpy | Zero marginal cost, 50 languages, milliseconds at v1 scale. | More than 300,000 documents → sqlite-vec (pre-v1, has a Windows wheel, some Windows load failures reported) or pgvector. |
| Model usage | None for discovery. Extraction and summaries through Ollama `qwen2.5:7b-instruct` with JSON-schema output by default; Claude Sonnet 5 through the Batch API as the fallback behind a $10/month cap enforced in code; every call written to `llm_call` | Ollama is installed and 24 GB RAM is ample for a 7B model. The job is bounded extraction with deterministic validation; cloud only where the golden set shows local quality is not enough. | Golden-set accuracy below 80% locally, or the nightly backlog not clearing → flip the default to cloud, still capped. |
| Media | References and provenance only: URL, page URL, discovery time, thumbnail URL; rendered by URL in the sidebar with a plain link as fallback | Costs nothing, keeps storage flat, and makes publication a policy change rather than a rewrite. Rendering by URL gives thumbnails without storing a byte. | Wanting cross-source photo de-duplication or an archive → cache thumbnails under 50 KB, a v2 policy change. |
| Eventual hosting | Streamlit Community Cloud (one private app on the free tier, verified) for the viewer; Cloudflare R2 (10 GB free, verified) holding the SQLite file uploaded by `eww publish`; FastAPI GeoJSON endpoint run locally as proof of the boundary | Free, private, nothing to patch. The single-writer model is what makes publish-by-upload legitimate. | Contributions → Postgres on Neon before anything else. Streamlit Cloud drops private apps → a €5 VPS, which would use the budget. Hugging Face Spaces is no longer a free option for Docker or Gradio apps (verified, Streamlit status unclear). |
| Web technology to learn for v1 | Zero JavaScript, zero CSS. Optional: about 15 lines of templated HTML for popups, skippable by using the sidebar. Concepts only: what a URL with query parameters is; what a GeoJSON file looks like; that the browser talks to localhost:8501. Plus about 40 lines of GitHub Actions YAML | Everything interactive comes from Leaflet through folium; Streamlit renders widgets from Python. | Wanting icons beyond coloured circles → folium `Icon` / `DivIcon`, still Python. |
| The README's frontend contradiction | Planning against: a disposable local Python viewer is in v1; "website usability" and "front-end design" are out until a real frontend exists | The README's "no frontend needed" means no production website; its MVP interest is in seeing events on a map, which the viewer delivers. | None. |
| Hazard scope | All GDACS and EONET hazard types are ingested, including earthquakes, volcanoes and tsunamis; the default map filter shows every type and you can hide the geophysical ones | The README's end state says "natural disasters"; the feeds supply geophysical events for free; a filter is cheaper than an argument. | None. |

### Positions behind the table

Hard problems 1 and 6 are answered in §3 (identity mechanism and schema); hard problem 5 in §1b (cost). The rest are here.

#### Hard problem 2: sources, and the discovery/enrichment split

**Two jobs.** *Discovery* is learning that an event exists, where, when and how severe. *Enrichment* is attaching headlines, links, posts and media to an event already known. Discovery must be complete and deterministic: a missed event makes the map lie, a duplicated event clutters it, and neither can be measured from inside the system. Enrichment may be lossy and best-effort: a missing headline is invisible. Those properties dictate the machinery. Discovery comes from feeds with stable ids, polled on a schedule that does not depend on you. Enrichment comes from APIs you can query by event and by time range, run when the laptop is on, and backfilled when it was not.

| Job | Sources in v1 | What they contribute |
|---|---|---|
| Discovery (the only creators of events) | GDACS (JSON SEARCH API; RSS fallback), NASA EONET v3, and since M2 Copernicus EMS Rapid Mapping activations (`gdacsId`, centroid, category, countries) | Hazard code, stable id and episode id, coordinates (GDACS also a bbox, EONET often a polygon), alert level and score, GLIDE number, start and end dates, country, a paragraph of description, links to the source's own page; a Copernicus activation adds a "satellite mapping was activated" severity flag and, on 2026-09-17, mostly named disasters the other two had not (8 of 9) |
| Discovery support (join, never create) | ReliefWeb disasters (GLIDE, primary type, country, narrative) | A deterministic cross-source key (the GLIDE number) and a narrative for M3 |
| Enrichment | GDELT DOC 2.0, ReliefWeb reports, Bluesky, YouTube, Mastodon; Reddit only if its approval request succeeds | Headlines with `socialimage` thumbnails in 65 languages; humanitarian reports with casualty figures; posts with image URLs; video links |
| Later | Wikipedia page summaries for named storms, Meteoalarm and NWS as a warnings layer, USGS for small earthquakes | Narrative for major events; forecast warnings; completeness for geophysical hazards |

**The class of authoritative feeds, judged on merit.** Strengths: free and keyless (GDACS, EONET, Copernicus, USGS, NWS all verified today); typed hazard codes; stable ids with episodes, so a five-day cyclone is one thing; coordinates and often footprints; severity already scored; permissive licences (GDACS "public domain / CC BY 4.0 with credit", NASA and USGS public domain, Copernicus open by EU regulation, Meteoalarm CC BY 4.0); deterministic, so the same input always yields the same map. Weaknesses: threshold bias, because GDACS alerts on humanitarian impact, so a European hailstorm or a regional heatwave rarely appears; latency of hours; EONET's wildfire and North America bias; no photographs and little narrative; and GDACS's wildfire volume (218 wildfire items in today's feed against 16 floods) means the map needs a severity filter on day one. Verdict: they are the spine, and news is layered on top.

**This contradicts the README's implied ordering**, which goes news → cluster → events. Doing it the README's way would cost you four things. First, relevance and place classification for roughly 2,000 hazard-keyword articles a day, which is about $3–4 a day on a cheap cloud model or ten-plus CPU-hours a day locally, before a single event exists. Second, geocoding from prose at volume, which Nominatim's policy forbids for scheduled scripts (4 requests a minute, verified) and which therefore means paying. Third, clustering with no anchor, where both error directions are common and the human review load grows with volume. Fourth, severity, dates and casualty numbers extracted by a model rather than read from a field, which puts the least reliable part of the pipeline under the most important columns. Roughly five to ten times the model bill, and most of your engineering time spent on de-duplication with no ground truth.

**Your proposal: a swarm of cheap models, possibly with browser automation, instead of paid news and social APIs.**

*For discovery: no.* Three reasons. The typed feeds already do discovery deterministically, for free, with better metadata than any model will extract from a web page. A browsing swarm's cost scales with pages visited and its recall is unknowable: a run that finds nothing looks exactly like a quiet day, which is the silent failure you should fear most in a system you check twice a week. And headless browsing of news and social sites is precisely where terms-of-service exposure lives (X's terms now quote liquidated damages for scraping; Google News RSS is licensed for personal feed readers only). The arithmetic is also decisive: a stripped page is about 3,000 tokens, so 3,000 pages a day on Haiku 4.5 is about $9 a day before output tokens; Browserbase's free plan is one browser-hour a month and its $20 plan is 80% of your ceiling before any tokens (verified).

*For enrichment: yes, in a narrow form, and not as a swarm.* One small model doing structured extraction over text you already hold through APIs whose terms permit it, with a strict JSON schema, deterministic validation, a golden set and a spend ledger. Nothing to click, so no browser. The validation is what makes it safe: a hazard type that disagrees with the lexicon is vetoed; a place is accepted only if it geocodes within twice the hazard radius of the anchored event; a casualty figure is accepted only with an evidence span that is a verbatim substring of the source text; a date must parse; anything else becomes "unknown" rather than a guess. Ollama's `format` parameter and Claude's structured outputs both constrain the JSON shape (verified), so malformed output is a code path, not a surprise.

**Social media, costed at the cheapest version that still adds something.** Links and metadata only, no media bytes, no paid tier: about 1 KB per post, so 500 posts a day is 15 MB a month; API cost zero; the only real cost is the compliance work each platform's terms require (deletion checks). Ranked by effort-to-value:

1. **Bluesky.** Small effort: one account, one app password, two HTTP calls. Moderate-to-high value: meteorologists and storm chasers post there, images have stable CDN URLs, there is a `lang` filter and `since`/`until` for backfill. Note that the public unauthenticated search endpoint returned 403 today, so the collector authenticates against your own PDS (3,000 requests per 5 minutes per IP, verified). Its developer guidelines require a way to delete content a user has deleted; `document.removed_at` and a periodic existence check cover that.
2. **YouTube Data API.** Small-to-medium effort (a Google Cloud project and a key). High value for the README's "videos" requirement. Gate: 100 `search.list` calls a day under the 2026 granular quota (verified). Compliance: stored metadata refreshed or dropped within 30 days.
3. **Mastodon.** Tiny effort (unauthenticated tag timelines, 300 requests per 5 minutes, verified). Low value because of volume. Worth its 30 lines.
4. **Reddit.** Medium effort: an OAuth script app, an approval request under the Responsible Builder Policy (now mandatory, verified), and 48-hour deletion compliance. High value: photographs, videos, local subreddits. Apply in week one; integrate only if approved.
5. **X.** Pay-per-read at $0.005 a post (verify). Reachable at up to 1,000 reads a month for about $5, but the worst value per euro and the only per-item recurring cost in the plan. Excluded from v1.

Dropped outright: Instagram (business account, app review, 30 hashtags per 7 days), TikTok (institutional researchers only), Facebook, Telegram (no public read API for arbitrary channels). Two of the README's four named platforms are unreachable, and one of the remaining two is gated.

#### Hard problem 3: geocoding

Four tiers, tried in order, every answer cached including "not found":

| Tier | Provider | Precision | Limits and licence (verified 2026-09-16 unless marked) | Used for |
|---|---|---|---|---|
| 0 | The feed itself | Point or polygon as supplied | Same licence as the feed | Every event's primary geometry; no geocoding needed |
| 1 | Local GeoNames `cities500` plus admin1 and country tables, loaded once into SQLite with FTS5 | City, admin1 or country centroid | CC BY 4.0, attribution in the app; unlimited, offline | Roughly 90% of place names in disaster news (towns, provinces, countries) |
| 2 | GeoNames web service `searchJSON` with your free username | Same as tier 1, plus alternate names in other languages and places under 500 inhabitants | 10,000 credits a day, 1,000 an hour, 1 credit per search; CC BY 4.0 | Names the local dump misses |
| 3 | Public Nominatim | Street, landmark, exact address | 4 requests a minute for scripts run on a schedule, single thread, identifying User-Agent, results must be cached; ODbL attribution | Streets and landmarks only, rare in this data |

Precision is recorded on every geometry and every extracted place (`exact`, `street`, `city`, `admin1`, `country`, `unresolved`). Disambiguation uses the country hint from the text or from the anchored event, then population. **An event whose location cannot be resolved does not exist in v1**, because events only come from feeds that carry coordinates. A *document* whose places cannot be resolved still attaches on text and time alone under a stricter threshold (§3) and contributes no geometry. A v2 candidate event without a resolved location is held in review and never drawn.

**Multiple pins per event.** In the data model, yes, from day one: `event_geometry` is one-to-many and typed, and it is needed anyway for footprints from EONET and Copernicus and for cyclone tracks. From M3, places extracted from attached documents are stored as `role = 'mention'` geometries linked to the document. On the v1 map, no: one marker per event at the primary centroid plus its footprint polygon. Several markers for one flood is a user-interface question (which one do I click?) that belongs to a real frontend. The README's idea survives in the schema and is deferred on the map.

#### Hard problem 4: media

The rule stands: **store references and provenance, never copies of bytes.** For each item: the media URL, the page it was found on, the source, the discovery time, and the author or publisher. Rendering by URL in the Streamlit sidebar (`st.image(url)`, a link for video) costs nothing and needs no stored bytes, so thumbnails *are* in v1, which is more than the README's "plain link is acceptable".

Two caveats and one thing it forecloses. References rot: publisher `og:image` URLs and Reddit previews expire or move, so the sidebar always falls back to the page link when an image fails to load. Some publishers block hot-linking by checking the referrer; same fallback. What the rule forecloses is de-duplicating the same photograph across Bluesky, Reddit and a newspaper, which needs perceptual hashing of bytes, and any offline or archival viewing. Neither matters for a private v1, and adding a bounded thumbnail cache later is a policy change, not a schema change: `document.media_url` stays the reference and a cache table would key on it.

#### Hard problem 7: freshness, and the laptop problem

**The number: 3 hours for the spine, 24 hours for enrichment.** GDACS and EONET update a few times a day; you will look at the map a few times a day; the failure that feels like death is a hurricane that is on the news and not on the map for half a day. Anything under an hour buys nothing until warning feeds exist. From the number, the architecture follows. Cron, not a queue: eight runs a day of a stateless fetch have nothing to queue. Polling, not streaming: the feeds are pull-only, and the one stream that exists (Bluesky's Jetstream) needs an always-on process, which is the one thing you do not have. Incremental everywhere: ingest upserts on natural keys and replays only unseen files; resolve and attach process only rows without a decision; a full rebuild is a flag, not the default.

**The laptop.** The spine collectors run in GitHub Actions at `7 */3 * * *` (seven minutes past, because the docs warn that runs at the top of the hour are delayed under load, verified) and commit to an orphan `data` branch with `GITHUB_TOKEN`, which needs no secret and, usefully, does not trigger other workflows (verified). They are keyless, so nothing sensitive is stored in GitHub. Enrichment collectors run on the laptop inside `eww sync` and query by time range from the last successful run: GDELT `startdatetime`/`enddatetime`, Bluesky `since`/`until`, YouTube `publishedAfter`. So the spine has no gaps, and enrichment gaps close themselves when the laptop returns.

**Gaps you should accept.** Headlines and posts for an event lag until the laptop is next on; if that is a week, the event is still on the map with its authority description, just without a feed. GDELT's search lookback is finite (about three months, **verify**), so a very long absence loses headlines for that period. Actions cron drifts by minutes under load; irrelevant at a 3-hour target.

**How the system records that it was not running.** Every collector run, successful or not, appends a line to `runs/<date>.jsonl` in the data branch, ingested into `collector_run`. A `heartbeat` view compares expected slots (every 3 hours) with observed ones. The GeoJSON `meta` carries `last_collector_run_at` and `missed_runs_7d`, the viewer shows a status strip built from it, and `eww doctor` prints the gaps. A quiet world shows recent runs with zero new items; a stopped pipeline shows missing slots. One more caveat from the docs: scheduled workflows are disabled after 60 days without activity only in *public* repositories (verified), and the collector's own commits count as activity, so this cannot bite a private repo and is unlikely to bite a public one.

#### Hard problem 8: the frontend

**The contradiction, resolved.** "No frontend is needed right now" and "website usability, front-end design" cannot both be MVP statements. I am planning against the first: v1 ships a disposable, local, Python-native viewer, and usability and design as disciplines wait for a real frontend. The README's MVP interest is in *seeing* events on a map, which the viewer delivers.

**The disposable Python layer is the right call, and I am not splitting the difference.** Learning enough JavaScript, CSS and tooling to build and debug an interactive map is two to four weeks of part-time work that adds no data and fails your own test: a plan whose second milestone requires learning a frontend framework. Streamlit plus folium gives pan, zoom, click, clustering, polygons and browser geolocation from Python, in one process, with a verified click-to-sidebar round trip. The GeoJSON boundary in §2 means a real frontend later is additive, not a rewrite: MapLibre reading `GET /events.geojson` is a weekend project *after* the data exists, and by then you will know what the map should do. What would make me argue the other way: if you already wanted to learn web development for its own sake, in which case the same boundary lets you do it in parallel.

**How much web technology you must learn.** Zero JavaScript. Zero CSS. About 15 lines of templated HTML if you want rich popups, and none if you use the sidebar. Three concepts: a URL with query parameters, the shape of a GeoJSON file, and that a browser on your machine talks to `localhost:8501`. Separately, about 40 lines of GitHub Actions YAML, which is not web technology but is new.

**What you must avoid so the swap stays cheap** is listed at the end of §2. The short version: the viewer imports `eww.api` and nothing else, and every decision about what to show lives below that line.

## 1b. Monthly cost

Everything free is marked free. Euro figures assume $1 ≈ €0.90; check the rate. "Verified" means the official page was read on 2026-09-16.

| Item | v1 monthly cost | Basis | Status |
|---|---|---|---|
| GitHub: private repo + Actions cron | Free | 2,000 minutes/month included on the Free plan; 8 runs/day × ~1.5 min × 30 ≈ 360 min | Verified |
| GitHub: `data` branch storage | Free | Estimated < 400 MB/year after git delta compression; guidance is < 1 GB ideal, < 5 GB strongly recommended | Guidance verified; the volume is an estimate, measured in M1 |
| SQLite, Python 3.12, uv, Streamlit, folium, streamlit-folium, sentence-transformers, spaCy, numpy | Free | Open source, local | Versions verified |
| Ollama + qwen2.5:7b-instruct (local extraction and summaries) | Free | Already installed on your machine; 24 GB RAM is ample for a 7–8B model | Verified installed |
| GDACS, NASA EONET, ReliefWeb API | Free | No keys; ReliefWeb asks for an `appname` parameter | Terms: **verify** (see §7) |
| GDELT DOC 2.0 API | Free | No key; polite spacing between calls | Rate limit is informal: **verify** |
| GeoNames gazetteer download | Free | CC BY 4.0, attribution shown in the app | Verify licence text |
| Nominatim public API | Free | ≤ 1 request/second, identifying User-Agent, results cached locally | Policy wording: **verify** |
| Bluesky (your account + an app password), Mastodon | Free | Post search requires authentication (public endpoint returned 403 today); 3,000 requests / 5 min per IP on the PDS | Verified |
| YouTube Data API key | Free | 100 `search.list` calls/day under the 2026 granular quota; stored metadata must be refreshed or dropped within 30 days | Verified |
| Reddit Data API | Free, **gated** | 100 queries/minute per OAuth client, but access now requires an approval request; deletion compliance within 48 h | Verified; approval outcome unknown |
| Open-Meteo (weather at the clicked pin, viewer-time only) | Free | Non-commercial use, no key | Limits: **verify** |
| Map tiles (OpenStreetMap or CARTO Positron via folium) | Free | Single viewer, light use | OSM tile policy: **verify** |
| Electricity for local models | < €1 | ~2 h/day of CPU load ≈ 3 kWh/month | Estimate |
| Cloud model, optional fallback: Claude Sonnet 5 via the Batch API | €0 by default; hard cap $10 ≈ €9 | Unit cost below; the cap is enforced in code | Prices from Anthropic's table cached 2026-06-24: **verify** |
| Hosting (M6, optional): Streamlit Community Cloud (one private app) + Cloudflare R2 (10 GB free) | Free | Sleeps after 12 h without traffic, wakes on visit | Verified |
| **Total** | **€0–€1 by default; ≤ €10 with the cloud fallback switched on** | Ceiling €25 | |

X is deliberately absent. Its API is now pay-per-usage only, at $0.005 per post read (**verify**); 1,000 reads a month would cost about $5, which fits, but it is the only source with a per-item recurring cost that scales with volume, so it is the last social source you would add, not the first.

**Unit cost of one document (hard problem 5).** Prompt shape for structured extraction: fixed instructions plus JSON schema plus three examples ≈ 1,500 tokens; per-document payload (title, excerpt ≤ 2,000 characters, the five candidate events) ≈ 800 tokens; output JSON ≈ 150 tokens.

| Path | Price basis | Cost per document |
|---|---|---|
| Local: Ollama, qwen2.5:7b-instruct, CPU | Electricity only | ≈ €0.0002, but throughput-bound: 20–30 s per document, roughly 120–180 documents per hour |
| Cloud A: Claude Haiku 4.5, Batch API (−50%), no caching (its minimum cacheable prefix is 4,096 tokens; ours is 1,500) | $1 in / $5 out → $0.50 / $2.50 batched | 2,300 × $0.50/M + 150 × $2.50/M = $0.00115 + $0.000375 ≈ **$0.0015** |
| Cloud B: Claude Sonnet 5, Batch API (−50%), prefix cached (minimum 1,024 tokens) | $2 / $10 → $1 / $5 batched; cache reads at 0.1× | 1,500 × $0.10/M + 800 × $1/M + 150 × $5/M = $0.00015 + $0.0008 + $0.00075 ≈ **$0.0017** |
| Either cloud model interactively (no batch) | Double the batched price | $0.0030–$0.0034 |

At this prompt shape Sonnet 5 with caching costs the same as Haiku 4.5 without it, so the cloud fallback is **Sonnet 5 through the Batch API**, not Haiku. A per-event summary refresh (≈ 1,500 cached + 2,500 variable input + 250 output tokens) costs ≈ $0.0039 on the same path.

**Monthly, at a realistic volume.** Documents that reach the extraction stage ≈ 150/day (raw GDELT volume is several times higher, but hazard lexicon and candidate-event blocking stop most of it before any model). About 40% of those need a model; the rest resolve with lexicon, NER and gazetteer → 60/day → 1,800/month → $3.1. Summaries: 40 active events refreshed once a day → 1,200/month → $4.7. Cloud-only total ≈ $7.8 ≈ €7, inside the cap. Default path (local) → $0.

**Where deterministic code, a local model or a cached embedding replaces a paid call**, in descending order of money saved:

1. Discovery from feeds instead of from the web. Removes the entire "classify the internet" bill (a browsing swarm would spend roughly 3,000 tokens per page visited; 3,000 pages a day on Haiku 4.5 is about $9 a day before output tokens).
2. Text similarity from local embeddings. Replaces every "is this the same event?" model call in both resolution and attachment, and replaces a paid embedding API. The single biggest saving.
3. Hazard type from a lexicon (English and Italian to start). Resolves roughly 60% of documents without a model.
4. Places from spaCy NER plus the local GeoNames gazetteer, Nominatim only on a miss, every answer cached forever including misses. Replaces paid geocoding entirely.
5. Casualty figures by regex over ReliefWeb and GDACS text (numbers adjacent to "dead, killed, injured, missing, displaced"), model only as a fallback and only with a verbatim evidence span.
6. Summaries from authority text first (GDACS description, ReliefWeb disaster overview); a model only when at least three attached documents add information, at most once a day per event.
7. Dates from feed fields and GDELT `seendate`, never extracted by a model.
8. Weather from Open-Meteo at click time, never stored.

**What scales with volume, and what stays flat.** Flat: Actions minutes (per run, not per event), tiles, gazetteer, model downloads. Scales with active events: per-event GDELT, Bluesky and YouTube queries (YouTube's 100 searches/day is the first ceiling you hit: above 50 active events each is searched every other day), and summary refreshes. Scales with documents: embedding CPU time (fine), storage (≈ 1 KB per document row, so 500,000 documents ≈ 0.5 GB, fine for SQLite but not for a 0.5 GB hosted free tier), Nominatim calls (sub-linear thanks to the cache), and cloud extraction. Cloud extraction is the only *paid* component that scales; the monthly cap converts it into a flat cost by design, and when the cap is hit the pipeline continues on the local model.

## 2. Architecture

```
  keyless typed feeds ──────►┌─────────────────────────────────────────────────────────────────┐
  GDACS · EONET · Copernicus │ SPINE COLLECTOR — GitHub Actions cron `7 */3 * * *`, stateless   │
                             │ fetch → normalise → snapshots/<source>/<utc-ts>.json             │
                             │ append runs/<date>.jsonl (heartbeat) → commit + push to `data`    │
                             └────────────────────────────┬────────────────────────────────────┘
                                                          │ git fetch — only when the laptop is on
┌─────────────────────────────────────────────────────────▼──────────────────────────────────────┐
│ LAPTOP — one Python package `eww`, one SQLite file, one Streamlit process                       │
│                                                                                                 │
│  `eww sync`  =  ingest → resolve → enrich → extract → embed → attach → summarise, then exit     │
│                                                                                                 │
│  ingest     replay unseen snapshots → source_record · collector_run              (idempotent)   │
│  resolve    source_record → event      (anchored identity; merge proposals for the grey zone)   │
│  enrich     per-event, time-ranged queries since the last successful run → document             │
│             GDELT · ReliefWeb · Bluesky · YouTube · Mastodon   (links and metadata only)        │
│  extract    lexicon → NER → gazetteer → GeoNames → Nominatim → [optional model] → extraction    │
│  embed      local sentence-transformers → document_embedding, event centroid vectors            │
│  attach     document ↔ event scoring → event_document {attached | candidate | rejected}          │
│  summarise  per-event summary, local model by default, cloud behind a hard cap → event.summary  │
│  review     Streamlit tab: accept / reject merge proposals and candidate attachments            │
│                                                                                                 │
│             ═══════════════════  SQLite  data/eww.sqlite  (WAL)  ═══════════════════            │
│                                                                                                 │
│  api        events_geojson(filters) → GeoJSON FeatureCollection       ◄── THE SWAP BOUNDARY     │
│  viewer     Streamlit + folium (Leaflet): pan / zoom / click, filters, details in the sidebar   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
   viewer-time only, never stored:  Open-Meteo weather at the clicked pin · map tiles · thumbnails
   fetched by the browser from their original URLs
```

**Components, one line each**

- **Spine collector** (GitHub Actions, and byte-for-byte the same code on the laptop): stateless fetchers for GDACS, EONET and Copernicus EMS; needs no keys; output is files on the `data` branch, never the database.
- **Data branch** (`data`, an orphan branch of this repo): the raw archive and the system of record for *what was observed, when*. Every downstream table can be rebuilt from it, which is what makes iterating on the clustering logic safe.
- **Ingest**: the only writer of `source_record` and `collector_run`; replays snapshot files it has not seen; idempotent on natural keys plus payload hash.
- **Resolve**: the only code path that inserts into `event`; applies the identity rules in §3 and writes `merge_proposal` rows for the grey zone.
- **Enrichment collectors** (laptop only): for each active event, query GDELT, ReliefWeb, Bluesky, YouTube and Mastodon by place, hazard, name and *time range since the last successful run*, so a week offline is a week of backfill, not a hole. They write `document` rows: URL, title, excerpt, publisher, time, thumbnail URL.
- **Extract**: hazard lexicon → NER → local gazetteer → GeoNames web service → Nominatim → optional model, in that order, stopping as soon as the document is classified and located. Each stage is a separate function so it can be removed or replaced alone.
- **Embed**: local sentence-transformers; one vector per document and one running centroid per active event.
- **Attach**: scores each new document against candidate events and writes `event_document` with a status; never creates events.
- **Summarise**: refreshes `event.summary` at most once per day per active event, and only when new attached documents add information.
- **Review**: a Streamlit tab where you accept or reject merge proposals and candidate attachments. This is the entire human-in-the-loop.
- **API**: `eww.api.events_geojson(filters)`, one Python function returning a GeoJSON FeatureCollection; exposed as a CLI exporter from M0 and as a FastAPI route in M6.
- **Viewer**: Streamlit + folium; consumes only what the API returns; disposable by design.

**Process count.** One long-lived process (`streamlit run app.py`) and one short-lived command (`eww sync`) that runs the chain above and exits. GitHub Actions runs the spine collector. Nothing else is resident on your machine.

**The five swap boundaries**

| Boundary | v1 implementation | Later implementation | What has to change |
|---|---|---|---|
| Spine collector sink | JSON files committed to the `data` branch | Cloudflare R2 bucket, or direct writes to a hosted Postgres | One `Sink` class in the collector; ingest reads from the new location |
| Storage engine | SQLite, WAL mode | Postgres + PostGIS + pgvector (Neon or Supabase) when a second writer appears | `db.py` connection and placeholder style; the DDL in §3 is written to be portable |
| Extractor backend | Ollama on the laptop | Claude through the Batch API | One class behind the `Extractor` interface; the ledger and the cap are shared |
| Geocoder provider | Local gazetteer, GeoNames web service, Nominatim | Self-hosted Photon, or a paid provider | One class behind the `Geocoder` interface; `geocode_cache` is provider-keyed and stays |
| Viewer | Streamlit + folium reading `events_geojson()` in-process | Any web map (MapLibre, Leaflet, deck.gl) reading `GET /events.geojson` | Nothing in the pipeline. The FastAPI wrapper is about 30 lines |

**The GeoJSON contract, the interface you must protect**

```
events_geojson(since, until=None, hazard=None, min_severity=0.0, status=None,
               bbox=None, include_footprints=False, limit=2000) -> FeatureCollection

FeatureCollection.meta   (a top-level "meta" member; RFC 7946 allows foreign members)
  data_as_of, last_collector_run_at, missed_runs_7d, expected_runs_7d, generated_at, filters_applied
  (last_collector_run_at is the last run that produced data; expected_runs_7d was added in M1 so the
   viewer can say "n of m runs missed")

Feature, one per event; geometry = Point at the primary centroid
  properties:
    event_id, hazard_type, title, status, started_at, ended_at, last_observed_at,
    severity_score (0..1), severity_label, country_iso3, precision, glide_number,
    source_ids [..], ems_activation (bool), doc_count, post_count, video_count,
    summary, summary_updated_at, thumbnail_url (a reference, may be null),
    detail_url (the primary source's own page for the event)

Feature, only when include_footprints=True; geometry = Polygon or LineString
  properties: event_id, role ("footprint" | "track" | "impact_area"), observed_at, source_id
  (the canonical event's own rows only: a merge moves the records and the canonical event's refresh
   rebuilds their polygons under its id, so a merged member's rows would draw twice; since M2)

Filters: since/until accept ISO 8601 or relative ("14d"); hazard is a list of hazard_type values;
bbox is [min_lon, min_lat, max_lon, max_lat]; every filter is applied inside this function.
limit=0 returns every event in the window; the CLI exporter and the viewer pass it, so their pin
count always equals the SQL count of events observed in the window (added after M0).
ems_activation is true when a record with source_id 'copernicus' sits on the event (M2);
`eww export --count` prints the number of Point features for filter-parity checks (M2).
```

**What you must not do, so the viewer swap stays cheap**

- Never let the viewer run SQL or import anything from `eww` except `eww.api` (reads) and, since M2, `eww.review` (the Review tab's three write actions: accept, reject, revert; it returns plain values and holds the SQL). If the viewer needs a field, add it to the contract.
- Never put filtering, severity mapping or merge-pointer resolution in the viewer. The viewer renders; it does not decide.
- Never keep state in Streamlit session state that a future frontend would need. Session state holds UI state (selected pin, open tab), nothing else.
- Never rely on folium popup HTML for anything the API does not already provide. The sidebar is built from `properties`, and a future frontend will do the same.
- Keep the contract in one file (`eww/api.py`) with a golden example in `tests/fixtures/events.geojson`; a test asserts the exporter's output validates against it.

## 3. Data model

SQLite DDL. Schema version 1 is everything up to the FUTURE tables; version 2 (M1) adds the `heartbeat` view at the end, applied to existing databases by `sql/migrations/0002_heartbeat_view.sql`. The only SQLite-specific constructs are `STRICT`, `fts5`, `strftime`/`printf` in the view and the two `PRAGMA` lines; everything else is plain SQL and moves to Postgres by dropping `STRICT`, mapping `TEXT` timestamps to `timestamptz`, `BLOB` to `vector(384)` and the JSON `TEXT` columns to `jsonb`. All timestamps are ISO 8601 UTC strings. All surrogate keys are ULIDs (sortable, generated in Python, no coordination). Columns and tables that exist only for the future are marked **FUTURE**; they are created now so that contributions land in existing tables rather than in a migration.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT;

-- ============================================================ provenance
CREATE TABLE source (
  source_id     TEXT PRIMARY KEY,   -- 'gdacs','eonet','reliefweb','gdelt','bluesky','reddit','youtube','user'
  kind          TEXT NOT NULL CHECK (kind IN ('authority','news','social','video','user')),
  display_name  TEXT NOT NULL,
  terms_url     TEXT,
  attribution   TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE collector_run (        -- heartbeat: one row per (run, source), copied from runs/*.jsonl
  run_id        TEXT NOT NULL,      -- ULID minted by the collector
  source_id     TEXT NOT NULL REFERENCES source(source_id),
  scheduled_for TEXT NOT NULL,      -- the 3-hour slot this run served, e.g. 2026-09-16T09:00:00Z
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL CHECK (status IN ('ok','partial','failed')),
  http_status   INTEGER,
  items_seen    INTEGER NOT NULL DEFAULT 0,
  snapshot_path TEXT,               -- path inside the data branch; NULL when the fetch failed
  error         TEXT,
  PRIMARY KEY (run_id, source_id)
) STRICT;
CREATE INDEX collector_run_slot ON collector_run (source_id, scheduled_for);

CREATE TABLE snapshot_ingest (      -- which raw files the laptop has replayed
  snapshot_path TEXT PRIMARY KEY,
  ingested_at   TEXT NOT NULL,
  items_seen    INTEGER NOT NULL,
  items_new     INTEGER NOT NULL,
  items_changed INTEGER NOT NULL
) STRICT;

-- ============================================================ the spine
CREATE TABLE event (
  event_id             TEXT PRIMARY KEY,   -- ULID; never reused, never deleted
  hazard_type          TEXT NOT NULL CHECK (hazard_type IN (
                         'flood','tropical_cyclone','severe_storm','wildfire','heatwave','coldwave',
                         'drought','landslide','volcano','earthquake','tsunami','other')),
  title                TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('candidate','active','ended','merged','rejected')),
  started_at           TEXT NOT NULL,
  ended_at             TEXT,
  last_observed_at     TEXT NOT NULL,
  severity_score       REAL CHECK (severity_score BETWEEN 0 AND 1),   -- normalised across sources
  severity_label       TEXT,               -- as the primary source says it: 'Orange', 'M 6.1', '12,000 ha'
  centroid_lat         REAL,               -- denormalised from the primary geometry for cheap bbox filters
  centroid_lon         REAL,
  country_iso3         TEXT,
  glide_number         TEXT,               -- cross-source key when any source supplies one
  summary              TEXT,
  summary_updated_at   TEXT,
  summary_method       TEXT,               -- 'authority', 'llm:qwen2.5-7b', 'llm:claude-sonnet-5'
  merged_into_event_id TEXT REFERENCES event(event_id),   -- soft merge: the row stays, the pointer moves
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  created_by           TEXT NOT NULL DEFAULT 'pipeline',  -- FUTURE: contributor_id
  revision             INTEGER NOT NULL DEFAULT 1         -- FUTURE: optimistic locking for edits
) STRICT;
CREATE UNIQUE INDEX event_glide  ON event (glide_number) WHERE glide_number IS NOT NULL AND merged_into_event_id IS NULL;
CREATE INDEX event_bbox   ON event (centroid_lat, centroid_lon);
CREATE INDEX event_window ON event (hazard_type, status, last_observed_at);

CREATE TABLE source_record (        -- one typed observation from an authoritative feed
  source_record_id TEXT PRIMARY KEY,
  source_id        TEXT NOT NULL REFERENCES source(source_id),
  external_id      TEXT NOT NULL,   -- GDACS eventid, EONET id, ReliefWeb disaster id
  external_episode TEXT NOT NULL DEFAULT '',  -- GDACS episodeid, EONET geometry date; '' when the source has none
  event_id         TEXT REFERENCES event(event_id),   -- NULL only between ingest and resolve
  hazard_type      TEXT NOT NULL,
  title            TEXT,
  observed_at      TEXT NOT NULL,
  started_at       TEXT,
  ended_at         TEXT,
  lat              REAL,
  lon              REAL,
  severity_raw     TEXT,            -- JSON: whatever the source says (alert level, score, magnitude, area)
  glide_number     TEXT,
  payload          TEXT NOT NULL,   -- the raw item, verbatim
  payload_hash     TEXT NOT NULL,
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  UNIQUE (source_id, external_id, external_episode)
) STRICT;
CREATE INDEX source_record_unresolved ON source_record (source_id) WHERE event_id IS NULL;
CREATE INDEX source_record_event      ON source_record (event_id);

-- ============================================================ attachments: news, reports, posts, videos
CREATE TABLE document (
  document_id    TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES source(source_id),
  kind           TEXT NOT NULL CHECK (kind IN ('article','report','post','video')),
  url            TEXT NOT NULL,
  url_canonical  TEXT NOT NULL,     -- lower-cased host, https, tracking parameters stripped, no fragment
  external_id    TEXT,              -- AT-proto URI, Reddit id, YouTube id, ReliefWeb report id
  title          TEXT,
  text_excerpt   TEXT CHECK (length(text_excerpt) <= 2000),   -- never a full article body
  language       TEXT,
  author         TEXT,
  publisher      TEXT,              -- domain or handle
  published_at   TEXT,
  fetched_at     TEXT NOT NULL,
  media_url      TEXT,              -- REFERENCE ONLY: og:image, post image, video thumbnail
  media_kind     TEXT CHECK (media_kind IN ('image','video','none')),
  payload        TEXT,              -- the raw API item, verbatim
  removed_at     TEXT,              -- set when the source reports a deletion; hidden everywhere after that
  UNIQUE (source_id, url_canonical)
) STRICT;
CREATE INDEX document_published ON document (published_at);
CREATE INDEX document_external  ON document (source_id, external_id);

CREATE TABLE event_geometry (       -- many per event, typed. "Multiple pins per event" live here.
  geometry_id      TEXT PRIMARY KEY,
  event_id         TEXT NOT NULL REFERENCES event(event_id),
  role             TEXT NOT NULL CHECK (role IN ('centroid','footprint','track','impact_area','mention')),
  geojson          TEXT NOT NULL,   -- a GeoJSON geometry object
  min_lat REAL NOT NULL, min_lon REAL NOT NULL, max_lat REAL NOT NULL, max_lon REAL NOT NULL,
  precision        TEXT NOT NULL CHECK (precision IN ('exact','street','city','admin1','country','unresolved')),
  observed_at      TEXT,
  source_id        TEXT REFERENCES source(source_id),
  source_record_id TEXT REFERENCES source_record(source_record_id),
  document_id      TEXT REFERENCES document(document_id),   -- set for role = 'mention'
  is_primary       INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1))
) STRICT;
CREATE INDEX        event_geometry_event   ON event_geometry (event_id, role);
CREATE UNIQUE INDEX event_geometry_primary ON event_geometry (event_id) WHERE is_primary = 1;

CREATE TABLE document_extraction (
  document_id       TEXT PRIMARY KEY REFERENCES document(document_id),
  method            TEXT NOT NULL,  -- 'lexicon+ner', 'llm:qwen2.5-7b', 'llm:claude-sonnet-5'
  model_version     TEXT,
  hazard_type       TEXT,
  hazard_confidence REAL,
  places            TEXT NOT NULL DEFAULT '[]',   -- JSON [{name, country_hint, lat, lon, precision, provider}]
  event_date        TEXT,
  figures           TEXT,           -- JSON {dead, injured, missing, displaced, evidence_span}
  extracted_at      TEXT NOT NULL,
  cost_usd          REAL NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE document_embedding (
  document_id TEXT PRIMARY KEY REFERENCES document(document_id),
  model       TEXT NOT NULL,        -- 'paraphrase-multilingual-MiniLM-L12-v2'
  dim         INTEGER NOT NULL,
  vector      BLOB NOT NULL         -- float32 little-endian; brute-force cosine in numpy at v1 scale
) STRICT;

CREATE TABLE event_document (
  event_id    TEXT NOT NULL REFERENCES event(event_id),
  document_id TEXT NOT NULL REFERENCES document(document_id),
  status      TEXT NOT NULL CHECK (status IN ('attached','candidate','rejected')),
  score       REAL,
  score_parts TEXT,                 -- JSON {spatial, temporal, text, hazard}
  method      TEXT NOT NULL,        -- 'rule', 'embedding', 'query' (retrieved for this event), 'human'
  decided_at  TEXT NOT NULL,
  decided_by  TEXT NOT NULL DEFAULT 'pipeline',   -- FUTURE: contributor_id
  PRIMARY KEY (event_id, document_id)
) STRICT;
CREATE INDEX event_document_by_doc ON event_document (document_id);
CREATE INDEX event_document_review ON event_document (status) WHERE status = 'candidate';

-- ============================================================ identity changes, always reversible
CREATE TABLE merge_proposal (       -- the review queue for cross-source identity
  proposal_id TEXT PRIMARY KEY,
  event_a     TEXT NOT NULL REFERENCES event(event_id),
  event_b     TEXT NOT NULL REFERENCES event(event_id),
  score       REAL NOT NULL,
  evidence    TEXT NOT NULL,        -- JSON: distance_km, days_apart, name_match, glide_match, text_sim
  status      TEXT NOT NULL CHECK (status IN ('open','accepted','rejected')),
  created_at  TEXT NOT NULL,
  decided_at  TEXT,
  UNIQUE (event_a, event_b)
) STRICT;

CREATE TABLE event_lineage (
  lineage_id             TEXT PRIMARY KEY,
  action                 TEXT NOT NULL CHECK (action IN ('merge','split','reassign','revert')),
  from_event_id          TEXT NOT NULL REFERENCES event(event_id),
  to_event_id            TEXT NOT NULL REFERENCES event(event_id),
  moved_source_records   TEXT NOT NULL DEFAULT '[]',  -- JSON list of source_record_id, so a revert is exact
  moved_documents        TEXT NOT NULL DEFAULT '[]',
  score                  REAL,
  evidence               TEXT,
  performed_by           TEXT NOT NULL,   -- 'pipeline' | 'human'   (FUTURE: contributor_id)
  performed_at           TEXT NOT NULL,
  reverted_by_lineage_id TEXT REFERENCES event_lineage(lineage_id)
) STRICT;

-- ============================================================ geocoding
CREATE TABLE gazetteer_place (      -- GeoNames cities500 + admin1 + country rows, loaded once (CC BY 4.0)
  geonameid      INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  asciiname      TEXT NOT NULL,
  alternatenames TEXT,              -- comma-separated, as shipped by GeoNames
  lat            REAL NOT NULL,
  lon            REAL NOT NULL,
  feature_class  TEXT NOT NULL,
  feature_code   TEXT NOT NULL,
  country_iso2   TEXT NOT NULL,
  admin1_code    TEXT,
  population     INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX gazetteer_ascii ON gazetteer_place (asciiname, country_iso2);
CREATE VIRTUAL TABLE gazetteer_fts USING fts5(name, asciiname, alternatenames,
  content='gazetteer_place', content_rowid='geonameid');   -- run the 'rebuild' command once after loading

CREATE TABLE geocode_cache (
  query_norm   TEXT NOT NULL,       -- lower-cased, diacritics folded, whitespace collapsed
  country_hint TEXT NOT NULL DEFAULT '',
  provider     TEXT NOT NULL,       -- 'gazetteer' | 'nominatim'
  lat          REAL,                -- NULL together with lon = provider found nothing (negative cache)
  lon          REAL,
  precision    TEXT NOT NULL,
  display_name TEXT,
  bbox         TEXT,                -- JSON [min_lon, min_lat, max_lon, max_lat]
  resolved_at  TEXT NOT NULL,
  PRIMARY KEY (query_norm, country_hint, provider)
) STRICT;

-- ============================================================ spend ledger
CREATE TABLE llm_call (
  call_id       TEXT PRIMARY KEY,
  purpose       TEXT NOT NULL CHECK (purpose IN ('extract','summarise','other')),
  backend       TEXT NOT NULL,      -- 'ollama' | 'anthropic'
  model         TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL,
  cost_usd      REAL NOT NULL,      -- 0 for ollama
  document_id   TEXT REFERENCES document(document_id),
  event_id      TEXT REFERENCES event(event_id),
  called_at     TEXT NOT NULL
) STRICT;
CREATE INDEX llm_call_month ON llm_call (called_at);

-- ============================================================ FUTURE: contributions (created now, empty until v2)
CREATE TABLE contributor (
  contributor_id TEXT PRIMARY KEY,
  handle         TEXT NOT NULL UNIQUE,
  role           TEXT NOT NULL CHECK (role IN ('owner','editor','contributor')),
  created_at     TEXT NOT NULL
) STRICT;

CREATE TABLE contribution (         -- anything a person submits, held until reviewed
  contribution_id TEXT PRIMARY KEY,
  contributor_id  TEXT NOT NULL REFERENCES contributor(contributor_id),
  event_id        TEXT REFERENCES event(event_id),   -- NULL = proposes a new event
  kind            TEXT NOT NULL CHECK (kind IN ('new_event','document','geometry','field_edit','merge','split')),
  payload         TEXT NOT NULL,    -- JSON shaped like the target row(s)
  status          TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected')),
  submitted_at    TEXT NOT NULL,
  reviewed_at     TEXT,
  reviewed_by     TEXT REFERENCES contributor(contributor_id)
) STRICT;

CREATE TABLE event_revision (       -- field-level edit history; the pipeline may write it too
  revision_id     TEXT PRIMARY KEY,
  event_id        TEXT NOT NULL REFERENCES event(event_id),
  revision        INTEGER NOT NULL,
  field           TEXT NOT NULL,
  old_value       TEXT,
  new_value       TEXT,
  changed_by      TEXT NOT NULL,
  changed_at      TEXT NOT NULL,
  contribution_id TEXT REFERENCES contribution(contribution_id),
  UNIQUE (event_id, revision, field)
) STRICT;

-- ============================================================ heartbeat (schema version 2)
-- Every 3-hour slot from the first collector run to now, and whether an 'ok' run started inside the
-- slot's 45-minute grace window (docs/architecture.md §4, M1). Plain SQL apart from strftime/printf.
CREATE VIEW heartbeat AS
WITH RECURSIVE
  bounds AS (
    SELECT substr(MIN(started_at), 1, 11)
             || printf('%02d', (CAST(substr(MIN(started_at), 12, 2) AS INTEGER) / 3) * 3) || ':00:00Z' AS first_slot,
           strftime('%Y-%m-%dT', 'now')
             || printf('%02d', (CAST(strftime('%H', 'now') AS INTEGER) / 3) * 3) || ':00:00Z' AS last_slot
    FROM collector_run
  ),
  slots(scheduled_for) AS (
    SELECT first_slot FROM bounds WHERE first_slot IS NOT NULL
    UNION ALL
    SELECT strftime('%Y-%m-%dT%H:%M:%SZ', scheduled_for, '+3 hours') FROM slots
    WHERE scheduled_for < (SELECT last_slot FROM bounds)
  )
SELECT s.scheduled_for,
       strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes') AS deadline,
       (SELECT COUNT(*) FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS ok_runs,
       (SELECT COUNT(DISTINCT r.source_id) FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS sources_ok,
       EXISTS (SELECT 1 FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS served
FROM slots s;
```

**Where event identity is enforced — exactly these seven places**

1. `source_record UNIQUE (source_id, external_id, external_episode)`. A feed item cannot exist twice. A re-seen item updates `last_seen_at`, and `payload` only when `payload_hash` changed.
2. `source_record.event_id`. Every authoritative observation belongs to exactly one event once `resolve` has run. This is checked by a query in `eww doctor` rather than a `NOT NULL` constraint because ingest and resolve are separate, restartable steps.
3. `eww.resolve.resolve_record()` is the only function in the codebase that inserts into `event`. Collectors, ingest and attach cannot create events. In v2, orphan clustering becomes a second caller of the same function, not a second insert path.
4. `event.merged_into_event_id` plus `event_lineage`. Identity changes are pointer moves and log rows, never deletes or row rewrites. The API follows pointers to the canonical event; a revert restores the pointer and moves back exactly the rows listed in `moved_source_records` and `moved_documents`.
5. `event_glide` partial unique index. Two live events cannot share a GLIDE number; `resolve` merges on GLIDE before it looks at anything else.
6. `event_document` primary key. A document attaches to an event at most once; its `status` is the decision and `score_parts` is the evidence.
7. `document UNIQUE (source_id, url_canonical)`. The same URL from the same source is one document. Syndicated copies on different domains stay separate rows and are grouped at display time by embedding similarity (cosine ≥ 0.95), not merged.

**Which rows exist purely for the future**

- Tables: `contributor`, `contribution`, `event_revision`.
- Columns: `event.created_by`, `event.revision`, `event_document.decided_by` (already written with the literal `'pipeline'` or `'human'`), `document.removed_at` (also needed now, for Reddit deletion compliance).
- Roles: `event_geometry.role = 'mention'` is written from M3 but not drawn in v1.
- Status: `event.status = 'candidate'` is reserved for v2 news-discovered events and is never written in v1.

**Severity normalisation** is a function, not a table (`eww/severity.py`, M2): GDACS Green / Orange / Red → 0.33 / 0.66 / 1.0; inside a band the continuous `episodealertscore` (0 to 2.5 observed) adds at most 0.03 as a tie-break, because the SEARCH API's `alertscore` turned out to be quantised to 1 / 2 / 3 (one value per band over 2,405 records on 2026-09-17), and Red stays exactly 1.0. EONET carries no alert level, so `magnitudeValue` is scaled per unit through piecewise-linear points in config (knots on the Saffir-Simpson steps 34 / 64 / 96 kt → 0.33 / 0.66 / 1.0; hectares with the 5,000 ha GDACS threshold at 0.33, 30,000 at 0.66 and 100,000 at 1.0; acres converted to hectares) and is 0.4 when absent; a Copernicus activation sets `ems_activation` and raises the score to at least 0.66; a ReliefWeb disaster contributes 0.5 when it is the only source (M3). The primary source of an event (`PRIMARY_SOURCE_ORDER`: GDACS, then Copernicus, then EONET) supplies its title, pin, detail link and `severity_label`, which keeps the source's own wording so the map can say "Orange" rather than "0.675".

### How identity is decided (hard problem 1)

**What an event is.** A row in `event` is one real-world hazard occurrence: one hazard type, one time window, one primary location plus any number of typed geometries, a normalised severity and a title. It is what a pin is. Successive GDACS episodes about the same cyclone are `source_record` rows under one event, not separate events. A cyclone that hits two countries is one event with a `track` geometry, not two events.

**Step 1 — anchored resolution, feeds → events.** Deterministic; no model involved.

```
resolve_record(rec):                                   # one source_record row; GDACS records resolve first
    A = aggregation_radius(rec.hazard_type)                                    # identity.yaml: 25 km unless the hazard says otherwise
    beyond = []                                                                # id-named events too far away to share a pin
    if rec.glide_number and (ev := live_event_by_glide(rec.glide_number))
       and ev holds no record of rec.source_id under another external_id:    # GDACS gave two cyclones one GLIDE
        if closest_distance(rec, ev) <= A: attach(rec, ev); return
        beyond.append(ev)
    if (ev := event_by_external_id(rec.source_id, rec.external_id)):         # a new episode or track point: never gated
        attach(rec, ev); rescore(ev); return                                  # the grown track may reach another feed now
    for ev in events named by rec.linked_ids()                                # Copernicus gdacsId, the GDACS URL EONET cites
          or whose records link to (rec.source_id, rec.external_id):          # the mirror arrived before the original
        if closest_distance(rec, ev) <= A: attach(rec, ev); return
        beyond.append(ev)

    # cross-source: has another feed already told us about this?
    R = 5000 km if rec is a named storm else R(rec.hazard_type)               # names decide for cyclones
    cands = live events where hazard_class(ev) == hazard_class(rec)
                     and no record of ev comes from rec.source_id             # one feed's identity is that feed's business
                     and |rec.started_at - ev.started_at| <= T(rec.hazard_type)
                     and closest_distance(rec, ev) <= R
                     and not key_conflict(rec, ev)                            # e.g. EONET naming a *different* GDACS id
            + beyond                                                          # scored by their rule: 'key' or 'glide' = 1.0
    new = create_event(rec)                                                   # the only INSERT into event
    for ev in cands by score, best first:
        if score >= 0.90 and closest_distance(rec, ev) <= A and not names_differ and not reverted_by_a_person(new, ev):
            merge(new -> ev, performed_by='pipeline'); break                  # lineage row, reversible
    propose_merge for every id-named candidate not merged, and for the best scored one >= 0.60   # human decides

rescore(ev):  the same candidates, merge and proposals for a live single-source event as a whole, after each sibling attach
```

Blocking radii `R` (km) and windows `T` (days), from `identity.yaml`: tropical_cyclone 500 / 10, flood 250 / 5, wildfire 100 / 7, severe_storm 200 / 3, drought 500 / 30, heatwave and coldwave 500 / 10, landslide 50 / 3, volcano 50 / 30, earthquake 150 / 2, tsunami 500 / 2. A named storm blocks with `named_storm_km` = 5,000 instead of its 500: on 2026-09-17 the first EONET track point of every named storm lay 600 to 4,500 km from GDACS's current position, and the sibling rule would otherwise have fixed the identity from that first point alone. Distances are the closest pair of positions of the two entities, so a track is compared as a track.

The **aggregation radius** `A` is the one number that decides whether two feeds share a pin without a person: 25 km by default, 200 km for tropical cyclones and 100 km for severe storms (two feeds sample a track hours apart; 182 km was the widest gap among 13 correct name matches), 250 km for drought and temperature extremes (a feed places its centroid anywhere inside a very large area). It gates ids and GLIDE numbers as well as scores, because a cited id is not proof: GDACS reuses wildfire ids, so on 2026-09-17 EONET items citing ids 1031101, 1031263, 1031440 and 1031545 pointed at GDACS fires on other continents, and three Nepal flood positions from three feeds lay 85 to 102 km apart. Beyond `A` the pipeline keeps the pins apart and writes a proposal that names the id and the distance. A record without coordinates cannot contradict a pin and joins on its key; a feed's own records are never gated; a person's accept is never gated.

`score(rec, ev)` is the maximum of: 1.0 if one entity cites the other's id or the GLIDE numbers are equal; 0.95 if both are named storms with the same normalised name (type words and the `-26` season suffix removed) inside `T`; otherwise `0.5*spatial + 0.3*temporal + 0.2*text`, where `spatial = 1 - d/R`, `temporal = 1 - dt/T` (both clipped to [0, 1]) and `text` is token Jaccard of the titles until M3 swaps in the cosine similarity of the local embedding model (`TITLE_SIMILARITY` in config is the hook). Two named storms with different names score at most the weighted value and are never merged automatically, only proposed. A GDACS depression is numbered until named (`TWENTYFOUR-26` became `DUJUAN-26` in a later episode), which is what `rescore` exists for.

**Step 2 — attachment, documents → events.** Documents never create events in v1.

```
attach_document(doc):
    x = extraction(doc)                    # hazard_type, places[], event_date; deterministic stages first
    if x.hazard_type is None: return       # not about a hazard; stays as a plain document row

    window = [doc.published_at - 7d, doc.published_at + 1d]
    cands  = events where hazard_class matches
             and [ev.started_at - 2d, coalesce(ev.ended_at, ev.last_observed_at) + 7d] overlaps window
             and (any place in x.places within 2*R of ev.centroid
                  or x.country == ev.country_iso3
                  or doc was retrieved by a query built for ev)

    for ev in cands:
        spatial  = 1.0 if a place is within R else 0.5 if within 2*R or same country else 0.0
        temporal = 1.0 inside the event window, linear decay to 0 over the following 7 days
        text     = cosine(doc.vector, ev.centroid_vector)      # centroid = mean of title + attached docs
        s = 0.45*spatial + 0.25*temporal + 0.30*text
        if x.places is empty: s = 0.20*temporal + 0.80*text, and require text >= 0.60
        if doc.method == 'query': s += 0.10                     # retrieval was already conditioned on ev

    best = argmax s
    if   best.s >= 0.75: write event_document(status='attached'); update ev.centroid_vector
    elif best.s >= 0.55: write event_document(status='candidate')                 # review tab
    else: write nothing; unattached documents are purged after 60 days
```

**Which error is worse.** For events, a wrong merge removes a disaster from the map (two floods become one pin, in the wrong place); a wrong split shows two pins for one flood. Removal is worse, because the product's promise is "see what is happening", so merging is conservative (automatic only at 0.90 or above, or on GLIDE and storm-name equality), soft, and reversible. For documents, a wrong attachment puts a Pakistan headline on a Bangladesh pin, which is visible and misleading; a missed attachment is one headline fewer among many, which nobody notices. So attachment favours precision: 0.75 to attach, a grey zone for review, nothing below. Both asymmetries push the same way: an algorithm may propose, but it never destroys or pollutes an identity silently.

**Human in the loop.** Yes, in exactly two places: merge proposals scoring 0.60 to 0.90, and candidate attachments scoring 0.55 to 0.75. The review tab shows each with its evidence; a decision is one click and writes `event_lineage` or `event_document`. At v1 volumes this is a handful of items a day. The human is you, and this is one of the load-bearing single-user assumptions: with contributors, review needs roles and rate limits, which is what the FUTURE tables are for.

**Does free local embedding change the design?** Yes, twice. Text similarity costs nothing, so it is a first-class signal in both steps rather than a tie-breaker, and every document is embedded rather than only the ambiguous ones. It also makes syndication detection free: pairs with cosine 0.95 or above are shown as one headline with several sources. At v1 scale (a few hundred active events, tens of thousands of documents) brute-force cosine over 384-dimensional float32 vectors in numpy takes milliseconds; no vector index is needed until several hundred thousand documents.

**The v2 hook, deliberately not built now: orphan clustering.** Documents with a resolved hazard, place and date that match no event are grouped by (hazard class, roughly 100 km cell, 48 hours) with cosine 0.80 or above; a group with at least three distinct publishers becomes an `event` with `status = 'candidate'` through the same `resolve_record()` path and is drawn hollow until you confirm it. This is where the README's original ambition lives, and it needs nothing in the schema that is not already there.

## 4. Milestones

**The riskiest assumption in the plan** is that free authoritative feeds alone put enough real events on the map, at the freshness target, for it to feel alive for *weather* hazards, with news reduced to a garnish. If that is wrong, the design must lean on news-driven discovery much earlier, which changes both the clustering problem and the cost profile. M0 is the cheapest possible test: no scheduler, no news, no model, and it ends with a number and a decision rule. The second risk, attachment precision from headlines alone, is tested in M3, which is also the milestone most likely to overrun.

Sizes are relative: M0 small, M1 medium, M2 medium, M3 large, M4 medium, M5 medium, M6 small. Every milestone ends with something you can look at, query or click.

### M0 — Real events on a local map (small) — DONE 2026-09-16

**Result.** Built and run the same day; `docs/m0-density.md` holds the counts. Exit criteria: (1) two consecutive collects fetched 2,192 GDACS and 1,051 EONET items into 3,633 `source_record` rows and 3,243 events; the second collect added 0 rows and left the event count at 3,243. (2) The duplicate query returns no rows; 0 unresolved records after `eww resolve`, and a second `eww resolve` changes nothing. (3) `eww export --since 30d` wrote 3,228 features, equal to the SQL count for the same window; a 12-feature sample carrying the `meta` member loaded in geojson.io without error. (4) The viewer drew 1,396 pins for its default 14-day window; clicking one showed title, hazard, start date, severity label, country and the source link in the sidebar. (5) Density bar, all four criteria met: 3,228 events observed in 30 days; 1,643 after excluding the 1,585 GDACS wildfires below Orange; 703 after also removing the 940 EONET items that mirror a GDACS event (M0 has no cross-source merging). Hazard types with 3 or more events: 5 (wildfire 1,000, earthquake 484, flood 111, tropical cyclone 33, drought 13). Continents with 3 or more: 6. Non-wildfire events in Europe: 50 (38 without the mirrors). The bar also passes on the de-duplicated set, so the anchored-feeds bet holds and Meteoalarm stays out of M2.

**What M0 taught the collectors.** GDACS: `alertlevel` is mandatory (HTTP 204 without it), a page past the end is a 204, and a combined `eventlist` collapsed to 4 rows when one listed type (TS) had no rows in the window, so the collector queries one type per request (about 30 requests for a 30-day window); the swagger file confirmed the parameter names. EONET: `start`/`end` returned the same 1,051 events as `days=30`; Polygon rings arrive as [lat, lon] and are swapped on ingest (39 of 40 GDACS-sourced flood polygons landed on GDACS's own point only after the swap); EONET carries no country, so `country_iso3` is read from GDACS-style titles ("Flood in Croatia 1104153") or implied by US-only fire sources (IRWIN, InciWeb), leaving 121 counted events without a continent. 940 of the 1,051 EONET items cite a GDACS report URL carrying the GDACS eventid: a deterministic cross-source key for M2, like Copernicus's `gdacsId`. `events_geojson(limit=0)` returns everything in the window; the exporter and the viewer use it, and the contract's default of 2,000 is unchanged.

**Goal.** Prove that the spine is thick enough and that a clickable map needs no JavaScript.

**In scope.** uv project skeleton; `sql/schema.sql` applied by `eww init-db`; GDACS and EONET collectors run locally for the last 30 days; `ingest` into `source_record`; `resolve` in its trivial form (one external id → one event, no cross-source merging); `eww export` to GeoJSON; a Streamlit page with a folium map, coloured circle markers by hazard type, click → sidebar with title, hazard, dates, severity label and source link; a bundled country → continent CSV; `eww doctor` printing the invariant queries; the density report.

**Out.** Actions, news, geocoding of prose, merging, models, filters beyond hazard type.

**Exit criteria.**
1. `uv run eww collect --source gdacs --source eonet --days 30` run twice in a row: the second run reports 0 new `source_record` rows and `SELECT COUNT(*) FROM event` is unchanged.
2. `SELECT source_id, external_id, external_episode, COUNT(*) FROM source_record GROUP BY 1,2,3 HAVING COUNT(*) > 1` returns no rows, and `SELECT COUNT(*) FROM source_record WHERE event_id IS NULL` returns 0 after `eww resolve`.
3. `uv run eww export --since 30d > events.geojson` loads in geojson.io without error and shows the same number of pins as the SQL count of events observed in the last 30 days.
4. `uv run streamlit run app.py` shows those pins; clicking one shows its title, hazard type, start date, severity label and a working link to the source's page in the sidebar.
5. `docs/m0-density.md` records, for the last 30 days: events by hazard type, by continent, and the number of non-wildfire events in Europe. Passing bar: at least 40 events after excluding GDACS wildfires below Orange; at least 4 hazard types with 3 or more events each; at least 4 continents with 3 or more events each; at least 3 non-wildfire events in Europe. If the world passes and Europe fails, Meteoalarm (CC BY 4.0) joins M2 as a warnings layer. If the world fails, the orphan-clustering hook in §3 moves into M3 and GDELT becomes a discovery source.

**Risk retired.** The riskiest assumption, and "an interactive map with zero JavaScript".

### M1 — Collection survives the laptop being off (medium)

**Status (2026-09-16): built, first run observed, exit criteria awaiting the calendar.** The orphan `data` branch exists (README.md only at `a0c330d`); `.github/workflows/collect.yml` runs `eww collect --all-spine --out data-branch` on `7 */3 * * *` and on dispatch, with the default GITHUB_TOKEN and `contents: write`. The first dispatched run took 31 s: gdacs=2,192 and eonet=1,051 items, committed as `28e7df3` by `eww-collector[bot]` on the first push attempt. `eww sync` on the laptop then fetched the branch and replayed it: 2 snapshot files, 11 new and 39 changed `source_record` rows, 2 run-log lines, 2 events created; a second `eww ingest --from branch` reported 0 new snapshot files (exit criterion 3 met). The `heartbeat` view marks the 15:00Z slot as served by both sources and the 12:00Z slot as missed, because M0's manual runs started 92 minutes into it: the 45-minute rule is deliberately strict. Exit criteria 1, 2, 4 and 5 need three days of scheduled runs, which start at 2026-09-16T18:07Z; `docs/m1-volume.md` is provisional at 0.91 MB of pack for the first two snapshot files. If git's delta compression does not pair consecutive snapshots, eight runs a day approach 7 MB/day and criterion 5 fails, in which case the pre-declared fallback (Cloudflare R2 as the sink) applies.

**Observed 2026-09-17.** The scheduled runs happened (18:52Z, 23:38Z, 04:45Z) but each started 1 h 35 min to 1 h 45 min after its slot, far outside the 45-minute grace, so the view counts them as missed: the strip read "7 of 8 runs missed" with the pipeline working. Exit criterion 4 is met to the letter (the strip and a hand count agree) and misses its point (telling a stopped pipeline from a slow scheduler). Whether to widen `HEARTBEAT_GRACE_MINUTES` or to accept a red strip while GitHub is late is open question 8 in §7.

**What M1 fixed in the design.** In Actions the data branch is checked out sparse (cone `runs/`) and new files are added with `git add --sparse`, so the checkout stays small while snapshots accumulate; a run commits nothing when only `runs/` changed, so a run in which every fetch failed leaves no commit and shows up only as a missed slot. `last_collector_run_at` in the GeoJSON `meta` is the last run that produced data (status ok or partial), and `expected_runs_7d` joined the contract so the strip can say "n of m runs missed". Runs replayed from `runs/*.jsonl` never overwrite a row the collector recorded itself (INSERT OR IGNORE); the same snapshot minute stamp produced by a laptop run and an Actions run would collide on `snapshot_path` and the second would be skipped, which loses nothing because both hold the same feed window.

**Goal.** The spine accumulates history on a schedule that does not depend on you, and the map says when the pipeline was not running.

**In scope.** Orphan `data` branch; `.github/workflows/collect.yml` on cron `7 */3 * * *` plus manual dispatch, `permissions: contents: write`, checkout of `main` and of `data` into a sub-folder, `uv run eww collect --all-spine --out data-branch/`, commit and push with a retry on non-fast-forward; snapshot files `snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json` and `runs/<YYYY-MM-DD>.jsonl`; `eww ingest` replays unseen files (tracked in `snapshot_ingest`) and loads the run log into `collector_run`; the `heartbeat` view; a status strip in the viewer ("last run 2 h ago · 1 missed run in 7 days"); `meta` in the GeoJSON; optionally a Windows Task Scheduler entry running `eww sync` at logon and every 2 hours.

**Out.** Enrichment, merging, filters.

**Exit criteria.**
1. The Actions run history shows scheduled runs on at least 3 consecutive days, and `runs/*.jsonl` has at least 20 lines with `"status": "ok"`.
2. During that window the laptop was off, or `eww sync` was not run, for at least 24 hours; afterwards `SELECT COUNT(*) FROM source_record WHERE first_seen_at BETWEEN <off-start> AND <off-end>` is greater than 0.
3. Running `eww ingest` twice: the second run reports 0 new snapshot files.
4. The status strip's "missed runs in 7 days" equals the number you count by hand on the Actions page for the same 7 days, where a run more than 45 minutes late counts as missed.
5. On a fresh clone of the `data` branch after 3 days, `git count-objects -vH` reports `size-pack` under 15 MB; `docs/m1-volume.md` records it with the extrapolated yearly figure.

**Risk retired.** Laptop-off gaps; Actions cron reliability; repository growth.

### M2 — One event, one pin (medium) — BUILT 2026-09-17, exit criteria 3, 4, 5 met; 1 and 2 met on a starter set awaiting your labels

**Result.** The database was rebuilt from the snapshots under the new rules (8 seconds): 3,855 records became 2,392 events, 2,379 of them live, 953 with two or more sources; 941 records joined an event by a deterministic key (940 EONET mirrors, 1 Copernicus activation), 13 events merged automatically, all named storms matched by name (Lala, Saudel, Narra, Iselle, Julio, Karina, Lowell, Bang-Lang, Etau, Krovanh, Edouard, Marie, Norbert), and 11 proposals wait in the Review tab (a Copernicus wildfire 2 km from a GDACS fire in Croatia, nine IRWIN fires 1 to 34 km from GDACS "Forest fires in United States" events, and "Tropical Storm Moke" against the numbered GDACS depression `TWO-C-26`). Copernicus: 9 activations in the 30-day window, all resolved, 8 of them events of their own. Exit criterion 3: accepting the Croatia proposal took the viewer from 747 to 746 pins, reverting it back to 747, `event_lineage` gained a `merge` and a `revert` row and `merged_into_event_id` was NULL again. Criterion 4: the viewer showed 2 pins for flood, 7 days, severity ≥ 0.66 and `eww export --count` printed 2 (Flood in China, Orange 0.675; Flood in Nepal, Red 1.0). Criterion 5: the Nepal pin's sidebar shows the "EMS activation" badge with sources copernicus, eonet and gdacs; the unresolved-Copernicus count is 0. Criteria 1 and 2: `data/labels/merge_pairs.csv` holds 24 true pairs and 24 non-pairs labelled from facts in the feeds (the same storm name in both feeds, an explicit GDACS id, or a conflicting one), marked `starter set (Claude, 2026-09-17)` in `labelled_by`, plus the 11 proposals unlabelled; `eww eval merges` reports 100% precision and 100% recall on that set, which is not yet your judgement: the recall is high because the set was drawn from pairs the feeds themselves resolve. Your labels, especially on the proposals, are what the criteria mean.

**The aggregation radius (2026-09-17, later the same day).** Looking at Flood in Nepal showed the three feeds 85 to 102 km apart on one pin, joined by Copernicus's `gdacsId` and EONET's GDACS URL, with GDACS's point chosen only because it ranks first. Decision: two feeds share a pin automatically only inside an aggregation radius, applied to ids and GLIDE numbers as well as scores; the radius and the other identity numbers moved to `identity.yaml` (25 km default; 200 km cyclones, 100 km severe storms, 250 km drought and temperature extremes). Rebuilt under the gate: 3,956 records (the branch had gained snapshots) → 2,445 events, 2,431 live, 927 multi-source; 913 records joined by key inside the radius, 14 automatic merges (13 storms by name at 74 to 195 km, one Copernicus activation into an EONET flood 17 km away by score), 39 open proposals, 27 of them pairs an id or a score called one event but the positions kept apart. Those 27 exposed a fact the id alone had hidden: **GDACS reuses wildfire event ids**. EONET's "Wildfire in Indonesia 1031101" (20 August) cites an id that by our first snapshot named "Forest fires in United States" (from 24 August); likewise Australia→Algeria (1031263), Brazil→Russia (1031545), Namibia→Congo (1031440). Under the id-only rule those items had been attached to the wrong fires; now they are proposals 1,900 to 14,500 km apart that a reviewer rejects at a glance. Of 927 multi-source events in the window, 853 have their sources within 5 km and 68 within 25 km; the 6 wider ones are named storms (39 to 182 km). Hand-labelled evaluation after the gate: 0 false merges, recall 83% (the four beyond-radius mirrors, Nepal twice among them, wait as proposals), every true pair merged or proposed. `docs/m2.md`, written by `eww report identity`, carries these measurements and is the file to re-run after tuning.

**What M2 taught.** GDACS: `alertscore` in the SEARCH API is 1 / 2 / 3, so `episodealertscore` breaks ties; every bbox was a degenerate point, so GDACS supplies no footprints and the "GDACS bbox as a polygon" item of the scope draws nothing until the API returns real boxes; two cyclones shared GLIDE `TC-2026-000161-CHN`, so GLIDE-first must not merge two ids of the same feed. EONET: 6,618 of the 7,617 blocked wildfire pairs are EONET mirrors of a *different* GDACS fire 0 to 2 km away (GDACS splits fires into several events), the reason the key-conflict guard exists and the best source of hand-labelled non-pairs. Storm tracks: the first EONET point of a named storm lay 600 to 4,500 km from GDACS's current position, hence the basin-wide block for named storms and the re-score on every sibling attach. Copernicus: the public list pages with `limit`/`offset`, 265 activations back to 2023, `gdacsId` on 76 of them, categories Wildfire, Flood, Storm, Earthquake, Mass movement, Volcanic activity plus non-hazards (public events, accidents) that yield no record. Identity rules apply when a record is first resolved; changed rules or thresholds reach old data by rebuilding the database from the snapshots, which the data branch was designed for.

**Goal.** Cross-source identity with reversible merges, normalised severity, and the filters the README asks for.

**In scope.** Copernicus EMS activations collector (spine, runs in Actions) joined on `gdacsId`, and EONET records joined on the GDACS eventid in their `sources[].url` (940 of 1,051 EONET items carried one on 2026-09-16), both before any scoring; the full `resolve_record()` from §3 with blocking, scoring, auto-merge at 0.90 and `merge_proposal` rows for 0.60–0.90; `event_lineage` with revert; a Review tab (accept, reject, revert); severity normalisation; footprints (EONET polygons, GDACS bbox) drawn with `folium.GeoJson`; filters: hazard types (multiselect), time window (1–90 days), minimum severity; MarkerCluster; LocateControl; `include_footprints` in the API; the hand-labelled pairs file. M0 found Europe thick enough (50 non-wildfire events in 30 days), so no Meteoalarm layer.

**Out.** News, geocoding of prose, models.

**Exit criteria.**
1. `data/labels/merge_pairs.csv` holds at least 20 hand-labelled true cross-source pairs (GDACS↔EONET, GDACS↔Copernicus) and at least 20 true non-pairs, all from real data.
2. Against that file, auto-merge produces zero false merges and recall of at least 60%; every remaining true pair appears in `merge_proposal` with `status = 'open'`.
3. In the Review tab: accept a proposal → one pin on the map; revert → two pins return; `event_lineage` gained 2 rows and `merged_into_event_id` is NULL again.
4. Filter parity: for "flood, last 7 days, severity ≥ 0.66", the pin count shown in the UI equals the count printed by `eww export --since 7d --hazard flood --min-severity 0.66 --count`.
5. Events with a Copernicus activation show an "EMS activation" badge, and `SELECT COUNT(*) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NULL` is 0.

**Risk retired.** The identity design works with minutes of human effort a week.

### M3 — Headlines on pins (large; most likely to overrun)

**Goal.** Attach free news to known events deterministically and show it in the sidebar.

**In scope.** Laptop enrichment collectors: GDELT DOC per active event (query built from country names, admin1 names, storm name and hazard keywords in English and Italian; `startdatetime` from the last successful run; at least 5 s between calls) and ReliefWeb reports (after appname approval; skipped with a logged warning if not approved); `document` rows with canonical URLs; syndication grouping at cosine ≥ 0.95; deterministic extraction (hazard lexicon EN/IT; spaCy `xx_ent_wiki_sm` and `en_core_web_sm`; gazetteer tier 1; GeoNames tier 2; Nominatim tier 3 behind a 4-per-minute token bucket); embeddings; `attach_document()` with the §3 thresholds; `mention` geometries; a sidebar "News" tab with headline, publisher, time and thumbnail by URL with link fallback; candidate attachments in the Review tab; a 60-day purge of unattached documents.

**Out.** Models, social sources, article bodies.

**Exit criteria.**
1. Coverage: at least 50% of events with severity ≥ 0.66 active in the last 14 days have 3 or more attached documents (an `eww doctor` query).
2. Precision: 100 random `attached` rows hand-checked, at least 90 correct; `docs/m3-eval.md` lists the wrong ones and why.
3. Rate limits: the log shows Nominatim never exceeded 4 requests in any minute, GeoNames never exceeded 1,000 in any hour, GDELT spacing never under 5 s, and every User-Agent string carries your contact address.
4. `geocode_cache` hit rate of at least 70% in the second week (`eww doctor` prints hits and misses since a date).
5. No bytes: `SELECT MAX(length(text_excerpt)) FROM document` is at most 2000, and `data/` contains only the SQLite file and model caches.
6. Determinism: `eww attach --rebuild` on a copy of the database reproduces the same `event_document` rows (the table diff is empty).

**Why it overruns.** Thresholds need tuning against real data, NER on headlines is weak, GDELT returns metaphorical "floods" of everything, and place disambiguation has a long tail. Budget for two passes over the labelled sample.

**Risk retired.** News enrichment is feasible without a model; geocoding quality is adequate.

### M4 — Summaries and numbers, under a hard budget (medium)

**Goal.** Fill the README's "written summary" fields with a model whose spend cannot exceed the cap.

**In scope.** An `Extractor` interface with `OllamaExtractor` (`qwen2.5:7b-instruct`, `format` set to the JSON schema) and `ClaudeBatchExtractor` (Sonnet 5, Batch API, cached prefix); settings `EWW_LLM_BACKEND` and `EWW_LLM_BUDGET_USD` (default 10); the `llm_call` ledger; the model used only for documents the deterministic path left ambiguous; per-event summaries (at most three sentences plus figures with evidence spans), refreshed at most daily while active and only when at least three new documents attached; a golden set (30 documents, 10 events) scored by `eww eval`; the summary shown in the sidebar with its "as of" time.

**Out.** Social sources, hosting.

**Exit criteria.**
1. `eww eval` on the golden set: hazard type accuracy at least 90%; place resolution at least 80%; casualty figures exact-match at least 80%, and every figure's evidence span is a verbatim substring of a source text (the script checks this).
2. Every summary sentence containing a number has an evidence span; `eww doctor` reports zero violations.
3. `SELECT SUM(cost_usd) FROM llm_call WHERE called_at >= date('now', 'start of month')` is at most 10, and with `EWW_LLM_BUDGET_USD=0` a full `eww sync` completes with zero rows where `backend = 'anthropic'`.
4. After a nightly `eww sync`, no document that passed blocking more than 24 hours ago is still without a `document_extraction` row.

**Risk retired.** Model cost containment; adequacy of the local model.

### M5 — Posts, videos and weather on click (medium)

**Goal.** The sidebar carries the README's social feed and forecast at zero recurring cost.

**In scope.** Bluesky collector (session from the app password in `.env`; `searchPosts` per active event with `since`/`until`; store URI, web URL, handle, text up to 300 characters, `createdAt`, image URLs); YouTube collector (at most 100 searches per UTC day, rotated across active events by severity; store id, title, channel, `publishedAt`, thumbnail URL); Mastodon tag timelines; a Reddit collector behind a feature flag that stays off until approval; compliance jobs (Bluesky and Reddit existence checks setting `removed_at`; YouTube metadata refreshed or dropped at 30 days); sidebar tabs News, Posts, Videos, Weather; Open-Meteo current conditions plus a 5-day forecast at the pin, cached for 30 minutes in Streamlit and never stored.

**Out.** X, media bytes, anything beyond a thumbnail and a link.

**Exit criteria.**
1. At least 30% of events active in the last 7 days have at least one attached post or video.
2. 50 random attached posts hand-checked, at least 40 relevant (posts are noisier than news, so 80% is the bar).
3. Quotas: the log shows at most 100 YouTube searches per UTC day and Bluesky at or under 1 request per second; `data/` still holds no image or video files.
4. Compliance: delete a test post of your own on Bluesky; after the next `eww sync` its `removed_at` is set and it no longer appears in the sidebar.
5. Clicking any pin shows the current temperature and a 5-day forecast for the pin's coordinates within 3 seconds.

**Risk retired.** Social value under free tiers; the forecast requirement.

### M6 — Open it from your phone (small, optional)

**Goal.** A private hosted copy, without touching the pipeline.

**In scope.** `eww publish`: `VACUUM INTO` a copy of the database, upload to a Cloudflare R2 bucket with boto3; a private Streamlit Community Cloud app deployed from this repo that downloads the file at start and every 30 minutes, with R2 credentials in Streamlit's secrets manager and no model libraries imported (the hosted viewer is read-only); `eww serve`: a FastAPI `GET /events.geojson` run locally as the proof of the boundary.

**Out.** Public access, authentication beyond Streamlit's private-app login, a custom domain.

**Exit criteria.**
1. A phone on mobile data opens the app URL, logs in, and shows the same pin count as the laptop within 30 minutes of `eww publish`.
2. An incognito browser without login cannot see the app.
3. `curl "http://localhost:8000/events.geojson?since=7d&hazard=flood"` returns a FeatureCollection identical to `eww export --since 7d --hazard flood`.
4. R2 usage stays under 1 GB and the hosted app starts in under 60 seconds.

**Risk retired.** A hosting path exists that does not require a rewrite.

## 5. Not in v1

Everything below is deferred on purpose. The plan is credible because this list is long.

| Deferred | One-line reason |
|---|---|
| News-driven event discovery (events created from clusters of articles with no feed anchor) | It is the expensive, unanchored version of the clustering problem; v1 proves the anchored version first. Pulled forward only if M0's density check fails. |
| User accounts, submissions, "join an event", edit history UI | The schema absorbs it (§3); the real cost is moderation and abuse handling, which a single private user does not need. |
| X / Twitter | Free tier cannot read posts; the paid tier alone exceeds the budget; scraping breaks the terms. Dropped, not deferred. |
| Instagram, TikTok, Facebook, Telegram | No public search API reachable by an individual without a business account or application process; scraping breaks the terms. Dropped. |
| Storing media bytes, galleries, embeds, thumbnail caching | References plus provenance cost nothing and keep the publish decision a policy change; rendering by URL in the sidebar is the free v1 answer. |
| Browser automation (Browserbase or similar) and any "LLM swarm" | No discovery value over free typed feeds; the cost scales with pages visited; it is where the terms-of-service exposure lives. |
| Fetching article bodies | Headlines, excerpts from APIs that permit them, and authority narratives are enough for v1 popups; body fetching is where publisher terms and paywalls bite. |
| Multiple pins per event on the map | Stored from M3 as `event_geometry.role='mention'`; drawing them is a UX question (which pin do I click?) for a real frontend. |
| Warning layers (NWS alerts, Meteoalarm, CAP feeds) | Warnings are forecasts of possible events, not observations; mixing them with occurrences muddles the map and multiplies volume. A separate toggleable layer in v2. |
| NASA FIRMS active-fire hotspots | Pixel detections, not events; turning them into fire events is its own clustering project. EONET and GDACS already publish wildfire events. |
| EM-DAT, GLIDE backfill | Historical; useful later as damage enrichment, not for discovery. (Copernicus EMS activations were listed here until M2 promoted them to the spine: low volume, but each is a confirmed disaster and most had no GDACS twin.) |
| Weather layers (radar, satellite, wind animation as on zoom.earth or nullschool) | A different product built on gridded model data; v1 is a pin map. Open-Meteo at the clicked pin covers the README's forecast requirement. |
| Historical backfill beyond 90 days | Cheap to add later from GDACS and EONET archives; not needed for "what is happening now". |
| Streaming ingestion (Bluesky Jetstream, GDELT 15-minute files) | Needs an always-on process, which contradicts the laptop constraint; polling every 3 h meets the freshness target. |
| Public deployment, authentication, donate button, SEO, mobile layout | Hosting is M6 and optional; everything that assumes a public audience waits for one. |
| "Front-end design" and "website usability" as disciplines | The v1 viewer is disposable; design effort goes into the GeoJSON contract, which is what a real frontend will consume. |
| Lexicons and NER beyond English and Italian | Each language is a small, separable addition once the pipeline works. |
| Trained classifiers for hazard type or relevance | The lexicon plus event anchoring is enough at v1 precision targets; training data appears as a by-product of the review queue. |
| A cloud database as the foundation | SQLite is rebuildable from the data branch and has no terms that can change; Postgres arrives with the first second writer. |
| Climate-change datasets (Copernicus CDS, reanalysis) | Out of scope for an event map; a different question and a different product. |

## 6. Implementation prompts

One prompt per milestone. Paste one into a fresh coding session opened in this repository, in order; each assumes the previous milestone's definition of done is met. Each prompt repeats the project brief so the session needs nothing else, and points at `docs/architecture.md` for the schema and contract, which are in the repo. Do not let a session skip the definition of done: it is the exit criterion from §4, and it is what you check yourself.

**Shared project brief** (repeated inside every prompt, kept here for reference):

> Extreme Weather Watch (EWW) is a private, single-user tool that shows recent extreme weather and natural-hazard events on an interactive world map running on my Windows 11 laptop. Stack: Python 3.12 managed with uv, SQLite in WAL mode with STRICT tables, Streamlit + folium (Leaflet) for the viewer. I am strong in Python and SQL and have never written JavaScript or CSS: do not introduce a frontend framework, JavaScript, CSS, or Node tooling. Budget is €25/month, so use only free tiers and local components. No terms-of-service violations: official APIs and feeds only, no scraping, no browser automation, honour every rate limit. Store references to media, never bytes; never store full article bodies (excerpts are at most 2,000 characters). All timestamps are UTC ISO 8601 strings; all surrogate keys are ULIDs; every command is idempotent and safe to re-run. HTTP goes through `httpx` with a User-Agent of the form `extreme-weather-watch/0.x (+contact from config)`. Rate limits, radii and thresholds live in `eww/config.py`. Logging is structured, to stdout. Tests use pytest against a temporary SQLite file. The schema is `sql/schema.sql` and the GeoJSON contract is `eww/api.py`; both are specified in `docs/architecture.md` §3 and §2.

### Prompt M0 — Real events on a local map

```
CONTEXT
Extreme Weather Watch (EWW) is a private, single-user tool that shows recent extreme weather and
natural-hazard events on an interactive world map on my Windows 11 laptop. Stack: Python 3.12 with uv,
SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only: no JavaScript, no CSS, no frontend
framework. Free tiers and local components only. Official APIs only; no scraping. References to media,
never bytes; excerpts <= 2,000 chars. UTC ISO 8601 timestamps, ULID keys, idempotent commands, httpx with
a User-Agent "extreme-weather-watch/0.1 (+<contact from config>)", settings in eww/config.py, pytest.
The repo currently holds README.md and docs/architecture.md. Read docs/architecture.md §2 (GeoJSON
contract) and §3 (DDL) before writing code; copy the DDL verbatim into sql/schema.sql.

TASK
1. Create the uv project: package `eww` with a `typer` CLI exposed as `eww` (run as `uv run eww ...`).
   Dependencies: httpx, typer, python-ulid, python-dotenv, streamlit, folium, streamlit-folium, pytest.
   Layout: eww/{cli,config,db,ids}.py, eww/collectors/{gdacs,eonet}.py, eww/{ingest,resolve,api,
   heartbeat}.py, app.py, sql/schema.sql, tests/, eww/data/countries.csv. `data/` is gitignored.
2. eww/db.py: connect() sets journal_mode=WAL and foreign_keys=ON; `eww init-db` applies sql/schema.sql
   when schema_version is empty and seeds the `source` table (gdacs, eonet with terms URLs and attribution).
3. Collectors. Each module exposes fetch(since, until) -> list[dict] (raw items) and
   normalise(item) -> dict with the source_record columns. Write raw items to
   data/snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json in exactly the format a later scheduled job will use.
   - GDACS: JSON API https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH with parameters
     eventlist=EQ;TC;FL;VO;WF;DR;TS, fromDate, toDate, alertlevel=Green;Orange;Red, pageSize=100,
     pageNumber (page until empty). Confirm parameter casing against
     https://www.gdacs.org/gdacsapi/swagger/v1/swagger.json. Response is a GeoJSON FeatureCollection;
     properties include eventtype, eventid, episodeid, eventname, glide, alertlevel, alertscore, fromdate,
     todate, datemodified, country, iso3, severitydata{severity,severitytext,severityunit},
     url{report,details,geometry}, iscurrent. external_id=eventid, external_episode=episodeid.
     Fallback: RSS https://www.gdacs.org/xml/rss.xml (gdacs:* fields). Attribution: "Global Disaster
     Alert and Coordination System (GDACS), European Union, CC BY 4.0".
   - EONET: https://eonet.gsfc.nasa.gov/api/v3/events?status=all&days=30&category=wildfires,
     severeStorms,floods,volcanoes,drought,landslides,tempExtremes,snow,dustHaze,earthquakes . Each
     event has id, title, description, link, closed, categories[], sources[]{id,url}, geometry[]
     {date,type,coordinates,magnitudeValue,magnitudeUnit}. One source_record per geometry entry:
     external_id=id, external_episode=geometry.date.
   - Hazard mapping (a dict in config): GDACS EQ→earthquake, TC→tropical_cyclone, FL→flood, VO→volcano,
     WF→wildfire, DR→drought, TS→tsunami. EONET wildfires→wildfire; severeStorms→tropical_cyclone if the
     title matches /hurricane|typhoon|cyclone|tropical/i else severe_storm; floods→flood;
     volcanoes→volcano; drought→drought; landslides→landslide; tempExtremes→heatwave unless the title
     says cold/freeze→coldwave; snow→coldwave; dustHaze→other; earthquakes→earthquake.
4. `eww collect --source gdacs --source eonet --days 30`: fetch, write snapshots, then ingest: upsert
   source_record on (source_id, external_id, external_episode); update payload only when payload_hash
   changed; set first_seen_at/last_seen_at; write one collector_run row per source per run.
5. `eww resolve`: for each source_record with event_id NULL, reuse the event of a sibling record with the
   same (source_id, external_id), otherwise create an event: title, hazard_type, started_at, ended_at,
   last_observed_at, centroid, country_iso3, glide_number, severity_label as the source says it,
   severity_score GDACS Green/Orange/Red → 0.33/0.66/1.0, EONET → 0.4. Insert the primary
   event_geometry (Point, is_primary=1) and a footprint row when EONET gives a Polygon. This function is
   the only place that inserts into event.
6. eww/api.py: events_geojson(...) exactly per the contract in docs/architecture.md §2, following
   merged_into_event_id pointers. `eww export --since 30d` prints the FeatureCollection.
7. `eww doctor`: prints duplicates in source_record, unresolved records, event count, last run.
   `eww report density --days 30` writes docs/m0-density.md: events by hazard type, by continent (via
   eww/data/countries.csv: iso3,continent from GeoNames countryInfo.txt, CC BY 4.0), and the count of
   non-wildfire events in Europe, plus the pass/fail against the bar in DEFINITION OF DONE.
8. app.py: Streamlit page; sidebar date range (default 14 days) and hazard multiselect; folium Map with
   OpenStreetMap tiles; one CircleMarker per feature, colour by hazard from a fixed palette, tooltip =
   event_id; st_folium(m, returned_objects=["last_object_clicked_tooltip"]); on click, show title,
   hazard, dates, severity_label, country and a link to detail_url in the sidebar. app.py imports only
   eww.api; no SQL in the viewer; no custom HTML beyond what folium generates.

CONSTRAINTS
Idempotent everything. No JavaScript or CSS. Tests: normalise() on saved fixture JSON for both sources,
schema applies on an empty file, export output has the contract's property keys.

DEFINITION OF DONE
1. `uv run eww collect --source gdacs --source eonet --days 30` twice: second run reports 0 new
   source_record rows and SELECT COUNT(*) FROM event is unchanged.
2. The GROUP BY duplicate query over (source_id, external_id, external_episode) returns no rows;
   SELECT COUNT(*) FROM source_record WHERE event_id IS NULL is 0 after `eww resolve`.
3. `uv run eww export --since 30d > events.geojson` loads in geojson.io and shows as many pins as the
   SQL count of events observed in the last 30 days.
4. `uv run streamlit run app.py` shows the pins; clicking one shows title, hazard, start date, severity
   label and a working source link in the sidebar.
5. docs/m0-density.md exists with the counts and the pass/fail verdict: >= 40 events excluding GDACS
   wildfires below Orange; >= 4 hazard types with >= 3 events; >= 4 continents with >= 3 events;
   >= 3 non-wildfire events in Europe.
```

### Prompt M1 — Collection survives the laptop being off

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map on my Windows 11 laptop. Python 3.12
with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only. Free tiers only; official
APIs only; no media bytes; idempotent commands; UTC ISO 8601; ULIDs; httpx with an identifying
User-Agent; settings in eww/config.py; pytest. See docs/architecture.md §2–§4.
STATE: M0 is done. `eww collect` fetches GDACS and EONET into data/snapshots/<source>/<ts>.json and
ingests them; `eww resolve` creates events; `eww export` emits GeoJSON; app.py shows the map.
PROBLEM: the laptop is often off, so history only accumulates if collection runs elsewhere. The fix is a
GitHub Actions cron that runs the same collector code and commits raw snapshots to an orphan `data`
branch of this repo; the laptop replays whatever it has not seen. Nothing here needs a secret.

TASK
1. Create an orphan branch `data` containing only README.md (explaining the layout below) and push it.
   Layout: snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json and runs/<YYYY-MM-DD>.jsonl. One JSON line per
   (run, source): {run_id, source_id, scheduled_for, started_at, finished_at, status: ok|partial|failed,
   http_status, items_seen, snapshot_path, error}. scheduled_for is the 3-hour slot (start of hour
   rounded down to a multiple of 3, UTC) the run served.
2. Extend `eww collect` with --all-spine (gdacs, eonet, and later copernicus) and --out <dir> so it
   writes snapshots and appends to runs/<date>.jsonl under <dir>, writing the run log even when a fetch
   fails (status failed, error text, no snapshot_path). Exit code 0 unless every source failed.
3. .github/workflows/collect.yml: on schedule cron "7 */3 * * *" and workflow_dispatch;
   permissions: contents: write; runs-on ubuntu-latest; steps: checkout main; checkout the `data`
   branch into ./data-branch (actions/checkout with ref: data, path: data-branch); install uv and the
   project; `uv run eww collect --all-spine --out data-branch`; in data-branch: git config a bot
   identity, add, commit with message "collect <UTC ts>: gdacs=<n> eonet=<n>", push; on non-fast-forward
   do `git pull --rebase` and retry up to 3 times. Commit nothing if no files changed except runs/.
   Use the default GITHUB_TOKEN only. Note the docs: the schedule can be delayed under load; pushes by
   GITHUB_TOKEN do not trigger workflows; schedules run from the default branch only.
4. `eww ingest`: two sources of snapshots. (a) a local directory (M0 behaviour). (b) the `data` branch:
   `git fetch origin data:data`, list files with `git ls-tree -r --name-only data snapshots runs`, read
   each with `git show data:<path>`; no checkout, no worktree. Track every processed file in
   snapshot_ingest (path is the key); skip files already there. Load runs/*.jsonl into collector_run
   with INSERT OR IGNORE on (run_id, source_id).
5. `eww sync` = git fetch + ingest + resolve, and later the enrichment steps. Print a one-line summary.
6. Heartbeat: a SQL view `heartbeat` (add to sql/schema.sql) listing every expected 3-hour slot from the
   first collector_run to now and whether an ok run exists for it within 45 minutes; a Python helper
   heartbeat.summary() returning last_collector_run_at, missed_runs_7d, expected_runs_7d. Put those in
   the GeoJSON `meta` (see the contract) and add `eww doctor` output for them.
7. app.py: a status strip above the map built only from `meta`: "Data as of <t> · last collector run
   <h> ago · <n> of <m> runs missed in 7 days", red when missed_runs_7d > 2 or last run > 6 h ago.
8. Optional: print the Windows Task Scheduler commands (schtasks) that run `uv run eww sync` at logon
   and every 2 hours in this repo directory; do not register them yourself.

CONSTRAINTS
The collector code must be identical in Actions and locally; only --out differs. No secrets. Keep each
snapshot as raw items exactly as fetched (JSON), so everything downstream can be rebuilt from the branch.

DEFINITION OF DONE
1. Actions history shows scheduled runs on >= 3 consecutive days; runs/*.jsonl has >= 20 lines with
   "status": "ok".
2. With the laptop off or `eww sync` not run for >= 24 h inside that window, afterwards
   SELECT COUNT(*) FROM source_record WHERE first_seen_at BETWEEN <off-start> AND <off-end> is > 0.
3. `eww ingest` twice in a row: the second run reports 0 new snapshot files.
4. The status strip's missed-runs count equals a hand count from the Actions page for the same 7 days
   (a run > 45 minutes late counts as missed).
5. On a fresh clone of the data branch after 3 days, `git count-objects -vH` shows size-pack < 15 MB;
   docs/m1-volume.md records it with the extrapolated yearly figure.
```

### Prompt M2 — One event, one pin

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map on my Windows 11 laptop. Python 3.12
with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only; no JavaScript or CSS. Free
tiers only; official APIs only; no media bytes; idempotent; UTC ISO 8601; ULIDs; identifying User-Agent;
settings in eww/config.py; pytest. Read docs/architecture.md §3, especially "How identity is decided".
STATE: M0 and M1 are done. M0's density check passed for the world and for Europe (50 non-wildfire European events), so task 8 below does not apply. GDACS and EONET are collected every 3 h by GitHub Actions into the `data`
branch; `eww sync` ingests and resolves; each external id is its own event; the map shows pins.
PROBLEM: the same cyclone or flood can arrive from two feeds and must become one pin, and every merge
must be reversible. A wrong merge (a disaster disappears) is worse than a wrong split (two pins).

TASK
1. Copernicus EMS collector (spine; add to --all-spine so Actions runs it): GET
   https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/ (paginated
   JSON; fields include code, name, category, countries, centroid as WKT POINT, activationTime,
   eventTime, gdacsId, closed). Map category to hazard_type. external_id=code. Attribution string:
   "Copernicus Emergency Management Service (© <year> European Union), <code>".
2. resolve_record() in full (pseudocode in §3): GLIDE match first; then (source_id, external_id); then
   for Copernicus, gdacsId → the event holding that GDACS external_id; then cross-source blocking:
   same hazard class, |Δstart| <= T, distance <= R with R/T per hazard from config
   (tropical_cyclone 500 km/10 d, flood 250/5, wildfire 100/7, severe_storm 200/3, drought 500/30,
   heatwave and coldwave 500/10, landslide 50/3, volcano 50/30, earthquake 150/2, tsunami 500/2).
   score = max(1.0 if GLIDE equal; 0.95 if same normalised storm name within T;
   0.5*(1-d/R) + 0.3*(1-Δt/T) + 0.2*title_similarity), title_similarity = token Jaccard for now (the
   embedding model arrives in M3; leave a hook). score >= 0.90 → merge automatically; 0.60–0.90 →
   create the event and write a merge_proposal; else create the event.
3. merge(from_event, to_event, performed_by): move source_records (and, later, documents), set
   from_event.status='merged' and merged_into_event_id, recompute the target's window, severity,
   centroid and primary geometry, write event_lineage with the exact moved ids. revert(lineage_id):
   the inverse, using only what the lineage row recorded, and a new lineage row with action='revert'.
   Recompute targets deterministically so re-running is a no-op.
4. Severity normalisation as one function: GDACS Green/Orange/Red → 0.33/0.66/1.0 with alertscore
   as tie-break within the band; EONET magnitudeValue scaled per unit where present, else 0.4;
   Copernicus activation adds an `ems_activation` flag (exposed in the GeoJSON properties) and raises the
   score to at least 0.66.
5. Review tab in app.py (a second Streamlit tab; still importing only eww.api plus a small
   eww.review module for the write actions): open merge_proposals with evidence (distance km, days apart,
   both titles, both sources) and Accept / Reject buttons; a list of the last 50 merges with a Revert
   button. Each click performs one action and reruns.
6. Map features: filters as Streamlit widgets (hazard multiselect, window slider 1–90 days, minimum
   severity 0/0.33/0.66/1.0); folium MarkerCluster; LocateControl; footprints via folium.GeoJson when the
   "footprints" checkbox is on (API include_footprints=True; GDACS bbox as a polygon, EONET polygons).
   All filtering stays inside events_geojson(); the viewer passes parameters only.
7. Labelling workflow: `eww labels candidates --days 30` writes data/labels/merge_candidates.csv (pairs
   from blocking with their evidence, one row each, an empty `same_event` column). I fill yes/no by hand
   into data/labels/merge_pairs.csv. `eww eval merges` prints precision and recall of the auto-merge rule
   and lists any true pair that is neither merged nor proposed.
8. Only if docs/m0-density.md says Europe failed: add a Meteoalarm ATOM collector
   (https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-<country>, CC BY 4.0 with redistribution
   conditions: show "EUMETNET – MeteoAlarm" or the national service, time of issue, a link to
   meteoalarm.org and their disclaimer) into a new `warning` table and a separate toggleable layer.
   Warnings are never events and never merge with them.

CONSTRAINTS
No deletes of event rows, ever. Every identity change goes through merge()/revert() and leaves a
lineage row. Auto-merge threshold and radii are config, not literals.

DEFINITION OF DONE
1. data/labels/merge_pairs.csv has >= 20 true cross-source pairs and >= 20 true non-pairs from real data.
2. `eww eval merges`: zero false merges; recall >= 60%; every remaining true pair is an open proposal.
3. Review tab: accept a proposal → one pin; revert → two pins; event_lineage gained 2 rows and
   merged_into_event_id is NULL again.
4. "flood, last 7 days, severity >= 0.66": UI pin count equals
   `eww export --since 7d --hazard flood --min-severity 0.66 --count`.
5. Events with a Copernicus activation show an "EMS activation" badge;
   SELECT COUNT(*) FROM source_record WHERE source_id='copernicus' AND event_id IS NULL is 0.
```

### Prompt M3 — Headlines on pins

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map on my Windows 11 laptop. Python 3.12
with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only; no JavaScript or CSS.
Free tiers only; official APIs only; honour every rate limit; no media bytes; never store article bodies
(text_excerpt <= 2,000 chars); idempotent; UTC ISO 8601; ULIDs; identifying User-Agent with my contact
address from config; settings in eww/config.py; pytest. Read docs/architecture.md §3 ("How identity is
decided", step 2) and the geocoding tiers in §1 before coding.
STATE: M0–M2 done. Events come from GDACS, EONET and Copernicus with cross-source merging, a review tab,
filters and footprints; identity numbers (aggregation radius, blocking, thresholds, weights) live in
identity.yaml; eww.matching.title_similarity() is token Jaccard and is the hook for the embedding
model (config.TITLE_SIMILARITY). No news exists yet. NO language model is used in this milestone.

TASK
1. GDELT collector (laptop only, runs inside `eww sync`): for each event active in the last 14 days,
   ordered by severity, build a query from the event's country names, admin1 names where known, the storm
   name if any, and hazard keywords in English and Italian, e.g.
   ("Emilia-Romagna" OR "Bologna" OR "Italy") (flood OR flooding OR alluvione). GET
   https://api.gdeltproject.org/api/v2/doc/doc?query=<q>&mode=ArtList&format=json&maxrecords=250
   &sort=DateDesc&startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS with startdatetime = the last
   successful run for that event (or event start - 2 days). Articles carry url, url_mobile, title,
   seendate, socialimage, domain, language, sourcecountry. Sleep >= 5 s between calls; on HTTP 429 back
   off and stop for this run. Cap the number of events queried per run in config. Cite GDELT in the app.
2. ReliefWeb reports collector: POST https://api.reliefweb.int/v2/reports?appname=<RELIEFWEB_APPNAME>
   filtering by disaster.glide when the event has one, else by country.iso3 plus disaster_type and
   date.original since the last run; fields title, url, date.original, source.shortname, disaster.glide,
   country.iso3, body (truncate to 2,000 chars). Max 1,000 calls/day. If RELIEFWEB_APPNAME is unset or
   the API returns 403, log one warning and skip. Terms: personal, non-commercial use.
3. Documents: insert into document with kind, url, url_canonical (https, lower-case host, tracking
   parameters stripped, no fragment), title, text_excerpt, language, publisher (domain), published_at,
   media_url = socialimage, media_kind, payload; UNIQUE (source_id, url_canonical) makes re-runs no-ops.
   Documents fetched by a per-event query remember that event (method='query') for scoring.
4. Deterministic extraction (eww/extract.py), stopping at the first stage that classifies and locates:
   (a) hazard lexicon per hazard type in English and Italian as a YAML file, with negative patterns
   ("flood of", "storm of criticism", "heatwave of"); (b) spaCy NER: xx_ent_wiki_sm (LOC) plus
   en_core_web_sm (GPE, LOC, FAC) for English; (c) geocoding tiers below; (d) publication date from the
   API. Write document_extraction with method='lexicon+ner', places JSON, hazard_confidence.
5. Geocoder (eww/geocode.py) behind one interface, results cached in geocode_cache including misses:
   tier 1 local gazetteer: `eww geonames load` downloads cities500.zip, admin1CodesASCII.txt and
   countryInfo.txt from https://download.geonames.org/export/dump/ into gazetteer_place and rebuilds
   gazetteer_fts; lookup by normalised name with a country hint (from country names/demonyms in the text,
   else the candidate event's country), ties broken by population. Tier 2 GeoNames web service
   http://api.geonames.org/searchJSON?q=&country=&maxRows=5&username=<GEONAMES_USERNAME>, 1 credit per
   call, stay under 1,000/hour and 10,000/day. Tier 3 Nominatim
   https://nominatim.openstreetmap.org/search?q=&countrycodes=&format=jsonv2&limit=1 behind a token
   bucket of 4 requests per minute, single-threaded. Record precision on every result. Attribution for
   GeoNames (CC BY 4.0) and OpenStreetMap (ODbL) in an About section of the app.
6. Embeddings (eww/embed.py): sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 on CPU;
   encode new documents in batches; store float32 little-endian BLOBs in document_embedding. An event's
   centroid vector = mean of its title embedding and its attached documents' embeddings, computed per run.
7. attach_document() exactly as in §3: candidates by hazard class and time window and (place within 2R
   or same country or method='query'); s = 0.45*spatial + 0.25*temporal + 0.30*text, with the no-place
   rule and the +0.10 query prior; >= 0.75 attached, 0.55–0.75 candidate, else nothing. Write score_parts.
   Store resolved places as event_geometry role='mention' linked to the document. `--rebuild` recomputes
   every attachment from scratch on a copy. Group syndicated copies (cosine >= 0.95) for display.
8. Viewer: a "News" tab in the sidebar for the clicked event: headline (link), publisher, time, thumbnail
   via st.image(media_url) with a plain link fallback if the image fails; syndicated copies collapsed to
   one line with a source count. Candidate attachments appear in the Review tab with Accept / Reject.
9. Housekeeping: `eww purge` deletes unattached documents older than 60 days with their extraction and
   embedding rows. `eww eval attachments --sample 100` writes a CSV of attached rows for me to label
   and computes precision from the filled file. `eww doctor` adds coverage, cache hit rate and rate-limit
   maxima (read from the structured log).

CONSTRAINTS
No model calls. No fetching of article pages. Every external call passes through one rate-limited
client per provider with the limits in config. Thresholds and weights are config, not literals.

DEFINITION OF DONE
1. >= 50% of events with severity >= 0.66 active in the last 14 days have >= 3 attached documents.
2. 100 random attached rows hand-checked: >= 90 correct; docs/m3-eval.md lists the errors and causes.
3. Log shows Nominatim <= 4 requests in any minute, GeoNames <= 1,000 in any hour, GDELT spacing >= 5 s,
   and a User-Agent with my contact address on every provider.
4. geocode_cache hit rate >= 70% in the second week (`eww doctor`).
5. SELECT MAX(length(text_excerpt)) FROM document <= 2000; data/ holds only the SQLite file and model
   caches.
6. `eww attach --rebuild` on a copy of the database reproduces identical event_document rows.
```

### Prompt M4 — Summaries and numbers, under a hard budget

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map on my Windows 11 laptop. Python 3.12
with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only; no JavaScript or CSS.
Budget €25/month all-in; free and local first. Official APIs only; no media bytes; excerpts <= 2,000
chars; idempotent; UTC ISO 8601; ULIDs; settings in eww/config.py; pytest. Read docs/architecture.md
§1b (unit cost) and §3 (document_extraction, llm_call) first. Ollama is installed on this machine
(24 GB RAM, Ryzen AI 7 CPU); an Anthropic API key MAY be present in .env as ANTHROPIC_API_KEY.
STATE: M0–M3 done. Events, cross-source merging, and deterministic news attachment work. Some documents
leave extraction without a hazard type or a place; events have no written summary yet.
PROBLEM: fill hazard/place/figures for the ambiguous documents and write a short per-event summary with
casualty figures, with a model, such that monthly spend cannot exceed a cap and every number is traceable
to source text.

TASK
1. An Extractor interface in eww/llm.py: extract(document, candidate_events) -> Extraction and
   summarise(event, authority_text, documents) -> Summary, both returning validated pydantic models.
   Two implementations selected by EWW_LLM_BACKEND (default "ollama"): 
   - OllamaExtractor: POST http://localhost:11434/api/chat with model qwen2.5:7b-instruct (pull it if
     missing and say so), stream=false, options temperature=0, and `format` set to the JSON schema of the
     expected output so the shape is constrained. cost_usd = 0.
   - ClaudeBatchExtractor: the official `anthropic` SDK, model claude-sonnet-5, submitted through the
     Message Batches API (client.messages.batches.create, poll processing_status until "ended", read
     results by custom_id, never by position). Use structured outputs via
     output_config={"format": {...json schema...}} and output_config effort "low"; put the fixed
     instructions + schema + examples first in the system prompt with cache_control so the prefix (make
     it >= 1,024 tokens) is cached; the per-document payload comes after. Do not use assistant prefill
     or budget_tokens; check the current Anthropic docs for the exact field names before writing code.
     Compute cost_usd from usage fields with prices in config (defaults: input $2/M, output $10/M, batch
     x0.5, cache read x0.1, cache write x1.25) and write one llm_call row per document.
2. Budget guard: EWW_LLM_BUDGET_USD (default 10). Before any cloud batch, sum llm_call.cost_usd for the
   current UTC month and estimate the batch from token counts; if the estimate would cross the cap, run
   the local backend instead and log "budget cap reached". With the cap at 0 the cloud backend is never
   called.
3. Output schema for extraction: hazard_type (enum from schema.sql or null), places [{name,
   country_hint}], event_date (ISO date or null), figures {dead, injured, missing, displaced} each
   {value, evidence_span} or null, confidence 0..1. Validation before anything is written: hazard_type
   must agree with the lexicon when the lexicon found one; each place is kept only if it geocodes (M3
   tiers) within 2R of a candidate event; each evidence_span must be a verbatim substring of the document
   title or excerpt; anything failing becomes null. Write document_extraction with method
   'llm:<model>' and the validated fields only. The model runs only for documents the deterministic
   stage left without a hazard type or without a resolved place.
4. Summaries: for each active event with >= 3 attached documents that were not part of the last
   summary, build input = authority description (GDACS/EONET/ReliefWeb text) + the 8 highest-scoring
   attached documents (title + excerpt) and ask for <= 3 sentences plus figures with evidence spans;
   refresh at most once per 24 h per event. Store event.summary, summary_updated_at, summary_method.
   Every sentence containing a number must map to an evidence span that is a verbatim substring of one
   of the inputs; drop sentences that fail. Show the summary and its "as of" time in the sidebar.
5. Golden set: tests/golden/documents.jsonl (30 documents I will label: expected hazard_type, places,
   figures) and tests/golden/events.jsonl (10 events with expected figures). `eww eval extraction
   --backend ollama|anthropic` prints hazard accuracy, place resolution rate, figures exact-match rate
   and the evidence-span check, and the total cost of the run.
6. `eww sync` gains the extract-with-model and summarise steps after attach; `eww doctor` reports
   month-to-date spend, documents awaiting extraction older than 24 h, and evidence-span violations.

CONSTRAINTS
Local backend is the default. Never call the cloud without the budget check. Never write a figure
without an evidence span. The Claude path must work with prompt caching and the Batch API together.

DEFINITION OF DONE
1. `eww eval extraction` on the golden set: hazard accuracy >= 90%, place resolution >= 80%, figures
   exact-match >= 80%, and every figure's evidence span is a verbatim substring (script-checked).
2. Every summary sentence containing a number has an evidence span; `eww doctor` shows 0 violations.
3. SELECT SUM(cost_usd) FROM llm_call WHERE called_at >= date('now','start of month') <= 10; with
   EWW_LLM_BUDGET_USD=0 a full `eww sync` writes zero rows with backend='anthropic'.
4. After a nightly `eww sync`, no document that passed blocking > 24 h ago lacks a document_extraction row.
```

### Prompt M5 — Posts, videos and weather on click

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map on my Windows 11 laptop. Python 3.12
with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only; no JavaScript or CSS.
Free tiers only; official APIs only; honour rate limits and each platform's deletion rules; store links
and metadata only, never media bytes; idempotent; UTC ISO 8601; ULIDs; identifying User-Agent; settings
in eww/config.py; secrets only in .env; pytest. Read docs/architecture.md §1 (social ranking) and §3.
STATE: M0–M4 done. Events have attached news with thumbnails, model-written summaries under a cap, and
a review tab. No social posts, videos or weather yet.

TASK
1. Bluesky collector (laptop, inside `eww sync`): create a session with POST
   https://bsky.social/xrpc/com.atproto.server.createSession using BLUESKY_HANDLE and BLUESKY_APP_PASSWORD
   from .env (refresh with com.atproto.server.refreshSession; never log tokens). For each event active in
   the last 14 days, GET https://bsky.social/xrpc/app.bsky.feed.searchPosts?q=<place + hazard keywords>
   &sort=latest&limit=100&since=<last run>&until=<now>&lang=<en|it> with the Bearer token. Store as
   document kind='post': external_id = post uri, url = https://bsky.app/profile/<handle>/post/<rkey>,
   author = handle, text_excerpt = record.text, published_at = record.createdAt, media_url = first
   embed image fullsize URL or the external link thumbnail, payload = the post JSON. Self-imposed limit
   1 request/second. The public unauthenticated endpoint returns 403, so always authenticate.
2. YouTube collector: GET https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&q=<query>
   &publishedAfter=<event start>&order=date&maxResults=25&key=<YOUTUBE_API_KEY>. Hard limit 100 searches
   per UTC day (the 2026 granular quota): rotate across active events by severity and record searches
   performed in collector_run.items_seen for source 'youtube'. Store kind='video': external_id = videoId,
   url = https://www.youtube.com/watch?v=<id>, title, author = channelTitle, published_at,
   media_url = snippet.thumbnails.medium.url. Compliance: attached videos are refreshed with
   videos.list?part=snippet&id=<up to 50 ids> (1 unit) at least every 30 days; a video no longer returned
   gets removed_at.
3. Mastodon collector: GET https://mastodon.social/api/v1/timelines/tag/<tag>?limit=40 unauthenticated,
   tags per hazard type (e.g. flood, alluvione, wildfire, hurricane), <= 300 requests per 5 minutes.
   Store kind='post' with the status URL, account acct, content stripped of HTML, created_at, first
   media_attachments preview_url.
4. Reddit collector behind EWW_REDDIT_ENABLED (default false; I will enable it only after Reddit approves
   my request). When enabled: OAuth script app, User-Agent "windows:eww:v0.x (by /u/<username>)", GET
   https://oauth.reddit.com/search?q=<query>&sort=new&t=week&limit=50&type=link, store permalink, title,
   subreddit, created_utc, preview image URL and nothing else; <= 60 requests/minute self-imposed
   (the limit is 100 QPM). Compliance: every <= 48 h, GET /api/info?id=t3_... for stored ids (<= 100 per
   call) and set removed_at for anything missing or marked removed; hidden rows are never shown.
5. Attachment: posts and videos go through the same attach_document() as news (method='query' with the
   +0.10 prior); no model calls for social text. Deletion sweep: a weekly job checks Bluesky posts via
   app.bsky.feed.getPosts?uris=<up to 25> and sets removed_at when a post is gone.
6. Viewer: the sidebar for the clicked event gets tabs News / Posts / Videos / Weather. Posts show text,
   handle, time, thumbnail by URL (fallback to link); videos show thumbnail + title as a link (no embed).
   Weather tab: GET https://api.open-meteo.com/v1/forecast?latitude=&longitude=&current=temperature_2m,
   precipitation,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,
   precipitation_sum,weather_code&forecast_days=5&timezone=auto at the pin coordinates, cached with
   st.cache_data(ttl=1800), never stored; show current conditions and a 5-row daily table; attribution
   "Weather data by Open-Meteo.com (CC BY 4.0)". The viewer still imports only eww.api (add a small
   eww.weather helper that the viewer may import).

CONSTRAINTS
No X, Instagram, TikTok, Facebook or Telegram. No scraping of any web page. No media bytes on disk.
Every provider has one rate-limited client with limits in config. Missing credentials → that collector
logs one line and is skipped; nothing else breaks.

DEFINITION OF DONE
1. >= 30% of events active in the last 7 days have >= 1 attached post or video.
2. 50 random attached posts hand-checked: >= 40 relevant.
3. Log shows <= 100 YouTube searches per UTC day and Bluesky <= 1 request/second; data/ holds no image
   or video files.
4. Delete a test post of my own on Bluesky; after the next `eww sync` its removed_at is set and it is
   gone from the sidebar.
5. Clicking any pin shows current temperature and a 5-day forecast for the pin's coordinates within 3 s.
```

### Prompt M6 — Open it from your phone (optional)

```
CONTEXT
Extreme Weather Watch (EWW): private, single-user hazard-event map, so far running only on my Windows 11
laptop. Python 3.12 with uv, SQLite (WAL, STRICT), Streamlit + folium. I know Python and SQL only; no
JavaScript or CSS. Free tiers only. The laptop is the only writer of the database; a hosted copy is
read-only. Read docs/architecture.md §2 (the GeoJSON contract is the boundary a future frontend will
consume) and the hosting row in §1.
STATE: M0–M5 done. `eww sync` maintains data/eww.sqlite; app.py renders the map from eww.api only.
GOAL: a private hosted copy I can open on my phone, without touching the pipeline, plus a local HTTP
endpoint proving the boundary.

TASK
1. `eww publish`: run `VACUUM INTO 'data/publish/eww.sqlite'` for a consistent copy; upload it with
   boto3 to a Cloudflare R2 bucket (S3-compatible: endpoint https://<ACCOUNT_ID>.r2.cloudflarestorage.com,
   credentials R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET from .env) as key eww.sqlite, plus
   eww.sqlite.meta.json {published_at, size_bytes, sha256}. Print the size and the time.
2. Split dependencies with uv groups: `viewer` (streamlit, folium, streamlit-folium, httpx, boto3) and
   `pipeline` (everything else: sentence-transformers, spacy, anthropic, ...). app.py and eww.api must
   import nothing from the pipeline group; add a test that imports app's modules with the pipeline
   packages absent. Export `requirements.txt` for the viewer group only (`uv export --group viewer
   --no-dev --no-hashes`).
3. Hosted mode in app.py: when EWW_DB_URL (or Streamlit secrets) points at R2, download eww.sqlite to a
   temp directory at start and re-check eww.sqlite.meta.json every 30 minutes (st.cache_resource with
   ttl); open the file read-only (mode=ro URI). Locally nothing changes.
4. Deploy on Streamlit Community Cloud as a private app from this GitHub repository (main, app.py),
   R2 credentials in the app's secrets manager, Python 3.12. Write docs/hosting.md with the exact steps,
   the one-private-app limit of the free tier, and how to remove the app.
5. `eww serve`: FastAPI + uvicorn on localhost:8000 with GET /events.geojson mapping query parameters
   (since, until, hazard repeated, min_severity, status, bbox, include_footprints, limit) onto
   events_geojson(), GET /health returning the heartbeat meta, permissive CORS for localhost. This is the
   URL a future JavaScript map would fetch; it adds no logic of its own.

CONSTRAINTS
The hosted app is read-only and holds no pipeline code paths. No secrets in the repo. The hosted app
must start with the viewer dependencies only (no torch, no spaCy). Do not add authentication of your own;
Streamlit's private-app login is the access control.

DEFINITION OF DONE
1. A phone on mobile data opens the app URL, logs in, and shows the same pin count as the laptop within
   30 minutes of `eww publish`.
2. An incognito browser without login cannot see the app.
3. curl "http://localhost:8000/events.geojson?since=7d&hazard=flood" returns a FeatureCollection
   identical to `eww export --since 7d --hazard flood`.
4. R2 usage < 1 GB; the hosted app starts in under 60 seconds.
```

## 7. Open questions

Only things that need your input or an external check.

1. **Geographic and language focus.** The lexicons and GDELT `sourcelang` filters start in English and Italian, and M0's density bar singles out Europe. If your interest is elsewhere, say so before M3; each extra language is a small, separable addition.
2. **Laptop uptime.** How many hours a day is the laptop typically on? The local-model default assumes at least one to two hours of `eww sync` time on most days. Under an hour, the cloud fallback should become the default (still capped).
3. **Repository visibility.** Private keeps the 2,000 Actions minutes and keeps the `data` branch private; public gives unlimited minutes but publishes the branch and brings the 60-day inactivity rule. The plan assumes private.
4. **Accounts you are willing to create.** All free, all yours to create and to hold the credentials for: a Bluesky app password, a Google Cloud project for the YouTube key, a GeoNames username, a ReliefWeb appname request (the API returns 403 without an approved one since November 2025), a Reddit approval request (outcome uncertain), and for M6 a Cloudflare account. The plan degrades gracefully if any is missing.
5. **A contact address for User-Agent strings.** Nominatim, the OSM tile policy, Wikimedia and NWS all require an identifying User-Agent, and several ask for a contact. Decide which address to publish in it. Until you do, the code sends the repository URL (`EWW_CONTACT` in `.env`, default `https://github.com/giovanniliverani/weather-watch`).
6. **Where the SQLite file is backed up.** It is rebuildable from the data branch plus a re-run of enrichment, but a nightly copy to your existing backup location saves hours. Your resource, your call.
7. **Facts to verify before relying on them** (from memory or likely to change; everything else in the document was read from the official page on 2026-09-16):
   - GDACS: verified 2026-09-16 against the swagger file: `eventlist`, `alertlevel`, `fromDate`, `toDate`, `pageSize`, `pageNumber`; volcano (`VO`) events do appear in the JSON API (2 in the 30-day window); `TS` returned none, and including it in a combined `eventlist` collapsed the result to 4 rows, hence one request per type.
   - EONET: the [lat, lon] order of Polygon rings is observed (2026-09-16), not documented. If EONET fixes it, flood footprints and centroids shift until `EONET_POLYGON_AXES_SWAPPED` in `eww/config.py` is turned off; re-check whenever an EONET flood pin sits far from its GDACS twin.
   - ReliefWeb: appname approval turnaround, and whether its "no derivative works" clause matters if you ever publish summaries built from its reports.
   - GDELT: the DOC API's search lookback (assumed about three months) and the informal one-request-per-5-seconds limit.
   - Bluesky: numeric rate limits for authenticated `searchPosts` on `bsky.social` (only the general 3,000 per 5 minutes per IP is published).
   - YouTube: whether the 30-day storage rule for API data applies to titles and thumbnail URLs you display with a live link.
   - Open-Meteo and GeoNames limits, and the OSM tile policy's view of a Streamlit-served Leaflet map: read the current pages when you reach M3 and M5.
   - Anthropic prices and the Batch discount: the figures come from a table cached 2026-06-24.
   - Streamlit Community Cloud's one-private-app allowance and resource limits (stated as approximate, dated February 2024).
   - The euro-dollar rate used in §1b.
   - M1 exit criteria 1, 2, 4 and 5 after three days of scheduled runs (from 2026-09-16T18:07Z): `runs/*.jsonl` lines with `status: ok`, `first_seen_at` inside a laptop-off window, the strip's missed count against a hand count from the Actions page, and `uv run eww report volume` replacing the provisional `docs/m1-volume.md`. Note the 2026-09-17 observation in §4: the runs are happening but 1 h 35 min to 1 h 45 min late.
   - The workflow pins `actions/checkout@v5` and `astral-sh/setup-uv@v10.1.0` (setup-uv publishes no moving `v10` tag); bump them when GitHub deprecates their Node runtime.
   - Copernicus EMS: the API shape (paging, fields, `gdacsId` form) was read from the live endpoint on 2026-09-17. The `source` seed cites https://www.copernicus.eu/en/access-data/copyright-and-licences as the terms page; that page exists and speaks of licences, but its text was not read, so whether it covers redistribution of activation metadata and the exact credit line is **verify**. The credit line used ("Copernicus Emergency Management Service (© <year> European Union), <code>") is the one you gave in the M2 prompt.
   - `named_storm_km` = 5,000 km rests on one season of storms (14 named pairs, all correct); a same-named storm in two basins inside 10 days would test it.
   - GDACS reuses wildfire event ids (four cases on 2026-09-17: 1031101, 1031263, 1031440, 1031545 named fires on other continents than the EONET items citing them). Whether flood and cyclone ids are ever reused is unobserved; the aggregation radius covers it either way, but `eww doctor` does not yet flag an id whose country changed between snapshots.
10. **The aggregation radius per hazard.** `identity.yaml` starts at 25 km with 200 km for tropical cyclones, 100 km for severe storms and 250 km for drought, heatwave and coldwave; those overrides are my reading of the feeds' sampling, not yours. The 27 proposals the gate wrote on 2026-09-17 are the evidence to judge them by: correct pairs piling up for one hazard mean its radius is too tight (the four beyond-radius flood mirrors sit at 37 to 90 km), wrong ones getting through mean too wide. Widen per hazard, never the default.
8. **The heartbeat grace against a slow scheduler.** GitHub ran the collector 1 h 35 min to 1 h 45 min after each slot on 2026-09-17, so the 45-minute rule showed 7 of 8 runs missed while data kept arriving. Either widen `HEARTBEAT_GRACE_MINUTES` (a 2-hour grace still catches a stopped pipeline within one day) or keep the strict rule and read the red strip as "GitHub is late". Your call; the code change is one constant.
9. **Label the pairs.** `data/labels/merge_pairs.csv` carries a starter set of 24 yes and 24 no labelled from the feeds' own facts and the 11 open proposals with an empty `same_event`; M2's exit criteria 1 and 2 are yours to close by reviewing those rows and adding your own from `merge_candidates.csv`, then running `uv run eww eval merges`.

## Revision log

- **2026-09-16 — M0 done, density bar passed.** 3,228 events observed in 30 days (1,643 after excluding GDACS wildfires below Orange, 703 after also removing EONET mirrors of GDACS events), 5 hazard types and 6 continents with 3 or more events, 50 non-wildfire events in Europe, so the anchored-feeds bet holds. Marked M0 done with its measured numbers and the collector facts it uncovered (per-type GDACS paging, mandatory `alertlevel`, EONET [lat, lon] polygons), added `limit=0` to the GeoJSON contract, gave M2 the EONET→GDACS eventid join and closed its Meteoalarm conditional, verified the GDACS parameter casing and volcano presence in §7, added the EONET axis-order check and the User-Agent default. Sections touched: 0, 2, 4, 6 (Prompt M2 STATE), 7.
- **2026-09-16 — M1 built and its first run observed.** Orphan `data` branch pushed, `collect.yml` dispatched once (31 s, gdacs=2,192, eonet=1,051, commit `28e7df3`), `eww sync` replayed it on the laptop (2 files, 11 new records), second ingest 0 new files. Added `expected_runs_7d` to the §2 contract and the `heartbeat` view (schema version 2) to the §3 DDL, recorded the M1 status and design fixes in §4, added the three-day follow-ups and the action pins to §7. Sections touched: 2, 3, 4, 7.
- **2026-09-17 — M2 built; exit criteria 3, 4, 5 met, 1 and 2 on a starter set.** Copernicus EMS joined the spine and now creates events (8 of 9 activations had no GDACS twin); `resolve_record()` implemented with the deterministic keys, a GLIDE guard for one number on two GDACS ids, basin-wide blocking for named storms, re-scoring on sibling attach, a key-conflict guard and the rule that differently named storms and human-reverted pairs never merge automatically; database rebuilt from snapshots (3,855 records → 2,392 events, 941 key joins, 13 name merges, 11 proposals); severity measured (quantised `alertscore`, degenerate GDACS bboxes). Recorded the algorithm and the severity function in §3, moved Copernicus from §5 into the §1 spine rows, noted the canonical-only footprints, `ems_activation`, `--count` and the `eww.review` import in the §2 contract, added the late-Actions observation to M1 and questions 8 and 9 plus the Copernicus licence **verify** to §7. Sections touched: 0, 1, 2, 3, 4, 5, 6 (Prompt M3 STATE), 7.
- **2026-09-17 — Aggregation radius; identity numbers moved to identity.yaml.** Flood in Nepal showed three feeds 85 to 102 km apart on one pin. Decision: two feeds share a pin automatically only inside an aggregation radius (25 km default, per-hazard overrides), ids and GLIDE included, otherwise a proposal; the radius, blocking radii, thresholds and weights now live in `identity.yaml`, loaded and validated by config. Rebuilt: 2,431 live events, 14 merges, 39 proposals, 27 kept apart by the radius; the gate exposed GDACS reusing wildfire ids (four EONET items had been attached to fires on other continents). `eww report identity` writes `docs/m2.md`. Updated the §1 clustering row, the §3 pseudocode and radius paragraph, the M2 status in §4, Prompt M3's STATE in §6, §7 (id reuse, question 10). Sections touched: 1, 3, 4, 6, 7.
