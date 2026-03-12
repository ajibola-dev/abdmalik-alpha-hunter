"""
modules/account_discovery.py
Network-based account discovery.

How it works:
- For every trusted account we monitor, we scrape who they interact with
  (retweets, replies, mentions) via Nitter
- We score each discovered account using quality signals
- Accounts that appear in multiple trusted networks get a higher score
- High-scoring accounts are suggested to you via Telegram for approval
- You approve/reject via /approve or /reject commands
- Approved accounts are automatically added to the watchlist

Network signal strength:
  Zun retweets X              → +3 points
  Zun replies to X            → +2 points  
  MztaCat also mentions X     → +2 points (cross-network confirmation)
  X has < 20K followers       → +2 points (quiet = early)
  X posts technical content   → +2 points
  X has < 5K followers        → +1 bonus (very quiet)
  X appears in 3+ networks    → +3 bonus (strong consensus)

Threshold for suggestion: 5/10
"""
import logging
import re
import time
import json
import random
import requests
from bs4 import BeautifulSoup
from config.settings import settings
from modules.database import _conn

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(settings.REQUEST_HEADERS)

# In-memory store of pending account suggestions
_pending_suggestions: dict[str, dict] = {}


def _get(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, timeout=settings.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt == retries - 1:
                logger.debug("GET %s failed: %s", url, exc)
            time.sleep(2 ** attempt)
    return None


def _nitter(path: str) -> str:
    instance = random.choice(settings.NITTER_INSTANCES)
    return f"{instance}{path}"


# ── Scrape interactions from a Nitter profile ─────────────────────────────

def scrape_interactions(handle: str, max_items: int = 30) -> list[str]:
    """
    Scrape handles that a given account has interacted with recently.
    Returns list of handles found in retweets and replies.
    """
    found_handles = []

    url = _nitter(f"/{handle}")
    resp = _get(url)
    if resp is None:
        logger.warning("Could not fetch profile for @%s", handle)
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Retweet sources ───────────────────────────────────────────────
        retweet_markers = soup.select(".retweet-header, .tweet-header")
        for marker in retweet_markers:
            text = marker.get_text()
            match = re.search(r'@([A-Za-z0-9_]{1,50})', text)
            if match:
                found_handles.append(match.group(1))

        # ── Reply targets (tweets starting with @handle) ──────────────────
        tweet_contents = soup.select(".tweet-content")
        for tweet in tweet_contents[:max_items]:
            text = tweet.get_text(strip=True)
            if text.startswith("@"):
                match = re.match(r'@([A-Za-z0-9_]{1,50})', text)
                if match:
                    found_handles.append(match.group(1))

        # ── Any @mentions in tweets ───────────────────────────────────────
        all_mentions = re.findall(r'@([A-Za-z0-9_]{4,50})', resp.text)
        found_handles.extend(all_mentions[:20])

    except Exception as exc:
        logger.debug("Interaction scrape error for @%s: %s", handle, exc)

    # Clean and deduplicate
    cleaned = list({h.lower(): h for h in found_handles
                    if h.lower() != handle.lower()}.values())

    logger.info("@%s interacts with %d accounts", handle, len(cleaned))
    return cleaned


def scrape_follower_count(handle: str) -> int:
    """Scrape follower count from Nitter profile."""
    url = _nitter(f"/{handle}")
    resp = _get(url)
    if resp is None:
        return -1
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        # Nitter shows stats in .profile-stat-num
        stats = soup.select(".profile-stat-num")
        if len(stats) >= 2:
            # Order is usually: tweets, following, followers, likes
            for stat in stats:
                parent = stat.parent
                if parent and "follower" in parent.get_text().lower():
                    count_text = stat.get_text().strip().replace(",", "")
                    if "K" in count_text:
                        return int(float(count_text.replace("K", "")) * 1000)
                    elif "M" in count_text:
                        return int(float(count_text.replace("M", "")) * 1_000_000)
                    elif count_text.isdigit():
                        return int(count_text)
    except Exception as exc:
        logger.debug("Follower count error for @%s: %s", handle, exc)
    return -1


def scrape_recent_content(handle: str) -> str:
    """Get recent tweet text to check if account posts technical content."""
    url = _nitter(f"/{handle}")
    resp = _get(url)
    if resp is None:
        return ""
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        tweets = soup.select(".tweet-content")
        return " ".join(t.get_text(strip=True) for t in tweets[:10])
    except Exception:
        return ""


# ── Account quality scoring ───────────────────────────────────────────────

_TECHNICAL_SIGNALS = [
    "testnet", "mainnet", "funding", "raised", "paradigm", "a16z",
    "zkp", "zk", "fhe", "depin", "validator", "node", "conviction",
    "airdrop", "eligibility", "farming", "grind", "infrastructure",
    "protocol", "l1", "l2", "rollup", "sequencer", "prover",
]

_NOISE_SIGNALS = [
    "giveaway", "win", "retweet", "follow to win", "nft drop",
    "100x", "moon", "pump", "buy now", "price target", "signal",
    "sponsored", "paid promotion", "referral", "ref link",
]


def score_account(handle: str, followers: int,
                  recent_content: str, network_score: float) -> dict:
    """
    Score a discovered account 0-10.
    Returns score + breakdown.
    """
    score = 0.0
    breakdown = {}

    # ── Follower count (quiet = early = valuable) ─────────────────────────
    if 0 < followers <= 2_000:
        follower_pts = 3.0
        follower_label = "very quiet (<2K)"
    elif followers <= 10_000:
        follower_pts = 2.5
        follower_label = "quiet (2K-10K)"
    elif followers <= 20_000:
        follower_pts = 2.0
        follower_label = "small (10K-20K)"
    elif followers <= 50_000:
        follower_pts = 1.0
        follower_label = "medium (20K-50K)"
    elif followers <= 100_000:
        follower_pts = 0.5
        follower_label = "large (50K-100K)"
    else:
        follower_pts = 0.0
        follower_label = "too large (100K+)"
    score += follower_pts
    breakdown["followers"] = f"{follower_pts} ({follower_label})"

    # ── Content quality ───────────────────────────────────────────────────
    content_lower = recent_content.lower()
    tech_count = sum(1 for s in _TECHNICAL_SIGNALS if s in content_lower)
    noise_count = sum(1 for s in _NOISE_SIGNALS if s in content_lower)

    tech_pts = min(tech_count * 0.4, 3.0)
    noise_penalty = min(noise_count * 0.5, 2.0)
    content_pts = max(tech_pts - noise_penalty, 0)
    score += content_pts
    breakdown["content"] = f"{content_pts:.1f} (tech:{tech_count} noise:{noise_count})"

    # ── Network score (how many trusted accounts interact with them) ───────
    net_pts = min(network_score, 4.0)
    score += net_pts
    breakdown["network"] = f"{net_pts:.1f}"

    score = min(round(score, 1), 10.0)

    if score >= 7:
        label = "🔥 Strong add"
    elif score >= 5:
        label = "👀 Worth watching"
    elif score >= 3:
        label = "🌱 Possible"
    else:
        label = "❄️ Skip"

    return {
        "handle": handle,
        "score": score,
        "label": label,
        "followers": followers,
        "breakdown": breakdown,
    }


# ── Main discovery pipeline ───────────────────────────────────────────────

def discover_accounts(watchlist: list[dict]) -> list[dict]:
    """
    Full network discovery run.
    1. Scrape interactions of all trusted accounts
    2. Build network overlap map
    3. Score each discovered account
    4. Return candidates above threshold
    """
    # Only run discovery from trusted tier-1 accounts
    seed_accounts = [a for a in watchlist if a.get("trusted")]
    if not seed_accounts:
        logger.warning("No trusted accounts to discover from")
        return []

    logger.info("Running network discovery from %d seed accounts", len(seed_accounts))

    # ── Build network overlap map ─────────────────────────────────────────
    # {handle: {source_account: interaction_score}}
    network_map: dict[str, dict] = {}

    for account in seed_accounts:
        handle = account["handle"]
        tier = account.get("tier", 2)
        weight = 3.0 if tier == 1 else 1.5

        interactions = scrape_interactions(handle)
        time.sleep(3)  # Be polite to Nitter

        for interacted_handle in interactions:
            if interacted_handle not in network_map:
                network_map[interacted_handle] = {}
            # Add weighted interaction score
            existing = network_map[interacted_handle].get(handle, 0)
            network_map[interacted_handle][handle] = existing + weight

    # Get existing watchlist handles to avoid re-suggesting
    existing_handles = {a["handle"].lower() for a in watchlist}

    # ── Score each discovered account ─────────────────────────────────────
    candidates = []
    processed = set()

    # Sort by total network score descending
    sorted_accounts = sorted(
        network_map.items(),
        key=lambda x: sum(x[1].values()),
        reverse=True
    )

    for discovered_handle, sources in sorted_accounts[:30]:  # Top 30 only
        if discovered_handle.lower() in existing_handles:
            continue
        if discovered_handle.lower() in processed:
            continue
        processed.add(discovered_handle.lower())

        network_score = sum(sources.values())
        cross_network = len(sources) >= 2  # Appears in multiple networks

        if cross_network:
            network_score += 3.0  # Bonus for consensus

        logger.info("Checking @%s (network score: %.1f, sources: %s)",
                    discovered_handle, network_score, list(sources.keys()))

        # Scrape their profile
        followers = scrape_follower_count(discovered_handle)
        time.sleep(2)
        content = scrape_recent_content(discovered_handle)
        time.sleep(2)

        result = score_account(
            handle=discovered_handle,
            followers=followers,
            recent_content=content,
            network_score=network_score,
        )
        result["sources"] = sources
        result["cross_network"] = cross_network

        if result["score"] >= 5.0:
            candidates.append(result)
            logger.info("Candidate found: @%s — %.1f/10 %s",
                        discovered_handle, result["score"], result["label"])

    logger.info("Discovery complete — %d candidates found", len(candidates))
    return candidates


def save_pending_suggestion(candidate: dict):
    """Save a discovered account as pending approval."""
    _pending_suggestions[candidate["handle"].lower()] = candidate
    # Also persist to DB
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS account_suggestions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                handle      TEXT UNIQUE,
                score       REAL,
                label       TEXT,
                followers   INTEGER,
                breakdown   TEXT,
                sources     TEXT,
                status      TEXT DEFAULT 'pending',
                suggested_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute("""
            INSERT INTO account_suggestions
                (handle, score, label, followers, breakdown, sources)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(handle) DO UPDATE SET
                score=excluded.score,
                status='pending',
                suggested_at=datetime('now')
        """, (
            candidate["handle"],
            candidate["score"],
            candidate["label"],
            candidate["followers"],
            json.dumps(candidate.get("breakdown", {})),
            json.dumps(candidate.get("sources", {})),
        ))


def approve_suggestion(handle: str) -> bool:
    """Approve a suggested account — adds to watchlist.json."""
    handle_lower = handle.lower().lstrip("@")
    with _conn() as con:
        con.execute("""
            UPDATE account_suggestions SET status='approved'
            WHERE lower(handle)=?
        """, (handle_lower,))

    # Add to watchlist.json
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = json.load(f)

        # Check not already there
        existing = [a["handle"].lower() for a in data.get("accounts", [])]
        if handle_lower not in existing:
            data["accounts"].append({
                "handle": handle,
                "name": handle,
                "tier": 2,
                "notes": "Auto-discovered via network analysis",
                "trusted": True,
            })
            with open(settings.WATCHLIST_PATH, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("@%s approved and added to watchlist", handle)
            return True
    except Exception as exc:
        logger.error("Could not update watchlist: %s", exc)
    return False


def reject_suggestion(handle: str):
    """Reject a suggested account."""
    handle_lower = handle.lower().lstrip("@")
    with _conn() as con:
        con.execute("""
            UPDATE account_suggestions SET status='rejected'
            WHERE lower(handle)=?
        """, (handle_lower,))
    logger.info("@%s rejected", handle_lower)


def format_suggestion_message(candidate: dict) -> str:
    """Format a candidate account suggestion for Telegram."""
    sources_str = ", ".join(f"@{s}" for s in candidate.get("sources", {}).keys())
    followers = candidate["followers"]
    f_str = f"{followers:,}" if followers > 0 else "unknown"
    cross = "✅ Multiple networks agree" if candidate.get("cross_network") else ""

    bd = candidate.get("breakdown", {})
    bd_str = "\n".join(f"  {k}: {v}" for k, v in bd.items())

    return (
        f"🔍 <b>New Account Discovered</b>\n\n"
        f"<b>@{candidate['handle']}</b>\n"
        f"Score: {candidate['score']}/10 — {candidate['label']}\n"
        f"Followers: {f_str}\n"
        f"Found via: {sources_str}\n"
        f"{cross}\n\n"
        f"<b>Score breakdown:</b>\n{bd_str}\n\n"
        f"Add to watchlist?\n"
        f"/approve {candidate['handle']}   |   /reject {candidate['handle']}"
    )
