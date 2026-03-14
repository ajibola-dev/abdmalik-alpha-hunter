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
    "bitcoin", "ethereum", "solana", "bnb", "polygon", "avalanche",
    "cardano", "polkadot", "cosmos", "tron", "litecoin", "dogecoin",
    "shiba", "pepe", "floki", "uniswap", "aave", "compound",
    "mantra", "mocaverse", "moca", "monad",
}

_MIN_FUNDING = {
    "Layer 1": 20_000_000, "Layer 2": 15_000_000,
    "ZK/Privacy": 10_000_000, "FHE": 5_000_000,
    "DePIN": 10_000_000, "AI/ML": 10_000_000,
    "Payments": 15_000_000, "Gaming": 10_000_000,
    "RWA": 8_000_000, "Restaking": 10_000_000,
    "Modular": 10_000_000, "Infrastructure": 8_000_000,
    "default": 5_000_000,
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

        if age_days > 730:
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

def verify_no_token(project_name: str, scan_id: str = "") -> bool:
    """Returns True if NO live token found."""
    resp = _http_get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
        scan_id=scan_id,
    )
    if resp is None:
        return True
    try:
        coins = resp.json().get("coins", [])
        for coin in coins[:3]:
            coin_name = coin.get("name", "").lower()
            proj_lower = project_name.lower()
            if proj_lower == coin_name or proj_lower in coin_name.split():
                logger.debug(
                    "[%s] project='%s' token live on CoinGecko — skipping",
                    scan_id, project_name
                )
                return False
    except Exception:
        pass
    return True


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

    results = []
    for project in unique:
        name = project["name"]
        try:
            time.sleep(1.5)

            if not verify_no_token(name, scan_id=scan_id):
                continue

            desc = project.get("description", "")
            project["novel_tech"] = _detect_novel_tech(desc)
            project["testnet_active"] = _detect_testnet(desc)

            score_result = score_project(project, caller_tier=2, caller_count=1)

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
        "[%s] Funding scan complete — total=%d qualified=%d (threshold=%d)",
        scan_id, len(results), len(qualified), settings.GENESIS_THRESHOLD
    )
    log_scan("funding_scanner", len(qualified), notes=f"scan_id={scan_id}")
    return qualified
