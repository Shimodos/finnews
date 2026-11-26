"""Свежие отчёты компаний из базы SEC EDGAR — первоисточник, без пересказа.

SEC — Комиссия по ценным бумагам США; публичные компании обязаны подавать ей отчёты:
10-K (годовой), 10-Q (квартальный), 8-K (важное событие). API официальный и без ключа, но
SEC требует в заголовке User-Agent контакт того, кто делает запросы, и не больше 10 запросов
в секунду. Контакт берётся из переменной окружения SEC_CONTACT (в коде и в git его нет);
без него запросы не делаются и блок на странице не показывается.

У фондов (ETF) таких отчётов нет — тикеры без номера CIK пропускаются.
"""

import logging
import time
from datetime import date, timedelta

import httpx

from .config import Config
from .market import LARGE_CAPS

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{doc}"
FORMS = ("10-K", "10-Q", "8-K")
PAUSE = 0.15  # секунд между запросами: заметно ниже предела SEC
LIMIT = 12


def parse(symbol: str, cik: int, data: dict, since: str) -> list[dict]:
    """Ответ submissions одной компании -> её отчёты нужных форм, поданные не раньше since."""
    recent = (data.get("filings") or {}).get("recent") or {}
    result = []
    for i, form in enumerate(recent.get("form") or []):
        day = str(recent["filingDate"][i])
        if form not in FORMS or day < since:
            continue
        # У 8-K — номера пунктов формы: о чём сообщение (2.02 — результаты квартала и т. д.).
        items = [code.strip() for code in str(recent["items"][i] or "").split(",") if code.strip()]
        if form == "8-K" and set(items) <= {"9.01"}:
            continue  # только приложения, самого события нет
        folder = str(recent["accessionNumber"][i]).replace("-", "")
        result.append({
            "symbol": symbol,
            "name": str(data.get("name") or symbol),
            "form": form,
            "date": day,
            "accepted": str(recent["acceptanceDateTime"][i]),
            "items": items,
            "url": ARCHIVE_URL.format(cik=cik, folder=folder, doc=str(recent["primaryDocument"][i] or "")),
        })
    return result


def recent(cfg: Config, *, days: int = 5, today: date | None = None, get=None) -> list[dict]:
    """Отчёты крупных компаний и тикеров из своего списка за последние days дней, свежие первыми.

    get(url) -> JSON; по умолчанию запрос к SEC с контактом из cfg.sec_contact.
    """
    if get is None:
        if not cfg.sec_contact:
            return []
        headers = {"User-Agent": f"finnews {cfg.sec_contact}"}

        def get(url: str) -> dict:
            response = httpx.get(url, headers=headers, timeout=40)
            response.raise_for_status()
            time.sleep(PAUSE)
            return response.json()

    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    try:
        ciks = {str(row["ticker"]).upper(): int(row["cik_str"]) for row in get(TICKERS_URL).values()}
    except Exception as error:  # noqa: BLE001 - без отчётов страница собирается, просто без блока
        log.warning("SEC EDGAR недоступен: %s", error)
        return []
    result = []
    for symbol in dict.fromkeys([*cfg.watchlist, *LARGE_CAPS]):
        cik = ciks.get(symbol)
        if cik is None:
            continue
        try:
            result.extend(parse(symbol, cik, get(SUBMISSIONS_URL.format(cik=cik)), since))
        except Exception as error:  # noqa: BLE001 - отказ по одной компании не мешает остальным
            log.warning("Отчёты %s недоступны: %s", symbol, error)
    result.sort(key=lambda item: (item["date"], item["accepted"]), reverse=True)
    return result[:LIMIT]
