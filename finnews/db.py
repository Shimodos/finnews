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
    digested_at TEXT,
    sector      TEXT NOT NULL DEFAULT '',
    tone        TEXT NOT NULL DEFAULT '',
    i18n        TEXT NOT NULL DEFAULT '',
    event       INTEGER,
    region      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
CREATE TABLE IF NOT EXISTS briefs (
    day        TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    created_at TEXT NOT NULL,
    data       TEXT NOT NULL
);
"""

# Колонки, появившиеся после первой версии: добавляются в уже существующую базу.
MIGRATIONS = {
    "sector": "ALTER TABLE articles ADD COLUMN sector TEXT NOT NULL DEFAULT ''",
    "tone": "ALTER TABLE articles ADD COLUMN tone TEXT NOT NULL DEFAULT ''",
    "i18n": "ALTER TABLE articles ADD COLUMN i18n TEXT NOT NULL DEFAULT ''",
    "event": "ALTER TABLE articles ADD COLUMN event INTEGER",
    "region": "ALTER TABLE articles ADD COLUMN region TEXT NOT NULL DEFAULT ''",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
    for column, statement in MIGRATIONS.items():
        if column not in existing:
            conn.execute(statement)
    conn.commit()
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


def save_score(
    conn, article_id: int, score: int, tickers: list[str], reason: str,
    *, sector: str = "", tone: str = "", i18n: str = "", region: str = "",
) -> None:
    conn.execute(
        "UPDATE articles SET score = ?, tickers = ?, reason = ?, sector = ?, tone = ?, i18n = ?,"
        " region = ? WHERE id = ?",
        (score, ",".join(tickers), reason, sector, tone, i18n, region, article_id),
    )


def top_recent(conn, *, min_score: int, hours: int, limit: int) -> list[sqlite3.Row]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM articles WHERE score >= ? AND published >= ?"
        " ORDER BY score DESC, published DESC LIMIT ?",
        (min_score, since, limit),
    ).fetchall()


def save_brief(conn, day: str, data: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO briefs (day, created_at, data) VALUES (?, ?, ?)",
        (day, now_iso(), data),
    )


def latest_brief(conn, *, hours: int) -> str:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT data FROM briefs WHERE created_at >= ? ORDER BY created_at DESC LIMIT 1", (since,)
    ).fetchone()
    return row["data"] if row else ""


def set_events(conn, groups: list[list[int]], *, hours: int) -> None:
    """Записывает группы новостей об одном событии; первый id в группе — главная новость.

    У всех новостей группы в колонке event стоит id главной. Прежняя разметка за это
    время стирается: группы каждый раз считаются заново.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    conn.execute("UPDATE articles SET event = NULL WHERE published >= ?", (since,))
    for group in groups:
        conn.executemany("UPDATE articles SET event = ? WHERE id = ?", [(group[0], i) for i in group])


def all_briefs(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT day, data FROM briefs ORDER BY day DESC").fetchall()


def between(conn, start: str, end: str) -> list[sqlite3.Row]:
    """Статьи, опубликованные в промежутке [start, end) — время в UTC, как в базе."""
    return conn.execute(
        "SELECT * FROM articles WHERE published >= ? AND published < ? ORDER BY published DESC",
        (start, end),
    ).fetchall()


def save_market(conn, data: str) -> None:
    """Хранится только последний снимок котировок."""
    conn.execute(
        "INSERT OR REPLACE INTO market (id, created_at, data) VALUES (1, ?, ?)", (now_iso(), data)
    )


def latest_market(conn, *, hours: int) -> str:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = conn.execute("SELECT data FROM market WHERE created_at >= ?", (since,)).fetchone()
    return row["data"] if row else ""


def for_digest(conn, *, min_score: int, hours: int, limit: int) -> list[sqlite3.Row]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM articles WHERE digested_at IS NULL AND score >= ? AND published >= ?"
        " ORDER BY score DESC, published DESC LIMIT ?",
        (min_score, since, limit),
    ).fetchall()


def recent(conn, *, hours: int) -> list[sqlite3.Row]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM articles WHERE published >= ? ORDER BY published DESC", (since,)
    ).fetchall()


def mark_digested(conn, ids: list[int]) -> None:
    conn.executemany("UPDATE articles SET digested_at = ? WHERE id = ?", [(now_iso(), i) for i in ids])
