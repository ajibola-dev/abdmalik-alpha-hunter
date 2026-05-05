"""
modules/github_scanner.py — v2.0

Finds new testnet repos from serious teams in the 4 verticals.
This is the autonomous signal source — no Twitter needed.

The pattern: Jito, Starknet, Celestia all had GitHub activity
months before mainstream Twitter coverage. This scanner catches
that window.

Search queries per vertical:
  ZK/L2:       "testnet" in:readme language:python OR language:rust
  PerpDEX:     "perpetual" OR "perp" testnet topic:defi
  Solana DeFi: "solana" testnet points-program
  Cosmos/DA:   "cosmos" OR "celestia" testnet validator
"""
import logging
import time
import requests
from datetime import datetime, timezone
from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

_GITHUB_QUERIES = {
    "zk_l2": [
        "zkvm testnet",
        "zk rollup testnet incentivized",
        "starknet compatible testnet",
        "fhe blockchain testnet",
        "zero knowledge testnet validator",
    ],
    "perpdex": [
        "perpetual dex testnet points",
        "perp protocol testnet incentive",
        "onchain perpetual trading testnet",
    ],
    "solana_defi": [
        "solana defi testnet points program",
        "solana lp incentive testnet",
        "svm testnet airdrop",
    ],
    "cosmos_da": [
        "cosmos data availability testnet",
        "celestia rollup testnet validator",
        "modular blockchain testnet ibc",
        "cosmos chain testnet incentivized",
    ],
}

# Funds with airdrop track record — boost signal if in repo description
_SIGNAL_FUNDS = [
    "paradigm", "a16z", "multicoin", "polychain", "dragonfly",
    "electric capital", "binance labs", "coinbase ventures",
    "1kx", "hack vc",
]

_GENESIS_THRESHOLD = 5.0  # Minimum score to alert


def _get_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
    return headers


def _search_repos(query: str, vertical: str) -> list[dict]:
    """Search GitHub repos for a query."""
    try:
        resp = _SESSION.get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": 10,
            },
            headers=_get_headers(),
            timeout=settings.REQUEST_TIMEOUT,
        )
        if resp.status_code == 403:
            logger.warning("GitHub rate limited")
            time.sleep(60)
            return []
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:
        logger.debug("GitHub search error: %s", exc)
        return []

    results = []
    for repo in items:
        # Skip repos older than 12 months
        pushed = repo.get("pushed_at", "")
        try:
            dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
            days_old = (datetime.now(timezone.utc) - dt).days
            if days_old > 365:
                continue
        except Exception:
            continue

        # Skip very new repos with no stars (likely noise)
        stars = repo.get("stargazers_count", 0)
        created = repo.get("created_at", "")
        try:
            dt_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - dt_created).days
        except Exception:
            age_days = 999

        # Filter: must have some traction OR be very recent
        if stars < 5 and age_days > 60:
            continue

        # Skip repos that are clearly forks of known projects
        if repo.get("fork", False):
            continue

        description = (repo.get("description", "") or "").lower()
        topics = repo.get("topics", [])

        results.append({
            "name": repo.get("name", ""),
            "full_name": repo.get("full_name", ""),
            "description": repo.get("description", "") or "",
            "url": repo.get("html_url", ""),
            "stars": stars,
            "days_since_commit": days_old,
            "age_days": age_days,
            "topics": topics,
            "vertical": vertical,
            "query": query,
        })

    return results


def _score_github_project(repo: dict) -> float:
    """
    Score a GitHub-discovered project.
    Uses same pattern-matching logic as scorer.py but adapted for
    GitHub-only data (no tweet context).
    """
    score = 0.0
    desc = repo.get("description", "").lower()
    topics = [t.lower() for t in repo.get("topics", [])]
    all_text = desc + " " + " ".join(topics)
    vertical = repo.get("vertical", "infrastructure")

    # Vertical score
    vertical_scores = {
        "zk_l2": 6, "perpdex": 6, "solana_defi": 4,
        "cosmos_da": 4, "infrastructure": 2,
    }
    score += vertical_scores.get(vertical, 2)

    # Testnet signal in description/topics
    testnet_words = ["testnet", "devnet", "incentivized", "validator",
                     "node", "prover", "points"]
    if any(w in all_text for w in testnet_words):
        score += 4

    # Backed by serious fund (mentioned in description)
    for fund in _SIGNAL_FUNDS:
        if fund in all_text:
            score += 3
            break

    # Recency bonus
    days = repo.get("days_since_commit", 999)
    if days < 7:
        score += 3
    elif days < 30:
        score += 2
    elif days < 90:
        score += 1

    # Stars as social proof
    stars = repo.get("stars", 0)
    if stars > 500:
        score += 2
    elif stars > 100:
        score += 1

    # Age sweet spot: 3-18 months
    age = repo.get("age_days", 999)
    if 90 <= age <= 540:
        score += 1

    return round(score, 1)


def run_github_scan(scan_id: str = "") -> list[dict]:
    """
    Run GitHub scan across all 4 verticals.
    Returns list of qualifying projects.
    """
    from modules.researcher import check_token_live
    from modules.action_planner import format_farming_brief_for_telegram
    from modules.scorer import FarmingBrief
    from modules.telegram_bot import send_message

    logger.info("[%s] ── GitHub Scanner starting ──────────────", scan_id)

    all_repos = {}

    for vertical, queries in _GITHUB_QUERIES.items():
        for query in queries:
            time.sleep(3)  # Gentle GitHub rate limiting
            repos = _search_repos(query, vertical)
            for repo in repos:
                key = repo["full_name"]
                if key not in all_repos:
                    all_repos[key] = repo
                    logger.debug("[%s] Found: %s (%s)",
                                 scan_id, repo["name"], vertical)

    logger.info("[%s] GitHub scan: %d unique repos found",
                scan_id, len(all_repos))

    qualified = []
    alerts_sent = 0

    for full_name, repo in all_repos.items():
        name = repo["name"]
        score = _score_github_project(repo)

        if score < _GENESIS_THRESHOLD:
            continue

        # Token check
        time.sleep(1)
        if check_token_live(name):
            logger.info("[%s] '%s' token live — skip", scan_id, name)
            continue

        logger.info("[%s] GitHub qualified: '%s' score=%.1f vertical=%s",
                    scan_id, name, score, repo["vertical"])

        # Store in DB
        project_id = db.upsert_project(
            name=name,
            mentioned_by="[github]",
            tweet_url=repo["url"],
            tweet_text=repo["description"],
            category=repo["vertical"],
            funding_usd=0,
            investors="",
            has_token=False,
            testnet_active=True,
            website="",
            github=repo["url"],
            description=repo["description"],
        )
        db.save_score(
            project_id=project_id,
            score=score,
            breakdown="{}",
            label="WATCH CLOSELY" if score >= 8 else "MONITOR",
        )

        if not db.already_alerted(project_id, "github"):
            # Build a simple farming brief for GitHub-discovered projects
            brief = FarmingBrief(
                project_name=name,
                vertical=repo["vertical"],
                conviction="WATCH CLOSELY" if score >= 8 else "MONITOR",
                why=f"GitHub active ({repo['days_since_commit']}d ago). "
                    f"{repo['description'][:100]}",
                window="Early — check for Discord and Galxe status.",
                actions=[
                    f"Visit {repo['url']} and read the README",
                    "Find their official Twitter/Discord",
                    "Check if testnet is open and interact immediately",
                    "Look for any points/farming program in docs",
                ],
                wallet_count=2 if score >= 8 else 1,
                zero_cost=True,
                red_flags=[],
                raw_signals={"source": "github", "score": score},
            )

            project_for_alert = {
                "name": name,
                "mentioned_by": "[github]",
                "tweet_url": repo["url"],
                "tweet_text": repo["description"],
                "funding_usd": 0,
                "investors": "",
            }

            message = (
                "⚙️ <b>ALPHA HUNTER — GITHUB SIGNAL</b>\n"
                f"<i>Found via GitHub scan — {repo['vertical'].upper()} vertical</i>\n\n"
            ) + format_farming_brief_for_telegram(brief, project_for_alert)

            if send_message(message):
                db.log_alert(project_id, "github")
                alerts_sent += 1

        qualified.append(repo)

    logger.info("[%s] GitHub scan complete — total=%d qualified=%d alerts=%d",
                scan_id, len(all_repos), len(qualified), alerts_sent)
    db.log_scan("github_scanner", alerts_sent, notes=f"scan_id={scan_id}")
    return qualified
