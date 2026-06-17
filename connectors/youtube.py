import json
import os
from datetime import date, timedelta

from db import get_connection

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def pull(db_conn=None) -> dict:
    yt_client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    account_id = os.environ.get("CLIENT_ID", "default")

    if not all([yt_client_id, client_secret, refresh_token]):
        print("[YouTube] Skipping: YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN not set")
        return {"posts_added": 0, "snapshots_added": 0}

    conn = db_conn or get_connection()
    posts_added = 0
    snapshots_added = 0

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        print("[YouTube] Authenticating...")
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=yt_client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )

        yt = build("youtube", "v3", credentials=creds)
        yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

        print("[YouTube] Fetching video list...")
        video_ids = []
        page_token = None
        while True:
            kwargs = dict(forMine=True, type="video", maxResults=50, part="id")
            if page_token:
                kwargs["pageToken"] = page_token
            resp = yt.search().list(**kwargs).execute()
            for item in resp.get("items", []):
                video_ids.append(item["id"]["videoId"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        print(f"[YouTube] Found {len(video_ids)} videos. Fetching stats...")

        date_end = date.today().isoformat()
        date_start = (date.today() - timedelta(days=365)).isoformat()

        cur = conn.cursor()

        for vid_id in video_ids:
            try:
                stats_resp = yt.videos().list(
                    part="snippet,statistics", id=vid_id
                ).execute()
                items = stats_resp.get("items", [])
                if not items:
                    continue
                item = items[0]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})

                title = snippet.get("title")
                url = f"https://www.youtube.com/watch?v={vid_id}"
                published_at = snippet.get("publishedAt")

                view_count = _int_or_none(stats.get("viewCount"))
                like_count = _int_or_none(stats.get("likeCount"))
                comment_count = _int_or_none(stats.get("commentCount"))

                engagement_rate = None
                if view_count and view_count > 0 and like_count is not None and comment_count is not None:
                    engagement_rate = round((like_count + comment_count) / view_count, 4)

                analytics_data = {}
                try:
                    ana_resp = yt_analytics.reports().query(
                        ids="channel==MINE",
                        startDate=date_start,
                        endDate=date_end,
                        metrics="estimatedMinutesWatched,averageViewDuration",
                        filters=f"video=={vid_id}",
                        dimensions="video",
                    ).execute()
                    rows = ana_resp.get("rows", [])
                    if rows:
                        analytics_data = {
                            "estimatedMinutesWatched": rows[0][1],
                            "averageViewDuration": rows[0][2],
                        }
                except Exception as e:
                    print(f"[YouTube] Analytics fetch failed for {vid_id}: {e}")

                raw = {
                    "statistics": stats,
                    "analytics": analytics_data,
                }

                cur.execute(
                    """
                    INSERT INTO posts (client_id, platform, platform_post_id, published_at, title, url)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (platform, platform_post_id) DO NOTHING
                    """,
                    (account_id, "youtube", vid_id, published_at, title, url),
                )
                if cur.rowcount:
                    posts_added += 1

                cur.execute("SELECT id FROM posts WHERE platform = %s AND platform_post_id = %s",
                            ("youtube", vid_id))
                post_row = cur.fetchone()
                if not post_row:
                    continue
                post_id = post_row[0]

                cur.execute(
                    """
                    INSERT INTO metrics_snapshots
                        (post_id, pulled_at, views, engagements, clicks, engagement_rate, raw)
                    VALUES (%s, NOW(), %s, %s, NULL, %s, %s)
                    """,
                    (post_id, view_count,
                     (like_count or 0) + (comment_count or 0),
                     engagement_rate,
                     json.dumps(raw)),
                )
                snapshots_added += 1

            except Exception as e:
                print(f"[YouTube] Error processing video {vid_id}: {e}")

        # Channel-level snapshot for ROI funnel
        try:
            ch_resp = yt.channels().list(part="statistics", mine=True).execute()
            ch_items = ch_resp.get("items", [])
            if ch_items:
                ch_stats = ch_items[0].get("statistics", {})
                cur.execute(
                    """
                    INSERT INTO channel_snapshots
                        (platform, pulled_at, subscribers, total_views, video_count, raw)
                    VALUES ('youtube', NOW(), %s, %s, %s, %s)
                    """,
                    (
                        _int_or_none(ch_stats.get("subscriberCount")),
                        _int_or_none(ch_stats.get("viewCount")),
                        _int_or_none(ch_stats.get("videoCount")),
                        json.dumps(ch_stats),
                    ),
                )
                print(f"[YouTube] Channel snapshot: {ch_stats.get('subscriberCount')} subscribers, "
                      f"{ch_stats.get('viewCount')} total views")
        except Exception as e:
            print(f"[YouTube] Channel stats fetch failed: {e}")

        conn.commit()
        cur.close()

    except Exception as e:
        print(f"[YouTube] Fatal error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    print(f"[YouTube] {len(video_ids)} videos fetched, {snapshots_added} snapshots added")
    return {"posts_added": posts_added, "snapshots_added": snapshots_added}


def _int_or_none(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
