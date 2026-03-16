"""
modules/pipeline.py
Orchestrates the full Alpha Hunter scan cycle.

v0.3 changes — Pipeline Staging:
  - run_scan_cycle() refactored into discrete named stages:
      extract_stage()      — fetch tweets, extract project names
      fast_filter_stage()  — drop obvious non-projects before research
      research_stage()     — API research on surviving candidates
      score_stage()        — Zun-method scoring
      alert_stage()        — DB persist + Telegram alert
  - scan_id (timestamp-based) propagated through all stages and logs
  - MAX_PROJECTS_PER_TWEET and MAX_PROJECTS_PER_SCAN enforced from settings
  - process_tweet() retained for backward compat (used by /research command)
  - All stage functions are independently testable and clearly separated

v0.2 changes carried forward:
  - tweet dedup via DB (is_seen_tweet delegates to database.tweet_seen)
  - structured log context throughout
"""
import json
import logging
import time
from datetime import datetime

from config.settings import settings
from modules import database as db
from modules.x_monitor import monitor_all_accounts, fetch_tweet_from_url
from modules.project_extractor import (
    extract_project_names, score_tweet_quality, is_seen_tweet
)
from modules.researcher import research_project
from modules.scorer import score_project
from modules.action_planner import generate_action_plan, format_action_plan_for_telegram
from modules.telegram_bot import send_message

logger = logging.getLogger(__name__)


def _load_watchlist() -> list[dict]:
    """
    Load active (non-probation) accounts from watchlist.json.
    Tier-0 probation accounts are excluded from all scan cycles.
    """
    import json as _json
    accounts = []
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = _json.load(f)
            all_accounts = data.get("accounts", [])
            accounts = [a for a in all_accounts
                        if a.get("tier", 2) > 0
                        and a.get("status", "active") != "probation"]
    except Exception as exc:
        logger.error("Could not load watchlist: %s", exc)
    return accounts


def _make_scan_id() -> str:
    """Short scan identifier for structured logs: e.g. 'scan_20240315_143022'"""
    return datetime.now().strftime("scan_%Y%m%d_%H%M%S")


# ══════════════════════════════════════════════════════════════════════════
# v0.3 PIPELINE STAGES
# ══════════════════════════════════════════════════════════════════════════

def extract_stage(watchlist: list[dict], scan_id: str) -> list[dict]:
    """
    Stage 1 — Fetch tweets + extract candidate project names.
    Returns list of candidate dicts:
      {tweet, handle, tier, project_name, tweet_quality}
    """
    logger.info("[%s] ── extract_stage starting ──────────────", scan_id)

    all_tweets = monitor_all_accounts(watchlist, scan_id=scan_id)
    db.log_scan("x_monitor", len(all_tweets),
                notes=f"scan_id={scan_id}")

    candidates = []
    projects_this_scan = 0

    for tweet in all_tweets:
        text = tweet.get("text", "")
        handle = tweet.get("handle", "unknown")
        tweet_url = tweet.get("url", "")
        tweet_id = tweet.get("id", "")

        # Tweet-level dedup (DB-backed since v0.2)
        if is_seen_tweet(tweet_url, text):
            logger.debug("[%s] tweet %s already seen", scan_id, tweet_id)
            continue

        # Get account tier + weight (v0.6: weight enables fractional tier-1)
        acc = next((a for a in watchlist if a.get("handle") == handle), {})
        tier = acc.get("tier", 2)
        weight = float(acc.get("weight", 1.0))

        # Quality gate — skip low-signal tweets early
        tweet_quality = score_tweet_quality(text, handle, tier)
        if tweet_quality < 0.2:
            logger.debug("[%s] @%s tweet quality %.2f < 0.2 — skipping",
                         scan_id, handle, tweet_quality)
            continue

        # Extract project names
        names = extract_project_names(
            text, scan_id=scan_id, tweet_id=tweet_id
        )
        if not names:
            continue

        logger.info("[%s] @%s quality=%.2f — candidates: %s",
                    scan_id, handle, tweet_quality, names)

        for name in names[:settings.MAX_PROJECTS_PER_TWEET]:
            if projects_this_scan >= settings.MAX_PROJECTS_PER_SCAN:
                logger.info("[%s] MAX_PROJECTS_PER_SCAN (%d) reached",
                            scan_id, settings.MAX_PROJECTS_PER_SCAN)
                break
            candidates.append({
                "tweet": tweet,
                "handle": handle,
                "tier": tier,
                "weight": weight,
                "project_name": name,
                "tweet_quality": tweet_quality,
            })
            projects_this_scan += 1

    logger.info("[%s] extract_stage complete — %d candidates",
                scan_id, len(candidates))
    return candidates


# Known live tokens — never research these, saves CoinGecko calls
_KNOWN_LIVE_TOKENS = {
    "bitcoin", "ethereum", "solana", "polygon", "avalanche", "base",
    "opensea", "polymarket", "coinbase", "zora", "uniswap", "aave",
    "chainlink", "arbitrum", "optimism", "blur", "dydx", "gmx",
    "hyperliquid", "jupiter", "raydium", "orca", "drift", "jito",
    "wormhole", "layerzero", "eigenlayer", "lido", "rocket pool",
    "pendle", "ethena", "ondo", "usual", "sky", "maker", "compound",
}

# Multi-word phrases that are never project names — seen in logs
_NOISE_PHRASES = {
    "airdrop era", "bitcoin ethereum", "ripple stellar", "cardano doge",
    "tron monero", "litecoin neo", "jack devine", "deputy director",
    "arkin group", "execute solana", "privy privy", "sapiensolana human",
    "those who", "airdrop era",
}

# Single words that are clearly not crypto projects
_HARD_NOISE_WORDS = {
    # Off-topic proper nouns seen in logs
    "risk", "return", "those", "iran", "strait", "hormuz",
    "sennin", "episode", "copytrade", "onboarded",
    # VC/investor names — not projects
    "animoca", "pantera", "framework", "multicoin", "paradigm",
    "sequoia", "andreessen", "binance", "coinbase", "kraken",
    # Generic crypto words extracted as names
    "farm", "farming", "round", "backed", "beta", "made", "genesis",
    "grind", "alpha", "signal", "early", "stage", "launch",
    "airdrop", "points", "rewards", "season", "quest",
    # Common English words that slip through
    "good", "great", "still", "just", "only", "real", "true",
    "high", "low", "new", "old", "big", "small", "fast", "slow",
    # Names/handles extracted as projects
    "frogy", "horlaj", "zun", "cat", "malik",
    # Chain names already live
    "bitcoin", "ethereum", "ripple", "stellar", "cardano",
    "doge", "tron", "monero", "litecoin", "neo",
    "afk", "knx", "markets", "hype", "lit",
}


def fast_filter_stage(candidates: list[dict], scan_id: str) -> list[dict]:
    """
    Stage 2 — Fast pre-research filter. v0.9.3: expanded noise detection.
    Drops obvious non-projects without any API calls.
    """
    import re as _re

    logger.info("[%s] ── fast_filter_stage (%d candidates) ──",
                scan_id, len(candidates))

    # Whitelist — short names that ARE real projects
    _WHITELIST = {"miden", "zama", "thru", "tempo", "dtel", "kaito",
                  "blur", "ondo", "knx", "afk"}

    passed = []
    for c in candidates:
        name = c["project_name"]
        name_lower = name.lower().strip()

        # Length guard
        if len(name) <= 2:
            logger.debug("[%s] fast_filter: '%s' too short", scan_id, name)
            continue

        # All-numeric guard
        if name.replace(" ", "").isdigit():
            logger.debug("[%s] fast_filter: '%s' all-numeric", scan_id, name)
            continue

        # Hard noise words
        if name_lower in _HARD_NOISE_WORDS:
            logger.debug("[%s] fast_filter: '%s' hard noise", scan_id, name)
            continue

        # Noise phrases
        if name_lower in _NOISE_PHRASES:
            logger.debug("[%s] fast_filter: '%s' noise phrase", scan_id, name)
            continue

        # Known live token blocklist
        if name_lower in _KNOWN_LIVE_TOKENS:
            logger.debug("[%s] fast_filter: '%s' known live token", scan_id, name)
            continue

        # Single CamelCase/Title word that looks like a common English word
        # e.g. "Risk", "Return", "Grind", "Those", "Onboarded"
        # Exempted: whitelist and words with numbers/special chars
        if (name_lower not in _WHITELIST
                and len(name.split()) == 1
                and len(name) <= 10
                and _re.match(r'^[A-Z][a-z]+$', name)):
            # Heuristic: if it appears in English dictionary patterns, skip
            # (ends in common suffixes, or is purely generic)
            common_suffixes = ("ed", "ing", "ion", "tion", "ism", "ist",
                               "ness", "ment", "ble", "ful", "ous")
            if any(name_lower.endswith(s) for s in common_suffixes):
                logger.debug("[%s] fast_filter: '%s' common English word",
                             scan_id, name)
                continue

        # DB research cache: already confirmed token-live
        cached = db.get_research_cache(name)
        if cached and cached.get("has_token"):
            logger.info("[%s] fast_filter: '%s' token already live (cache)",
                        scan_id, name)
            continue

        passed.append(c)

    dropped = len(candidates) - len(passed)
    logger.info("[%s] fast_filter_stage: %d passed, %d dropped",
                scan_id, len(passed), dropped)
    return passed


def research_stage(candidates: list[dict], scan_id: str) -> list[dict]:
    """
    Stage 3 — Deep research each candidate using APIs.
    Attaches research result to each candidate dict.
    Skips candidates whose token is already live.
    """
    logger.info("[%s] ── research_stage (%d candidates) ──",
                scan_id, len(candidates))

    enriched = []
    for c in candidates:
        name = c["project_name"]
        tweet_text = c["tweet"].get("text", "")

        try:
            logger.info("[%s] researching '%s' (via @%s)",
                        scan_id, name, c["handle"])
            # v0.9.6: if tweet is a numbered list with multiple projects,
            # research each project in isolation to prevent tech contamination.
            # Detect: tweet has 3+ numbered items AND name appears as a list item
            import re as _re
            list_items = _re.findall(
                r'^\s*\d+[\.\)]\s+(\S+)', tweet_text, _re.MULTILINE
            )
            is_list_tweet = len(list_items) >= 3
            research_text = "" if is_list_tweet else tweet_text
            project = research_project(name, tweet_text=research_text)
            project["mentioned_by"] = c["handle"]
            project["tweet_url"] = c["tweet"].get("url", "")
            project["tweet_text"] = tweet_text[:500]

            if project.get("has_token"):
                logger.info("[%s] '%s' — token live, skipping", scan_id, name)
                # Still persist to DB so /newprojects shows it as token-live
                db.upsert_project(
                    name=name,
                    mentioned_by=c["handle"],
                    tweet_url=c["tweet"].get("url", ""),
                    tweet_text=c["tweet"].get("text", "")[:500],
                    has_token=True,
                )
                continue

            c["project"] = project
            enriched.append(c)
            time.sleep(1)  # gentle pacing between API calls

        except Exception as exc:
            logger.error("[%s] research error for '%s': %s",
                         scan_id, name, exc)

    logger.info("[%s] research_stage complete — %d enriched",
                scan_id, len(enriched))
    return enriched


def score_stage(candidates: list[dict], scan_id: str) -> list[dict]:
    """
    Stage 4 — Score each researched candidate using Zun Method.
    Persists project + score to DB.
    Attaches score_result and project_id to candidate dict.
    """
    logger.info("[%s] ── score_stage (%d candidates) ──",
                scan_id, len(candidates))

    scored = []
    for c in candidates:
        project = c["project"]
        name = c["project_name"]
        tier = c["tier"]

        try:
            score_result = score_project(
                project,
                caller_tier=tier,
                caller_weight=c.get("weight", 1.0),
            )

            # Persist project to DB
            project_extras = {
                k: v for k, v in project.items()
                if k not in ("name", "mentioned_by", "tweet_url",
                             "tweet_text", "research_notes",
                             "novel_tech", "source_record")
            }
            if project.get("novel_tech"):
                project_extras["description"] = (
                    project_extras.get("description", "") +
                    " [tech:" + ",".join(project["novel_tech"]) + "]"
                )

            project_id = db.upsert_project(
                name=name,
                mentioned_by=c["handle"],
                tweet_url=c["tweet"].get("url", ""),
                tweet_text=c["tweet"].get("text", "")[:500],
                **project_extras,
            )
            db.save_score(
                project_id=project_id,
                score=score_result.score,
                breakdown=json.dumps(score_result.breakdown),
                label=score_result.label,
            )

            c["project_id"] = project_id
            c["score_result"] = score_result
            scored.append(c)

            logger.info("[%s] scored '%s': %.1f/10 [%s]",
                        scan_id, name, score_result.score, score_result.label)


        except Exception as exc:
            logger.error("[%s] score error for '%s': %s", scan_id, name, exc)

    logger.info("[%s] score_stage complete — %d scored", scan_id, len(scored))

    # v0.9.4: auto-promotion disabled until cross-mention tracking is
    # properly scoped to per-project per-account matching.
    # Manual promotion via /promote_watchlist still works.
    # TODO: re-enable when record_cross_mention() is properly called
    # only when a probation account's own tweet mentions a project
    # that an active account also mentions.
    return scored


def alert_stage(candidates: list[dict], scan_id: str) -> int:
    """
    Stage 5 — Send Telegram alerts for qualifying projects.
    Returns count of alerts sent.
    """
    logger.info("[%s] ── alert_stage (%d candidates) ──",
                scan_id, len(candidates))

    genesis_count = 0

    for c in candidates:
        project = c["project"]
        project_id = c["project_id"]
        score_result = c["score_result"]
        name = c["project_name"]

        if score_result.score < settings.GENESIS_THRESHOLD:
            continue
        if db.already_alerted(project_id):
            logger.debug("[%s] '%s' already alerted — skipping",
                         scan_id, name)
            continue
        if db.alerts_today() >= settings.MAX_ALERTS_PER_DAY:
            logger.warning("[%s] MAX_ALERTS_PER_DAY (%d) reached",
                           scan_id, settings.MAX_ALERTS_PER_DAY)
            break

        try:
            action_plan = generate_action_plan(project, score_result)
            message = format_action_plan_for_telegram(
                project, score_result, action_plan
            )

            success = send_message(message)
            if success:
                db.log_alert(project_id, "genesis")
                genesis_count += 1
                logger.info(
                    "[%s] 🌟 genesis alert sent: '%s' (%.1f/10)",
                    scan_id, name, score_result.score
                )

                # Auto-add per-wallet tasks (v0.9)
                from modules.action_planner import generate_wallet_tasks_for_db
                wallet_tasks = generate_wallet_tasks_for_db(
                    project, project_id, action_plan
                )
                for wt in wallet_tasks:
                    db.add_grind_task(**wt)

        except Exception as exc:
            logger.error("[%s] alert error for '%s': %s",
                         scan_id, name, exc)

    logger.info("[%s] alert_stage complete — %d alerts sent",
                scan_id, genesis_count)
    return genesis_count


# ══════════════════════════════════════════════════════════════════════════
# MAIN SCAN CYCLE (v0.3 — orchestrates stages)
# ══════════════════════════════════════════════════════════════════════════

def run_scan_cycle():
    """Full scan: orchestrates all 5 pipeline stages."""
    scan_id = _make_scan_id()

    logger.info("=" * 60)
    logger.info("🎯 Alpha Hunter Scan Starting [%s]", scan_id)
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

    # ── Run stages sequentially ────────────────────────────────────────────
    candidates = extract_stage(watchlist, scan_id)
    candidates = fast_filter_stage(candidates, scan_id)
    candidates = research_stage(candidates, scan_id)
    candidates = score_stage(candidates, scan_id)
    genesis_count = alert_stage(candidates, scan_id)

    logger.info("=" * 60)
    logger.info("✅ [%s] Scan complete — %d genesis calls", scan_id, genesis_count)
    logger.info("=" * 60)

    # Notify watchdog that scan completed successfully
    try:
        from modules.watchdog import record_heartbeat
        record_heartbeat()
    except Exception:
        pass  # watchdog is optional — never let it break the pipeline


# ══════════════════════════════════════════════════════════════════════════
# MANUAL RESEARCH (Telegram /research command — backward compat)
# ══════════════════════════════════════════════════════════════════════════

def process_tweet(tweet: dict, caller_tier: int = 2,
                  caller_weight: float = 1.0) -> list[dict]:
    """
    Process a single tweet through a mini pipeline.
    Retained for backward compatibility — used by research_tweet_url().
    """
    text = tweet.get("text", "")
    handle = tweet.get("handle", "unknown")
    tweet_url = tweet.get("url", "")
    tweet_id = tweet.get("id", "")

    if is_seen_tweet(tweet_url, text):
        return []

    tweet_quality = score_tweet_quality(text, handle, caller_tier)
    if tweet_quality < 0.2:
        return []

    project_names = extract_project_names(text, tweet_id=tweet_id)
    if not project_names:
        return []

    logger.info("@%s mentioned %d project(s): %s",
                handle, len(project_names), project_names)

    results = []
    for name in project_names[:settings.MAX_PROJECTS_PER_TWEET]:
        try:
            project = research_project(name, tweet_text=text)
            project["mentioned_by"] = handle
            project["tweet_url"] = tweet_url
            project["tweet_text"] = text[:500]

            if project.get("has_token"):
                logger.info("Skipping %s — token already live", name)
                continue

            score_result = score_project(
                project,
                caller_tier=caller_tier,
                caller_weight=caller_weight,
            )

            project_extras = {
                k: v for k, v in project.items()
                if k not in ("name", "mentioned_by", "tweet_url",
                             "tweet_text", "research_notes",
                             "novel_tech", "source_record")
            }
            if project.get("novel_tech"):
                project_extras["description"] = (
                    project_extras.get("description", "") +
                    " [tech:" + ",".join(project["novel_tech"]) + "]"
                )

            project_id = db.upsert_project(
                name=name,
                mentioned_by=handle,
                tweet_url=tweet_url,
                tweet_text=text[:500],
                **project_extras,
            )
            db.save_score(
                project_id=project_id,
                score=score_result.score,
                breakdown=json.dumps(score_result.breakdown),
                label=score_result.label,
            )
            results.append({
                "project": project,
                "project_id": project_id,
                "score_result": score_result,
            })
            time.sleep(1)

        except Exception as exc:
            logger.error("Error processing project '%s': %s", name, exc)

    return results


def research_tweet_url(tweet_url: str, chat_id: str = None):
    """
    Manual pipeline: research a specific tweet URL forwarded by user.
    Called when user sends /research <url> on Telegram.
    """
    send_message("🔍 Fetching tweet...", chat_id=chat_id)

    tweet = fetch_tweet_from_url(tweet_url)
    if not tweet:
        send_message(
            "❌ Could not fetch that tweet. Try pasting the text directly.",
            chat_id=chat_id
        )
        return

    send_message("📝 Tweet found. Extracting projects...", chat_id=chat_id)

    results = process_tweet(tweet, caller_tier=1)

    if not results:
        send_message(
            "🔍 No new projects found in that tweet, or all projects already have tokens.",
            chat_id=chat_id
        )
        return

    for item in results:
        project = item["project"]
        score_result = item["score_result"]
        action_plan = generate_action_plan(project, score_result)
        message = format_action_plan_for_telegram(project, score_result, action_plan)
        send_message(message, chat_id=chat_id)


# ══════════════════════════════════════════════════════════════════════════
# DISCOVERY / FUNDING / GITHUB CYCLES (unchanged — pass-through)
# ══════════════════════════════════════════════════════════════════════════


def run_backfill_cycle(max_tweets: int = 50):
    """
    Historical backfill — processes the last N tweets from every watchlist
    account through the full pipeline.

    Use this when:
      - You just added a new account to the watchlist
      - Alpha Hunter was offline and you missed recent posts
      - You want to catch projects mentioned before this bot was running

    Called via: python main.py --backfill [--backfill-count N]
    """
    from modules.x_monitor import backfill_all_accounts

    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("⏮️  Backfill Starting [%s] (last %d tweets per account)",
                scan_id, max_tweets)
    logger.info("=" * 60)

    watchlist = _load_watchlist()
    if not watchlist:
        logger.warning("[%s] Watchlist is empty!", scan_id)
        return

    for acc in watchlist:
        db.upsert_account(
            handle=acc.get("handle", ""),
            name=acc.get("name", ""),
            tier=acc.get("tier", 2),
            trusted=acc.get("trusted", False),
            notes=acc.get("notes", ""),
        )

    # Fetch historical tweets (bypasses since_id)
    all_tweets = backfill_all_accounts(
        watchlist, max_tweets=max_tweets, scan_id=scan_id
    )

    if not all_tweets:
        logger.info("[%s] Backfill: no new historical tweets found", scan_id)
        return

    logger.info("[%s] Backfill: %d historical tweets to process", scan_id, len(all_tweets))

    # Build candidates with tier+weight
    candidates = []
    for tweet in all_tweets:
        handle = tweet.get("handle", "")
        acc = next((a for a in watchlist if a.get("handle") == handle), {})
        tier = acc.get("tier", 2)
        weight = float(acc.get("weight", 1.0))
        tweet_quality = score_tweet_quality(tweet.get("text", ""), handle, tier)
        if tweet_quality < 0.2:
            continue
        names = extract_project_names(
            tweet.get("text", ""), scan_id=scan_id, tweet_id=tweet.get("id", "")
        )
        for name in names[:settings.MAX_PROJECTS_PER_TWEET]:
            candidates.append({
                "tweet": tweet, "handle": handle,
                "tier": tier, "weight": weight,
                "project_name": name, "tweet_quality": tweet_quality,
            })

    candidates = fast_filter_stage(candidates, scan_id)
    candidates = research_stage(candidates, scan_id)
    candidates = score_stage(candidates, scan_id)
    genesis_count = alert_stage(candidates, scan_id)

    logger.info("=" * 60)
    logger.info("✅ [%s] Backfill complete — %d genesis calls", scan_id, genesis_count)
    logger.info("=" * 60)

def run_discovery_cycle():
    """Network discovery run — finds new accounts from trusted interactions."""
    from modules.account_discovery import (
        discover_accounts, save_pending_suggestion,
        format_suggestion_message
    )
    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("🔍 Network Discovery Starting [%s]", scan_id)
    logger.info("=" * 60)

    watchlist = _load_watchlist()
    candidates = discover_accounts(watchlist)

    if not candidates:
        logger.info("[%s] No new candidates found this cycle", scan_id)
        return

    for candidate in candidates:
        save_pending_suggestion(candidate)
        message = format_suggestion_message(candidate)
        send_message(message)
        time.sleep(2)

    logger.info("[%s] Discovery complete — %d suggestions sent",
                scan_id, len(candidates))


def run_funding_scan_cycle():
    """Funding scan cycle — runs every 12 hours."""
    from modules.funding_scanner import run_funding_scan

    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("💰 Funding Scan Cycle Starting [%s]", scan_id)
    logger.info("=" * 60)

    qualified = run_funding_scan(scan_id=scan_id)
    genesis_count = 0

    for item in qualified:
        project = item["project"]
        project_id = item["project_id"]
        score_result = item["score_result"]

        if (not db.already_alerted(project_id, "funding") and
                db.alerts_today() < settings.MAX_ALERTS_PER_DAY):

            action_plan = generate_action_plan(project, score_result)
            message = (
                "💰 <b>ALPHA HUNTER — FUNDING SIGNAL</b>\n"
                f"<i>Source: {project.get('source','').upper()}</i>\n\n"
            ) + format_action_plan_for_telegram(project, score_result, action_plan)

            if send_message(message):
                db.log_alert(project_id, "funding")
                genesis_count += 1

    logger.info("[%s] Funding cycle complete — %d alerts sent",
                scan_id, genesis_count)


def run_github_scan_cycle():
    """GitHub scan cycle — runs every 24 hours."""
    from modules.github_scanner import run_github_scan

    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("⚙️  GitHub Scan Cycle Starting [%s]", scan_id)
    logger.info("=" * 60)

    qualified = run_github_scan(scan_id=scan_id)
    genesis_count = 0

    for item in qualified:
        project = item["project"]
        project_id = item["project_id"]
        score_result = item["score_result"]

        if (not db.already_alerted(project_id, "github") and
                db.alerts_today() < settings.MAX_ALERTS_PER_DAY):

            action_plan = generate_action_plan(project, score_result)
            message = (
                "⚙️ <b>ALPHA HUNTER — GITHUB SIGNAL</b>\n"
                "<i>Detected on GitHub before Twitter</i>\n\n"
            ) + format_action_plan_for_telegram(project, score_result, action_plan)

            if send_message(message):
                db.log_alert(project_id, "github")
                genesis_count += 1

    logger.info("[%s] GitHub cycle complete — %d alerts sent",
                scan_id, genesis_count)

def run_coingecko_scan_cycle():
    """
    CoinGecko trending + search scan — v0.8.
    Catches projects spiking in search before they appear on Twitter.
    Runs every 6 hours.
    """
    from modules.coingecko_scanner import run_coingecko_scan

    scan_id = _make_scan_id()
    logger.info("=" * 60)
    logger.info("📈 CoinGecko Scan Cycle Starting [%s]", scan_id)
    logger.info("=" * 60)

    qualified = run_coingecko_scan(scan_id=scan_id)
    genesis_count = 0

    for item in qualified:
        project = item["project"]
        project_id = item["project_id"]
        score_result = item["score_result"]

        if (not db.already_alerted(project_id, "coingecko") and
                db.alerts_today() < settings.MAX_ALERTS_PER_DAY):

            action_plan = generate_action_plan(project, score_result)
            message = (
                "📈 <b>ALPHA HUNTER — TRENDING SIGNAL</b>\n"
                f"<i>Source: CoinGecko {project.get('source','').replace('coingecko_','').upper()}</i>\n\n"
            ) + format_action_plan_for_telegram(project, score_result, action_plan)

            if send_message(message):
                db.log_alert(project_id, "coingecko")
                genesis_count += 1

    logger.info("[%s] CoinGecko cycle complete — %d alerts sent",
                scan_id, genesis_count)
