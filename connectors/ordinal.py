import json
import os
from datetime import date, timedelta

import requests

from db import get_connection

_BASE_URL = "https://app.tryordinal.com/api/v1"
_LOOKBACK_DAYS = 365 * 3  # 3 years — captures full Ordinal history

_PLATFORM_MAP = {
    "LinkedIn": "linkedin",
    "Twitter": "x",
    "Instagram": "instagram",
}


def _resolve_client_id(profile_name: str, default: str) -> str:
    """
    Map an Ordinal profile display name to a client_id slug.

    Set ORDINAL_PROFILE_CLIENTS in .env as a JSON object:
        ORDINAL_PROFILE_CLIENTS={"Martin Petkov": "martin-petkov", "Will Leatherman": "will-leatherman"}

    Profiles not listed fall back to the default CLIENT_ID.
    """
    raw = os.environ.get("ORDINAL_PROFILE_CLIENTS", "")
    if not raw:
        return default
    try:
        mapping = json.loads(raw)
        return mapping.get(profile_name, default)
    except json.JSONDecodeError:
        print(f"[Ordinal] Warning: ORDINAL_PROFILE_CLIENTS is not valid JSON, ignoring.")
        return default


def pull(db_conn=None) -> dict:
    api_key = os.environ.get("ORDINAL_API_KEY")
    account_id = os.environ.get("CLIENT_ID", "default")
    if not api_key:
        print("[Ordinal] Skipping: ORDINAL_API_KEY not set")
        return {"posts_added": 0, "snapshots_added": 0}

    conn = db_conn or get_connection()
    session = _make_session(api_key)
    total_posts_added = 0
    total_snapshots_added = 0

    try:
        print("[Ordinal] Fetching connected profiles...")
        profiles = _get_profiles(session)
        if not profiles:
            print("[Ordinal] No profiles found.")
            return {"posts_added": 0, "snapshots_added": 0}

        print(f"[Ordinal] Found {len(profiles)} profile(s): "
              f"{', '.join(p['channel'] + ':' + p['name'] for p in profiles)}")

        start_date = (date.today() - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        end_date = date.today().isoformat()

        cur = conn.cursor()

        for profile in profiles:
            channel = profile.get("channel", "")
            platform = _PLATFORM_MAP.get(channel)
            if not platform:
                print(f"[Ordinal] Skipping unsupported channel: {channel}")
                continue

            profile_id = profile["id"]
            profile_name = profile.get("name", profile_id)
            client_id = _resolve_client_id(profile_name, account_id)
            print(f"[Ordinal] Pulling {channel} posts for {profile_name} (client: {client_id})...")

            posts, snapshots = _pull_platform(
                session, cur, client_id, platform, profile_id, start_date, end_date
            )
            total_posts_added += posts
            total_snapshots_added += snapshots
            print(f"[Ordinal] {channel}: {posts} new posts, {snapshots} snapshots added")

        conn.commit()
        cur.close()

    except Exception as e:
        print(f"[Ordinal] Fatal error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    print(f"[Ordinal] Done. {total_posts_added} new posts, {total_snapshots_added} snapshots total")
    return {"posts_added": total_posts_added, "snapshots_added": total_snapshots_added}


def _pull_platform(session, cur, account_id: str, platform: str, profile_id: str,
                   start_date: str, end_date: str) -> tuple[int, int]:
    posts_added = 0
    snapshots_added = 0

    if platform == "linkedin":
        endpoint = f"{_BASE_URL}/analytics/linkedin/{profile_id}/posts"
    elif platform == "x":
        endpoint = f"{_BASE_URL}/analytics/x/{profile_id}/posts"
    else:
        print(f"[Ordinal] No analytics endpoint implemented for {platform}")
        return 0, 0

    try:
        resp = session.get(endpoint, params={"startDate": start_date, "endDate": end_date})
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            items = items.get("data", [])
    except Exception as e:
        print(f"[Ordinal] Failed to fetch {platform} analytics: {e}")
        return 0, 0

    for item in items:
        try:
            platform_post_id = item.get("id") or item.get("url")
            if not platform_post_id:
                continue

            url = item.get("url")
            # LinkedIn uses "commentary", X uses "text"
            title = (item.get("commentary") or item.get("text") or "")[:500] or None
            published_at = item.get("publishedAt")
            post_type = item.get("type")

            views = _int_or_none(item.get("impressionCount"))
            likes = _int_or_none(item.get("likeCount"))
            # LinkedIn uses commentCount, X uses replyCount
            comments = _int_or_none(item.get("commentCount") or item.get("replyCount"))
            # LinkedIn uses shareCount, X uses retweetCount
            shares = _int_or_none(item.get("shareCount") or item.get("retweetCount"))
            clicks = _int_or_none(item.get("clickCount"))
            engagements = _sum_or_none(likes, comments, shares)
            engagement_rate = _float_or_none(item.get("engagement"))

            raw = {
                "type": post_type,
                "likeCount": likes,
                "commentCount": comments,
                "shareCount": shares,
                "clickCount": clicks,
                "saveCount": _int_or_none(item.get("saveCount")),
                "sendCount": _int_or_none(item.get("sendCount")),
                "bookmarkCount": _int_or_none(item.get("bookmarkCount")),
                "quoteCount": _int_or_none(item.get("quoteCount")),
                "impressionCount": views,
                "emv": item.get("emv") or item.get("earnedMediaValue"),
                "ordinalPost": item.get("ordinalPost"),
            }

            cur.execute(
                """
                INSERT INTO posts (client_id, platform, platform_post_id, published_at, title, url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (platform, platform_post_id) DO NOTHING
                """,
                (account_id, platform, str(platform_post_id), published_at, title, url),
            )
            if cur.rowcount:
                posts_added += 1

            cur.execute(
                "SELECT id FROM posts WHERE platform = %s AND platform_post_id = %s",
                (platform, str(platform_post_id)),
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
                (post_id, views, engagements, clicks, engagement_rate, json.dumps(raw)),
            )
            snapshots_added += 1

        except Exception as e:
            print(f"[Ordinal] Error processing post {item.get('id', '?')}: {e}")

    return posts_added, snapshots_added


def _get_profiles(session) -> list[dict]:
    try:
        resp = session.get(f"{_BASE_URL}/profiles/scheduling")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("data", data.get("profiles", []))
    except Exception as e:
        print(f"[Ordinal] Could not fetch profiles: {e}")
        return []


def _make_session(api_key: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    return s


def _int_or_none(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float_or_none(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _sum_or_none(*vals) -> int | None:
    filtered = [v for v in vals if v is not None]
    return sum(filtered) if filtered else None
