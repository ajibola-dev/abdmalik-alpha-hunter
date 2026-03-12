"""
modules/database.py — SQLite storage for Alpha Hunter
Tables:
  - watched_accounts   : X accounts being monitored
  - discovered_projects: projects extracted from tweets
  - project_scores     : scoring history
  - grind_tracker      : your personal task/wallet tracking
  - alerts_sent        : dedup alert history
"""
import sqlite3
import logging
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)


def _conn():
    return sqlite3.connect(settings.DB_PATH)


def init_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS watched_accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            handle      TEXT UNIQUE NOT NULL,
            name        TEXT,
            tier        INTEGER DEFAULT 2,
            trusted     INTEGER DEFAULT 0,
            notes       TEXT,
            added_at    TEXT DEFAULT (datetime('now')),
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
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER REFERENCES discovered_projects(id),
            score           REAL,
            breakdown       TEXT,
            label           TEXT,
            scored_at       TEXT DEFAULT (datetime('now'))
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
        """)
    logger.info("Database initialised at %s", settings.DB_PATH)


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
        row = con.execute("SELECT id FROM watched_accounts WHERE handle=?", (handle,)).fetchone()
        return row[0]


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
        row = con.execute("SELECT id FROM discovered_projects WHERE name=?", (name,)).fetchone()
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
