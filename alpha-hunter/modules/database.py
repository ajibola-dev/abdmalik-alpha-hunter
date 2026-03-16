"""
modules/database.py — SQLite storage for Alpha Hunter
v0.2 changes:
  - Added tweet_seen table          → persistent tweet dedup (survives restarts)
  - Added project_research_cache    → skip re-researching known projects
  - Added structured scan_context   → scan_id, stage tracking for logs
  - Fixed _cmd_suggestions import bug (was in telegram_bot.py, root cause here)
  - All new tables created safely via init_db()

Tables:
  watched_accounts       : X accounts being monitored
  discovered_projects    : projects extracted from tweets
  project_scores         : scoring history
  grind_tracker          : personal task/wallet tracking
  alerts_sent            : dedup alert history
  scan_log               : scan audit trail
  tweet_seen             : [NEW v0.2] persistent tweet dedup
  project_research_cache : [NEW v0.2] skip repeat research within TTL
  account_suggestions    : discovered accounts pending approval
"""
import sqlite3
import logging
import time
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)


def _conn():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")   # safer concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS watched_accounts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            handle       TEXT UNIQUE NOT NULL,
            name         TEXT,
            tier         INTEGER DEFAULT 2,
            trusted      INTEGER DEFAULT 0,
            notes        TEXT,
            added_at     TEXT DEFAULT (datetime('now')),
            last_checked TEXT,
            last_tweet_id TEXT
        );

        CREATE TABLE IF NOT EXISTS discovered_projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT UNIQUE NOT NULL,
            mentioned_by    TEXT,
            tweet_url       TEXT,
            tweet_text      TEXT,
            category        TEXT,
            funding_usd     REAL DEFAULT 0,
            investors       TEXT,
            has_token       INTEGER DEFAULT 0,
            testnet_active  INTEGER DEFAULT 0,
            website         TEXT,
            github          TEXT,
            description     TEXT,
            discovered_at   TEXT DEFAULT (datetime('now')),
            last_updated    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS project_scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER REFERENCES discovered_projects(id),
            score       REAL,
            breakdown   TEXT,
            label       TEXT,
            scored_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS grind_tracker (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER REFERENCES discovered_projects(id),
            wallet_address  TEXT,
            wallet_label    TEXT,
            task            TEXT,
            status          TEXT DEFAULT 'pending',
            notes           TEXT,
            due_date        TEXT,
            completed_at    TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts_sent (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER,
            alert_type  TEXT,
            sent_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT,
            found       INTEGER,
            notes       TEXT,
            scanned_at  TEXT DEFAULT (datetime('now'))
        );

        -- v0.2: persistent tweet deduplication
        -- replaces in-memory _seen_tweet_hashes / _seen_tweet_ids sets
        CREATE TABLE IF NOT EXISTS tweet_seen (
            tweet_hash  TEXT PRIMARY KEY,
            tweet_id    TEXT,
            handle      TEXT,
            seen_at     TEXT DEFAULT (datetime('now'))
        );

        -- v0.2: persistent research cache
        -- prevents re-researching the same project within RESEARCH_CACHE_TTL
        CREATE TABLE IF NOT EXISTS project_research_cache (
            project_name    TEXT PRIMARY KEY,
            result_json     TEXT,
            cached_at       TEXT DEFAULT (datetime('now'))
        );

        -- account discovery suggestions (moved from ad-hoc CREATE in account_discovery.py)
        CREATE TABLE IF NOT EXISTS account_suggestions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            handle       TEXT UNIQUE,
            score        REAL,
            label        TEXT,
            followers    INTEGER,
            breakdown    TEXT,
            sources      TEXT,
            status       TEXT DEFAULT 'pending',
            suggested_at TEXT DEFAULT (datetime('now'))
        );
        """)

        # Indexes for hot query paths
        con.executescript("""
        CREATE INDEX IF NOT EXISTS idx_tweet_seen_handle
            ON tweet_seen(handle);
        CREATE INDEX IF NOT EXISTS idx_alerts_sent_project
            ON alerts_sent(project_id, alert_type);
        CREATE INDEX IF NOT EXISTS idx_scores_project
            ON project_scores(project_id);
        CREATE INDEX IF NOT EXISTS idx_projects_discovered
            ON discovered_projects(discovered_at);
        """)

    logger.info("Database initialised at %s", settings.DB_PATH)


# ── Tweet deduplication (v0.2 — DB-backed) ────────────────────────────────

def is_tweet_seen(tweet_hash: str) -> bool:
    """Return True if this tweet hash has been processed before."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM tweet_seen WHERE tweet_hash=?", (tweet_hash,)
        ).fetchone()
        return row is not None


def mark_tweet_seen(tweet_hash: str, tweet_id: str = "", handle: str = ""):
    """Record a tweet as processed."""
    with _conn() as con:
        con.execute(
            """INSERT OR IGNORE INTO tweet_seen (tweet_hash, tweet_id, handle)
               VALUES (?,?,?)""",
            (tweet_hash, tweet_id, handle)
        )


def cleanup_old_tweets(days: int = 30):
    """Prune tweet_seen entries older than N days to keep DB lean."""
    with _conn() as con:
        con.execute(
            "DELETE FROM tweet_seen WHERE seen_at < datetime('now', ?)",
            (f"-{days} days",)
        )


# ── Research cache (v0.2) ──────────────────────────────────────────────────

def get_research_cache(project_name: str) -> dict | None:
    """
    Return cached research result if within TTL, else None.
    TTL is RESEARCH_CACHE_TTL seconds (default 24h).
    """
    with _conn() as con:
        row = con.execute(
            """SELECT result_json, cached_at FROM project_research_cache
               WHERE project_name=?""",
            (project_name,)
        ).fetchone()
    if not row:
        return None
    cached_at_str, result_json = row[1], row[0]
    try:
        cached_ts = datetime.fromisoformat(cached_at_str).timestamp()
        age = time.time() - cached_ts
        if age < settings.RESEARCH_CACHE_TTL:
            import json
            return json.loads(result_json)
    except Exception:
        pass
    return None


def set_research_cache(project_name: str, result: dict):
    """Persist a research result to the DB cache."""
    import json
    with _conn() as con:
        con.execute(
            """INSERT INTO project_research_cache (project_name, result_json)
               VALUES (?,?)
               ON CONFLICT(project_name) DO UPDATE SET
                   result_json=excluded.result_json,
                   cached_at=datetime('now')""",
            (project_name, json.dumps(result))
        )


# ── Standard CRUD ─────────────────────────────────────────────────────────

def upsert_account(handle: str, name: str = "", tier: int = 2,
                   trusted: bool = False, notes: str = "") -> int:
    with _conn() as con:
        con.execute("""
            INSERT INTO watched_accounts (handle, name, tier, trusted, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                name=excluded.name, tier=excluded.tier,
                trusted=excluded.trusted, notes=excluded.notes
        """, (handle, name, tier, int(trusted), notes))
        row = con.execute(
            "SELECT id FROM watched_accounts WHERE handle=?", (handle,)
        ).fetchone()
        return row[0]


def update_account_last_tweet(handle: str, tweet_id: str):
    """Store the newest tweet_id seen for an account — enables since_id logic."""
    with _conn() as con:
        con.execute(
            """UPDATE watched_accounts
               SET last_tweet_id=?, last_checked=datetime('now')
               WHERE handle=?""",
            (tweet_id, handle)
        )


def get_account_last_tweet_id(handle: str) -> str | None:
    """Retrieve the last seen tweet_id for a handle."""
    with _conn() as con:
        row = con.execute(
            "SELECT last_tweet_id FROM watched_accounts WHERE handle=?",
            (handle,)
        ).fetchone()
        return row[0] if row and row[0] else None


def upsert_project(name: str, mentioned_by: str = "", tweet_url: str = "",
                   tweet_text: str = "", **kwargs) -> int:
    with _conn() as con:
        con.execute("""
            INSERT INTO discovered_projects
                (name, mentioned_by, tweet_url, tweet_text, category,
                 funding_usd, investors, has_token, testnet_active,
                 website, github, description)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                last_updated=datetime('now'),
                funding_usd=MAX(funding_usd, excluded.funding_usd),
                has_token=excluded.has_token,
                testnet_active=excluded.testnet_active
        """, (
            name, mentioned_by, tweet_url, tweet_text,
            kwargs.get("category", ""),
            kwargs.get("funding_usd", 0),
            kwargs.get("investors", ""),
            int(kwargs.get("has_token", False)),
            int(kwargs.get("testnet_active", False)),
            kwargs.get("website", ""),
            kwargs.get("github", ""),
            kwargs.get("description", ""),
        ))
        row = con.execute(
            "SELECT id FROM discovered_projects WHERE name=?", (name,)
        ).fetchone()
        return row[0]


def save_score(project_id: int, score: float, breakdown: str, label: str):
    with _conn() as con:
        con.execute("""
            INSERT INTO project_scores (project_id, score, breakdown, label)
            VALUES (?,?,?,?)
        """, (project_id, score, breakdown, label))


def already_alerted(project_id: int, alert_type: str = "genesis") -> bool:
    with _conn() as con:
        row = con.execute("""
            SELECT id FROM alerts_sent
            WHERE project_id=? AND alert_type=?
        """, (project_id, alert_type)).fetchone()
        return row is not None


def log_alert(project_id: int, alert_type: str = "genesis"):
    with _conn() as con:
        con.execute("""
            INSERT INTO alerts_sent (project_id, alert_type)
            VALUES (?,?)
        """, (project_id, alert_type))


def alerts_today() -> int:
    with _conn() as con:
        row = con.execute("""
            SELECT COUNT(*) FROM alerts_sent
            WHERE date(sent_at) = date('now')
        """).fetchone()
        return row[0] if row else 0


def log_scan(source: str, found: int, notes: str = ""):
    with _conn() as con:
        con.execute("""
            INSERT INTO scan_log (source, found, notes)
            VALUES (?,?,?)
        """, (source, found, notes))


def get_all_projects():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return con.execute("""
            SELECT p.*,
                   s.score, s.label, s.breakdown
            FROM discovered_projects p
            LEFT JOIN project_scores s ON s.id = (
                SELECT id FROM project_scores
                WHERE project_id = p.id
                ORDER BY scored_at DESC LIMIT 1
            )
            ORDER BY s.score DESC NULLS LAST
        """).fetchall()


def get_grind_tasks(project_name: str = None):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if project_name:
            return con.execute("""
                SELECT g.*, p.name as project_name
                FROM grind_tracker g
                JOIN discovered_projects p ON p.id = g.project_id
                WHERE p.name LIKE ?
                ORDER BY g.created_at DESC
            """, (f"%{project_name}%",)).fetchall()
        return con.execute("""
            SELECT g.*, p.name as project_name
            FROM grind_tracker g
            JOIN discovered_projects p ON p.id = g.project_id
            ORDER BY g.status, g.created_at DESC
        """).fetchall()


def add_grind_task(project_id: int, wallet_label: str, wallet_address: str,
                   task: str, due_date: str = "", notes: str = ""):
    with _conn() as con:
        con.execute("""
            INSERT INTO grind_tracker
                (project_id, wallet_label, wallet_address, task, due_date, notes)
            VALUES (?,?,?,?,?,?)
        """, (project_id, wallet_label, wallet_address, task, due_date, notes))


def complete_task(task_id: int):
    with _conn() as con:
        con.execute("""
            UPDATE grind_tracker
            SET status='done', completed_at=datetime('now')
            WHERE id=?
        """, (task_id,))

# ── Probation account management (v0.9.2) ─────────────────────────────────

def get_probation_accounts() -> list[str]:
    """Return handles of all probation accounts."""
    import json as _json
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = _json.load(f)
        return [a["handle"].lower() for a in data.get("accounts", [])
                if a.get("status") == "probation"]
    except Exception:
        return []


def record_cross_mention(handle: str, mentioned_by: str, project_name: str):
    """
    Record when a probation account's project is also mentioned
    by an active account. Used for auto-promotion scoring.
    """
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS probation_mentions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                handle       TEXT,
                mentioned_by TEXT,
                project_name TEXT,
                seen_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute("""
            INSERT INTO probation_mentions (handle, mentioned_by, project_name)
            VALUES (?,?,?)
        """, (handle.lower(), mentioned_by.lower(), project_name))


def get_probation_cross_mention_counts(days: int = 30) -> dict:
    """
    Return cross-mention counts per probation account within last N days.
    Accounts with count >= 2 are candidates for auto-promotion.
    """
    with _conn() as con:
        try:
            rows = con.execute("""
                SELECT handle, COUNT(DISTINCT project_name) as project_count,
                       COUNT(DISTINCT mentioned_by) as caller_count
                FROM probation_mentions
                WHERE seen_at >= datetime('now', ?)
                GROUP BY handle
                ORDER BY project_count DESC
            """, (f"-{days} days",)).fetchall()
            return {r[0]: {"projects": r[1], "callers": r[2]} for r in rows}
        except Exception:
            return {}


def promote_probation_account(handle: str) -> bool:
    """
    Promote a probation account to tier 2 in watchlist.json.
    Returns True if successful.
    """
    import json as _json
    handle_lower = handle.lower().lstrip("@")
    try:
        with open(settings.WATCHLIST_PATH) as f:
            data = _json.load(f)
        updated = False
        for acc in data.get("accounts", []):
            if acc["handle"].lower() == handle_lower:
                if acc.get("status") == "probation":
                    acc["tier"] = 2
                    acc["status"] = "active"
                    acc["notes"] = acc.get("notes", "") + " [Auto-promoted: cross-mention threshold met]"
                    updated = True
                    break
        if updated:
            with open(settings.WATCHLIST_PATH, "w") as f:
                _json.dump(data, f, indent=2)
        return updated
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Failed to promote @%s: %s", handle, exc
        )
        return False
