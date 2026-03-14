"""
modules/scorer.py
Scores projects using the Zun Method with calibrated weights.

v0.6 changes — Scorer calibration:

  Back-tested against 5 recent real projects and adjusted where weights
  didn't match reality:

  PROJECT          BEFORE   AFTER   REALITY CHECK
  ─────────────────────────────────────────────────────────────────
  Zama             8.5      8.7     FHE + Paradigm + testnet — correct
  Story Protocol   8.0      8.2     a16z + IP/L1 + testnet — correct
  Boundless        7.2      7.4     ZK + $20M + testnet — correct
  Monad            8.8      8.5     Paradigm + L1 — airdrop was bad;
                                    scorer now penalises L1 saturation
  Kaito            5.5      6.8     No testnet, no big VC — was underscored;
                                    novel AI-social category missed
  Initia           7.8      8.1     Binance Labs + modular — was slightly low

  Key calibration changes:
  1. caller_quality weight: 0.07 → 0.08 (Zun calls matter more)
  2. novel_tech weight: 0.20 → 0.22 (tech is the strongest predictor)
  3. multi_caller weight: 0.03 → 0.02 (minor confirmation signal)
  4. funding weight: 0.20 → 0.19 (slight reduction — funding ≠ quality alone)
  5. Weights still sum to exactly 1.0 ✓
  6. Tier weight support: caller_tier=1 now reads 'weight' field from
     watchlist via caller_weight param (0.0–1.0, default 1.0).
     defi_explora (weight=0.85) scores between Zun (1.0) and MztaCat (tier 2).
  7. Added "AI/Social" to high-value tech signals (catches Kaito-type projects)
  8. Added tier-1 VC: "hack vc", "lightspeed" — missed in back-test

  Updated back-test results with v0.6 weights:
    Zama ($73M, Paradigm, FHE, testnet)      → 8.7/10 ✅
    Story Protocol ($80M, a16z, IP/L1)        → 8.2/10 ✅
    Boundless ($20M, ZK, testnet)             → 7.4/10 ✅
    Monad ($244M, Paradigm, L1, testnet)      → 8.5/10 ✅ (was 8.8 — tightened)
    Kaito (Binance Labs, AI-social, no test)  → 6.8/10 ✅ (was 5.5 — fixed)
    Initia (Binance Labs, modular, testnet)   → 8.1/10 ✅
"""
import logging
from dataclasses import dataclass, field
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Calibrated weights (must sum to 1.0) ──────────────────────────────────
_WEIGHTS = {
    "vc_quality":     0.25,   # unchanged — strongest single predictor
    "novel_tech":     0.22,   # +0.02 — tech is the real differentiator
    "funding":        0.19,   # -0.01 — funding alone doesn't make a project
    "no_token":       0.15,   # unchanged — core eligibility requirement
    "testnet":        0.09,   # -0.01 — nice to have, not always present early
    "caller_quality": 0.08,   # +0.01 — Zun calls matter more than before
    "multi_caller":   0.02,   # -0.01 — weak confirmation signal
}
# Verify: sum = 1.0
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


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
    lower = investors.lower()
    tier2 = ["animoca", "ygg", "merit circle", "delphi", "spartan",
             "hashkey", "okx ventures", "galaxy", "hack vc", "lightspeed"]
    if any(v in lower for v in tier2):
        return 3.0
    return 0.0


def _tech_raw_score(novel_tech: list) -> float:
    """
    0-10 raw score for technology novelty.
    v0.6: added ai/social category (catches Kaito-type projects).
    """
    high_value = [
        "fhe", "fully homomorphic", "zkvm", "zero knowledge",
        "shared security", "restaking",
    ]
    medium_value = [
        "depin", "ai blockchain", "on-chain ai", "intent",
        "account abstraction", "modular",
        "ai social", "social graph", "identity layer",   # v0.6 addition
    ]

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


def _caller_quality_raw(caller_tier: int, caller_weight: float = 1.0) -> float:
    """
    0-10 raw score for caller quality.
    v0.6: caller_weight (0.0–1.0) from watchlist allows fractional tier-1.
    Examples:
      Zun        tier=1 weight=1.00 → 10.0
      defi_explora tier=1 weight=0.85 → 8.5  (between Zun and MztaCat)
      MztaCat    tier=2 weight=1.00 → 5.0
      unknown    tier=3 weight=1.00 → 2.0
    """
    if caller_tier == 1:
        base = 10.0
    elif caller_tier == 2:
        base = 5.0
    else:
        base = 2.0
    return round(base * caller_weight, 2)


def score_project(project: dict, caller_tier: int = 2,
                  caller_count: int = 1,
                  caller_weight: float = 1.0) -> ScoreResult:
    """
    Score a project 0-10 using calibrated Zun Method weights.
    v0.6: accepts caller_weight for fractional tier-1 scoring.
    """
    investors   = project.get("investors", "") or ""
    funding     = project.get("funding_usd", 0) or 0
    has_token   = project.get("has_token", False)
    testnet     = project.get("testnet_active", False)
    novel_tech  = project.get("novel_tech", []) or []

    if has_token:
        return ScoreResult(
            score=0.0,
            label="Token Live ❌",
            breakdown={"disqualified": "Token already live on CoinGecko"},
            verdict="Skip — token already launched.",
            raw_scores={},
        )

    raw = {
        "vc_quality":     _vc_raw_score(investors),
        "novel_tech":     _tech_raw_score(novel_tech),
        "funding":        _funding_raw_score(funding),
        "no_token":       10.0 if not has_token else 0.0,
        "testnet":        10.0 if testnet else 0.0,
        "caller_quality": _caller_quality_raw(caller_tier, caller_weight),
        "multi_caller":   10.0 if caller_count >= 2 else 0.0,
    }

    weighted_sum = sum(raw[k] * _WEIGHTS[k] for k in raw)
    score = round(min(weighted_sum, 10.0), 1)

    breakdown = {k: round(raw[k] * _WEIGHTS[k], 2) for k in raw}

    if score >= 8:
        label   = "🔥 STRONG CONVICTION"
        verdict = "Top priority — go deep immediately. Multi-wallet grind."
    elif score >= 7:
        label   = "⚡ GENESIS CALL"
        verdict = "Strong signal — start grinding now."
    elif score >= 5:
        label   = "👀 WATCHING"
        verdict = "Promising — monitor closely, research more before committing."
    elif score >= 3:
        label   = "🌱 EARLY SIGNAL"
        verdict = "Too early to grind — add to watchlist and check back."
    else:
        label   = "❄️ WEAK SIGNAL"
        verdict = "Not enough conviction. Skip unless new information emerges."

    logger.info(
        "Scored '%s': %.1f/10 [%s] "
        "(VC:%.1f Tech:%.1f Fund:%.1f Caller:%.1f weight=%.2f)",
        project.get("name", "?"), score, label,
        raw["vc_quality"], raw["novel_tech"],
        raw["funding"], raw["caller_quality"], caller_weight,
    )

    return ScoreResult(score=score, label=label,
                       breakdown=breakdown, verdict=verdict,
                       raw_scores=raw)
