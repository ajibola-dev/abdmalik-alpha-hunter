"""
modules/researcher_async.py
Async research pipeline — v0.4

Replaces the blocking researcher.py HTTP calls with aiohttp + asyncio
for the research_stage() in pipeline.py.

Key improvements over sync researcher.py:
  - All API calls (CoinGecko, GitHub, DeFiLlama, website scrape) run
    concurrently per project using asyncio.gather()
  - asyncio.Semaphore(CONCURRENT_REQUESTS) prevents hammering APIs
  - Timeout enforced via aiohttp.ClientTimeout (settings.REQUEST_TIMEOUT)
  - Content size cap enforced at stream level (MAX_SCRAPE_BYTES)
  - DB research cache (get/set_research_cache) used identically to sync version
  - DeFiLlama dataset fetched once at startup, shared across all coroutines
  - Falls back gracefully — if aiohttp unavailable, pipeline_async.py
    imports sync researcher.py instead

Usage (pipeline_async.py calls this):
    results = await research_projects_async(candidates)

Each result is a full project dict identical in shape to research_project().
"""
import asyncio
import logging
import time
from typing import Optional

from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

# ── Lazy import of aiohttp ─────────────────────────────────────────────────
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning(
        "aiohttp not installed — async researcher unavailable. "
        "Install with: pip install aiohttp"
    )

# ── Shared DeFiLlama cache (loaded once, shared by all coroutines) ─────────
_defillama_cache: list = []
_defillama_cache_time: float = 0
_defillama_lock: Optional[asyncio.Lock] = None  # created lazily inside event loop


def _get_defillama_lock():
    global _defillama_lock
    if _defillama_lock is None:
        _defillama_lock = asyncio.Lock()
    return _defillama_lock


# ── Async HTTP helper ──────────────────────────────────────────────────────

async def _async_get(session: "aiohttp.ClientSession",
                     url: str,
                     params: dict = None,
                     extra_headers: dict = None,
                     max_bytes: int = None) -> Optional[bytes]:
    """
    Single async GET with exponential backoff.
    Returns raw bytes or None on failure.
    max_bytes: if set, stream is cut off at this size (website scrape cap).
    """
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(settings.MAX_RETRIES):
        try:
            async with session.get(
                url,
                params=params,
                headers=headers or None,
                allow_redirects=True,
            ) as resp:
                if resp.status == 429:
                    wait = 60 * (attempt + 1)
                    logger.warning(
                        "Rate limited on %s — sleeping %ds", url, wait
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status >= 400:
                    logger.debug(
                        "HTTP %d on %s (attempt %d)",
                        resp.status, url, attempt + 1
                    )
                    return None

                if max_bytes:
                    # Stream with size cap
                    chunks = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(8192):
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= max_bytes:
                            break
                    return b"".join(chunks)
                else:
                    return await resp.read()

        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < settings.MAX_RETRIES - 1:
                logger.debug(
                    "Async GET %s failed (attempt %d): %s — retry in %ds",
                    url, attempt + 1, exc, wait
                )
                await asyncio.sleep(wait)
            else:
                logger.debug("Async GET %s failed after %d attempts: %s",
                             url, settings.MAX_RETRIES, exc)
    return None


# ── DeFiLlama (loaded once, shared) ───────────────────────────────────────

async def _ensure_defillama_cache(session: "aiohttp.ClientSession"):
    """Load DeFiLlama raises into shared cache if stale. Thread-safe via lock."""
    global _defillama_cache, _defillama_cache_time

    age = time.time() - _defillama_cache_time
    if _defillama_cache and age < settings.DEFILLAMA_CACHE_TTL:
        return

    async with _get_defillama_lock():
        # Double-check after acquiring lock
        age = time.time() - _defillama_cache_time
        if _defillama_cache and age < settings.DEFILLAMA_CACHE_TTL:
            return

        logger.info("Fetching DeFiLlama raises (async, one-time)...")
        data = await _async_get(session, "https://api.llama.fi/raises")
        if data:
            import json
            try:
                _defillama_cache = json.loads(data).get("raises", [])
                _defillama_cache_time = time.time()
                logger.info("DeFiLlama async cache: %d records",
                            len(_defillama_cache))
            except Exception as exc:
                logger.error("DeFiLlama parse error: %s", exc)


def _search_defillama_cache(project_name: str) -> dict:
    """Search the in-memory DeFiLlama cache. Called after _ensure_defillama_cache."""
    name_lower = project_name.lower()
    best = None
    for r in _defillama_cache:
        rn = str(r.get("name", "")).lower()
        if rn == name_lower or name_lower in rn.split():
            best = r
            break
        if name_lower in rn and best is None:
            best = r

    if not best:
        return {}
    amount = best.get("amount", 0) or 0
    return {
        "funding_usd": float(amount) * 1_000_000,
        "investors": ", ".join(
            best.get("leadInvestors", []) + best.get("otherInvestors", [])
        ),
        "round": best.get("round", ""),
        "date": best.get("date", ""),
        "website": best.get("url", ""),
    }


# ── Per-project async research coroutines ─────────────────────────────────

async def _check_token_live_async(
        session: "aiohttp.ClientSession", project_name: str) -> bool:
    import json
    data = await _async_get(
        session,
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
    )
    if not data:
        return False
    try:
        coins = json.loads(data).get("coins", [])
        name_lower = project_name.lower()
        for coin in coins[:3]:
            cn = coin.get("name", "").lower()
            if name_lower == cn or name_lower in cn.split():
                return True
    except Exception:
        pass
    return False


async def _search_github_async(
        session: "aiohttp.ClientSession", project_name: str) -> dict:
    import json
    headers = {}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
        headers["Accept"] = "application/vnd.github.v3+json"

    data = await _async_get(
        session,
        "https://api.github.com/search/repositories",
        params={"q": project_name, "sort": "stars", "per_page": 5},
        extra_headers=headers if headers else None,
    )
    if not data:
        return {}
    try:
        items = json.loads(data).get("items", [])
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
                recency_bonus = max(0, 180 - days_old) / 180
                return stars * (0.7 + 0.3 * recency_bonus)

            best = max(items, key=_rank)
            return {
                "github_url": best.get("html_url", ""),
                "stars": best.get("stargazers_count", 0),
                "last_commit": best.get("updated_at", ""),
                "description": best.get("description", ""),
            }
    except Exception:
        pass
    return {}


async def _scrape_website_async(
        session: "aiohttp.ClientSession", website: str) -> str:
    if not website:
        return ""
    data = await _async_get(
        session, website, max_bytes=settings.MAX_SCRAPE_BYTES
    )
    if not data:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(data, "html.parser")
        for tag in soup(["script", "style", "nav", "footer",
                          "aside", "head", "meta", "noscript",
                          "iframe", "svg", "img"]):
            tag.decompose()
        return " ".join(soup.get_text(separator=" ").split())[:3000]
    except Exception:
        return ""


def _detect_novel_tech(text: str) -> list[str]:
    found, lower = [], text.lower()
    for signal in settings.NOVEL_TECH_SIGNALS:
        if signal in lower:
            found.append(signal)
    return found


def _detect_testnet(text: str) -> bool:
    signals = ["testnet", "devnet", "join our network", "run a node",
                "validator", "node operator", "public testnet"]
    lower = text.lower()
    return any(s in lower for s in signals)


# ── Single project research coroutine ─────────────────────────────────────

async def _research_one(
        semaphore: asyncio.Semaphore,
        session: "aiohttp.ClientSession",
        project_name: str,
        tweet_text: str = "") -> dict:
    """
    Research a single project — all API calls run concurrently.
    Respects global semaphore to cap concurrent requests.
    """
    async with semaphore:
        # DB cache check (sync — fast)
        cached = db.get_research_cache(project_name)
        if cached:
            logger.debug("Async DB cache hit: %s", project_name)
            return cached

        logger.info("Async researching: %s", project_name)

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

        # Ensure DeFiLlama is loaded (shared, only fetches once)
        await _ensure_defillama_cache(session)

        # Token check — must be done first; if live, skip everything else
        has_token = await _check_token_live_async(session, project_name)
        result["has_token"] = has_token
        if has_token:
            result["research_notes"].append("⚠️ Token already live on CoinGecko")
            db.set_research_cache(project_name, result)
            return result

        # DeFiLlama lookup (sync, uses in-memory cache)
        funding_data = _search_defillama_cache(project_name)
        if funding_data:
            result.update(funding_data)
            result["research_notes"].append(
                f"💰 DeFiLlama: ${funding_data.get('funding_usd', 0)/1e6:.0f}M raised"
            )

        # GitHub + website scrape — run concurrently
        website = result.get("website", "")
        gh_task = asyncio.create_task(
            _search_github_async(session, project_name)
        )
        web_task = asyncio.create_task(
            _scrape_website_async(session, website)
        )

        gh, page_text = await asyncio.gather(gh_task, web_task)

        if gh:
            result["github"] = gh.get("github_url", "")
            result["research_notes"].append(
                f"⚙️ GitHub: {gh.get('stars', 0)}★ "
                f"updated {gh.get('last_commit','')[:10]}"
            )

        if page_text:
            combined = page_text + " " + tweet_text
            novel = _detect_novel_tech(combined)
            result["novel_tech"] = novel
            if novel:
                result["research_notes"].append(
                    f"🔬 Novel tech: {', '.join(novel)}"
                )
            if _detect_testnet(page_text):
                result["testnet_active"] = True
                result["research_notes"].append("🧪 Testnet on website")

        # Tech signals from tweet text
        for n in _detect_novel_tech(tweet_text):
            if n not in result["novel_tech"]:
                result["novel_tech"].append(n)
        if _detect_testnet(tweet_text):
            result["testnet_active"] = True

        db.set_research_cache(project_name, result)
        return result


# ── Public interface: research a batch of candidates ──────────────────────

async def research_projects_async(candidates: list[dict]) -> list[dict]:
    """
    Research all candidates concurrently.
    Attaches project dict to each candidate.
    Returns only candidates where research succeeded and token is not live.

    Called by pipeline_async.py's research_stage_async().
    """
    if not AIOHTTP_AVAILABLE:
        logger.error(
            "aiohttp not available — cannot run async research. "
            "Falling back is handled by pipeline_async.py."
        )
        return []

    semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)
    timeout = aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=settings.REQUEST_HEADERS,
    ) as session:
        tasks = [
            _research_one(
                semaphore, session,
                c["project_name"],
                c["tweet"].get("text", ""),
            )
            for c in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for c, res in zip(candidates, results):
        if isinstance(res, Exception):
            logger.error(
                "Async research exception for '%s': %s",
                c["project_name"], res
            )
            continue
        if res.get("has_token"):
            logger.info("Async: '%s' token live — skipping",
                        c["project_name"])
            continue
        c["project"] = res
        c["project"]["mentioned_by"] = c["handle"]
        c["project"]["tweet_url"] = c["tweet"].get("url", "")
        c["project"]["tweet_text"] = c["tweet"].get("text", "")[:500]
        enriched.append(c)

    logger.info("Async research complete — %d/%d enriched",
                len(enriched), len(candidates))
    return enriched
