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
    )
