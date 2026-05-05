# 🎯 Alpha Hunter v0.9.8

Autonomous crypto airdrop intelligence bot. Monitors Twitter/X accounts, funding databases, GitHub, and CoinGecko to surface pre-token blockchain projects worth farming — before they trend.

Deployed on Railway. Alerts via Telegram.

---

## What It Does

- Monitors 6 curated X accounts every 4 hours for project mentions
- Scans DeFiLlama's 6,865+ funding raises every 12 hours autonomously
- Scans GitHub for testnet repositories every 24 hours
- Scans CoinGecko trending + keyword search every 6 hours
- Scores every discovered project 0–10 using the Zun Method
- Sends Telegram alerts with tech-specific farming action plans
- Tracks your wallets and grind tasks per project

---

## Architecture

```
Scheduler (5 threads)
├── X Monitor (every 4h)     → project_extractor → pipeline_async → scorer → alert
├── CoinGecko Scanner (6h)   → coingecko_scanner → scorer → alert
├── Funding Scanner (12h)    → funding_scanner → scorer → alert
├── GitHub Scanner (24h)     → github_scanner → scorer → alert
└── Network Discovery (24h)  → account_discovery → suggestions
```

---

## Module Reference

| Module | Purpose |
|--------|---------|
| `main.py` | Entry point — starts scheduler + watchdog + Telegram polling |
| `modules/scheduler.py` | 5 threads with staggered start delays |
| `modules/pipeline.py` | 5-stage sync pipeline: extract → filter → research → score → alert |
| `modules/pipeline_async.py` | Async wrapper for X Monitor using aiohttp |
| `modules/project_extractor.py` | Proximity-based name extraction from tweets |
| `modules/researcher.py` | Sync research: DeFiLlama + GitHub + CoinGecko + website scrape |
| `modules/researcher_async.py` | Async research with aiohttp + Semaphore |
| `modules/scorer.py` | Zun Method weighted scoring 0–10 |
| `modules/funding_scanner.py` | DeFiLlama + CryptoRank funding scanner |
| `modules/coingecko_scanner.py` | CoinGecko trending + keyword search |
| `modules/github_scanner.py` | GitHub testnet repo detection |
| `modules/action_planner.py` | Tech-specific task templates per project type |
| `modules/telegram_bot.py` | All Telegram commands and polling |
| `modules/x_monitor.py` | RapidAPI Twitter fetch with user_id cache + since_id |
| `modules/database.py` | SQLite — projects, scores, tasks, wallets, alerts |
| `modules/watchdog.py` | Health monitor — alerts on silence > 30min |
| `modules/account_discovery.py` | Discovers new alpha accounts from interactions |
| `config/settings.py` | All constants — overridable via Railway env vars |
| `config/watchlist.json` | Monitored accounts with tier, weight, status |

---

## Scoring System (Zun Method)

Projects scored 0–10 using weighted signals:

| Signal | Weight | Notes |
|--------|--------|-------|
| VC quality | 0.25 | Top-tier VCs: a16z, Paradigm, Multicoin, etc. |
| Novel tech | 0.22 | FHE, ZK, DePIN, restaking, modular, AI |
| No token | 0.17 | Pre-TGE is the core requirement |
| Funding | 0.17 | DeFiLlama funding amount |
| Testnet | 0.12 | Active testnet = 8.0, incentivized = 10.0 |
| Caller quality | 0.05 | Tier-1 caller = 10.0 x weight |
| Multi-caller | 0.02 | Same project mentioned by 2+ accounts |

**Score labels:**
- 8.0+ : STRONG CONVICTION
- 6.5+ : GENESIS CALL
- 5.0+ : WATCHING
- 3.0+ : EARLY SIGNAL
- Below : WEAK SIGNAL

**Alert threshold:** GENESIS_THRESHOLD=5.0 (overridable via env var)

**Autonomous scoring:** Projects found by DeFiLlama/GitHub/CoinGecko without a Twitter caller get caller_quality=5.0 (neutral) — not penalised for missing human signal.

**Unknown data:** funding=0 scores 1.0 (unknown, not confirmed zero). Empty investors scores 1.0 (unknown, not confirmed unbacked).

---

## Tech-Specific Action Plans

| Category | Strategy |
|----------|---------|
| FHE | FHE compute tasks, Discord OG, governance votes |
| ZK | Prover node, proof generation, on-chain diversity |
| DePIN | Hardware connection, uptime, geographic diversity |
| Restaking | AVS operator registration, multi-asset staking |
| AI/ML | Compute provision, model deployment, inference tasks |
| Modular | Node operation, DA transactions, ecosystem dApps |
| Default | Testnet transactions, Discord, quests (Galxe/Layer3) |

---

## Watchlist System

**Acceptance criteria (all 5 required):**
1. Posts about projects with NO live token yet
2. Mentions SPECIFIC project names
3. References backing signals (VC, funding, testnet, node/ambassador)
4. Posts BEFORE projects trend (under 50K followers preferred)
5. Structured or specific calls — not general commentary

**Tier system:**
- Tier 1 (weight 0.75–1.0): Deep infra focus, conviction calls
- Tier 2 (weight 0.85–1.0): Broader scope, 4/5 criteria
- Tier 0 (probation): Not fetched in scans, auto-promotes on cross-mention threshold

**Current active accounts:**
- @Zun2025 — Tier 1, w=1.0
- @defi_explora — Tier 1, w=0.85
- @MztaCat — Tier 2, w=1.0
- @bigray0x — Tier 1, w=0.75
- @huseyin1tekin — Tier 1, w=0.85 (Zun + defi_explora follow)
- @MalikRasak1 — Tier 2, w=0.85

**Probation (7):** AlphaFrog13, CA_Template, asedd72, WEB3Seer, defioyins, abrahamonchain, CryptoSchool13

---

## Token Verification (Funding Scanner)

Before any funding alert fires:
1. Internal blocklist (~60 known launched projects) — instant, no API
2. CoinGecko — primary check
3. CoinMarketCap — fallback if CoinGecko rate-limited or down
4. Both unavailable — skip safely (never alerts when uncertain)

---

## Known Project Seed Data

DeFiLlama doesn't always match common project names. Hardcoded fallback in researcher.py:
- Zama ($73M, Paradigm), Fhenix ($22M, Multicoin), Arcium ($5.5M, Greenfield)
- Inco ($4.5M, 1kx), Fairblock ($1.5M, NGC), Miden ($25M, a16z)
- Boundless ($20M, Blockchain Capital)

---

## Telegram Commands

```
/topalpha                    Top scored projects from all sources
/newprojects                 Discovered in last 48h
/project <name>              Full project details + score breakdown
/watchlist                   Active accounts with X profile links
/watchlist_review            Probation accounts + cross-mention counts
/promote_watchlist <handle>  Manually promote probation account
/tasks                       Pending grind tasks
/done <id>                   Mark task complete
/wallets                     Wallet summary with progress bars
/addwallet <p> <l> <t>       Add task to specific wallet
/status                      Agent health check
/lookup <name>               Research any project by name (force fresh)
/research <url or text>      Research tweet URL or pasted text
/backfill [N]                Process last N tweets (default 50)
/debug                       Full pipeline diagnostic
/suggestions                 Pending account discoveries
/approve <handle>            Add discovered account
/reject <handle>             Dismiss suggestion
```

---

## Deployment (Railway)

**Required env vars:**
```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
RAPIDAPI_KEY=       # twitter241.p.rapidapi.com by davethebeast
GITHUB_TOKEN=       # optional but recommended
```

**Optional overrides:**
```
GENESIS_THRESHOLD=5.0
SCAN_INTERVAL_HOURS=4
```

**Database:** SQLite at /app/data/alpha_hunter.db — persists via Railway volume.

**Startup:** python main.py

**Procfile:** worker: python main.py

---

## Key Design Decisions

**Proximity-based extraction:** Names only extracted from tweet sentences containing a crypto/funding signal word. Prevents general commentary from producing noise candidates.

**Async research:** All X Monitor research runs concurrently via aiohttp. Semaphores created fresh per asyncio.run() call — never stored at module level (prevents event loop binding crash).

**DeFiLlama cache:** Loaded once per scan, shared across concurrent tasks via threading.Lock.

**Since_id logic:** Only fetches tweets newer than last seen tweet_id per account. DB-backed dedup prevents reprocessing on restart.

**List extraction:** Numbered lists bypass the alpha signal gate. Each list item researched in isolation to prevent tech signal contamination between projects.

---

## Version History

- v0.2–v0.5: Core pipeline, DB dedup, async research, DeFiLlama cache
- v0.6: Watchdog, /backfill, multi-wallet tracker, GENESIS_THRESHOLD=5.0
- v0.7: Event loop crash fixed, funding scanner pre-score filter
- v0.7.2: Multi-endpoint tweet fetch, text paste research, improved tech detection
- v0.8: CoinGecko trending scanner, testnet weight rebalance, autonomous scoring
- v0.9: Tech-specific action plans, multi-wallet task generation, /lookup
- v0.9.1: x.com/i/status/ URL fix, paste quality gate, @handle to X profile link
- v0.9.2: Probation tier system, 5 acceptance criteria, /watchlist_review
- v0.9.3: Autonomous scorer fix, /topalpha all sources, weight adjustments
- v0.9.4: Fast filter hardened, DeFiLlama multi-fetch fixed
- v0.9.5: Proximity-based extraction, /lookup command
- v0.9.6: Auto-promotion disabled, Telegram 400 fix, numbered list extraction fix
- v0.9.7: Known project seed data, scoring gap fixed, FHE contamination fixed
- v0.9.8: CoinGecko to CMC fallback chain, blocklist expanded, fail-safe token check

---

## Full Version History (including original engineer's work)

### v0.1 — Original Engineer
The foundation the entire project is built on:
- Basic project structure: `main.py`, `config/settings.py`, `config/watchlist.json`
- Twitter/X monitoring via RapidAPI (twitter241.p.rapidapi.com by davethebeast)
- Basic project name extraction from tweet text (capitalised word detection)
- DeFiLlama raises integration — first funding data source
- GitHub search integration — repo-based signal detection
- CoinGecko token-live check — basic pre-token filter
- Website scraping via BeautifulSoup
- Zun Method scoring concept — first implementation of weighted scoring
- SQLite database with initial tables: watched_accounts, discovered_projects, project_scores, grind_tracker, alerts_sent, scan_log
- Telegram bot with initial commands: /start, /status, /topalpha, /newprojects, /project, /watchlist, /tasks, /done, /research, /suggestions, /approve, /reject
- Basic watchlist.json with Zun2025, defi_explora, MztaCat
- Railway deployment setup (Procfile, requirements.txt)
- Account discovery module — finds new alpha accounts from interactions

### v0.2 — Claude (this session, starting point)
First fixes after reviewing the uploaded codebase:
- DB-backed tweet dedup (tweet_seen table) — survives restarts
- DB-backed research cache (project_research_cache) — skip repeat API calls within TTL
- Fixed broken _cmd_suggestions() import pattern in telegram_bot.py
- Structured scan_id logging propagated through all stages
- GitHub authentication bug fixed (token headers not being sent)
- Twitter user_id cache (24h TTL) — avoids repeat user lookups

### v0.3 — Pipeline Staging
- run_scan_cycle() refactored into 5 discrete named stages: extract → fast_filter → research → score → alert
- process_tweet() retained for /research command backward compatibility
- MAX_PROJECTS_PER_TWEET and MAX_PROJECTS_PER_SCAN enforced from settings

### v0.4 — Async Research
- Async research pipeline via aiohttp replacing sync sequential calls
- asyncio.Semaphore for concurrent CoinGecko rate limiting

### v0.5 — DeFiLlama Dedup + Standardisation
- DeFiLlama shared cache with threading.Lock
- funding_scanner and github_scanner standardised
- Retry/backoff standardised across all modules

### v0.6 — Watchdog + Wallets + Backfill
- Watchdog module — health checks every 30min, Telegram alerts on silence
- /backfill command — historical tweet processing
- Multi-wallet tracker (/wallets, /addwallet commands)
- GENESIS_THRESHOLD lowered to 5.0
- Scorer calibrated against real projects (Zama, Story, Miden etc.)

### v0.7 — Critical Bug Fixes
- FIXED: asyncio.Semaphore event loop binding crash — semaphores now created fresh per asyncio.run() call, never at module level
- Funding scanner pre-score filter — cuts 574 to ~100 CoinGecko calls
- since_id logic — only fetches genuinely new tweets per scan
- fetch_tweet_from_url tries multiple endpoint patterns

### v0.7.2 — Research Improvements
- Multi-endpoint tweet fetch with 4 response structure patterns
- research_text_directly() for pasted tweet text
- Tech detection improved (tweet + GitHub + website combined)
- x.com/i/status/ URL format parsing fixed

### v0.8 — Autonomous Scanning
- CoinGecko trending scanner added (new coingecko_scanner.py)
- Testnet weight rebalanced 0.09 to 0.12
- Autonomous sources no longer penalised by missing caller signal
- Tech scoring expanded for modular, RWA, payments categories

### v0.9 — Action Plans + /lookup
- Tech-specific action plan templates (FHE/ZK/DePIN/restaking/AI/modular)
- Multi-wallet task auto-generation on genesis alert
- /lookup command — research any project by name on demand

### v0.9.1 — Bug Fixes
- x.com/i/status/ URL parsing fixed in fetch_tweet_from_url
- Paste text quality gate (score >= 3.0 to show results)
- mentioned_by shows X profile link not bare @handle (Telegram tagging fix)

### v0.9.2 — Watchlist System
- Probation tier (tier=0) — accounts monitored but not fetched
- 5 explicit acceptance criteria documented in watchlist.json
- /watchlist_review and /promote_watchlist commands
- Auto-promotion logic (cross-mention threshold)

### v0.9.3 — Autonomous Scorer Fix
- Projects found by DeFiLlama/GitHub/CoinGecko score on signals only
- caller_quality=5.0 neutral for autonomous finds (not penalised)
- /topalpha shows projects from all sources with source labels
- huseyin1tekin weight bumped to 0.85 (Zun + defi_explora both follow)

### v0.9.4 — Noise Reduction
- fast_filter_stage hardened with _NOISE_PHRASES and _HARD_NOISE_WORDS
- Common English suffix detector catches "Onboarded", "Animoca" style words
- DeFiLlama multi-fetch fixed (threading.Lock on fast path)

### v0.9.5 — Smart Extraction
- Proximity-based extraction — names only extracted near signal words
- Numbered lists bypass all gates (structured alpha content)
- /lookup command — clears stale cache before each lookup

### v0.9.6 — Stability Fixes
- Auto-promotion disabled (was promoting all probation accounts incorrectly)
- Telegram 400 errors fixed (_sanitise_html for & in investor names)
- Numbered list extraction fixed (bypasses alpha signal gate, accepts lowercase)

### v0.9.7 — Scoring Accuracy
- Known project seed data — hardcoded funding for Zama, Fhenix, Miden etc.
- Scoring gap fixed — unknown funding/VC scores 1.0 not 0.0
- FHE contamination fixed — list items researched in isolation
- DeFiLlama fuzzy matching improved (4 strategies: exact/starts/contains/reverse)
- Noise words expanded (farm, frogy, round, pantera, framework etc.)

### v0.9.8 — Token Check Hardening
- CoinGecko to CoinMarketCap fallback chain in verify_no_token()
- Rate limit failure no longer defaults to "assume safe" — triggers fallback instead
- Blocklist expanded to ~60 known launched projects
- Blocklist checked before any API call (instant rejection)
