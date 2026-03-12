"""
modules/telegram_bot.py
Handles Telegram:
- Sends genesis call alerts
- Listens for manual tweet forwards (/research <url>)
- Handles /track, /tasks, /status commands
"""
import logging
import urllib.request
import urllib.parse
import json
import threading
import time
from config.settings import settings

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
_last_update_id = 0


def send_message(text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    """Send a message to Telegram."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set")
        return False

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
            logger.error("Telegram error: %s", result)
            return False
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def get_updates(offset: int = 0) -> list[dict]:
    """Poll Telegram for new messages."""
    try:
        url = f"{_BASE}/getUpdates?offset={offset}&timeout=30"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read())
            return data.get("result", [])
    except Exception as exc:
        logger.debug("getUpdates error: %s", exc)
        return []


def _handle_command(text: str, chat_id: str, pipeline_callback=None):
    """Handle incoming Telegram commands."""
    text = text.strip()

    if text.startswith("/start"):
        send_message(
            "👋 <b>Alpha Hunter is active!</b>\n\n"
            "Commands:\n"
            "/research <tweet_url> — Research a project from a tweet\n"
            "/status — Show agent status\n"
            "/projects — List tracked projects\n"
            "/tasks — Show your pending grind tasks\n"
            "/done <task_id> — Mark a task complete\n\n"
            "<i>Or just forward any tweet containing a project mention!</i>",
            chat_id=chat_id
        )

    elif text.startswith("/research") or ("twitter.com" in text) or ("x.com" in text):
        # Extract URL from message
        import re
        urls = re.findall(r'https?://(?:twitter\.com|x\.com)/\S+', text)
        if urls and pipeline_callback:
            send_message(f"🔍 Researching tweet... one moment.", chat_id=chat_id)
            pipeline_callback(tweet_url=urls[0], chat_id=chat_id)
        else:
            send_message("Please include a tweet URL: /research https://twitter.com/...", chat_id=chat_id)

    elif text.startswith("/status"):
        from modules.database import get_all_projects, alerts_today
        projects = get_all_projects()
        today_alerts = alerts_today()
        send_message(
            f"📊 <b>Alpha Hunter Status</b>\n\n"
            f"Projects tracked: {len(projects)}\n"
            f"Alerts sent today: {today_alerts}/{settings.MAX_ALERTS_PER_DAY}\n"
            f"Scan interval: every {settings.SCAN_INTERVAL_HOURS}h\n"
            f"Status: 🟢 Running",
            chat_id=chat_id
        )

    elif text.startswith("/projects"):
        from modules.database import get_all_projects
        projects = get_all_projects()
        if not projects:
            send_message("No projects tracked yet.", chat_id=chat_id)
            return
        msg = "📋 <b>Tracked Projects</b>\n\n"
        for p in projects[:10]:
            score = p["score"] or 0
            msg += f"• <b>{p['name']}</b> — {score}/10\n"
        send_message(msg, chat_id=chat_id)

    elif text.startswith("/tasks"):
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

    elif text.startswith("/done"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            from modules.database import complete_task
            complete_task(int(parts[1]))
            send_message(f"✅ Task {parts[1]} marked complete!", chat_id=chat_id)
        else:
            send_message("Usage: /done <task_id>", chat_id=chat_id)


def start_polling(pipeline_callback=None):
    """Start polling for Telegram messages in a background thread."""
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
                        _handle_command(text, chat_id, pipeline_callback)
            except Exception as exc:
                logger.debug("Polling error: %s", exc)
            time.sleep(3)

    thread = threading.Thread(target=_poll, daemon=True)
    thread.start()
    return thread
