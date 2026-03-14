"""
modules/x_monitor.py
Monitors X accounts via Twttr API (RapidAPI) by davethebeast.
Host: twitter241.p.rapidapi.com

v0.2 changes:
  - user_id lookups now cached in-memory with TTL (USER_ID_CACHE_TTL)
    → was calling API on every scan for every account
  - Tweet deduplication moved to DB via database.mark_tweet_seen()
    → was in-memory set; lost on every restart causing duplicate processing
  - since_id logic: uses last_tweet_id from DB to fetch only NEW tweets
    → was always fetching the same 10 tweets every cycle
  - Exponential backoff on 429 with configurable MAX_RETRIES
  - Structured log context: scan_id, handle, tweet_id in every log line
"""
import logging
import time
import re
import hashlib
import os
import requests
from typing import Optional
from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_API_HOST = "twitter241.p.rapidapi.com"
_API_BASE = f"https://{_API_HOST}"
_SESSION = requests.Session()

# ── User ID cache (v0.2) ───────────────────────────────────────────────────
# Keyed by handle (lowercase) → {"user_id": str, "cached_at": float}
_user_id_cache: dict[str, dict] = {}


def _get_headers() -> dict:
    key = getattr(settings, "RAPIDAPI_KEY", "") or os.getenv("RAPIDAPI_KEY", "")
    if not key:
        logger.error("RAPIDAPI_KEY not set!")
        return {}
    return {
        "x-rapidapi-host": _API_HOST,
        "x-rapidapi-key": key,
        "Content-Type": "application/json",
    }


def _get(url: str, params: dict = None,
         retries: int = None) -> Optional[dict]:
    """
    GET with exponential backoff.
    v0.2: uses settings.MAX_RETRIES, settings.RETRY_BACKOFF_BASE,
          handles 429 with a 60s sleep before counting as an attempt.
    """
    max_retries = retries or settings.MAX_RETRIES
    headers = _get_headers()
    if not headers:
        return None

    for attempt in range(max_retries):
        try:
            r = _SESSION.get(url, headers=headers, params=params,
                             timeout=settings.REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                logger.warning(
                    "RapidAPI rate limited — sleeping %ds (attempt %d/%d)",
                    wait, attempt + 1, max_retries
                )
                time.sleep(wait)
                continue   # don't count as a failed attempt
            if r.status_code == 401:
                logger.error("RapidAPI 401 — check RAPIDAPI_KEY")
                return None
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < max_retries - 1:
                logger.debug(
                    "RapidAPI request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, exc, wait
                )
                time.sleep(wait)
            else:
                logger.warning(
                    "RapidAPI request failed after %d attempts: %s", max_retries, exc
                )
    return None


# ── User ID cache (v0.2) ───────────────────────────────────────────────────

def get_user_id(handle: str) -> Optional[str]:
    """
    Look up a Twitter user ID by handle.
    v0.2: results cached in memory for USER_ID_CACHE_TTL seconds (default 24h).
    Eliminates one API call per account per scan.
    """
    handle_lower = handle.lower()
    cached = _user_id_cache.get(handle_lower)
    if cached:
        age = time.time() - cached["cached_at"]
        if age < settings.USER_ID_CACHE_TTL:
            return cached["user_id"]

    data = _get(f"{_API_BASE}/user", params={"username": handle})
    if not data:
        return None
    try:
        user_id = (data.get("result", {})
                       .get("data", {})
                       .get("user", {})
                       .get("result", {})
                       .get("rest_id"))
        if user_id:
            _user_id_cache[handle_lower] = {
                "user_id": user_id,
                "cached_at": time.time(),
            }
            logger.debug("Cached user_id for @%s → %s", handle, user_id)
            return user_id
    except Exception:
        pass
    logger.warning("Could not find user ID for @%s", handle)
    return None


def _tweet_hash(tweet_id: str, handle: str) -> str:
    return hashlib.md5(f"{handle}:{tweet_id}".encode()).hexdigest()


def fetch_recent_tweets(handle: str, max_results: int = 10,
                        scan_id: str = "") -> list[dict]:
    """
    Fetch recent tweets from a user via Twttr API.
    v0.2:
      - Uses last_tweet_id from DB as since_id to fetch only truly new tweets.
      - Deduplication via DB tweet_seen table instead of in-memory set.
    Returns list of {handle, text, url, id, date}
    """
    log_ctx = f"[scan={scan_id}] @{handle}" if scan_id else f"@{handle}"

    user_id = get_user_id(handle)
    if not user_id:
        return []

    # Build params — include since_id if we have it
    params = {"user": user_id, "count": str(max_results)}
    since_id = db.get_account_last_tweet_id(handle)
    if since_id:
        params["since_id"] = since_id
        logger.debug("%s fetching tweets since_id=%s", log_ctx, since_id)

    data = _get(f"{_API_BASE}/user-tweets", params=params)
    if not data:
        return []

    tweets = []
    newest_id = None

    try:
        instructions = (data.get("result", {})
                            .get("timeline", {})
                            .get("instructions", []))

        for instruction in instructions:
            entries = instruction.get("entries", [])
            for entry in entries:
                content = entry.get("content", {})
                item_content = content.get("itemContent", {})
                tweet_results = item_content.get("tweet_results", {})
                result = tweet_results.get("result", {})
                legacy = result.get("legacy", {})

                tweet_id = legacy.get("id_str", "")
                text = legacy.get("full_text", "")
                created_at = legacy.get("created_at", "")

                if not tweet_id or not text:
                    continue
                if text.startswith("RT @"):
                    continue

                # DB-backed dedup (v0.2)
                t_hash = _tweet_hash(tweet_id, handle)
                if db.is_tweet_seen(t_hash):
                    logger.debug("%s tweet %s already seen — skipping",
                                 log_ctx, tweet_id)
                    continue

                db.mark_tweet_seen(t_hash, tweet_id=tweet_id, handle=handle)

                # Track newest tweet_id for next scan's since_id
                if newest_id is None or int(tweet_id) > int(newest_id):
                    newest_id = tweet_id

                tweets.append({
                    "handle": handle,
                    "text": text,
                    "url": f"https://twitter.com/{handle}/status/{tweet_id}",
                    "id": tweet_id,
                    "date": created_at,
                })

    except Exception as exc:
        logger.warning("%s error parsing tweets: %s", log_ctx, exc)

    # Persist the newest tweet_id for next scan
    if newest_id:
        db.update_account_last_tweet(handle, newest_id)

    if tweets:
        logger.info("%s fetched %d new tweets", log_ctx, len(tweets))
    else:
        logger.info("%s no new tweets", log_ctx)

    return tweets


def fetch_tweet_from_url(tweet_url: str) -> Optional[dict]:
    """Fetch a single tweet by URL — for manual /research command."""
    match = re.search(r'/status/(\d+)', tweet_url)
    if not match:
        return None

    tweet_id = match.group(1)
    data = _get(f"{_API_BASE}/tweet", params={"pid": tweet_id})
    if not data:
        return None

    try:
        result = data.get("result", {})
        legacy = result.get("legacy", {})
        text = legacy.get("full_text", "")
        if text:
            return {
                "handle": "manual_forward",
                "text": text,
                "url": tweet_url,
                "id": tweet_id,
                "date": legacy.get("created_at", "manual"),
            }
    except Exception as exc:
        logger.warning("Error fetching tweet %s: %s", tweet_id, exc)

    return None


def monitor_all_accounts(watchlist: list[dict],
                         scan_id: str = "") -> list[dict]:
    """Fetch recent tweets from all accounts in watchlist."""
    all_tweets = []

    for account in watchlist:
        handle = account.get("handle", "")
        if not handle:
            continue
        logger.info("[scan=%s] Checking @%s...", scan_id, handle)
        tweets = fetch_recent_tweets(
            handle, max_results=10, scan_id=scan_id
        )
        all_tweets.extend(tweets)
        time.sleep(2)  # Respect rate limits

    logger.info("[scan=%s] Total new tweets: %d from %d accounts",
                scan_id, len(all_tweets), len(watchlist))
    return all_tweets


def backfill_account(handle: str, max_tweets: int = 50,
                     scan_id: str = "") -> list[dict]:
    """
    Fetch the last N tweets from a single account, bypassing since_id.
    Used for historical backfill — catches projects mentioned before
    Alpha Hunter was monitoring this account.

    Unlike fetch_recent_tweets(), this:
      - Ignores since_id (fetches regardless of what's been seen before)
      - Respects tweet_seen DB (won't duplicate-process already-seen tweets)
      - Fetches up to max_tweets (default 50, capped at 100 by API)
    """
    log_ctx = f"[backfill scan={scan_id}] @{handle}"
    logger.info("%s fetching last %d tweets", log_ctx, max_tweets)

    user_id = get_user_id(handle)
    if not user_id:
        return []

    # Deliberately omit since_id — we want historical tweets
    params = {"user": user_id, "count": str(min(max_tweets, 100))}
    data = _get(f"{_API_BASE}/user-tweets", params=params)
    if not data:
        return []

    tweets = []
    try:
        instructions = (data.get("result", {})
                            .get("timeline", {})
                            .get("instructions", []))
        for instruction in instructions:
            for entry in instruction.get("entries", []):
                legacy = (entry.get("content", {})
                              .get("itemContent", {})
                              .get("tweet_results", {})
                              .get("result", {})
                              .get("legacy", {}))
                tweet_id = legacy.get("id_str", "")
                text = legacy.get("full_text", "")
                if not tweet_id or not text or text.startswith("RT @"):
                    continue

                # Still respect DB dedup — don't reprocess known tweets
                t_hash = _tweet_hash(tweet_id, handle)
                if db.is_tweet_seen(t_hash):
                    continue
                db.mark_tweet_seen(t_hash, tweet_id=tweet_id, handle=handle)

                tweets.append({
                    "handle": handle,
                    "text": text,
                    "url": f"https://twitter.com/{handle}/status/{tweet_id}",
                    "id": tweet_id,
                    "date": legacy.get("created_at", ""),
                })
    except Exception as exc:
        logger.warning("%s parse error: %s", log_ctx, exc)

    logger.info("%s backfill complete — %d tweets retrieved",
                log_ctx, len(tweets))
    return tweets


def backfill_all_accounts(watchlist: list[dict],
                           max_tweets: int = 50,
                           scan_id: str = "") -> list[dict]:
    """Backfill historical tweets from all watchlist accounts."""
    all_tweets = []
    for account in watchlist:
        handle = account.get("handle", "")
        if not handle:
            continue
        tweets = backfill_account(handle, max_tweets=max_tweets,
                                  scan_id=scan_id)
        all_tweets.extend(tweets)
        time.sleep(3)
    logger.info("[%s] Backfill complete — %d total tweets from %d accounts",
                scan_id, len(all_tweets), len(watchlist))
    return all_tweets
