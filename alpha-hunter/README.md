# 🎯 Alpha Hunter v2.0

Autonomous Web3 farming intelligence bot. Monitors proven alpha callers on X, GitHub testnet activity, and surfaces early-stage projects in the 4 verticals that have historically produced the biggest airdrops — before they trend.

**Stack:** Python 3.11, SQLite, Railway/VPS deployment, Telegram alerts

---

## The Strategy

Based on the playbook of top 0.01% airdrop farmers (mztacat, CC2Ventures, Faycytw, MingoAirdrop, x256xx, FabianoSolana):

> "The opportunity never disappeared. Only the lazy strategy did." — mztacat

**Target window:** 3-6 months before mainnet + token launch. Pre-Galxe. Discord under 50K. This is when organic, technical interaction = max allocation at TGE.

**Zero-cost first.** Build capital from airdrops. Compound into next conviction play.

---

## The 4 Verticals

| Vertical | Pattern | Proven Example |
|----------|---------|----------------|
| ZK/L2 | Deploy contracts, use dApps, bridge — pre-Discord | Starknet: $120k from $0 (mztacat) |
| PerpDEX | Organic trading volume, LP vaults, hold eco tokens | Hyperliquid: lifechanging for CC2 + mztacat |
| Solana DeFi | LP when TVL is low, stake, genuine usage | Jito: mid-5 figs on ONE wallet (CC2) |
| Cosmos/DA | Small stake → retro chain-reaction | Celestia: $140k from $100 TIA (mztacat) |

---

## Architecture

```
X Monitor (every 4h)
  └── 10 signal accounts → extract → vertical filter → research → evaluate → alert

GitHub Scanner (every 24h)
  └── ZK/L2 + PerpDEX + Solana + Cosmos queries → pre-token testnet detection
```

Two scan cycles. No DeFiLlama. No CoinGecko trending. No 6-hour scans.

---

## Signal Accounts

| Account | Vertical | Why |
|---------|----------|-----|
| @mztacat | ZK/L2, PerpDEX, Cosmos/DA | Nodes/testnets specialist. Starknet $120k from $0. |
| @CC2Ventures | PerpDEX, Solana DeFi, Cosmos/DA | Wormhole biggest cook ever. Jito mid-5 figs one wallet. |
| @Faycytw | ZK/L2 | ZK/L2 specialist. +$300k via honest UX-aligned farming. |
| @MingoAirdrop | ZK/L2, PerpDEX, Cosmos/DA | Multi-vertical SMART farmer. Starknet + Hyperliquid. |
| @x256xx | PerpDEX | PerpDEX pure specialist. Hyperliquid ecosystem tier list. |
| @FabianoSolana | Solana DeFi | $10k → 6 figures via LP/staking/genuine DeFi usage. |
| @3liXBT | ZK/L2, Solana DeFi | Cross-vertical execution. Wallet setup meta. |
| @Zun2025 | ZK/L2, Cosmos/DA | Structured pre-TGE conviction lists. |
| @defi_explora | ZK/L2, Cosmos/DA | Posts funded projects early, seed rounds. |
| @bigray0x | ZK/L2 | Numbered testnet lists. Free testnet opportunities. |

---

## How the Evaluation Works

No arbitrary weighted score. Pattern matching against historical airdrop criteria:

**GRIND NOW** — Matches the profile of Starknet/Hyperliquid/Jito/Celestia at the time they were farmable:
- No live token
- Active testnet or points program
- Under 50K Discord (or no Discord yet)
- Pre-Galxe
- Technical interaction required
- Backed by funds with airdrop track record

**WATCH CLOSELY** — Strong signals but one piece missing (e.g. testnet not live yet)

**MONITOR** — Early detection, keep on radar

**SKIP** — Token live, or doesn't fit any vertical

---

## Alert Format

Not a score. A **farming brief**:

```
🔥 ALPHA HUNTER — GRIND NOW

Project: [name]
Vertical: ZK/L2 (Starknet-pattern)
Called by: @mztacat
Why this fits: ZK/L2 vertical. No Discord yet — extremely early window. Pre-Galxe. Technical interaction = dev edge.
Window: Wide open — extremely early. Move now.

🏃 DO THIS NOW (ZK/L2):
1. Deploy a simple contract or interact with deployed dApps on testnet
2. Bridge assets through the official bridge
3. Use every deployed dApp at least once
4. Get Discord OG role the moment Discord opens
5. Run transactions across multiple days/weeks
6. If node program opens: run a node.

💼 Wallets: 3 wallets — stagger entries by 1-2 weeks each.
✅ Zero cost — testnet/points, no capital required to start.
```

---

## Module Reference

| Module | Purpose |
|--------|---------|
| `modules/pipeline.py` | 4-stage pipeline: extract → filter → research → evaluate → alert |
| `modules/project_extractor.py` | Vertical-aware extraction — only extracts near ZK/L2/PerpDEX/Solana/Cosmos signals |
| `modules/researcher.py` | Focused research: token check, GitHub, testnet detection, investor extraction from tweet |
| `modules/scorer.py` | Pattern matcher against Starknet/Hyperliquid/Jito/Celestia historical criteria |
| `modules/action_planner.py` | Formats farming brief for Telegram |
| `modules/github_scanner.py` | GitHub queries per vertical — finds testnet repos before Twitter coverage |
| `modules/scheduler.py` | 2 cycles: X Monitor (4h) + GitHub (24h) |
| `modules/telegram_bot.py` | All commands |
| `modules/x_monitor.py` | RapidAPI Twitter fetch |
| `modules/database.py` | SQLite — projects, scores, alerts, heartbeat |
| `modules/watchdog.py` | Health monitor — Telegram alert on silence |
| `config/watchlist.json` | Signal accounts with tier, weight, verticals |
| `config/settings.py` | All constants — overridable via env vars |

---

## Telegram Commands

```
/start              See all commands
/topalpha           GRIND NOW + WATCH CLOSELY projects
/newprojects        Discovered in last 48h
/project <name>     Full project details
/lookup <name>      Research any project on demand
/research <text>    Research pasted tweet text
/verticals          The 4 verticals and why they print
/watchlist          Signal accounts
/status             System health
/backfill [N]       Process last N tweets per account (default 50)
```

---

## Deployment

**Required env vars:**
```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
RAPIDAPI_KEY=         # twitter241.p.rapidapi.com by davethebeast
GITHUB_TOKEN=         # optional but recommended
```

**Startup:**
```
python main.py
```

**Procfile:**
```
worker: python main.py
```

**Database:** SQLite at `/app/data/alpha_hunter.db` — persist via volume.

---

## Version History

### v2.0 (May 2026) — Complete rebuild
**Strategy change:** From generic project scoring to pattern matching against historical airdrop criteria.

Removed: DeFiLlama scanner, CoinGecko scanner, async research pipeline, account discovery  
Rebuilt: Scorer (historical patterns), extractor (vertical-aware), researcher (5 key questions), pipeline (simplified), GitHub scanner (vertical queries)  
Watchlist: 10 proven signal accounts across 4 verticals

### v0.9.9 (Mar 2026) — Dead code audit
Removed 18 dead code findings. Probation scaffolding cleaned up. CMC fallback added to token check.

### v0.9.8 (Mar 2026) — Token check hardening
CoinGecko → CMC fallback chain. Blocklist expanded. Fail-safe on both APIs down.

### v0.9.7 (Mar 2026) — Scoring accuracy
Known project seed data. FHE contamination fixed. DeFiLlama fuzzy matching improved.

### v0.9.5 (Mar 2026) — Smart extraction
Proximity-based extraction. /lookup command added.

### v0.9.2 (Mar 2026) — Watchlist system
Probation tier. 5 acceptance criteria. /watchlist_review.

### v0.8 (Mar 2026) — Autonomous scanning
CoinGecko trending scanner. Testnet weight rebalance.

### v0.7 (Mar 2026) — Critical bug fixes
Event loop crash fixed. Funding scanner pre-score filter.

### v0.1 (Jan 2026) — Original engineer
Foundation: RapidAPI Twitter, DeFiLlama, GitHub, CoinGecko, Zun Method scoring, SQLite, Telegram bot, Railway deployment.
