"""
modules/x_monitor.py
Monitors X accounts via Twttr API (RapidAPI) by davethebeast.
Host: twitter241.p.rapidapi.com
"""
import logging
import time
import re
import os
import requests
from typing import Optional
from config.settings import settings

logger = logging.getLogger(__name__)

_API_HOST = "twitter241.p.rapidapi.com"
_API_BASE = f"https://{_API_HOST}"
_seen_tweet_ids: set = set()
_SESSION = requests.Session()


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


def _get(url: str, params: dict = None) -> Optional[dict]:
    headers = _get_headers()
    if not headers:
        return None
    try:
        r = _SESSION.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            logger.warning("RapidAPI rate limited — sleeping 60s")
            time.sleep(60)
            return None
        if r.status_code == 401:
            logger.error("RapidAPI 401 — check RAPIDAPI_KEY")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("RapidAPI request failed: %s", exc)
        return None


def get_user_id(handle: str) -> Optional[str]:
    """Look up a Twitter user ID by handle."""
    data = _get(f"{_API_BASE}/user", params={"username": handle})
    if not data:
        return None
    try:
        # Response structure: data.result.data.user.result.rest_id
        user_id = (data.get("result", {})
                      .get("data", {})
                      .get("user", {})
                      .get("result", {})
                      .get("rest_id"))
        if user_id:
            return user_id
    except Exception:
        pass
    logger.warning("Could not find user ID for @%s", handle)
    return None


def fetch_recent_tweets(handle: str, max_results: int = 10) -> list[dict]:
    """
    Fetch recent tweets from a user via Twttr API.
    Returns list of {handle, text, url, id, date}
    """
    user_id = get_user_id(handle)
    if not user_id:
        return []

    data = _get(f"{_API_BASE}/user-tweets",
                params={"user": user_id, "count": str(max_results)})
    if not data:
        return []

    tweets = []
    try:
        # Navigate the Twitter GraphQL response structure
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
                if tweet_id in _seen_tweet_ids:
                    continue
                # Skip retweets
                if text.startswith("RT @"):
                    continue

                _seen_tweet_ids.add(tweet_id)
                tweets.append({
                    "handle": handle,
                    "text": text,
                    "url": f"https://twitter.com/{handle}/status/{tweet_id}",
                    "id": tweet_id,
                    "date": created_at,
                })
    except Exception as exc:
        logger.warning("Error parsing tweets for @%s: %s", handle, exc)

    if tweets:
        logger.info("Fetched %d new tweets from @%s", len(tweets), handle)
    else:
        logger.info("No new tweets from @%s", handle)

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


def monitor_all_accounts(watchlist: list[dict]) -> list[dict]:
    """Fetch recent tweets from all accounts in watchlist."""
    all_tweets = []

    for account in watchlist:
        handle = account.get("handle", "")
        if not handle:
            continue
        logger.info("Checking @%s...", handle)
        tweets = fetch_recent_tweets(handle, max_results=10)
        all_tweets.extend(tweets)
        time.sleep(2)  # Respect rate limits

    logger.info("Total new tweets: %d from %d accounts",
                len(all_tweets), len(watchlist))
    return all_tweets
