"""
modules/action_planner.py
Generates actionable task checklists for each project.
Predicts eligibility criteria based on patterns from past successful projects.

Past project analysis:
- Zama:      FHE compute, Discord OG, testnet transactions, governance votes
- Story:     IP registration, testnet minting, validator nodes, ecosystem dApps
- Boundless: ZK compute tasks, node operation, Discord quests
- Monad:     Testnet txns (but influencers won — pure tx volume wasn't enough)
- Kaito:     Content creation, Yaps score, quality engagement on X
"""
import logging

logger = logging.getLogger(__name__)

# ── Task templates by project type ─────────────────────────────────────────

_UNIVERSAL_TASKS = [
    "Join official Discord and get OG/early role",
    "Follow official X account and turn on notifications",
    "Join official Telegram group",
    "Register email on waitlist/early access form",
    "Add project to your bookmarks for daily check",
]

_TESTNET_TASKS = [
    "Add testnet to MetaMask (get RPC from docs)",
    "Request testnet tokens from faucet",
    "Send 10+ testnet transactions (vary amounts/times)",
    "Interact with every deployed contract/dApp on testnet",
    "Bridge assets if bridge is available",
    "Provide liquidity if DEX is available",
    "Vote on any governance proposals",
    "Run a node/validator if technically accessible",
]

_COMMUNITY_TASKS = [
    "Post quality content about the project on X (not spam)",
    "Answer questions in Discord helpdesk channels",
    "Submit bug reports if you find issues on testnet",
    "Participate in any community calls/AMAs",
    "Complete any official quest campaigns (Galxe, Layer3, Zealy)",
]

_MULTI_WALLET_TASKS = [
    "Repeat testnet interactions on Wallet 2 (different timing)",
    "Repeat testnet interactions on Wallet 3 (different timing)",
    "Use different IP for each wallet if possible",
    "Space wallet interactions by 2-3 days minimum",
    "Vary transaction amounts — not identical across wallets",
]

# ── Eligibility predictions based on past patterns ─────────────────────────

_ELIGIBILITY_PATTERNS = {
    "fhe": {
        "prediction": "Likely rewards: compute task completion, early testnet users, Discord OG role, governance participation. Based on Zama's distribution.",
        "priority_tasks": ["FHE compute tasks on testnet", "Discord OG role", "Governance votes"],
    },
    "zero knowledge": {
        "prediction": "Likely rewards: ZK proof generation, node operation, early testnet transactions, technical contributions. Based on Boundless patterns.",
        "priority_tasks": ["Generate ZK proofs on testnet", "Run a prover node if available", "Early testnet activity"],
    },
    "depin": {
        "prediction": "Likely rewards: device/hardware contribution, uptime metrics, geographic diversity. Based on Helium, DIMO patterns.",
        "priority_tasks": ["Connect hardware/device to network", "Maintain high uptime", "Refer other node operators"],
    },
    "ai blockchain": {
        "prediction": "Likely rewards: model deployment, inference tasks, data contribution, staking. Based on Bittensor, Gensyn patterns.",
        "priority_tasks": ["Deploy or run a model", "Complete inference tasks", "Stake early if available"],
    },
    "layer-1": {
        "prediction": "Likely rewards: validator/node operation, early transactions, ecosystem dApp usage, delegation. Based on Story Protocol patterns.",
        "priority_tasks": ["Run a validator node", "Use every dApp deployed on the chain", "Delegate stake"],
    },
    "default": {
        "prediction": "Likely rewards: early testnet activity, community contribution, unique wallet interactions, on-chain diversity. Based on general patterns.",
        "priority_tasks": ["Early testnet transactions", "Community role in Discord", "Quest completion"],
    }
}


def generate_action_plan(project: dict, score_result=None) -> dict:
    """
    Generate a full action plan for a project including:
    - Immediate tasks (do today)
    - Testnet grind tasks
    - Community tasks
    - Multi-wallet strategy
    - Eligibility prediction
    """
    name = project.get("name", "Unknown")
    novel_tech = project.get("novel_tech", [])
    testnet = project.get("testnet_active", False)
    website = project.get("website", "")
    funding = project.get("funding_usd", 0) or 0

    # ── Pick eligibility pattern ───────────────────────────────────────────
    eligibility = _ELIGIBILITY_PATTERNS["default"]
    for tech in novel_tech:
        tech_lower = tech.lower()
        for key in _ELIGIBILITY_PATTERNS:
            if key in tech_lower:
                eligibility = _ELIGIBILITY_PATTERNS[key]
                break

    # ── Build task list ────────────────────────────────────────────────────
    immediate = list(_UNIVERSAL_TASKS)
    if website:
        immediate.insert(0, f"Visit {website} and read the full docs")

    grind = []
    if testnet:
        grind = list(_TESTNET_TASKS)
        grind = [eligibility["priority_tasks"][0]] + grind if eligibility["priority_tasks"] else grind

    community = list(_COMMUNITY_TASKS)
    multi_wallet = list(_MULTI_WALLET_TASKS)

    # ── Wallet recommendation ──────────────────────────────────────────────
    if funding >= 50_000_000:
        wallet_count = 3
        wallet_note = "Project has strong funding — worth using 3 wallets"
    elif funding >= 20_000_000:
        wallet_count = 2
        wallet_note = "Solid project — 2 wallets recommended"
    else:
        wallet_count = 1
        wallet_note = "Early stage — start with 1 wallet, scale if testnet launches"

    return {
        "project_name": name,
        "immediate_tasks": immediate,
        "testnet_tasks": grind if testnet else ["⏳ No testnet yet — check docs daily"],
        "community_tasks": community,
        "multi_wallet_tasks": multi_wallet[:wallet_count * 2],
        "wallet_recommendation": {"count": wallet_count, "note": wallet_note},
        "eligibility_prediction": eligibility["prediction"],
        "priority_tasks": eligibility["priority_tasks"],
    }


def format_action_plan_for_telegram(project: dict, score_result,
                                     action_plan: dict) -> str:
    """Format the full action plan as a Telegram HTML message."""
    name = project.get("name", "Unknown")
    funding = project.get("funding_usd", 0) or 0
    investors = project.get("investors", "Not found")
    novel_tech = project.get("novel_tech", [])
    website = project.get("website", "")
    mentioned_by = project.get("mentioned_by", "")
    tweet_url = project.get("tweet_url", "")

    funding_str = f"${funding/1e6:.0f}M" if funding >= 1e6 else "Unknown"

    msg = f"""🎯 <b>ALPHA HUNTER — GENESIS CALL</b>

<b>Project:</b> {name}
<b>Score:</b> {score_result.score}/10 — {score_result.label}
<b>Called by:</b> @{mentioned_by}
<b>Funding:</b> {funding_str}
<b>Investors:</b> {investors[:100] if investors else 'Unknown'}
<b>Tech:</b> {', '.join(novel_tech) if novel_tech else 'General blockchain'}
<b>Website:</b> {website or 'Check tweet'}

━━━━━━━━━━━━━━━━━━━━
🏃 <b>DO THESE TODAY:</b>
"""
    for i, task in enumerate(action_plan["immediate_tasks"][:4], 1):
        msg += f"{i}. {task}\n"

    if action_plan["testnet_tasks"] and "No testnet" not in action_plan["testnet_tasks"][0]:
        msg += "\n🧪 <b>TESTNET GRIND:</b>\n"
        for i, task in enumerate(action_plan["testnet_tasks"][:4], 1):
            msg += f"{i}. {task}\n"

    msg += f"\n💼 <b>WALLETS:</b> {action_plan['wallet_recommendation']['note']}\n"

    msg += f"\n🔮 <b>ELIGIBILITY PREDICTION:</b>\n{action_plan['eligibility_prediction'][:200]}\n"

    if tweet_url:
        msg += f"\n<a href='{tweet_url}'>📎 Source tweet</a>"

    msg += f"\n\n<i>Verdict: {score_result.verdict}</i>"

    return msg
