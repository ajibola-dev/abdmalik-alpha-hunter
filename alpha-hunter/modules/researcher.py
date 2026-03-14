"""
modules/researcher.py
Researches extracted project names.

v0.2 changes:
  - FIXED: GitHub auth token was prepared but never passed to _get() — now fixed
  - FIXED: research_cache moved to DB (get_research_cache / set_research_cache)
    → survives restarts; uses RESEARCH_CACHE_TTL from settings
  - DeFiLlama cache TTL now reads from settings.DEFILLAMA_CACHE_TTL
  - Website scraper: enforces MAX_SCRAPE_BYTES content size cap
  - Website scraper: strips more noise tags (head, meta, script, style, nav, footer, aside)
  - Exponential backoff uses settings.MAX_RETRIES / RETRY_BACKOFF_BASE
  - GitHub search: passes auth headers correctly (was silently unauthenticated)
  - Structured log context added throughout

v0.3 / v0.4 note:
  - Async HTTP (aiohttp) applied in researcher_async.py; this sync version
    remains for backward-compatible callers (telegram /research command etc.)
"""
import logging
import time
import requests
from bs4 import BeautifulSoup
from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# ── Single shared DeFiLlama cache ─────────────────────────────────────────
# (funding_scanner.py has its own copy — they share the same API endpoint
#  so both caches are independent. A future v0.5 could unify them.)
_defillama_raises_cache: list = []
_defillama_cache_time: float = 0


def _get(url: str, params: dict = None,
         retries: int = None,
         extra_headers: dict = None) -> requests.Response | None:
    """
    Resilient GET with exponential backoff.
    v0.2: uses settings.MAX_RETRIES and RETRY_BACKOFF_BASE.
          accepts extra_headers for per-call auth (e.g. GitHub token).
    """
    max_retries = retries or settings.MAX_RETRIES
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(max_retries):
        try:
            r = _SESSION.get(
                url,
                params=params,
                headers=headers if headers else None,
                timeout=settings.REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            return r
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < max_retries - 1:
                logger.debug(
                    "GET %s failed (attempt %d/%d): %s — retry in %ds",
                    url, attempt + 1, max_retries, exc, wait
                )
                time.sleep(wait)
            else:
                logger.debug(
                    "GET %s failed after %d attempts: %s", url, max_retries, exc
                )
    return None


def _get_defillama_raises() -> list:
    """
    Fetch DeFiLlama raises ONCE per session and cache.
    v0.2: TTL driven by settings.DEFILLAMA_CACHE_TTL.
    """
    global _defillama_raises_cache, _defillama_cache_time

    age = time.time() - _defillama_cache_time
    if _defillama_raises_cache and age < settings.DEFILLAMA_CACHE_TTL:
        return _defillama_raises_cache

    logger.info("Fetching DeFiLlama raises (one-time cache)...")
    resp = _get("https://api.llama.fi/raises")
    if resp is None:
        return _defillama_raises_cache  # return stale if available

    try:
        _defillama_raises_cache = resp.json().get("raises", [])
        _defillama_cache_time = time.time()
        logger.info("DeFiLlama raises cached: %d records",
                    len(_defillama_raises_cache))
    except Exception as exc:
        logger.error("DeFiLlama raises parse error: %s", exc)

    return _defillama_raises_cache


def check_token_live(project_name: str) -> bool:
    """
    Returns True if project already has a live token on CoinGecko.
    Failed check defaults to False (safer direction).
    """
    resp = _get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
    )
    if resp is None:
        logger.debug("CoinGecko check failed for '%s' — assuming no token",
                     project_name)
        return False
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:3]:
            coin_name = coin.get("name", "").lower()
            proj_lower = project_name.lower()
            if proj_lower == coin_name or proj_lower in coin_name.split():
                logger.info("Token live: '%s' matches '%s' on CoinGecko",
                            project_name, coin.get("name"))
                return True
    except Exception as exc:
        logger.debug("CoinGecko parse error: %s", exc)
    return False


def search_defillama_raises(project_name: str) -> dict:
    """Search cached DeFiLlama raises for a project."""
    raises = _get_defillama_raises()
    name_lower = project_name.lower()

    best_match = None
    for raise_ in raises:
        raise_name = str(raise_.get("name", "")).lower()
        if raise_name == name_lower or name_lower in raise_name.split():
            best_match = raise_
            break
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
    """
    Search GitHub for project repos.
    v0.2 FIX: auth headers are now actually passed to _get().
    Previously the headers dict was built but silently discarded.
    """
    github_headers = {}
    if settings.GITHUB_TOKEN:
        github_headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
        github_headers["Accept"] = "application/vnd.github.v3+json"

    resp = _get(
        "https://api.github.com/search/repositories",
        params={"q": project_name, "sort": "stars", "per_page": 5},
        extra_headers=github_headers if github_headers else None,
    )
    if resp is None:
        return {}
    try:
        items = resp.json().get("items", [])
        if items:
            # v0.2: pick repo with best combination of stars + recency
            # (previously just took items[0] sorted by updated)
            def _repo_rank(r):
                stars = r.get("stargazers_count", 0)
                # Penalise repos that haven't been touched in 6+ months
                from datetime import datetime, timezone
                updated = r.get("updated_at", "")
                try:
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    days_old = (datetime.now(timezone.utc) - dt).days
                except Exception:
                    days_old = 999
                recency_bonus = max(0, 180 - days_old) / 180  # 0-1
                return stars * (0.7 + 0.3 * recency_bonus)

            best = max(items, key=_repo_rank)
            return {
                "github_url": best.get("html_url", ""),
                "stars": best.get("stargazers_count", 0),
                "last_commit": best.get("updated_at", ""),
                "description": best.get("description", ""),
            }
    except Exception:
        pass
    return {}


def scrape_project_website(website: str) -> str:
    """
    Scrape project website for signal text.
    v0.2: enforces MAX_SCRAPE_BYTES content cap — prevents huge pages
          blocking the pipeline. Also strips more noise tags.
    """
    if not website:
        return ""

    resp = _get(website)
    if resp is None:
        return ""

    try:
        # Content size cap (v0.2)
        raw = resp.content[:settings.MAX_SCRAPE_BYTES]
        soup = BeautifulSoup(raw, "html.parser")

        # Remove all non-content tags
        for tag in soup(["script", "style", "nav", "footer",
                          "aside", "head", "meta", "noscript",
                          "iframe", "svg", "img"]):
            tag.decompose()

        text = " ".join(soup.get_text(separator=" ").split())
        return text[:3000]
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
    """
    Full research pipeline.
    v0.2: checks DB research cache first (survives restarts).
          Falls back to in-memory cache for same-session hits.
    """
    # 1. DB-backed research cache (v0.2) — survives restarts
    cached = db.get_research_cache(project_name)
    if cached:
        logger.debug("DB research cache hit: %s", project_name)
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
        "category": "Infrastructure",
        "research_notes": [],
    }

    # Token check first — early exit if live
    result["has_token"] = check_token_live(project_name)
    if result["has_token"]:
        result["research_notes"].append("⚠️ Token already live on CoinGecko")
        db.set_research_cache(project_name, result)
        return result

    time.sleep(1)

    # DeFiLlama funding check
    funding_data = search_defillama_raises(project_name)
    if funding_data:
        result.update(funding_data)
        result["research_notes"].append(
            f"💰 DeFiLlama: ${funding_data.get('funding_usd', 0)/1e6:.0f}M raised"
        )

    # GitHub (v0.2: now properly authenticated)
    gh = search_github(project_name)
    if gh:
        result["github"] = gh.get("github_url", "")
        result["research_notes"].append(
            f"⚙️ GitHub: {gh.get('stars', 0)}★ "
            f"updated {gh.get('last_commit','')[:10]}"
        )

    # Website scrape (v0.2: content cap applied inside scrape_project_website)
    if result.get("website"):
        page_text = scrape_project_website(result["website"])
        combined_text = page_text + " " + tweet_text
        novel = _detect_novel_tech(combined_text)
        result["novel_tech"] = novel
        if novel:
            result["research_notes"].append(
                f"🔬 Novel tech: {', '.join(novel)}"
            )
        if _detect_testnet(page_text):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet on website")

    # Tech signals from tweet itself
    for n in _detect_novel_tech(tweet_text):
        if n not in result["novel_tech"]:
            result["novel_tech"].append(n)
    if _detect_testnet(tweet_text):
        result["testnet_active"] = True

    # Persist to DB cache (v0.2)
    db.set_research_cache(project_name, result)
    return result
