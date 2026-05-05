"""
modules/action_planner.py — v2.0

Generates farming briefs. Not scores. Not generic task lists.

The output answers: "What exactly should I do TODAY to get
max allocation when this project launches?"

Based on the SMART farmer playbook from mztacat/CC2Ventures:
- Organic power-user activity
- Technical depth over breadth
- Look like a paid ambassador
- Zero-cost first, skin-in-game when it counts
"""
import logging

logger = logging.getLogger(__name__)


def format_farming_brief_for_telegram(brief, project: dict) -> str:
    """
    Format a FarmingBrief as Telegram HTML.
    This replaces the old genesis call format.
    """
    from modules.scorer import FarmingBrief
    name = brief.project_name
    vertical = brief.vertical
    conviction = brief.conviction
    wallet_count = brief.wallet_count

    # Conviction header
    if conviction == "GRIND NOW":
        header = "🔥 ALPHA HUNTER — GRIND NOW"
        verdict_color = "🔥"
    elif conviction == "WATCH CLOSELY":
        header = "👀 ALPHA HUNTER — WATCH CLOSELY"
        verdict_color = "👀"
    elif conviction == "MONITOR":
        header = "📡 ALPHA HUNTER — MONITOR"
        verdict_color = "📡"
    else:
        return ""  # Don't send SKIP alerts

    vertical_labels = {
        "zk_l2": "ZK/L2 (Starknet-pattern)",
        "perpdex": "PerpDEX (Hyperliquid-pattern)",
        "solana_defi": "Solana DeFi (Jito-pattern)",
        "cosmos_da": "Cosmos/DA (Celestia-pattern)",
        "interop": "Interop (Wormhole-pattern)",
        "restaking": "Restaking (EigenLayer-pattern)",
        "infrastructure": "Infrastructure",
    }
    vertical_label = vertical_labels.get(vertical, vertical)

    mentioned_by = project.get("mentioned_by", "")
    tweet_url = project.get("tweet_url", "")
    funding = project.get("funding_usd", 0) or 0
    investors = project.get("investors", "") or ""

    # Build caller display
    if mentioned_by and not mentioned_by.startswith("["):
        caller_str = f'<a href="https://twitter.com/{mentioned_by}">@{mentioned_by}</a>'
    else:
        caller_str = mentioned_by or "autonomous"

    msg = f"<b>{header}</b>\n\n"
    msg += f"<b>Project:</b> {name}\n"
    msg += f"<b>Vertical:</b> {vertical_label}\n"
    msg += f"<b>Called by:</b> {caller_str}\n"

    if funding >= 1_000_000:
        msg += f"<b>Funding:</b> ${funding/1e6:.1f}M\n"
    if investors:
        msg += f"<b>Backed by:</b> {investors[:80]}\n"

    msg += f"\n<b>Why this fits:</b>\n{brief.why}\n"
    msg += f"\n<b>Window:</b> {brief.window}\n"

    # Red flags
    if brief.red_flags:
        msg += f"\n⚠️ <b>Watch out:</b>\n"
        for flag in brief.red_flags[:3]:
            msg += f"  • {flag}\n"

    # Actions
    if brief.actions:
        msg += f"\n🏃 <b>DO THIS NOW ({vertical_label}):</b>\n"
        for i, action in enumerate(brief.actions[:6], 1):
            msg += f"{i}. {action}\n"

    # Wallet recommendation
    wallet_note = {
        1: "1 wallet — zero cost. Scale up if it develops.",
        2: "2 wallets — vary timing and activity between them.",
        3: "3 wallets — stagger entries by 1-2 weeks each.",
        5: "5 wallets — serious conviction. Vary IP, timing, amounts.",
    }.get(wallet_count, f"{wallet_count} wallets")
    msg += f"\n💼 <b>Wallets:</b> {wallet_note}\n"

    # Zero cost indicator
    if brief.zero_cost:
        msg += "\n✅ <b>Zero cost</b> — testnet/points, no capital required to start.\n"

    if tweet_url and tweet_url != "manual":
        msg += f"\n<a href='{tweet_url}'>📎 Source</a>"

    return msg


def format_farming_brief_for_telegram_v2(brief, project: dict) -> str:
    """Alias for consistency."""
    return format_farming_brief_for_telegram(brief, project)
