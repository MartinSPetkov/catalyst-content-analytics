import json
import os
from datetime import datetime, timezone

import pandas as pd

from db import get_connection

_DEFAULT_CSV = "samples/linkedin_export.csv"

_COL_DATE = "Date"
_COL_URL = "Post URL"
_COL_TITLE = "Post title or first 150 characters"
_COL_IMPRESSIONS = "Impressions"
_COL_CLICKS = "Clicks"
_COL_REACTIONS = "Reactions"
_COL_COMMENTS = "Comments"
_COL_REPOSTS = "Reposts"
_COL_ENGAGEMENT = "Engagement rate"


def pull(db_conn=None) -> dict:
    if os.environ.get("UNIPILE_API_KEY"):
        return _unipile_pull(db_conn)

    path = os.environ.get("LINKEDIN_CSV_PATH", _DEFAULT_CSV)
    if not os.path.exists(path):
        print(f"[LinkedIn] Skipping: CSV file not found at {path}")
        return {"posts_added": 0, "snapshots_added": 0}

    conn = db_conn or get_connection()
    posts_added = 0
    snapshots_added = 0
    existing = 0

    try:
        print(f"[LinkedIn] Reading CSV from {path}...")
        df = pd.read_csv(path)
        print(f"[LinkedIn] {len(df)} rows found.")

        cur = conn.cursor()

        for _, row in df.iterrows():
            try:
                url = _str_or_none(row, _COL_URL)
                if not url:
                    continue

                platform_post_id = url
                published_at = _parse_date(row)
                title = _str_or_none(row, _COL_TITLE)

                impressions = _int_or_none(row, _COL_IMPRESSIONS)
                clicks = _int_or_none(row, _COL_CLICKS)
                reactions = _int_or_none(row, _COL_REACTIONS) or 0
                comments = _int_or_none(row, _COL_COMMENTS) or 0
                reposts = _int_or_none(row, _COL_REPOSTS) or 0
                engagements = reactions + comments + reposts if any([reactions, comments, reposts]) else None
                engagement_rate = _parse_pct(row, _COL_ENGAGEMENT)

                raw = {
                    "impressions": impressions,
                    "clicks": clicks,
                    "reactions": reactions,
                    "comments": comments,
                    "reposts": reposts,
                }

                cur.execute(
                    """
                    INSERT INTO posts (platform, platform_post_id, published_at, title, url)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (platform, platform_post_id) DO NOTHING
                    """,
                    ("linkedin", platform_post_id, published_at, title, url),
                )
                if cur.rowcount:
                    posts_added += 1
                else:
                    existing += 1

                cur.execute(
                    "SELECT id FROM posts WHERE platform = %s AND platform_post_id = %s",
                    ("linkedin", platform_post_id),
                )
                post_row = cur.fetchone()
                if not post_row:
                    continue
                post_id = post_row[0]

                cur.execute(
                    """
                    INSERT INTO metrics_snapshots
                        (post_id, pulled_at, views, engagements, clicks, engagement_rate, raw)
                    VALUES (%s, NOW(), %s, %s, %s, %s, %s)
                    """,
                    (post_id, impressions, engagements, clicks, engagement_rate, json.dumps(raw)),
                )
                snapshots_added += 1

            except Exception as e:
                print(f"[LinkedIn] Error processing row {row.get(_COL_URL, '?')}: {e}")

        conn.commit()
        cur.close()

    except Exception as e:
        print(f"[LinkedIn] Fatal error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    total = posts_added + existing
    print(f"[LinkedIn] {total} rows ingested, {posts_added} new posts, {existing} existing")
    return {"posts_added": posts_added, "snapshots_added": snapshots_added}


def _unipile_pull(db_conn=None) -> dict:
    raise NotImplementedError(
        "Unipile live pull not yet implemented. "
        "Set UNIPILE_API_KEY and implement this function to enable live LinkedIn pulls."
    )


def _str_or_none(row: pd.Series, col: str) -> str | None:
    if col not in row.index:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    return str(val).strip() or None


def _int_or_none(row: pd.Series, col: str) -> int | None:
    if col not in row.index:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_date(row: pd.Series) -> datetime | None:
    val = _str_or_none(row, _COL_DATE)
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    print(f"[LinkedIn] Could not parse date: {val}")
    return None


def _parse_pct(row: pd.Series, col: str) -> float | None:
    val = _str_or_none(row, col)
    if not val:
        return None
    try:
        return round(float(val.replace("%", "").strip()) / 100, 6)
    except (ValueError, AttributeError):
        return None
