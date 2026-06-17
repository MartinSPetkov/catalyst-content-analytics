import json
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import db
from connectors import youtube, ordinal
from analytics import tagger, scorer, recommender


def run_cycle() -> None:
    print(f"[Scheduler] Cycle starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    db_conn = db.get_connection()
    cur = db_conn.cursor()

    cur.execute("INSERT INTO pull_log (started_at) VALUES (NOW()) RETURNING id")
    log_id = cur.fetchone()[0]
    db_conn.commit()

    posts_added = 0
    snapshots_added = 0
    errors = []

    # Connectors
    # youtube temporarily disabled — LinkedIn-only mode
    for platform_name, connector in [
        # ("youtube", youtube),
        ("ordinal", ordinal),
    ]:
        print(f"[Scheduler] Pulling {platform_name}...")
        try:
            result = connector.pull(db_conn)
            posts_added += result["posts_added"]
            snapshots_added += result["snapshots_added"]
        except Exception as e:
            msg = str(e)
            print(f"[Scheduler] {platform_name} error: {msg}")
            errors.append({"platform": platform_name, "error": msg})

    # Analytics pipeline
    print("[Scheduler] Tagging new posts...")
    try:
        tagger.tag_untagged_posts(db_conn)
    except Exception as e:
        msg = str(e)
        print(f"[Scheduler] Tagger error: {msg}")
        errors.append({"platform": "tagger", "error": msg})

    print("[Scheduler] Recomputing attribute scores...")
    try:
        scorer.recompute_scores(db_conn)
    except Exception as e:
        msg = str(e)
        print(f"[Scheduler] Scorer error: {msg}")
        errors.append({"platform": "scorer", "error": msg})

    print("[Scheduler] Generating recommendations...")
    try:
        recommender.generate_recommendations(db_conn)
    except Exception as e:
        msg = str(e)
        print(f"[Scheduler] Recommender error: {msg}")
        errors.append({"platform": "recommender", "error": msg})

    # Close out pull_log
    cur.execute(
        """
        UPDATE pull_log
        SET finished_at       = NOW(),
            platforms_pulled  = %s,
            posts_added       = %s,
            snapshots_added   = %s,
            errors            = %s
        WHERE id = %s
        """,
        (
            json.dumps(["ordinal"]),
            posts_added,
            snapshots_added,
            json.dumps(errors),
            log_id,
        ),
    )
    db_conn.commit()
    cur.close()
    db_conn.close()

    print(
        f"[Scheduler] Cycle complete. "
        f"Posts added: {posts_added}. "
        f"Snapshots added: {snapshots_added}. "
        f"Errors: {len(errors)}."
    )
    if errors:
        print(f"[Scheduler] Errors: {errors}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_cycle()
        sys.exit(0)
    else:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        sched = BlockingScheduler()
        sched.add_job(run_cycle, CronTrigger(hour=8, minute=0))
        print("[Scheduler] Started. Next run at 08:00 local time.")
        sched.start()
