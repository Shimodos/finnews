"""Календарь отчётов: какие крупные компании отчитываются в ближайшие дни.

Источник — календарь на сайте Nasdaq (JSON, ключ не нужен). Адрес неофициальный и может
измениться, поэтому любая ошибка не ломает сборку: день, по которому нет ответа, пропускается.
"""

import logging
import re
from datetime import date, timedelta

import httpx

from .config import Config
from .market import LARGE_CAPS

log = logging.getLogger(__name__)

URL = "https://api.nasdaq.com/api/calendar/earnings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
}
WHEN = {"time-pre-market": "before", "time-after-hours": "after"}  # до открытия / после закрытия

MIN_CAP = 10_000_000_000  # «крупная» — капитализация от 10 млрд долларов
TOP_PER_DAY = 3


def _money(text) -> float | None:
    """'$1,361,556,740' -> 1361556740.0; '($0.38)' -> -0.38; пусто или 'N/A' -> None."""
    digits = re.sub(r"[^\d.]", "", str(text or ""))
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if "(" in str(text) else value


def select(rows: list, keep: set[str]) -> list[dict]:
    """Из отчётов одного дня: компании из списка keep и самые крупные из остальных."""
    items = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "name": str(row.get("name") or symbol),
            "when": WHEN.get(row.get("time"), ""),
            "eps": _money(row.get("epsForecast")),
            "cap": _money(row.get("marketCap")) or 0,
        })
    items.sort(key=lambda item: -item["cap"])
    top = [i["symbol"] for i in items if i["cap"] >= MIN_CAP and i["symbol"] not in keep][:TOP_PER_DAY]
    return [i for i in items if i["symbol"] in keep or i["symbol"] in top]


def _http_get(day: str) -> dict:
    response = httpx.get(URL, params={"date": day}, headers=HEADERS, timeout=25, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def upcoming(cfg: Config, *, days: int = 7, today: date | None = None, get=None) -> list[dict]:
    """Отчёты на ближайшие дни: [{"date": "2026-12-01", "items": [...]}], дни без отчётов опущены.

    get(день в формате ISO) -> JSON ответа; по умолчанию запрос к Nasdaq.
    """
    get = get or _http_get
    today = today or date.today()
    keep = {t.upper() for t in cfg.watchlist} | set(LARGE_CAPS)
    result = []
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        try:
            rows = (get(day.isoformat()).get("data") or {}).get("rows") or []
        except Exception as error:  # noqa: BLE001 - источник неофициальный, отказ не критичен
            log.warning("Календарь отчётов на %s недоступен: %s", day, error)
            continue
        items = select(rows, keep)
        if items:
            result.append({"date": day.isoformat(), "items": items})
    return result
