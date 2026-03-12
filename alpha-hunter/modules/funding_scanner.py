"""
modules/funding_scanner.py
Scans funding databases for early-stage projects with no token yet.
This is the "Bot 1 upgrade" — broader categories, smarter filtering,
Zun-method scoring instead of simple threshold checks.

Sources:
  - DeFiLlama raises (free, 6,800+ funding rounds)
  - CryptoRank public page (scraping)
  - Messari funding (scraping)

Categories tracked (ALL of these — unlike Bot 1 which only did L1/GameFi):
  Layer 1, Layer 2, ZK/Privacy, FHE, DePIN, AI/ML, Payments,
  Gaming/NFT, Infrastructure, RWA, Social, Identity, Oracle,
  Restaking, Modular, Cross-chain
"""
import logging
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from config.settings import settings
from modules.database import upsert_project, save_score, already_alerted, log_scan

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# ── Category keyword map ───────────────────────────────────────────────────
# Maps keywords found in project descriptions → our category labels
_CATEGORY_MAP = {
    # Infrastructure tiers
    "layer 1": "Layer 1", "l1 blockchain": "Layer 1", "layer1": "Layer 1",
    "layer 2": "Layer 2", "l2": "Layer 2", "rollup": "Layer 2",
    "optimistic": "Layer 2", "zk rollup": "ZK/Privacy",

    # Cryptography / Privacy
    "zero knowledge": "ZK/Privacy", "zkp": "ZK/Privacy", "zk proof": "ZK/Privacy",
    "zkvm": "ZK/Privacy", "fully homomorphic": "FHE", "fhe": "FHE",
    "privacy": "Privacy", "mpc": "Privacy",

    # Physical / Real world
    "depin": "DePIN", "decentralized physical": "DePIN",
    "wireless": "DePIN", "energy": "DePIN", "iot": "DePIN",

    # AI / ML
    "artificial intelligence": "AI/ML", "machine learning": "AI/ML",
    "ai agent": "AI/ML", "on-chain ai": "AI/ML", "inference": "AI/ML",
    "compute": "AI/ML",

    # Finance / Payments
    "payments": "Payments", "stablecoin": "Payments", "remittance": "Payments",
    "cross-border": "Payments", "cbdc": "Payments",

    # Gaming
    "gaming": "Gaming", "game": "Gaming", "nft": "Gaming",
    "play to earn": "Gaming", "metaverse": "Gaming",

    # Real world assets
    "real world asset": "RWA", "rwa": "RWA", "tokenized": "RWA",
    "real estate": "RWA", "commodities": "RWA",

    # Social / Identity
    "social": "Social", "identity": "Identity", "did": "Identity",
    "reputation": "Identity", "soulbound": "Identity",

    # Restaking / Shared security
    "restaking": "Restaking", "shared security": "Restaking",
    "avs": "Restaking", "eigenlayer": "Restaking",

    # Modular / Interop
    "modular": "Modular", "data availability": "Modular",
    "interoperability": "Cross-chain", "bridge": "Cross-chain",
    "cross-chain": "Cross-chain",

    # Oracles / Data
    "oracle": "Oracle", "data feed": "Oracle", "price feed": "Oracle",

    # General infrastructure
    "infrastructure": "Infrastructure", "developer tool": "Infrastructure",
    "middleware": "Infrastructure", "sdk": "Infrastructure",
}

# Projects to skip (known launched or irrelevant)
_BLOCKLIST = {
    "bitcoin", "ethereum", "solana", "bnb", "polygon", "avalanche",
    "cardano", "polkadot", "cosmos", "tron", "litecoin", "dogecoin",
    "shiba", "pepe", "floki", "uniswap", "aave", "compound",
    "mantra", "mocaverse", "moca", "monad",  # already launched
}

# Minimum funding thresholds by category (USD)
_MIN_FUNDING = {
    "Layer 1": 20_000_000,
    "Layer 2": 15_000_000,
    "ZK/Privacy": 10_000_000,
    "FHE": 5_000_000,
    "DePIN": 10_000_000,
    "AI/ML": 10_000_000,
    "Payments": 15_000_000,
    "Gaming": 10_000_000,
    "RWA": 8_000_000,
    "Restaking": 10_000_000,
    "Modular": 10_000_000,
    "Infrastructure": 8_000_000,
    "default": 5_000_000,
}


def _get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, params=params,
                             timeout=settings.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt == retries - 1:
                logger.debug("GET %s failed: %s", url, exc)
            time.sleep(2 ** attempt)
    return None


def _detect_category(text: str) -> str:
    """Detect project category from description/name text."""
    lower = text.lower()
    for keyword, category in _CATEGORY_MAP.items():
        if keyword in lower:
            return category
    return "Infrastructure"


def _is_blocklisted(name: str) -> bool:
    return any(b in name.lower() for b in _BLOCKLIST)


def _days_since(timestamp) -> int:
    """Days since a Unix timestamp or date string."""
    try:
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except Exception:
        return 9999


# ── DeFiLlama raises scanner ───────────────────────────────────────────────

_defillama_cache = []
_defillama_cache_time = 0


def _get_defillama_raises() -> list:
    global _defillama_cache, _defillama_cache_time
    age = time.time() - _defillama_cache_time
    if _defillama_cache and age < 21600:
        return _defillama_cache
    resp = _get("https://api.llama.fi/raises")
    if resp is None:
        return _defillama_cache
    try:
        _defillama_cache = resp.json().get("raises", [])
        _defillama_cache_time = time.time()
        logger.info("DeFiLlama: %d raises cached", len(_defillama_cache))
    except Exception as exc:
        logger.error("DeFiLlama parse error: %s", exc)
    return _defillama_cache


def scan_defillama() -> list[dict]:
    """
    Scan DeFiLlama raises for promising unfunded projects.
    Returns list of candidate project dicts.
    """
    raises = _get_defillama_raises()
    candidates = []

    for r in raises:
        name = r.get("name", "").strip()
        if not name or _is_blocklisted(name):
            continue

        amount = float(r.get("amount", 0) or 0) * 1_000_000
        date = r.get("date", 0)
        age_days = _days_since(date)

        # Skip old raises
        if age_days > 730:  # 2 years
            continue

        # Build description from available fields
        desc_parts = [
            r.get("category", ""),
            r.get("description", ""),
            r.get("name", ""),
        ]
        desc = " ".join(str(p) for p in desc_parts if p)
        category = _detect_category(desc)

        # Check minimum funding
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
            "has_token": False,  # will be verified
            "testnet_active": False,
            "novel_tech": [],
        })

    logger.info("DeFiLlama scan: %d candidates after filters", len(candidates))
    return candidates


# ── CryptoRank scanner ─────────────────────────────────────────────────────

def scan_cryptorank() -> list[dict]:
    """Scrape CryptoRank upcoming projects page."""
    candidates = []
    url = "https://cryptorank.io/upcoming-ico"
    resp = _get(url)
    if resp is None:
        logger.warning("CryptoRank scan failed")
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

            desc_el = row.select_one(
                ".description, [class*='desc'], p"
            )
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
        logger.error("CryptoRank parse error: %s", exc)

    logger.info("CryptoRank scan: %d candidates", len(candidates))
    return candidates


# ── Token verification ─────────────────────────────────────────────────────

def verify_no_token(project_name: str) -> bool:
    """Returns True if NO live token found (safe to research further)."""
    resp = _get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name}
    )
    if resp is None:
        return True  # Can't verify — assume no token
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:3]:
            coin_name = coin.get("name", "").lower()
            proj_lower = project_name.lower()
            if proj_lower == coin_name or proj_lower in coin_name.split():
                return False  # Token exists
    except Exception:
        pass
    return True


# ── Main funding scan pipeline ────────────────────────────────────────────

def run_funding_scan() -> list[dict]:
    """
    Full funding scan across all sources.
    Returns scored candidates ready for alerting.
    """
    from modules.scorer import score_project
    from modules.researcher import _detect_novel_tech, _detect_testnet

    logger.info("=" * 50)
    logger.info("💰 Funding Scanner Starting")
    logger.info("=" * 50)

    # Gather from all sources
    all_candidates = []
    all_candidates.extend(scan_defillama())
    time.sleep(2)
    all_candidates.extend(scan_cryptorank())

    # Deduplicate by name
    seen = {}
    for c in all_candidates:
        key = c["name"].lower().strip()
        if key not in seen:
            seen[key] = c
        else:
            # Merge — prefer higher funding
            if c["funding_usd"] > seen[key]["funding_usd"]:
                seen[key] = c

    unique = list(seen.values())
    logger.info("Funding scan: %d unique candidates", len(unique))

    results = []
    for project in unique:
        try:
            # Verify no token (with rate limit protection)
            time.sleep(1.5)
            if not verify_no_token(project["name"]):
                logger.debug("Skipping %s — token live", project["name"])
                continue

            # Detect novel tech from description
            desc = project.get("description", "")
            project["novel_tech"] = _detect_novel_tech(desc)
            project["testnet_active"] = _detect_testnet(desc)

            # Score
            score_result = score_project(
                project, caller_tier=2, caller_count=1
            )

            # Save to DB
            project_id = upsert_project(
                name=project["name"],
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
            logger.error("Funding scan error for '%s': %s",
                         project["name"], exc)

    qualified = [r for r in results
                 if r["score_result"].score >= settings.GENESIS_THRESHOLD]

    logger.info("Funding scan complete — %d qualified (score ≥ %d)",
                len(qualified), settings.GENESIS_THRESHOLD)
    log_scan("funding_scanner", len(qualified))
    return qualified
