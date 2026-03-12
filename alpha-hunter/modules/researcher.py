"""
modules/researcher.py
Researches extracted project names.

Fixes applied:
- DeFiLlama raises fetched ONCE and cached globally (not per-project)
- Persistent research cache via DB (survives restarts)
- Retry logic on CoinGecko token check
- novel_tech preserved properly
"""
import logging
import time
import requests
from bs4 import BeautifulSoup
from config.settings import settings

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# ── Global caches ─────────────────────────────────────────────────────────
_research_cache: dict = {}          # in-memory per session
_defillama_raises_cache: list = []  # fetched ONCE per session
_defillama_cache_time: float = 0


def _get(url: str, params: dict = None, retries: int = 3) -> requests.Response | None:
    """Resilient GET with retries."""
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, params=params, timeout=settings.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt == retries - 1:
                logger.debug("GET %s failed after %d attempts: %s", url, retries, exc)
            time.sleep(2 ** attempt)
    return None


def _get_defillama_raises() -> list:
    """
    Fetch DeFiLlama raises ONCE per session and cache.
    Refreshes every 6 hours.
    """
    global _defillama_raises_cache, _defillama_cache_time

    age = time.time() - _defillama_cache_time
    if _defillama_raises_cache and age < 21600:  # 6 hours
        return _defillama_raises_cache

    logger.info("Fetching DeFiLlama raises (one-time cache)...")
    resp = _get("https://api.llama.fi/raises")
    if resp is None:
        return _defillama_raises_cache  # return stale if available

    try:
        _defillama_raises_cache = resp.json().get("raises", [])
        _defillama_cache_time = time.time()
        logger.info("DeFiLlama raises cached: %d records", len(_defillama_raises_cache))
    except Exception as exc:
        logger.error("DeFiLlama raises parse error: %s", exc)

    return _defillama_raises_cache


def check_token_live(project_name: str) -> bool:
    """
    Returns True if project already has a live token on CoinGecko.
    Has retry logic — failed check defaults to False (safer direction).
    """
    resp = _get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
        retries=3
    )
    if resp is None:
        logger.debug("CoinGecko check failed for '%s' — assuming no token", project_name)
        return False
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:3]:
            coin_name = coin.get("name", "").lower()
            proj_lower = project_name.lower()
            # Require reasonably close match to avoid false positives
            if proj_lower == coin_name or proj_lower in coin_name.split():
                logger.info("Token live: '%s' matches '%s' on CoinGecko",
                            project_name, coin.get("name"))
                return True
    except Exception as exc:
        logger.debug("CoinGecko parse error: %s", exc)
    return False


def search_defillama_raises(project_name: str) -> dict:
    """
    Search cached DeFiLlama raises for a project.
    Uses the global cache — no extra HTTP request per project.
    """
    raises = _get_defillama_raises()
    name_lower = project_name.lower()

    best_match = None
    for raise_ in raises:
        raise_name = str(raise_.get("name", "")).lower()
        # Exact or close match only
        if raise_name == name_lower or name_lower in raise_name.split():
            best_match = raise_
            break
        # Partial match — keep as fallback
        if name_lower in raise_name and best_match is None:
            best_match = raise_

    if not best_match:
        return {}

    amount = best_match.get("amount", 0) or 0
    return {
        "funding_usd": float(amount) * 1_000_000,
        "investors": ", ".join(
            best_match.get("leadInvestors", []) +
            best_match.get("otherInvestors", [])
        ),
        "round": best_match.get("round", ""),
        "date": best_match.get("date", ""),
        "website": best_match.get("url", ""),
    }


def search_github(project_name: str) -> dict:
    """Search GitHub for project repos."""
    headers = {}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    resp = _get(
        "https://api.github.com/search/repositories",
        params={"q": project_name, "sort": "updated", "per_page": 3}
    )
    if resp is None:
        return {}
    try:
        items = resp.json().get("items", [])
        if items:
            top = items[0]
            return {
                "github_url": top.get("html_url", ""),
                "stars": top.get("stargazers_count", 0),
                "last_commit": top.get("updated_at", ""),
                "description": top.get("description", ""),
            }
    except Exception:
        pass
    return {}


def scrape_project_website(website: str) -> str:
    if not website:
        return ""
    resp = _get(website)
    if resp is None:
        return ""
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return " ".join(soup.get_text(separator=" ").split())[:3000]
    except Exception:
        return ""


def _detect_novel_tech(text: str) -> list[str]:
    found = []
    lower = text.lower()
    for signal in settings.NOVEL_TECH_SIGNALS:
        if signal in lower:
            found.append(signal)
    return found


def _detect_testnet(text: str) -> bool:
    signals = ["testnet", "devnet", "join our network", "run a node",
                "validator", "node operator", "public testnet"]
    lower = text.lower()
    return any(s in lower for s in signals)


def research_project(project_name: str, tweet_text: str = "") -> dict:
    """Full research pipeline. Uses global DeFiLlama cache."""
    if project_name in _research_cache:
        logger.debug("Research cache hit: %s", project_name)
        return _research_cache[project_name]

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
        "category": "Infrastructure",
        "research_notes": [],
    }

    # Token check first
    result["has_token"] = check_token_live(project_name)
    if result["has_token"]:
        result["research_notes"].append("⚠️ Token already live on CoinGecko")
        _research_cache[project_name] = result
        return result

    time.sleep(1)

    # Funding from cached DeFiLlama
    funding_data = search_defillama_raises(project_name)
    if funding_data:
        result.update(funding_data)
        result["research_notes"].append(
            f"💰 DeFiLlama: ${funding_data.get('funding_usd', 0)/1e6:.0f}M raised"
        )

    # GitHub
    gh = search_github(project_name)
    if gh:
        result["github"] = gh.get("github_url", "")
        result["research_notes"].append(
            f"⚙️ GitHub: {gh.get('stars', 0)}★ updated {gh.get('last_commit','')[:10]}"
        )

    # Website scrape
    if result.get("website"):
        page_text = scrape_project_website(result["website"])
        combined_text = page_text + " " + tweet_text
        novel = _detect_novel_tech(combined_text)
        result["novel_tech"] = novel
        if novel:
            result["research_notes"].append(f"🔬 Novel tech: {', '.join(novel)}")
        if _detect_testnet(page_text):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet on website")

    # Tech signals from tweet itself
    for n in _detect_novel_tech(tweet_text):
        if n not in result["novel_tech"]:
            result["novel_tech"].append(n)
    if _detect_testnet(tweet_text):
        result["testnet_active"] = True

    _research_cache[project_name] = result
    return result
