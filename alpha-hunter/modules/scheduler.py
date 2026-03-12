"""
modules/scheduler.py
Two separate schedules:
  - Main scan:       every SCAN_INTERVAL_HOURS (default 4h)
  - Discovery scan:  every 24h (network account discovery)
"""
import logging
import time
import threading
from config.settings import settings

logger = logging.getLogger(__name__)


def run_scheduler():
    """Run main scan cycle on schedule."""
    from modules.pipeline import run_scan_cycle
    interval = settings.SCAN_INTERVAL_HOURS * 3600
    logger.info("Main scheduler started — scanning every %dh",
                settings.SCAN_INTERVAL_HOURS)
    while True:
        try:
            run_scan_cycle()
        except Exception as exc:
            logger.error("Scan cycle failed: %s", exc, exc_info=True)
        logger.info("Next main scan in %dh", settings.SCAN_INTERVAL_HOURS)
        time.sleep(interval)


def run_discovery_scheduler():
    """Run network discovery every 24h in background thread."""
    from modules.pipeline import run_discovery_cycle
    logger.info("Discovery scheduler started — running every 24h")
    # Wait 1h before first discovery run (let main scan run first)
    time.sleep(3600)
    while True:
        try:
            run_discovery_cycle()
        except Exception as exc:
            logger.error("Discovery cycle failed: %s", exc, exc_info=True)
        logger.info("Next discovery in 24h")
        time.sleep(86400)


def start_all_schedulers():
    """Start discovery in background, run main scan in foreground."""
    discovery_thread = threading.Thread(
        target=run_discovery_scheduler, daemon=True
    )
    discovery_thread.start()
    logger.info("Discovery scheduler running in background")
    run_scheduler()  # Blocking
