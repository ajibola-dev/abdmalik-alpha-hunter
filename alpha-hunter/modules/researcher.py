"""
modules/researcher.py
Researches extracted project names:
- Searches DeFiLlama raises for funding data
- Searches CoinGecko to verify no token live
- Scrapes project website for team/tech signals
- Searches GitHub for technical activity
"""
import logging
import time
import re
import requests
from bs4 import BeautifulSoup
from config.settings import settings

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# Cache to avoid re-researching same projects
_research_cache: dict = {}


def _get(url: str, params: dict = None) -> requests.Response | None:
    try:
        r = _SESSION.get(url, params=params, timeout=settings.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


def check_token_live(project_name: str) -> bool:
    """Returns True if project already has a live token on CoinGecko."""
    resp = _get("https://api.coingecko.com/api/v3/search",
                params={"query": project_name})
    if resp is None:
        return False
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:3]:
            name = coin.get("name", "").lower()
            if project_name.lower() in name or name in project_name.lower():
                logger.info("Token live: '%s' found as '%s' on CoinGecko",
                            project_name, coin.get("name"))
                return True
    except Exception:
        pass
    return False


def search_defillama_raises(project_name: str) -> dict:
    """Search DeFiLlama raises for funding data on a specific project."""
    resp = _get("https://api.llama.fi/raises")
    if resp is None:
        return {}
    try:
        raises = resp.json().get("raises", [])
        name_lower = project_name.lower()
        for raise_ in raises:
            raise_name = str(raise_.get("name", "")).lower()
            if name_lower in raise_name or raise_name in name_lower:
                amount = raise_.get("amount", 0) or 0
                return {
                    "funding_usd": float(amount) * 1_000_000,
                    "investors": ", ".join(
                        raise_.get("leadInvestors", []) +
                        raise_.get("otherInvestors", [])
                    ),
                    "round": raise_.get("round", ""),
                    "date": raise_.get("date", ""),
                    "website": raise_.get("url", ""),
                }
    except Exception as exc:
        logger.debug("DeFiLlama search error: %s", exc)
    return {}


def search_github(project_name: str) -> dict:
    """Search GitHub for project repos — signals technical activity."""
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
    """Scrape project homepage for tech/team signals."""
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
    """Returns list of novel tech signals found in text."""
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
    """
    Full research pipeline for a project name.
    Returns enriched project dict.
    """
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

    # ── Token check ───────────────────────────────────────────────────────
    result["has_token"] = check_token_live(project_name)
    if result["has_token"]:
        result["research_notes"].append("⚠️ Token already live on CoinGecko")
        _research_cache[project_name] = result
        return result

    time.sleep(1)

    # ── Funding check ─────────────────────────────────────────────────────
    funding_data = search_defillama_raises(project_name)
    if funding_data:
        result.update(funding_data)
        result["research_notes"].append(
            f"💰 Found on DeFiLlama: ${funding_data.get('funding_usd',0)/1e6:.0f}M raised"
        )

    # ── GitHub check ──────────────────────────────────────────────────────
    gh = search_github(project_name)
    if gh:
        result["github"] = gh.get("github_url", "")
        result["research_notes"].append(
            f"⚙️ GitHub: {gh.get('stars', 0)} stars, last commit {gh.get('last_commit','')[:10]}"
        )

    # ── Website scrape ────────────────────────────────────────────────────
    if result.get("website"):
        page_text = scrape_project_website(result["website"])
        novel = _detect_novel_tech(page_text + " " + tweet_text)
        if novel:
            result["novel_tech"] = novel
            result["research_notes"].append(f"🔬 Novel tech: {', '.join(novel)}")

        if _detect_testnet(page_text):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet detected on website")

    # ── Check tweet text for tech signals ────────────────────────────────
    novel_from_tweet = _detect_novel_tech(tweet_text)
    for n in novel_from_tweet:
        if n not in result["novel_tech"]:
            result["novel_tech"].append(n)

    if _detect_testnet(tweet_text):
        result["testnet_active"] = True

    _research_cache[project_name] = result
    return result
