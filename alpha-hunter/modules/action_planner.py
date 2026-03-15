"""
modules/action_planner.py
Generates actionable task checklists for each project.

v0.9 changes:
  - Task templates now tech-specific — ZK/FHE/DePIN/restaking/AI each
    get their own targeted task lists based on how those categories
    historically distributed airdrops
  - Multi-wallet task generation: wallet 1 gets standard tasks,
    wallet 2+ get varied timing/amount instructions to avoid sybil detection
  - Eligibility predictions are now category-aware and specific
  - format_action_plan_for_telegram updated to show tech-specific insight
  - Tasks auto-assigned per wallet in DB when genesis alert fires

Past project analysis (unchanged):
  Zama:    FHE compute, Discord OG, testnet transactions, governance votes
  Story:   IP registration, testnet minting, validator nodes, ecosystem dApps
  Boundless: ZK compute tasks, node operation, Discord quests
  miden:   ZK VM operations, testnet transactions, developer activity
  Kaito:   Content creation, Yaps score, quality engagement on X
"""
import logging

logger = logging.getLogger(__name__)

_UNIVERSAL_TASKS = [
    "Join official Discord and get OG/early role immediately",
    "Follow official X account and turn on notifications",
    "Join official Telegram group",
    "Register email on waitlist/early access form",
    "Add project to bookmarks — check docs daily for testnet launch",
]

# ── Tech-specific task templates ──────────────────────────────────────────

_TECH_TASKS = {
    "fhe": {
        "immediate": [
            "Read the FHE docs — understand what compute tasks will be required",
            "Join Discord and ask about FHE testnet compute program",
            "Sign up for any developer/compute program waitlist",
        ],
        "testnet": [
            "Complete all FHE compute tasks on testnet (these are the core metric)",
            "Run FHE computations with varying input sizes",
            "Submit at least one governance vote if available",
            "Generate proofs and submit them on-chain",
            "Participate in any bug bounty or feedback program",
        ],
        "eligibility": (
            "Based on Zama: rewards likely go to FHE compute task completers, "
            "Discord OG role holders, early testnet users with diverse task completion, "
            "governance participants. Volume of compute > volume of transactions."
        ),
        "priority_tasks": [
            "Complete ALL FHE compute tasks — this is the primary eligibility signal",
            "Get Discord OG role immediately",
            "Governance votes if available",
        ],
    },

    "zero knowledge": {
        "immediate": [
            "Read the ZK docs — identify if prover/verifier roles are open",
            "Join Discord and check for ZK developer program",
            "Sign up for prover node waitlist if available",
        ],
        "testnet": [
            "Generate ZK proofs on testnet",
            "Run a prover node if technically feasible",
            "Submit proofs with varying circuit complexity",
            "Use every dApp deployed on the ZK chain",
            "Bridge assets to/from testnet",
            "Interact with the sequencer if accessible",
        ],
        "eligibility": (
            "Based on Boundless/miden: rewards go to proof generators, node operators, "
            "early testnet users with on-chain diversity. Technical contribution "
            "outweighs pure transaction volume. Run a node if possible."
        ),
        "priority_tasks": [
            "Run a prover/verifier node — highest eligibility signal for ZK projects",
            "Generate ZK proofs on testnet",
            "Use every dApp on the chain",
        ],
    },

    "depin": {
        "immediate": [
            "Identify what hardware/device is required to contribute",
            "Join Discord and check for early device contributor program",
            "Register location/device on waitlist",
        ],
        "testnet": [
            "Connect hardware/device to the network ASAP",
            "Maintain maximum uptime — this is the primary metric",
            "Refer other node operators through official referral",
            "Stake if a staking mechanism is available",
            "Verify your device coverage area is unique",
        ],
        "eligibility": (
            "Based on Helium/DIMO/Hivemapper: rewards go to early device contributors "
            "with high uptime, geographic diversity, and referral activity. "
            "Location uniqueness matters — rural areas often rewarded more."
        ),
        "priority_tasks": [
            "Connect device immediately — first-mover advantage is massive in DePIN",
            "Maintain 99%+ uptime from day one",
            "Refer at least 3 other node operators",
        ],
    },

    "restaking": {
        "immediate": [
            "Check if AVS operator registration is open",
            "Read the restaking docs and identify supported LSTs",
            "Join Discord and ask about early operator program",
        ],
        "testnet": [
            "Register as an AVS operator on testnet",
            "Stake supported assets (ETH, LSTs) on testnet",
            "Opt into every AVS available on testnet",
            "Run operator software if technically feasible",
            "Participate in slashing/penalty test scenarios",
        ],
        "eligibility": (
            "Based on EigenLayer ecosystem: rewards go to early operators, "
            "delegators, and AVS participants. Operator registration before "
            "mainnet is the strongest signal. Restake multiple assets."
        ),
        "priority_tasks": [
            "Register as AVS operator — this is the primary eligibility action",
            "Opt into every available AVS",
            "Stake and delegate to multiple operators",
        ],
    },

    "ai blockchain": {
        "immediate": [
            "Identify if model deployment, inference tasks, or data contribution is open",
            "Join Discord and check for AI compute contributor program",
            "Register as a compute provider if program is open",
        ],
        "testnet": [
            "Deploy or run an AI model on testnet",
            "Complete all available inference tasks",
            "Stake compute credits if mechanism exists",
            "Submit training data if data contribution program is open",
            "Participate in model evaluation tasks",
        ],
        "eligibility": (
            "Based on Bittensor/Gensyn: rewards go to compute providers, "
            "model deployers, and inference task completers. Quality of "
            "contribution outweighs quantity. Early subnet registration is key."
        ),
        "priority_tasks": [
            "Deploy a model or register as compute provider immediately",
            "Complete all inference/training tasks available",
            "Join all subnet communities early",
        ],
    },

    "modular": {
        "immediate": [
            "Check if data availability node program is open",
            "Read docs to understand sequencer/DA layer architecture",
            "Sign up for light node or full node program",
        ],
        "testnet": [
            "Run a light node or full node if accessible",
            "Submit data availability transactions on testnet",
            "Use every application built on the modular chain",
            "Bridge between modular layers if bridge is live",
            "Participate in network stress tests if announced",
        ],
        "eligibility": (
            "Based on Celestia/Avail: rewards go to early node runners, "
            "testnet participants, and ecosystem dApp users. "
            "Node operation is the highest signal — even light nodes count."
        ),
        "priority_tasks": [
            "Run a node — light node minimum, full node if possible",
            "Use every dApp built on the chain",
            "Submit DA transactions across multiple blocks",
        ],
    },

    "default": {
        "immediate": [
            "Read the full docs and understand the protocol mechanics",
            "Join Discord and identify any early contributor programs",
            "Check for testnet faucet and request tokens",
        ],
        "testnet": [
            "Add testnet to MetaMask (get RPC from docs)",
            "Request testnet tokens from faucet",
            "Send 10+ testnet transactions (vary amounts and times)",
            "Interact with every deployed contract/dApp on testnet",
            "Bridge assets if bridge is available",
            "Provide liquidity if DEX is live",
            "Vote on any governance proposals",
            "Run a node/validator if technically accessible",
        ],
        "eligibility": (
            "Based on general patterns: rewards likely go to early testnet users "
            "with on-chain diversity, community contributors, quest completers, "
            "and unique wallet interactions. Vary amounts and timing across wallets."
        ),
        "priority_tasks": [
            "Early testnet transactions with on-chain diversity",
            "Community role in Discord",
            "Complete any official quest campaigns (Galxe, Layer3, Zealy)",
        ],
    },
}

_COMMUNITY_TASKS = [
    "Post quality content about the project on X (not spam — genuine takes)",
    "Answer questions in Discord helpdesk channels",
    "Submit bug reports if you find issues on testnet",
    "Participate in community calls/AMAs",
    "Complete any official quest campaigns (Galxe, Layer3, Zealy)",
]

# ── Wallet-specific task variations ───────────────────────────────────────

def _wallet_variation_note(wallet_num: int) -> list[str]:
    """Generate timing/amount variation instructions per wallet number."""
    if wallet_num == 1:
        return [
            "Primary wallet — standard interaction timing",
            "Use this wallet for governance and community roles",
        ]
    elif wallet_num == 2:
        return [
            f"Wallet {wallet_num}: space interactions 2-3 days after Wallet 1",
            "Use different transaction amounts than Wallet 1",
            "Interact with dApps in different order",
        ]
    else:
        return [
            f"Wallet {wallet_num}: space interactions 5-7 days after Wallet 1",
            "Use a different IP if possible",
            "Vary amounts by at least 30% from other wallets",
            "Interact during different hours of the day",
        ]


def _get_tech_template(novel_tech: list) -> dict:
    """Return the most specific tech template available."""
    # Priority order — most specific first
    priority = [
        "fhe", "zero knowledge", "depin", "restaking",
        "ai blockchain", "modular",
    ]
    tech_lower = " ".join(novel_tech).lower()
    for tech in priority:
        if tech in tech_lower:
            return _TECH_TASKS[tech]
    return _TECH_TASKS["default"]


def generate_action_plan(project: dict, score_result=None,
                         num_wallets: int = None) -> dict:
    """
    Generate a full action plan.
    v0.9: tech-specific tasks, multi-wallet instructions auto-generated.
    """
    name = project.get("name", "Unknown")
    novel_tech = project.get("novel_tech", [])
    testnet = project.get("testnet_active", False)
    website = project.get("website", "")
    funding = project.get("funding_usd", 0) or 0
    category = project.get("category", "Infrastructure")

    # Determine wallet count from funding if not specified
    if num_wallets is None:
        if funding >= 50_000_000:
            num_wallets = 3
        elif funding >= 20_000_000:
            num_wallets = 2
        else:
            num_wallets = 1

    # Get tech-specific template
    template = _get_tech_template(novel_tech)

    # Build immediate tasks
    immediate = list(_UNIVERSAL_TASKS)
    if website:
        immediate.insert(0, f"Visit {website} and read the full docs")
    immediate.extend(template["immediate"])

    # Testnet tasks
    if testnet:
        testnet_tasks = list(template["testnet"])
    else:
        testnet_tasks = ["⏳ No testnet yet — check docs and Discord daily for launch announcement"]

    # Community tasks
    community = list(_COMMUNITY_TASKS)

    # Multi-wallet instructions
    wallet_plans = []
    for i in range(1, num_wallets + 1):
        wallet_plans.append({
            "wallet_num": i,
            "label": f"Wallet {i}",
            "variation_notes": _wallet_variation_note(i),
            "tasks": testnet_tasks[:4],  # Core tasks per wallet
        })

    wallet_note = {
        1: "Early stage — 1 wallet. Scale to 2-3 if testnet launches.",
        2: "Solid project — 2 wallets recommended.",
        3: "Strong conviction — 3 wallets. Vary timing and amounts.",
    }.get(num_wallets, f"{num_wallets} wallets")

    return {
        "project_name": name,
        "category": category,
        "novel_tech": novel_tech,
        "immediate_tasks": immediate,
        "testnet_tasks": testnet_tasks,
        "community_tasks": community,
        "wallet_plans": wallet_plans,
        "wallet_recommendation": {"count": num_wallets, "note": wallet_note},
        "eligibility_prediction": template["eligibility"],
        "priority_tasks": template["priority_tasks"],
        "tech_template_used": next(
            (k for k in _TECH_TASKS if k != "default"
             and k in " ".join(novel_tech).lower()),
            "default"
        ),
    }


def format_action_plan_for_telegram(project: dict, score_result,
                                     action_plan: dict) -> str:
    """Format action plan as Telegram HTML. v0.9: tech-specific insight shown."""
    name = project.get("name", "Unknown")
    funding = project.get("funding_usd", 0) or 0
    investors = project.get("investors", "Not found")
    novel_tech = project.get("novel_tech", [])
    website = project.get("website", "")
    mentioned_by = project.get("mentioned_by", "")
    tweet_url = project.get("tweet_url", "")
    category = project.get("category", "Infrastructure")

    funding_str = f"${funding/1e6:.1f}M" if funding >= 1e6 else "Unknown"
    tech_str = ", ".join(novel_tech) if novel_tech else category
    template_used = action_plan.get("tech_template_used", "default")
    wallets = action_plan["wallet_recommendation"]

    # v0.9.1: show X profile link instead of bare @handle
    # bare @handle in Telegram tries to tag a Telegram user — wrong behaviour
    if mentioned_by and not mentioned_by.startswith("["):
        caller_str = f'<a href="https://twitter.com/{mentioned_by}">@{mentioned_by}</a>'
    else:
        caller_str = mentioned_by or "autonomous scan"

    msg = (
        f"🎯 <b>ALPHA HUNTER — GENESIS CALL</b>\n\n"
        f"<b>Project:</b> {name}\n"
        f"<b>Score:</b> {score_result.score}/10 — {score_result.label}\n"
        f"<b>Category:</b> {category}\n"
        f"<b>Called by:</b> {caller_str}\n"
        f"<b>Funding:</b> {funding_str}\n"
        f"<b>Investors:</b> {investors[:100] if investors else 'Unknown'}\n"
        f"<b>Tech:</b> {tech_str}\n"
        f"<b>Website:</b> {website or 'Check tweet'}\n"
    )

    if template_used != "default":
        msg += f"<b>Strategy:</b> {template_used.upper()} playbook applied 🎯\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🏃 <b>DO THESE TODAY:</b>\n"
    for i, task in enumerate(action_plan["immediate_tasks"][:4], 1):
        msg += f"{i}. {task}\n"

    if action_plan["testnet_tasks"] and "No testnet" not in action_plan["testnet_tasks"][0]:
        msg += "\n🧪 <b>TESTNET GRIND:</b>\n"
        for i, task in enumerate(action_plan["testnet_tasks"][:4], 1):
            msg += f"{i}. {task}\n"

    # Multi-wallet section
    msg += f"\n💼 <b>WALLETS:</b> {wallets['note']}\n"
    if len(action_plan["wallet_plans"]) > 1:
        for wp in action_plan["wallet_plans"][1:]:
            notes = " | ".join(wp["variation_notes"][:2])
            msg += f"  <i>Wallet {wp['wallet_num']}: {notes}</i>\n"

    msg += f"\n🔮 <b>ELIGIBILITY ({template_used.upper()}):</b>\n"
    msg += f"{action_plan['eligibility_prediction'][:250]}\n"

    msg += "\n⭐ <b>PRIORITY ACTIONS:</b>\n"
    for task in action_plan["priority_tasks"][:3]:
        msg += f"• {task}\n"

    if tweet_url and tweet_url != "manual":
        msg += f"\n<a href='{tweet_url}'>📎 Source tweet</a>"

    msg += f"\n\n<i>Verdict: {score_result.verdict}</i>"
    return msg


def generate_wallet_tasks_for_db(project: dict, project_id: int,
                                  action_plan: dict) -> list[dict]:
    """
    v0.9: Generate per-wallet task records for DB insertion.
    Called from pipeline after genesis alert fires.
    Returns list of task dicts ready for db.add_grind_task().
    """
    tasks = []
    priority = action_plan.get("priority_tasks", [])
    wallet_plans = action_plan.get("wallet_plans", [])

    for wp in wallet_plans:
        wallet_label = wp["label"]

        # Priority tasks for wallet 1, testnet tasks for others
        if wp["wallet_num"] == 1:
            task_list = priority[:3] if priority else wp["tasks"][:3]
        else:
            task_list = wp["tasks"][:3]

        for task in task_list:
            tasks.append({
                "project_id": project_id,
                "wallet_label": wallet_label,
                "wallet_address": "",
                "task": task,
                "notes": " | ".join(wp["variation_notes"]),
            })

    return tasks
