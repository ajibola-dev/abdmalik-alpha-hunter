"""
modules/pipeline.py
Orchestrates the full Alpha Hunter scan cycle:
1. Fetch tweets from watchlist accounts
2. Extract project names
3. Research each project
4. Score it (Zun method)
5. Generate action plan
6. Send Telegram alert if score >= threshold
7. Save everything to DB
"""
import json
import logging
import time
from config.settings import settings
from modules import database as db
from modules.x_monitor import monitor_all_accounts, fetch_tweet_from_url
from modules.project_extractor import extract_project_names, score_tweet_quality
from modules.researcher import research_project
from modules.scorer import score_project
from modules.action_planner import generate_action_plan, format_action_plan_for_telegram
from modules.telegram_bot import send_message

logger = logging.getLogger(__name__)


def _load_watchlist() -> list[dict]:
    """Load accounts from watchlist.json + database."""
    import json
    accounts = []
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = json.load(f)
            accounts = data.get("accounts", [])
    except Exception as exc:
        logger.error("Could not load watchlist: %s", exc)
    return accounts


def process_tweet(tweet: dict, caller_tier: int = 2) -> list[dict]:
    """
    Process a single tweet — extract projects, research, score.
    Returns list of scored project dicts that passed threshold.
    """
    text = tweet.get("text", "")
    handle = tweet.get("handle", "unknown")
    tweet_url = tweet.get("url", "")

    # Dedup — skip tweets already processed this session
    from modules.project_extractor import is_seen_tweet
    if is_seen_tweet(tweet_url, text):
        return []

    # Quality check
    tweet_quality = score_tweet_quality(text, handle, caller_tier)
    if tweet_quality < 0.2:
        return []

    # Extract project names
    project_names = extract_project_names(text)
    if not project_names:
        return []

    logger.info("@%s mentioned %d project(s): %s", handle, len(project_names), project_names)

    results = []
    for name in project_names[:5]:  # Max 5 per tweet
        try:
            # Research
            project = research_project(name, tweet_text=text)
            project["mentioned_by"] = handle
            project["tweet_url"] = tweet_url
            project["tweet_text"] = text[:500]

            # Skip if token live
            if project.get("has_token"):
                logger.info("Skipping %s — token already live", name)
                continue

            # Score
            score_result = score_project(project, caller_tier=caller_tier)

            # Save to DB — store novel_tech as comma-separated string
            project_extras = {k: v for k, v in project.items()
                   if k not in ("name", "mentioned_by", "tweet_url",
                                "tweet_text", "research_notes",
                                "novel_tech", "source_record")}
            # Store novel_tech in description field for persistence
            if project.get("novel_tech"):
                project_extras["description"] = (
                    project_extras.get("description", "") +
                    " [tech:" + ",".join(project["novel_tech"]) + "]"
                )
            project_id = db.upsert_project(
                name=name,
                mentioned_by=handle,
                tweet_url=tweet_url,
                tweet_text=text[:500],
                **project_extras
            )
            db.save_score(
                project_id=project_id,
                score=score_result.score,
                breakdown=json.dumps(score_result.breakdown),
                label=score_result.label,
            )

            results.append({
                "project": project,
                "project_id": project_id,
                "score_result": score_result,
            })

            time.sleep(1)

        except Exception as exc:
            logger.error("Error processing project '%s': %s", name, exc)

    return results


def run_scan_cycle():
    """Full scan: fetch all watchlist tweets, process, alert."""
    logger.info("=" * 60)
    logger.info("🎯 Alpha Hunter Scan Starting")
    logger.info("=" * 60)

    watchlist = _load_watchlist()
    if not watchlist:
        logger.warning("Watchlist is empty!")
        return

    # Sync watchlist to DB
    for acc in watchlist:
        db.upsert_account(
            handle=acc.get("handle", ""),
            name=acc.get("name", ""),
            tier=acc.get("tier", 2),
            trusted=acc.get("trusted", False),
            notes=acc.get("notes", ""),
        )

    # Fetch all tweets
    all_tweets = monitor_all_accounts(watchlist)
    db.log_scan("x_monitor", len(all_tweets))

    genesis_count = 0

    for tweet in all_tweets:
        handle = tweet.get("handle", "")
        # Get tier for this account
        acc = next((a for a in watchlist if a.get("handle") == handle), {})
        tier = acc.get("tier", 2)

        scored_projects = process_tweet(tweet, caller_tier=tier)

        for item in scored_projects:
            project = item["project"]
            project_id = item["project_id"]
            score_result = item["score_result"]

            # Alert if above threshold and not already alerted
            if (score_result.score >= settings.GENESIS_THRESHOLD and
                    not db.already_alerted(project_id) and
                    db.alerts_today() < settings.MAX_ALERTS_PER_DAY):

                action_plan = generate_action_plan(project, score_result)
                message = format_action_plan_for_telegram(project, score_result, action_plan)

                success = send_message(message)
                if success:
                    db.log_alert(project_id, "genesis")
                    genesis_count += 1
                    logger.info("🌟 Genesis alert sent for '%s' (%.1f/10)",
                                project["name"], score_result.score)

                    # Auto-add grind tasks to tracker
                    for i, task in enumerate(action_plan["immediate_tasks"][:3]):
                        db.add_grind_task(
                            project_id=project_id,
                            wallet_label="Wallet 1",
                            wallet_address="",
                            task=task,
                        )

    logger.info("=" * 60)
    logger.info("✅ Scan complete — %d genesis calls", genesis_count)
    logger.info("=" * 60)


def research_tweet_url(tweet_url: str, chat_id: str = None):
    """
    Manual pipeline: research a specific tweet URL forwarded by user.
    Called when user sends /research <url> on Telegram.
    """
    send_message("🔍 Fetching tweet...", chat_id=chat_id)

    from modules.x_monitor import fetch_tweet_from_url
    tweet = fetch_tweet_from_url(tweet_url)

    if not tweet:
        send_message("❌ Could not fetch that tweet. Try pasting the text directly.", chat_id=chat_id)
        return

    send_message(f"📝 Tweet found. Extracting projects...", chat_id=chat_id)

    results = process_tweet(tweet, caller_tier=1)  # Manual = high trust

    if not results:
        send_message("🔍 No new projects found in that tweet, or all projects already have tokens.", chat_id=chat_id)
        return

    for item in results:
        project = item["project"]
        score_result = item["score_result"]
        action_plan = generate_action_plan(project, score_result)
        message = format_action_plan_for_telegram(project, score_result, action_plan)
        send_message(message, chat_id=chat_id)
