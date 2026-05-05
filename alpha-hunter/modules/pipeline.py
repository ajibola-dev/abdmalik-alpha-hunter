"""
modules/pipeline.py — v2.0

Simplified pipeline. Two scan cycles:
  1. X Monitor — watches signal accounts every 4h
  2. GitHub Scanner — watches for new testnet repos every 24h

No DeFiLlama. No CoinGecko. No complex scoring.

The pipeline:
  extract → filter (vertical check) → research → evaluate → alert
"""
import logging
import time
import uuid
import json

from config.settings import settings
from modules import database as db
from modules.project_extractor import extract_project_names, score_tweet_quality
from modules.researcher import research_project
from modules.scorer import evaluate_project, FarmingBrief
from modules.action_planner import format_farming_brief_for_telegram

logger = logging.getLogger(__name__)


def _make_scan_id() -> str:
    from datetime import datetime
    return f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _load_watchlist() -> list[dict]:
    """Load active accounts from watchlist.json."""
    import json as _json
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = _json.load(f)
        return [a for a in data.get("accounts", [])
                if a.get("status", "active") == "active"]
    except Exception as exc:
        logger.error("Could not load watchlist: %s", exc)
        return []


def _send_alert(message: str) -> bool:
    """Send Telegram message."""
    from modules.telegram_bot import send_message
    return send_message(message)


def _already_alerted(project_id: int, source: str = "x_monitor") -> bool:
    return db.already_alerted(project_id, source)


# ── Extract stage ─────────────────────────────────────────────────────────

def extract_stage(tweets: list[dict],
                  watchlist: list[dict],
                  scan_id: str) -> list[dict]:
    """
    Extract project candidates from tweets.
    Only passes tweets that have vertical signal.
    """
    logger.info("[%s] ── extract_stage (%d tweets) ──", scan_id, len(tweets))

    # Build handle → account map for tier/vertical lookup
    account_map = {a["handle"].lower(): a for a in watchlist}

    candidates = []
    for tweet in tweets:
        handle = tweet.get("handle", "").lower()
        text = tweet.get("text", "")
        account = account_map.get(handle, {})
        tier = account.get("tier", 2)
        weight = account.get("weight", 1.0)
        verticals = account.get("verticals", [])

        quality = score_tweet_quality(text, handle, tier, verticals)

        # Quality gate — tier 1 accounts get lower threshold
        min_quality = 0.15 if tier == 1 else 0.25
        if quality < min_quality:
            continue

        names = extract_project_names(text, scan_id=scan_id,
                                       tweet_id=tweet.get("id", ""))
        if not names:
            continue

        logger.info("[%s] @%s quality=%.2f — candidates: %s",
                    scan_id, handle, quality, names)

        for name in names:
            candidates.append({
                "project_name": name,
                "handle": handle,
                "tweet": tweet,
                "caller_tier": tier,
                "caller_weight": weight,
                "caller_verticals": verticals,
                "quality": quality,
            })

    logger.info("[%s] extract_stage complete — %d candidates", scan_id, len(candidates))
    return candidates


# ── Filter stage ──────────────────────────────────────────────────────────

def filter_stage(candidates: list[dict], scan_id: str) -> list[dict]:
    """
    Drop obvious non-projects before any API call.
    Deduplicate by project name.
    """
    logger.info("[%s] ── filter_stage (%d candidates) ──",
                scan_id, len(candidates))

    seen_names = set()
    passed = []

    for c in candidates:
        name = c["project_name"]
        name_lower = name.lower().strip()

        # Deduplicate
        if name_lower in seen_names:
            continue

        # Check DB cache — if we already know it has a token, skip
        cached = db.get_research_cache(name)
        if cached and cached.get("has_token"):
            logger.debug("[%s] filter: '%s' token live (cache)", scan_id, name)
            continue

        seen_names.add(name_lower)
        passed.append(c)

    dropped = len(candidates) - len(passed)
    logger.info("[%s] filter_stage: %d passed, %d dropped",
                scan_id, len(passed), dropped)
    return passed


# ── Research stage ────────────────────────────────────────────────────────

def research_stage(candidates: list[dict], scan_id: str) -> list[dict]:
    """Research each candidate. Sequential — no more rate limit crashes."""
    logger.info("[%s] ── research_stage (%d candidates) ──",
                scan_id, len(candidates))

    enriched = []
    for c in candidates:
        name = c["project_name"]
        tweet_text = c["tweet"].get("text", "")

        # List tweets: research each project in isolation
        # to prevent FHE contamination across projects
        list_tweet = bool(
            __import__('re').search(r'^\s*\d+[\.\)]', tweet_text,
                                     __import__('re').MULTILINE)
            and len(candidates) > 1
        )
        research_text = "" if list_tweet else tweet_text

        try:
            project = research_project(name, tweet_text=research_text)

            if project.get("has_token"):
                logger.info("[%s] '%s' token live — skip", scan_id, name)
                continue

            project["mentioned_by"] = c["handle"]
            project["tweet_url"] = c["tweet"].get("url", "")
            project["tweet_text"] = tweet_text[:300]
            enriched.append({**c, "project": project})

        except Exception as exc:
            logger.error("[%s] research error for '%s': %s", scan_id, name, exc)

        time.sleep(1.5)  # Gentle — no rate limit issues

    logger.info("[%s] research_stage complete — %d enriched",
                scan_id, len(enriched))
    return enriched


# ── Evaluate stage ────────────────────────────────────────────────────────

def evaluate_stage(enriched: list[dict], scan_id: str) -> list[dict]:
    """Evaluate each project against historical airdrop patterns."""
    logger.info("[%s] ── evaluate_stage (%d projects) ──",
                scan_id, len(enriched))

    evaluated = []
    for c in enriched:
        project = c["project"]
        name = project.get("name", "?")

        brief = evaluate_project(
            project,
            caller_tier=c.get("caller_tier", 2),
            caller_weight=c.get("caller_weight", 1.0),
        )

        logger.info("[%s] '%s': conviction=%s vertical=%s",
                    scan_id, name, brief.conviction, brief.vertical)

        if brief.conviction == "SKIP":
            continue

        # Store in DB
        project_id = db.upsert_project(
            name=name,
            mentioned_by=project.get("mentioned_by", ""),
            tweet_url=project.get("tweet_url", ""),
            tweet_text=project.get("tweet_text", ""),
            category=project.get("vertical", ""),
            funding_usd=project.get("funding_usd", 0),
            investors=project.get("investors", ""),
            has_token=project.get("has_token", False),
            testnet_active=project.get("testnet_active", False),
            website=project.get("website", ""),
            github=project.get("github", ""),
            description=project.get("description", ""),
        )
        db.save_score(
            project_id=project_id,
            score=brief.raw_signals.get("total", 0),
            breakdown=json.dumps(brief.raw_signals),
            label=brief.conviction,
        )

        evaluated.append({**c, "brief": brief, "project_id": project_id})

    logger.info("[%s] evaluate_stage complete — %d worth alerting",
                scan_id, len(evaluated))
    return evaluated


# ── Alert stage ───────────────────────────────────────────────────────────

def alert_stage(evaluated: list[dict], scan_id: str,
                source: str = "x_monitor") -> int:
    """Send farming briefs for GRIND NOW and WATCH CLOSELY projects."""
    logger.info("[%s] ── alert_stage (%d candidates) ──",
                scan_id, len(evaluated))

    sent = 0
    for c in evaluated:
        brief = c["brief"]
        project = c["project"]
        project_id = c["project_id"]

        # Only alert on GRIND NOW and WATCH CLOSELY
        if brief.conviction not in ("GRIND NOW", "WATCH CLOSELY"):
            continue

        if _already_alerted(project_id, source):
            continue

        if db.alerts_today() >= settings.MAX_ALERTS_PER_DAY:
            logger.warning("[%s] Daily alert limit reached", scan_id)
            break

        message = format_farming_brief_for_telegram(brief, project)
        if not message:
            continue

        if _send_alert(message):
            db.log_alert(project_id, source)
            sent += 1
            logger.info("[%s] 🌟 alert sent: '%s' (%s)",
                        scan_id, brief.project_name, brief.conviction)

    logger.info("[%s] alert_stage complete — %d sent", scan_id, sent)
    return sent


# ── Main scan cycles ──────────────────────────────────────────────────────

def run_scan_cycle():
    """X Monitor scan cycle."""
    from modules.x_monitor import monitor_all_accounts

    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("🎯 Alpha Hunter Scan Starting [%s]", scan_id)
    logger.info("=" * 60)

    watchlist = _load_watchlist()
    if not watchlist:
        logger.error("[%s] No accounts in watchlist", scan_id)
        return

    tweets = monitor_all_accounts(watchlist, scan_id=scan_id)
    if not tweets:
        logger.info("[%s] No new tweets", scan_id)
        return

    candidates = extract_stage(tweets, watchlist, scan_id)
    if not candidates:
        logger.info("[%s] No candidates extracted", scan_id)
        db.update_heartbeat()
        return

    filtered = filter_stage(candidates, scan_id)
    enriched = research_stage(filtered, scan_id)
    evaluated = evaluate_stage(enriched, scan_id)
    sent = alert_stage(evaluated, scan_id)

    db.update_heartbeat()
    logger.info("=" * 60)
    logger.info("✅ [%s] Scan complete — %d alerts sent", scan_id, sent)
    logger.info("=" * 60)


def run_github_scan_cycle():
    """GitHub scan — finds new testnet repos from serious teams."""
    from modules.github_scanner import run_github_scan
    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("⚙️ GitHub Scan Starting [%s]", scan_id)
    logger.info("=" * 60)
    run_github_scan(scan_id=scan_id)
    db.update_heartbeat()


# ── Manual research (for /research and /lookup commands) ─────────────────

def process_tweet(tweet: dict, caller_tier: int = 1) -> list[dict]:
    """Process a single tweet manually."""
    scan_id = _make_scan_id()
    watchlist = _load_watchlist()
    account = next(
        (a for a in watchlist
         if a["handle"].lower() == tweet.get("handle", "").lower()),
        {"tier": caller_tier, "weight": 1.0, "verticals": []}
    )

    candidates = extract_stage([tweet], [account], scan_id)
    filtered = filter_stage(candidates, scan_id)
    enriched = research_stage(filtered, scan_id)
    return evaluate_stage(enriched, scan_id)
