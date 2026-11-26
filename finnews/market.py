"""Котировки для страницы: индексы, список инвестора, фонды сфер и крупные компании.

Источник — JSON-эндпоинт графиков Yahoo Finance: ключ не нужен, но он неофициальный
и может измениться без предупреждения. Поэтому любая ошибка здесь не ломает сборку
страницы: символ, по которому нет ответа, просто пропускается.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from . import db
from .config import Config

log = logging.getLogger(__name__)

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0"}  # без него Yahoo отвечает отказом

INDEXES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^FTSE": "FTSE 100",
    "^GDAXI": "DAX",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
}

# Биржи, чьи часы работы показываются на странице: индекс, по которому Yahoo отдаёт
# время сессии, и название города (ru, uk, en).
EXCHANGES = (
    ("^GSPC", ("Нью-Йорк", "Нью-Йорк", "New York")),
    ("^FTSE", ("Лондон", "Лондон", "London")),
    ("^GDAXI", ("Франкфурт", "Франкфурт", "Frankfurt")),
    ("^N225", ("Токио", "Токіо", "Tokyo")),
    ("^HSI", ("Гонконг", "Гонконг", "Hong Kong")),
)

# По какому фонду считается изменение сферы за день (отраслевые фонды SPDR).
SECTOR_SYMBOLS = {
    "markets": "^GSPC",
    "tech": "XLK",
    "finance": "XLF",
    "energy": "XLE",
    "health": "XLV",
    "consumer": "XLY",
    "industry": "XLI",
    "crypto": "BTC-USD",
}

# Крупные компании США: среди них ищутся лидеры роста и падения за день.
LARGE_CAPS = (
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "JPM", "LLY",
    "V", "UNH", "XOM", "MA", "COST", "WMT", "HD", "PG", "JNJ", "NFLX",
    "BAC", "ORCL", "CRM", "KO", "CVX", "AMD", "PEP", "MRK", "ABBV", "ADBE",
    "DIS", "MCD", "CSCO", "INTC", "BA", "NKE", "PFE", "GS", "CAT", "IBM",
    "QCOM", "TXN", "GE", "UBER", "PLTR", "WFC", "MS", "T", "VZ", "SBUX",
)

MAX_POINTS = 32


def parse(result: dict, *, with_points: bool) -> dict | None:
    """Один элемент chart.result из ответа Yahoo -> котировка. None, если данных нет."""
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        return None
    item = {
        "name": meta.get("shortName") or meta.get("symbol") or "",
        "type": meta.get("instrumentType") or "",
        "price": round(float(price), 2),
        "prev": round(float(prev), 2),
        "change": round((float(price) - float(prev)) / float(prev) * 100, 2),
        "time": datetime.fromtimestamp(meta.get("regularMarketTime") or 0, timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    # Обычная торговая сессия биржи (текущая или ближайшая): начало и конец в секундах Unix
    # и часовой пояс биржи — по ним страница считает, открыта ли биржа и когда откроется.
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    if regular.get("start") and regular.get("end") and meta.get("exchangeTimezoneName"):
        item["session"] = {
            "start": int(regular["start"]),
            "end": int(regular["end"]),
            "tz": str(meta["exchangeTimezoneName"]),
        }
    if with_points:
        quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [c for c in quotes.get("close") or [] if c is not None]
        step = max(1, -(-len(closes) // MAX_POINTS))
        item["points"] = [round(float(c), 2) for c in closes[::step]]
    return item


def movers(data: dict, count: int = 3) -> list[str]:
    """Символы лидеров дня среди акций: сначала выросшие сильнее всех, затем упавшие."""
    stocks = [(s, q["change"]) for s, q in (data.get("quotes") or {}).items() if q.get("type") == "EQUITY"]
    gainers = sorted((x for x in stocks if x[1] > 0), key=lambda x: -x[1])[:count]
    losers = sorted((x for x in stocks if x[1] < 0), key=lambda x: x[1])[:count]
    return [s for s, _ in gainers + losers]


def _signed(change: float) -> str:
    return f"{change:+.2f}%"


def summary(data: dict) -> str:
    """Котировки текстом — для модели, чтобы обзор дня опирался на данные биржи."""
    quotes = data.get("quotes") or {}
    if not quotes:
        return ""
    # У каждой биржи своя последняя сессия, поэтому дата стоит у каждой строки.
    lines = [
        f"{quotes[s]['name']}: {quotes[s]['price']:,.2f}, за день {_signed(quotes[s]['change'])}"
        f" (данные на {quotes[s]['time'][:10]} {quotes[s]['time'][11:16]} UTC)"
        for s in data.get("pulse") or []
        if s in quotes
    ]
    sectors = [
        f"{sector} ({symbol}) {_signed(quotes[symbol]['change'])}"
        for sector, symbol in SECTOR_SYMBOLS.items()
        if symbol in quotes and sector != "markets"
    ]
    if sectors:
        lines.append("Сферы по отраслевым фондам США: " + "; ".join(sectors))
    leaders = [f"{s} {_signed(quotes[s]['change'])}" for s in movers(data)]
    if leaders:
        lines.append("Сильнее всего изменились среди крупных компаний США: " + "; ".join(leaders))
    return "\n".join(lines)


def _http_get(symbol: str) -> dict:
    response = httpx.get(
        URL.format(symbol=quote(symbol)),
        params={"range": "1d", "interval": "15m"},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def snapshot(cfg: Config, *, get=None) -> dict:
    """Собирает котировки. get(symbol) -> JSON ответа; по умолчанию запрос к Yahoo."""
    get = get or _http_get
    pulse = list(dict.fromkeys([*INDEXES, *cfg.watchlist]))
    symbols = list(dict.fromkeys([*pulse, *SECTOR_SYMBOLS.values(), *LARGE_CAPS]))

    def load(symbol: str):
        try:
            result = get(symbol)["chart"]["result"][0]
            return symbol, parse(result, with_points=symbol in pulse)
        except Exception as error:  # noqa: BLE001 - источник неофициальный, отказ не критичен
            log.warning("Котировка %s недоступна: %s", symbol, error)
            return symbol, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        loaded = list(pool.map(load, symbols))
    quotes = {symbol: item for symbol, item in loaded if item}
    for symbol, name in INDEXES.items():
        if symbol in quotes:
            quotes[symbol]["name"] = name
    sessions = {s: quotes[s]["session"] for s, _ in EXCHANGES if "session" in quotes.get(s, {})}
    return {
        "asof": db.now_iso(),
        "pulse": [s for s in pulse if s in quotes],
        "sessions": sessions,
        "quotes": quotes,
    }
