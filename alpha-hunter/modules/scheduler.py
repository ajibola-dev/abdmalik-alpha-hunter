"""modules/scheduler.py — Runs scan every N hours."""
import logging
import time
from config.settings import settings
from modules.pipeline import run_scan_cycle

logger = logging.getLogger(__name__)


def run_scheduler():
    interval = settings.SCAN_INTERVAL_HOURS * 3600
    logger.info("Scheduler started — scanning every %dh", settings.SCAN_INTERVAL_HOURS)
    while True:
        try:
            run_scan_cycle()
        except Exception as exc:
            logger.error("Scan cycle failed: %s", exc, exc_info=True)
        logger.info("Next scan in %dh", settings.SCAN_INTERVAL_HOURS)
        time.sleep(interval)
