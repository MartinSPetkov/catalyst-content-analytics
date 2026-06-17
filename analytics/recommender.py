import json

from shared import llm, antislop


def generate_recommendations(db_conn) -> None:
    cur = db_conn.cursor()

    # Top attribute scores
    cur.execute("""
        SELECT attribute_type, attribute_value, post_count, avg_engagement_rate,
               trend_delta, confidence
        FROM attribute_scores
        WHERE confidence IN ('medium', 'high')
        ORDER BY avg_engagement_rate DESC
    """)
    top_scores = cur.fetchall()

    # Active hypotheses
    cur.execute("""
        SELECT id, hypothesis_text, confidence_score
        FROM hypotheses
        WHERE is_active = true
        ORDER BY generated_at DESC
        LIMIT 5
    """)
    hypotheses = cur.fetchall()

    # Top 3 posts by engagement in last 90 days
    cur.execute("""
        SELECT p.title, p.platform, p.url, ms.engagement_rate
        FROM metrics_snapshots ms
        JOIN posts p ON p.id = ms.post_id
        WHERE ms.pulled_at > NOW() - INTERVAL '90 days'
          AND ms.engagement_rate IS NOT NULL
        ORDER BY ms.engagement_rate DESC
        LIMIT 3
    """)
    top_posts = cur.fetchall()

    # All scores for snapshot
    cur.execute("SELECT attribute_type, attribute_value, avg_engagement_rate, confidence FROM attribute_scores")
    all_scores = cur.fetchall()
    scores_snapshot = [
        {"attribute_type": r[0], "attribute_value": r[1],
         "avg_engagement_rate": float(r[2]) if r[2] else None, "confidence": r[3]}
        for r in all_scores
    ]

    # Build prompt
    def _fmt_score(r) -> str:
        avg = f"{float(r[3]):.4f}" if r[3] is not None else "N/A"
        delta = f"{float(r[4]):.4f}" if r[4] is not None else "N/A"
        return f"  {r[0]}={r[1]}: avg_engagement={avg}, posts={r[2]}, trend_delta={delta}, confidence={r[5]}"

    scores_text = "\n".join(_fmt_score(r) for r in top_scores)
    hypotheses_text = "\n".join(
        f"  [{r[1]:.3f}] {r[2]}" for r in hypotheses
    ) if hypotheses else "  None yet."

    top_posts_text = "\n".join(
        f"  [{r[3]:.4f} engagement] {r[0] or 'Untitled'} ({r[1]}) — {r[2] or 'no url'}"
        for r in top_posts
    ) if top_posts else "  No recent top posts."

    prompt = f"""You are a content strategy advisor. Based on the performance data below, generate 5 specific content ideas.

TOP ATTRIBUTE SCORES (what formats, topics, and hooks are performing best):
{scores_text}

ACTIVE HYPOTHESES:
{hypotheses_text}

TOP 3 POSTS LAST 90 DAYS:
{top_posts_text}

Return a JSON array of exactly 5 objects. Each object must have:
  title           — a specific, actionable content title
  format          — one of: long-form, list, case-study, opinion, how-to, interview, other
  topic_cluster   — one of: AI search, GTM strategy, content ops, demand gen, sales, product marketing, other
  hook_type       — one of: question, bold-claim, stat, story, contrarian, other
  reasoning       — must cite a specific attribute score, e.g. "long-form posts on GTM strategy average 4.2% engagement vs 1.8% overall"
  confidence_basis — which attribute score(s) this recommendation is based on

Be specific. Do not use vague claims. Every reasoning field must reference a number from the attribute scores above.

Return only the JSON array. No commentary."""

    print("[Recommender] Calling LLM for recommendations...")
    try:
        results = llm.call_json(prompt)
        if isinstance(results, dict):
            results = results.get("result", results.get("data", [results]))
        if not isinstance(results, list):
            print(f"[Recommender] Unexpected response shape, aborting.")
            cur.close()
            return
    except Exception as e:
        print(f"[Recommender] LLM call failed: {e}")
        cur.close()
        return

    attributes_used = [
        {"attribute_type": r[0], "attribute_value": r[1],
         "avg_engagement_rate": float(r[3]) if r[3] else None, "confidence": r[5]}
        for r in top_scores
    ]

    rec_count = 0
    for item in results[:5]:
        try:
            reasoning = item.get("reasoning", "")
            reasoning = antislop.clean(reasoning)

            cur.execute(
                """
                INSERT INTO recommendations
                    (generated_at, reasoning, attributes_used, scores_snapshot)
                VALUES (NOW(), %s, %s, %s)
                """,
                (reasoning, json.dumps(attributes_used), json.dumps(scores_snapshot)),
            )
            rec_count += 1
        except Exception as e:
            print(f"[Recommender] Error storing recommendation: {e}")

    # Hypothesis refresh — new hypotheses for high-confidence rising attributes
    cur.execute("""
        SELECT attribute_type, attribute_value, avg_engagement_rate, trend_delta
        FROM attribute_scores
        WHERE confidence = 'high' AND trend_delta > 0
    """)
    rising = cur.fetchall()

    new_hypotheses_needed = []
    for attr_type, attr_val, avg_rate, delta in rising:
        cur.execute(
            """
            SELECT 1 FROM hypotheses
            WHERE is_active = true
              AND hypothesis_text ILIKE %s
            LIMIT 1
            """,
            (f"%{attr_val}%",),
        )
        if not cur.fetchone():
            new_hypotheses_needed.append((attr_type, attr_val, float(avg_rate), float(delta)))

    hyp_count = 0
    if new_hypotheses_needed:
        hyp_lines = "\n".join(
            f"  {t}={v}: avg_engagement={r:.4f}, trend_delta=+{d:.4f}"
            for t, v, r, d in new_hypotheses_needed
        )
        hyp_prompt = f"""Generate one hypothesis per attribute below. Each hypothesis is a falsifiable belief about content performance.

Attributes showing high confidence and positive trend:
{hyp_lines}

Return a JSON array where each item has:
  attribute_type, attribute_value, hypothesis_text, confidence_score (0.0-1.0)

hypothesis_text should be a single declarative sentence citing the data.
Return only the JSON array."""

        try:
            hyp_results = llm.call_json(hyp_prompt)
            if isinstance(hyp_results, dict):
                hyp_results = hyp_results.get("result", hyp_results.get("data", [hyp_results]))
            if isinstance(hyp_results, list):
                for h in hyp_results:
                    text = antislop.clean(h.get("hypothesis_text", ""))
                    score = h.get("confidence_score", 0.5)
                    cur.execute(
                        """
                        INSERT INTO hypotheses
                            (hypothesis_text, confidence_score, supporting_post_count, generated_at, is_active)
                        VALUES (%s, %s, 0, NOW(), true)
                        """,
                        (text, score),
                    )
                    hyp_count += 1
        except Exception as e:
            print(f"[Recommender] Hypothesis generation failed: {e}")

    # Deactivate hypotheses whose attribute now has low confidence
    cur.execute("""
        UPDATE hypotheses h
        SET is_active = false
        WHERE h.is_active = true
          AND EXISTS (
            SELECT 1 FROM attribute_scores s
            WHERE h.hypothesis_text ILIKE '%' || s.attribute_value || '%'
              AND s.confidence = 'low'
          )
    """)
    deactivated = cur.rowcount

    db_conn.commit()
    cur.close()
    print(f"[Recommender] Generated {rec_count} recommendations. "
          f"{hyp_count} hypotheses added, {deactivated} deactivated.")
