"""Ближайшие торговые сессии бирж — с праздниками и сокращёнными днями.

Источник — библиотека exchange_calendars: календари бирж зашиты в неё и обновляются вместе
с пакетом, сеть не нужна. Обеденные перерывы (Токио, Гонконг) не учитываются. Если календарь
получить не удалось, биржа просто не попадает в результат — страница тогда считает часы
по обычному расписанию будней (см. скрипт в site.py).
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Индекс, под которым биржа показана на странице (market.EXCHANGES) -> код её календаря.
CALENDARS = {
    "^GSPC": "XNYS",
    "^FTSE": "XLON",
    "^GDAXI": "XFRA",
    "^N225": "XTKS",
    "^HSI": "XHKG",
}


def upcoming(days: int = 10, now: datetime | None = None) -> dict:
    """{индекс: [[начало, конец], ...]} — сессии, которые идут сейчас или начнутся за days дней.

    Время — секунды Unix. now — для тестов.
    """
    now = now or datetime.now(timezone.utc)
    start, end = (now - timedelta(days=2)).date(), (now + timedelta(days=days)).date()
    result = {}
    try:
        import exchange_calendars
    except Exception as error:  # noqa: BLE001 - без календаря страница обходится расписанием будней
        log.warning("Календари бирж недоступны: %s", error)
        return result
    for symbol, code in CALENDARS.items():
        try:
            calendar = exchange_calendars.get_calendar(code, start=str(start), end=str(end))
            sessions = [
                [int(calendar.session_open(s).timestamp()), int(calendar.session_close(s).timestamp())]
                for s in calendar.sessions  # все сессии календаря, уже ограниченного start и end
            ]
        except Exception as error:  # noqa: BLE001
            log.warning("Календарь биржи %s недоступен: %s", code, error)
            continue
        sessions = [s for s in sessions if s[1] > now.timestamp()]
        if sessions:
            result[symbol] = sessions
    return result
