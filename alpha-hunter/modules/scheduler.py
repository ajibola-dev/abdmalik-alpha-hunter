"""
modules/scheduler.py
Four independent schedules running concurrently.

v0.4 change:
  - X Monitor now runs run_scan_cycle_async() from pipeline_async.py
    instead of the sync run_scan_cycle() from pipeline.py
  - If aiohttp is not installed, automatically falls back to sync scan
  - All other cycles unchanged

Schedule:
  Every 4h  — X Monitor scan (async research)
  Every 12h — Funding scan (DeFiLlama + CryptoRank)
  Every 24h — GitHub scan (technical signals before Twitter)
  Every 24h — Network discovery (find new alpha accounts)
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
    X monitor (async) runs in foreground (main thread).
    """
    from modules.pipeline import (
        run_funding_scan_cycle,
        run_github_scan_cycle,
        run_discovery_cycle,
    )

    # v0.4: use async scan cycle; fall back to sync if aiohttp not available
    try:
        from modules.pipeline_async import run_scan_cycle_async
        scan_fn = run_scan_cycle_async
        logger.info("Using async scan cycle (aiohttp available)")
    except ImportError:
        from modules.pipeline import run_scan_cycle
        scan_fn = run_scan_cycle
        logger.info("Using sync scan cycle (aiohttp not available)")

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
    logger.info("  X Monitor:        every %sh (async)", settings.SCAN_INTERVAL_HOURS)
    logger.info("  Funding Scanner:  every 12h")
    logger.info("  GitHub Scanner:   every 24h")
    logger.info("  Network Discovery: every 24h")
    logger.info("=" * 55)

    # ── Foreground: X monitor (async) ──────────────────────────────────────
    _run_loop("X Monitor", scan_fn, scan_interval)
