import os

import pandas as pd
import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Catalyst Content Analytics", layout="wide")


@st.cache_resource
def get_engine():
    from sqlalchemy import create_engine
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return create_engine(url)


def query(sql: str, params=None) -> pd.DataFrame:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return pd.DataFrame()


try:
    conn = get_engine()
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("Filters")

# Client filter — populated from distinct client_ids in posts
_client_rows = query("SELECT DISTINCT client_id FROM posts ORDER BY client_id")
_client_options = ["All"] + (list(_client_rows["client_id"]) if not _client_rows.empty else [])
client_label = st.sidebar.selectbox("Client", _client_options)
client = None if client_label == "All" else client_label

platform_label = st.sidebar.selectbox("Platform", ["All", "YouTube", "LinkedIn"])
plat = {"All": None, "YouTube": "youtube", "LinkedIn": "linkedin"}[platform_label]


def _filters(alias: str = "p") -> str:
    """Return AND clauses for active client + platform filters."""
    clauses = []
    if client:
        clauses.append(f"{alias}.client_id = %(client)s")
    if plat:
        clauses.append(f"{alias}.platform = %(plat)s")
    return (" AND " + " AND ".join(clauses)) if clauses else ""


def _params(extra: dict | None = None) -> dict | None:
    params = {}
    if client:
        params["client"] = client
    if plat:
        params["plat"] = plat
    if extra:
        params.update(extra)
    return params or None


st.title("Catalyst Content Analytics")
active_filters = [f for f in [client_label if client else None,
                               platform_label if plat else None] if f]
if active_filters:
    st.caption("Showing: " + " · ".join(f"**{f}**" for f in active_filters))

tab1, tab2, tab3 = st.tabs(["Performance", "Recommendations", "ROI"])


# ── TAB 1: Performance ──────────────────────────────────────────────────────
with tab1:
    st.header("Top Posts by Engagement Rate")
    top_posts = query(f"""
        SELECT
            p.platform,
            LEFT(p.title, 60)            AS title,
            p.url,
            m.engagement_rate,
            m.views,
            m.pulled_at
        FROM posts p
        JOIN metrics_snapshots m ON m.post_id = p.id
        WHERE m.pulled_at = (
            SELECT MAX(pulled_at)
            FROM metrics_snapshots
            WHERE post_id = p.id
        )
        {_filters()}
        ORDER BY m.engagement_rate DESC NULLS LAST
        LIMIT 5
    """, params=_params())
    if not top_posts.empty:
        top_posts["engagement_rate"] = top_posts["engagement_rate"].apply(
            lambda x: f"{float(x)*100:.2f}%" if x is not None else "—"
        )
        top_posts["views"] = top_posts["views"].apply(
            lambda x: f"{int(x):,}" if x is not None else "—"
        )
        top_posts["pulled_at"] = pd.to_datetime(top_posts["pulled_at"]).dt.strftime("%Y-%m-%d")
        top_posts.columns = ["Platform", "Title", "URL", "Engagement Rate", "Views", "Last Pulled"]
        st.dataframe(top_posts, use_container_width=True, hide_index=True)
    else:
        st.info("No post data yet. Run the scheduler to pull metrics.")

    st.header("What's Working: Attribute Scores")
    attr_scores = query(
        """
        SELECT attribute_type, attribute_value, avg_engagement_rate, confidence, post_count
        FROM attribute_scores
        WHERE (%(client)s IS NULL OR client_id = %(client)s)
          AND (%(plat)s IS NULL OR platform = %(plat)s)
        ORDER BY avg_engagement_rate DESC NULLS LAST
        """,
        params={"client": client, "plat": plat},
    )

    _CONFIDENCE_ICON = {"low": "🔴", "medium": "🟡", "high": "🟢"}

    if not attr_scores.empty:
        attr_types = ["topic_cluster", "format", "hook_type", "length_bucket"]
        cols = st.columns(4)
        for col, attr in zip(cols, attr_types):
            with col:
                st.subheader(attr.replace("_", " ").title())
                subset = attr_scores[attr_scores["attribute_type"] == attr].head(3)
                if subset.empty:
                    st.caption("No data yet.")
                else:
                    chart_data = subset.set_index("attribute_value")[["avg_engagement_rate"]]
                    chart_data.columns = ["Avg Engagement Rate"]
                    st.bar_chart(chart_data, height=180)
                    for _, row in subset.iterrows():
                        icon = _CONFIDENCE_ICON.get(row["confidence"], "⚪")
                        rate = f"{float(row['avg_engagement_rate'])*100:.2f}%" if row["avg_engagement_rate"] else "—"
                        st.caption(f"{icon} **{row['attribute_value']}** — {rate} ({row['post_count']} posts)")
    else:
        st.info("No attribute scores yet. Run the scheduler first.")

    st.header("Engagement Over Time (Top 3 Posts)")
    top3 = query(f"""
        SELECT p.id, p.title
        FROM posts p
        JOIN metrics_snapshots ms ON ms.post_id = p.id
        WHERE ms.engagement_rate IS NOT NULL
        {_filters()}
        GROUP BY p.id, p.title
        ORDER BY AVG(ms.engagement_rate) DESC NULLS LAST
        LIMIT 3
    """, params=_params())

    if not top3.empty:
        snapshots_data = {}
        for _, row in top3.iterrows():
            label = (row["title"] or str(row["id"]))[:40]
            df = query(
                """
                SELECT pulled_at, engagement_rate
                FROM metrics_snapshots
                WHERE post_id = %(post_id)s
                ORDER BY pulled_at
                """,
                params=_params({"post_id": str(row["id"])}),
            )
            if not df.empty:
                df["pulled_at"] = pd.to_datetime(df["pulled_at"])
                df = df.set_index("pulled_at")
                snapshots_data[label] = df["engagement_rate"].astype(float)

        if snapshots_data:
            chart_df = pd.DataFrame(snapshots_data)
            st.line_chart(chart_df)
        else:
            st.info("Not enough snapshot history yet for a trend chart.")
    else:
        st.info("No engagement data yet.")


# ── TAB 2: Recommendations ──────────────────────────────────────────────────
with tab2:
    st.header("Content Recommendations")

    recs = query("""
        SELECT id, generated_at, reasoning, attributes_used, scores_snapshot
        FROM recommendations
        ORDER BY generated_at DESC
        LIMIT 5
    """)

    if recs.empty:
        st.info("No recommendations yet. Run the scheduler to generate them.")
    else:
        import json as _json
        for i, (_, rec) in enumerate(recs.iterrows(), 1):
            attrs = rec["attributes_used"]
            if isinstance(attrs, str):
                try:
                    attrs = _json.loads(attrs)
                except Exception:
                    attrs = []

            meta = {a.get("attribute_type"): a.get("attribute_value") for a in attrs} if isinstance(attrs, list) else {}
            heading = f"Recommendation {i}"

            st.subheader(heading)
            st.markdown(rec["reasoning"] or "—")
            st.caption(
                f"Format: {meta.get('format', '—')} | "
                f"Topic: {meta.get('topic_cluster', '—')} | "
                f"Hook: {meta.get('hook_type', '—')} | "
                f"Generated: {pd.to_datetime(rec['generated_at']).strftime('%Y-%m-%d') if rec['generated_at'] else '—'}"
            )
            with st.expander("Scores that drove this"):
                if isinstance(attrs, list) and attrs:
                    attr_df = pd.DataFrame(attrs)
                    st.dataframe(attr_df, use_container_width=True, hide_index=True)
                else:
                    st.caption("No attribute data attached.")
            st.divider()

    st.info(
        "Recommendations update each time new performance data arrives. "
        "Confidence scores rise as more posts accumulate in each attribute category. "
        "Rising trend signals surface before they reach high confidence."
    )


# ── TAB 3: ROI ──────────────────────────────────────────────────────────────
with tab3:

    if plat == "linkedin" or (plat is None):
        # ── LinkedIn ROI proxy chain ─────────────────────────────────────────
        if plat == "linkedin" or plat is None:
            header = "LinkedIn ROI Proxy Chain" if plat == "linkedin" else "LinkedIn ROI Proxy Chain"
            st.header(header)
            st.caption("Impressions → Engagements → Saves → DMs sent. Each step is a stronger buying signal than the last.")

        li_funnel = query("""
            SELECT
                COALESCE(SUM(ms.views), 0)                           AS impressions,
                COALESCE(SUM(ms.engagements), 0)                     AS engagements,
                COALESCE(SUM((ms.raw->>'saveCount')::int), 0)        AS saves,
                COALESCE(SUM((ms.raw->>'sendCount')::int), 0)        AS dms_sent
            FROM metrics_snapshots ms
            JOIN posts p ON p.id = ms.post_id
            WHERE p.platform = 'linkedin'
              AND ms.pulled_at = (SELECT MAX(pulled_at) FROM metrics_snapshots WHERE post_id = p.id)
        """)
        if not li_funnel.empty:
            r = li_funnel.iloc[0]
            impressions = int(r["impressions"] or 0)
            engagements = int(r["engagements"] or 0)
            saves       = int(r["saves"] or 0)
            dms         = int(r["dms_sent"] or 0)

            fc1, fc2, fc3, fc4 = st.columns(4)
            fc1.metric("Impressions", f"{impressions:,}", help="Total reach across all LinkedIn posts")
            fc2.metric("Engagements", f"{engagements:,}",
                       f"{engagements/impressions*100:.1f}% of impressions" if impressions else None,
                       help="Likes + comments + reposts")
            fc3.metric("Saves", f"{saves:,}",
                       f"{saves/impressions*100:.2f}% of impressions" if impressions else None,
                       help="Reader bookmarked the post — high intent signal.")
            fc4.metric("DMs sent", f"{dms:,}",
                       f"{dms/impressions*100:.2f}% of impressions" if impressions else None,
                       help="Post shared via DM — strongest intent signal available.")

            st.caption("**Why saves and DMs matter:** A like takes one tap. A save means the reader wants to return. A DM means they sent it to someone specific — both are stronger purchase-intent signals than engagement rate alone.")

        # Top LinkedIn posts
        st.header("Top LinkedIn Posts by Engagement Rate")
        li_top = query("""
            SELECT
                LEFT(p.title, 80)                          AS post,
                TO_CHAR(p.published_at, 'Mon YYYY')        AS published,
                ms.views                                   AS impressions,
                ms.engagements,
                ROUND(ms.engagement_rate * 100, 2)         AS engagement_pct,
                (ms.raw->>'saveCount')::int                AS saves,
                (ms.raw->>'sendCount')::int                AS dms,
                p.topic_cluster,
                p.format,
                p.hook_type
            FROM metrics_snapshots ms
            JOIN posts p ON p.id = ms.post_id
            WHERE p.platform = 'linkedin'
              AND ms.pulled_at = (SELECT MAX(pulled_at) FROM metrics_snapshots WHERE post_id = p.id)
              AND ms.engagement_rate IS NOT NULL
            ORDER BY ms.engagement_rate DESC NULLS LAST
            LIMIT 10
        """)
        if not li_top.empty:
            li_top.columns = ["Post", "Published", "Impressions", "Engagements",
                              "Engagement %", "Saves", "DMs", "Topic", "Format", "Hook"]
            st.dataframe(li_top, use_container_width=True, hide_index=True)
        else:
            st.info("No LinkedIn engagement data yet.")

        # LinkedIn engagement trend
        st.header("LinkedIn Engagement Rate Over Time")
        st.caption("Each point is a weekly pull snapshot.")
        li_trend = query("""
            SELECT
                DATE_TRUNC('week', ms.pulled_at)   AS week,
                AVG(ms.engagement_rate) * 100      AS avg_engagement_pct
            FROM metrics_snapshots ms
            JOIN posts p ON p.id = ms.post_id
            WHERE p.platform = 'linkedin'
              AND ms.engagement_rate IS NOT NULL
            GROUP BY week
            ORDER BY week
        """)
        if not li_trend.empty and len(li_trend) > 1:
            li_trend["week"] = pd.to_datetime(li_trend["week"])
            st.line_chart(li_trend.set_index("week")["avg_engagement_pct"].astype(float))
        else:
            st.info("Trend data builds up as weekly snapshots accumulate.")

    if plat != "linkedin":
        # ── YouTube audience funnel ──────────────────────────────────────────
        st.header("YouTube Audience Funnel")
        st.caption("Views → Engagements → Subscribers. Each step measures how many people moved from passive reach to active response to committed audience.")

        channel = query("""
            SELECT subscribers, total_views, pulled_at
            FROM channel_snapshots
            WHERE platform = 'youtube'
            ORDER BY pulled_at DESC
            LIMIT 1
        """)
        totals_30 = query("""
            SELECT
                COALESCE(SUM(ms.views), 0)       AS total_views,
                COALESCE(SUM(ms.engagements), 0) AS total_engagements
            FROM metrics_snapshots ms
            JOIN posts p ON p.id = ms.post_id
            WHERE ms.pulled_at > NOW() - INTERVAL '30 days'
              AND p.platform = 'youtube'
        """)

        if not channel.empty and not totals_30.empty:
            subs = int(channel.iloc[0]["subscribers"] or 0)
            views_30 = int(totals_30.iloc[0]["total_views"] or 0)
            eng_30 = int(totals_30.iloc[0]["total_engagements"] or 0)
            eng_rate = f"{(eng_30/views_30*100):.1f}%" if views_30 > 0 else "—"
            sub_rate = f"{(subs/int(channel.iloc[0]['total_views'])*100):.2f}%" if channel.iloc[0]["total_views"] else "—"

            c1, c2, c3 = st.columns(3)
            c1.metric("Views (last 30 days)", f"{views_30:,}", help="Total YouTube video views in the last 30 days")
            c2.metric("Engagements (last 30 days)", f"{eng_30:,}", f"{eng_rate} of views", help="Likes + comments")
            c3.metric("Total Subscribers", f"{subs:,}", help="Current subscriber count — the committed audience")

            st.markdown("**Funnel conversion rates**")
            funnel_df = pd.DataFrame([
                {"Stage": "Views → Engagements (last 30d)", "Rate": eng_rate, "Interpretation": "% of viewers who liked or commented"},
                {"Stage": "All-time views → Subscribers", "Rate": sub_rate, "Interpretation": "% of viewers who subscribed"},
            ])
            st.dataframe(funnel_df, use_container_width=True, hide_index=True)
        else:
            st.info("Run the scheduler once to populate channel data.")

        st.header("Subscriber Growth Over Time")
        sub_history = query("""
            SELECT pulled_at, subscribers
            FROM channel_snapshots
            WHERE platform = 'youtube' AND subscribers IS NOT NULL
            ORDER BY pulled_at
        """)
        if not sub_history.empty and len(sub_history) > 1:
            sub_history["pulled_at"] = pd.to_datetime(sub_history["pulled_at"])
            sub_history = sub_history.set_index("pulled_at")
            st.line_chart(sub_history["subscribers"].astype(float))
        else:
            st.info("Subscriber history builds up over time as the scheduler runs every Monday at 08:00.")

        st.header("Top 10 Videos by Engagement Rate")
        st.caption("High engagement rate = high audience response per view. These are the formats and topics worth repeating.")
        top_roi = query("""
            SELECT
                LEFT(p.title, 65)        AS title,
                ms.views,
                ms.engagements,
                ROUND(ms.engagement_rate * 100, 2) AS engagement_pct,
                p.topic_cluster,
                p.format,
                p.hook_type
            FROM metrics_snapshots ms
            JOIN posts p ON p.id = ms.post_id
            WHERE ms.pulled_at = (SELECT MAX(pulled_at) FROM metrics_snapshots WHERE post_id = p.id)
              AND p.platform = 'youtube'
              AND ms.engagement_rate IS NOT NULL
            ORDER BY ms.engagement_rate DESC NULLS LAST
            LIMIT 10
        """)
        if not top_roi.empty:
            top_roi.columns = ["Title", "Views", "Engagements", "Engagement %", "Topic", "Format", "Hook"]
            st.dataframe(top_roi, use_container_width=True, hide_index=True)
        else:
            st.info("No engagement data yet.")

    # ── Weekly reach (always shown, filtered by platform) ────────────────────
    st.header("Weekly Views (Last 90 Days)")
    weekly = query(f"""
        SELECT
            DATE_TRUNC('week', ms.pulled_at) AS week,
            p.platform,
            SUM(ms.views) AS total_views
        FROM metrics_snapshots ms
        JOIN posts p ON p.id = ms.post_id
        WHERE ms.pulled_at > NOW() - INTERVAL '90 days'
          AND ms.views IS NOT NULL
        {_filters()}
        GROUP BY week, p.platform
        ORDER BY week
    """, params=_params())
    if not weekly.empty:
        pivot = weekly.pivot(index="week", columns="platform", values="total_views").fillna(0)
        st.line_chart(pivot)
    else:
        st.info("No weekly reach data yet.")

    st.header("Pull History")
    pull_log = query("""
        SELECT
            started_at,
            EXTRACT(EPOCH FROM (finished_at - started_at))::int AS duration_secs,
            posts_added,
            snapshots_added,
            errors
        FROM pull_log
        ORDER BY started_at DESC
        LIMIT 10
    """)
    if not pull_log.empty:
        pull_log["started_at"] = pd.to_datetime(pull_log["started_at"]).dt.strftime("%Y-%m-%d %H:%M")
        pull_log["duration_secs"] = pull_log["duration_secs"].apply(
            lambda x: f"{int(x)}s" if x is not None and not pd.isna(x) else "—"
        )
        pull_log["errors"] = pull_log["errors"].apply(
            lambda x: len(x) if isinstance(x, list) else (0 if x in (None, "null", "[]") else 1)
        )
        pull_log.columns = ["Started", "Duration", "Posts Added", "Snapshots Added", "Errors"]
        st.dataframe(pull_log, use_container_width=True, hide_index=True)
    else:
        st.info("No pull history yet.")
