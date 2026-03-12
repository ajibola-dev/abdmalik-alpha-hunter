"""
modules/project_extractor.py
Extracts project names from tweet text.
Uses pattern matching + known project signals.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Signals that a tweet is talking about a project worth investigating
_ALPHA_SIGNALS = [
    "going deep on", "been building on", "grinding", "testnet",
    "airdrop", "been using", "conviction", "positioning",
    "early on", "been on", "no token yet", "pre-tge", "pre tge",
    "accumulating", "farming", "node", "validator",
    "going all in", "bullish on", "watching", "next big",
    "underrated", "hidden gem", "sleep on", "don't sleep",
    "contribute", "participation", "eligibility",
]

# Words to ignore as project names
_NOISE_WORDS = {
    "the", "this", "that", "with", "from", "have", "been", "will",
    "just", "some", "they", "them", "their", "what", "when", "where",
    "bitcoin", "ethereum", "solana", "crypto", "blockchain", "web3",
    "defi", "nft", "token", "airdrop", "testnet", "mainnet",
    "twitter", "telegram", "discord", "github", "alpha", "based",
    "good", "great", "real", "true", "free", "new", "big", "next",
}


def _is_alpha_tweet(text: str) -> bool:
    """Returns True if tweet likely contains project alpha."""
    lower = text.lower()
    return any(signal in lower for signal in _ALPHA_SIGNALS)


def extract_project_names(tweet_text: str) -> list[str]:
    """
    Extract potential project names from tweet text.
    Looks for:
    - Capitalised words/phrases that look like project names
    - Words after "on", "with", "building", "grinding", "using"
    - Numbered lists (1. ProjectName)
    - $TICKER mentions
    - @mentions that might be project handles
    """
    if not _is_alpha_tweet(tweet_text):
        return []

    candidates = []

    # ── Numbered list items (1. Story Protocol) ───────────────────────────
    list_pattern = re.findall(r'\d+\.\s+([A-Z][A-Za-z0-9\s]{2,30}?)(?:\n|$|,|\()', tweet_text)
    candidates.extend(list_pattern)

    # ── After key phrases ─────────────────────────────────────────────────
    phrase_patterns = [
        r'(?:on|building on|grinding|deep on|bullish on|watching|using|farming)\s+([A-Z][A-Za-z0-9]{2,20})',
        r'(?:project|protocol|chain|network|platform)\s+(?:called\s+)?([A-Z][A-Za-z0-9]{2,20})',
    ]
    for pattern in phrase_patterns:
        matches = re.findall(pattern, tweet_text)
        candidates.extend(matches)

    # ── $TICKER mentions ──────────────────────────────────────────────────
    tickers = re.findall(r'\$([A-Z]{2,8})', tweet_text)
    candidates.extend(tickers)

    # ── Capitalised multi-word names (Story Protocol, Zama Network) ───────
    cap_words = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b', tweet_text)
    for word in cap_words:
        if len(word) > 3 and word.lower() not in _NOISE_WORDS:
            candidates.append(word)

    # ── Clean and deduplicate ─────────────────────────────────────────────
    cleaned = []
    seen = set()
    for c in candidates:
        c = c.strip()
        if (c and len(c) > 2 and
                c.lower() not in _NOISE_WORDS and
                c.lower() not in seen):
            seen.add(c.lower())
            cleaned.append(c)

    if cleaned:
        logger.debug("Extracted from tweet: %s", cleaned)

    return cleaned


def score_tweet_quality(tweet_text: str, handle: str,
                        account_tier: int = 2) -> float:
    """
    Score how likely a tweet contains genuine alpha (0-1).
    Higher = more likely to be a real project mention.
    """
    score = 0.0
    lower = tweet_text.lower()

    # Account tier bonus
    if account_tier == 1:
        score += 0.3
    elif account_tier == 2:
        score += 0.15

    # Alpha signal density
    signal_count = sum(1 for s in _ALPHA_SIGNALS if s in lower)
    score += min(signal_count * 0.1, 0.3)

    # Conviction language
    if any(w in lower for w in ["conviction", "going all in", "deep on", "been building"]):
        score += 0.2

    # Numbered list = structured alpha post
    if re.search(r'\d+\.', tweet_text):
        score += 0.2

    return min(score, 1.0)
