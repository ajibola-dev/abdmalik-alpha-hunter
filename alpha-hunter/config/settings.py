"""
config/settings.py — Alpha Hunter configuration
v0.2 — All hardcoded constants moved here. Single source of truth.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / ".env", override=False)


class Settings:
    BASE_DIR: Path = _BASE_DIR
    DATA_DIR: Path = _BASE_DIR / "data"
    LOG_DIR: Path = _BASE_DIR / "logs"
    DB_PATH: str = str(_BASE_DIR / "data" / "alpha_hunter.db")
    WATCHLIST_PATH: str = str(_BASE_DIR / "config" / "watchlist.json")

    # ── Telegram ───────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── API Keys ───────────────────────────────────────────────────────────
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    CMC_API_KEY: str = os.getenv("CMC_API_KEY", "")  # optional — fallback token check in funding_scanner
    X_BEARER_TOKEN: str = os.getenv("X_BEARER_TOKEN", "")
    RAPIDAPI_KEY: str = os.getenv("RAPIDAPI_KEY", "")

    # ── Scoring thresholds ─────────────────────────────────────────────────
    # v0.7: lowered default from 7 to 5 to catch "WATCHING" tier (5.0-6.9)
    # miden scored 5.5 and was never alerted — this fixes that.
    # Set GENESIS_THRESHOLD=7 in Railway env vars to restore strict mode.
    GENESIS_THRESHOLD: float = float(os.getenv("GENESIS_THRESHOLD", "5"))
    MAX_ALERTS_PER_DAY: int = int(os.getenv("MAX_ALERTS_PER_DAY", "10"))

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_HOURS: int = int(os.getenv("SCAN_INTERVAL_HOURS", "4"))

    # ── Pipeline limits (v0.2 — previously hardcoded in pipeline.py) ───────
    MAX_PROJECTS_PER_TWEET: int = int(os.getenv("MAX_PROJECTS_PER_TWEET", "5"))
    MAX_PROJECTS_PER_SCAN: int = int(os.getenv("MAX_PROJECTS_PER_SCAN", "30"))
    MAX_CANDIDATES_PER_TWEET: int = int(os.getenv("MAX_CANDIDATES_PER_TWEET", "8"))

    # ── HTTP / request settings (v0.2 — previously hardcoded per-module) ──
    REQUEST_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "20"))
    MAX_SCRAPE_BYTES: int = int(os.getenv("MAX_SCRAPE_BYTES", "500000"))   # 500 KB cap
    CONCURRENT_REQUESTS: int = int(os.getenv("CONCURRENT_REQUESTS", "5"))  # async semaphore
    REQUEST_HEADERS: dict = {
        "User-Agent": "Mozilla/5.0 (compatible; AlphaHunter/1.0)"
    }

    # ── Retry / backoff (v0.2) ─────────────────────────────────────────────
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF_BASE: int = int(os.getenv("RETRY_BACKOFF_BASE", "2"))   # seconds ^ attempt

    # ── Cache TTLs (v0.2) ──────────────────────────────────────────────────
    DEFILLAMA_CACHE_TTL: int = int(os.getenv("DEFILLAMA_CACHE_TTL", "21600"))  # 6 hours
    RESEARCH_CACHE_TTL: int = int(os.getenv("RESEARCH_CACHE_TTL", "86400"))    # 24 hours
    USER_ID_CACHE_TTL: int = int(os.getenv("USER_ID_CACHE_TTL", "86400"))      # 24 hours

    # ── Nitter instances (rotated for reliability) ─────────────────────────
    NITTER_INSTANCES: list = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.1d4.us",
    ]

    # ── Top tier VCs ───────────────────────────────────────────────────────
    TOP_TIER_VCS: list = [
        "paradigm", "a16z", "andreessen horowitz", "polychain",
        "multicoin", "pantera", "sequoia", "coinbase ventures",
        "binance labs", "dragonfly", "electric capital",
        "framework ventures", "spartan", "delphi digital",
    ]

    # ── Novel tech categories ──────────────────────────────────────────────
    NOVEL_TECH_SIGNALS: list = [
        "fhe", "fully homomorphic", "zero knowledge", "zk proof",
        "zkvm", "zkp", "depin", "decentralized physical",
        "ai blockchain", "on-chain ai", "autonomous agent",
        "restaking", "shared security", "modular",
        "intent based", "account abstraction",
    ]

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
