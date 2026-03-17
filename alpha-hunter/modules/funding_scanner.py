"""
modules/funding_scanner.py
Scans funding databases for early-stage projects with no token yet.

v0.5 changes:
  - DeFiLlama cache REMOVED from this module entirely.
    Now imports _get_defillama_raises() from researcher.py — single shared
    cache across the whole process. Was fetching independently; now zero
    duplicate HTTP calls between researcher and funding scanner.
  - _get() replaced with shared _http_get() utility (see below) that reads
    MAX_RETRIES and RETRY_BACKOFF_BASE from settings — no more hardcoded 3.
  - Structured logging: every meaningful log line now includes scan_id
    and project_name where applicable.
  - TTL hardcode (21600) replaced with settings.DEFILLAMA_CACHE_TTL.
  - CoinGecko token check sleeps removed — rate limiting handled in _http_get.

Sources:
  DeFiLlama raises (free, ~6800 funding rounds)
  CryptoRank public page (scraping)
"""
import logging
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from config.settings import settings
from modules.database import upsert_project, save_score, already_alerted, log_scan

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# ── Shared HTTP helper (settings-driven retry + backoff) ──────────────────

def _http_get(url, params=None, extra_headers=None, scan_id=""):
    """
    Standardised GET with exponential backoff.
    Reads MAX_RETRIES and RETRY_BACKOFF_BASE from settings.
    Replaces the old hardcoded _get(retries=3).
    """
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(settings.MAX_RETRIES):
        try:
            r = _SESSION.get(
                url,
                params=params,
                headers=headers if headers else None,
                timeout=settings.REQUEST_TIMEOUT,
            )
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                logger.warning(
                    "[%s] Rate limited on %s — sleeping %ds",
                    scan_id, url, wait
                )
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < settings.MAX_RETRIES - 1:
                logger.debug(
                    "[%s] GET %s failed (attempt %d/%d): %s — retry in %ds",
                    scan_id, url, attempt + 1, settings.MAX_RETRIES, exc, wait
                )
                time.sleep(wait)
            else:
                logger.debug(
                    "[%s] GET %s failed after %d attempts: %s",
                    scan_id, url, settings.MAX_RETRIES, exc
                )
    return None


# ── Category keyword map ───────────────────────────────────────────────────

_CATEGORY_MAP = {
    "layer 1": "Layer 1", "l1 blockchain": "Layer 1", "layer1": "Layer 1",
    "layer 2": "Layer 2", "l2": "Layer 2", "rollup": "Layer 2",
    "optimistic": "Layer 2", "zk rollup": "ZK/Privacy",
    "zero knowledge": "ZK/Privacy", "zkp": "ZK/Privacy", "zk proof": "ZK/Privacy",
    "zkvm": "ZK/Privacy", "fully homomorphic": "FHE", "fhe": "FHE",
    "privacy": "Privacy", "mpc": "Privacy",
    "depin": "DePIN", "decentralized physical": "DePIN",
    "wireless": "DePIN", "energy": "DePIN", "iot": "DePIN",
    "artificial intelligence": "AI/ML", "machine learning": "AI/ML",
    "ai agent": "AI/ML", "on-chain ai": "AI/ML", "inference": "AI/ML",
    "compute": "AI/ML",
    "payments": "Payments", "stablecoin": "Payments", "remittance": "Payments",
    "cross-border": "Payments", "cbdc": "Payments",
    "gaming": "Gaming", "game": "Gaming", "nft": "Gaming",
    "play to earn": "Gaming", "metaverse": "Gaming",
    "real world asset": "RWA", "rwa": "RWA", "tokenized": "RWA",
    "real estate": "RWA", "commodities": "RWA",
    "social": "Social", "identity": "Identity", "did": "Identity",
    "reputation": "Identity", "soulbound": "Identity",
    "restaking": "Restaking", "shared security": "Restaking",
    "avs": "Restaking", "eigenlayer": "Restaking",
    "modular": "Modular", "data availability": "Modular",
    "interoperability": "Cross-chain", "bridge": "Cross-chain",
    "cross-chain": "Cross-chain",
    "oracle": "Oracle", "data feed": "Oracle", "price feed": "Oracle",
    "infrastructure": "Infrastructure", "developer tool": "Infrastructure",
    "middleware": "Infrastructure", "sdk": "Infrastructure",
}

_BLOCKLIST = {
    # Base chains / L1s / L2s
    "bitcoin", "ethereum", "solana", "bnb", "polygon", "avalanche",
    "cardano", "polkadot", "cosmos", "tron", "litecoin", "dogecoin",
    "arbitrum", "optimism", "base", "zksync", "starknet", "mantle",
    "scroll", "linea", "blast", "manta", "mode", "taiko",
    # Meme / low-signal tokens
    "shiba", "pepe", "floki", "bonk", "wif", "dogwifhat",
    # Launched DeFi protocols
    "uniswap", "aave", "compound", "curve", "convex", "balancer",
    "maker", "spark", "morpho", "pendle", "ethena", "ethena labs",
    "lido", "rocket pool", "frax", "sky",
    # Launched infrastructure / known projects
    "eigenlayer", "symbiotic", "karak",
    "story", "story protocol",
    "walrus", "walrus foundation",
    "redotpay", "coinflow",
    "daylight", "daylight energy",
    "meanw hile", "meanwhile",
    "mantra", "mocaverse", "moca", "monad",
    "hyperliquid", "jupiter", "drift", "jito", "marinade",
    "wormhole", "layerzero", "axelar", "celer",
    "chainlink", "pyth", "api3",
    "the graph", "filecoin", "arweave",
    "astar", "moonbeam", "acala",
    "injective", "sei", "aptos", "sui", "movement",
    "berachain", "fuel", "eclipse",
    "blur", "opensea", "looks rare",
    "dydx", "gmx", "gains", "kwenta",
}

# Blockchain relevance signals — project description must contain at least one
# A project with none of these is likely not a blockchain-native farming opportunity
_BLOCKCHAIN_SIGNALS = {
    "blockchain", "protocol", "chain", "layer", "rollup",
    "defi", "dapp", "smart contract", "on-chain", "onchain",
    "testnet", "mainnet", "devnet", "node", "validator",
    "token", "crypto", "web3", "wallet", "zk", "fhe",
    "depin", "restaking", "modular", "l1", "l2",
    "nft", "dao", "governance", "staking", "airdrop",
}


def _is_blockchain_relevant(project: dict) -> bool:
    """
    Returns True if the project appears to be blockchain-native.
    Filters out funded companies that are adjacent to crypto but
    not actual blockchain projects (insurance companies, ETFs, etc.)
    """
    text = " ".join([
        project.get("description", ""),
        project.get("category", ""),
        project.get("name", ""),
    ]).lower()
    return any(signal in text for signal in _BLOCKCHAIN_SIGNALS)

# v1.0: minimum funding raised — $5M threshold was passing too many
# small raises that will never reach airdrop scale.
# FHE exception kept at $5M (rare category, smaller raises still valid).
_MIN_FUNDING = {
    "Layer 1": 25_000_000, "Layer 2": 20_000_000,
    "ZK/Privacy": 15_000_000, "FHE": 5_000_000,
    "DePIN": 15_000_000, "AI/ML": 15_000_000,
    "Payments": 20_000_000, "Gaming": 15_000_000,
    "RWA": 15_000_000, "Restaking": 15_000_000,
    "Modular": 15_000_000, "Infrastructure": 15_000_000,
    "default": 10_000_000,
}


def _detect_category(text: str) -> str:
    lower = text.lower()
    for keyword, category in _CATEGORY_MAP.items():
        if keyword in lower:
            return category
    return "Infrastructure"


def _is_blocklisted(name: str) -> bool:
    return any(b in name.lower() for b in _BLOCKLIST)


def _days_since(timestamp) -> int:
    try:
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


# ── DeFiLlama scan (v0.5: uses shared cache from researcher.py) ───────────

def scan_defillama(scan_id: str = "") -> list[dict]:
    """
    Scan DeFiLlama raises for promising unfunded projects.
    v0.5: imports shared _get_defillama_raises() from researcher.py.
    Zero duplicate HTTP calls — same in-memory cache used by researcher.
    """
    from modules.researcher import _get_defillama_raises
    raises = _get_defillama_raises()

    candidates = []
    for r in raises:
        name = r.get("name", "").strip()
        if not name or _is_blocklisted(name):
            continue

        amount = float(r.get("amount", 0) or 0) * 1_000_000
        age_days = _days_since(r.get("date", 0))

        if age_days > 365:  # v1.0: tightened from 730 — 2yr-old raises are likely launched
            continue

        desc = " ".join(str(p) for p in [
            r.get("category", ""), r.get("description", ""), r.get("name", "")
        ] if p)
        category = _detect_category(desc)

        min_fund = _MIN_FUNDING.get(category, _MIN_FUNDING["default"])
        if amount < min_fund:
            continue

        investors = ", ".join(
            r.get("leadInvestors", []) + r.get("otherInvestors", [])
        )
        candidates.append({
            "name": name,
            "funding_usd": amount,
            "investors": investors,
            "category": category,
            "website": r.get("url", ""),
            "description": desc[:300],
            "source": "defillama",
            "age_days": age_days,
            "has_token": False,
            "testnet_active": False,
            "novel_tech": [],
        })

    logger.info("[%s] DeFiLlama scan: %d candidates after filters",
                scan_id, len(candidates))
    return candidates


# ── CryptoRank scanner ─────────────────────────────────────────────────────

def scan_cryptorank(scan_id: str = "") -> list[dict]:
    """Scrape CryptoRank upcoming projects page."""
    candidates = []
    resp = _http_get("https://cryptorank.io/upcoming-ico", scan_id=scan_id)
    if resp is None:
        logger.warning("[%s] CryptoRank scan failed", scan_id)
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr, .project-row, [class*='TableRow']")

        for row in rows[:50]:
            name_el = row.select_one(
                ".name, [class*='name'], [class*='Name'], h3, h4"
            )
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not name or len(name) < 2 or _is_blocklisted(name):
                continue

            desc_el = row.select_one(".description, [class*='desc'], p")
            desc = desc_el.get_text(strip=True) if desc_el else ""
            category = _detect_category(name + " " + desc)

            candidates.append({
                "name": name,
                "funding_usd": 0,
                "investors": "",
                "category": category,
                "website": "",
                "description": desc[:200],
                "source": "cryptorank",
                "has_token": False,
                "testnet_active": False,
                "novel_tech": [],
            })

    except Exception as exc:
        logger.error("[%s] CryptoRank parse error: %s", scan_id, exc)

    logger.info("[%s] CryptoRank scan: %d candidates", scan_id, len(candidates))
    return candidates


# ── Token verification ─────────────────────────────────────────────────────

def _check_coingecko(project_name: str, proj_lower: str,
                     proj_first_word: str, scan_id: str) -> bool | None:
    """
    Check CoinGecko. Returns:
      False = token confirmed live (skip project)
      True  = no token found (safe)
      None  = API unavailable (try fallback)
    """
    resp = _http_get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
        scan_id=scan_id,
    )
    if resp is None:
        logger.warning("[%s] CoinGecko unavailable for '%s' — trying fallback",
                       scan_id, project_name)
        return None  # signal fallback needed
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:5]:
            coin_name = coin.get("name", "").lower().strip()
            coin_symbol = coin.get("symbol", "").lower()
            rank = coin.get("market_cap_rank")
            if rank and rank < 2000:
                if (proj_lower in coin_name or coin_name in proj_lower or
                        proj_first_word == coin_name.split()[0] or
                        proj_lower == coin_symbol):
                    logger.info("[%s] '%s' matched ranked CoinGecko coin '%s' (rank %d)",
                                scan_id, project_name, coin.get("name"), rank)
                    return False
            if proj_lower == coin_name or proj_lower in coin_name or coin_name in proj_lower:
                logger.info("[%s] '%s' matched CoinGecko coin '%s'",
                            scan_id, project_name, coin.get("name"))
                return False
    except Exception as exc:
        logger.debug("[%s] CoinGecko parse error: %s", scan_id, exc)
        return None
    return True


def _check_coinmarketcap(project_name: str, proj_lower: str,
                          proj_first_word: str, scan_id: str) -> bool | None:
    """
    CoinMarketCap fallback — no API key needed for basic search.
    Returns False (token live), True (no token), or None (unavailable).
    """
    resp = _http_get(
        "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/search/quick",
        params={"keyword": project_name, "limit": "5"},
        scan_id=scan_id,
    )
    if resp is None:
        logger.warning("[%s] CoinMarketCap also unavailable for '%s' — skipping safely",
                       scan_id, project_name)
        return None
    try:
        data = resp.json()
        coins = data.get("data", {}).get("cryptoCurrencyList", [])
        for coin in coins:
            coin_name = coin.get("name", "").lower().strip()
            coin_symbol = coin.get("symbol", "").lower()
            rank = coin.get("cmcRank", 9999)
            if rank < 2000:
                if (proj_lower in coin_name or coin_name in proj_lower or
                        proj_first_word == coin_name.split()[0] or
                        proj_lower == coin_symbol):
                    logger.info("[%s] '%s' matched ranked CMC coin '%s' (rank %d)",
                                scan_id, project_name, coin.get("name"), rank)
                    return False
            if proj_lower == coin_name or proj_lower in coin_name or coin_name in proj_lower:
                logger.info("[%s] '%s' matched CMC coin '%s'",
                            scan_id, project_name, coin.get("name"))
                return False
    except Exception as exc:
        logger.debug("[%s] CMC parse error: %s", scan_id, exc)
        return None
    return True


def verify_no_token(project_name: str, scan_id: str = "") -> bool:
    """
    Returns True if NO live token found — safe to alert.

    Check order:
      1. Internal blocklist (instant, no API)
      2. CoinGecko (primary)
      3. CoinMarketCap (fallback if CoinGecko unavailable)
      4. If both unavailable — skip safely (return False)

    Never alerts when uncertain.
    """
    name_lower = project_name.lower().strip()

    # 1. Blocklist — no API call needed
    if _is_blocklisted(name_lower):
        logger.info("[%s] '%s' in blocklist — skipping", scan_id, project_name)
        return False

    proj_first_word = name_lower.split()[0] if name_lower.split() else name_lower

    # 2. CoinGecko — primary check
    cg_result = _check_coingecko(project_name, name_lower, proj_first_word, scan_id)
    if cg_result is False:
        return False   # token confirmed live
    if cg_result is True:
        return True    # confirmed no token

    # 3. CoinMarketCap — fallback when CoinGecko unavailable
    time.sleep(1)
    cmc_result = _check_coinmarketcap(project_name, name_lower, proj_first_word, scan_id)
    if cmc_result is False:
        return False   # token confirmed live
    if cmc_result is True:
        return True    # confirmed no token

    # 4. Both unavailable — skip safely
    logger.warning("[%s] Both CoinGecko and CMC unavailable for '%s' — skipping",
                   scan_id, project_name)
    return False


# ── Main funding scan pipeline ────────────────────────────────────────────

def run_funding_scan(scan_id: str = "") -> list[dict]:
    """
    Full funding scan across all sources.
    Returns scored candidates ready for alerting.
    """
    from modules.scorer import score_project
    from modules.researcher import _detect_novel_tech, _detect_testnet

    if not scan_id:
        from modules.pipeline import _make_scan_id
        scan_id = _make_scan_id()

    logger.info("[%s] ── Funding Scanner starting ──────────────", scan_id)

    # Gather from all sources
    all_candidates = []
    all_candidates.extend(scan_defillama(scan_id=scan_id))
    time.sleep(2)
    all_candidates.extend(scan_cryptorank(scan_id=scan_id))

    # Deduplicate by name
    seen = {}
    for c in all_candidates:
        key = c["name"].lower().strip()
        if key not in seen or c["funding_usd"] > seen[key]["funding_usd"]:
            seen[key] = c

    unique = list(seen.values())
    logger.info("[%s] Funding scan: %d unique candidates", scan_id, len(unique))

    # Pre-score using cached data only (no API calls) to cut candidates
    # before expensive CoinGecko checks. Projects with no VC, no funding,
    # and no novel tech will never score above threshold regardless of token status.
    # v1.0: raised from 2.0 to 3.5 — at 2.0 almost all 544 candidates
    # passed, causing 6h+ scans due to CoinGecko rate limiting.
    # At 3.5: only projects with real VC (tier-2+) OR $20M+ funding
    # reach the token check. Expected: 544 → ~80-100 candidates.
    PRE_SCORE_MIN = 3.5

    results = []
    skipped_pre = 0
    for project in unique:
        name = project["name"]
        try:
            # Blockchain relevance check — skip non-blockchain companies
            if not _is_blockchain_relevant(project):
                logger.debug(
                    "[%s] '%s' not blockchain relevant — skipping",
                    scan_id, name
                )
                skipped_pre += 1
                continue

            # Quick pre-score with no API calls
            desc = project.get("description", "")
            project["novel_tech"] = _detect_novel_tech(desc)
            project["testnet_active"] = _detect_testnet(desc)
            pre_score = score_project(project, caller_tier=2, caller_count=1)

            if pre_score.score < PRE_SCORE_MIN:
                skipped_pre += 1
                continue

            time.sleep(1.5)

            if not verify_no_token(name, scan_id=scan_id):
                continue

            score_result = pre_score  # Already scored above — reuse

            logger.info(
                "[%s] funding scored project='%s' score=%.1f label='%s'",
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
            logger.error(
                "[%s] funding scan error project='%s': %s", scan_id, name, exc
            )

    qualified = [
        r for r in results
        if r["score_result"].score >= settings.GENESIS_THRESHOLD
    ]

    logger.info(
        "[%s] Funding scan complete — total=%d qualified=%d "
        "(pre-filtered=%d threshold=%.1f)",
        scan_id, len(results), len(qualified),
        skipped_pre, settings.GENESIS_THRESHOLD
    )
    log_scan("funding_scanner", len(qualified), notes=f"scan_id={scan_id}")
    return qualified
