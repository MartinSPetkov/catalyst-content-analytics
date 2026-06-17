# CLAUDE.md

## What this repo is
A standalone content analytics tracker that pulls content performance from YouTube, Medium, and LinkedIn, stores it in a time-series Postgres database, analyzes what is working, generates content recommendations, and surfaces everything in a deployed Streamlit dashboard.

## Stack
- Language: Python 3.11+
- Database: Postgres (Supabase in production, local Postgres for dev)
- Migrations: Alembic
- Dashboard: Streamlit
- Scheduler: APScheduler (runs locally, writes to Supabase)
- LLM: Claude via `claude -p` subprocess (subscription auth, zero API cost)

## LLM access
All LLM calls go through `shared/llm.py`. Nothing calls Claude directly.
`shared/llm.py` shells out to `claude -p "<prompt>"` and reads stdout.
Do NOT use the anthropic SDK. Do NOT set ANTHROPIC_API_KEY anywhere in this repo.
If ANTHROPIC_API_KEY is present in the environment at runtime, llm.py must raise
a RuntimeError immediately with a clear message.
The model string lives as a single constant in `shared/llm.py`. Never hardcode it elsewhere.

## Architecture
```
connectors/   — one file per platform, pulls raw posts + metrics
analytics/    — tagger (LLM), scorer (SQL/pandas), recommender (LLM)
shared/       — llm.py, fetch.py, antislop.py, report.py
scheduler.py  — APScheduler orchestrator: pull → tag → score → recommend
dashboard.py  — Streamlit, read-only, connects to Supabase
migrations/   — Alembic, one file per schema change
samples/      — sample CSVs and fixture data for local dev
outputs/      — generated HTML reports (not committed)
docs/         — project documentation
```

## Database tables
- `posts` — one row per piece of content; attributes set on first ingest only
- `metrics_snapshots` — time-series; one row per post per pull cycle
- `attribute_scores` — recomputed each pull from SQL; no LLM involved
- `hypotheses` — LLM-generated beliefs, confidence-scored
- `recommendations` — every generated recommendation, timestamped with reasoning
- `pull_log` — one row per scheduler run

## Key conventions
- A post is tagged once on first ingest. `tagged_at IS NOT NULL` = never touch it again.
- Attribute scores are computed in pure SQL/pandas. No LLM.
- LLM is called only for: initial post tagging, hypothesis generation, recommendations.
- All human-facing prose passes through `shared/antislop.py` before being stored.
- Connectors degrade gracefully when credentials are missing. Log and return, never crash.
- LinkedIn runs CSV-ingest mode by default. Unipile is a stub behind `UNIPILE_API_KEY`.
- The dashboard makes no LLM calls. It is read-only.

## What never to do
- Never set ANTHROPIC_API_KEY in this repo.
- Never re-tag a post that already has `tagged_at` set.
- Never call Claude from `dashboard.py`.
- Never scrape LinkedIn.
- Never fabricate a metric. Missing fields are NULL, not guesses.
- Never block on a missing optional key. Degrade gracefully and log it.
