"""
modules/coingecko_scanner.py
CoinGecko trending + search spike detection — v0.8

Autonomous signal source: catches projects spiking in search volume
BEFORE they appear on crypto Twitter. No watchlist accounts needed.

How it works:
  1. Fetches CoinGecko /search/trending — top 15 coins/NFTs by search volume
  2. Fetches CoinGecko /search for each configured keyword
     (testnet, airdrop, zk, depin, etc.)
  3. Filters out projects with live tokens (already launched)
  4. Scores and persists survivors
  5. Returns qualified candidates for alerting

Why this matters:
  Projects appear in CoinGecko trending BEFORE Twitter hype because:
  - Researchers and traders search for them directly
  - DeFi aggregators index them early
  - The trending data reflects organic search interest

Runs every 6 hours (separate from X monitor cycle).
CoinGecko free tier: ~30 req/min — we use minimal calls.
"""
import logging
import time
import requests
from config.settings import settings
from modules.database import upsert_project, save_score, log_scan

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# Search keywords that surface early-stage projects
_SEARCH_KEYWORDS = [
    "testnet",
    "airdrop",
    "zk rollup",
    "depin",
    "restaking",
    "modular blockchain",
    "fhe",
    "incentivized testnet",
]

# Known launched projects to skip
_LAUNCHED_BLOCKLIST = {
    "bitcoin", "ethereum", "solana", "bnb", "xrp", "cardano",
    "dogecoin", "shiba", "tron", "avalanche", "polygon", "chainlink",
    "uniswap", "aave", "compound", "maker", "curve", "lido",
    "eigenlayer", "arbitrum", "optimism", "base", "starknet",
    "zksync", "mantle", "blur", "hyperliquid", "jupiter",
}


def _get(url: str, params: dict = None) -> dict | None:
    """Rate-limited GET — respects CoinGecko free tier."""
    for attempt in range(settings.MAX_RETRIES):
        try:
            r = _SESSION.get(url, params=params,
                             timeout=settings.REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                logger.warning("CoinGecko rate limited — sleeping %ds", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < settings.MAX_RETRIES - 1:
                time.sleep(wait)
            else:
                logger.debug("CoinGecko GET %s failed: %s", url, exc)
    return None


def _is_blocklisted(name: str) -> bool:
    return any(b in name.lower() for b in _LAUNCHED_BLOCKLIST)


def scan_trending() -> list[dict]:
    """
    Fetch CoinGecko trending coins — top 15 by search volume.
    Returns pre-token candidates only.
    """
    data = _get("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        logger.warning("CoinGecko trending fetch failed")
        return []

    candidates = []
    coins = data.get("coins", [])

    for entry in coins:
        item = entry.get("item", {})
        name = item.get("name", "").strip()
        symbol = item.get("symbol", "").upper()

        if not name or _is_blocklisted(name):
            continue

        # Score rank — lower is more trending
        score_rank = item.get("score", 15)

        candidates.append({
            "name": name,
            "symbol": symbol,
            "funding_usd": 0,
            "investors": "",
            "category": "Infrastructure",
            "website": item.get("large", ""),  # image URL not useful, skip
            "description": f"Trending on CoinGecko (rank #{score_rank + 1})",
            "source": "coingecko_trending",
            "has_token": False,  # will verify
            "testnet_active": False,
            "novel_tech": [],
            "trending_rank": score_rank,
        })

    logger.info("CoinGecko trending: %d raw candidates", len(candidates))
    return candidates


def scan_search_keywords() -> list[dict]:
    """
    Search CoinGecko for each keyword — surfaces early projects
    that are being searched but may not be trending yet.
    """
    candidates = {}

    for keyword in _SEARCH_KEYWORDS:
        time.sleep(2)  # Gentle rate limiting
        data = _get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": keyword}
        )
        if not data:
            continue

        coins = data.get("coins", [])
        for coin in coins[:5]:  # Top 5 per keyword
            name = coin.get("name", "").strip()
            if not name or _is_blocklisted(name):
                continue

            # Skip coins with market cap rank — they're launched
            rank = coin.get("market_cap_rank")
            if rank and rank < 1000:
                continue

            key = name.lower()
            if key not in candidates:
                candidates[key] = {
                    "name": name,
                    "symbol": coin.get("symbol", "").upper(),
                    "funding_usd": 0,
                    "investors": "",
                    "category": "Infrastructure",
                    "website": "",
                    "description": f"Appearing in CoinGecko search for '{keyword}'",
                    "source": "coingecko_search",
                    "has_token": False,
                    "testnet_active": "testnet" in keyword or "incentivized testnet" in keyword,
                    "novel_tech": [keyword] if keyword in [
                        "zk rollup", "depin", "restaking", "fhe",
                        "modular blockchain", "incentivized testnet"
                    ] else [],
                    "search_keywords": [keyword],
                }
            else:
                # Project appeared for multiple keywords — stronger signal
                existing = candidates[key]
                if keyword not in existing.get("search_keywords", []):
                    existing["search_keywords"].append(keyword)
                    if keyword in ["zk rollup", "depin", "restaking",
                                   "fhe", "modular blockchain"]:
                        if keyword not in existing["novel_tech"]:
                            existing["novel_tech"].append(keyword)

    logger.info("CoinGecko search: %d unique candidates across %d keywords",
                len(candidates), len(_SEARCH_KEYWORDS))
    return list(candidates.values())


def run_coingecko_scan(scan_id: str = "") -> list[dict]:
    """
    Full CoinGecko scan pipeline.
    Returns qualified candidates ready for alerting.
    """
    from modules.scorer import score_project
    from modules.researcher import _detect_novel_tech, _detect_testnet, check_token_live

    if not scan_id:
        from modules.pipeline import _make_scan_id
        scan_id = _make_scan_id()

    logger.info("[%s] ── CoinGecko Scanner starting ──────────────", scan_id)

    # Gather candidates
    all_candidates = []
    all_candidates.extend(scan_trending())
    time.sleep(3)
    all_candidates.extend(scan_search_keywords())

    # Deduplicate by name
    seen = {}
    for c in all_candidates:
        key = c["name"].lower().strip()
        if key not in seen:
            seen[key] = c
        else:
            # Merge novel_tech
            for tech in c.get("novel_tech", []):
                if tech not in seen[key]["novel_tech"]:
                    seen[key]["novel_tech"].append(tech)

    unique = list(seen.values())
    logger.info("[%s] CoinGecko scan: %d unique candidates", scan_id, len(unique))

    results = []
    for project in unique:
        name = project["name"]
        try:
            time.sleep(2)  # CoinGecko rate limiting

            # Token verification
            if check_token_live(name):
                logger.debug("[%s] '%s' token live — skipping", scan_id, name)
                continue

            # Enrich tech from description
            desc = project.get("description", "")
            extra_tech = _detect_novel_tech(desc)
            for t in extra_tech:
                if t not in project["novel_tech"]:
                    project["novel_tech"].append(t)

            if _detect_testnet(desc):
                project["testnet_active"] = True

            score_result = score_project(project, caller_tier=2, caller_count=1)

            logger.info(
                "[%s] coingecko scored project='%s' score=%.1f label='%s'",
                scan_id, name, score_result.score, score_result.label
            )

            project_id = upsert_project(
                name=name,
                mentioned_by=f"[{project['source']}]",
                tweet_url="",
                tweet_text="",
                category=project["category"],
                funding_usd=project["funding_usd"],
                investors=project["investors"],
                has_token=False,
                testnet_active=project["testnet_active"],
                website=project["website"],
                description=project["description"],
            )
            save_score(
                project_id=project_id,
                score=score_result.score,
                breakdown=__import__('json').dumps(score_result.breakdown),
                label=score_result.label,
            )

            results.append({
                "project": project,
                "project_id": project_id,
                "score_result": score_result,
            })

        except Exception as exc:
            logger.error("[%s] CoinGecko scan error project='%s': %s",
                         scan_id, name, exc)

    qualified = [
        r for r in results
        if r["score_result"].score >= settings.GENESIS_THRESHOLD
    ]

    logger.info(
        "[%s] CoinGecko scan complete — total=%d qualified=%d",
        scan_id, len(results), len(qualified)
    )
    log_scan("coingecko_scanner", len(qualified), notes=f"scan_id={scan_id}")
    return qualified
