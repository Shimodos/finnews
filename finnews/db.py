import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    title_key   TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,
    published   TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    score       INTEGER,
    tickers     TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    digested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def recent_title_keys(conn: sqlite3.Connection, days: int = 3) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT title_key FROM articles WHERE fetched_at >= ?", (since,))
    return [r["title_key"] for r in rows]


def insert_article(conn, *, url, title, title_key, summary, source, published) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO articles (url, title, title_key, summary, source, published, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (url, title, title_key, summary, source, published, now_iso()),
    )
    return cur.rowcount == 1


def unscored(conn, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM articles WHERE score IS NULL ORDER BY published DESC LIMIT ?", (limit,)
    ).fetchall()


def save_score(conn, article_id: int, score: int, tickers: list[str], reason: str) -> None:
    conn.execute(
        "UPDATE articles SET score = ?, tickers = ?, reason = ? WHERE id = ?",
        (score, ",".join(tickers), reason, article_id),
    )


def for_digest(conn, *, min_score: int, hours: int, limit: int) -> list[sqlite3.Row]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM articles WHERE digested_at IS NULL AND score >= ? AND published >= ?"
        " ORDER BY score DESC, published DESC LIMIT ?",
        (min_score, since, limit),
    ).fetchall()


def mark_digested(conn, ids: list[int]) -> None:
    conn.executemany("UPDATE articles SET digested_at = ? WHERE id = ?", [(now_iso(), i) for i in ids])
