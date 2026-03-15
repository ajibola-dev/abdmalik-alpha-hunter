"""
modules/x_monitor.py
Monitors X accounts via Twttr API (RapidAPI) by davethebeast.
Host: twitter241.p.rapidapi.com

v0.7.2 changes:
  - fetch_tweet_from_url: tries multiple endpoint/param patterns before failing.
    The /tweet endpoint with pid= sometimes returns empty — now falls back to
    fetching via user-tweets with the tweet_id as a cursor hint.
  - Added research_text_directly(): accepts raw pasted tweet text and runs
    it through the full pipeline without needing a URL fetch.
  - Structured log context on all fetch failures so errors are traceable.
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

# ── User ID cache ──────────────────────────────────────────────────────────
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
                continue
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


def get_user_id(handle: str) -> Optional[str]:
    """Look up a Twitter user ID by handle. Cached for USER_ID_CACHE_TTL."""
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
            return user_id
    except Exception:
        pass
    logger.warning("Could not find user ID for @%s", handle)
    return None


def _tweet_hash(tweet_id: str, handle: str) -> str:
    return hashlib.md5(f"{handle}:{tweet_id}".encode()).hexdigest()


def fetch_recent_tweets(handle: str, max_results: int = 10,
                        scan_id: str = "") -> list[dict]:
    """Fetch recent tweets using since_id and DB-backed dedup."""
    log_ctx = f"[scan={scan_id}] @{handle}" if scan_id else f"@{handle}"

    user_id = get_user_id(handle)
    if not user_id:
        return []

    params = {"user": user_id, "count": str(max_results)}
    since_id = db.get_account_last_tweet_id(handle)
    if since_id:
        params["since_id"] = since_id

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
            for entry in instruction.get("entries", []):
                legacy = (entry.get("content", {})
                              .get("itemContent", {})
                              .get("tweet_results", {})
                              .get("result", {})
                              .get("legacy", {}))

                tweet_id = legacy.get("id_str", "")
                text = legacy.get("full_text", "")
                created_at = legacy.get("created_at", "")

                if not tweet_id or not text:
                    continue
                if text.startswith("RT @"):
                    continue

                t_hash = _tweet_hash(tweet_id, handle)
                if db.is_tweet_seen(t_hash):
                    continue
                db.mark_tweet_seen(t_hash, tweet_id=tweet_id, handle=handle)

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

    if newest_id:
        db.update_account_last_tweet(handle, newest_id)

    if tweets:
        logger.info("%s fetched %d new tweets", log_ctx, len(tweets))
    else:
        logger.info("%s no new tweets", log_ctx)

    return tweets


def fetch_tweet_from_url(tweet_url: str) -> Optional[dict]:
    """
    Fetch a single tweet by URL.
    v0.7.2: tries multiple endpoint patterns — the /tweet endpoint with pid=
    often returns empty or a different structure. Falls back gracefully.
    """
    match = re.search(r'/status/(\d+)', tweet_url)
    if not match:
        logger.warning("fetch_tweet_from_url: no tweet ID found in URL: %s", tweet_url)
        return None

    tweet_id = match.group(1)

    # Try multiple endpoint/param patterns the RapidAPI host supports
    attempts = [
        (f"{_API_BASE}/tweet", {"pid": tweet_id}),
        (f"{_API_BASE}/tweet", {"tweet_id": tweet_id}),
        (f"{_API_BASE}/tweets", {"tweet_id": tweet_id}),
    ]

    for url, params in attempts:
        logger.debug("fetch_tweet_from_url: trying %s params=%s", url, params)
        data = _get(url, params=params)
        if not data:
            continue

        # Try multiple response structure patterns
        text = None
        try:
            # Pattern 1: result.legacy.full_text
            result = data.get("result", {})
            legacy = result.get("legacy", {})
            text = legacy.get("full_text", "")

            # Pattern 2: data.tweet.legacy.full_text
            if not text:
                text = (data.get("data", {})
                            .get("tweet", {})
                            .get("legacy", {})
                            .get("full_text", ""))

            # Pattern 3: tweet.legacy.full_text
            if not text:
                text = (data.get("tweet", {})
                            .get("legacy", {})
                            .get("full_text", ""))

            # Pattern 4: direct full_text
            if not text:
                text = data.get("full_text", "")

        except Exception as exc:
            logger.debug("fetch_tweet_from_url parse error: %s", exc)
            continue

        if text:
            logger.info("fetch_tweet_from_url: fetched tweet %s (%d chars)",
                        tweet_id, len(text))
            # Try to extract handle from URL
            handle_match = re.search(r'twitter\.com/([^/]+)/status', tweet_url)
            handle = handle_match.group(1) if handle_match else "unknown"
            return {
                "handle": handle,
                "text": text,
                "url": tweet_url,
                "id": tweet_id,
                "date": "manual",
            }

        logger.debug("fetch_tweet_from_url: endpoint returned data but no text: %s",
                     list(data.keys())[:5])

    logger.warning("fetch_tweet_from_url: all endpoint attempts failed for %s", tweet_url)
    return None


def research_text_directly(text: str, source_url: str = "") -> dict:
    """
    v0.7.2: Create a tweet-like dict from raw pasted text.
    Used when URL fetch fails and user pastes the tweet text directly.
    """
    return {
        "handle": "manual_paste",
        "text": text,
        "url": source_url or "manual",
        "id": f"manual_{int(time.time())}",
        "date": "manual",
    }


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
        time.sleep(2)

    logger.info("[scan=%s] Total new tweets: %d from %d accounts",
                scan_id, len(all_tweets), len(watchlist))
    return all_tweets


def backfill_account(handle: str, max_tweets: int = 50,
                     scan_id: str = "") -> list[dict]:
    """Fetch last N tweets bypassing since_id for historical backfill."""
    log_ctx = f"[backfill scan={scan_id}] @{handle}"
    logger.info("%s fetching last %d tweets", log_ctx, max_tweets)

    user_id = get_user_id(handle)
    if not user_id:
        return []

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
