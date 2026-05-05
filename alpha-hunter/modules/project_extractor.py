"""
modules/project_extractor.py — v2.0

Rebuilt from scratch. The old version extracted any capitalised word
near a signal word. That produced "Abacus Global", "Iran", "Risk", etc.

New approach: vertical-aware extraction.
A name is only extracted if:
  1. It appears in a numbered list (structured alpha call), OR
  2. A conviction phrase + project name pattern is detected, OR
  3. The name appears near a SPECIFIC vertical signal in the same sentence

The 4 verticals filter extraction:
  - ZK/L2: "testnet", "deploy", "prover", "rollup", "zkvm"
  - PerpDEX: "perp", "perpetual", "trading", "vault", "points program"
  - Solana DeFi: "solana", "lp", "stake", "jito-style", "yield"
  - Cosmos/DA: "cosmos", "ibc", "stake", "validator", "da layer"

Anything that doesn't fit a vertical gets dropped before research.
"""
import re
import logging
import hashlib
from config.settings import settings
from modules import database as db

logger = logging.getLogger(__name__)

# ── Vertical signal words ─────────────────────────────────────────────────
_VERTICAL_SIGNALS = {
    "zk_l2": [
        "testnet", "zk", "zkvm", "zkp", "zk proof", "rollup", "l2",
        "layer 2", "deploy", "prover", "verifier", "sequencer",
        "starknet", "zkync", "scroll", "linea", "taiko", "miden",
        "fhe", "fully homomorphic", "privacy", "zero knowledge",
        "node program", "validator program",
    ],
    "perpdex": [
        "perp", "perpetual", "perpdex", "hyperliquid", "trading",
        "points program", "volume farm", "vault", "lp vault",
        "trading competition", "market maker", "order book",
        "leverage", "futures", "options protocol",
    ],
    "solana_defi": [
        "solana", "svm", "jito", "jupiter", "meteora", "raydium",
        "marinade", "kamino", "drift", "marginfi", "liquidity pool",
        "lp", "yield farm", "stake", "validator", "liquid staking",
    ],
    "cosmos_da": [
        "cosmos", "ibc", "celestia", "data availability", "da layer",
        "modular", "dymension", "neutron", "osmosis", "atom",
        "validator", "staking", "governance", "interchain",
        "rollapp", "settlement layer",
    ],
    "general_early": [
        "testnet", "mainnet launch", "token launch", "tge",
        "airdrop", "points", "farming", "early access",
        "waitlist", "ambassador", "node operator", "incentivized",
        "no token yet", "pre-tge", "pre tge", "tokenless",
        "backed by", "raised", "seed round", "series a",
        "going all in", "deep on", "conviction",
    ],
}

# All vertical signals flattened for tweet-level quality gate
_ALL_SIGNALS = set(s for signals in _VERTICAL_SIGNALS.values() for s in signals)

# Conviction phrases — always extract names after these
_CONVICTION_PHRASES = [
    r"going all in on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"deep on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"been building on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"grinding\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"conviction on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"early on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"bullish on\s+([A-Za-z][A-Za-z0-9]{2,20})",
    r"farming\s+([A-Za-z][A-Za-z0-9]{2,20})",
]

# Hard noise — never extract these regardless of context
_HARD_NOISE = {
    # Common English words
    "the", "this", "that", "with", "from", "have", "been", "will",
    "just", "some", "they", "them", "their", "what", "when", "where",
    "would", "could", "should", "about", "after", "before", "very",
    # Financial/macro words
    "risk", "return", "management", "expectations", "performing",
    "optimistic", "abacus", "autonomous", "quietly", "market",
    # Generic crypto words
    "token", "airdrop", "testnet", "mainnet", "devnet", "network",
    "protocol", "platform", "ecosystem", "wallet", "chain",
    "farm", "farming", "round", "backed", "beta", "made", "genesis",
    "alpha", "signal", "early", "stage", "launch", "season",
    # VC/investor names
    "animoca", "pantera", "framework", "multicoin", "paradigm",
    "sequoia", "binance", "coinbase", "capital", "ventures", "labs",
    # Names extracted as projects in logs
    "frogy", "grind", "sennin", "episode", "copytrade", "onboarded",
    # Chains already live
    "bitcoin", "ethereum", "solana", "polygon", "avalanche",
    "cardano", "doge", "tron", "ripple", "bnb", "base",
    # Common false positives from logs
    "iran", "strait", "hormuz", "jack", "deputy", "director",
    "abacus", "global", "performing", "beyond", "most", "optimistic",
    "afk", "knx", "hype", "lit", "made", "those",
}


def _tweet_has_vertical_signal(text: str) -> bool:
    """Does this tweet contain ANY signal from our 4 verticals?"""
    lower = text.lower()
    return any(signal in lower for signal in _ALL_SIGNALS)


def _sentence_vertical(sentence: str) -> str | None:
    """Which vertical does this sentence belong to? None if no match."""
    lower = sentence.lower()
    for vertical, signals in _VERTICAL_SIGNALS.items():
        if any(s in lower for s in signals):
            return vertical
    return None


def _split_sentences(text: str) -> list[str]:
    """Split tweet into sentences/clauses."""
    parts = re.split(r'[\n\r]+|(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def _clean_name(name: str) -> str | None:
    """Clean and validate an extracted name."""
    name = name.strip().rstrip('.,;:')
    lower = name.lower()
    if len(name) < 2 or len(name) > 35:
        return None
    if lower in _HARD_NOISE:
        return None
    if name.replace(" ", "").isdigit():
        return None
    if '?' in name or name.lower().startswith('is') and len(name) > 8:
        return None
    return name


def extract_project_names(tweet_text: str,
                           scan_id: str = "",
                           tweet_id: str = "") -> list[str]:
    """
    v2.0: Vertical-aware extraction.
    Only extracts names that are in context of the 4 farming verticals.
    """
    if not _tweet_has_vertical_signal(tweet_text):
        return []

    candidates = []
    seen = set()

    # ── Priority 1: Numbered list items ───────────────────────────────────
    # Zun-style: "1. Fhenix 2. Zama 3. Miden" — always extract
    list_items = re.findall(
        r'^\s*\d+[\.\)]\s+([A-Za-z][A-Za-z0-9][A-Za-z0-9\s]{0,28}?)(?:\s*[\(\n,]|$)',
        tweet_text, re.MULTILINE
    )
    for item in list_items:
        item = item.strip()
        # Quality checks
        if not item or len(item) < 2 or len(item) > 35:
            continue
        if '?' in item:
            continue
        # Must not be all noise words
        words = item.lower().split()
        if all(w in _HARD_NOISE for w in words):
            continue
        # Reject sentence fragments (too many words, no capitals)
        if len(words) > 3 and not any(w[0].isupper() for w in words if len(w) > 2):
            continue
        cleaned = _clean_name(item)
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            candidates.append(cleaned)

    # If numbered list found, return immediately — this is structured alpha
    if candidates:
        result = candidates[:settings.MAX_CANDIDATES_PER_TWEET]
        logger.debug("[%s] Numbered list extraction: %s", scan_id, result)
        return result

    # ── Priority 2: Conviction phrases ────────────────────────────────────
    for pattern in _CONVICTION_PHRASES:
        for match in re.findall(pattern, tweet_text, re.IGNORECASE):
            cleaned = _clean_name(match)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                candidates.append(cleaned)

    # ── Priority 3: $TICKER mentions ─────────────────────────────────────
    tickers = re.findall(r'\$([A-Z]{2,8})\b', tweet_text)
    for t in tickers:
        if t.lower() not in _HARD_NOISE and t.lower() not in seen:
            seen.add(t.lower())
            candidates.append(t)

    # ── Priority 4: Names from vertical-signal sentences only ─────────────
    sentences = _split_sentences(tweet_text)
    for sentence in sentences:
        vertical = _sentence_vertical(sentence)
        if not vertical:
            continue  # Skip sentences with no vertical context

        # Extract capitalised words from vertical-relevant sentences
        cap_words = re.findall(
            r'\b([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)\b', sentence
        )
        for word in cap_words:
            cleaned = _clean_name(word)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                candidates.append(cleaned)

        # Also extract lowercase names near strong signals
        # Pattern: "raised $Xm" or "backed by" or "farming X"
        funding_patterns = [
            r'([a-z][a-z0-9]{3,20})\s+(?:raised|testnet|launched|mainnet)',
            r'(?:farming|grinding|building on)\s+([a-z][a-z0-9]{3,20})',
        ]
        for pattern in funding_patterns:
            for match in re.findall(pattern, sentence.lower()):
                cleaned = _clean_name(match)
                if cleaned and cleaned.lower() not in seen and len(match) >= 4:
                    seen.add(cleaned.lower())
                    candidates.append(cleaned)

    result = candidates[:settings.MAX_CANDIDATES_PER_TWEET]
    if result:
        logger.debug("[%s] Extracted: %s", scan_id, result)
    return result


def score_tweet_quality(tweet_text: str, handle: str,
                        account_tier: int = 2,
                        account_verticals: list = None) -> float:
    """
    v2.0: Quality score based on vertical signal density.
    Higher = more likely to contain actionable farming intel.
    """
    score = 0.0
    lower = tweet_text.lower()

    # Tier base
    score += 0.15 if account_tier == 1 else 0.05

    # Vertical signal count
    signal_count = sum(1 for s in _ALL_SIGNALS if s in lower)
    score += min(signal_count * 0.08, 0.40)

    # Conviction phrase
    if any(p.split(r'\s+')[0].replace('r"', '').replace('\\', '') in lower
           for p in ["going all in", "deep on", "grinding", "conviction"]):
        score += 0.20

    # Numbered list = high quality
    if re.search(r'^\s*\d+[\.\)]', tweet_text, re.MULTILINE):
        score += 0.30

    # Vertical specificity bonus
    if account_verticals:
        for v in account_verticals:
            v_signals = _VERTICAL_SIGNALS.get(v, [])
            if any(s in lower for s in v_signals[:5]):
                score += 0.10
                break

    return min(score, 1.0)
