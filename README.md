# Catalyst Content Analytics

A self-sustaining content analytics system that pulls performance data from YouTube and LinkedIn, learns what is working, generates recommendations, and surfaces everything in a Streamlit dashboard. Runs on a weekly schedule with no manual trigger.

## What it does

1. **Pulls** posts and metrics from YouTube (Data API v3 + Analytics API) and LinkedIn (via Ordinal).
2. **Stores** everything in a time-series Postgres schema: raw posts, per-pull metric snapshots, LLM-generated tags, computed attribute scores, hypotheses, and recommendations.
3. **Tags** each post once on first ingest (topic cluster, format, hook type, length bucket) using Claude via `claude -p`.
4. **Scores** every attribute combination in pure SQL — no LLM, fully deterministic and cheap to recompute each cycle.
5. **Recommends** five specific content ideas per cycle, grounded in the attribute scores, with reasoning that cites real numbers.
6. **Runs itself** via a launchd daemon (macOS) every Monday at 08:00 — no cron management needed.
7. **Surfaces results** in a Streamlit dashboard with client and platform filtering, a LinkedIn ROI proxy chain (Impressions → Saves → DMs), and a YouTube subscriber funnel. Multiple clients can share one database — each client's data is scoped by `CLIENT_ID`.

## Architecture

```
connectors/   — one file per platform; pull raw posts + metrics
analytics/    — tagger (LLM), scorer (SQL/pandas), recommender (LLM)
shared/       — llm.py, fetch.py, antislop.py, report.py
scheduler.py  — APScheduler orchestrator: pull → tag → score → recommend
dashboard.py  — Streamlit, read-only, no LLM calls
migrations/   — Alembic migrations, one file per schema change
samples/      — fixture data for local dev
tests/        — unit and integration tests
```

## LLM access

All LLM calls go through `shared/llm.py`, which shells out to `claude -p`. This uses Claude subscription auth — no API key and no per-token billing. Setting `ANTHROPIC_API_KEY` in the environment will raise a `RuntimeError` at startup.

## Prerequisites

- Python 3.11+
- PostgreSQL (local) or a Supabase project
- [Claude CLI](https://claude.ai/download) installed and authenticated (`claude -p "hello"` should work)
- YouTube Data API credentials (OAuth 2.0)
- [Ordinal](https://tryordinal.com) account with LinkedIn connected (for LinkedIn data)

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd catalyst-content-analytics
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy the example and fill in your values:

```bash
cp .env.example .env
```

`.env` variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string, e.g. `postgresql://user:pass@host:5432/dbname` |
| `CLIENT_ID` | Unique slug for this client account, e.g. `acme-corp`. Scopes all data in a shared database. |
| `YOUTUBE_CLIENT_ID` | OAuth 2.0 client ID from Google Cloud Console |
| `YOUTUBE_CLIENT_SECRET` | OAuth 2.0 client secret |
| `YOUTUBE_REFRESH_TOKEN` | Long-lived refresh token (see below) |
| `ORDINAL_API_KEY` | API key from tryordinal.com → Settings → API |

**Never commit `.env`.** It is in `.gitignore`.

### 3. Get YouTube credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials.
2. Create an OAuth 2.0 Client ID (Desktop app type).
3. Enable the **YouTube Data API v3** and **YouTube Analytics API** for your project.
4. Run the auth helper to get a refresh token:

```bash
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/yt-analytics.readonly',
])
creds = flow.run_local_server(port=0)
print('REFRESH TOKEN:', creds.refresh_token)
"
```

### 4. Run database migrations

```bash
alembic upgrade head
```

This creates all tables, indexes, and the `channel_snapshots` table in one step. Each migration is idempotent and has a downgrade path.

### 5. Run a pull cycle

```bash
python scheduler.py --once
```

This runs the full pipeline: pull → tag → score → recommend. Expect 3–8 minutes on first run (tagging all posts in batches of 5 with 2-second pauses between batches).

### 6. Launch the dashboard

```bash
streamlit run dashboard.py
```

Open `http://localhost:8501`. The sidebar has two filters: **Client** (populated live from the database — a new client appears automatically after their first pull) and **Platform** (All / YouTube / LinkedIn). Both filters apply to every view including attribute scores and ROI.

## Scheduled runs

The scheduler is installed as a macOS launchd daemon and fires automatically every Monday at 08:00.

To install or reload it:

```bash
cp com.catalyst.scheduler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.catalyst.scheduler.plist
```

Logs go to `outputs/scheduler.log` and `outputs/scheduler.error.log`.

To run manually at any time:

```bash
python scheduler.py --once
```

## Database design

### Tables

| Table | Purpose |
|---|---|
| `posts` | One row per piece of content. Keyed by `(client_id, platform, platform_post_id)`. Attributes (topic, format, hook) set once on first ingest. |
| `metrics_snapshots` | Time-series. One row per post per pull cycle. Views, engagements, clicks, engagement rate, raw JSONB. |
| `attribute_scores` | Recomputed each cycle from SQL. Keyed by `(client_id, platform, attribute_type, attribute_value)`. Stores avg engagement rate, trend delta (recent vs prior 30-day window), confidence. |
| `hypotheses` | LLM-generated falsifiable beliefs. Auto-activated when a high-confidence rising attribute is detected; auto-deactivated when confidence drops. |
| `recommendations` | Five content ideas per cycle with reasoning grounded in attribute scores. Full scores snapshot stored for audit. |
| `pull_log` | One row per scheduler run. Tracks duration, posts added, snapshots added, errors. |
| `channel_snapshots` | Subscriber and total-view history for channel-level metrics (YouTube). |

### Indexes

All hot query paths are indexed:

```sql
idx_metrics_post_time            ON metrics_snapshots (post_id, pulled_at DESC)
idx_metrics_pulled_at            ON metrics_snapshots (pulled_at)
idx_posts_attributes             ON posts (topic_cluster, format, hook_type)
idx_posts_client_platform        ON posts (client_id, platform, published_at DESC)
idx_attribute_scores_client_platform ON attribute_scores (client_id, platform, attribute_type)
idx_channel_snapshots_platform_time  ON channel_snapshots (platform, pulled_at DESC)
```

### At 50 million rows

`metrics_snapshots` is the table that grows unboundedly — one row per post per weekly pull. At 500 posts × 52 weeks × ~20 years that is still under 600k rows, but if the system scales to many clients and platforms:

- **Partition by `pulled_at`** (range partitioning by month or quarter). Postgres partition pruning means queries with a `WHERE pulled_at > ...` clause only scan recent partitions.
- **Columnar store for analytics queries** (Timescale hypertables or a read replica with pg_analytics). The scorer's `GROUP BY` aggregations are the only slow path — moving them to a columnar store reduces scan cost by 5–10×.
- **Materialised views** for `attribute_scores`. Currently recomputed from raw snapshots each cycle. At scale, maintain incrementally rather than full recompute.
- **Archive old snapshots** to cold storage (S3 via `pg_dump` or Parquet) after 2 years. The dashboard only needs the latest snapshot per post plus trend windows of 30 and 60 days.

## Multi-client usage

The database is designed to hold data for multiple clients simultaneously. Each client gets a unique `CLIENT_ID` slug (e.g. `acme-corp`, `martin-petkov`). All posts, attribute scores, and analytics are scoped to that ID.

To onboard a second client:
1. Give them their own `.env` with a unique `CLIENT_ID` and their own platform credentials.
2. Run `python scheduler.py --once` from their environment — their posts land in the shared database under their `client_id`.
3. Their name appears automatically in the dashboard Client dropdown after the first pull.

There is no schema change required. The dashboard Client filter isolates their data completely.

## Key design decisions

**Tag once, never re-tag.** `tagged_at IS NOT NULL` is the permanent guard. The `UPDATE ... WHERE tagged_at IS NULL` in the tagger makes this safe even if the scheduler fires twice concurrently.

**Score in SQL, not LLM.** Attribute scores are fully deterministic and recomputed from raw data every cycle. This means scores always reflect the latest snapshot and there is no stale cache to invalidate.

**LLM via `claude -p`, not the SDK.** Zero API cost, no key management, no per-token billing risk. The tradeoff is that the process blocks on subprocess output, which is fine for a weekly batch job.

**Antislop on all stored prose.** Every recommendation and hypothesis passes through `shared/antislop.py` before being written to the database. This catches and rewrites LLM filler language before it reaches the dashboard.

**Degrade gracefully.** Every connector returns `{"posts_added": 0, "snapshots_added": 0}` when credentials are missing. The scheduler continues to the next platform and logs the skip.

## Tests

```bash
pytest tests/ -v
```

Tests cover the scorer (deterministic SQL logic via an in-memory SQLite fixture), antislop (pattern detection and clean pass-through), the `_strip_fences` JSON parser in llm.py, and the tagger's `tagged_at` guard.

## Config and secrets

- `.env` holds all credentials. Never committed (in `.gitignore`).
- `.env.example` shows all required variables with placeholder values.
- `ANTHROPIC_API_KEY` must **not** be set. `shared/llm.py` raises `RuntimeError` at import if it is.
- The Streamlit deployment uses environment secrets (set in the Streamlit Cloud dashboard), not a committed file.
