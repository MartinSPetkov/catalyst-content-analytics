import time

from shared import llm

_TOPIC_CLUSTERS = [
    "AI search", "GTM strategy", "content ops", "demand gen", "sales", "product marketing",
    "personal development", "mindset", "leadership", "productivity", "other"
]
_FORMATS = ["long-form", "list", "case-study", "opinion", "how-to", "interview", "other"]
_HOOK_TYPES = ["question", "bold-claim", "stat", "story", "contrarian", "other"]
_BATCH_SIZE = 5


def tag_untagged_posts(db_conn) -> None:
    cur = db_conn.cursor()

    cur.execute(
        "SELECT id, title, url, platform, word_count FROM posts WHERE tagged_at IS NULL"
    )
    rows = cur.fetchall()

    if not rows:
        print("[Tagger] No untagged posts. Done.")
        cur.close()
        return

    print(f"[Tagger] {len(rows)} untagged posts found. Processing in batches of {_BATCH_SIZE}...")
    tagged_count = 0

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i: i + _BATCH_SIZE]

        post_lines = []
        for post_id, title, url, platform, word_count in batch:
            post_lines.append(
                f'- post_id: "{post_id}", title: "{title or ""}", '
                f'url: "{url or ""}", platform: "{platform}", word_count: {word_count}'
            )

        prompt = f"""Classify each of these content posts and return a JSON array.

Posts:
{chr(10).join(post_lines)}

Return a JSON array where each item has exactly these keys:
  post_id, topic_cluster, format, hook_type, length_bucket

Allowed values:
  topic_cluster: {_TOPIC_CLUSTERS}
  format: {_FORMATS}
  hook_type: {_HOOK_TYPES}
  length_bucket: "short" (word_count < 500), "medium" (500-2000), "long" (>2000).
    If word_count is null, infer from the title.

Return only the JSON array. No commentary."""

        try:
            results = llm.call_json(prompt)
            if isinstance(results, dict):
                results = results.get("result", results.get("data", [results]))
            if not isinstance(results, list):
                print(f"[Tagger] Unexpected response shape for batch {i // _BATCH_SIZE + 1}, skipping.")
                continue
        except Exception as e:
            print(f"[Tagger] LLM call failed for batch {i // _BATCH_SIZE + 1}: {e}")
            continue

        for item in results:
            try:
                post_id = item.get("post_id")
                if not post_id:
                    continue

                topic_cluster = item.get("topic_cluster")
                fmt = item.get("format")
                hook_type = item.get("hook_type")
                length_bucket = item.get("length_bucket")

                cur.execute(
                    """
                    UPDATE posts
                    SET topic_cluster = %s,
                        format        = %s,
                        hook_type     = %s,
                        length_bucket = %s,
                        tagged_at     = NOW()
                    WHERE id = %s
                      AND tagged_at IS NULL
                    """,
                    (topic_cluster, fmt, hook_type, length_bucket, str(post_id)),
                )

                title_preview = next(
                    (r[1] or str(r[0]) for r in batch if str(r[0]) == str(post_id)), str(post_id)
                )
                print(f"[Tagger] Tagged post: {title_preview[:50]}")
                tagged_count += 1

            except Exception as e:
                print(f"[Tagger] Error updating post {item.get('post_id', '?')}: {e}")

        db_conn.commit()

        if i + _BATCH_SIZE < len(rows):
            time.sleep(2)

    cur.close()
    print(f"[Tagger] Done. {tagged_count} posts tagged.")
