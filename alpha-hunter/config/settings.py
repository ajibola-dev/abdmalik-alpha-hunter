"""
config/settings.py — Alpha Hunter configuration
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

    # ── API Keys (optional — improves research quality) ────────────────────
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    CMC_API_KEY: str = os.getenv("CMC_API_KEY", "")

    # ── Scoring thresholds ─────────────────────────────────────────────────
    GENESIS_THRESHOLD: int = int(os.getenv("GENESIS_THRESHOLD", "7"))
    MAX_ALERTS_PER_DAY: int = int(os.getenv("MAX_ALERTS_PER_DAY", "10"))

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_HOURS: int = int(os.getenv("SCAN_INTERVAL_HOURS", "4"))

    # ── Nitter instances (public, rotated for reliability) ─────────────────
    NITTER_INSTANCES: list = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.1d4.us",
        "https://nitter.tiekoetter.com",
        "https://nitter.rawbit.ninja",
        "https://nitter.unixfox.eu",
        "https://nitter.esmailelbob.xyz",
        "https://nitter.weiler.rocks",
        "https://nitter.sethforprivacy.com",
    ]

    # ── Top tier VCs ───────────────────────────────────────────────────────
    TOP_TIER_VCS: list = [
        "paradigm", "a16z", "andreessen horowitz", "polychain",
        "multicoin", "pantera", "sequoia", "coinbase ventures",
        "binance labs", "dragonfly", "electric capital",
        "framework ventures", "spartan", "delphi digital",
    ]

    # ── Novel tech categories that signal high value ───────────────────────
    NOVEL_TECH_SIGNALS: list = [
        "fhe", "fully homomorphic", "zero knowledge", "zk proof",
        "zkvm", "zkp", "depin", "decentralized physical",
        "ai blockchain", "on-chain ai", "autonomous agent",
        "restaking", "shared security", "modular",
        "intent based", "account abstraction",
    ]

    # ── Request settings ───────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = 20
    REQUEST_HEADERS: dict = {
        "User-Agent": "Mozilla/5.0 (compatible; AlphaHunter/1.0)"
    }

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
