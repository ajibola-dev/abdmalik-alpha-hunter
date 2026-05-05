"""
main.py — Alpha Hunter v2.0

Usage:
  python main.py              # Continuous mode
  python main.py --scan-once  # Single scan and exit
  python main.py --status     # Database report
  python main.py --reset-db   # Reset database
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
    from modules.database import get_all_projects
    projects = get_all_projects()

    print("\n" + "=" * 60)
    print("  ALPHA HUNTER v2.0 — STATUS REPORT")
    print("=" * 60)
    print(f"\n📋 TRACKED PROJECTS ({len(projects)} total)\n")

    grind = [p for p in projects if p.get("label") == "GRIND NOW"]
    watch = [p for p in projects if p.get("label") == "WATCH CLOSELY"]
    monitor = [p for p in projects if p.get("label") == "MONITOR"]

    for label, group, emoji in [
        ("GRIND NOW", grind, "🔥"),
        ("WATCH CLOSELY", watch, "👀"),
        ("MONITOR", monitor, "📡"),
    ]:
        if group:
            print(f"\n{emoji} {label}:")
            for p in group:
                print(f"  {p['name']:<30} {p.get('category',''):<15} "
                      f"@{p.get('mentioned_by','?')}")

    print("\n" + "=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Alpha Hunter v2.0")
    parser.add_argument("--scan-once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reset-db", action="store_true")
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

    if args.scan_once:
        logger.info("Running single scan...")
        from modules.pipeline import run_scan_cycle
        run_scan_cycle()
        print_status()
        return

    # Continuous mode
    logger.info("🎯 Alpha Hunter v2.0 starting...")

    from modules.watchdog import start_watchdog
    from modules.telegram_bot import start_polling
    from modules.scheduler import start_all_schedulers

    start_watchdog()

    import threading
    polling_thread = threading.Thread(
        target=start_polling, daemon=True, name="telegram-polling"
    )
    polling_thread.start()
    logger.info("Telegram polling started")

    start_all_schedulers()


if __name__ == "__main__":
    main()
