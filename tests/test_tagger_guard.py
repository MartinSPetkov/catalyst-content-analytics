"""
Tests for the tagger's tag-once guard.

The critical invariant: a post with tagged_at IS NOT NULL must never be
re-tagged, even if the tagger is called multiple times. This is enforced at
the SQL level with WHERE tagged_at IS NULL in the UPDATE statement.

These tests use an in-memory SQLite database so they run without Postgres
or a live LLM.
"""
import sqlite3
import uuid
from unittest.mock import patch

import pytest

from analytics.tagger import _BATCH_SIZE, _TOPIC_CLUSTERS, _FORMATS, _HOOK_TYPES


# ── SQLite fixture ────────────────────────────────────────────────────────────

def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE posts (
            id                TEXT PRIMARY KEY,
            platform          TEXT NOT NULL,
            platform_post_id  TEXT NOT NULL,
            published_at      TEXT,
            title             TEXT,
            url               TEXT,
            topic_cluster     TEXT,
            format            TEXT,
            hook_type         TEXT,
            length_bucket     TEXT,
            word_count        INTEGER,
            tagged_at         TEXT
        );
    """)
    conn.commit()
    return conn


def _add_post(conn, tagged=False, topic_cluster=None):
    pid = str(uuid.uuid4())
    tagged_at = "2024-01-01 00:00:00" if tagged else None
    conn.execute(
        """INSERT INTO posts
           (id, platform, platform_post_id, published_at, title, tagged_at, topic_cluster)
           VALUES (?, 'linkedin', ?, datetime('now'), 'Test post', ?, ?)""",
        (pid, pid, tagged_at, topic_cluster),
    )
    conn.commit()
    return pid


# ── SQLite-compatible tagger ──────────────────────────────────────────────────

def _tag_untagged_sqlite(db_conn, llm_response: list) -> int:
    """
    Minimal re-implementation of tag_untagged_posts using SQLite syntax.
    Accepts a pre-baked llm_response list instead of calling the LLM.
    Returns the number of posts tagged.
    """
    cur = db_conn.cursor()
    cur.execute(
        "SELECT id, title, url, platform, word_count FROM posts WHERE tagged_at IS NULL"
    )
    rows = cur.fetchall()

    if not rows:
        cur.close()
        return 0

    tagged_count = 0
    for item in llm_response:
        post_id = item.get("post_id")
        if not post_id:
            continue
        cur.execute(
            """
            UPDATE posts
            SET topic_cluster = ?,
                format        = ?,
                hook_type     = ?,
                length_bucket = ?,
                tagged_at     = datetime('now')
            WHERE id = ?
              AND tagged_at IS NULL
            """,
            (
                item.get("topic_cluster"),
                item.get("format"),
                item.get("hook_type"),
                item.get("length_bucket"),
                str(post_id),
            ),
        )
        if cur.rowcount:
            tagged_count += 1

    db_conn.commit()
    cur.close()
    return tagged_count


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTagOnceGuard:
    """tagged_at IS NOT NULL = never touch it again."""

    def test_untagged_post_gets_tagged(self):
        conn = _make_db()
        pid = _add_post(conn, tagged=False)
        response = [{"post_id": pid, "topic_cluster": "mindset",
                     "format": "long-form", "hook_type": "stat", "length_bucket": "long"}]
        count = _tag_untagged_sqlite(conn, response)
        assert count == 1
        row = conn.execute("SELECT topic_cluster, tagged_at FROM posts WHERE id = ?", (pid,)).fetchone()
        assert row["topic_cluster"] == "mindset"
        assert row["tagged_at"] is not None

    def test_already_tagged_post_is_skipped(self):
        conn = _make_db()
        pid = _add_post(conn, tagged=True, topic_cluster="original-value")
        response = [{"post_id": pid, "topic_cluster": "should-not-change",
                     "format": "list", "hook_type": "question", "length_bucket": "short"}]
        count = _tag_untagged_sqlite(conn, response)
        assert count == 0
        row = conn.execute("SELECT topic_cluster FROM posts WHERE id = ?", (pid,)).fetchone()
        assert row["topic_cluster"] == "original-value"

    def test_calling_tagger_twice_does_not_overwrite(self):
        conn = _make_db()
        pid = _add_post(conn, tagged=False)
        first_response = [{"post_id": pid, "topic_cluster": "AI search",
                           "format": "long-form", "hook_type": "stat", "length_bucket": "long"}]
        second_response = [{"post_id": pid, "topic_cluster": "mindset",
                            "format": "list", "hook_type": "question", "length_bucket": "short"}]

        _tag_untagged_sqlite(conn, first_response)
        _tag_untagged_sqlite(conn, second_response)  # should be a no-op

        row = conn.execute("SELECT topic_cluster FROM posts WHERE id = ?", (pid,)).fetchone()
        assert row["topic_cluster"] == "AI search"

    def test_mixed_batch_only_tags_untagged(self):
        conn = _make_db()
        pid_tagged = _add_post(conn, tagged=True, topic_cluster="original")
        pid_untagged = _add_post(conn, tagged=False)

        response = [
            {"post_id": pid_tagged, "topic_cluster": "should-not-change",
             "format": "list", "hook_type": "question", "length_bucket": "short"},
            {"post_id": pid_untagged, "topic_cluster": "leadership",
             "format": "how-to", "hook_type": "bold-claim", "length_bucket": "medium"},
        ]
        count = _tag_untagged_sqlite(conn, response)
        assert count == 1

        tagged_row = conn.execute(
            "SELECT topic_cluster FROM posts WHERE id = ?", (pid_tagged,)
        ).fetchone()
        untagged_row = conn.execute(
            "SELECT topic_cluster FROM posts WHERE id = ?", (pid_untagged,)
        ).fetchone()

        assert tagged_row["topic_cluster"] == "original"
        assert untagged_row["topic_cluster"] == "leadership"


class TestAllowedValues:
    """Verify the allowed taxonomy lists are non-empty and contain expected values."""

    def test_topic_clusters_include_linkedin_relevant_topics(self):
        assert "content ops" in _TOPIC_CLUSTERS
        assert "GTM strategy" in _TOPIC_CLUSTERS
        assert "personal development" in _TOPIC_CLUSTERS

    def test_formats_include_core_linkedin_formats(self):
        assert "list" in _FORMATS
        assert "opinion" in _FORMATS
        assert "how-to" in _FORMATS

    def test_hook_types_cover_main_patterns(self):
        assert "stat" in _HOOK_TYPES
        assert "question" in _HOOK_TYPES
        assert "contrarian" in _HOOK_TYPES

    def test_batch_size_is_sensible(self):
        """Batch size should be small enough to fit in a single LLM call but not 1."""
        assert 3 <= _BATCH_SIZE <= 10
