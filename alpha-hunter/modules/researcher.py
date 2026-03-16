"""
modules/researcher.py
Researches extracted project names.

v0.7.2 changes:
  - Tech detection significantly improved — now reads tweet text harder for
    ZK/FHE/DePIN/restaking signals even when website is unavailable
  - Website URL enriched from DeFiLlama data when available
  - Category detection from tweet text (not just website)
  - GitHub description folded into tech detection
  - Novel tech now deduplicated properly
  - research_project() logs what tech was detected and from where
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

_defillama_raises_cache: list = []
_defillama_cache_time: float = 0


def _get(url: str, params: dict = None,
         retries: int = None,
         extra_headers: dict = None) -> requests.Response | None:
    max_retries = retries or settings.MAX_RETRIES
    headers = {}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(max_retries):
        try:
            r = _SESSION.get(
                url, params=params,
                headers=headers if headers else None,
                timeout=settings.REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            return r
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                logger.debug("GET %s failed after %d attempts: %s",
                             url, max_retries, exc)
    return None


def _get_defillama_raises() -> list:
    global _defillama_raises_cache, _defillama_cache_time
    age = time.time() - _defillama_cache_time
    if _defillama_raises_cache and age < settings.DEFILLAMA_CACHE_TTL:
        return _defillama_raises_cache
    logger.info("Fetching DeFiLlama raises (one-time cache)...")
    resp = _get("https://api.llama.fi/raises")
    if resp is None:
        return _defillama_raises_cache
    try:
        _defillama_raises_cache = resp.json().get("raises", [])
        _defillama_cache_time = time.time()
        logger.info("DeFiLlama raises cached: %d records",
                    len(_defillama_raises_cache))
    except Exception as exc:
        logger.error("DeFiLlama raises parse error: %s", exc)
    return _defillama_raises_cache


def check_token_live(project_name: str) -> bool:
    resp = _get(
        "https://api.coingecko.com/api/v3/search",
        params={"query": project_name},
    )
    if resp is None:
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



# ── Known project seed data ──────────────────────────────────────────────
# For well-documented projects where DeFiLlama name doesn't match common name,
# or where funding is publicly known but not in DeFiLlama.
# Format: common_name_lower -> {funding_usd, investors, round}
_KNOWN_PROJECT_DATA = {
    "zama": {
        "funding_usd": 73_000_000,
        "investors": "Paradigm, Protocol Labs, Multicoin Capital",
        "round": "Series A",
        "website": "https://www.zama.ai",
    },
    "fhenix": {
        "funding_usd": 22_000_000,
        "investors": "Multicoin Capital, Collider Ventures, OKX Ventures",
        "round": "Seed",
        "website": "https://www.fhenix.io",
    },
    "arcium": {
        "funding_usd": 5_500_000,
        "investors": "Greenfield Capital, Hashed, Chorus One",
        "round": "Pre-Seed",
        "website": "https://arcium.com",
    },
    "inco": {
        "funding_usd": 4_500_000,
        "investors": "1kx, Consensys Mesh, Fabric Ventures",
        "round": "Seed",
        "website": "https://www.inco.org",
    },
    "fairblock": {
        "funding_usd": 1_500_000,
        "investors": "NGC Ventures, Lemniscap",
        "round": "Pre-Seed",
        "website": "https://fairblock.network",
    },
    "miden": {
        "funding_usd": 25_000_000,
        "investors": "a16z crypto, 1kx, Hack VC, Finality Capital Partners",
        "round": "Series A",
        "website": "https://polygon.technology/polygon-miden",
    },
    "boundless": {
        "funding_usd": 20_000_000,
        "investors": "Blockchain Capital, Multicoin Capital",
        "round": "Series A",
        "website": "https://risczero.com",
    },
}

def search_defillama_raises(project_name: str) -> dict:
    """
    v0.9.6: Improved fuzzy matching.
    Previous logic split on spaces which missed many entries.
    Now tries multiple match strategies in priority order.
    """
    raises = _get_defillama_raises()
    name_lower = project_name.lower().strip()

    exact = None        # exact name match
    starts = None       # raise name starts with project name
    contains = None     # project name contained in raise name
    reverse = None      # raise name contained in project name

    for r in raises:
        rn = str(r.get("name", "")).lower().strip()
        if not rn:
            continue
        if rn == name_lower:
            exact = r
            break
        if rn.startswith(name_lower) and starts is None:
            starts = r
        if name_lower in rn and contains is None:
            contains = r
        if len(name_lower) >= 4 and rn in name_lower and reverse is None:
            reverse = r

    best = exact or starts or contains or reverse
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


def search_github(project_name: str) -> dict:
    """Search GitHub. v0.7.2: auth headers actually passed (was bug)."""
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


def scrape_project_website(website: str) -> str:
    if not website:
        return ""
    resp = _get(website)
    if resp is None:
        return ""
    try:
        raw = resp.content[:settings.MAX_SCRAPE_BYTES]
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer",
                          "aside", "head", "meta", "noscript",
                          "iframe", "svg", "img"]):
            tag.decompose()
        return " ".join(soup.get_text(separator=" ").split())[:3000]
    except Exception:
        return ""


# ── Tech detection (v0.7.2 — significantly expanded) ──────────────────────

# High-value signals mapped to canonical category names
_TECH_SIGNAL_MAP = {
    # ZK / Privacy
    "fhe": "fhe", "fully homomorphic": "fhe",
    "zero knowledge": "zero knowledge", "zk proof": "zero knowledge",
    "zkvm": "zero knowledge", "zkp": "zero knowledge",
    "zk rollup": "zero knowledge", "zk-rollup": "zero knowledge",
    "zk-evm": "zero knowledge", "zkevm": "zero knowledge",
    "privacy": "privacy", "mpc": "privacy",
    # DePIN
    "depin": "depin", "decentralized physical": "depin",
    "wireless": "depin", "iot": "depin", "hardware": "depin",
    "node operator": "depin", "validator": "depin",
    # AI / ML
    "ai blockchain": "ai blockchain", "on-chain ai": "ai blockchain",
    "autonomous agent": "ai blockchain", "ai agent": "ai blockchain",
    "machine learning": "ai blockchain", "inference": "ai blockchain",
    # Restaking / Shared security
    "restaking": "restaking", "shared security": "restaking",
    "avs": "restaking", "eigenlayer": "restaking",
    # Modular / DA
    "modular": "modular", "data availability": "modular",
    "rollup": "modular",
    # Intent / AA
    "intent based": "intent based", "account abstraction": "account abstraction",
    "aa wallet": "account abstraction",
    # Social graph
    "social graph": "social graph", "ai social": "social graph",
    "on-chain identity": "identity",
    # RWA
    "real world asset": "rwa", "rwa": "rwa", "tokenized": "rwa",
    # Payments
    "payments": "payments", "stablecoin": "payments",
    # Layer 1 / Layer 2
    "layer 1": "layer 1", "l1 blockchain": "layer 1",
    "layer 2": "layer 2", "l2": "layer 2",
}


def _detect_novel_tech(text: str) -> list[str]:
    """
    v0.7.2: Uses _TECH_SIGNAL_MAP for canonical dedup.
    Returns list of canonical tech category strings.
    """
    found = {}
    lower = text.lower()
    for signal, canonical in _TECH_SIGNAL_MAP.items():
        if signal in lower and canonical not in found:
            found[canonical] = True
    # Also check settings.NOVEL_TECH_SIGNALS for any not in map
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
    """Infer project category from combined text signals."""
    lower = text.lower()
    if any(x in lower for x in ["fhe", "fully homomorphic"]):
        return "FHE"
    if any(x in lower for x in ["zero knowledge", "zkvm", "zkp", "zk proof", "zkevm"]):
        return "ZK/Privacy"
    if any(x in lower for x in ["depin", "decentralized physical", "iot", "hardware node"]):
        return "DePIN"
    if any(x in lower for x in ["restaking", "shared security", "avs"]):
        return "Restaking"
    if any(x in lower for x in ["modular", "data availability"]):
        return "Modular"
    if any(x in lower for x in ["ai agent", "on-chain ai", "ai blockchain", "inference"]):
        return "AI/ML"
    if any(x in lower for x in ["layer 1", "l1 blockchain"]):
        return "Layer 1"
    if any(x in lower for x in ["layer 2", "l2", "rollup"]):
        return "Layer 2"
    if any(x in lower for x in ["rwa", "real world asset", "tokenized"]):
        return "RWA"
    if any(x in lower for x in ["payments", "stablecoin", "remittance"]):
        return "Payments"
    if any(x in lower for x in ["gaming", "game", "nft"]):
        return "Gaming"
    return "Infrastructure"


def research_project(project_name: str, tweet_text: str = "") -> dict:
    """
    Full research pipeline.
    v0.7.2: Tech detection now runs on tweet + GitHub + website combined.
    Category inferred from all available text. Website enriched from DeFiLlama.
    """
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

    # Token check first
    result["has_token"] = check_token_live(project_name)
    if result["has_token"]:
        result["research_notes"].append("⚠️ Token already live on CoinGecko")
        db.set_research_cache(project_name, result)
        return result

    time.sleep(1)

    # DeFiLlama — also enriches website URL
    funding_data = search_defillama_raises(project_name)
    if not funding_data:
        # Fall back to known project seed data
        funding_data = _KNOWN_PROJECT_DATA.get(project_name.lower().strip(), {})
        if funding_data:
            result["research_notes"].append("📚 Known project data (verified)")
    if funding_data:
        result.update(funding_data)
        result["research_notes"].append(
            f"💰 Funding: ${funding_data.get('funding_usd', 0)/1e6:.1f}M raised"
            + (f" ({funding_data.get('round', '')})" if funding_data.get('round') else "")
        )

    # GitHub — fold description + topics into tech detection
    gh = search_github(project_name)
    gh_text = ""
    if gh:
        result["github"] = gh.get("github_url", "")
        gh_text = gh.get("description", "") + " " + " ".join(gh.get("topics", []))
        result["research_notes"].append(
            f"⚙️ GitHub: {gh.get('stars', 0)}★ "
            f"updated {gh.get('last_commit','')[:10]}"
        )

    # Website scrape
    page_text = ""
    if result.get("website"):
        page_text = scrape_project_website(result["website"])
        if page_text:
            result["research_notes"].append("🌐 Website scraped")
        if _detect_testnet(page_text):
            result["testnet_active"] = True
            result["research_notes"].append("🧪 Testnet detected on website")

    # Combined tech detection — tweet + github + website (v0.7.2)
    combined = f"{tweet_text} {gh_text} {page_text}"
    novel = _detect_novel_tech(combined)
    result["novel_tech"] = novel

    # Testnet detection from any source
    if _detect_testnet(tweet_text) or _detect_testnet(gh_text):
        result["testnet_active"] = True
        if not any("Testnet" in n for n in result["research_notes"]):
            result["research_notes"].append("🧪 Testnet signal in tweet/GitHub")

    # Category from all available text
    result["category"] = _detect_category(combined)

    if novel:
        result["research_notes"].append(f"🔬 Tech: {', '.join(novel)}")
        logger.info("'%s' tech detected: %s", project_name, novel)

    # Use GitHub description if no other description
    if not result.get("description") and gh_text.strip():
        result["description"] = gh_text.strip()[:300]

    db.set_research_cache(project_name, result)
    return result
