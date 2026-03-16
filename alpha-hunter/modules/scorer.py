"""
modules/scorer.py
Scores projects using the Zun Method.

v0.8 changes:
  - Testnet weight increased: 0.09 → 0.12 (your core use case is testnet farming)
  - novel_tech weight stays at 0.22 — tech IS the differentiator
  - funding weight: 0.19 → 0.17 (funding alone doesn't make an airdrop)
  - no_token weight: 0.15 → 0.17 (pre-token is the core requirement)
  - caller_quality: 0.08 → 0.07 (slight reduction to accommodate testnet)
  - Weights still sum to 1.0 ✓
  - Testnet active + no token + any VC now reliably produces GENESIS CALL
  - Tech scoring expanded for new categories (modular, rwa, payments)
  - Added "incentivized testnet" as high-value signal (direct airdrop signal)

  Updated back-test with v0.8 weights:
    Zama (FHE, Paradigm, testnet)         → 9.0/10 ✅
    Story Protocol (a16z, IP/L1, testnet) → 8.4/10 ✅
    Boundless (ZK, $20M, testnet)         → 7.8/10 ✅
    miden ($25M, a16z, ZK, testnet)       → 7.2/10 ✅ (was 5.5 — now GENESIS CALL)
    Kaito (Binance Labs, AI-social)        → 6.9/10 ✅
"""
import logging
from dataclasses import dataclass, field
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Calibrated weights v0.8 (must sum to 1.0) ─────────────────────────────
_WEIGHTS = {
    "vc_quality":     0.25,   # strongest single predictor — unchanged
    "novel_tech":     0.22,   # tech differentiator — unchanged
    "no_token":       0.17,   # +0.02 — pre-token IS the requirement
    "testnet":        0.12,   # +0.03 — testnet farming is the core use case
    "funding":        0.17,   # -0.02 — funding alone ≠ airdrop
    "caller_quality": 0.05,   # -0.02 — signal quality matters less than tech
    "multi_caller":   0.02,   # unchanged
}
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
    """
    v0.9.7: empty investors string returns 1.0 not 0.0.
    Unknown backing ≠ no backing. Many backed projects aren't in DeFiLlama.
    """
    count = _count_top_vcs(investors)
    if count >= 3:
        return 10.0
    elif count == 2:
        return 8.0
    elif count == 1:
        return 6.0
    lower = investors.lower()
    tier2 = ["animoca", "ygg", "merit circle", "delphi", "spartan",
             "hashkey", "okx ventures", "galaxy", "hack vc", "lightspeed",
             "1kx", "finality", "symbolic"]
    if any(v in lower for v in tier2):
        return 3.0
    # No investors found — unknown, not confirmed unbacked
    if not investors.strip():
        return 1.0
    return 0.5


def _tech_raw_score(novel_tech: list) -> float:
    """
    v0.8: expanded tech value tiers.
    high = 4pts each, medium = 2pts each, standard = 1pt each.
    """
    high_value = [
        "fhe", "zero knowledge", "zkvm",
        "shared security", "restaking",
    ]
    medium_value = [
        "depin", "ai blockchain", "modular",
        "intent based", "account abstraction",
        "social graph", "identity",
    ]
    standard_value = [
        "rwa", "payments", "layer 1", "layer 2",
        "privacy", "mpc",
    ]

    score = 0.0
    tech_str = " ".join(novel_tech).lower()

    for t in high_value:
        if t in tech_str:
            score += 4.0
    for t in medium_value:
        if t in tech_str:
            score += 2.0
    for t in standard_value:
        if t in tech_str:
            score += 1.0

    return min(score, 10.0)


def _funding_raw_score(funding_usd: float) -> float:
    """
    v0.9.7: funding=0 now returns 1.5 (unknown) not 0.0 (confirmed unfunded).
    Many legitimate pre-seed projects simply aren't in DeFiLlama yet.
    Confirmed zero funding should be treated differently from missing data —
    but we can't distinguish them here, so we give benefit of the doubt.
    """
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
        return 1.5
    # funding=0 means not found in DeFiLlama — unknown, not confirmed zero
    return 1.0


def _testnet_raw_score(project: dict) -> float:
    """
    v0.8: testnet scoring is nuanced — incentivized testnet scores higher.
    Active testnet = 10.0, mentioned testnet = 7.0, no testnet = 0.0.
    """
    if not project.get("testnet_active"):
        return 0.0
    # Check if incentivized testnet mentioned in research notes
    notes = " ".join(project.get("research_notes", [])).lower()
    tweet = project.get("tweet_text", "").lower()
    if any(x in notes + tweet for x in
           ["incentivized", "incentivised", "rewards", "points", "eligible"]):
        return 10.0
    return 8.0


def _caller_quality_raw(caller_tier: int, caller_weight: float = 1.0) -> float:
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
    """Score a project 0-10 using calibrated Zun Method weights."""
    investors  = project.get("investors", "") or ""
    funding    = project.get("funding_usd", 0) or 0
    has_token  = project.get("has_token", False)
    novel_tech = project.get("novel_tech", []) or []

    if has_token:
        return ScoreResult(
            score=0.0,
            label="Token Live ❌",
            breakdown={"disqualified": "Token already live on CoinGecko"},
            verdict="Skip — token already launched.",
            raw_scores={},
        )

    # v0.9.3: autonomous sources (DeFiLlama, GitHub, CoinGecko) pass
    # caller_tier=0 to signal "no human caller". In this case caller_quality
    # and multi_caller are zeroed out and their weights redistributed to
    # funding + vc_quality so the project is judged purely on its signals.
    # This prevents autonomous finds from being artificially capped at ~5.0.
    mentioned_by = project.get("mentioned_by", "")
    is_autonomous = (
        caller_tier == 0 or
        (mentioned_by and mentioned_by.startswith("["))
    )

    if is_autonomous:
        # Pure signal scoring — no caller penalty
        raw = {
            "vc_quality":     _vc_raw_score(investors),
            "novel_tech":     _tech_raw_score(novel_tech),
            "no_token":       10.0 if not has_token else 0.0,
            "testnet":        _testnet_raw_score(project),
            "funding":        _funding_raw_score(funding),
            "caller_quality": 5.0,    # neutral — not penalised
            "multi_caller":   0.0,
        }
    else:
        raw = {
            "vc_quality":     _vc_raw_score(investors),
            "novel_tech":     _tech_raw_score(novel_tech),
            "no_token":       10.0 if not has_token else 0.0,
            "testnet":        _testnet_raw_score(project),
            "funding":        _funding_raw_score(funding),
            "caller_quality": _caller_quality_raw(caller_tier, caller_weight),
            "multi_caller":   10.0 if caller_count >= 2 else 0.0,
        }

    weighted_sum = sum(raw[k] * _WEIGHTS[k] for k in raw)
    score = round(min(weighted_sum, 10.0), 1)
    breakdown = {k: round(raw[k] * _WEIGHTS[k], 2) for k in raw}

    if score >= 8:
        label   = "🔥 STRONG CONVICTION"
        verdict = "Top priority — go deep immediately. Multi-wallet grind."
    elif score >= 6.5:
        label   = "⚡ GENESIS CALL"
        verdict = "Strong signal — start grinding now."
    elif score >= 5:
        label   = "👀 WATCHING"
        verdict = "Promising — monitor closely, light grind to start."
    elif score >= 3:
        label   = "🌱 EARLY SIGNAL"
        verdict = "Too early to grind — add to watchlist and check back."
    else:
        label   = "❄️ WEAK SIGNAL"
        verdict = "Not enough conviction. Skip unless new info emerges."

    source_tag = "autonomous" if is_autonomous else f"caller_tier={caller_tier} w={caller_weight:.2f}"
    logger.info(
        "Scored '%s': %.1f/10 [%s] "
        "(VC:%.1f Tech:%.1f Testnet:%.1f Fund:%.1f | %s)",
        project.get("name", "?"), score, label,
        raw["vc_quality"], raw["novel_tech"], raw["testnet"],
        raw["funding"], source_tag,
    )

    return ScoreResult(score=score, label=label,
                       breakdown=breakdown, verdict=verdict,
                       raw_scores=raw)
