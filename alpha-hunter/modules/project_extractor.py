"""
modules/project_extractor.py
Extracts project names from tweet text.

v0.9.5 — Smart proximity-based extraction:

  Old approach: extract capitalised words → filter by noise list
  Problem: blocklist is infinite, humans post off-topic content

  New approach: a name is only extracted if a SIGNAL WORD appears
  within proximity (same sentence / ±10 words). This mirrors how a
  human reader decides "this tweet is about a project" — the name
  appears in context, not in isolation.

  Extraction priority (unchanged structure, new proximity gate):
    1. Numbered list items — always extracted (structured alpha posts)
    2. Names after conviction phrases — always extracted
    3. $TICKER mentions — always extracted
    4. Quoted names — always extracted
    5. Capitalised words — ONLY if a signal word is nearby
    6. Lowercase names near funding signals — ONLY with proximity

  Signal proximity gate:
    - Splits tweet into sentences first
    - For each sentence, checks if ANY signal word is present
    - Only extracts names from signal-positive sentences
    - Exceptions: numbered lists and explicit conviction phrases
      bypass the gate (they are inherently signal-positive)
"""
import re
import logging
import hashlib

from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

# ── Alpha signals (for tweet-level quality gate) ──────────────────────────
_ALPHA_SIGNALS = [
    "going deep on", "been building on", "grinding",
    "testnet", "airdrop", "been using", "conviction",
    "positioning", "early on", "been on", "no token yet",
    "pre-tge", "pre tge", "accumulating", "farming",
    "node", "validator", "going all in", "bullish on",
    "watching", "next big", "underrated", "hidden gem",
    "sleep on", "don't sleep", "contribute",
    "participation", "eligibility",
    "raised", "seed round", "series a", "series b",
    "ambassador", "backed by", "funded by", "just raised",
    "raised $", "no token", "tokenless", "pre-launch",
    "whitelist", "allowlist", "points program",
    "incentivized", "incentivised", "rewards",
    "built on", "deploying on", "ecosystem",
    "deep dive", "alpha", "gem", "narrative",
    "infrastructure", "protocol", "l1", "l2",
    "zkvm", "zk proof", "fhe", "depin", "restaking",
    "mainnet", "launch", "launching", "live on",
]

# ── Proximity signals (sentence-level gate) ───────────────────────────────
# A name is only extracted from a sentence containing one of these.
# Broader than _ALPHA_SIGNALS — catches more context while still
# filtering out pure commentary sentences.
_PROXIMITY_SIGNALS = [
    # Funding
    "raised", "funding", "funded", "backed", "investors",
    "seed", "series", "round", "million", "$",
    # Technical
    "testnet", "mainnet", "devnet", "node", "validator",
    "protocol", "blockchain", "chain", "layer", "rollup",
    "zk", "zkvm", "fhe", "depin", "restaking", "modular",
    "smart contract", "on-chain", "onchain", "dapp",
    # Intent
    "going all in", "deep on", "conviction", "bullish on",
    "early on", "been on", "grinding", "farming",
    "no token", "pre-tge", "pre tge", "tokenless",
    "airdrop", "whitelist", "allowlist", "eligibility",
    "ambassador", "ecosystem grant",
    # Launch signals
    "launch", "launching", "live", "deploy", "deployed",
    "announced", "just dropped", "went live",
    # Community
    "discord", "telegram", "waitlist", "early access",
]

_NOISE_WORDS = {
    "the", "this", "that", "with", "from", "have", "been", "will",
    "just", "some", "they", "them", "their", "what", "when", "where",
    "would", "could", "should", "about", "after", "before", "during",
    "while", "still", "also", "then", "than", "only", "here", "there",
    "over", "into", "onto", "upon", "even", "back", "down", "again",
    "very", "much", "more", "most", "your", "mine", "ours", "each",
    "both", "such", "same", "like", "make", "take", "come", "know",
    "think", "look", "want", "give", "tell", "keep", "hold", "feel",
    "bitcoin", "ethereum", "solana", "polygon", "avalanche",
    "crypto", "blockchain", "web3", "defi", "nft", "dao",
    "token", "airdrop", "testnet", "mainnet", "devnet",
    "chain", "network", "protocol", "platform", "ecosystem",
    "wallet", "address", "transaction", "contract", "dapp",
    "liquidity", "staking", "yield", "farming", "bridge",
    "layer", "rollup", "snapshot", "whitelist", "allowlist",
    "season", "points", "rewards", "incentive", "grant",
    "twitter", "telegram", "discord", "github", "youtube",
    "medium", "substack", "mirror", "notion", "google",
    "chrome", "firefox", "metamask", "ledger", "trezor",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march",
    "april", "june", "july", "august", "september", "october",
    "november", "december", "today", "tomorrow", "yesterday",
    "week", "month", "year", "quarter", "daily", "weekly",
    "alpha", "based", "chad", "lfg", "wagmi", "ngmi", "gm", "gn",
    "dyor", "nfa", "iykyk", "probably", "nothing", "variational",
    "extended", "basically", "literally", "actually", "tbh",
    "good", "great", "real", "true", "free", "new", "big", "next",
    "best", "first", "last", "top", "high", "low", "full", "early",
    "late", "long", "short", "fast", "slow", "hard", "easy", "deep",
    "massive", "huge", "small", "tiny", "quick", "important",
    "incredible", "amazing", "insane", "wild", "bullish", "bearish",
    "allah", "god", "lord", "jesus", "nothing", "something",
    "everyone", "someone", "anyone", "noone", "people", "team",
    "community", "company", "project", "startup", "product",
    "america", "europe", "asia", "africa", "china", "india",
    "korea", "japan", "russia", "france", "germany", "brazil",
    "iran", "strait", "hormuz", "risk", "return", "those",
    "grind", "sennin", "episode", "copytrade", "onboarded",
    "animoca", "markets", "ripple", "stellar", "cardano",
    "doge", "tron", "monero", "litecoin", "neo",
    "airdropped", "contrary", "privy", "unlike", "instead",
    "however", "therefore", "because", "president", "trump",
    "congress", "senate", "white", "house", "women",
    "international", "recognition", "honor", "privilege",
    "upcoming", "adventure", "ancient", "forgotten", "mystery",
    "library", "prompts", "grandma", "expert", "leading",
    "coming", "wanna", "matches", "tee", "info",
    "potential", "send", "sell", "hodl", "crazy", "contributor",
    "launched", "pivoting", "absolute", "breaking", "projected",
    "opensea", "polymarket", "coinbase", "zora", "base",
    "polygon", "dime", "miled", "milked", "privacy", "users",
    "migrated", "deployed", "shipped", "released", "updated",
    "announced", "confirmed", "revealed", "reported", "shared",
    "mainnet", "testnet", "devnet", "network", "protocol",
    "finance", "capital", "ventures", "labs", "foundation",
    "drop", "human", "humans", "those", "execute", "sapien",
    "jack", "deputy", "director", "arkin", "group",
}


def _tweet_hash(tweet_url: str, text: str) -> str:
    return hashlib.md5(f"{tweet_url}{text[:100]}".encode()).hexdigest()


def is_seen_tweet(tweet_url: str, text: str) -> bool:
    h = _tweet_hash(tweet_url, text)
    if db.is_tweet_seen(h):
        return True
    import re as _re
    m = _re.search(r'/status/(\d+)', tweet_url)
    tweet_id = m.group(1) if m else ""
    handle = tweet_url.split("twitter.com/")[-1].split("/")[0] \
        if "twitter.com/" in tweet_url else ""
    db.mark_tweet_seen(h, tweet_id=tweet_id, handle=handle)
    return False


def _is_alpha_tweet(text: str) -> bool:
    """Tweet-level gate — does this tweet contain ANY alpha signal?"""
    lower = text.lower()
    return any(signal in lower for signal in _ALPHA_SIGNALS)


def _sentence_has_signal(sentence: str) -> bool:
    """
    Sentence-level proximity gate.
    Returns True if the sentence contains at least one proximity signal.
    This is the core of smart extraction — names are only pulled from
    sentences that are themselves about crypto/projects/funding.
    """
    lower = sentence.lower()
    return any(sig in lower for sig in _PROXIMITY_SIGNALS)


def _split_sentences(text: str) -> list[str]:
    """
    Split tweet into sentences/clauses for proximity checking.
    Handles newlines, periods, and common list separators.
    """
    # Split on newlines, periods followed by space, and common separators
    parts = re.split(r'[\n\r]+|(?<=[.!?])\s+|(?<=\n)-\s*', text)
    # Also split numbered list items into their own "sentences"
    expanded = []
    for part in parts:
        sub = re.split(r'(?:^|\s)(?:\d+[.)]\s+)', part)
        expanded.extend(sub)
    return [p.strip() for p in expanded if p.strip()]


def extract_project_names(tweet_text: str,
                           scan_id: str = "",
                           tweet_id: str = "") -> list[str]:
    """
    Smart proximity-based extraction.
    Names are only extracted when signal words appear in the same
    sentence/clause — not from off-topic sentences in the same tweet.
    """
    log_ctx = f"[scan={scan_id} tweet={tweet_id}]" if scan_id else ""

    if not _is_alpha_tweet(tweet_text):
        return []

    candidates = []
    sentences = _split_sentences(tweet_text)

    # ── Priority 1: Numbered list items ──────────────────────────────────
    # Numbered lists are inherently structured alpha — bypass proximity gate
    list_items = re.findall(
        r'^\s*\d+[\.\)]\s+([A-Z][A-Za-z0-9][A-Za-z0-9\s]{1,28}?)(?:\s*[\(\n]|$)',
        tweet_text, re.MULTILINE
    )
    for item in list_items:
        item = item.strip()
        if item and item.lower() not in _NOISE_WORDS:
            candidates.append(item)

    # ── Priority 2: Conviction phrases — bypass proximity gate ───────────
    phrase_patterns = [
        r'(?:deep on|all in on|grinding|building on|bullish on|watching)\s+([A-Z][A-Za-z0-9]{2,20})',
        r'(?:project|protocol)\s+(?:called\s+)?([A-Z][A-Za-z0-9]{2,20})',
        r'(?:been on|early on|positioned on)\s+([A-Z][A-Za-z0-9]{2,20})',
    ]
    for pattern in phrase_patterns:
        for match in re.findall(pattern, tweet_text):
            if match.lower() not in _NOISE_WORDS:
                candidates.append(match)

    # ── Priority 3: $TICKER — bypass proximity gate ───────────────────────
    tickers = re.findall(r'\$([A-Z]{2,8})\b', tweet_text)
    candidates.extend(tickers)

    # ── Priority 4: Quoted names — bypass proximity gate ─────────────────
    quoted = re.findall(r'["\']([A-Z][A-Za-z0-9\s]{2,25})["\']', tweet_text)
    for q in quoted:
        if q.lower() not in _NOISE_WORDS:
            candidates.append(q.strip())

    # ── Priority 5: Capitalised words — ONLY from signal sentences ────────
    for sentence in sentences:
        if not _sentence_has_signal(sentence):
            continue  # Skip sentences with no crypto/funding context

        cap_words = re.findall(
            r'\b([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)\b', sentence
        )
        for word in cap_words:
            if len(word) > 3 and word.lower() not in _NOISE_WORDS:
                candidates.append(word)

    # ── Priority 6: Lowercase names near funding signals ──────────────────
    # Only from signal-positive sentences
    for sentence in sentences:
        if not _sentence_has_signal(sentence):
            continue

        funding_patterns = [
            r'(?:raised?|funded|backed)\s+(?:\$[\d\.]+[mk]?\s+)?(?:in\s+)?(?:a\s+)?(?:seed|series\s+[ab]|pre-?seed)?\s*(?:round)?\s+(?:by\s+)?([a-z][a-z0-9]{4,20})',
            r'(?:ambassador|partnered|building)\s+(?:for|with)\s+([a-z][a-z0-9]{4,20})',
            r'([a-z][a-z0-9]{4,20})\s+(?:raised|launched|mainnet|testnet|airdrop)',
            r'(?:check\s+out|early\s+on|grind(?:ing)?)\s+([a-z][a-z0-9]{4,20})',
        ]
        for pattern in funding_patterns:
            for match in re.findall(pattern, sentence.lower()):
                if (len(match) >= 5 and match not in _NOISE_WORDS
                        and not match.isdigit()):
                    candidates.append(match)

    # ── Trailing suffix strip ─────────────────────────────────────────────
    _SUFFIX_NOISE = {
        "mainnet", "testnet", "devnet", "network", "protocol",
        "finance", "capital", "ventures", "labs", "foundation",
        "users", "rewards", "points", "season", "airdrop",
    }
    stripped = []
    for c in candidates:
        parts = c.split()
        if len(parts) > 1 and parts[-1].lower() in _SUFFIX_NOISE:
            c = " ".join(parts[:-1])
        stripped.append(c)
    candidates = stripped

    # ── Deduplicate and clean ─────────────────────────────────────────────
    seen = set()
    cleaned = []
    for c in candidates:
        c = " ".join(c.split())
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
    """
    Tweet quality score — used as a pre-filter gate.
    Content-driven, not account-driven.
    """
    score = 0.0
    lower = tweet_text.lower()

    # Tier still gives a small base boost — but content drives score
    if account_tier == 1:
        score += 0.15
    elif account_tier == 2:
        score += 0.10

    signal_count = sum(1 for s in _ALPHA_SIGNALS if s in lower)
    score += min(signal_count * 0.1, 0.4)

    if any(w in lower for w in ["conviction", "going all in",
                                  "deep on", "been building",
                                  "raised", "seed round", "backed by"]):
        score += 0.2

    # Numbered list = high quality structured post
    if re.search(r'^\s*\d+[\.\)]', tweet_text, re.MULTILINE):
        score += 0.3

    return min(score, 1.0)
