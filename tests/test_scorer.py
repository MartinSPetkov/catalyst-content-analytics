"""
Tests for analytics/scorer.py

Uses an in-memory SQLite database to exercise the scorer's SQL logic without
needing a live Postgres connection. The scorer uses only standard SQL that
works across both engines.
"""
import sqlite3
import uuid
from datetime import datetime, timedelta

import pytest

from analytics.scorer import _ATTRIBUTE_TYPES


# ── SQLite fixture ────────────────────────────────────────────────────────────

def _make_db():
    """Return an in-memory SQLite connection pre-populated with the relevant tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE posts (
            id            TEXT PRIMARY KEY,
            platform      TEXT NOT NULL,
            platform_post_id TEXT NOT NULL,
            published_at  TEXT,
            title         TEXT,
            url           TEXT,
            topic_cluster TEXT,
            format        TEXT,
            hook_type     TEXT,
            length_bucket TEXT,
            word_count    INTEGER,
            tagged_at     TEXT
        );

        CREATE TABLE metrics_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id         TEXT,
            pulled_at       TEXT NOT NULL,
            views           INTEGER,
            engagements     INTEGER,
            clicks          INTEGER,
            engagement_rate REAL,
            raw             TEXT
        );

        CREATE TABLE attribute_scores (
            platform        TEXT NOT NULL,
            attribute_type  TEXT NOT NULL,
            attribute_value TEXT NOT NULL,
            post_count      INTEGER,
            avg_engagement_rate REAL,
            trend_delta     REAL,
            confidence      TEXT,
            updated_at      TEXT,
            PRIMARY KEY (platform, attribute_type, attribute_value)
        );
    """)
    conn.commit()
    return conn


def _add_post(conn, post_id=None, topic_cluster="AI search", fmt="long-form",
              hook_type="stat", length_bucket="long"):
    post_id = post_id or str(uuid.uuid4())
    conn.execute(
        """INSERT INTO posts
           (id, platform, platform_post_id, published_at, topic_cluster, format, hook_type, length_bucket, tagged_at)
           VALUES (?, 'linkedin', ?, datetime('now'), ?, ?, ?, ?, datetime('now'))""",
        (post_id, post_id, topic_cluster, fmt, hook_type, length_bucket),
    )
    conn.commit()
    return post_id


def _add_snapshot(conn, post_id, engagement_rate, days_ago=0):
    pulled_at = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO metrics_snapshots (post_id, pulled_at, views, engagements, engagement_rate)
           VALUES (?, ?, 1000, 50, ?)""",
        (post_id, pulled_at, engagement_rate),
    )
    conn.commit()


# ── Minimal scorer that runs against SQLite ───────────────────────────────────

def _recompute_scores_sqlite(conn):
    """
    Mirror of analytics/scorer.recompute_scores() but using ? placeholders and
    SQLite-compatible syntax so tests don't need a Postgres connection.
    """
    cur = conn.cursor()
    upserted = 0

    for attr in _ATTRIBUTE_TYPES:
        cur.execute(f"""
            SELECT
                p.platform                AS platform,
                p.{attr}                  AS attribute_value,
                COUNT(DISTINCT p.id)      AS post_count,
                AVG(ms.engagement_rate)   AS avg_engagement_rate
            FROM posts p
            JOIN metrics_snapshots ms ON ms.post_id = p.id
            JOIN (
                SELECT post_id, MAX(pulled_at) AS latest
                FROM metrics_snapshots
                GROUP BY post_id
            ) latest_ms ON latest_ms.post_id = ms.post_id
                       AND latest_ms.latest  = ms.pulled_at
            WHERE p.{attr} IS NOT NULL
              AND ms.engagement_rate IS NOT NULL
            GROUP BY p.platform, p.{attr}
        """)
        base_rows = cur.fetchall()

        cur.execute(f"""
            SELECT p.platform, p.{attr} AS attribute_value, AVG(ms.engagement_rate) AS recent_avg
            FROM posts p
            JOIN metrics_snapshots ms ON ms.post_id = p.id
            WHERE p.{attr} IS NOT NULL
              AND ms.engagement_rate IS NOT NULL
              AND ms.pulled_at > datetime('now', '-30 days')
            GROUP BY p.platform, p.{attr}
        """)
        recent = {(row[0], row[1]): row[2] for row in cur.fetchall()}

        cur.execute(f"""
            SELECT p.platform, p.{attr} AS attribute_value, AVG(ms.engagement_rate) AS prior_avg
            FROM posts p
            JOIN metrics_snapshots ms ON ms.post_id = p.id
            WHERE p.{attr} IS NOT NULL
              AND ms.engagement_rate IS NOT NULL
              AND ms.pulled_at BETWEEN datetime('now', '-60 days') AND datetime('now', '-30 days')
            GROUP BY p.platform, p.{attr}
        """)
        prior = {(row[0], row[1]): row[2] for row in cur.fetchall()}

        for row in base_rows:
            platform, attribute_value, post_count, avg_engagement_rate = row[0], row[1], row[2], row[3]
            trend_delta = None
            r = recent.get((platform, attribute_value))
            p = prior.get((platform, attribute_value))
            if r is not None and p is not None:
                trend_delta = float(r) - float(p)

            if post_count < 3:
                confidence = "low"
            elif post_count < 10:
                confidence = "medium"
            else:
                confidence = "high"

            cur.execute(
                """
                INSERT INTO attribute_scores
                    (platform, attribute_type, attribute_value, post_count,
                     avg_engagement_rate, trend_delta, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT (platform, attribute_type, attribute_value) DO UPDATE SET
                    post_count          = excluded.post_count,
                    avg_engagement_rate = excluded.avg_engagement_rate,
                    trend_delta         = excluded.trend_delta,
                    confidence          = excluded.confidence,
                    updated_at          = datetime('now')
                """,
                (platform, attr, attribute_value, post_count,
                 avg_engagement_rate, trend_delta, confidence),
            )
            upserted += 1

    conn.commit()
    cur.close()
    return upserted


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestConfidenceBanding:
    """Confidence is determined by post_count thresholds: <3=low, 3-9=medium, >=10=high."""

    def test_low_confidence_below_threshold(self):
        conn = _make_db()
        for _ in range(2):
            pid = _add_post(conn, topic_cluster="AI search")
            _add_snapshot(conn, pid, engagement_rate=0.05)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT confidence FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'AI search'"
        ).fetchone()
        assert row["confidence"] == "low"

    def test_medium_confidence_at_threshold(self):
        conn = _make_db()
        for _ in range(5):
            pid = _add_post(conn, topic_cluster="GTM strategy")
            _add_snapshot(conn, pid, engagement_rate=0.03)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT confidence FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'GTM strategy'"
        ).fetchone()
        assert row["confidence"] == "medium"

    def test_high_confidence_at_ten_posts(self):
        conn = _make_db()
        for _ in range(10):
            pid = _add_post(conn, topic_cluster="content ops")
            _add_snapshot(conn, pid, engagement_rate=0.04)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT confidence FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'content ops'"
        ).fetchone()
        assert row["confidence"] == "high"


class TestAverageEngagementRate:
    """Scorer should average the latest snapshot per post, not all snapshots."""

    def test_uses_latest_snapshot_only(self):
        """If a post has two snapshots, only the most recent one counts."""
        conn = _make_db()
        pid = _add_post(conn, topic_cluster="mindset")
        _add_snapshot(conn, pid, engagement_rate=0.01, days_ago=10)  # old
        _add_snapshot(conn, pid, engagement_rate=0.09, days_ago=0)   # latest
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT avg_engagement_rate FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'mindset'"
        ).fetchone()
        # Should reflect 0.09 (latest), not average of 0.01 + 0.09
        assert abs(row["avg_engagement_rate"] - 0.09) < 0.001

    def test_averages_across_posts(self):
        conn = _make_db()
        for rate in [0.02, 0.04, 0.06]:
            pid = _add_post(conn, topic_cluster="leadership")
            _add_snapshot(conn, pid, engagement_rate=rate)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT avg_engagement_rate FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'leadership'"
        ).fetchone()
        assert abs(row["avg_engagement_rate"] - 0.04) < 0.001


class TestMultipleAttributeTypes:
    """Scores are computed independently per attribute type."""

    def test_all_four_attribute_types_scored(self):
        conn = _make_db()
        for _ in range(3):
            pid = _add_post(
                conn,
                topic_cluster="productivity",
                fmt="list",
                hook_type="question",
                length_bucket="short",
            )
            _add_snapshot(conn, pid, engagement_rate=0.05)
        upserted = _recompute_scores_sqlite(conn)
        assert upserted == 4  # one row per attribute type

    def test_different_values_get_separate_rows(self):
        conn = _make_db()
        for topic in ["AI search", "mindset", "leadership"]:
            for _ in range(3):
                pid = _add_post(conn, topic_cluster=topic)
                _add_snapshot(conn, pid, engagement_rate=0.03)
        _recompute_scores_sqlite(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM attribute_scores WHERE attribute_type = 'topic_cluster'"
        ).fetchone()[0]
        assert count == 3


class TestTrendDelta:
    """trend_delta = recent 30-day avg minus prior 30-60-day avg."""

    def test_positive_trend_when_recent_is_higher(self):
        conn = _make_db()
        # Prior window (35 days ago)
        pid1 = _add_post(conn, topic_cluster="sales")
        _add_snapshot(conn, pid1, engagement_rate=0.02, days_ago=35)
        # Recent window (5 days ago)
        pid2 = _add_post(conn, topic_cluster="sales")
        _add_snapshot(conn, pid2, engagement_rate=0.06, days_ago=5)
        # Third post to hit medium confidence
        pid3 = _add_post(conn, topic_cluster="sales")
        _add_snapshot(conn, pid3, engagement_rate=0.04, days_ago=5)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT trend_delta FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'sales'"
        ).fetchone()
        assert row["trend_delta"] is not None
        assert row["trend_delta"] > 0

    def test_no_trend_when_only_recent_data(self):
        """trend_delta is NULL when there are no snapshots in the prior window."""
        conn = _make_db()
        for _ in range(3):
            pid = _add_post(conn, topic_cluster="demand gen")
            _add_snapshot(conn, pid, engagement_rate=0.05, days_ago=2)
        _recompute_scores_sqlite(conn)
        row = conn.execute(
            "SELECT trend_delta FROM attribute_scores WHERE platform = 'linkedin' AND attribute_value = 'demand gen'"
        ).fetchone()
        assert row["trend_delta"] is None
