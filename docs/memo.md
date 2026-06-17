# Catalyst Content Analytics — Technical Memo

## Architecture and data model

The system has four layers: connectors pull raw data, a scheduler orchestrates the pipeline, a Postgres database stores everything, and a Streamlit dashboard renders it read-only.

```
connectors/   → pull posts + metrics from each platform
scheduler.py  → pull → tag → score → recommend (weekly, self-sustaining)
Supabase      → time-series Postgres, shared across clients
dashboard.py  → read-only Streamlit, no LLM calls
```

### Schema

**`posts`** — one row per piece of content, written once on first ingest.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `client_id` | text | Scopes all data per client. Multi-tenant by default. |
| `platform` | text | `youtube`, `linkedin`, `x` |
| `platform_post_id` | text | Native ID from the platform |
| `published_at` | timestamptz | |
| `title` | text | First 500 chars of post text |
| `topic_cluster` | text | LLM-tagged on first ingest, never changed |
| `format` | text | long-form, list, how-to, opinion, etc. |
| `hook_type` | text | stat, question, bold-claim, story, contrarian |
| `length_bucket` | text | short / medium / long |
| `tagged_at` | timestamptz | Non-null = tagging complete. Hard guard against re-tagging. |

**`metrics_snapshots`** — the time-series core. One row per post per pull cycle.

| Column | Type | Notes |
|---|---|---|
| `post_id` | uuid FK → posts | |
| `pulled_at` | timestamptz | When this snapshot was taken |
| `views` | bigint | Impressions (LinkedIn) or views (YouTube) |
| `engagements` | bigint | Likes + comments + shares/reposts |
| `clicks` | integer | |
| `engagement_rate` | numeric(6,4) | Provided by platform or computed |
| `raw` | jsonb | Full platform response — saves, DMs sent, EMV, watch time, etc. |

**`attribute_scores`** — recomputed from SQL every cycle. No LLM involved.

| Column | Type | Notes |
|---|---|---|
| `client_id` | text | PK component |
| `platform` | text | PK component — LinkedIn and YouTube scores are never blended |
| `attribute_type` | text | topic_cluster, format, hook_type, length_bucket |
| `attribute_value` | text | e.g. "stat", "long-form", "GTM strategy" |
| `avg_engagement_rate` | numeric | Latest snapshot per post, averaged across all posts with this attribute |
| `trend_delta` | numeric | Recent 30-day avg minus prior 30–60-day avg. Positive = rising. |
| `confidence` | text | low (<3 posts), medium (3–9), high (≥10) |

**`hypotheses`** — LLM-generated falsifiable beliefs about what is working. Auto-activated when a high-confidence, rising attribute is detected. Auto-deactivated when confidence drops.

**`recommendations`** — five content ideas per cycle, grounded in attribute scores, with full scores snapshot stored for audit and reproducibility.

**`pull_log`** — one row per scheduler run: started_at, finished_at, posts added, snapshots added, errors.

**`channel_snapshots`** — subscriber and total-view history for channel-level metrics (YouTube only).

### Indexes

```sql
idx_metrics_post_time            ON metrics_snapshots (post_id, pulled_at DESC)
idx_metrics_pulled_at            ON metrics_snapshots (pulled_at)
idx_posts_attributes             ON posts (topic_cluster, format, hook_type)
idx_posts_client_platform        ON posts (client_id, platform, published_at DESC)
idx_attribute_scores_client_platform ON attribute_scores (client_id, platform, attribute_type)
idx_channel_snapshots_platform_time  ON channel_snapshots (platform, pulled_at DESC)
```

The compound index on `(post_id, pulled_at DESC)` covers the most expensive query pattern: finding the latest snapshot per post. The `(client_id, platform, published_at DESC)` index covers the dashboard's filtering path without a full table scan.

### How it scales

`metrics_snapshots` is the only table that grows unboundedly — one row per post per weekly pull. At 500 posts and 52 pulls per year, that is 26,000 rows per year per client: trivial. At scale across many clients:

- **Partition `metrics_snapshots` by `pulled_at`** (monthly or quarterly). Postgres partition pruning means time-bounded queries touch only recent partitions.
- **Materialise `attribute_scores`.** Currently a full recompute each cycle (fast at current scale). At 50M rows, switch to incremental maintenance with a trigger or scheduled function.
- **Columnar storage for aggregation queries.** The scorer's GROUP BY aggregations are the only slow path. A Timescale hypertable or a read replica with columnar extension cuts scan cost by 5–10×.
- **Archive snapshots older than 2 years.** The dashboard needs only the latest snapshot per post and 30/60-day trend windows. Older rows can go to S3 as Parquet.

---

## Platforms, and why

**YouTube** — full programmatic access via the Data API v3 and YouTube Analytics API. Per-video view counts, likes, comments, watch time, and subscriber history. OAuth refresh token means it runs unattended. Pagination handled natively.

**LinkedIn via Ordinal** — LinkedIn offers no public API for organic post metrics. The options were: manual CSV export (one-shot, no time-series), scraping (terms of service violation), or a licensed data partner. Ordinal is a licensed analytics platform that provides per-post impressions, likes, comments, shares, saves, and DMs sent via a clean REST API. Saves and DMs sent are the two most valuable signals — saves indicate high intent to return; DMs indicate the content was worth sharing to a specific person. Neither is available in a native export.

**X (Twitter)** — pulled via the same Ordinal connection at no additional cost. Currently showing zero posts due to limited posting activity, but the connector is live.

The decision not to split into platform-specific tables was deliberate. A unified `posts` table with a `platform` column keeps cross-platform comparison possible and means adding a new platform is a new connector file, not a new table and a new set of analytics queries.

---

## How analysis and recommendations work, and how the loop gets sharper

**Tagging** happens once, on first ingest. The LLM classifies each post across four dimensions: topic cluster, format, hook type, and length bucket. The `tagged_at` guard (`UPDATE ... WHERE tagged_at IS NULL`) makes this safe to call repeatedly — already-tagged posts are never touched.

**Scoring** is pure SQL, no LLM. Every cycle, `attribute_scores` is fully recomputed: for each attribute type, group posts by their attribute value, take the latest snapshot per post, and average engagement rates. A trend delta compares the recent 30-day window to the prior 30–60-day window — a positive delta means that attribute is rising, regardless of its absolute level. Confidence bands (low / medium / high) prevent a single viral post from dominating a "long-form" score.

**Recommendations** are generated by the LLM once per cycle, with the top attribute scores and trend deltas passed as structured context. Each recommendation must cite a specific number from the scores — vague reasoning is rejected by the prompt structure. Every output passes through `antislop.py` before being stored, which catches and rewrites filler language ("revolutionary", "game-changing", "in today's world") that would erode trust in the dashboard.

**The loop gets sharper in two ways.** First, every new pull adds a snapshot — the trend delta becomes more meaningful as the time series lengthens, and confidence bands shift from low to medium to high as post counts accumulate. Second, the hypothesis layer surfaces rising attributes automatically: when a high-confidence attribute shows a positive trend delta and no active hypothesis covers it, the LLM is asked to generate a falsifiable belief about it. When confidence drops, the hypothesis is deactivated. Over time, the system builds a tested belief library about what formats and topics perform on each platform for each client.

---

## How ROI is framed, and why a client would trust it

There is no conversion data available from YouTube or LinkedIn without deeper instrumentation (UTM tracking, CRM integration). Rather than fabricate a number, the system defines explicit proxy chains that move from reach to intent.

**YouTube:** Views → Engagements (likes + comments) → Subscribers. Each step measures commitment: a view is passive, an engagement is active response, a subscription is a stated intent to return. Conversion rates between steps are shown explicitly alongside the raw numbers so the client can see where the funnel narrows.

**LinkedIn:** Impressions → Engagements → Saves → DMs sent. Saves and DMs sent are the strongest intent signals available without CRM data. A save means the reader bookmarked the post to return to it. A DM means they sent it to a specific person — the closest available proxy to "this content drove a conversation."

The ROI view is honest about what it is: a proxy chain, not attributed revenue. The case for client trust is that the proxies are real platform signals (not estimates), the reasoning is explicit (not a black box), and the trend data shows whether those signals are improving over time. A client can watch saves per impression rise quarter over quarter and make a reasonable inference about pipeline warming even without a closed-loop attribution system.

---

## Rough cost at scale, and where it breaks first

**Current cost: near zero.** LLM calls go through `claude -p` using subscription auth. No API fees, no per-token billing. Supabase free tier handles the current data volume. The scheduler runs on a local machine via launchd.

**A note on the LLM integration.** The `claude -p` subprocess approach was chosen for this demo because it uses subscription auth and costs nothing to run. It is not the production path. All LLM calls are isolated behind `shared/llm.py` — the rest of the codebase never calls the model directly and has no knowledge of how it is invoked. Switching to the Anthropic API means changing one function in that file: replace the `subprocess.run` call with an `anthropic.Anthropic().messages.create()` call, set the model constant, and add `ANTHROPIC_API_KEY` to the environment. Nothing else in the system changes. At production scale, the API path is strictly better: it is async-capable, supports streaming, has proper rate-limit headers, and removes the dependency on a locally installed CLI.

**At 10 clients:**
- Supabase Pro tier (~$25/month) for connection pooling and more storage.
- The scheduler needs to run in the cloud — a small VM or a managed cron service (~$10–20/month). The launchd approach only works on a machine that is always on.
- LLM cost via API: tagging runs once per post (never re-tagged), so the ongoing cost per cycle is five recommendation calls plus any new hypothesis generation — roughly 10,000–15,000 tokens per client per week. At current Claude pricing that is well under $1 per client per month.
- **Ordinal (LinkedIn + X data):** Pro plan at $265/month includes 4 social profiles. Each additional profile costs $20/month. For a Catalyst deployment covering 10 clients each with LinkedIn and X, that is 20 profiles — $265 + (16 × $20) = **$585/month** for Ordinal alone. This is the dominant cost line at scale and should be factored into client pricing. One Ordinal account can serve all clients from a single API key; profiles are the billing unit, not seats.

**Where it breaks first:**
1. **The scheduler is single-threaded.** Ten clients pulling simultaneously means ten sequential runs. At ~5 minutes per client, that is nearly an hour. The fix is to run connectors concurrently (asyncio or a thread pool per client).
2. **The LLM subprocess blocks.** `claude -p` is synchronous. At scale, move to async API calls with proper rate-limit handling.
3. **`attribute_scores` full recompute.** Fine at current scale. At tens of millions of snapshots, the GROUP BY aggregations become slow. The fix is incremental maintenance rather than full recompute each cycle.
4. **No authentication on the dashboard.** The current Streamlit deployment is public. Multi-client use requires row-level access control — either Streamlit's built-in auth or deploying behind a gateway that scopes the `client_id` filter to the logged-in user.

---

## How AI was used to build this

This system was built using Claude Code with a `CLAUDE.md` file that defined the architecture, constraints, and conventions before a single line was written. That file is in the repo root.

**What Claude Code did:** scaffolded the module structure, wrote the connectors, migrations, scheduler, dashboard, and tests, and iterated on each as real errors surfaced during live runs against the APIs.

**What required human judgment throughout:**

- *Catching a broken CLI flag.* Claude initially called `claude -p` with `--output-format json`. That flag is not supported in `-p` mode — it silently returns empty stdout, causing the tagger to fail on every batch. The fix was to remove the flag and parse JSON from plain text output using `_strip_fences()`. Claude generated the broken call; human review of the actual terminal output caught it.

- *The Ordinal field mapping.* The API documentation listed `emv` as the earned media value field. The live API returned `earnedMediaValue`. Claude wrote the connector against the docs; the field came back null in production. The fix (`item.get("emv") or item.get("earnedMediaValue")`) came from reading the raw API response, not from Claude.

- *LinkedIn data access.* Claude initially proposed scraping LinkedIn. That was rejected immediately — terms of service violation, brittle, and wrong for a production system. The decision to use Ordinal as a licensed data partner was a human call after evaluating the actual options.

- *Schema decisions.* Claude proposed platform-specific tables (`youtube_posts`, `linkedin_posts`). That was overruled in favour of a unified `posts` table with a `platform` column, for the reasons documented in the design decisions section. The `client_id` column for multi-tenancy was a human addition once the single-client architecture was working.

- *Taxonomy mismatch.* The initial topic clusters were GTM-focused (`AI search`, `GTM strategy`, `demand gen`). The YouTube content is personal development and mindset coaching — a completely different domain. Claude used what it was given; catching the mismatch and extending the taxonomy required looking at the actual content.

**Why prompts are embedded in the code, not stored in a separate folder:**

A `prompts/` directory would create two sources of truth. The prompt sent to the LLM at runtime is constructed dynamically — it includes live data (attribute scores, top posts, active hypotheses) formatted into the prompt string at call time. Storing a static version separately would be immediately stale and would obscure the logic that shapes the actual input.

Keeping prompts inline in `analytics/tagger.py`, `analytics/recommender.py`, and `shared/antislop.py` means the prompt, the data it receives, and the parsing of its output are all readable together. A future engineer can see exactly what the model is asked, what context it gets, and what happens to its response — without jumping between files. The prompt is part of the code, not documentation of it.
