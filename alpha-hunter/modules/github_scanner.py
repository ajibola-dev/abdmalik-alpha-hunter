"""
modules/github_scanner.py
Scans GitHub for technical signals BEFORE they appear on Twitter.

What we look for:
  - New testnet repos published in last 30 days
  - Node/validator programs with recent activity
  - ZK/FHE/DePIN/AI projects with fresh commits
  - Repos with "airdrop" eligibility signals in README

This catches projects at the most technical stage —
before crypto Twitter even knows they exist.
"""
import logging
import time
import requests
from datetime import datetime, timezone
from config.settings import settings
from modules.database import upsert_project, save_score, log_scan

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

# ── Search queries targeting early testnet/node activity ──────────────────
_GITHUB_QUERIES = [
    # Testnet launches
    "blockchain testnet validator 2025",
    "layer1 testnet node operator 2025",
    "zkvm testnet launch 2025",

    # Novel tech categories
    "fhe blockchain homomorphic 2025",
    "depin node operator testnet",
    "zk proof system testnet validator",
    "ai blockchain inference testnet",
    "restaking avs operator 2025",

    # Airdrop/eligibility signals
    "airdrop eligibility testnet participation",
    "node operator airdrop incentive",
]

# Words in repo description that signal a serious project
_SERIOUS_SIGNALS = [
    "testnet", "validator", "node operator", "mainnet", "zkp",
    "fhe", "depin", "layer 1", "l1 blockchain", "zk rollup",
    "restaking", "modular blockchain", "consensus", "prover",
]

# Words that disqualify a repo
_NOISE_SIGNALS = [
    "tutorial", "example", "demo", "sample", "boilerplate",
    "learn", "course", "workshop", "test", "practice",
    "fork of", "clone of",
]


def _get(url, params=None, retries=3):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    for attempt in range(retries):
        try:
            r = _SESSION.get(url, params=params, headers=headers,
                             timeout=settings.REQUEST_TIMEOUT)
            if r.status_code == 403:
                logger.warning("GitHub rate limited — sleeping 60s")
                time.sleep(60)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt == retries - 1:
                logger.debug("GitHub GET failed: %s", exc)
            time.sleep(2 ** attempt)
    return None


def _days_since_update(updated_at: str) -> int:
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


def _score_repo(repo: dict) -> float:
    """Score a GitHub repo 0-10 for relevance."""
    score = 0.0
    desc = (repo.get("description") or "").lower()
    name = (repo.get("name") or "").lower()
    topics = [t.lower() for t in repo.get("topics", [])]
    all_text = f"{desc} {name} {' '.join(topics)}"

    # Noise check
    if any(n in all_text for n in _NOISE_SIGNALS):
        return 0.0

    # Serious signals
    hits = sum(1 for s in _SERIOUS_SIGNALS if s in all_text)
    score += min(hits * 2.0, 6.0)

    # Stars (social proof)
    stars = repo.get("stargazers_count", 0)
    if stars >= 500:
        score += 2.0
    elif stars >= 100:
        score += 1.0
    elif stars >= 20:
        score += 0.5

    # Recent activity
    days = _days_since_update(repo.get("updated_at", ""))
    if days <= 7:
        score += 2.0
    elif days <= 30:
        score += 1.0

    return min(score, 10.0)


def _extract_org_name(repo: dict) -> str:
    """Extract organisation/project name from repo."""
    full_name = repo.get("full_name", "")
    owner = full_name.split("/")[0] if "/" in full_name else ""
    # Prefer org name over individual username
    if repo.get("owner", {}).get("type") == "Organization":
        return owner
    # Fall back to repo name cleaned up
    name = repo.get("name", "").replace("-", " ").replace("_", " ").title()
    return name


def scan_github() -> list[dict]:
    """
    Scan GitHub for early-stage blockchain projects.
    Returns list of candidate dicts.
    """
    candidates = {}  # org_name → best repo

    for query in _GITHUB_QUERIES:
        logger.debug("GitHub query: %s", query)
        resp = _get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": 10,
            }
        )
        if resp is None:
            continue

        try:
            items = resp.json().get("items", [])
            for repo in items:
                repo_score = _score_repo(repo)
                if repo_score < 4.0:
                    continue

                org_name = _extract_org_name(repo)
                if not org_name:
                    continue

                # Keep highest-scoring repo per org
                if (org_name not in candidates or
                        repo_score > candidates[org_name]["repo_score"]):
                    desc = repo.get("description") or ""
                    topics = repo.get("topics", [])

                    # Detect category
                    from modules.funding_scanner import _detect_category
                    category = _detect_category(
                        desc + " " + " ".join(topics)
                    )

                    # Detect novel tech
                    from modules.researcher import _detect_novel_tech, _detect_testnet
                    novel_tech = _detect_novel_tech(desc + " " + " ".join(topics))
                    testnet = _detect_testnet(desc + " " + " ".join(topics))

                    candidates[org_name] = {
                        "name": org_name,
                        "funding_usd": 0,
                        "investors": "",
                        "category": category,
                        "website": repo.get("homepage") or "",
                        "github": repo.get("html_url", ""),
                        "description": desc[:300],
                        "source": "github",
                        "novel_tech": novel_tech,
                        "testnet_active": testnet,
                        "has_token": False,
                        "stars": repo.get("stargazers_count", 0),
                        "repo_score": repo_score,
                        "last_commit": repo.get("updated_at", "")[:10],
                    }

        except Exception as exc:
            logger.error("GitHub query error: %s", exc)

        time.sleep(3)  # Respect GitHub rate limits

    logger.info("GitHub scan: %d unique orgs found", len(candidates))
    return list(candidates.values())


def run_github_scan() -> list[dict]:
    """
    Full GitHub scan pipeline with scoring and DB persistence.
    """
    from modules.scorer import score_project
    from modules.researcher import check_token_live

    logger.info("=" * 50)
    logger.info("⚙️  GitHub Scanner Starting")
    logger.info("=" * 50)

    candidates = scan_github()
    results = []

    for project in candidates:
        try:
            time.sleep(1.5)

            # Token check
            if check_token_live(project["name"]):
                logger.debug("Skipping %s — token live", project["name"])
                continue

            # Score
            score_result = score_project(
                project, caller_tier=2, caller_count=1
            )

            # Save to DB
            project_id = upsert_project(
                name=project["name"],
                mentioned_by="[github]",
                tweet_url=project.get("github", ""),
                tweet_text="",
                category=project["category"],
                funding_usd=0,
                investors="",
                has_token=False,
                testnet_active=project["testnet_active"],
                website=project["website"],
                github=project["github"],
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
            logger.error("GitHub scan error for '%s': %s",
                         project["name"], exc)

    qualified = [r for r in results
                 if r["score_result"].score >= settings.GENESIS_THRESHOLD]

    logger.info("GitHub scan complete — %d qualified", len(qualified))
    log_scan("github_scanner", len(qualified))
    return qualified
