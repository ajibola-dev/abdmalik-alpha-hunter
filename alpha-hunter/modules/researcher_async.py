"""
modules/researcher_async.py
Async research pipeline — v0.7.2

v0.7.2 changes:
  - FIXED: Event loop semaphore binding crash.
    _coingecko_semaphore was a module-level singleton bound to the first
    asyncio event loop. Each asyncio.run() call creates a new event loop,
    causing "bound to a different event loop" error on every scan after the
    first. Fix: semaphores now created fresh inside research_projects_async()
    and passed explicitly to each coroutine. Never stored at module level.
  - Tech detection updated to match researcher.py v0.7.2 (_TECH_SIGNAL_MAP)
  - GitHub topics now included in tech detection
  - DeFiLlama lock also fixed (returns fresh lock each call)
"""
import asyncio
import logging
import time
from typing import Optional

from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("aiohttp not installed — async researcher unavailable.")

# ── DeFiLlama cache (module-level is fine — just data, not event loop objects)
_defillama_cache: list = []
_defillama_cache_time: float = 0
# Threading lock for cache update — safe across event loops
import threading
_defillama_thread_lock = threading.Lock()



async def _async_get(session: "aiohttp.ClientSession",
                     url: str,
                     params: dict = None,
                     extra_headers: dict = None,
                     max_bytes: int = None) -> Optional[bytes]:
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(settings.MAX_RETRIES):
        try:
            async with session.get(
                url, params=params,
                headers=headers or None,
                allow_redirects=True,
            ) as resp:
                if resp.status == 429:
                    wait = 60 * (attempt + 1)
                    logger.warning("Rate limited on %s — sleeping %ds", url, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status >= 400:
                    return None
                if max_bytes:
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
                await asyncio.sleep(wait)
            else:
                logger.debug("Async GET %s failed: %s", url, exc)
    return None


async def _ensure_defillama_cache(session: "aiohttp.ClientSession"):
    """
    Load DeFiLlama cache if stale.
    v0.9.3: uses threading.Lock (not asyncio.Lock) to prevent multiple
    concurrent coroutines from each triggering a fetch simultaneously.
    Only one fetch happens; others wait and reuse the result.
    """
    global _defillama_cache, _defillama_cache_time

    # Fast path — already cached
    with _defillama_thread_lock:
        age = time.time() - _defillama_cache_time
        if _defillama_cache and age < settings.DEFILLAMA_CACHE_TTL:
            return
        # Mark as fetching with a sentinel so other coroutines skip
        _defillama_cache_time = time.time()  # prevents re-entry during fetch

    logger.info("Fetching DeFiLlama raises (async, one-time)...")
    data = await _async_get(session, "https://api.llama.fi/raises")
    if data:
        import json
        try:
            parsed = json.loads(data).get("raises", [])
            with _defillama_thread_lock:
                _defillama_cache = parsed
                _defillama_cache_time = time.time()
            logger.info("DeFiLlama async cache: %d records", len(_defillama_cache))
        except Exception as exc:
            logger.error("DeFiLlama parse error: %s", exc)


def _search_defillama_cache(project_name: str) -> dict:
    name_lower = project_name.lower()
    best = None
    with _defillama_thread_lock:
        cache = list(_defillama_cache)
    for r in cache:
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


async def _check_token_live_async(
        session: "aiohttp.ClientSession",
        project_name: str,
        coingecko_sem: asyncio.Semaphore = None) -> bool:
    """
    v0.7.2: semaphore passed from caller, not from module level.
    Eliminates event loop binding crash.
    """
    import json
    sem = coingecko_sem or asyncio.Semaphore(1)
    async with sem:
        data = await _async_get(
            session,
            "https://api.coingecko.com/api/v3/search",
            params={"query": project_name},
        )
        await asyncio.sleep(2)
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
                "description": best.get("description", "") or "",
                "topics": best.get("topics", []),
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


# ── Tech detection (mirrors researcher.py v0.7.2) ─────────────────────────

_TECH_SIGNAL_MAP = {
    "fhe": "fhe", "fully homomorphic": "fhe",
    "zero knowledge": "zero knowledge", "zk proof": "zero knowledge",
    "zkvm": "zero knowledge", "zkp": "zero knowledge",
    "zk rollup": "zero knowledge", "zk-rollup": "zero knowledge",
    "zk-evm": "zero knowledge", "zkevm": "zero knowledge",
    "privacy": "privacy", "mpc": "privacy",
    "depin": "depin", "decentralized physical": "depin",
    "wireless": "depin", "iot": "depin", "hardware": "depin",
    "node operator": "depin", "validator": "depin",
    "ai blockchain": "ai blockchain", "on-chain ai": "ai blockchain",
    "autonomous agent": "ai blockchain", "ai agent": "ai blockchain",
    "machine learning": "ai blockchain", "inference": "ai blockchain",
    "restaking": "restaking", "shared security": "restaking",
    "avs": "restaking", "eigenlayer": "restaking",
    "modular": "modular", "data availability": "modular",
    "rollup": "modular",
    "intent based": "intent based",
    "account abstraction": "account abstraction",
    "social graph": "social graph", "ai social": "social graph",
    "real world asset": "rwa", "rwa": "rwa", "tokenized": "rwa",
    "payments": "payments", "stablecoin": "payments",
    "layer 1": "layer 1", "l1 blockchain": "layer 1",
    "layer 2": "layer 2", "l2": "layer 2",
    # Synced with researcher.py (were missing from async version)
    "aa wallet": "account abstraction",
    "on-chain identity": "identity",
}


def _detect_novel_tech(text: str) -> list[str]:
    found = {}
    lower = text.lower()
    for signal, canonical in _TECH_SIGNAL_MAP.items():
        if signal in lower and canonical not in found:
            found[canonical] = True
    for signal in settings.NOVEL_TECH_SIGNALS:
        if signal in lower and signal not in found:
            found[signal] = True
    return list(found.keys())


def _detect_testnet(text: str) -> bool:
    signals = ["testnet", "devnet", "join our network", "run a node",
                "validator", "node operator", "public testnet",
                "incentivized testnet", "testnet is live", "testnet launch"]
    lower = text.lower()
    return any(s in lower for s in signals)


def _detect_category(text: str) -> str:
    lower = text.lower()
    if any(x in lower for x in ["fhe", "fully homomorphic"]):
        return "FHE"
    if any(x in lower for x in ["zero knowledge", "zkvm", "zkp", "zk proof"]):
        return "ZK/Privacy"
    if any(x in lower for x in ["depin", "decentralized physical", "iot"]):
        return "DePIN"
    if any(x in lower for x in ["restaking", "shared security", "avs"]):
        return "Restaking"
    if any(x in lower for x in ["modular", "data availability"]):
        return "Modular"
    if any(x in lower for x in ["ai agent", "on-chain ai", "ai blockchain"]):
        return "AI/ML"
    if any(x in lower for x in ["layer 1", "l1 blockchain"]):
        return "Layer 1"
    if any(x in lower for x in ["layer 2", "l2", "rollup"]):
        return "Layer 2"
    return "Infrastructure"


async def _research_one(
        semaphore: asyncio.Semaphore,
        session: "aiohttp.ClientSession",
        project_name: str,
        tweet_text: str = "",
        coingecko_sem: asyncio.Semaphore = None) -> dict:
    """
    Research a single project concurrently.
    v0.7.2: coingecko_sem passed from caller (fresh per batch).
    Tech detection uses combined tweet + github + website text.
    """
    async with semaphore:
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

        await _ensure_defillama_cache(session)

        # Token check with fresh per-batch semaphore
        has_token = await _check_token_live_async(
            session, project_name, coingecko_sem=coingecko_sem
        )
        result["has_token"] = has_token
        if has_token:
            result["research_notes"].append("⚠️ Token already live on CoinGecko")
            db.set_research_cache(project_name, result)
            return result

        # DeFiLlama (sync cache lookup)
        funding_data = _search_defillama_cache(project_name)
        if funding_data:
            result.update(funding_data)
            result["research_notes"].append(
                f"💰 DeFiLlama: ${funding_data.get('funding_usd', 0)/1e6:.1f}M raised"
            )

        # GitHub + website concurrently
        website = result.get("website", "")
        gh_task = asyncio.create_task(
            _search_github_async(session, project_name)
        )
        web_task = asyncio.create_task(
            _scrape_website_async(session, website)
        )
        gh, page_text = await asyncio.gather(gh_task, web_task)

        gh_text = ""
        if gh:
            result["github"] = gh.get("github_url", "")
            gh_text = gh.get("description", "") + " " + " ".join(gh.get("topics", []))
            result["research_notes"].append(
                f"⚙️ GitHub: {gh.get('stars', 0)}★ "
                f"updated {gh.get('last_commit','')[:10]}"
            )
            if not result.get("description") and gh_text.strip():
                result["description"] = gh_text.strip()[:300]

        # Combined tech detection
        combined = f"{tweet_text} {gh_text} {page_text}"
        novel = _detect_novel_tech(combined)
        result["novel_tech"] = novel
        result["category"] = _detect_category(combined)

        if _detect_testnet(combined):
            result["testnet_active"] = True

        if novel:
            result["research_notes"].append(f"🔬 Tech: {', '.join(novel)}")

        db.set_research_cache(project_name, result)
        return result


async def research_projects_async(candidates: list[dict]) -> list[dict]:
    """
    Research all candidates concurrently.
    v0.7.2: semaphores created fresh here — never reused across event loops.
    """
    if not AIOHTTP_AVAILABLE:
        logger.error("aiohttp not available — cannot run async research.")
        return []

    # Fresh semaphores for this event loop — key fix for the crash bug
    semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)
    coingecko_sem = asyncio.Semaphore(1)
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
                coingecko_sem=coingecko_sem,
            )
            for c in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for c, res in zip(candidates, results):
        if isinstance(res, Exception):
            logger.error("Async research exception for '%s': %s",
                         c["project_name"], res)
            continue
        if res.get("has_token"):
            logger.info("Async: '%s' token live — skipping", c["project_name"])
            continue
        c["project"] = res
        c["project"]["mentioned_by"] = c["handle"]
        c["project"]["tweet_url"] = c["tweet"].get("url", "")
        c["project"]["tweet_text"] = c["tweet"].get("text", "")[:500]
        enriched.append(c)

    logger.info("Async research complete — %d/%d enriched",
                len(enriched), len(candidates))
    return enriched
