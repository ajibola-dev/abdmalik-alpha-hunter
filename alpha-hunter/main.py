"""
main.py — Alpha Hunter entry point

Usage:
  python main.py               # Run continuously (scheduler mode)
  python main.py --scan-once   # Run a single scan and exit
  python main.py --research <tweet_url>  # Research a specific tweet
  python main.py --reset-db    # Reset database
  python main.py --status      # Show database report
"""
import sys
import argparse
from modules.logger_setup import setup_logging
setup_logging()

import logging
from modules.database import init_db
from config.settings import settings

logger = logging.getLogger(__name__)


def print_status():
    from modules.database import get_all_projects, get_grind_tasks
    projects = get_all_projects()
    tasks = get_grind_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]

    print("\n" + "=" * 70)
    print("  ALPHA HUNTER — STATUS REPORT")
    print("=" * 70)
    print(f"\n📋 TRACKED PROJECTS ({len(projects)} total)\n")
    for p in projects:
        score = p["score"] or 0
        label = p["label"] or "Unscored"
        funding = p["funding_usd"] or 0
        funding_str = f"${funding/1e6:.0f}M" if funding >= 1e6 else "?"
        print(f"  [{score:4.1f}/10] {p['name']:<35} {p['category']:<20} {funding_str:>8}  {label}")

    print(f"\n📝 PENDING GRIND TASKS ({len(pending)} total)\n")
    for t in pending[:10]:
        print(f"  [{t['id']}] {t['project_name']:<25} ({t['wallet_label']}) — {t['task'][:50]}")

    print("\n" + "=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Alpha Hunter")
    parser.add_argument("--scan-once", action="store_true")
    parser.add_argument("--research", type=str, metavar="TWEET_URL")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--backfill", action="store_true",
                        help="Process last N tweets from all watchlist accounts")
    parser.add_argument("--backfill-count", type=int, default=50,
                        metavar="N", help="Tweets to backfill per account (default 50)")
    args = parser.parse_args()

    if args.reset_db:
        import os
        if os.path.exists(settings.DB_PATH):
            os.remove(settings.DB_PATH)
            print(f"✅ Database deleted: {settings.DB_PATH}")
        init_db()
        print("✅ Fresh database created.")
        return

    init_db()

    if args.status:
        print_status()
        return

    if args.research:
        from modules.pipeline import research_tweet_url
        research_tweet_url(args.research)
        return

    if args.backfill:
        logger.info("Running historical backfill (%d tweets per account)...",
                    args.backfill_count)
        from modules.pipeline import run_backfill_cycle
        run_backfill_cycle(max_tweets=args.backfill_count)
        print_status()
        return

    if args.scan_once:
        logger.info("Running single scan...")
        from modules.pipeline import run_scan_cycle
        run_scan_cycle()
        print_status()
        return

    # Continuous mode — scheduler + Telegram polling
    logger.info("🎯 Alpha Hunter starting in continuous mode...")
    from modules.pipeline import research_tweet_url
    from modules.telegram_bot import start_polling
    from modules.scheduler import start_all_schedulers

    # Start watchdog health monitor in background
    from modules.watchdog import start_watchdog
    start_watchdog()

    # Start Telegram polling in background
    start_polling(pipeline_callback=lambda tweet_url, chat_id:
                  research_tweet_url(tweet_url, chat_id))

    # Run all schedulers (main scan + discovery)
    start_all_schedulers()


if __name__ == "__main__":
    main()
