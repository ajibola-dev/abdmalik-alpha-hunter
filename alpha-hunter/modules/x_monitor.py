"""
modules/x_monitor.py
Monitors X accounts via the official X API v2.
Uses Bearer Token authentication — no Nitter needed.
"""
import logging
import time
import re
import os
import requests
from typing import Optional
from config.settings import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.twitter.com/2"
_seen_tweet_ids: set = set()
_SESSION = requests.Session()


def _get_headers() -> dict:
    token = getattr(settings, "X_BEARER_TOKEN", "") or os.getenv("X_BEARER_TOKEN", "")
    if not token:
        logger.error("X_BEARER_TOKEN not set!")
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "AlphaHunterBot/1.0",
    }


def _get(url: str, params: dict = None) -> Optional[dict]:
    headers = _get_headers()
    if not headers:
        return None
    try:
        r = _SESSION.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            logger.warning("X API rate limited — sleeping 60s")
            time.sleep(60)
            return None
        if r.status_code == 401:
            logger.error("X API 401 Unauthorized — check Bearer Token")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("X API request failed: %s", exc)
        return None


def get_user_id(handle: str) -> Optional[str]:
    """Look up a Twitter user ID by handle."""
    data = _get(f"{_API_BASE}/users/by/username/{handle}")
    if data and "data" in data:
        return data["data"]["id"]
    logger.warning("Could not find user ID for @%s", handle)
    return None


def fetch_recent_tweets(handle: str, max_results: int = 10) -> list[dict]:
    """
    Fetch recent tweets from a user via X API v2.
    Returns list of {handle, text, url, id, date}
    """
    user_id = get_user_id(handle)
    if not user_id:
        return []

    params = {
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,text",
        "exclude": "retweets,replies",
    }

    data = _get(f"{_API_BASE}/users/{user_id}/tweets", params=params)
    if not data or "data" not in data:
        logger.warning("No tweets returned for @%s", handle)
        return []

    tweets = []
    for tweet in data["data"]:
        tweet_id = tweet["id"]
        if tweet_id in _seen_tweet_ids:
            continue
        _seen_tweet_ids.add(tweet_id)

        tweets.append({
            "handle": handle,
            "text": tweet["text"],
            "url": f"https://twitter.com/{handle}/status/{tweet_id}",
            "id": tweet_id,
            "date": tweet.get("created_at", ""),
        })

    if tweets:
        logger.info("Fetched %d new tweets from @%s", len(tweets), handle)
    else:
        logger.info("No new tweets from @%s (all seen or none posted)", handle)

    return tweets


def fetch_tweet_from_url(tweet_url: str) -> Optional[dict]:
    """Fetch a single tweet by URL — for manual /research command."""
    match = re.search(r'/status/(\d+)', tweet_url)
    if not match:
        return None

    tweet_id = match.group(1)
    data = _get(f"{_API_BASE}/tweets/{tweet_id}",
                params={"tweet.fields": "created_at,text"})
    if not data or "data" not in data:
        return None

    tweet = data["data"]
    return {
        "handle": "manual_forward",
        "text": tweet["text"],
        "url": tweet_url,
        "id": tweet_id,
        "date": tweet.get("created_at", "manual"),
    }


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
        time.sleep(3)  # Respect rate limits

    logger.info("Total new tweets: %d from %d accounts",
                len(all_tweets), len(watchlist))
    return all_tweets
