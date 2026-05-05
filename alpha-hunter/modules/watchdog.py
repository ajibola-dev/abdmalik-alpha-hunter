"""
modules/watchdog.py
Alpha Hunter health monitor — v0.6

Runs as a background thread. Checks:
  1. Scan heartbeat   — did a scan complete within 2× SCAN_INTERVAL_HOURS?
  2. Telegram health  — can we reach the Telegram API?
  3. RapidAPI health  — is the Twitter API key still responding?

If any check fails it sends a Telegram alert to your chat.
Checks run every 30 minutes. Alerts are rate-limited to once per 2 hours
per issue type so you don't get spammed during an outage.

Self-fixing scope (what's realistic on Railway):
  - The watchdog cannot restart processes — Railway handles crash recovery.
  - What it CAN do: detect silent failures (stuck loops, bad API keys,
    rate limits) that don't crash the process but stop producing output.
  - On Railway: if the main process dies, Railway restarts it automatically.
    The watchdog covers the case where it's alive but silently broken.

Usage: called from main.py start_watchdog() — runs as a daemon thread.
"""
import logging
import threading
import time
import urllib.request
import json
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Heartbeat registry ─────────────────────────────────────────────────────
# Pipeline stages call record_heartbeat() after each successful scan.
# Watchdog checks that this timestamp is recent enough.
_last_heartbeat: float = time.time()  # initialised to startup time
_heartbeat_lock = threading.Lock()

# Alert rate limiting — tracks last alert time per issue type
_last_alert_sent: dict[str, float] = {}
_ALERT_COOLDOWN = 7200  # 2 hours between repeat alerts for same issue


def record_heartbeat():
    """
    Call this at the end of every successful scan cycle.
    Tells the watchdog the system is alive and working.
    """
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    logger.debug("Watchdog heartbeat recorded")


def _should_alert(issue_key: str) -> bool:
    """Rate-limit alerts — returns True if enough time has passed."""
    last = _last_alert_sent.get(issue_key, 0)
    if time.time() - last > _ALERT_COOLDOWN:
        _last_alert_sent[issue_key] = time.time()
        return True
    return False


def _send_alert(message: str):
    """Send a watchdog alert via Telegram. Bypasses send_message import cycle."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Watchdog alert cannot send — Telegram not configured")
        return
    payload = json.dumps({
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                logger.info("Watchdog alert sent: %s", message[:60])
            else:
                logger.error("Watchdog Telegram error: %s", result)
    except Exception as exc:
        logger.error("Watchdog alert failed: %s", exc)


# ── Health checks ──────────────────────────────────────────────────────────

def _check_scan_heartbeat() -> bool:
    """
    Returns False if no scan has completed within 2× the scan interval.
    Catches silent failures — stuck loops, frozen threads.
    """
    # v1.0: raised from 2x to 3x SCAN_INTERVAL_HOURS.
    # Funding scanner can take 6h+ due to CoinGecko rate limiting.
    # With 4h scan interval, 2x = 8h was too short. 3x = 12h is safe.
    max_silence = settings.SCAN_INTERVAL_HOURS * 3600 * 3
    with _heartbeat_lock:
        age = time.time() - _last_heartbeat
    if age > max_silence:
        logger.warning("Watchdog: no heartbeat in %.0fh", age / 3600)
        return False
    return True


def _check_telegram() -> bool:
    """Ping Telegram getMe — confirms bot token is valid and API is reachable."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return True  # not configured — not a failure
    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False)
    except Exception as exc:
        logger.warning("Watchdog: Telegram check failed: %s", exc)
        return False


def _check_rapidapi() -> bool:
    """
    Light check that RAPIDAPI_KEY is set and the host is reachable.
    Does not burn an API call — just checks DNS/connectivity.
    """
    if not settings.RAPIDAPI_KEY:
        logger.warning("Watchdog: RAPIDAPI_KEY is not set")
        return False
    try:
        req = urllib.request.Request(
            "https://twitter241.p.rapidapi.com/",
            headers={
                "x-rapidapi-host": "twitter241.p.rapidapi.com",
                "x-rapidapi-key": settings.RAPIDAPI_KEY,
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Any response (even 404) means the host is reachable
            return True
    except urllib.error.HTTPError:
        return True   # HTTP error = reachable, just wrong endpoint
    except Exception as exc:
        logger.warning("Watchdog: RapidAPI check failed: %s", exc)
        return False


# ── Main watchdog loop ─────────────────────────────────────────────────────

def _watchdog_loop():
    """Run health checks every 30 minutes."""
    CHECK_INTERVAL = 1800  # 30 minutes

    # Give the system time to complete its first scan before raising alarms
    startup_grace = settings.SCAN_INTERVAL_HOURS * 3600 * 1.5
    logger.info("Watchdog: startup grace period %.0fh", startup_grace / 3600)
    time.sleep(startup_grace)

    logger.info("Watchdog: health checks active (every 30m)")

    while True:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # ── Check 1: scan heartbeat ────────────────────────────────────────
        if not _check_scan_heartbeat():
            if _should_alert("heartbeat"):
                _send_alert(
                    f"⚠️ <b>Alpha Hunter — Silent Failure</b>\n\n"
                    f"No scan has completed in over "
                    f"{settings.SCAN_INTERVAL_HOURS * 2}h.\n"
                    f"The process may be stuck or crashed.\n\n"
                    f"<i>Check Railway logs. Redeploy if needed.</i>\n"
                    f"<i>{now_str}</i>"
                )

        # ── Check 2: Telegram API ──────────────────────────────────────────
        if not _check_telegram():
            if _should_alert("telegram"):
                logger.error("Watchdog: Telegram API unreachable")
                # Can't send Telegram alert if Telegram is down — just log

        # ── Check 3: RapidAPI ─────────────────────────────────────────────
        if not _check_rapidapi():
            if _should_alert("rapidapi"):
                _send_alert(
                    f"⚠️ <b>Alpha Hunter — RapidAPI Issue</b>\n\n"
                    f"Cannot reach the Twitter/X API.\n"
                    f"Scans will not fetch new tweets until resolved.\n\n"
                    f"Check your RAPIDAPI_KEY in Railway environment vars.\n"
                    f"<i>{now_str}</i>"
                )

        time.sleep(CHECK_INTERVAL)


def start_watchdog():
    """Start the watchdog as a background daemon thread."""
    thread = threading.Thread(
        target=_watchdog_loop,
        daemon=True,
        name="watchdog",
    )
    thread.start()
    logger.info("Watchdog started — health checks every 30m")
    return thread
