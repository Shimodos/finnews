"""Цены на бензин на заправках: США и Евросоюз.

Оба источника официальные, без ключа и обновляются раз в неделю:
- США — Управление энергетической информации (EIA): средняя цена обычного бензина, доллары
  за галлон; на странице шесть последних недель;
- ЕС — еженедельный «Нефтяной бюллетень» Еврокомиссии: средняя цена Euro-super 95 с налогами
  по 27 странам, евро за 1000 литров; файл xlsx, ссылка на него берётся со страницы бюллетеня.

Для Азии открытого источника, который можно читать автоматически, не нашлось.
Каждый источник загружается отдельно: отказ одного не мешает другому (см. refresh).
"""

import io
import logging
import re
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0"}
EIA_URL = "https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_nus_w.htm"
EC_PAGE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
LITRES_PER_GALLON = 3.785411784


def _get(url: str) -> httpx.Response:
    response = httpx.get(url, headers=HEADERS, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return response


def parse_eia(html: str) -> dict:
    """Страница EIA -> последняя и предыдущая недельная цена обычного бензина, $ за галлон."""
    dates = [
        re.sub(r"<[^>]+>|\s+", "", cell)
        for cell in re.findall(r'<th[^>]*class="Series5"[^>]*>(.*?)</th>', html, re.S)
    ]
    # Первая строка Regular — по всей стране. Название лежит во вложенной таблице, поэтому
    # строку берём до ячейки DataHist (ссылка на историю), а не до ближайшего </tr>.
    row = re.search(r">\s*Regular\s*</td>(.*?)DataHist", html, re.S)
    values = [float(v) for v in re.findall(r"<td[^>]*>\s*(\d+\.\d+)\s*</td>", row.group(1))] if row else []
    if len(values) < 2 or len(dates) < len(values):
        raise RuntimeError("EIA: не нашёл строку с ценой обычного бензина")
    period = datetime.strptime(dates[len(values) - 1], "%m/%d/%y").date()
    return {"value": values[-1], "prev": values[-2], "period": period.isoformat()}


def us() -> dict:
    return parse_eia(_get(EIA_URL).text)


def parse_ec(content: bytes) -> dict:
    """Файл бюллетеня Еврокомиссии -> средняя цена Euro-super 95 по ЕС, евро за литр."""
    book = zipfile.ZipFile(io.BytesIO(content))
    strings = [
        re.sub(r"<[^>]+>", "", item)
        for item in re.findall(r"<si>(.*?)</si>", book.read("xl/sharedStrings.xml").decode("utf-8"), re.S)
    ]
    # Пустые ячейки записаны без закрывающего тега (<c .../>) — убираем их, иначе они
    # «съедают» следующую ячейку.
    sheet = re.sub(r"<c [^>]*/>", "", book.read("xl/worksheets/sheet1.xml").decode("utf-8"))
    cells = {}
    for ref, attrs, body in re.findall(r'<c r="([A-Z]+\d+)"([^>]*)>(.*?)</c>', sheet, re.S):
        value = re.search(r"<v>(.*?)</v>", body)
        if value:
            cells[ref] = strings[int(value.group(1))] if 't="s"' in attrs else value.group(1)
    if "super 95" not in cells.get("B1", ""):
        raise RuntimeError("Еврокомиссия: в колонке B не Euro-super 95 — формат файла изменился")
    row = next((ref[1:] for ref, text in cells.items() if ref[0] == "A" and "EUR27" in text), None)
    if row is None:
        raise RuntimeError("Еврокомиссия: не нашёл строку со средней ценой по ЕС")
    # дата в файле — число дней в счёте Excel (от 30.12.1899)
    period = date(1899, 12, 30) + timedelta(days=int(float(cells["A2"])))
    return {"value": round(float(cells[f"B{row}"]) / 1000, 3), "period": period.isoformat()}


def eu() -> dict:
    page = _get(EC_PAGE).text
    link = re.search(r'href="([^"]*prices(?:%20|\s|\+)with(?:%20|\s|\+)taxes[^"]*\.xlsx[^"]*)"', page, re.I)
    if not link:
        raise RuntimeError("Еврокомиссия: на странице бюллетеня нет ссылки на файл с ценами")
    return parse_ec(_get(urljoin(EC_PAGE, link.group(1).replace("&amp;", "&"))).content)


def refresh(old: dict, sources=None) -> dict:
    """Новые значения поверх прежних. sources — {название: функция} для подмены в тестах.

    В файле Еврокомиссии только текущая неделя, поэтому «было» для ЕС берём из своего прошлого
    значения, когда неделя сменилась.
    """
    result = dict(old or {})
    for name, load in (sources or {"us": us, "eu": eu}).items():
        try:
            item = load()
        except Exception as error:  # noqa: BLE001 - один источник не должен ронять другой
            log.warning("Цена бензина (%s) недоступна: %s", name, error)
            continue
        before = result.get(name) or {}
        if "prev" not in item:
            if before.get("period") == item["period"]:
                if "prev" in before:
                    item["prev"] = before["prev"]
            elif "value" in before:
                item["prev"] = before["value"]
        result[name] = item
    return result
