"""Макроданные для блока «Экономика в цифрах»: ставки, инфляция, безработица.

Все источники официальные и без ключа:
- ставка ФРС — ФРБ Нью-Йорка (markets.newyorkfed.org);
- инфляция и безработица США — Бюро статистики труда США (api.bls.gov, без ключа до 25 запросов
  в сутки с одного адреса);
- ставка ЕЦБ — портал данных ЕЦБ (data-api.ecb.europa.eu);
- инфляция и безработица еврозоны — Eurostat;
- доходность 10-летних облигаций США — индекс ^TNX из Yahoo Finance.

Каждый показатель загружается отдельно: если источник не ответил, остальные всё равно
обновляются, а для пропущенного остаётся прошлое значение (см. refresh).
"""

import logging
from datetime import date

import httpx

from . import market

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0"}
ORDER = ("fed", "us_cpi", "us_unemployment", "us_10y", "ecb", "eu_cpi", "eu_unemployment")
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"


def _get(url: str, **kwargs) -> httpx.Response:
    response = httpx.get(url, headers=HEADERS, timeout=40, follow_redirects=True, **kwargs)
    response.raise_for_status()
    return response


def fed() -> dict:
    """Целевой диапазон ставки ФРС."""
    url = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
    rate = _get(url).json()["refRates"][0]
    return {
        "low": float(rate["targetRateFrom"]),
        "high": float(rate["targetRateTo"]),
        "period": str(rate["effectiveDate"]),
    }


def bls(today: date | None = None, post=None) -> dict:
    """Инфляция за год (по индексу CPI) и безработица США. Один запрос на оба ряда."""
    year = (today or date.today()).year
    payload = {
        "seriesid": ["CUUR0000SA0", "LNS14000000"],
        "startyear": str(year - 2),
        "endyear": str(year),
    }
    if post is None:
        response = httpx.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/", json=payload, headers=HEADERS, timeout=40
        )
        response.raise_for_status()
        data = response.json()
    else:
        data = post(payload)
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {data.get('status')} {data.get('message')}")
    series = {}
    for item in data["Results"]["series"]:
        # только месяцы (M01..M12); M13 — среднее за год
        series[item["seriesID"]] = {
            f"{row['year']}-{row['period'][1:]}": float(row["value"])
            for row in item["data"]
            if row["period"].startswith("M") and row["period"] != "M13" and row["value"] not in ("", "-")
        }
    result = {}
    cpi = series.get("CUUR0000SA0") or {}

    def yearly(period: str) -> float | None:
        before = f"{int(period[:4]) - 1}{period[4:]}"
        return round((cpi[period] / cpi[before] - 1) * 100, 1) if before in cpi else None

    # Берём последние месяцы, для которых есть значение годом раньше: бывает, что месяц
    # не публиковался (так было с октябрём 2025), и тогда сравнить его не с чем.
    months = [m for m in sorted(cpi, reverse=True) if yearly(m) is not None]
    if months:
        result["us_cpi"] = {"value": yearly(months[0]), "period": months[0]}
        if len(months) > 1:
            result["us_cpi"]["prev"] = yearly(months[1])
    jobs = series.get("LNS14000000") or {}
    months = sorted(jobs, reverse=True)
    if months:
        result["us_unemployment"] = {"value": jobs[months[0]], "period": months[0]}
        if len(months) > 1:
            result["us_unemployment"]["prev"] = jobs[months[1]]
    return result


def us_10y() -> dict:
    """Доходность 10-летних облигаций США: значение индекса ^TNX — это проценты."""
    quote = market.parse(market._http_get("^TNX")["chart"]["result"][0], with_points=False)
    if not quote:
        raise RuntimeError("нет данных по ^TNX")
    return {"value": quote["price"], "prev": quote["prev"], "period": quote["time"][:10]}


def ecb() -> dict:
    """Ставка ЕЦБ по депозитам — главная ставка еврозоны."""
    url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
    lines = _get(url, params={"lastNObservations": 1, "format": "csvdata"}).text.strip().splitlines()
    head, row = lines[0].split(","), lines[-1].split(",")
    return {"value": float(row[head.index("OBS_VALUE")]), "period": row[head.index("TIME_PERIOD")]}


def _eurostat(dataset: str, get=None, **filters) -> dict:
    """Последнее заполненное значение ряда Eurostat и значение перед ним.

    Последний месяц у Eurostat иногда ещё пуст — берём последний заполненный.
    get(dataset, params) -> JSON; по умолчанию запрос к Eurostat.
    """
    params = {"format": "JSON", **filters, "lastTimePeriod": 4}
    data = get(dataset, params) if get else _get(EUROSTAT + dataset, params=params).json()
    periods = {index: period for period, index in data["dimension"]["time"]["category"]["index"].items()}
    filled = sorted((periods[int(i)], float(v)) for i, v in data["value"].items() if v is not None)
    if not filled:
        raise RuntimeError(f"Eurostat {dataset}: нет значений")
    result = {"value": filled[-1][1], "period": filled[-1][0]}
    if len(filled) > 1:
        result["prev"] = filled[-2][1]
    return result


def eu_cpi(get=None) -> dict:
    """Инфляция еврозоны за год по индексу HICP (все товары и услуги)."""
    return _eurostat("prc_hicp_minr", get, geo="EA", coicop18="TOTAL", unit="RCH_A")


def eu_unemployment(get=None) -> dict:
    """Безработица еврозоны."""
    return _eurostat("une_rt_m", get, geo="EA21", s_adj="SA", age="TOTAL", sex="T", unit="PC_ACT")


def fetch(sources=None) -> dict:
    """Загружает показатели. sources — {название: функция} для подмены в тестах."""
    sources = sources or {
        "fed": fed, "bls": bls, "us_10y": us_10y, "ecb": ecb, "eu_cpi": eu_cpi,
        "eu_unemployment": eu_unemployment,
    }
    result = {}
    for name, load in sources.items():
        try:
            value = load()
        except Exception as error:  # noqa: BLE001 - один источник не должен ронять остальные
            log.warning("Макроданные %s недоступны: %s", name, error)
            continue
        if name == "bls":
            result.update(value)  # один источник даёт два показателя
        else:
            result[name] = value
    return result


def refresh(old: dict, sources=None) -> dict:
    """Новые значения поверх прежних: показатель, который не удалось обновить, не пропадает."""
    return {**(old or {}), **fetch(sources)}


def summary(data: dict) -> str:
    """Показатели текстом — для модели, чтобы обзор дня мог на них опираться."""
    lines = []
    for key in ORDER:
        item = (data or {}).get(key)
        if not item:
            continue
        if key == "fed":
            lines.append(f"Ставка ФРС: {item['low']:.2f}–{item['high']:.2f}% (на {item['period']})")
            continue
        label = {
            "us_cpi": "Инфляция в США за год",
            "us_unemployment": "Безработица в США",
            "us_10y": "Доходность 10-летних облигаций США",
            "ecb": "Депозитная ставка ЕЦБ",
            "eu_cpi": "Инфляция в еврозоне за год",
            "eu_unemployment": "Безработица в еврозоне",
        }[key]
        before = f", до этого {item['prev']}%" if "prev" in item else ""
        lines.append(f"{label}: {item['value']}% (за {item['period']}{before})")
    return "\n".join(lines)
