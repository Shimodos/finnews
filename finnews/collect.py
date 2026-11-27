import calendar
import html
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

import feedparser
import httpx

from . import db
from .config import Config

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; rss-reader)"
SIMILARITY = 0.9
_TAG = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^\w\s]")


def clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text or "")).split())


def title_key(title: str) -> str:
    return " ".join(_NON_WORD.sub(" ", title.lower()).split())


def untranslated(original: str, translated: str) -> bool:
    """Перевода нет или он повторяет оригинал, написанный не кириллицей."""
    original, translated = " ".join(str(original or "").split()), " ".join(str(translated or "").split())
    if re.search("[а-яёіїєґ]", original, re.IGNORECASE):
        return False
    return not translated or translated.casefold() == original.casefold()


def is_duplicate(key: str, known: list[str]) -> bool:
    for other in known:
        if key == other:
            return True
        # Быстрый отсев по длине, чтобы не сравнивать заведомо разные заголовки
        if abs(len(key) - len(other)) > 0.2 * max(len(key), len(other)):
            continue
        if SequenceMatcher(None, key, other).ratio() >= SIMILARITY:
            return True
    return False


def entry_published(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        dt = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
        return dt.isoformat(timespec="seconds")
    return db.now_iso()


def fetch_feed(client: httpx.Client, url: str) -> list:
    resp = client.get(url)
    resp.raise_for_status()
    return feedparser.parse(resp.content).entries


def collect(cfg: Config, conn, feeds: list[tuple[str, str]] | None = None) -> dict[str, int]:
    """Собирает ленты (по умолчанию все из настроек).

    Возвращает {источник: число новых статей}; -1 — лента не ответила.
    """
    feeds = cfg.feeds if feeds is None else feeds
    known = db.recent_title_keys(conn)
    stats: dict[str, int] = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
    ) as client:
        for source, url in feeds:
            try:
                entries = fetch_feed(client, url)
            except httpx.HTTPError as e:
                log.warning("Лента %s недоступна: %s", source, e)
                stats[source] = -1
                continue
            added = 0
            for entry in entries:
                title = clean(entry.get("title", ""))
                link = entry.get("link", "")
                if not title or not link:
                    continue
                key = title_key(title)
                if is_duplicate(key, known):
                    continue
                inserted = db.insert_article(
                    conn,
                    url=link,
                    title=title,
                    title_key=key,
                    summary=clean(entry.get("summary", ""))[:1000],
                    source=source,
                    published=entry_published(entry),
                )
                if inserted:
                    known.append(key)
                    added += 1
            stats[source] = added
    conn.commit()
    return stats
