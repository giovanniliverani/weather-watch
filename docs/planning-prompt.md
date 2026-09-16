# Planning prompt — Extreme Weather Watch

Paste everything below the line into a fresh session. Edit the **MY CONSTRAINTS** block first — those
lines change the answer more than anything else in this prompt.

---

You are a staff-level engineer who has shipped data-ingestion products end to end. I am about to build
the project described below and I want an architecture and sequencing document before I write any code.
Your job is to make decisions I can act on, not to survey the option space.

## THE PROJECT

This is the README, verbatim. Treat it as a statement of intent, not a specification — parts of it are
probably wrong, and I want you to say so.

> # Extreme Weather Watch
>
> ## Introduction
>
> The value proposition of this website is to collect extreme weather events from news/social media
> channels/other weather apps/etc. in a single space. Users can see, at a glance, what is happening
> around the world, weather-wise. The website would cluster these extreme weather events, catalogue them
> by type, strength, etc. and provide users quick access to:
>
> - written summary information related to the event (start date, ongoing, size, damages, n injured,
>   missing, dead, etc.)
> - pictures and videos of the event pulled from social media, prominent people (politicians,
>   meteorologists), newspapers, etc.
> - social media posts related to the event (mainly x, bluesky, instagram, reddit)
> - current weather conditions + 3/5 days weather forecast
>
> Ideally, the website should present itself like https://zoom.earth/ or Google Maps/OpenMaps. It should
> be a world map, easy to navigate, zoom in, etc. It should be easy to pin your location using
> geolocation data, with the map centred on where you are.
>
> The map would display extreme weather events over the last X days using different icons. There would be
> filters you can choose to show only events of a specific type / scale / timeframe. (e.g. only fires
> bigger than X in the last Y days).
>
> The map would be interactive; by clicking on the dot of the natural event (e.g. red for fire, blue for
> flood, etc.) we would be able to see the photos/videos of the event, the headlines, maybe a feed (as
> explained above)
>
> Eventually, if the website gets enough traction, we would introduce the possibility for a user to make
> their own account and submit information for an event. An event could be created by said user or they
> could join an already existing event. The user would be able to add pictures/videos/links but also
> enrich the description of the event. Practically making this a Wikipedia for natural disasters.
> Ultimately, the end-goal of the website would be to have first-hand user input to document extreme
> weather events, without changing the platform.
>
> We realise this is too far ahead, so the first step would be to have a platform that finds (1)
> catalogues (2) and displays events without users' input. However, it is useful to consider this when
> designing a proper database structure to accommodate this future feature. No frontend is needed for
> this right now, as I would be interested mainly in the MVP right now: data collection, data
> presentation, website usability, front-end design, etc.
>
> ## Proof of Concept
>
> 1. Version control via GitHub.
> 2. Aggregate data either via a "news" API or via web scraping. (Interesting to research if an LLM swarm
>    + bots + search with a browser such as Browserbase would be sufficient since parsing websites is too
>    complex)
> 3. Save the data in a database (cloud-based?/postgreSQL? not sure what is best, to investigate)
>    (critical to have a method to properly aggregate events, e.g. if 5/10 posts/news articles are about
>    the same event we should be able to understand this and group these)
> 4. Normalise, clean and enrich the data. Add approximate (or precise depending on the information
>    available) location pins (even multiple pins for the same article — e.g. if it mentions streets or
>    multiple cities).
> 5. Display data via a website.
> 6. Add a "donate" button (for later).
>
> ## Links
>
> Great maps: earth.nullschool.net, zoom.earth, worldview.earthdata.nasa.gov, wxcharts.com, weather.gov,
> msn.com/en-gb/weather/maps
>
> Other maps: openstreetmap.org, what3words.com
>
> Good weather data: weatherspark.com, weatherpro.com, charts.ecmwf.int
>
> Collection of catastrophic events data: emdat.be, gdacs.org, disasters-nasa.hub.arcgis.com,
> reliefweb.int/disasters
>
> Data on climate change: climatechangetracker.org, atlas.climate.copernicus.eu, climatereanalyzer.org,
> cds.climate.copernicus.eu
>
> Repos/Documentation: github.com/sunshineplan/weather, gitlab.com/KNMI-OSS/KNMI-App

## MY CONSTRAINTS

Read these carefully. They are tighter than most projects of this shape and they should visibly constrain
your answer.

- **Team:** one developer, part-time. No one to hand ops to.
- **My skills:** Python and SQL, comfortably. That is genuinely it. **I have never done web development
  of any kind** — no JavaScript, no React, no CSS, no experience deploying a website. Do not assume I
  will pick it up along the way. A plan whose second milestone requires me to learn a frontend framework
  is a plan I will abandon.
- **Budget:** hard ceiling €25/month, all-in, including LLM and API spend. €50 is an absolute maximum I
  am unhappy about. Assume anything above that is simply unavailable — do not design for it and then
  suggest I economise. Free tiers and locally-run components are strongly preferred.
- **Where it runs:** localhost, on my laptop, for as long as possible. I will deploy only once there is
  something I am comfortable with. Treat hosting as a late milestone, not a foundation.
- **Visibility:** this is a private, personal project for now. It may never be published. Design for a
  single user — me — and say where that assumption is load-bearing.
- **Ops appetite:** low. Prefer managed or free-tier over anything I have to patch, and prefer one
  process over four.
- **Legal appetite:** none for terms-of-service violations, even privately. If a source requires scraping
  against its terms, say so and route around it.
- **Target:** an interactive world map, running on my machine, with real events on it, reached in a few
  weeks of part-time work. Not a product. A thing that works.

## WHAT I WANT FROM YOU

One document, structured exactly as in OUTPUT FORMAT below. Its purpose is to let me start building
tomorrow and to stop me from making a decision in week one that I have to unwind in week six.

## HARD PROBLEMS YOU MUST RESOLVE, NOT DEFER

These are the parts where a generic plan fails. Take a position on each, in the document, with reasoning.

1. **Event identity.** What is an "event" as a row in a database? Given ten articles and forty posts,
   what mechanism decides that twelve of them describe the same flood? Be concrete about the blocking
   strategy (space, time, hazard type) and the similarity step. Which is worse for this product — wrongly
   merging two events, or wrongly splitting one — and how does that asymmetry shape the design? Say
   whether a human ever needs to be in the loop, and where.

2. **Source strategy, and the discovery/enrichment split.** Begin by separating two jobs the README runs
   together: **discovery** (learning that an event happened, where, when, how severe) and **enrichment**
   (attaching links, headlines and posts to an event already known). They have completely different cost
   profiles and probably want completely different machinery. State which sources serve which job.

   Then decide which source class is the **spine** of the data model and which is layered on top. Note
   that the URLs in the README are illustrative of the *kind* of thing I want and are not a shortlist —
   judge sources on merit and propose better ones if they exist. But you must explicitly evaluate, as a
   class, the authoritative structured feeds that emit typed events with stable identifiers, coordinates
   and severity already parsed (GDACS, ReliefWeb, NASA FIRMS, USGS, Copernicus EMS, and others you
   identify), because they are free and deterministic and that is decisive at my budget. If your
   conclusion contradicts the README's implied ordering, say so plainly and explain what doing it the
   README's way would cost me.

   **Evaluate this proposal of mine specifically, and push back if it is wrong:** that a swarm of very
   cheap LLMs, possibly with browser automation, could substitute for paid news and social APIs. Assess it
   separately for discovery and for enrichment, since I suspect it is a much better idea for one than the
   other. Consider reliability and silent failure, not just token cost.

   For social media, cost me out the cheapest version that still adds something: **storing links and
   metadata only**, with no media fetched and no paid tier. Say which platforms are actually reachable
   under a €25 ceiling and which I should drop entirely, and rank the reachable ones by effort-to-value.

3. **Geocoding.** Turning prose into coordinates. Which provider, at what precision, under what rate
   limits and licence terms, and what happens to an event whose location cannot be resolved. Address the
   README's "multiple pins per event" idea — does it survive contact with the data model, or is it a
   v2 feature?

4. **Media, deliberately deprioritised.** I am relaxing the copyright question: this is private and
   pre-production, and publication is hypothetical. Do not spend the document litigating it. Instead
   adopt one cheap rule and confirm it survives: **store references and provenance, never copies of
   bytes.** That costs nothing now, keeps storage near zero, and means a future decision to publish is a
   policy change rather than a rewrite. Tell me if you think that rule is wrong or if it forecloses
   something I will want. Rendering media — embeds, thumbnails, galleries — is out of scope for v1 unless
   it is genuinely free; a plain link is an acceptable v1 answer.

5. **Unit cost, against a €25 ceiling.** Estimate the marginal cost of ingesting one document — tokens
   in, tokens out, model choice — and multiply out to a monthly figure at a realistic volume. Then show
   the total monthly bill for your proposed v1 as a small itemised table, and demonstrate that it fits
   under €25. If it does not, cut scope until it does rather than presenting me with an overrun.

   Identify every place where deterministic code, a locally-run model, or a cached vector embedding can
   replace a paid API call without materially hurting quality — I would much rather spend engineering
   effort than carry a recurring bill. Note in particular that text embeddings can be generated locally
   at zero marginal cost, and say whether that changes your clustering design. Flag any component whose
   cost scales with event volume rather than staying flat, since that is what will bite me later.

6. **Schema forward-compatibility.** User contributions, provenance, edit history, event merging and
   splitting after the fact, and multiple geometries per event all need to be *designable* now even though
   they are built later. Show the schema that absorbs those without a painful migration. Be explicit about
   which columns and tables exist purely for the future.

7. **Freshness, and the laptop problem.** How stale can the map be before the thing feels dead? Pick a
   number and derive the architecture from it — cron versus queue, polling versus streaming, how much of
   the pipeline must be incremental — rather than the reverse.

   Then confront a tension my constraints create: I want to run on localhost, but event history only
   accumulates if collection runs continuously, and my laptop is frequently off. A pipeline that only
   runs when I happen to be working will produce a patchy dataset, and I probably will not notice for
   weeks. Resolve this within the budget — free-tier schedulers exist, including GitHub Actions on a cron
   — or tell me explicitly which gaps I should accept and how the system should record that it was not
   running, so I can tell a quiet world from a stopped pipeline.

8. **The frontend, given that I have never built one.** The README contradicts itself here — it says "no
   frontend is needed right now" and then lists "website usability, front-end design" as MVP concerns.
   Resolve that and state the resolution you are planning against.

   More importantly, design around my actual skills. I want a map I can pan, zoom and click, and I have
   written zero lines of JavaScript in my life. Decide how much web technology I genuinely have to learn
   to get an interactive map in v1, and drive that number as close to zero as the result allows. Consider
   seriously whether the v1 viewer should be a **disposable** Python-native layer, with the backend
   emitting standard geospatial data over HTTP so that the viewer can later be replaced by a real
   web frontend without touching the data pipeline. If you think that is the right call, name the
   interface boundary precisely and state what I must avoid doing so the swap stays cheap. If you think
   it is the wrong call — that the throwaway layer will cost me more than learning the real thing — argue
   that instead. Do not split the difference.

## RULES

- **Decide.** Where there is a choice, make it, give at most three sentences of justification, and state
  what evidence would change your mind. Do not write "you could use X or Y." If you genuinely cannot
  decide without information I have not given you, that belongs in Open Questions, not in the body.
- **No code**, with two exceptions: SQL DDL for the core tables, and short pseudocode if it is the
  clearest way to express the clustering logic.
- **Walking skeleton, not layer-by-layer.** Every milestone must end in something I can look at, query,
  or click. I do not want a milestone that delivers only a schema.
- **Falsifiable exit criteria.** Each milestone states how I know it is done, in terms I could check
  without your help. "Ingestion works" is not an exit criterion; "a scheduled job has run for three
  consecutive days and the event table has non-zero rows with no duplicate GDACS ids" is.
- **At most seven milestones.** If it does not fit, your v1 scope is too big — cut it and put the
  remainder in the Not In V1 list.
- **Name the riskiest assumption** in the whole plan, and make the earliest milestone the cheapest test
  of it. If the plan's central bet is wrong, I want to find out in week one, not week six.
- **No hour or day estimates.** Flag relative size — small, medium, large — and say which milestone is
  most likely to overrun and why.
- **Disagree with me where warranted.** The README was written by someone excited, not someone who had
  costed it. If a feature is disproportionately expensive relative to what it adds, say so.
- **Flag your own uncertainty.** If a claim about a specific API's terms, rate limits, or pricing is from
  memory and might be stale, mark it as something I should verify rather than asserting it.
- No filler. Skip generic engineering advice — I do not need to be told to use version control or write
  tests.

## OUTPUT FORMAT

**0. Summary.** Under 200 words: what I am building first, and the single most consequential thing I am
probably getting wrong.

**1. Decisions.** A table: decision, choice, why, what would change my mind. Cover at minimum language
and runtime, database, local development setup, scheduling, map rendering and the viewer layer, geocoding,
the clustering approach, and eventual hosting. Include one row for "how much web technology I have to
learn for v1," answered as concretely as you can.

**1b. Monthly cost.** A short itemised table of recurring cost for the v1 you are proposing, totalled,
with anything free marked as free and anything I should verify marked as such. This exists so the €25
ceiling is checked rather than assumed.

**2. Architecture.** Components and the flow of data between them. A text diagram. Say what each component
is responsible for in one line, and name the boundary where I could later swap an implementation.

**3. Data model.** Core tables as SQL DDL, with keys and the indexes that matter. Call out exactly where
event identity is enforced. Mark future-proofing columns as such.

**4. Milestones.** M0 through at most M6. For each: goal in one sentence, what is in scope, what is
explicitly out, falsifiable exit criteria, which risk it retires, and relative size.

**5. Not in v1.** Everything from the README deliberately deferred, each with a one-line reason. Be
ruthless here — this section existing is what makes the rest credible.

**6. Implementation prompts.** One prompt per milestone, each self-contained enough to paste into a fresh
coding session that has never seen this document: the context it needs, the task, the constraints, and the
definition of done. These are the artefacts I will actually work from, so write them with care rather than
compressing them.

**7. Open questions.** Only things that genuinely require my input or an external check — decisions about
my own resources, appetite, or facts you cannot verify. Not a dumping ground for choices you should have
made.
