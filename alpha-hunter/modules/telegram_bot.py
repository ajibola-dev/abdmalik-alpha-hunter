"""
modules/telegram_bot.py
Telegram interface for Alpha Hunter.

Commands:
  /start          - Help menu
  /status         - Agent health + stats
  /topalpha       - Top 5 scored projects
  /newprojects    - Projects discovered in last 48h
  /project <name> - Full details on a specific project
  /watchlist      - Show monitored X accounts
  /tasks          - Pending grind tasks
  /done <id>      - Mark task complete
  /research <url> - Research a tweet URL manually
"""
import logging
import urllib.request
import json
import threading
import time
import re
from config.settings import settings

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
_last_update_id = 0


def send_message(text: str, chat_id: str = None,
                 parse_mode: str = "HTML") -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set")
        return False

    # Telegram max message length is 4096
    if len(text) > 4000:
        text = text[:3990] + "\n<i>...truncated</i>"

    cid = chat_id or settings.TELEGRAM_CHAT_ID
    payload = json.dumps({
        "chat_id": cid,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{_BASE}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return True
            logger.error("Telegram API error: %s", result)
            return False
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def get_updates(offset: int = 0) -> list[dict]:
    try:
        url = f"{_BASE}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url, timeout=35) as resp:
            data = json.loads(resp.read())
            return data.get("result", [])
    except Exception as exc:
        logger.debug("getUpdates error: %s", exc)
        return []


# ── Command handlers ───────────────────────────────────────────────────────

def _cmd_start(chat_id: str):
    send_message(
        "🎯 <b>Alpha Hunter — Active</b>\n\n"
        "<b>Commands:</b>\n"
        "/topalpha — Top scored projects\n"
        "/newprojects — Discovered last 48h\n"
        "/project &lt;name&gt; — Project details\n"
        "/watchlist — Monitored X accounts\n"
        "/tasks — Your pending grind tasks\n"
        "/done &lt;id&gt; — Mark task complete\n"
        "/status — Agent health\n"
        "/research &lt;tweet_url&gt; — Research a tweet\n"        "/suggestions — Pending account discoveries\n"        "/approve &lt;handle&gt; — Add discovered account\n"        "/reject &lt;handle&gt; — Dismiss suggestion\n\n"
        "<i>Or just forward any tweet URL and I'll research it instantly.</i>",
        chat_id=chat_id
    )


def _cmd_topalpha(chat_id: str):
    from modules.database import get_all_projects
    projects = get_all_projects()
    scored = [p for p in projects if p["score"] and p["score"] >= 5]
    scored = sorted(scored, key=lambda x: x["score"] or 0, reverse=True)[:5]

    if not scored:
        send_message("No high-scoring projects yet. Run a scan first.", chat_id=chat_id)
        return

    msg = "🏆 <b>Top Alpha Picks</b>\n\n"
    for p in scored:
        funding = p["funding_usd"] or 0
        f_str = f"${funding/1e6:.0f}M" if funding >= 1e6 else "Unknown funding"
        msg += (f"<b>{p['name']}</b> — {p['score']}/10 {p['label']}\n"
                f"  {f_str} | @{p['mentioned_by'] or '?'}\n\n")
    send_message(msg, chat_id=chat_id)


def _cmd_newprojects(chat_id: str):
    from modules.database import _conn
    with _conn() as con:
        con.row_factory = __import__('sqlite3').Row
        rows = con.execute("""
            SELECT p.*, s.score, s.label
            FROM discovered_projects p
            LEFT JOIN project_scores s ON s.id = (
                SELECT id FROM project_scores WHERE project_id = p.id
                ORDER BY scored_at DESC LIMIT 1
            )
            WHERE p.discovered_at >= datetime('now', '-48 hours')
            ORDER BY s.score DESC NULLS LAST
        """).fetchall()

    if not rows:
        send_message("No new projects discovered in the last 48 hours.", chat_id=chat_id)
        return

    msg = f"🆕 <b>New Projects (Last 48h)</b> — {len(rows)} found\n\n"
    for p in rows:
        score = p["score"] or "?"
        msg += f"• <b>{p['name']}</b> — {score}/10 | @{p['mentioned_by'] or '?'}\n"
    send_message(msg, chat_id=chat_id)


def _cmd_project(chat_id: str, name: str):
    from modules.database import _conn
    import sqlite3
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT p.*, s.score, s.label, s.breakdown
            FROM discovered_projects p
            LEFT JOIN project_scores s ON s.id = (
                SELECT id FROM project_scores WHERE project_id = p.id
                ORDER BY scored_at DESC LIMIT 1
            )
            WHERE p.name LIKE ?
        """, (f"%{name}%",)).fetchone()

    if not row:
        send_message(f"Project '{name}' not found. Try /newprojects to see what's tracked.", chat_id=chat_id)
        return

    funding = row["funding_usd"] or 0
    f_str = f"${funding/1e6:.0f}M" if funding >= 1e6 else "Not found"

    try:
        breakdown = json.loads(row["breakdown"] or "{}")
        bd_str = "\n".join(f"  {k}: {v}" for k, v in breakdown.items())
    except Exception:
        bd_str = "N/A"

    msg = (
        f"🔍 <b>{row['name']}</b>\n\n"
        f"Score: {row['score'] or '?'}/10 — {row['label'] or '?'}\n"
        f"Category: {row['category'] or '?'}\n"
        f"Funding: {f_str}\n"
        f"Investors: {row['investors'] or 'Unknown'}\n"
        f"Token live: {'Yes ❌' if row['has_token'] else 'No ✅'}\n"
        f"Testnet: {'Active ✅' if row['testnet_active'] else 'Not detected'}\n"
        f"Mentioned by: @{row['mentioned_by'] or '?'}\n"
        f"Website: {row['website'] or 'Unknown'}\n\n"
        f"<b>Score breakdown:</b>\n{bd_str}\n\n"
        f"Discovered: {row['discovered_at'][:10]}"
    )
    if row["tweet_url"]:
        msg += f"\n<a href='{row['tweet_url']}'>📎 Source tweet</a>"

    send_message(msg, chat_id=chat_id)


def _cmd_watchlist(chat_id: str):
    from modules.database import _conn
    import sqlite3
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM watched_accounts ORDER BY tier, handle"
        ).fetchall()

    if not rows:
        send_message("No accounts in watchlist yet.", chat_id=chat_id)
        return

    msg = "👀 <b>Monitored Accounts</b>\n\n"
    for r in rows:
        tier_str = "⭐ Tier 1" if r["tier"] == 1 else "Tier 2"
        msg += f"@{r['handle']} — {tier_str}\n<i>{r['notes'] or ''}</i>\n\n"
    send_message(msg, chat_id=chat_id)


def _cmd_status(chat_id: str):
    from modules.database import get_all_projects, alerts_today
    projects = get_all_projects()
    today = alerts_today()
    send_message(
        f"📊 <b>Alpha Hunter Status</b>\n\n"
        f"🟢 Running\n"
        f"Projects tracked: {len(projects)}\n"
        f"Alerts today: {today}/{settings.MAX_ALERTS_PER_DAY}\n"
        f"Scan interval: every {settings.SCAN_INTERVAL_HOURS}h\n"
        f"Genesis threshold: {settings.GENESIS_THRESHOLD}/10",
        chat_id=chat_id
    )


def _cmd_tasks(chat_id: str):
    from modules.database import get_grind_tasks
    tasks = get_grind_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]
    if not pending:
        send_message("✅ No pending tasks! You're all caught up.", chat_id=chat_id)
        return
    msg = "📝 <b>Pending Grind Tasks</b>\n\n"
    for t in pending[:10]:
        msg += f"[{t['id']}] <b>{t['project_name']}</b> ({t['wallet_label']})\n{t['task']}\n\n"
    send_message(msg, chat_id=chat_id)


def _cmd_done(chat_id: str, task_id: str):
    if not task_id.isdigit():
        send_message("Usage: /done <task_id>", chat_id=chat_id)
        return
    from modules.database import complete_task
    complete_task(int(task_id))
    send_message(f"✅ Task {task_id} marked complete! Keep grinding 💪", chat_id=chat_id)


# ── Main command router ────────────────────────────────────────────────────

def _handle_message(text: str, chat_id: str, pipeline_callback=None):
    text = text.strip()
    lower = text.lower()

    if lower.startswith("/start"):
        _cmd_start(chat_id)
    elif lower.startswith("/topalpha"):
        _cmd_topalpha(chat_id)
    elif lower.startswith("/newprojects"):
        _cmd_newprojects(chat_id)
    elif lower.startswith("/project "):
        _cmd_project(chat_id, text[9:].strip())
    elif lower.startswith("/watchlist"):
        _cmd_watchlist(chat_id)
    elif lower.startswith("/status"):
        _cmd_status(chat_id)
    elif lower.startswith("/tasks"):
        _cmd_tasks(chat_id)
    elif lower.startswith("/done "):
        _cmd_done(chat_id, text[6:].strip())
    elif lower.startswith("/research ") or "twitter.com/" in lower or "x.com/" in lower:
        urls = re.findall(r'https?://(?:twitter\.com|x\.com)/\S+', text)
        if urls and pipeline_callback:
            send_message("🔍 On it — researching that tweet...", chat_id=chat_id)
            pipeline_callback(tweet_url=urls[0], chat_id=chat_id)
        else:
            send_message("Usage: /research https://twitter.com/...", chat_id=chat_id)
    elif lower.startswith("/approve "):
        _cmd_approve(chat_id, text[9:].strip())
    elif lower.startswith("/reject "):
        _cmd_reject(chat_id, text[8:].strip())
    elif lower.startswith("/suggestions"):
        _cmd_suggestions(chat_id)
    else:
        send_message(
            "Unknown command. Type /start to see all commands.",
            chat_id=chat_id
        )


def start_polling(pipeline_callback=None):
    """Start Telegram polling in a background thread."""
    global _last_update_id

    def _poll():
        global _last_update_id
        logger.info("Telegram polling started")
        while True:
            try:
                updates = get_updates(offset=_last_update_id + 1)
                for update in updates:
                    _last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if text and chat_id:
                        _handle_message(text, chat_id, pipeline_callback)
            except Exception as exc:
                logger.debug("Polling loop error: %s", exc)
            time.sleep(3)

    thread = threading.Thread(target=_poll, daemon=True)
    thread.start()
    return thread


def _cmd_approve(chat_id: str, handle: str):
    from modules.account_discovery import approve_suggestion
    if not handle:
        send_message("Usage: /approve <handle>", chat_id=chat_id)
        return
    success = approve_suggestion(handle)
    if success:
        send_message(
            f"✅ <b>@{handle}</b> added to watchlist!\n"
            f"They'll be monitored from the next scan cycle.",
            chat_id=chat_id
        )
    else:
        send_message(f"❌ Could not add @{handle}. May already be in watchlist.", chat_id=chat_id)


def _cmd_reject(chat_id: str, handle: str):
    from modules.account_discovery import reject_suggestion
    if not handle:
        send_message("Usage: /reject <handle>", chat_id=chat_id)
        return
    reject_suggestion(handle)
    send_message(f"❌ @{handle} rejected and won't be suggested again.", chat_id=chat_id)


def _cmd_suggestions(chat_id: str):
    """Show all pending account suggestions."""
    with __import__('modules.database', fromlist=['_conn']).database._conn() as con:
        con.row_factory = __import__('sqlite3').Row
        try:
            rows = con.execute("""
                SELECT * FROM account_suggestions
                WHERE status='pending'
                ORDER BY score DESC
            """).fetchall()
        except Exception:
            send_message("No suggestions yet. Run a discovery cycle first.", chat_id=chat_id)
            return

    if not rows:
        send_message("No pending suggestions right now.", chat_id=chat_id)
        return

    msg = f"🔍 <b>Pending Account Suggestions</b> ({len(rows)})\n\n"
    for r in rows:
        msg += (f"<b>@{r['handle']}</b> — {r['score']}/10 {r['label']}\n"
                f"  Followers: {r['followers']:,}\n"
                f"  /approve {r['handle']}  |  /reject {r['handle']}\n\n")
    send_message(msg, chat_id=chat_id)
