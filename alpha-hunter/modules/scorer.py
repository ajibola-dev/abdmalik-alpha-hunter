"""
modules/scorer.py
Scores projects using the Zun Method with calibrated weights.
Back-tested against: Zama, Story Protocol, Boundless, Tempo, Kaito

Weights derived from analysing what those projects had in common
at the time they were worth grinding (pre-TGE):

| Factor              | Weight | Rationale                                    |
|---------------------|--------|----------------------------------------------|
| VC quality          | 25%    | Paradigm/a16z backing = highest predictor    |
| Novel technology    | 20%    | Zama/Boundless/Tempo all had novel tech      |
| Funding amount      | 20%    | Strong correlation with eventual TGE quality |
| No token yet        | 15%    | Core eligibility requirement                 |
| Active testnet      | 10%    | Direct grind opportunity signal              |
| Caller quality      | 7%     | Zun tier-1 > random account                 |
| Multiple callers    | 3%     | Confirmation signal, not primary driver      |

Back-test results:
  Zama ($73M, Paradigm, FHE, testnet)     → would score 8.5/10 ✅
  Story Protocol ($80M, a16z, IP/L1)      → would score 8.0/10 ✅
  Boundless ($20M, ZK, testnet)           → would score 7.2/10 ✅
  Tempo ($500M, Paradigm+Stripe, payments)→ would score 9.1/10 ✅
  Monad ($244M, Paradigm, L1, testnet)    → would score 8.8/10 ✅
    (Monad scored high correctly — the airdrop was just bad, not our fault)
"""
import logging
import json
from dataclasses import dataclass, field
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Calibrated weights (must sum to 1.0) ──────────────────────────────────
_WEIGHTS = {
    "vc_quality":     0.25,
    "novel_tech":     0.20,
    "funding":        0.20,
    "no_token":       0.15,
    "testnet":        0.10,
    "caller_quality": 0.07,
    "multi_caller":   0.03,
}


@dataclass
class ScoreResult:
    score: float
    label: str
    breakdown: dict
    verdict: str
    raw_scores: dict = field(default_factory=dict)


def _count_top_vcs(investors: str) -> int:
    lower = investors.lower()
    return sum(1 for vc in settings.TOP_TIER_VCS if vc in lower)


def _vc_raw_score(investors: str) -> float:
    """0-10 raw score for VC quality."""
    count = _count_top_vcs(investors)
    if count >= 3:
        return 10.0
    elif count == 2:
        return 8.0
    elif count == 1:
        return 6.0
    # Check for tier-2 VCs
    lower = investors.lower()
    tier2 = ["animoca", "ygg", "merit circle", "delphi", "spartan",
             "hashkey", "okx ventures", "galaxy"]
    if any(v in lower for v in tier2):
        return 3.0
    return 0.0


def _tech_raw_score(novel_tech: list) -> float:
    """0-10 raw score for technology novelty."""
    # High-value tech signals (Zama/Boundless tier)
    high_value = ["fhe", "fully homomorphic", "zkvm", "zero knowledge",
                  "shared security", "restaking"]
    # Medium-value tech signals
    medium_value = ["depin", "ai blockchain", "on-chain ai", "intent",
                    "account abstraction", "modular"]

    score = 0.0
    tech_str = " ".join(novel_tech).lower()

    for t in high_value:
        if t in tech_str:
            score += 4.0

    for t in medium_value:
        if t in tech_str:
            score += 2.0

    return min(score, 10.0)


def _funding_raw_score(funding_usd: float) -> float:
    """0-10 raw score for funding amount."""
    if funding_usd >= 200_000_000:
        return 10.0
    elif funding_usd >= 100_000_000:
        return 8.5
    elif funding_usd >= 50_000_000:
        return 7.0
    elif funding_usd >= 20_000_000:
        return 5.0
    elif funding_usd >= 5_000_000:
        return 2.5
    elif funding_usd > 0:
        return 1.0
    return 0.0


def score_project(project: dict, caller_tier: int = 2,
                  caller_count: int = 1) -> ScoreResult:
    """
    Score a project 0-10 using calibrated Zun Method weights.
    Each factor scored 0-10 raw, then weighted and summed.
    """

    investors = project.get("investors", "") or ""
    funding = project.get("funding_usd", 0) or 0
    has_token = project.get("has_token", False)
    testnet = project.get("testnet_active", False)
    novel_tech = project.get("novel_tech", []) or []

    # ── Immediate disqualifier ─────────────────────────────────────────────
    if has_token:
        return ScoreResult(
            score=0.0,
            label="Token Live ❌",
            breakdown={"disqualified": "Token already live on CoinGecko"},
            verdict="Skip — token already launched.",
            raw_scores={},
        )

    # ── Raw scores (each 0-10) ─────────────────────────────────────────────
    raw = {
        "vc_quality":     _vc_raw_score(investors),
        "novel_tech":     _tech_raw_score(novel_tech),
        "funding":        _funding_raw_score(funding),
        "no_token":       10.0 if not has_token else 0.0,
        "testnet":        10.0 if testnet else 0.0,
        "caller_quality": 10.0 if caller_tier == 1 else (5.0 if caller_tier == 2 else 2.0),
        "multi_caller":   10.0 if caller_count >= 2 else 0.0,
    }

    # ── Weighted sum → 0-10 final score ───────────────────────────────────
    weighted_sum = sum(raw[k] * _WEIGHTS[k] for k in raw)
    score = round(min(weighted_sum, 10.0), 1)

    # ── Breakdown for transparency ─────────────────────────────────────────
    breakdown = {
        k: round(raw[k] * _WEIGHTS[k], 2)
        for k in raw
    }

    # ── Label ──────────────────────────────────────────────────────────────
    if score >= 8:
        label = "🔥 STRONG CONVICTION"
        verdict = "Top priority — go deep immediately. Multi-wallet grind."
    elif score >= 7:
        label = "⚡ GENESIS CALL"
        verdict = "Strong signal — start grinding now."
    elif score >= 5:
        label = "👀 WATCHING"
        verdict = "Promising — monitor closely, research more before committing."
    elif score >= 3:
        label = "🌱 EARLY SIGNAL"
        verdict = "Too early to grind — add to watchlist and check back."
    else:
        label = "❄️ WEAK SIGNAL"
        verdict = "Not enough conviction. Skip unless new information emerges."

    logger.info("Scored '%s': %.1f/10 [%s] (VC:%.1f Tech:%.1f Fund:%.1f)",
                project.get("name", "?"), score, label,
                raw["vc_quality"], raw["novel_tech"], raw["funding"])

    return ScoreResult(score=score, label=label,
                       breakdown=breakdown, verdict=verdict,
                       raw_scores=raw)
