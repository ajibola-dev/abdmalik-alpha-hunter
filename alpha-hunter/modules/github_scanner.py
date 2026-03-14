"""
modules/github_scanner.py
Scans GitHub for technical signals BEFORE they appear on Twitter.

v0.5 changes:
  - _get() replaced with _http_get() — reads MAX_RETRIES and
    RETRY_BACKOFF_BASE from settings, identical to other modules.
    Was hardcoded retries=3; 403 rate-limit handling preserved.
  - Structured logging: every log line now includes scan_id and
    project_name where relevant.
  - scan_github() and run_github_scan() accept scan_id parameter
    propagated from pipeline.run_github_scan_cycle().
  - _make_scan_id() imported from pipeline for consistent IDs.
  - __import__('json') inline replaced with top-level import.

What we look for (unchanged):
  - New testnet repos published in last 30 days
  - Node/validator programs with recent activity
  - ZK/FHE/DePIN/AI projects with fresh commits
  - Repos with airdrop eligibility signals in README
"""
import json
import logging
import time
import requests
from datetime import datetime, timezone
from config.settings import settings
from modules.database import upsert_project, save_score, log_scan

logger = logging.getLogger(__name__)

_SESSION = requests.Session()

# ── GitHub search queries ──────────────────────────────────────────────────

_GITHUB_QUERIES = [
    "blockchain testnet validator 2025",
    "layer1 testnet node operator 2025",
    "zkvm testnet launch 2025",
    "fhe blockchain homomorphic 2025",
    "depin node operator testnet",
    "zk proof system testnet validator",
    "ai blockchain inference testnet",
    "restaking avs operator 2025",
    "airdrop eligibility testnet participation",
    "node operator airdrop incentive",
]

_SERIOUS_SIGNALS = [
    "testnet", "validator", "node operator", "mainnet", "zkp",
    "fhe", "depin", "layer 1", "l1 blockchain", "zk rollup",
    "restaking", "modular blockchain", "consensus", "prover",
]

_NOISE_SIGNALS = [
    "tutorial", "example", "demo", "sample", "boilerplate",
    "learn", "course", "workshop", "test", "practice",
    "fork of", "clone of",
]


# ── Shared HTTP helper (settings-driven retry + backoff) ──────────────────

def _http_get(url, params=None, scan_id=""):
    """
    Standardised GET for GitHub API with exponential backoff.
    v0.5: reads MAX_RETRIES and RETRY_BACKOFF_BASE from settings.
    Handles 403 rate-limit with a 60s pause (GitHub-specific).
    Always attaches GITHUB_TOKEN if available.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    for attempt in range(settings.MAX_RETRIES):
        try:
            r = _SESSION.get(
                url,
                params=params,
                headers=headers,
                timeout=settings.REQUEST_TIMEOUT,
            )
            if r.status_code == 403:
                wait = 60 * (attempt + 1)
                logger.warning(
                    "[%s] GitHub rate limited — sleeping %ds (attempt %d/%d)",
                    scan_id, wait, attempt + 1, settings.MAX_RETRIES
                )
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except Exception as exc:
            wait = settings.RETRY_BACKOFF_BASE ** attempt
            if attempt < settings.MAX_RETRIES - 1:
                logger.debug(
                    "[%s] GitHub GET failed (attempt %d/%d): %s — retry in %ds",
                    scan_id, attempt + 1, settings.MAX_RETRIES, exc, wait
                )
                time.sleep(wait)
            else:
                logger.debug(
                    "[%s] GitHub GET failed after %d attempts: %s",
                    scan_id, settings.MAX_RETRIES, exc
                )
    return None


# ── Repo scoring ───────────────────────────────────────────────────────────

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

    if any(n in all_text for n in _NOISE_SIGNALS):
        return 0.0

    hits = sum(1 for s in _SERIOUS_SIGNALS if s in all_text)
    score += min(hits * 2.0, 6.0)

    stars = repo.get("stargazers_count", 0)
    if stars >= 500:
        score += 2.0
    elif stars >= 100:
        score += 1.0
    elif stars >= 20:
        score += 0.5

    days = _days_since_update(repo.get("updated_at", ""))
    if days <= 7:
        score += 2.0
    elif days <= 30:
        score += 1.0

    return min(score, 10.0)


def _extract_org_name(repo: dict) -> str:
    full_name = repo.get("full_name", "")
    owner = full_name.split("/")[0] if "/" in full_name else ""
    if repo.get("owner", {}).get("type") == "Organization":
        return owner
    return repo.get("name", "").replace("-", " ").replace("_", " ").title()


# ── Main GitHub scan ───────────────────────────────────────────────────────

def scan_github(scan_id: str = "") -> list[dict]:
    """
    Scan GitHub for early-stage blockchain projects.
    v0.5: scan_id propagated through all log lines.
    """
    candidates = {}

    for query in _GITHUB_QUERIES:
        logger.debug("[%s] GitHub query: %s", scan_id, query)
        resp = _http_get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "updated", "order": "desc", "per_page": 10},
            scan_id=scan_id,
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

                if (org_name not in candidates or
                        repo_score > candidates[org_name]["repo_score"]):

                    desc = repo.get("description") or ""
                    topics = repo.get("topics", [])
                    combined = desc + " " + " ".join(topics)

                    from modules.funding_scanner import _detect_category
                    from modules.researcher import _detect_novel_tech, _detect_testnet

                    category = _detect_category(combined)
                    novel_tech = _detect_novel_tech(combined)
                    testnet = _detect_testnet(combined)

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

                    logger.debug(
                        "[%s] GitHub candidate project='%s' "
                        "stars=%d repo_score=%.1f",
                        scan_id, org_name,
                        repo.get("stargazers_count", 0), repo_score
                    )

        except Exception as exc:
            logger.error("[%s] GitHub query error query='%s': %s",
                         scan_id, query, exc)

        time.sleep(3)

    logger.info("[%s] GitHub scan: %d unique orgs found",
                scan_id, len(candidates))
    return list(candidates.values())


def run_github_scan(scan_id: str = "") -> list[dict]:
    """
    Full GitHub scan pipeline with scoring and DB persistence.
    v0.5: scan_id propagated, structured log context throughout.
    """
    from modules.scorer import score_project
    from modules.researcher import check_token_live

    if not scan_id:
        from modules.pipeline import _make_scan_id
        scan_id = _make_scan_id()

    logger.info("[%s] ── GitHub Scanner starting ──────────────", scan_id)

    candidates = scan_github(scan_id=scan_id)
    results = []

    for project in candidates:
        name = project["name"]
        try:
            time.sleep(1.5)

            if check_token_live(name):
                logger.debug(
                    "[%s] project='%s' token live — skipping", scan_id, name
                )
                continue

            score_result = score_project(project, caller_tier=2, caller_count=1)

            logger.info(
                "[%s] github scored project='%s' score=%.1f label='%s'",
                scan_id, name, score_result.score, score_result.label
            )

            project_id = upsert_project(
                name=name,
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
                breakdown=json.dumps(score_result.breakdown),
                label=score_result.label,
            )

            results.append({
                "project": project,
                "project_id": project_id,
                "score_result": score_result,
            })

        except Exception as exc:
            logger.error(
                "[%s] GitHub scan error project='%s': %s", scan_id, name, exc
            )

    qualified = [
        r for r in results
        if r["score_result"].score >= settings.GENESIS_THRESHOLD
    ]

    logger.info(
        "[%s] GitHub scan complete — total=%d qualified=%d (threshold=%d)",
        scan_id, len(results), len(qualified), settings.GENESIS_THRESHOLD
    )
    log_scan("github_scanner", len(qualified), notes=f"scan_id={scan_id}")
    return qualified
