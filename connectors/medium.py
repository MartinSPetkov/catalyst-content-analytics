import json
import os
import re
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

from db import get_connection
from shared.fetch import fetch_page


def pull(db_conn=None) -> dict:
    username = os.environ.get("MEDIUM_USERNAME")
    if not username:
        print("[Medium] Skipping: MEDIUM_USERNAME not set")
        return {"posts_added": 0, "snapshots_added": 0}

    conn = db_conn or get_connection()
    posts_added = 0
    snapshots_added = 0

    try:
        feed_url = f"https://medium.com/feed/@{username}"
        print(f"[Medium] Fetching RSS feed from {feed_url}...")
        feed = feedparser.parse(feed_url)
        entries = feed.get("entries", [])
        print(f"[Medium] {len(entries)} posts found in feed.")

        cur = conn.cursor()

        for entry in entries:
            try:
                title = entry.get("title")
                url = entry.get("link")
                if not url:
                    continue

                published_at = _parse_date(entry)
                platform_post_id = url

                raw_content = _get_content(entry)
                word_count = _count_words(raw_content)

                claps, responses = _scrape_engagement(url)

                raw = {"claps": claps, "responses": responses}

                cur.execute(
                    """
                    INSERT INTO posts (platform, platform_post_id, published_at, title, url, word_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (platform, platform_post_id) DO NOTHING
                    """,
                    ("medium", platform_post_id, published_at, title, url, word_count),
                )
                if cur.rowcount:
                    posts_added += 1

                cur.execute(
                    "SELECT id FROM posts WHERE platform = %s AND platform_post_id = %s",
                    ("medium", platform_post_id),
                )
                post_row = cur.fetchone()
                if not post_row:
                    continue
                post_id = post_row[0]

                cur.execute(
                    """
                    INSERT INTO metrics_snapshots
                        (post_id, pulled_at, views, engagements, clicks, engagement_rate, raw)
                    VALUES (%s, NOW(), NULL, %s, NULL, NULL, %s)
                    """,
                    (post_id, claps, json.dumps(raw)),
                )
                snapshots_added += 1

            except Exception as e:
                print(f"[Medium] Error processing entry {entry.get('link', '?')}: {e}")

        conn.commit()
        cur.close()

    except Exception as e:
        print(f"[Medium] Fatal error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    print(f"[Medium] {len(entries)} posts fetched, {snapshots_added} snapshots added")
    return {"posts_added": posts_added, "snapshots_added": snapshots_added}


def _parse_date(entry) -> datetime | None:
    published = entry.get("published_parsed")
    if published:
        try:
            return datetime(*published[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _get_content(entry) -> str:
    for key in ("content", "summary"):
        val = entry.get(key)
        if val:
            if isinstance(val, list):
                val = val[0].get("value", "")
            return val
    return ""


def _count_words(html: str) -> int | None:
    if not html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return len(text.split())


def _scrape_engagement(url: str) -> tuple[int | None, int | None]:
    claps = None
    responses = None
    try:
        html = fetch_page(url)
        if not html:
            return claps, responses
        soup = BeautifulSoup(html, "html.parser")

        # clap count: aria-label like "123 claps"
        for el in soup.find_all(attrs={"aria-label": re.compile(r"\d+\s+clap", re.I)}):
            m = re.search(r"(\d[\d,]*)", el["aria-label"])
            if m:
                claps = int(m.group(1).replace(",", ""))
                break

        # response count: text containing "response"
        for el in soup.find_all(string=re.compile(r"\d+\s+response", re.I)):
            m = re.search(r"(\d+)", el)
            if m:
                responses = int(m.group(1))
                break

    except Exception as e:
        print(f"[Medium] Engagement scrape failed for {url}: {e}")

    return claps, responses
