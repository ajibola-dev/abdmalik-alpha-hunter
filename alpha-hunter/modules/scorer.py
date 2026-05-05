"""
modules/scorer.py — v2.0

Rebuilt from scratch. No more arbitrary weighted scoring.

The question is simple:
  "Does this project match the pattern of projects that gave
   real, substantial airdrops to early farmers?"

Historical pattern (Starknet, Hyperliquid, Jito, Celestia, Arbitrum,
Optimism, Wormhole, EigenLayer, dYdX, Blur):

  MUST HAVE (disqualify if missing):
    - No live token yet
    - Active testnet OR points program running

  STRONG SIGNALS (the more the better):
    - Under 50K Discord/community (early window)
    - Backed by serious funds with airdrop track record
    - Technical interaction required (not just clicking)
    - ZK/L2, PerpDEX, Solana DeFi, or Cosmos/DA vertical
    - Pre-Galxe (no active Galxe campaign yet)
    - GitHub activity in last 90 days
    - 3-18 month old project (not brand new, not too old)

  KILL SIGNALS (disqualify immediately):
    - Token already live
    - Mainnet launched without farming program
    - Galxe campaign already has 100K+ participants
    - Project is over 24 months old with no token news
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Funds with proven airdrop track record
_AIRDROP_TRACK_RECORD_FUNDS = [
    # Tier S — consistently produced massive airdrops
    "paradigm", "a16z", "andreessen horowitz", "multicoin",
    "polychain", "electric capital", "dragonfly",
    # Tier A — produced good airdrops
    "binance labs", "coinbase ventures", "sequoia", "pantera",
    "framework ventures", "delphi digital", "spartan",
    "1kx", "hack vc", "multicoin capital",
    # Notable for specific verticals
    "celestia", "cosmos", "osmosis",  # Cosmos ecosystem
    "solana ventures", "jump crypto",  # Solana ecosystem
]

# Verticals that have historically produced the biggest airdrops
_HIGH_VALUE_VERTICALS = {
    "zk_l2": 3,        # Starknet $120k, zkSync, Linea, Scroll, Taiko
    "perpdex": 3,       # Hyperliquid lifechanging, dYdX, GMX
    "solana_defi": 2,   # Jito, Tensor, Meteora, Jupiter
    "cosmos_da": 2,     # Celestia, Dymension, Osmosis, Neutron
    "interop": 2,       # Wormhole, LayerZero, Axelar
    "restaking": 2,     # EigenLayer, Symbiotic
    "infrastructure": 1,
}


@dataclass
class FarmingBrief:
    """What the bot returns — not a score, a farming brief."""
    project_name: str
    vertical: str
    conviction: str        # GRIND NOW / WATCH / SKIP
    why: str               # 1-2 sentences: why this fits the pattern
    window: str            # How long before the window closes
    actions: list[str]     # Exactly what to do today
    wallet_count: int      # How many wallets to run
    zero_cost: bool        # Can farm with zero capital?
    red_flags: list[str]   # Anything concerning
    raw_signals: dict = field(default_factory=dict)


def evaluate_project(project: dict, caller_tier: int = 2,
                     caller_weight: float = 1.0) -> FarmingBrief:
    """
    v2.0: Pattern match against historical airdrop criteria.
    Returns a farming brief, not a score.
    """
    name = project.get("name", "Unknown")
    investors = (project.get("investors", "") or "").lower()
    funding = project.get("funding_usd", 0) or 0
    has_token = project.get("has_token", False)
    testnet = project.get("testnet_active", False)
    novel_tech = [t.lower() for t in (project.get("novel_tech", []) or [])]
    category = (project.get("category", "") or "").lower()
    discord_size = project.get("discord_size", None)
    has_galxe = project.get("has_galxe", False)
    github_days = project.get("github_days_since_commit", 999)
    project_age_days = project.get("project_age_days", 999)
    tweet_text = (project.get("tweet_text", "") or "").lower()
    mentioned_by = project.get("mentioned_by", "")

    signals = {}
    red_flags = []

    # ── KILL SIGNALS ─────────────────────────────────────────────────────
    if has_token:
        return FarmingBrief(
            project_name=name,
            vertical="unknown",
            conviction="SKIP",
            why="Token already live. Farming window is closed.",
            window="Closed",
            actions=[],
            wallet_count=0,
            zero_cost=True,
            red_flags=["Token live"],
            raw_signals={"kill": "token_live"}
        )

    # ── DETECT VERTICAL ──────────────────────────────────────────────────
    vertical = _detect_vertical(novel_tech, category, tweet_text, investors)
    vertical_score = _HIGH_VALUE_VERTICALS.get(vertical, 0)
    signals["vertical"] = vertical
    signals["vertical_score"] = vertical_score

    # ── BACKING QUALITY ──────────────────────────────────────────────────
    backing_score = 0
    matched_funds = []
    for fund in _AIRDROP_TRACK_RECORD_FUNDS:
        if fund in investors:
            backing_score += 1
            matched_funds.append(fund)
    backing_score = min(backing_score, 3)
    signals["backing_score"] = backing_score
    signals["matched_funds"] = matched_funds

    # ── EARLY WINDOW SIGNALS ─────────────────────────────────────────────
    early_score = 0

    # Under 50K Discord = early window still open
    if discord_size is not None:
        if discord_size < 10_000:
            early_score += 3
            signals["discord"] = f"{discord_size} (VERY early)"
        elif discord_size < 50_000:
            early_score += 2
            signals["discord"] = f"{discord_size} (early)"
        elif discord_size < 200_000:
            early_score += 1
            signals["discord"] = f"{discord_size} (growing)"
        else:
            red_flags.append(f"Large community ({discord_size}) — window may be closing")
    else:
        # No Discord yet = extremely early
        early_score += 3
        signals["discord"] = "No Discord yet — extremely early"

    # No Galxe campaign = pre-hype
    if not has_galxe:
        early_score += 2
        signals["galxe"] = "No Galxe campaign yet"
    else:
        early_score += 0
        signals["galxe"] = "Galxe campaign exists"
        if discord_size and discord_size > 100_000:
            red_flags.append("Galxe + large community = retail already here")

    # GitHub active
    if github_days < 30:
        early_score += 2
        signals["github"] = f"Active ({github_days} days since commit)"
    elif github_days < 90:
        early_score += 1
        signals["github"] = f"Recent ({github_days} days since commit)"
    else:
        red_flags.append(f"GitHub inactive ({github_days} days) — team may have stalled")

    # Project age — sweet spot is 3-18 months
    if 90 <= project_age_days <= 540:
        early_score += 1
        signals["age"] = f"{project_age_days // 30} months old (sweet spot)"
    elif project_age_days > 720:
        red_flags.append(f"Project is {project_age_days // 30} months old — why no token yet?")

    signals["early_score"] = early_score

    # ── TESTNET / FARMING PROGRAM ────────────────────────────────────────
    farming_score = 0
    if testnet:
        farming_score += 3
        signals["testnet"] = "Active testnet"
    else:
        red_flags.append("No testnet detected — nothing to farm yet")

    # Technical interaction required = dev edge
    tech_required = _requires_technical_interaction(novel_tech, tweet_text)
    if tech_required:
        farming_score += 2
        signals["tech_required"] = "Technical interaction required — dev edge applies"
    signals["farming_score"] = farming_score

    # ── CALLER QUALITY ───────────────────────────────────────────────────
    caller_score = 0
    if caller_tier == 1:
        caller_score = round(3 * caller_weight, 1)
    elif caller_tier == 2:
        caller_score = round(1.5 * caller_weight, 1)
    signals["caller_score"] = caller_score

    # ── CONVICTION DECISION ──────────────────────────────────────────────
    total = (
        vertical_score * 2 +
        backing_score * 2 +
        early_score +
        farming_score +
        caller_score
    )
    signals["total"] = round(total, 1)

    critical_missing = []
    if not testnet:
        critical_missing.append("no testnet")
    if backing_score == 0 and funding < 5_000_000:
        critical_missing.append("no backing/funding")

    if total >= 16 and not critical_missing:
        conviction = "GRIND NOW"
    elif total >= 10 and len(critical_missing) <= 1:
        conviction = "WATCH CLOSELY"
    elif total >= 6:
        conviction = "MONITOR"
    else:
        conviction = "SKIP"

    # Override: if called by tier-1 account with no red flags, bump up
    if caller_tier == 1 and conviction == "MONITOR" and len(red_flags) == 0:
        conviction = "WATCH CLOSELY"

    # ── BUILD FARMING BRIEF ──────────────────────────────────────────────
    why = _build_why(name, vertical, backing_score, matched_funds,
                      early_score, farming_score, tech_required, signals)
    window = _estimate_window(discord_size, has_galxe, project_age_days)
    actions = _build_actions(vertical, testnet, tech_required, funding)
    wallet_count = _recommend_wallets(total, backing_score, vertical_score)
    zero_cost = funding == 0 or testnet  # testnet = zero cost to interact

    logger.info(
        "FarmingBrief '%s': conviction=%s vertical=%s total=%.1f "
        "backing=%d early=%d farming=%d caller=%.1f",
        name, conviction, vertical, total,
        backing_score, early_score, farming_score, caller_score
    )

    return FarmingBrief(
        project_name=name,
        vertical=vertical,
        conviction=conviction,
        why=why,
        window=window,
        actions=actions,
        wallet_count=wallet_count,
        zero_cost=zero_cost,
        red_flags=red_flags,
        raw_signals=signals
    )


def _detect_vertical(novel_tech: list, category: str,
                     tweet_text: str, investors: str) -> str:
    combined = " ".join(novel_tech) + " " + category + " " + tweet_text

    if any(x in combined for x in ["fhe", "zero knowledge", "zk", "zkvm",
                                    "l2", "layer 2", "rollup", "starknet",
                                    "zkync", "scroll", "linea", "taiko"]):
        return "zk_l2"

    if any(x in combined for x in ["perp", "perpetual", "dex", "trading",
                                    "hyperliquid", "gmx", "drift", "vertex"]):
        return "perpdex"

    if any(x in combined for x in ["solana", "svm", "sol ", "jito",
                                    "jupiter", "meteora", "raydium"]):
        return "solana_defi"

    if any(x in combined for x in ["cosmos", "celestia", "da layer",
                                    "data availability", "ibc", "atom",
                                    "dymension", "neutron", "osmosis"]):
        return "cosmos_da"

    if any(x in combined for x in ["bridge", "interop", "wormhole",
                                    "layerzero", "axelar", "cross-chain"]):
        return "interop"

    if any(x in combined for x in ["restaking", "eigenlayer", "avs",
                                    "shared security", "symbiotic"]):
        return "restaking"

    return "infrastructure"


def _requires_technical_interaction(novel_tech: list, tweet_text: str) -> bool:
    technical_signals = [
        "deploy", "contract", "node", "validator", "prover",
        "zk proof", "fhe", "zkvm", "run a node", "operator",
        "lp", "liquidity", "stake", "bridge", "swap",
    ]
    combined = " ".join(novel_tech) + " " + tweet_text
    return any(s in combined for s in technical_signals)


def _build_why(name, vertical, backing_score, matched_funds,
               early_score, farming_score, tech_required, signals) -> str:
    parts = []

    vertical_names = {
        "zk_l2": "ZK/L2 (Starknet-pattern)",
        "perpdex": "PerpDEX (Hyperliquid-pattern)",
        "solana_defi": "Solana DeFi (Jito-pattern)",
        "cosmos_da": "Cosmos/DA (Celestia-pattern)",
        "interop": "Interop (Wormhole-pattern)",
        "restaking": "Restaking (EigenLayer-pattern)",
        "infrastructure": "Infrastructure",
    }
    parts.append(f"{vertical_names.get(vertical, vertical)} vertical")

    if backing_score > 0 and matched_funds:
        parts.append(f"backed by {matched_funds[0]}")

    discord = signals.get("discord", "")
    if "No Discord" in discord:
        parts.append("no Discord yet — extremely early window")
    elif "VERY early" in discord:
        parts.append("tiny community — in the early window")
    elif "early" in discord:
        parts.append("community still small — window open")

    if signals.get("galxe") == "No Galxe campaign yet":
        parts.append("pre-Galxe")

    if tech_required:
        parts.append("technical interaction = dev edge")

    return ". ".join(parts).capitalize() + "."


def _estimate_window(discord_size, has_galxe, project_age_days) -> str:
    if discord_size is None and not has_galxe:
        return "Wide open — extremely early. Move now."
    if discord_size and discord_size < 10_000 and not has_galxe:
        return "Still early. 2-4 months before mainstream."
    if discord_size and discord_size < 50_000 and not has_galxe:
        return "Early-mid. 1-3 months of edge remaining."
    if has_galxe:
        return "Narrowing — Galxe exists. Still farmable if TVL/volume based."
    return "Unknown — needs more data."


def _build_actions(vertical: str, testnet: bool,
                   tech_required: bool, funding: float) -> list[str]:
    """Return the SMART farmer action list for this vertical."""
    base = []

    if not testnet:
        return ["Wait — no testnet yet. Set alert for testnet launch. Do not touch yet."]

    if vertical == "zk_l2":
        base = [
            "Deploy a simple contract or interact with deployed dApps on testnet",
            "Bridge assets through the official bridge (even testnet tokens)",
            "Use every deployed dApp at least once — swaps, lending, minting",
            "Get Discord OG role the moment Discord opens",
            "Run transactions across multiple days/weeks — consistency matters",
            "If node program opens: run a node. This is the dev edge.",
        ]
    elif vertical == "perpdex":
        base = [
            "Start trading with real volume — even small amounts count",
            "Provide liquidity in vaults if available (delta-neutral if possible)",
            "Trade consistently over weeks — not one big session",
            "Hold any ecosystem tokens if points are tied to holding",
            "Use referral system if available",
        ]
    elif vertical == "solana_defi":
        base = [
            "Provide LP in primary pool (even small amount earns points)",
            "Stake any available tokens — jitoSOL pattern",
            "Use the protocol daily with real transactions",
            "Join validator set if applicable",
        ]
    elif vertical == "cosmos_da":
        base = [
            "Stake native token with multiple validators (spread the stake)",
            "Participate in governance — vote on every proposal",
            "Run a light node or full node if technically accessible",
            "Stay staked — don't unstake until after snapshot",
        ]
    else:
        base = [
            "Interact with the testnet daily — vary amounts and timing",
            "Use every deployed contract/dApp",
            "Get early community role (Discord/TG)",
            "Bridge assets to/from testnet if bridge exists",
        ]

    return base


def _recommend_wallets(total: float, backing_score: int,
                       vertical_score: int) -> int:
    if total >= 18 and backing_score >= 2:
        return 5
    elif total >= 14:
        return 3
    elif total >= 10:
        return 2
    return 1
