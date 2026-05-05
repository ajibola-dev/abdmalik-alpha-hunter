"""
modules/scheduler.py — v2.0

Two cycles only:
  1. X Monitor — every 4h
  2. GitHub Scanner — every 24h

DeFiLlama and CoinGecko scanners removed.
"""
import logging
import time
import threading
from config.settings import settings

logger = logging.getLogger(__name__)


def _run_loop(name: str, fn, interval_seconds: int, initial_delay: int = 0):
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
    from modules.pipeline import run_scan_cycle, run_github_scan_cycle

    scan_interval = settings.SCAN_INTERVAL_HOURS * 3600

    threads = [
        threading.Thread(
            target=_run_loop,
            args=("GitHub Scanner", run_github_scan_cycle, 86400, 1800),
            daemon=True, name="github-scanner"
        ),
    ]

    for t in threads:
        t.start()
        logger.info("Started background thread: %s", t.name)

    logger.info("=" * 55)
    logger.info("  ALPHA HUNTER v2.0 — ALL SYSTEMS ACTIVE")
    logger.info("  X Monitor:      every %sh", settings.SCAN_INTERVAL_HOURS)
    logger.info("  GitHub Scanner: every 24h")
    logger.info("  Verticals:      ZK/L2 | PerpDEX | Solana DeFi | Cosmos/DA")
    logger.info("=" * 55)

    _run_loop("X Monitor", run_scan_cycle, scan_interval)
