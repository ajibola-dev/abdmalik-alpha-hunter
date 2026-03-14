"""
modules/project_extractor.py
Extracts project names from tweet text.

v0.2 changes:
  - Tweet deduplication moved to DB (is_seen_tweet now delegates to database.py)
    → in-memory _seen_tweet_hashes set removed; survives restarts
  - MAX_CANDIDATES_PER_TWEET reads from settings (was hardcoded to 8)
  - Structured log context (scan_id, tweet_id) passed through
  - _NOISE_WORDS expanded with more common false-positive sources

Architecture note:
  Staged extraction was already implemented by the previous engineer:
    Stage 1 — candidate extraction (regex: list items, phrases, tickers, caps)
    Stage 2 — noise word filtering (_NOISE_WORDS set)
    Stage 3 — dedup + length guard
  v0.2 does not redesign this; it wires the dedup to DB and adds
  settings-driven limits.
"""
import re
import logging
import hashlib

from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

_ALPHA_SIGNALS = [
    "going deep on", "been building on", "grinding",
    "testnet", "airdrop", "been using", "conviction",
    "positioning", "early on", "been on", "no token yet",
    "pre-tge", "pre tge", "accumulating", "farming",
    "node", "validator", "going all in", "bullish on",
    "watching", "next big", "underrated", "hidden gem",
    "sleep on", "don't sleep", "contribute",
    "participation", "eligibility",
]

# Comprehensive noise word list — real-world crypto tweet vocabulary
_NOISE_WORDS = {
    # Common English
    "the", "this", "that", "with", "from", "have", "been", "will",
    "just", "some", "they", "them", "their", "what", "when", "where",
    "would", "could", "should", "about", "after", "before", "during",
    "while", "still", "also", "then", "than", "only", "here", "there",
    "over", "into", "onto", "upon", "even", "back", "down", "again",
    "very", "much", "more", "most", "your", "mine", "ours", "each",
    "both", "such", "same", "like", "make", "take", "come", "know",
    "think", "look", "want", "give", "tell", "keep", "hold", "feel",
    # Crypto generic terms (not project names)
    "bitcoin", "ethereum", "solana", "polygon", "avalanche",
    "crypto", "blockchain", "web3", "defi", "nft", "dao",
    "token", "airdrop", "testnet", "mainnet", "devnet",
    "chain", "network", "protocol", "platform", "ecosystem",
    "wallet", "address", "transaction", "contract", "dapp",
    "liquidity", "staking", "yield", "farming", "bridge",
    "layer", "rollup", "snapshot", "whitelist", "allowlist",
    "season", "points", "rewards", "incentive", "grant",
    # Platforms / apps
    "twitter", "telegram", "discord", "github", "youtube",
    "medium", "substack", "mirror", "notion", "google",
    "chrome", "firefox", "metamask", "ledger", "trezor",
    # Time / calendar words (often capitalised in tweets)
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march",
    "april", "june", "july", "august", "september", "october",
    "november", "december", "today", "tomorrow", "yesterday",
    "week", "month", "year", "quarter", "daily", "weekly",
    # Crypto-Twitter slang / non-project proper nouns
    "alpha", "based", "chad", "lfg", "wagmi", "ngmi", "gm", "gn",
    "dyor", "nfa", "iykyk", "probably", "nothing", "variational",
    "extended", "basically", "literally", "actually", "tbh",
    # Common adjectives / qualifiers that appear capitalised
    "good", "great", "real", "true", "free", "new", "big", "next",
    "best", "first", "last", "top", "high", "low", "full", "early",
    "late", "long", "short", "fast", "slow", "hard", "easy", "deep",
    "massive", "huge", "small", "tiny", "quick", "important",
    "incredible", "amazing", "insane", "wild", "bullish", "bearish",
    # Generic words that appear as false positives in logs
    "allah", "god", "lord", "jesus", "nothing", "something",
    "everyone", "someone", "anyone", "noone", "people", "team",
    "community", "company", "project", "startup", "product",
    # Countries / regions (often capitalised)
    "america", "europe", "asia", "africa", "china", "india",
    "korea", "japan", "russia", "france", "germany", "brazil",
}


def _tweet_hash(tweet_url: str, text: str) -> str:
    return hashlib.md5(f"{tweet_url}{text[:100]}".encode()).hexdigest()


def is_seen_tweet(tweet_url: str, text: str) -> bool:
    """
    v0.2: delegates to DB-backed dedup instead of in-memory set.
    The tweet_seen table in database.py persists across restarts.
    """
    h = _tweet_hash(tweet_url, text)
    if db.is_tweet_seen(h):
        return True
    # Extract tweet_id from URL if possible
    import re as _re
    m = _re.search(r'/status/(\d+)', tweet_url)
    tweet_id = m.group(1) if m else ""
    handle = tweet_url.split("twitter.com/")[-1].split("/")[0] if "twitter.com/" in tweet_url else ""
    db.mark_tweet_seen(h, tweet_id=tweet_id, handle=handle)
    return False


def _is_alpha_tweet(text: str) -> bool:
    lower = text.lower()
    return any(signal in lower for signal in _ALPHA_SIGNALS)


def extract_project_names(tweet_text: str,
                           scan_id: str = "",
                           tweet_id: str = "") -> list[str]:
    """
    Extract project names from tweet text.
    Priority order: numbered lists > key phrases > tickers > quoted > capitalised
    v0.2: MAX cap reads from settings.MAX_CANDIDATES_PER_TWEET
    """
    log_ctx = f"[scan={scan_id} tweet={tweet_id}]" if scan_id else ""

    if not _is_alpha_tweet(tweet_text):
        return []

    candidates = []

    # ── Priority 1: Numbered list items ──────────────────────────────────
    list_items = re.findall(
        r'^\s*\d+[\.\)]\s+([A-Z][A-Za-z0-9][A-Za-z0-9\s]{1,28}?)(?:\s*[\(\n]|$)',
        tweet_text, re.MULTILINE
    )
    for item in list_items:
        item = item.strip()
        if item and item.lower() not in _NOISE_WORDS:
            candidates.append(item)

    # ── Priority 2: After conviction phrases ──────────────────────────────
    phrase_patterns = [
        r'(?:deep on|all in on|grinding|building on|bullish on|watching)\s+([A-Z][A-Za-z0-9]{2,20})',
        r'(?:project|protocol)\s+(?:called\s+)?([A-Z][A-Za-z0-9]{2,20})',
        r'(?:been on|early on|positioned on)\s+([A-Z][A-Za-z0-9]{2,20})',
    ]
    for pattern in phrase_patterns:
        for match in re.findall(pattern, tweet_text):
            if match.lower() not in _NOISE_WORDS:
                candidates.append(match)

    # ── Priority 3: $TICKER mentions ─────────────────────────────────────
    tickers = re.findall(r'\$([A-Z]{2,8})\b', tweet_text)
    candidates.extend(tickers)

    # ── Priority 4: Quoted project names ─────────────────────────────────
    quoted = re.findall(r'["\']([A-Z][A-Za-z0-9\s]{2,25})["\']', tweet_text)
    for q in quoted:
        if q.lower() not in _NOISE_WORDS:
            candidates.append(q.strip())

    # ── Priority 5: Capitalised words (last resort — most noisy) ─────────
    if not candidates:
        cap_words = re.findall(
            r'\b([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)\b', tweet_text
        )
        for word in cap_words:
            if len(word) > 3 and word.lower() not in _NOISE_WORDS:
                candidates.append(word)

    # ── Deduplicate and clean ─────────────────────────────────────────────
    seen = set()
    cleaned = []
    for c in candidates:
        c = " ".join(c.split())   # collapse whitespace / newline artifacts
        cl = c.lower()
        if c and len(c) > 2 and cl not in _NOISE_WORDS and cl not in seen:
            seen.add(cl)
            cleaned.append(c)

    cap = settings.MAX_CANDIDATES_PER_TWEET
    if cleaned:
        logger.debug("%s Extracted: %s", log_ctx, cleaned[:cap])

    return cleaned[:cap]


def score_tweet_quality(tweet_text: str, handle: str,
                        account_tier: int = 2) -> float:
    score = 0.0
    lower = tweet_text.lower()

    if account_tier == 1:
        score += 0.3
    elif account_tier == 2:
        score += 0.15

    signal_count = sum(1 for s in _ALPHA_SIGNALS if s in lower)
    score += min(signal_count * 0.1, 0.3)

    if any(w in lower for w in ["conviction", "going all in",
                                  "deep on", "been building"]):
        score += 0.2

    # Numbered list = high quality structured alpha post
    if re.search(r'^\s*\d+[\.\)]', tweet_text, re.MULTILINE):
        score += 0.3

    return min(score, 1.0)
