"""
modules/x_monitor.py
Monitors X accounts via Nitter scraping.
No API key needed. Rotates across multiple Nitter instances.
Also handles manual tweet URL forwards from Telegram.
"""
import logging
import time
import random
import re
from typing import Optional
import requests
from bs4 import BeautifulSoup
from config.settings import settings

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

# ── Nitter instance health check ──────────────────────────────────────────
_healthy_instances: list[str] = []
_last_health_check: float = 0


def _get_healthy_instance() -> str | None:
    """Returns a working Nitter instance, checking health every 30 mins."""
    global _healthy_instances, _last_health_check
    import time as _time

    if _healthy_instances and (_time.time() - _last_health_check) < 1800:
        import random
        return random.choice(_healthy_instances)

    # Re-check all instances
    _healthy_instances = []
    for instance in settings.NITTER_INSTANCES:
        try:
            r = _SESSION.get(f"{instance}/twitter", timeout=8)
            if r.status_code == 200:
                _healthy_instances.append(instance)
                logger.debug("Nitter healthy: %s", instance)
        except Exception:
            logger.debug("Nitter down: %s", instance)

    _last_health_check = _time.time()

    if not _healthy_instances:
        logger.warning("All Nitter instances are down!")
        return None

    logger.info("Healthy Nitter instances: %d/%d",
                len(_healthy_instances), len(settings.NITTER_INSTANCES))
    import random
    return random.choice(_healthy_instances)
_SESSION.headers.update(settings.REQUEST_HEADERS)


def _get(url: str, retries: int = 3) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, timeout=settings.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == retries - 1:
                logger.warning("GET %s failed: %s", url, exc)
            time.sleep(2 ** attempt)
    return None


def _nitter_url(handle: str, instance: str = None) -> str:
    """Build a Nitter profile URL for a given handle."""
    instance = instance or random.choice(settings.NITTER_INSTANCES)
    return f"{instance}/{handle}"


def fetch_recent_tweets(handle: str, max_tweets: int = 20) -> list[dict]:
    """
    Scrape recent tweets from a user's Nitter profile.
    Returns list of {text, url, date, handle}
    """
    tweets = []

    for instance in settings.NITTER_INSTANCES:
        url = _nitter_url(handle, instance)
        resp = _get(url)
        if resp is None:
            continue

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            tweet_items = soup.select(".timeline-item, .tweet-content, [class*='timeline']")

            if not tweet_items:
                # Try alternate selectors
                tweet_items = soup.select("div.tweet")

            for item in tweet_items[:max_tweets]:
                text_el = item.select_one(".tweet-content, .content")
                date_el = item.select_one(".tweet-date a, time")
                link_el = item.select_one(".tweet-link, a[href*='/status/']")

                text = text_el.get_text(strip=True) if text_el else ""
                date = date_el.get("title", date_el.get_text(strip=True)) if date_el else ""
                link = link_el.get("href", "") if link_el else ""

                if not link.startswith("http"):
                    link = f"https://twitter.com{link}"

                if text:
                    tweets.append({
                        "handle": handle,
                        "text": text,
                        "url": link,
                        "date": date,
                    })

            if tweets:
                logger.info("Fetched %d tweets from @%s via %s", len(tweets), handle, instance)
                return tweets

        except Exception as exc:
            logger.debug("Nitter parse error (%s): %s", instance, exc)
            continue

    logger.warning("Could not fetch tweets for @%s from any Nitter instance", handle)
    return []


def fetch_tweet_from_url(tweet_url: str) -> Optional[dict]:
    """
    Fetch a single tweet from a URL.
    Converts twitter.com or x.com URLs to Nitter for scraping.
    """
    # Convert to Nitter URL
    for domain in ["twitter.com", "x.com"]:
        if domain in tweet_url:
            path = tweet_url.split(domain)[-1]
            for instance in settings.NITTER_INSTANCES:
                nitter_url = f"{instance}{path}"
                resp = _get(nitter_url)
                if resp is None:
                    continue
                try:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    text_el = soup.select_one(".tweet-content, .main-tweet .content")
                    if text_el:
                        return {
                            "handle": "manual_forward",
                            "text": text_el.get_text(strip=True),
                            "url": tweet_url,
                            "date": "manual",
                        }
                except Exception as exc:
                    logger.debug("Manual tweet fetch error: %s", exc)
                    continue

    return None


def monitor_all_accounts(watchlist: list[dict]) -> list[dict]:
    """
    Fetch recent tweets from all accounts in watchlist.
    Returns all new tweets across all accounts.
    """
    all_tweets = []
    for account in watchlist:
        handle = account.get("handle", "")
        if not handle:
            continue
        logger.info("Checking @%s...", handle)
        tweets = fetch_recent_tweets(handle)
        all_tweets.extend(tweets)
        time.sleep(2)  # Be polite to Nitter

    logger.info("Total tweets fetched: %d from %d accounts",
                len(all_tweets), len(watchlist))
    return all_tweets
