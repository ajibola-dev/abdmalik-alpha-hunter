"""
modules/telegram_bot.py — v2.0

Commands rebuilt around the farming brief format.
Removed: /wallets complexity, /suggestions, /approve, /reject
Added: /verticals, /grind (current active grinds)
"""
import logging
import threading
import time
import json
import re
import urllib.request
import urllib.error
import urllib.parse

from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
_last_update_id = 0


def _sanitise_html(text: str) -> str:
    text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', text)
    return text


def send_message(text: str, chat_id: str = None,
                 parse_mode: str = "HTML") -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    if parse_mode == "HTML":
        text = _sanitise_html(text)
    if len(text) > 4000:
        text = text[:3990] + "\n<i>...truncated</i>"

    target = chat_id or settings.TELEGRAM_CHAT_ID
    if not target:
        return False

    payload = json.dumps({
        "chat_id": target,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{_BASE_URL}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def _get_updates(offset: int = 0) -> list[dict]:
    try:
        url = f"{_BASE_URL}/getUpdates?timeout=30&offset={offset}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read())
            return data.get("result", [])
    except Exception:
        return []


# ── Commands ──────────────────────────────────────────────────────────────

def _cmd_start(chat_id: str):
    msg = (
        "🎯 <b>Alpha Hunter v2.0</b>\n\n"
        "<b>4 Verticals:</b> ZK/L2 | PerpDEX | Solana DeFi | Cosmos/DA\n\n"
        "<b>Commands:</b>\n"
        "/topalpha — Top projects to farm right now\n"
        "/newprojects — Discovered in last 48h\n"
        "/project &lt;name&gt; — Project details\n"
        "/lookup &lt;name&gt; — Research any project\n"
        "/research &lt;url or text&gt; — Research a tweet\n"
        "/verticals — What we're tracking and why\n"
        "/grind — Your active grind list\n"
        "/addgrind &lt;project&gt; — Add to grind list\n"
        "/donegrind &lt;project&gt; — Mark as done\n"
        "/watchlist — Signal accounts\n"
        "/status — System health\n"
        "/backfill [N] — Process last N tweets (default 50)\n"
    )
    send_message(msg, chat_id=chat_id)


def _cmd_verticals(chat_id: str):
    msg = (
        "📊 <b>The 4 Verticals</b>\n\n"
        "<b>ZK/L2</b> — Starknet gave mztacat $120k from $0. "
        "Find pre-Discord testnets. Deploy contracts. Run nodes.\n\n"
        "<b>PerpDEX</b> — Hyperliquid was lifechanging for CC2 and mztacat. "
        "Trade organically. LP vaults. Hold eco tokens.\n\n"
        "<b>Solana DeFi</b> — Jito gave CC2 mid-5 figs on ONE wallet. "
        "LP when TVL is low. Stake. Genuine usage.\n\n"
        "<b>Cosmos/DA</b> — Celestia $140k from $100 TIA stake. "
        "Stake → retro chain-reaction. Validator participation.\n\n"
        "<i>Target window: 3-6 months before mainnet + token. "
        "Pre-Galxe. Discord under 50K. You go in. You look like a paid ambassador.</i>"
    )
    send_message(msg, chat_id=chat_id)


def _cmd_topalpha(chat_id: str):
    projects = db.get_all_projects()
    actionable = [
        p for p in projects
        if p.get("label") in ("GRIND NOW", "WATCH CLOSELY")
        and not p.get("has_token")
    ]
    actionable = sorted(actionable,
                        key=lambda x: x.get("score", 0) or 0,
                        reverse=True)[:10]

    if not actionable:
        send_message(
            "No GRIND NOW projects yet.\n"
            "Run /backfill to process historical tweets, or wait for next scan.",
            chat_id=chat_id
        )
        return

    msg = "🔥 <b>Top Farming Opportunities</b>\n\n"
    for p in actionable:
        name = p["name"]
        label = p.get("label", "?")
        category = p.get("category", "?")
        mb = p.get("mentioned_by", "")
        testnet = "🧪" if p.get("testnet_active") else ""

        if mb and not mb.startswith("["):
            source = f'<a href="https://twitter.com/{mb}">@{mb}</a>'
        else:
            source = mb or "autonomous"

        emoji = "🔥" if label == "GRIND NOW" else "👀"
        msg += f"{emoji} <b>{name}</b> — {category}\n"
        msg += f"  {testnet} {label} | {source}\n\n"

    send_message(msg, chat_id=chat_id)


def _cmd_newprojects(chat_id: str):
    projects = db.get_all_projects()
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    recent = []
    for p in projects:
        discovered = p.get("discovered_at", "")
        try:
            dt = datetime.fromisoformat(discovered)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > cutoff:
                recent.append(p)
        except Exception:
            pass

    recent = sorted(recent, key=lambda x: x.get("score", 0) or 0, reverse=True)[:15]

    if not recent:
        send_message("No new projects in last 48h.", chat_id=chat_id)
        return

    msg = f"🆕 <b>New Projects (Last 48h)</b> — {len(recent)} found\n\n"
    for p in recent:
        name = p["name"]
        label = p.get("label", "?")
        mb = p.get("mentioned_by", "")
        if mb and not mb.startswith("["):
            source = f"@{mb}"
        else:
            source = mb or "?"
        testnet = "🧪" if p.get("testnet_active") else ""
        emoji = "🔥" if label == "GRIND NOW" else "👀" if label == "WATCH CLOSELY" else "📡"
        msg += f"{emoji} {testnet}<b>{name}</b> — {label} | {source}\n"

    send_message(msg, chat_id=chat_id)


def _cmd_project(chat_id: str, name: str):
    projects = db.get_all_projects()
    match = next(
        (p for p in projects if p["name"].lower() == name.lower()), None
    )
    if not match:
        send_message(f"Project '{name}' not found. Try /newprojects.", chat_id=chat_id)
        return

    funding = match.get("funding_usd", 0) or 0
    breakdown = {}
    try:
        breakdown = json.loads(match.get("score_breakdown", "{}") or "{}")
    except Exception:
        pass

    msg = (
        f"🔍 <b>{match['name']}</b>\n\n"
        f"<b>Conviction:</b> {match.get('label', '?')}\n"
        f"<b>Vertical:</b> {match.get('category', '?')}\n"
        f"<b>Funding:</b> {'${:.1f}M'.format(funding/1e6) if funding >= 1e6 else 'Unknown'}\n"
        f"<b>Investors:</b> {match.get('investors', 'Unknown') or 'Unknown'}\n"
        f"<b>Token live:</b> {'Yes ❌' if match.get('has_token') else 'No ✅'}\n"
        f"<b>Testnet:</b> {'Active ✅' if match.get('testnet_active') else 'Not detected'}\n"
        f"<b>GitHub:</b> {match.get('github', 'Unknown') or 'Unknown'}\n"
        f"<b>Mentioned by:</b> @{match.get('mentioned_by', '?')}\n"
        f"<b>Discovered:</b> {match.get('discovered_at', '?')[:10]}\n"
    )
    if breakdown:
        msg += f"\n<b>Signal breakdown:</b>\n"
        for k, v in breakdown.items():
            if k not in ("source", "kill"):
                msg += f"  {k}: {v}\n"

    send_message(msg, chat_id=chat_id)


def _cmd_lookup(chat_id: str, project_name: str):
    if not project_name or len(project_name) < 2:
        send_message("Usage: /lookup &lt;project name&gt;\nExample: /lookup miden",
                     chat_id=chat_id)
        return

    send_message(f"🔍 Looking up <b>{project_name}</b>...", chat_id=chat_id)

    def _run():
        try:
            from modules.researcher import research_project
            from modules.scorer import evaluate_project
            from modules.action_planner import format_farming_brief_for_telegram
            from modules import database as _db

            # Clear stale cache
            try:
                with _db._conn() as con:
                    con.execute(
                        "DELETE FROM project_research_cache WHERE project_name=?",
                        (project_name,)
                    )
            except Exception:
                pass

            project = research_project(project_name, tweet_text="")
            project["mentioned_by"] = "lookup"
            project["tweet_url"] = ""
            project["tweet_text"] = ""

            if project.get("has_token"):
                send_message(
                    f"❌ <b>{project_name}</b> already has a live token.\n"
                    "Farming window closed.",
                    chat_id=chat_id
                )
                return

            brief = evaluate_project(project, caller_tier=0)
            funding = project.get("funding_usd", 0) or 0

            if brief.conviction in ("GRIND NOW", "WATCH CLOSELY"):
                msg = format_farming_brief_for_telegram(brief, project)
                send_message(msg, chat_id=chat_id)
            else:
                msg = (
                    f"🔍 <b>{project_name}</b>\n\n"
                    f"Conviction: {brief.conviction}\n"
                    f"Vertical: {brief.vertical}\n"
                    f"Funding: {'${:.1f}M'.format(funding/1e6) if funding >= 1e6 else 'Not found'}\n"
                    f"Testnet: {'Active ✅' if project.get('testnet_active') else 'Not detected'}\n"
                    f"Token: {'Live ❌' if project.get('has_token') else 'No ✅'}\n\n"
                    f"<i>{brief.why}</i>\n\n"
                )
                if brief.red_flags:
                    msg += "⚠️ " + " | ".join(brief.red_flags[:2]) + "\n"
                if brief.actions:
                    msg += "\n<b>If you still want to farm:</b>\n"
                    for a in brief.actions[:3]:
                        msg += f"• {a}\n"
                send_message(msg, chat_id=chat_id)

        except Exception as exc:
            send_message(f"❌ Lookup failed: {exc}", chat_id=chat_id)
            logger.error("Lookup error: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def _cmd_research_paste(chat_id: str, text: str):
    """Research pasted tweet text."""
    send_message("🔍 Researching...", chat_id=chat_id)

    def _run():
        try:
            from modules.x_monitor import research_text_directly
            from modules.pipeline import process_tweet
            tweet = research_text_directly(text)
            results = process_tweet(tweet, caller_tier=1)

            meaningful = [r for r in results
                          if r["brief"].conviction in ("GRIND NOW", "WATCH CLOSELY")]

            if not meaningful:
                if results:
                    names = [r["project"]["name"] for r in results]
                    send_message(
                        f"🔍 Found {len(results)} candidate(s) but none meet farming criteria.\n"
                        f"Names: {', '.join(names[:5])}\n\n"
                        f"<i>Try pasting a tweet about a specific testnet or farming program.</i>",
                        chat_id=chat_id
                    )
                else:
                    send_message(
                        "🔍 No farming opportunities detected.\n"
                        "<i>Paste a tweet about a testnet, points program, or node launch.</i>",
                        chat_id=chat_id
                    )
                return

            from modules.action_planner import format_farming_brief_for_telegram
            for item in meaningful:
                msg = format_farming_brief_for_telegram(
                    item["brief"], item["project"]
                )
                send_message(msg, chat_id=chat_id)

        except Exception as exc:
            send_message(f"❌ Research error: {exc}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


def _cmd_watchlist(chat_id: str):
    import json as _json
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = _json.load(f)
        accounts = [a for a in data.get("accounts", [])
                    if a.get("status") == "active"]
    except Exception as exc:
        send_message(f"❌ {exc}", chat_id=chat_id)
        return

    msg = f"👀 <b>Signal Accounts</b> ({len(accounts)})\n\n"
    for a in accounts:
        handle = a["handle"]
        tier = a.get("tier", 2)
        verticals = ", ".join(a.get("verticals", []))
        url = f"https://twitter.com/{handle}"
        tier_str = "⭐" if tier == 1 else "•"
        msg += f"{tier_str} <a href='{url}'>@{handle}</a> — {verticals}\n"

    send_message(msg, chat_id=chat_id)


def _cmd_status(chat_id: str):
    from datetime import datetime, timezone
    heartbeat = db.get_last_heartbeat()
    projects = db.get_all_projects()
    total = len(projects)
    grind_now = len([p for p in projects if p.get("label") == "GRIND NOW"])
    watch = len([p for p in projects if p.get("label") == "WATCH CLOSELY"])

    if heartbeat:
        try:
            dt = datetime.fromisoformat(heartbeat)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            mins_ago = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
            hb_str = f"{mins_ago}m ago"
        except Exception:
            hb_str = heartbeat
    else:
        hb_str = "Never"

    msg = (
        f"🎯 <b>Alpha Hunter v2.0 Status</b>\n\n"
        f"Last scan: {hb_str}\n"
        f"Projects tracked: {total}\n"
        f"🔥 GRIND NOW: {grind_now}\n"
        f"👀 WATCH CLOSELY: {watch}\n\n"
        f"<b>Verticals:</b> ZK/L2 | PerpDEX | Solana DeFi | Cosmos/DA\n"
        f"<b>Signal accounts:</b> mztacat, CC2Ventures, Faycytw, MingoAirdrop, x256xx, FabianoSolana"
    )
    send_message(msg, chat_id=chat_id)


def _cmd_backfill(chat_id: str, n: int = 50):
    send_message(f"⏳ Backfilling last {n} tweets per account...", chat_id=chat_id)

    def _run():
        try:
            from modules.x_monitor import backfill_all_accounts
            from modules.pipeline import (extract_stage, filter_stage,
                                           research_stage, evaluate_stage,
                                           alert_stage, _load_watchlist)
            watchlist = _load_watchlist()
            scan_id = f"backfill_{int(time.time())}"
            tweets = backfill_all_accounts(watchlist, max_tweets=n, scan_id=scan_id)
            candidates = extract_stage(tweets, watchlist, scan_id)
            filtered = filter_stage(candidates, scan_id)
            enriched = research_stage(filtered, scan_id)
            evaluated = evaluate_stage(enriched, scan_id)
            sent = alert_stage(evaluated, scan_id, source="backfill")
            send_message(
                f"✅ Backfill complete.\n"
                f"Tweets: {len(tweets)} | Candidates: {len(candidates)} | "
                f"Alerts: {sent}",
                chat_id=chat_id
            )
        except Exception as exc:
            send_message(f"❌ Backfill failed: {exc}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ── Router ────────────────────────────────────────────────────────────────

def _handle_message(text: str, chat_id: str):
    lower = text.lower().strip()

    if lower in ("/start", "/help"):
        _cmd_start(chat_id)
    elif lower == "/topalpha":
        _cmd_topalpha(chat_id)
    elif lower == "/newprojects":
        _cmd_newprojects(chat_id)
    elif lower.startswith("/project "):
        _cmd_project(chat_id, text[9:].strip())
    elif lower.startswith("/lookup "):
        _cmd_lookup(chat_id, text[8:].strip())
    elif lower == "/watchlist":
        _cmd_watchlist(chat_id)
    elif lower == "/verticals":
        _cmd_verticals(chat_id)
    elif lower == "/status":
        _cmd_status(chat_id)
    elif lower.startswith("/backfill"):
        parts = text.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 50
        _cmd_backfill(chat_id, n)
    elif lower.startswith("/research "):
        raw = text[10:].strip()
        urls = re.findall(r'https?://(?:twitter\.com|x\.com)/\S+', raw)
        if urls:
            send_message(
                "⚠️ URL research: x.com/i/status/ format not supported by RapidAPI.\n"
                "Paste the tweet text directly instead.",
                chat_id=chat_id
            )
        elif len(raw) > 15:
            _cmd_research_paste(chat_id, raw)
        else:
            send_message(
                "Usage: /research &lt;paste tweet text&gt;\n"
                "Example: /research 1. Miden 2. Fhenix 3. Zama — going all in",
                chat_id=chat_id
            )
    elif len(text) > 30 and not text.startswith("/"):
        _cmd_research_paste(chat_id, text)
    else:
        send_message(
            "Unknown command. Use /start to see all commands.",
            chat_id=chat_id
        )


def start_polling():
    """Start Telegram polling loop."""
    global _last_update_id
    logger.info("Telegram polling started")

    while True:
        try:
            updates = _get_updates(offset=_last_update_id + 1)
            for update in updates:
                _last_update_id = update.get("update_id", _last_update_id)
                message = update.get("message", {})
                text = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))
                if text and chat_id:
                    try:
                        _handle_message(text, chat_id)
                    except Exception as exc:
                        logger.error("Handler error: %s", exc)
        except Exception as exc:
            logger.error("Polling error: %s", exc)
            time.sleep(5)
