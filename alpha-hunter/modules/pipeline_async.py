"""
modules/pipeline_async.py
Async-capable pipeline — v0.4

Drop-in upgrade for pipeline.py's research_stage().
Replaces blocking HTTP research calls with concurrent async calls
while keeping all other stages (extract, filter, score, alert) unchanged.

How it works:
  - research_stage_async() wraps the v0.4 researcher_async module
  - asyncio.run() bridges the sync scheduler into the async research calls
  - If aiohttp is not installed, falls back to sync research_stage()
    so the system degrades gracefully without crashing

To enable async research in run_scan_cycle():
  In scheduler.py or main.py, import run_scan_cycle_async instead of
  run_scan_cycle. Everything else (stages, DB, scoring, alerts) is identical.

Pipeline flow (v0.4):
  extract_stage()           [sync  — unchanged]
  fast_filter_stage()       [sync  — unchanged]
  research_stage_async()    [ASYNC — new v0.4]
  score_stage()             [sync  — unchanged]
  alert_stage()             [sync  — unchanged]
"""
import asyncio
import logging

from config.settings import settings
from modules import database as db
from modules.pipeline import (
    _load_watchlist,
    _make_scan_id,
    extract_stage,
    fast_filter_stage,
    score_stage,
    alert_stage,
)

logger = logging.getLogger(__name__)

# Detect aiohttp availability
try:
    from modules.researcher_async import research_projects_async
    ASYNC_AVAILABLE = True
except ImportError:
    ASYNC_AVAILABLE = False


async def research_stage_async(candidates: list[dict],
                                scan_id: str) -> list[dict]:
    """
    Async version of pipeline.research_stage().
    Runs all project research concurrently with asyncio.Semaphore
    limiting parallel requests to settings.CONCURRENT_REQUESTS.

    Falls back to sync research_stage() if aiohttp is unavailable.
    """
    if not ASYNC_AVAILABLE:
        logger.warning(
            "[%s] aiohttp not available — falling back to sync research_stage",
            scan_id
        )
        from modules.pipeline import research_stage
        return research_stage(candidates, scan_id)

    logger.info("[%s] ── research_stage_async (%d candidates) ──",
                scan_id, len(candidates))

    enriched = await research_projects_async(candidates)

    logger.info("[%s] research_stage_async complete — %d enriched",
                scan_id, len(enriched))
    return enriched


def run_scan_cycle_async():
    """
    Full async scan cycle — v0.4.
    Identical to pipeline.run_scan_cycle() except research_stage
    is replaced with the concurrent async version.

    Use this in scheduler.py to enable async research.
    """
    scan_id = _make_scan_id()

    logger.info("=" * 60)
    logger.info("🎯 Alpha Hunter Async Scan Starting [%s]", scan_id)
    logger.info("=" * 60)

    watchlist = _load_watchlist()
    if not watchlist:
        logger.warning("[%s] Watchlist is empty!", scan_id)
        return

    # Sync watchlist to DB
    for acc in watchlist:
        db.upsert_account(
            handle=acc.get("handle", ""),
            name=acc.get("name", ""),
            tier=acc.get("tier", 2),
            trusted=acc.get("trusted", False),
            notes=acc.get("notes", ""),
        )

    # ── Sync stages ────────────────────────────────────────────────────────
    candidates = extract_stage(watchlist, scan_id)
    candidates = fast_filter_stage(candidates, scan_id)

    # ── Async research stage ───────────────────────────────────────────────
    candidates = asyncio.run(research_stage_async(candidates, scan_id))

    # ── Sync stages ────────────────────────────────────────────────────────
    candidates = score_stage(candidates, scan_id)
    genesis_count = alert_stage(candidates, scan_id)

    logger.info("=" * 60)
    logger.info("✅ [%s] Async scan complete — %d genesis calls",
                scan_id, genesis_count)
    logger.info("=" * 60)
