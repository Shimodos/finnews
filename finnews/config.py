import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Ленты: (название источника, URL). Добавлять новые — просто строкой сюда.
FEEDS: list[tuple[str, str]] = [
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch Pulse", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    # Европа
    ("CNBC Europe", "https://www.cnbc.com/id/19794221/device/rss/rss.html"),
    ("DW Business", "https://rss.dw.com/rdf/rss-en-bus"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
    # Азия
    ("CNBC Asia", "https://www.cnbc.com/id/19832390/device/rss/rss.html"),
    ("SCMP Business", "https://www.scmp.com/rss/92/feed"),
    ("Yahoo Finance (Nikkei)", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EN225&region=US&lang=en-US"),
]


def yahoo_ticker_feed(ticker: str) -> tuple[str, str]:
    return (
        f"Yahoo Finance ({ticker})",
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
    )


@dataclass
class Config:
    db_path: str = "finnews.db"
    score_model: str = "claude-haiku-5-5"
    digest_model: str = "claude-sonnet-5-5"
    min_score: int = 6
    max_digest_items: int = 40
    watchlist: list[str] = field(default_factory=list)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # Когда страница пересобирается: время UTC вида "05:00". Пусто — отсчёта на странице нет.
    schedule: list[str] = field(default_factory=list)
    # Контакт для запросов к SEC EDGAR (его требует SEC); пусто — отчёты компаний не запрашиваются.
    sec_contact: str = ""
    # Адрес сайта, например https://finnews.app — для превью ссылки в соцсетях. Пусто — без превью.
    site_url: str = ""

    @property
    def feeds(self) -> list[tuple[str, str]]:
        return FEEDS + [yahoo_ticker_feed(t) for t in self.watchlist]


def load_config() -> Config:
    load_dotenv()
    watchlist = [t.strip().upper() for t in os.getenv("WATCHLIST", "").split(",") if t.strip()]
    return Config(
        db_path=os.getenv("FINNEWS_DB", "finnews.db"),
        score_model=os.getenv("FINNEWS_SCORE_MODEL", "claude-haiku-5-5"),
        digest_model=os.getenv("FINNEWS_DIGEST_MODEL", "claude-sonnet-5-5"),
        min_score=int(os.getenv("FINNEWS_MIN_SCORE", "6")),
        watchlist=watchlist,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        schedule=[t.strip() for t in os.getenv("FINNEWS_SCHEDULE", "").split(",") if t.strip()],
        sec_contact=os.getenv("SEC_CONTACT", "").strip(),
        site_url=os.getenv("FINNEWS_SITE_URL", "").strip(),
    )
