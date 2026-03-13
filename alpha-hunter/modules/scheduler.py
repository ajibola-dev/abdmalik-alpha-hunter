"""
modules/scheduler.py
Four independent schedules running concurrently:

  Every 4h  — X Monitor scan (Zun + watchlist tweets)
  Every 12h — Funding scan (DeFiLlama + CryptoRank, all categories)
  Every 24h — GitHub scan (technical signals before Twitter)
  Every 24h — Network discovery (find new alpha accounts)

All run in background threads except the X monitor which runs
in the main thread.
"""
import logging
import time
import threading
from config.settings import settings

logger = logging.getLogger(__name__)


def _run_loop(name: str, fn, interval_seconds: int, initial_delay: int = 0):
    """Generic scheduler loop."""
    if initial_delay:
        logger.info("%s starting in %dm", name, initial_delay // 60)
        time.sleep(initial_delay)
    while True:
        logger.info("▶️  %s starting", name)
        try:
            fn()
        except Exception as exc:
            logger.error("%s failed: %s", name, exc, exc_info=True)
        logger.info("⏸️  %s done — next in %dh",
                    name, interval_seconds // 3600)
        time.sleep(interval_seconds)


def start_all_schedulers():
    """
    Start all scan cycles.
    Funding + GitHub + Discovery run in background threads.
    X monitor runs in foreground (main thread).
    """
    from modules.pipeline import (
        run_scan_cycle,
        run_funding_scan_cycle,
        run_github_scan_cycle,
        run_discovery_cycle,
    )

    scan_interval = settings.SCAN_INTERVAL_HOURS * 3600

    # ── Background threads ─────────────────────────────────────────────────
    threads = [
        threading.Thread(
            target=_run_loop,
            args=("Funding Scanner", run_funding_scan_cycle, 43200, 300),
            daemon=True, name="funding-scanner"
        ),
        threading.Thread(
            target=_run_loop,
            args=("GitHub Scanner", run_github_scan_cycle, 86400, 1800),
            daemon=True, name="github-scanner"
        ),
        threading.Thread(
            target=_run_loop,
            args=("Network Discovery", run_discovery_cycle, 86400, 3600),
            daemon=True, name="network-discovery"
        ),
    ]

    for t in threads:
        t.start()
        logger.info("Started background thread: %s", t.name)

    logger.info("=" * 55)
    logger.info("  ALPHA HUNTER — ALL SYSTEMS ACTIVE")
    logger.info("  X Monitor:        every %sh", settings.SCAN_INTERVAL_HOURS)
    logger.info("  Funding Scanner:  every 12h")
    logger.info("  GitHub Scanner:   every 24h")
    logger.info("  Network Discovery: every 24h")
    logger.info("=" * 55)

    # ── Foreground: X monitor ──────────────────────────────────────────────
    _run_loop("X Monitor", run_scan_cycle, scan_interval)
