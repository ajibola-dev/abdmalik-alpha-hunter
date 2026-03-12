# 🎯 Alpha Hunter

An autonomous intelligence agent that monitors high-conviction crypto alpha callers on X (Twitter),
extracts early-stage project mentions, researches them, and delivers actionable grind plans to Telegram.

## What it does
- Monitors a curated watchlist of quiet, high-conviction X accounts every 4 hours
- Detects new project mentions before they go viral
- Scores projects using the "Zun method" — team, tech novelty, VC quality, community size
- Generates per-project action plans with predicted eligibility criteria
- Tracks your grind across multiple wallets per project
- Sends everything to Telegram instantly

## Architecture
```
alpha-hunter/
├── main.py                    ← entry point
├── requirements.txt
├── Procfile                   ← Railway deployment
├── .env.example
├── config/
│   ├── settings.py            ← all configuration
│   └── watchlist.json         ← your X accounts to monitor
├── modules/
│   ├── x_monitor.py           ← Nitter scraping + manual tweet handler
│   ├── project_extractor.py   ← extracts project names from tweet text
│   ├── researcher.py          ← researches extracted projects (funding, team, tech)
│   ├── scorer.py              ← Zun-method scoring (0-10)
│   ├── action_planner.py      ← generates task checklists + eligibility predictions
│   ├── tracker.py             ← personal grind tracker (projects, wallets, tasks)
│   ├── telegram_bot.py        ← sends alerts + accepts manual tweet forwards
│   ├── database.py            ← SQLite storage
│   ├── scheduler.py           ← runs everything on schedule
│   └── logger_setup.py
└── data/
    └── alpha_hunter.db        ← auto-created
```

## Scoring (Zun Method)
| Signal | Points |
|---|---|
| Tier-1 VC (Paradigm, a16z, Polychain) | +2 |
| Novel technology (ZK, FHE, AI, DePIN) | +2 |
| Funding > $50M | +2 |
| Small community (early) | +1 |
| Active testnet | +1 |
| No token yet | +1 |
| Multiple callers agree | +1 |

Genesis alert threshold: 7/10

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in your .env
python main.py
```
