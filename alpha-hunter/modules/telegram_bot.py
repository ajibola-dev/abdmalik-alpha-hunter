"""
modules/telegram_bot.py
Telegram interface for Alpha Hunter.

v0.2 fix:
  - FIXED _cmd_suggestions(): broken import pattern removed.
    Was: __import__('modules.database', fromlist=['_conn']).database._conn()
    Now: direct import from modules.database (same as all other commands)

Commands:
  /start          - Help menu
  /status         - Agent health + stats
  /topalpha       - Top 5 scored projects
  /newprojects    - Projects discovered in last 48h
  /project <n>    - Full details on a specific project
  /watchlist      - Show monitored X accounts
  /tasks          - Pending grind tasks
  /done <id>      - Mark task complete
  /research <url> - Research a tweet URL manually
  /suggestions    - Pending account discoveries
  /approve <h>    - Add discovered account
  /reject <h>     - Dismiss suggestion
"""
import logging
import urllib.request
import json
import sqlite3
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
        "/research &lt;tweet_url&gt; — Research a tweet\n"
        "/backfill [N] — Scan last N tweets per account (default 50)\n"
        "/debug — Diagnose pipeline (tweet fetch, quality gate, extraction)\n"
        "/suggestions — Pending account discoveries\n"
        "/approve &lt;handle&gt; — Add discovered account\n"
        "/reject &lt;handle&gt; — Dismiss suggestion\n\n"
        "<i>Or just forward any tweet URL and I'll research it instantly.</i>",
        chat_id=chat_id
    )


def _cmd_topalpha(chat_id: str):
    from modules.database import get_all_projects
    projects = get_all_projects()
    scored = [p for p in projects if p["score"] and p["score"] >= 5]
    scored = sorted(scored, key=lambda x: x["score"] or 0, reverse=True)[:5]

    if not scored:
        send_message("No high-scoring projects yet. Run a scan first.",
                     chat_id=chat_id)
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
        con.row_factory = sqlite3.Row
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
        send_message("No new projects discovered in the last 48 hours.",
                     chat_id=chat_id)
        return

    msg = f"🆕 <b>New Projects (Last 48h)</b> — {len(rows)} found\n\n"
    for p in rows:
        score = p["score"] or "?"
        msg += f"• <b>{p['name']}</b> — {score}/10 | @{p['mentioned_by'] or '?'}\n"
    send_message(msg, chat_id=chat_id)


def _cmd_project(chat_id: str, name: str):
    from modules.database import _conn
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
        send_message(
            f"Project '{name}' not found. Try /newprojects to see what's tracked.",
            chat_id=chat_id
        )
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
        if r["tier"] == 1:
            tier_str = "⭐ Tier 1"
        else:
            tier_str = "Tier 2"
        handle = r["handle"]
        twitter_url = f"https://twitter.com/{handle}"
        msg += (
            f"<b>{r['name'] or handle}</b> — {tier_str}\n"
            f"<a href='{twitter_url}'>@{handle} on X/Twitter</a>\n"
            f"<i>{r['notes'] or ''}</i>\n\n"
        )
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
        send_message("✅ No pending tasks! You're all caught up.",
                     chat_id=chat_id)
        return
    msg = "📝 <b>Pending Grind Tasks</b>\n\n"
    for t in pending[:10]:
        msg += (f"[{t['id']}] <b>{t['project_name']}</b> "
                f"({t['wallet_label']})\n{t['task']}\n\n")
    send_message(msg, chat_id=chat_id)


def _cmd_done(chat_id: str, task_id: str):
    if not task_id.isdigit():
        send_message("Usage: /done <task_id>", chat_id=chat_id)
        return
    from modules.database import complete_task
    complete_task(int(task_id))
    send_message(f"✅ Task {task_id} marked complete! Keep grinding 💪",
                 chat_id=chat_id)


def _cmd_suggestions(chat_id: str):
    """
    v0.2 FIX: replaced broken __import__ chain with direct DB import.
    The account_suggestions table is now created in init_db() so it always
    exists — no need for ad-hoc CREATE TABLE here.
    """
    from modules.database import _conn
    with _conn() as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("""
                SELECT * FROM account_suggestions
                WHERE status='pending'
                ORDER BY score DESC
            """).fetchall()
        except Exception as exc:
            logger.error("Suggestions query error: %s", exc)
            send_message("No suggestions yet. Run a discovery cycle first.",
                         chat_id=chat_id)
            return

    if not rows:
        send_message("No pending suggestions right now.", chat_id=chat_id)
        return

    msg = f"🔍 <b>Pending Account Suggestions</b> ({len(rows)})\n\n"
    for r in rows:
        followers = r["followers"] or 0
        msg += (
            f"<b>@{r['handle']}</b> — {r['score']}/10 {r['label']}\n"
            f"  Followers: {followers:,}\n"
            f"  /approve {r['handle']}  |  /reject {r['handle']}\n\n"
        )
    send_message(msg, chat_id=chat_id)


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
        send_message(
            f"❌ Could not add @{handle}. May already be in watchlist.",
            chat_id=chat_id
        )


def _cmd_reject(chat_id: str, handle: str):
    from modules.account_discovery import reject_suggestion
    if not handle:
        send_message("Usage: /reject <handle>", chat_id=chat_id)
        return
    reject_suggestion(handle)
    send_message(f"❌ @{handle} rejected and won't be suggested again.",
                 chat_id=chat_id)


def _cmd_backfill(chat_id: str, count: int = 50):
    """Trigger historical backfill from Telegram."""
    send_message(
        f"⏮️ Starting backfill — last {count} tweets per account.\n"
        f"This may take several minutes...",
        chat_id=chat_id
    )
    import threading
    def _run():
        from modules.pipeline import run_backfill_cycle
        run_backfill_cycle(max_tweets=count)
        send_message("✅ Backfill complete! Use /newprojects to see results.",
                     chat_id=chat_id)
    threading.Thread(target=_run, daemon=True).start()


def _cmd_debug(chat_id: str):
    """
    Diagnostic command — shows exactly what the pipeline sees.
    Reports: watchlist accounts, tweet fetch counts, quality gate results,
    extraction results. Helps diagnose silent failures without reading logs.
    """
    from modules.x_monitor import get_user_id, fetch_recent_tweets
    from modules.project_extractor import (
        _is_alpha_tweet, score_tweet_quality, extract_project_names
    )
    from modules.database import _conn
    import sqlite3

    send_message("🔬 Running diagnostics...", chat_id=chat_id)

    # ── 1. Watchlist status ───────────────────────────────────────────────
    from config.settings import settings
    import json
    try:
        with open(settings.WATCHLIST_PATH) as f:
            wl = json.load(f)
        accounts = wl.get("accounts", [])
    except Exception as exc:
        send_message(f"❌ Could not load watchlist: {exc}", chat_id=chat_id)
        return

    msg = f"📋 <b>Watchlist</b>: {len(accounts)} accounts\n"
    for a in accounts:
        msg += f"  @{a['handle']} tier={a['tier']} weight={a.get('weight',1.0)}\n"
    send_message(msg, chat_id=chat_id)

    # ── 2. Per-account tweet fetch + quality gate ─────────────────────────
    for account in accounts:
        handle = account["handle"]
        tier = account.get("tier", 2)

        send_message(f"🔍 Checking @{handle}...", chat_id=chat_id)

        # Resolve user_id
        uid = get_user_id(handle)
        if not uid:
            send_message(f"  ❌ @{handle}: could not resolve user_id — check RAPIDAPI_KEY", chat_id=chat_id)
            continue

        # Fetch tweets (uses since_id — may return 0 if all seen)
        tweets = fetch_recent_tweets(handle, max_results=10)

        if not tweets:
            # Check DB for last seen tweet
            with _conn() as con:
                con.row_factory = sqlite3.Row
                row = con.execute(
                    "SELECT last_tweet_id, last_checked FROM watched_accounts WHERE handle=?",
                    (handle,)
                ).fetchone()
            if row and row["last_tweet_id"]:
                send_message(
                    f"  ℹ️ @{handle}: 0 new tweets (since_id={row['last_tweet_id'][:8]}... "
                    f"last checked {(row['last_checked'] or 'never')[:16]})",
                    chat_id=chat_id
                )
            else:
                send_message(
                    f"  ⚠️ @{handle}: 0 tweets returned and no since_id recorded — "
                    f"API may have returned empty or user_id is wrong",
                    chat_id=chat_id
                )
            continue

        # Quality gate breakdown
        passed_quality = 0
        passed_alpha = 0
        extracted_any = 0
        sample_projects = []

        for t in tweets:
            text = t["text"]
            quality = score_tweet_quality(text, handle, tier)
            is_alpha = _is_alpha_tweet(text)

            if quality >= 0.2:
                passed_quality += 1
            if is_alpha:
                passed_alpha += 1
                names = extract_project_names(text)
                if names:
                    extracted_any += 1
                    sample_projects.extend(names[:3])

        result = (
            f"  ✅ @{handle}: {len(tweets)} tweets fetched\n"
            f"     Quality ≥0.2: {passed_quality}/{len(tweets)}\n"
            f"     Alpha signal: {passed_alpha}/{len(tweets)}\n"
            f"     Had extractions: {extracted_any}/{len(tweets)}\n"
        )
        if sample_projects:
            result += f"     Sample projects: {', '.join(sample_projects[:5])}\n"
        else:
            result += f"     ⚠️ No projects extracted — showing raw tweets:\n"
            for i, t in enumerate(tweets[:3], 1):
                preview = t["text"][:200].replace("<","&lt;").replace(">","&gt;")
                result += f"\n     [{i}] {preview}\n"

        send_message(result, chat_id=chat_id)

    # ── 3. DB summary ─────────────────────────────────────────────────────
    with _conn() as con:
        con.row_factory = sqlite3.Row
        tweets_seen = con.execute("SELECT COUNT(*) FROM tweet_seen").fetchone()[0]
        projects = con.execute("SELECT COUNT(*) FROM discovered_projects").fetchone()[0]
        scores = con.execute("SELECT COUNT(*) FROM project_scores").fetchone()[0]
        token_live = con.execute(
            "SELECT COUNT(*) FROM discovered_projects WHERE has_token=1"
        ).fetchone()[0]
        recent = con.execute("""
            SELECT p.name, p.has_token, p.mentioned_by, s.score, s.label
            FROM discovered_projects p
            LEFT JOIN project_scores s ON s.id = (
                SELECT id FROM project_scores WHERE project_id=p.id
                ORDER BY scored_at DESC LIMIT 1
            )
            ORDER BY p.discovered_at DESC LIMIT 8
        """).fetchall()

    db_msg = (
        f"🗄️ <b>Database</b>\n"
        f"  Tweets seen (dedup): {tweets_seen}\n"
        f"  Projects stored: {projects} ({token_live} token-live, skipped)\n"
        f"  Scores recorded: {scores}\n"
    )
    if recent:
        db_msg += "\n<b>Recent projects:</b>\n"
        for r in recent:
            status = "❌ token live" if r["has_token"] else f"{r['score'] or '?'}/10"
            db_msg += f"  • {r['name']} — {status} via @{r['mentioned_by'] or '?'}\n"

    send_message(db_msg, chat_id=chat_id)


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
            send_message("Usage: /research https://twitter.com/...",
                         chat_id=chat_id)
    elif lower.startswith("/approve "):
        _cmd_approve(chat_id, text[9:].strip())
    elif lower.startswith("/reject "):
        _cmd_reject(chat_id, text[8:].strip())
    elif lower.startswith("/suggestions"):
        _cmd_suggestions(chat_id)
    elif lower.startswith("/debug"):
        _cmd_debug(chat_id)
    elif lower.startswith("/backfill"):
        parts = text.split()
        count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 50
        _cmd_backfill(chat_id, count)
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
