"""
modules/researcher.py — v2.0

Stripped down. No DeFiLlama. No CoinGecko trending.

What we actually need to know about a project:
  1. Does it have a live token? (CoinGecko search — kill signal)
  2. Is there an active testnet? (GitHub + website scrape)
  3. Which vertical does it fit? (tech detection)
  4. Is there a Discord? How big? (scrape if possible)
  5. Any backing info from tweet context?

That's it. No more 6-hour funding scanner scans.
"""
import logging
import time
import re
import requests
from bs4 import BeautifulSoup
from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)


def _get(url: str, params: dict = None, retries: int = None) -> requests.Response | None:
    max_retries = retries or settings.MAX_RETRIES
    for attempt in range(max_retries):
        try:
            r = _SESSION.get(url, params=params, timeout=settings.REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning("Rate limited on %s — sleeping %ds", url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < max_retries - 1:
                time.sleep(wait)
    return None


def check_token_live(project_name: str) -> bool:
    """Primary: CoinGecko. Returns True if token is live."""
    resp = _get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
    )
    if resp is None:
        return False  # Unknown — assume no token, don't block
    try:
        coins = resp.json().get("coins", [])
        name_lower = project_name.lower().strip()
        first_word = name_lower.split()[0] if name_lower.split() else name_lower
        for coin in coins[:5]:
            cn = coin.get("name", "").lower().strip()
            rank = coin.get("market_cap_rank")
            symbol = coin.get("symbol", "").lower()
            if rank and rank < 2000:
                if (name_lower in cn or cn in name_lower or
                        first_word == cn.split()[0] if cn.split() else False or
                        name_lower == symbol):
                    logger.info("Token live: '%s' matches '%s' (rank %d)",
                                project_name, coin.get("name"), rank)
                    return True
            if name_lower == cn or name_lower in cn:
                logger.info("Token live: '%s' matches '%s'",
                            project_name, coin.get("name"))
                return True
    except Exception:
        pass
    return False


def search_github(project_name: str) -> dict:
    """Search GitHub for project repos."""
    headers = {}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
        headers["Accept"] = "application/vnd.github.v3+json"

    resp = _get(
        "https://api.github.com/search/repositories",
        params={"q": project_name, "sort": "stars", "per_page": 5},
    )
    if resp is None:
        return {}

    try:
        items = resp.json().get("items", [])
        if items:
            from datetime import datetime, timezone
            def _rank(r):
                stars = r.get("stargazers_count", 0)
                updated = r.get("updated_at", "")
                try:
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    days_old = (datetime.now(timezone.utc) - dt).days
                except Exception:
                    days_old = 999
                return stars * max(0, 1 - days_old / 365)

            best = max(items, key=_rank)
            updated = best.get("updated_at", "")
            days_since = 999
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass

            # Calculate project age
            created = best.get("created_at", "")
            age_days = 999
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass

            return {
                "github_url": best.get("html_url", ""),
                "stars": best.get("stargazers_count", 0),
                "last_commit": updated,
                "days_since_commit": days_since,
                "age_days": age_days,
                "description": best.get("description", "") or "",
                "topics": best.get("topics", []),
            }
    except Exception:
        pass
    return {}


def scrape_website(url: str) -> dict:
    """Scrape project website for testnet/discord signals."""
    if not url:
        return {}

    resp = _get(url)
    if resp is None:
        return {}

    result = {
        "testnet_mentioned": False,
        "discord_url": None,
        "discord_size_estimate": None,
        "galxe_mentioned": False,
    }

    try:
        raw = resp.content[:settings.MAX_SCRAPE_BYTES]
        soup = BeautifulSoup(raw, "html.parser")
        text = soup.get_text(separator=" ").lower()

        # Testnet signals
        if any(s in text for s in ["testnet", "devnet", "public testnet",
                                    "testnet is live", "join testnet"]):
            result["testnet_mentioned"] = True

        # Discord link
        discord_links = re.findall(r'discord\.gg/[\w-]+|discord\.com/invite/[\w-]+', text)
        if discord_links:
            result["discord_url"] = discord_links[0]

        # Galxe presence
        if "galxe" in text or "galaxy.eco" in text:
            result["galxe_mentioned"] = True

    except Exception:
        pass

    return result


def _detect_vertical_from_text(text: str) -> str:
    lower = text.lower()
    if any(x in lower for x in ["fhe", "zero knowledge", "zk ", "zkvm",
                                  "zk proof", "rollup", "starknet", "l2 "]):
        return "zk_l2"
    if any(x in lower for x in ["perp", "perpetual", "hyperliquid",
                                  "trading competition", "vault"]):
        return "perpdex"
    if any(x in lower for x in ["solana", "jito", "meteora", "jupiter"]):
        return "solana_defi"
    if any(x in lower for x in ["cosmos", "ibc", "celestia", "da layer",
                                  "data availability", "staking"]):
        return "cosmos_da"
    return "infrastructure"


def research_project(project_name: str, tweet_text: str = "") -> dict:
    """
    v2.0: Focused research pipeline.
    No DeFiLlama. No CoinGecko trending.
    Goal: answer the 5 questions that matter for farming.
    """
    cached = db.get_research_cache(project_name)
    if cached:
        logger.debug("Cache hit: %s", project_name)
        return cached

    logger.info("Researching: %s", project_name)

    result = {
        "name": project_name,
        "funding_usd": 0,
        "investors": "",
        "has_token": False,
        "testnet_active": False,
        "website": "",
        "github": "",
        "description": "",
        "novel_tech": [],
        "category": "infrastructure",
        "vertical": "infrastructure",
        "discord_size": None,
        "has_galxe": False,
        "github_days_since_commit": 999,
        "project_age_days": 999,
        "research_notes": [],
    }

    # 1. Token check — kill signal
    time.sleep(1)
    result["has_token"] = check_token_live(project_name)
    if result["has_token"]:
        result["research_notes"].append("⛔ Token already live")
        db.set_research_cache(project_name, result)
        return result

    # 2. GitHub
    gh = search_github(project_name)
    if gh:
        result["github"] = gh.get("github_url", "")
        result["github_days_since_commit"] = gh.get("days_since_commit", 999)
        result["project_age_days"] = gh.get("age_days", 999)
        result["description"] = gh.get("description", "")

        gh_text = gh.get("description", "") + " " + " ".join(gh.get("topics", []))
        result["research_notes"].append(
            f"⚙️ GitHub: {gh.get('stars', 0)}★ "
            f"({gh.get('days_since_commit', 999)}d ago)"
        )

        # Testnet from GitHub
        testnet_words = ["testnet", "devnet", "validator", "node", "prover"]
        if any(w in gh_text.lower() for w in testnet_words):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet signal in GitHub")

    # 3. Detect vertical from all available text
    combined = f"{tweet_text} {result['description']}"
    result["vertical"] = _detect_vertical_from_text(combined)

    # 4. Testnet from tweet text
    if not result["testnet_active"]:
        testnet_words = ["testnet", "devnet", "testnet is live", "join testnet",
                         "testnet launch", "public testnet", "incentivized testnet"]
        if any(w in tweet_text.lower() for w in testnet_words):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet signal in tweet")

    # 5. Extract funding/investors from tweet context
    investors_from_tweet = _extract_investors_from_tweet(tweet_text)
    if investors_from_tweet:
        result["investors"] = investors_from_tweet
        result["research_notes"].append(f"💰 Backing: {investors_from_tweet[:60]}")

    funding_from_tweet = _extract_funding_from_tweet(tweet_text)
    if funding_from_tweet:
        result["funding_usd"] = funding_from_tweet
        result["research_notes"].append(
            f"💰 Funding: ${funding_from_tweet/1e6:.1f}M (from tweet)"
        )

    db.set_research_cache(project_name, result)
    return result


def _extract_investors_from_tweet(tweet_text: str) -> str:
    """Extract VC/investor names mentioned in the tweet."""
    known_funds = [
        "a16z", "andreessen horowitz", "paradigm", "multicoin",
        "polychain", "electric capital", "dragonfly", "pantera",
        "binance labs", "coinbase ventures", "sequoia", "1kx",
        "hack vc", "framework ventures", "spartan", "delphi",
        "jump crypto", "solana ventures",
    ]
    lower = tweet_text.lower()
    found = [f for f in known_funds if f in lower]
    return ", ".join(found) if found else ""


def _extract_funding_from_tweet(tweet_text: str) -> float:
    """Extract funding amount from tweet text."""
    patterns = [
        r'\$(\d+(?:\.\d+)?)\s*[Mm](?:illion)?',
        r'(\d+(?:\.\d+)?)\s*[Mm](?:illion)?\s*(?:USD|USDC)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, tweet_text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1)) * 1_000_000
            except Exception:
                pass
    return 0
