import pandas as pd


_ATTRIBUTE_TYPES = ["topic_cluster", "format", "hook_type", "length_bucket"]


def recompute_scores(db_conn) -> None:
    cur = db_conn.cursor()
    upserted = 0

    for attr in _ATTRIBUTE_TYPES:
        # Latest snapshot per post, grouped by platform + attribute value
        cur.execute(f"""
            SELECT
                p.client_id               AS client_id,
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
            GROUP BY p.client_id, p.platform, p.{attr}
        """)
        base_rows = cur.fetchall()

        # Trend: recent window (last 30 days), per client + platform
        cur.execute(f"""
            SELECT p.client_id, p.platform, p.{attr} AS attribute_value,
                   AVG(ms.engagement_rate) AS recent_avg
            FROM posts p
            JOIN metrics_snapshots ms ON ms.post_id = p.id
            WHERE p.{attr} IS NOT NULL
              AND ms.engagement_rate IS NOT NULL
              AND ms.pulled_at > NOW() - INTERVAL '30 days'
            GROUP BY p.client_id, p.platform, p.{attr}
        """)
        recent = {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}

        # Trend: prior window (30-60 days ago), per client + platform
        cur.execute(f"""
            SELECT p.client_id, p.platform, p.{attr} AS attribute_value,
                   AVG(ms.engagement_rate) AS prior_avg
            FROM posts p
            JOIN metrics_snapshots ms ON ms.post_id = p.id
            WHERE p.{attr} IS NOT NULL
              AND ms.engagement_rate IS NOT NULL
              AND ms.pulled_at BETWEEN NOW() - INTERVAL '60 days' AND NOW() - INTERVAL '30 days'
            GROUP BY p.client_id, p.platform, p.{attr}
        """)
        prior = {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}

        for client_id, platform, attribute_value, post_count, avg_engagement_rate in base_rows:
            trend_delta = None
            r = recent.get((client_id, platform, attribute_value))
            p = prior.get((client_id, platform, attribute_value))
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
                    (client_id, platform, attribute_type, attribute_value, post_count,
                     avg_engagement_rate, trend_delta, confidence, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (client_id, platform, attribute_type, attribute_value) DO UPDATE SET
                    post_count          = EXCLUDED.post_count,
                    avg_engagement_rate = EXCLUDED.avg_engagement_rate,
                    trend_delta         = EXCLUDED.trend_delta,
                    confidence          = EXCLUDED.confidence,
                    updated_at          = NOW()
                """,
                (client_id, platform, attr, attribute_value, post_count,
                 avg_engagement_rate, trend_delta, confidence),
            )
            upserted += 1

        # Remove stale rows for this attribute type (values no longer in posts)
        cur.execute(f"""
            DELETE FROM attribute_scores
            WHERE attribute_type = %s
              AND (client_id, platform, attribute_value) NOT IN (
                  SELECT DISTINCT client_id, platform, {attr}
                  FROM posts
                  WHERE {attr} IS NOT NULL
              )
        """, (attr,))

    db_conn.commit()
    cur.close()
    print(f"[Scorer] Recomputed {upserted} attribute scores across {len(_ATTRIBUTE_TYPES)} attribute types.")
