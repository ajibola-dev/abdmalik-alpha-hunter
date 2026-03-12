"""
modules/project_extractor.py
Extracts project names from tweet text.

Fixes applied:
- Expanded noise word list (was extracting "Monday", "Discord", "GMT" etc)
- Tighter capitalised word regex (was too greedy)
- Added seen-tweet deduplication via tweet URL hash
"""
import re
import logging
import hashlib

logger = logging.getLogger(__name__)

# Tweets already processed — prevents reprocessing same tweet each scan
_seen_tweet_hashes: set = set()

_ALPHA_SIGNALS = [
    "going deep on", "been building on", "grinding", "testnet",
    "airdrop", "been using", "conviction", "positioning",
    "early on", "been on", "no token yet", "pre-tge", "pre tge",
    "accumulating", "farming", "node", "validator",
    "going all in", "bullish on", "watching", "next big",
    "underrated", "hidden gem", "sleep on", "don't sleep",
    "contribute", "participation", "eligibility",
]

# Comprehensive noise words — real-world crypto tweet vocabulary
_NOISE_WORDS = {
    # Common English
    "the", "this", "that", "with", "from", "have", "been", "will",
    "just", "some", "they", "them", "their", "what", "when", "where",
    "would", "could", "should", "about", "after", "before", "during",
    "while", "still", "also", "then", "than", "only", "here", "there",
    "over", "into", "onto", "upon", "even", "back", "down", "again",
    # Crypto generic terms
    "bitcoin", "ethereum", "solana", "polygon", "avalanche",
    "crypto", "blockchain", "web3", "defi", "nft", "dao",
    "token", "airdrop", "testnet", "mainnet", "devnet",
    "chain", "network", "protocol", "platform", "ecosystem",
    "wallet", "address", "transaction", "contract", "dapp",
    # Platforms
    "twitter", "telegram", "discord", "github", "youtube",
    "medium", "substack", "mirror", "notion", "google",
    # Time words (very commonly capitalised in tweets)
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march",
    "april", "june", "july", "august", "september", "october",
    "november", "december", "today", "tomorrow", "yesterday",
    # People/pronouns
    "alpha", "based", "chad", "lfg", "wagmi", "ngmi", "gm", "gn",
    # Adjectives commonly capitalised
    "good", "great", "real", "true", "free", "new", "big", "next",
    "best", "first", "last", "top", "high", "low", "full", "early",
    "late", "long", "short", "fast", "slow", "hard", "easy", "deep",
}


def _tweet_hash(tweet_url: str, text: str) -> str:
    return hashlib.md5(f"{tweet_url}{text[:100]}".encode()).hexdigest()


def is_seen_tweet(tweet_url: str, text: str) -> bool:
    h = _tweet_hash(tweet_url, text)
    if h in _seen_tweet_hashes:
        return True
    _seen_tweet_hashes.add(h)
    return False


def _is_alpha_tweet(text: str) -> bool:
    lower = text.lower()
    return any(signal in lower for signal in _ALPHA_SIGNALS)


def extract_project_names(tweet_text: str) -> list[str]:
    """
    Extract project names from tweet text.
    Priority order: numbered lists > key phrases > tickers > capitalised words
    """
    if not _is_alpha_tweet(tweet_text):
        return []

    candidates = []

    # ── Priority 1: Numbered list items ───────────────────────────────────
    # Matches: "1. Story Protocol" or "1. Zama"
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

    # ── Priority 3: $TICKER mentions ──────────────────────────────────────
    tickers = re.findall(r'\$([A-Z]{2,8})\b', tweet_text)
    candidates.extend(tickers)

    # ── Priority 4: Quoted project names ──────────────────────────────────
    quoted = re.findall(r'["\']([A-Z][A-Za-z0-9\s]{2,25})["\']', tweet_text)
    for q in quoted:
        if q.lower() not in _NOISE_WORDS:
            candidates.append(q.strip())

    # ── Priority 5: Capitalised words (most prone to noise — last) ─────────
    # Only if numbered list found nothing — avoids noisy fallback on generic tweets
    if not candidates:
        cap_words = re.findall(r'\b([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)\b', tweet_text)
        for word in cap_words:
            if len(word) > 3 and word.lower() not in _NOISE_WORDS:
                candidates.append(word)

    # ── Deduplicate and clean ──────────────────────────────────────────────
    seen = set()
    cleaned = []
    for c in candidates:
        c = c.strip()
        cl = c.lower()
        if c and len(c) > 2 and cl not in _NOISE_WORDS and cl not in seen:
            seen.add(cl)
            cleaned.append(c)

    if cleaned:
        logger.debug("Extracted: %s", cleaned)

    return cleaned[:8]  # Hard cap at 8 to prevent explosion


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

    if any(w in lower for w in ["conviction", "going all in", "deep on", "been building"]):
        score += 0.2

    # Numbered list = high quality structured alpha post
    if re.search(r'^\s*\d+[\.\)]', tweet_text, re.MULTILINE):
        score += 0.3

    return min(score, 1.0)
