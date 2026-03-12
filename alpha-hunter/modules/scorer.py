"""
modules/scorer.py
Scores projects using the Zun Method.
Focuses on: team quality, tech novelty, VC backing, community size, timing.
"""
import logging
import json
from dataclasses import dataclass
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    score: float
    label: str
    breakdown: dict
    verdict: str


def _has_top_vc(investors: str) -> bool:
    lower = investors.lower()
    return any(vc in lower for vc in settings.TOP_TIER_VCS)


def _count_top_vcs(investors: str) -> int:
    lower = investors.lower()
    return sum(1 for vc in settings.TOP_TIER_VCS if vc in lower)


def score_project(project: dict, caller_tier: int = 2,
                  caller_count: int = 1) -> ScoreResult:
    """
    Score a project 0-10 using the Zun Method.

    Scoring breakdown:
    - Tier-1 VC backing          : 0-2 pts
    - Novel technology            : 0-2 pts
    - Funding amount              : 0-2 pts
    - No token yet                : 1 pt
    - Active testnet              : 1 pt
    - Early community (small)     : 1 pt
    - Multiple callers agree      : 1 pt
    """
    score = 0.0
    breakdown = {}

    investors = project.get("investors", "")
    funding = project.get("funding_usd", 0) or 0
    has_token = project.get("has_token", False)
    testnet = project.get("testnet_active", False)
    novel_tech = project.get("novel_tech", [])

    # ── Immediate disqualifiers ────────────────────────────────────────────
    if has_token:
        return ScoreResult(
            score=0,
            label="Token Live ❌",
            breakdown={"disqualified": "Token already live"},
            verdict="Skip — token already launched."
        )

    # ── VC backing (0-2 pts) ───────────────────────────────────────────────
    top_vc_count = _count_top_vcs(investors)
    if top_vc_count >= 2:
        vc_pts = 2.0
    elif top_vc_count == 1:
        vc_pts = 1.0
    else:
        vc_pts = 0.0
    score += vc_pts
    breakdown["vc_backing"] = vc_pts

    # ── Novel technology (0-2 pts) ─────────────────────────────────────────
    if len(novel_tech) >= 2:
        tech_pts = 2.0
    elif len(novel_tech) == 1:
        tech_pts = 1.0
    else:
        tech_pts = 0.0
    score += tech_pts
    breakdown["novel_tech"] = tech_pts

    # ── Funding (0-2 pts) ──────────────────────────────────────────────────
    if funding >= 100_000_000:
        funding_pts = 2.0
    elif funding >= 50_000_000:
        funding_pts = 1.5
    elif funding >= 20_000_000:
        funding_pts = 1.0
    elif funding >= 5_000_000:
        funding_pts = 0.5
    else:
        funding_pts = 0.0
    score += funding_pts
    breakdown["funding"] = funding_pts

    # ── No token yet (1 pt) ────────────────────────────────────────────────
    if not has_token:
        score += 1.0
        breakdown["no_token"] = 1.0
    else:
        breakdown["no_token"] = 0.0

    # ── Active testnet (1 pt) ─────────────────────────────────────────────
    if testnet:
        score += 1.0
        breakdown["testnet"] = 1.0
    else:
        breakdown["testnet"] = 0.0

    # ── Caller quality bonus (0-1 pt) ─────────────────────────────────────
    if caller_tier == 1:
        caller_pts = 1.0
    elif caller_tier == 2:
        caller_pts = 0.5
    else:
        caller_pts = 0.0
    score += caller_pts
    breakdown["caller_quality"] = caller_pts

    # ── Multiple callers agree (1 pt) ─────────────────────────────────────
    if caller_count >= 2:
        score += 1.0
        breakdown["multi_caller"] = 1.0
    else:
        breakdown["multi_caller"] = 0.0

    score = min(round(score, 1), 10.0)

    # ── Label ──────────────────────────────────────────────────────────────
    if score >= 8:
        label = "🔥 STRONG CONVICTION"
        verdict = "High priority — go deep immediately."
    elif score >= 7:
        label = "⚡ GENESIS CALL"
        verdict = "Strong signal — start grinding now."
    elif score >= 5:
        label = "👀 WATCHING"
        verdict = "Promising — monitor closely, research more."
    elif score >= 3:
        label = "🌱 EARLY SIGNAL"
        verdict = "Too early — add to watchlist."
    else:
        label = "❄️ WEAK SIGNAL"
        verdict = "Not enough conviction yet."

    logger.info("Scored '%s': %.1f/10 [%s]",
                project.get("name", "?"), score, label)

    return ScoreResult(score=score, label=label,
                       breakdown=breakdown, verdict=verdict)
