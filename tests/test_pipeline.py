import json
from types import SimpleNamespace

from finnews import db
from finnews.collect import clean, is_duplicate, title_key
from finnews.config import Config
from finnews.llm import (
    Brief, BriefText, EventGroup, EventGroups, MoverReason, MoverReasons, Score, ScoreBatch, Week, WeekText,
    build_brief, build_digest, build_weekly, explain_movers, group_events, score_articles,
)
from finnews.telegram import split_message


def test_clean_strips_html():
    assert clean("<p>Fed &amp; rates</p>\n ok") == "Fed & rates ok"


def test_duplicates():
    known = [title_key("Fed holds rates steady, signals cuts in 2027")]
    assert is_duplicate(title_key("Fed holds rates steady; signals cuts in 2027!"), known)
    assert is_duplicate(title_key("Fed holds rates steady, signals cut in 2027"), known)
    assert not is_duplicate(title_key("Apple reports record quarterly revenue"), known)


def test_split_message():
    text = "\n\n".join(["a" * 1000] * 10)
    parts = split_message(text, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts).count("a") == 10000


class FakeClient:
    """Подмена anthropic.Anthropic: без сети, фиксированные ответы."""

    def __init__(self):
        self.messages = SimpleNamespace(parse=self._parse)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.digest_prompt = ""

    def _parse(self, **kw):
        blocks = kw["messages"][0]["content"].split("index: ")[1:]
        if kw["output_format"] is EventGroups:
            # склеивает всё про Apple; index 99 и одиночная группа должны отсеяться
            apple = [i for i, block in enumerate(blocks) if "Apple" in block]
            groups = [EventGroup(indexes=[*apple, 99]), EventGroup(indexes=[len(blocks) - 1])]
            return SimpleNamespace(stop_reason="end_turn", parsed_output=EventGroups(groups=groups))
        if kw["output_format"] is MoverReasons:
            self.movers_prompt = kw["messages"][0]["content"]
            # ZZZ не спрашивали — должен отсеяться; story 99 не существует — ссылка не сохранится
            items = [
                MoverReason(symbol="aapl", story=0, ru="Причина <роста>", uk="Причина", en="Reason"),
                MoverReason(symbol="ZZZ", story=99, ru="Лишнее", uk="Лишнее", en="Extra"),
            ]
            return SimpleNamespace(stop_reason="end_turn", parsed_output=MoverReasons(items=items))
        if kw["output_format"] is Week:
            self.week_prompt = kw["messages"][0]["content"]
            text = WeekText(overview="Итог <недели>", themes=["Сюжет"], ahead=["Отчёт CRM"], takeaway="Вывод")
            week = Week(ru=text, uk=text.model_copy(update={"overview": "Підсумок тижня"}), en=text)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=week)
        if kw["output_format"] is Brief:
            self.brief_prompt = kw["messages"][0]["content"]
            text = BriefText(overview="Обзор <дня>", points=["Пункт"], term="ETF", term_explained="Фонд")
            brief = Brief(ru=text, uk=text.model_copy(update={"overview": "Огляд дня"}), en=text)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=brief)
        items = [
            Score(
                index=i, score=8 if "Apple" in block else 2, tickers=["aapl"], sector="tech",
                region="asia" if "Nikkei" in block else "us",
                tone="positive", title_ru="Заголовок по-русски", title_uk="Заголовок українською",
                summary_ru="", summary_uk="", why_ru="Почему важно", why_uk="Чому важливо",
                why_en="Why it matters",
            )
            for i, block in enumerate(blocks)
        ]
        return SimpleNamespace(stop_reason="end_turn", parsed_output=ScoreBatch(items=items))

    def _create(self, **kw):
        self.digest_prompt = kw["messages"][0]["content"]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ДАЙДЖЕСТ")])


def test_score_and_digest(tmp_path):
    cfg = Config(db_path=str(tmp_path / "t.db"), watchlist=["AAPL"])
    conn = db.connect(cfg.db_path)
    for i, title in enumerate(["Apple beats earnings", "Ten stocks to buy now"]):
        assert db.insert_article(
            conn, url=f"https://example.com/{i}", title=title, title_key=title_key(title),
            summary="", source="Test", published=db.now_iso(),
        )
    # повторная вставка того же URL игнорируется
    assert not db.insert_article(
        conn, url="https://example.com/0", title="x", title_key="x", summary="", source="Test",
        published=db.now_iso(),
    )
    client = FakeClient()
    assert score_articles(cfg, conn, client=client) == 2
    assert score_articles(cfg, conn, client=client) == 0

    text, ids = build_digest(cfg, conn, client=client)
    assert text == "ДАЙДЖЕСТ"
    assert len(ids) == 1  # вторая новость ниже порога важности
    assert "Apple beats earnings" in client.digest_prompt
    assert "Ten stocks" not in client.digest_prompt
    assert "AAPL" in client.digest_prompt

    db.mark_digested(conn, ids)
    assert build_digest(cfg, conn, client=client) == ("", [])


def test_site(tmp_path):
    from finnews import site

    cfg = Config(db_path=str(tmp_path / "t.db"))
    conn = db.connect(cfg.db_path)
    for url, title, published in (
        ("https://example.com/new", "Fed <cuts> rates", db.now_iso()),
        ("https://example.com/old", "Stale story", "2020-01-01T00:00:00+00:00"),
        ("javascript:alert(1)", "Bad link", db.now_iso()),
    ):
        db.insert_article(
            conn, url=url, title=title, title_key=title_key(title), summary="", source="Test",
            published=published,
        )
    path = site.build(cfg, conn, out_dir=str(tmp_path / "site"))
    page = path.read_text(encoding="utf-8")
    assert "Fed &lt;cuts&gt; rates" in page
    assert "Stale story" not in page
    assert "javascript:" not in page
    assert "2 новости за 24 часа" in page
    assert "2 новини за 24 години" in page
    assert "2 stories in 24 hours" in page
    assert 'class="brief"' not in page  # без оценки нет ни обзора, ни сортировки по важности
    assert "data-sort" not in page


def test_site_translated(tmp_path):
    from finnews import site

    cfg = Config(db_path=str(tmp_path / "t.db"))
    conn = db.connect(cfg.db_path)
    db.insert_article(
        conn, url="https://example.com/a", title="Apple beats earnings", title_key="apple beats earnings",
        summary="Original summary", source="Test", published=db.now_iso(),
    )
    client = FakeClient()
    assert score_articles(cfg, conn, client=client) == 1
    assert build_brief(cfg, conn, client=client)

    page = site.render(cfg, conn)
    # заголовок на трёх языках, ссылка ведёт на оригинал
    assert '<span data-l="ru">Заголовок по-русски</span>' in page
    assert '<span data-l="uk">Заголовок українською</span>' in page
    assert '<span data-l="en">Apple beats earnings</span>' in page
    assert 'href="https://example.com/a"' in page
    assert "Why it matters" in page and "Чому важливо" in page
    assert 'data-group="tech"' in page and 'data-score="8"' in page
    assert "data-sort" in page
    # обзор дня: экранирован и на своём языке
    assert "Обзор &lt;дня&gt;" in page and "Огляд дня" in page
    assert 'class="market' not in page  # без котировок блоков рынка нет

    # архив и словарик: по странице на каждый день с обзором
    out = tmp_path / "site"
    site.build(cfg, conn, out_dir=str(out))
    days =[p.name for p in out.glob("day-*.html")]
    assert len(days) == 1
    archive = (out / "archive.html").read_text(encoding="utf-8")
    assert f'href="{days[0]}"' in archive and "Огляд дня" in archive
    # поиск по архиву: важные новости на трёх языках со ссылкой на оригинал
    index = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert index == [{
        "d": days[0][4:14], "p": True, "u": "https://example.com/a", "s": "Test",
        "ru": "Заголовок по-русски", "uk": "Заголовок українською", "en": "Apple beats earnings",
    }]
    assert 'id="q"' in archive and "search.json" in archive
    glossary = (out / "glossary.html").read_text(encoding="utf-8")
    assert "ETF" in glossary and "Фонд" in glossary
    assert 'class="info"' in glossary and 'role="tooltip"' in glossary  # значок с подсказкой

    # словарик: тот же термин под другим названием — повтор; остаётся самая свежая запись
    def term(name, text):
        part = {"overview": "", "points": [], "term": name, "term_explained": text}
        return json.dumps({"ru": part, "uk": part, "en": part})

    db.save_brief(conn, "2026-01-05", term("CDS (кредитный дефолтный своп)", "старое объяснение"))
    db.save_brief(conn, "2026-01-06", term("Кредитный дефолтный своп (CDS)", "свежее объяснение"))
    db.save_brief(conn, "2026-01-07", term("Дивиденд", "доля прибыли"))
    glossary = site.render_glossary(conn)
    assert glossary.count('class="entry"') == 3  # ETF, CDS один раз, дивиденд
    assert "свежее объяснение" in glossary and "старое объяснение" not in glossary
    # модели напоминают уже объяснённые термины прошлых дней
    client = FakeClient()
    assert build_brief(cfg, conn, client=client)
    assert "уже объяснённые в прошлые дни: Дивиденд; Кредитный дефолтный своп (CDS); CDS" in client.brief_prompt
    day_page = (out / days[0]).read_text(encoding="utf-8")
    assert "Обзор &lt;дня&gt;" in day_page and "Заголовок по-русски" in day_page
    assert 'href="archive.html"' in page and 'href="glossary.html"' in page


def test_events(tmp_path):
    from finnews import site

    cfg = Config(db_path=str(tmp_path / "t.db"))
    conn = db.connect(cfg.db_path)
    for i, (title, source) in enumerate([
        ("Apple beats earnings", "CNBC"),
        ("Apple tops profit forecasts", "MarketWatch"),
        ("Apple results lift suppliers", "Yahoo"),
    ]):
        db.insert_article(
            conn, url=f"https://example.com/{i}", title=title, title_key=title_key(title),
            summary="", source=source, published=db.now_iso(),
        )
    client = FakeClient()
    assert score_articles(cfg, conn, client=client) == 3
    assert group_events(cfg, conn, client=client) == 1
    lead = {r["event"] for r in conn.execute("SELECT event FROM articles")}
    assert len(lead) == 1 and None not in lead  # все три — одно событие с одной главной новостью

    page = site.render(cfg, conn)
    assert page.count("<article") == 1  # повторы не идут отдельными новостями
    also = page.split('class="also"')[1].split("</p>")[0]
    assert also.count("<a ") == 2 and "Ещё об этом:" in also
    assert "3 новости за 24 часа" in page  # счётчик показывает все заметки

    db.set_events(conn, [], hours=24)  # разметка стирается при следующем пересчёте
    assert site.render(cfg, conn).count("<article") == 3


def test_weekly(tmp_path):
    from datetime import date, datetime, timedelta, timezone

    from finnews import market, site

    today = date.today()
    monday = today - timedelta(days=today.weekday())

    def day_ts(day):
        return int(datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc).timestamp())

    def fake_month(symbol):
        if symbol == "BAD":
            raise RuntimeError("нет ответа")
        days = [monday - timedelta(days=4), monday - timedelta(days=3), monday, monday + timedelta(days=1)]
        return {"chart": {"result": [{
            "meta": {"gmtoffset": 0},
            "timestamp": [day_ts(d) for d in days],
            "indicators": {"quote": [{"close": [90.0, 100.0, None, 103.0]}]},
        }]}}

    changes = market.week_changes(["^GSPC", "BAD"], monday, get=fake_month)
    assert changes == {"^GSPC": {"name": "S&P 500", "change": 3.0}}  # 103 против 100 до начала недели

    cfg = Config(db_path=str(tmp_path / "t.db"))
    conn = db.connect(cfg.db_path)
    client = FakeClient()
    assert not build_weekly(cfg, conn, monday, changes=changes, client=client)  # писать не о чем
    db.insert_article(
        conn, url="https://example.com/a", title="Apple beats earnings", title_key="apple beats earnings",
        summary="", source="Test", published=db.now_iso(),
    )
    score_articles(cfg, conn, client=client)
    build_brief(cfg, conn, client=client)
    assert build_weekly(cfg, conn, monday, changes=changes, client=client)
    assert "S&P 500: +3.00%" in client.week_prompt and "Apple beats earnings" in client.week_prompt
    assert "Обзор <дня>" in client.week_prompt  # обзоры дней этой недели идут в запрос

    out = tmp_path / "site"
    site.build(cfg, conn, out_dir=str(out))
    page = (out / f"week-{monday.isoformat()}.html").read_text(encoding="utf-8")
    assert "Итог &lt;недели&gt;" in page and "Підсумок тижня" in page
    assert "+3.00%" in page and "Отчёт CRM" in page and "Вывод" in page
    assert f'href="week-{monday.isoformat()}.html"' in (out / "archive.html").read_text(encoding="utf-8")
    assert f'href="week-{monday.isoformat()}.html"' in (out / "index.html").read_text(encoding="utf-8")


def test_macro(tmp_path):
    import json
    from datetime import date

    from finnews import macro, site

    def fake_bls(payload):
        assert payload["seriesid"] == ["CUUR0000SA0", "LNS14000000"]
        cpi = [("2026", "M13", "330.0"), ("2026", "M10", "330.0"), ("2026", "M09", "327.0"),
               ("2025", "M10", "320.0"), ("2025", "M09", "318.0")]
        jobs = [("2026", "M10", "4.1"), ("2026", "M09", "4.2")]
        rows = lambda data: [{"year": y, "period": p, "value": v} for y, p, v in data]  # noqa: E731
        return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
            {"seriesID": "CUUR0000SA0", "data": rows(cpi)}, {"seriesID": "LNS14000000", "data": rows(jobs)},
        ]}}

    def broken():
        raise RuntimeError("нет ответа")

    def fake_eurostat(dataset, params):
        assert dataset == "prc_hicp_minr" and params["coicop18"] == "TOTAL" and params["unit"] == "RCH_A"
        return {
            "dimension": {"time": {"category": {"index": {"2026-08": 0, "2026-09": 1, "2026-10": 2}}}},
            "value": {"0": 3.2, "1": 3.8, "2": None},
        }

    data = macro.refresh(
        {"ecb": {"value": 2.5, "period": "2026-11-20"}},  # прошлое значение
        sources={
            "fed": lambda: {"low": 3.75, "high": 4.0, "period": "2026-11-24"},
            "bls": lambda: macro.bls(today=date(2026, 11, 27), post=fake_bls),
            "ecb": broken,
            "eu_cpi": lambda: macro.eu_cpi(get=fake_eurostat),
        },
    )
    assert data["eu_cpi"] == {"value": 3.8, "period": "2026-09", "prev": 3.2}  # пустой октябрь пропущен
    assert data["us_cpi"] == {"value": 3.1, "period": "2026-10", "prev": 2.8}  # 330/320 и 327/318
    assert data["us_unemployment"] == {"value": 4.1, "period": "2026-10", "prev": 4.2}
    assert data["ecb"]["value"] == 2.5  # источник не ответил — осталось прежнее значение
    assert "Ставка ФРС: 3.75–4.00%" in macro.summary(data)

    cfg = Config(db_path=str(tmp_path / "t.db"))
    conn = db.connect(cfg.db_path)
    db.put(conn, "macro", json.dumps(data))
    db.insert_article(
        conn, url="https://example.com/a", title="Apple beats earnings", title_key="apple beats earnings",
        summary="", source="Test", published=db.now_iso(),
    )
    client = FakeClient()
    score_articles(cfg, conn, client=client)
    assert build_brief(cfg, conn, client=client)
    assert "Инфляция в США за год: 3.1% (за 2026-10, до этого 2.8%)" in client.brief_prompt

    block = site.render(cfg, conn).split('class="market macro"')[1].split("</section>")[0]
    assert block.count('class="fact"') == 5
    assert "Euro area inflation" in block and "3.8%" in block and "09.2026" in block
    assert "3.75–4.00%" in block and "3.1%" in block and "10.2026" in block and "2.8%" in block
    assert "2.50%" in block and "20.11.2026" in block


def test_earnings(tmp_path):
    import json
    from datetime import date

    from finnews import earnings, site

    def fake_nasdaq(day):
        if day == "2026-12-01":
            raise RuntimeError("нет ответа")  # отказ по одному дню не мешает остальным
        if day != "2026-12-02":
            return {"data": {"rows": None}}
        return {"data": {"rows": [
            {"symbol": "SMALL", "name": "Small Co", "time": "time-pre-market",
             "marketCap": "$900,000,000", "epsForecast": "$1.00"},
            {"symbol": "CRM", "name": "Salesforce <Inc>", "time": "time-after-hours",
             "marketCap": "$159,052,972,000", "epsForecast": "$2.41"},
            {"symbol": "SNOW", "name": "Snowflake Inc.", "time": "time-not-supplied",
             "marketCap": "$95,640,551,000", "epsForecast": "($0.32)"},
            {"symbol": "VOO", "name": "Watched", "time": "", "marketCap": "N/A", "epsForecast": ""},
        ]}}

    cfg = Config(db_path=str(tmp_path / "t.db"), watchlist=["VOO"])
    days = earnings.upcoming(cfg, today=date(2026, 11, 27), get=fake_nasdaq)
    assert [d["date"] for d in days] == ["2026-12-02"]  # выходные и пустые дни опущены
    items = {i["symbol"]: i for i in days[0]["items"]}
    assert set(items) == {"CRM", "SNOW", "VOO"}  # мелкая компания отсеяна, тикер из списка оставлен
    assert items["CRM"]["when"] == "after" and items["CRM"]["eps"] == 2.41
    assert items["SNOW"]["eps"] == -0.32 and items["VOO"]["eps"] is None

    conn = db.connect(cfg.db_path)
    db.save_market(conn, json.dumps({"quotes": {}, "earnings": days}))
    page = site.render(cfg, conn)
    block = page.split('class="market earnings"')[1].split("</section>")[0]
    assert "среда, 2 декабря" in block and "Wednesday, December 2" in block
    assert "Salesforce &lt;Inc&gt;" in block and "после закрытия" in block
    assert "прогноз прибыли на акцию</span>" in block and ": $2.41" in block and ": −$0.32" in block


def test_build_assets(tmp_path):
    from finnews import site

    cfg = Config(db_path=str(tmp_path / "t.db"), site_url="https://example.com/")
    conn = db.connect(cfg.db_path)
    page = site.build(cfg, conn, out_dir=str(tmp_path / "out")).read_text(encoding="utf-8")
    for name in ("favicon.svg", "favicon-32.png", "apple-touch-icon.png", "og.png"):
        assert (tmp_path / "out" / name).stat().st_size > 0
    assert '<link rel="icon" href="favicon.svg" type="image/svg+xml">' in page
    assert '<meta property="og:image" content="https://example.com/og.png">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    # без адреса сайта превью ссылки нет, значок остаётся
    cfg.site_url = ""
    page = site.build(cfg, conn, out_dir=str(tmp_path / "out")).read_text(encoding="utf-8")
    assert "og:image" not in page and 'rel="icon"' in page


def test_filings(tmp_path):
    import json
    from datetime import date

    from finnews import edgar, site

    def fake_sec(url):
        if url == edgar.TICKERS_URL:
            return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
        if "0001045810" in url:
            raise RuntimeError("нет ответа")  # отказ по одной компании не мешает остальным
        assert url == "https://data.sec.gov/submissions/CIK0000320193.json"
        rows = [  # форма, дата, пункты, документ
            ("8-K", "2026-11-25", "2.02,9.01", "a.htm"), ("4", "2026-11-25", "", "x.xml"),
            ("8-K", "2026-11-24", "9.01", "b.htm"), ("10-Q", "2026-11-24", "", "q.htm"),
            ("8-K", "2026-11-23", "6.66", "c.htm"), ("10-K", "2026-11-01", "", "old.htm"),
        ]
        return {"name": "Apple <Inc>", "filings": {"recent": {
            "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
            "items": [r[2] for r in rows], "primaryDocument": [r[3] for r in rows],
            "accessionNumber": [f"0000320193-26-00000{i}" for i in range(len(rows))],
            "acceptanceDateTime": [f"{r[1]}T21:30:00.000Z" for r in rows],
        }}}

    cfg = Config(db_path=str(tmp_path / "t.db"), watchlist=["VOO", "AAPL"])
    assert edgar.recent(cfg) == []  # контакт не задан — к SEC не обращаемся
    items = edgar.recent(cfg, today=date(2026, 11, 26), get=fake_sec)
    # фонд без CIK пропущен; прочие формы, 8-K из одних приложений и старый отчёт отсеяны
    assert [(i["form"], i["date"]) for i in items] == [
        ("8-K", "2026-11-25"), ("10-Q", "2026-11-24"), ("8-K", "2026-11-23")]
    assert items[0]["items"] == ["2.02", "9.01"]
    assert items[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000000/a.htm"

    conn = db.connect(cfg.db_path)
    assert "market earnings filings" not in site.render(cfg, conn)
    items.append({**items[0], "url": "https://example.com/x", "date": "2026-11-26"})  # чужой адрес
    db.put(conn, "filings", json.dumps({"items": items}))
    page = site._filings(json.loads(db.get(conn, "filings")[0]), date(2026, 11, 27))
    assert page.count('class="mover"') == 3 and "example.com" not in page
    assert "Apple &lt;Inc&gt;" in page and "25.11.2026" in page
    assert "результаты квартала" in page and "quarterly results" in page  # пункт 2.02
    assert "квартальный отчёт" in page  # 10-Q
    assert "сообщение о важном событии" in page  # 8-K с незнакомым пунктом
    assert site._filings(json.loads(db.get(conn, "filings")[0]), date(2026, 12, 20)) == ""  # устарели


def test_hours(tmp_path):
    import json
    from datetime import datetime, timezone

    from finnews import hours, market, site

    def utc(*args):
        return int(datetime(*args, tzinfo=timezone.utc).timestamp())

    # четверг 26 ноября 2026 — День благодарения: Нью-Йорк закрыт, в пятницу короткая сессия
    ahead = hours.upcoming(now=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc))
    assert set(ahead) == {"^GSPC", "^FTSE", "^GDAXI", "^N225", "^HSI"}
    assert ahead["^GSPC"][0] == [utc(2026, 11, 27, 14, 30), utc(2026, 11, 27, 18, 0)]
    assert ahead["^GSPC"][1] == [utc(2026, 11, 30, 14, 30), utc(2026, 11, 30, 21, 0)]
    assert ahead["^FTSE"][0] == [utc(2026, 11, 26, 8, 0), utc(2026, 11, 26, 16, 30)]  # сессия идёт
    assert ahead["^N225"][0][0] == utc(2026, 11, 27, 0, 0)  # сегодняшняя уже закончилась

    cfg = Config(db_path=str(tmp_path / "t.db"))
    data = market.snapshot(cfg, get=fake_yahoo)
    data["hours"] = {**ahead, "^HSI": "мусор"}
    conn = db.connect(cfg.db_path)
    db.save_market(conn, json.dumps(data))
    page = site.render(cfg, conn)
    assert page.count('class="exch"') == 5 and page.count("data-sessions=") == 4
    assert f'data-sessions="{utc(2026, 11, 27, 14, 30)}-{utc(2026, 11, 27, 18, 0)},' in page


def fake_yahoo(symbol):
    """Ответ в формате эндпоинта графиков Yahoo; для одного символа — отказ."""
    if symbol == "MSFT":
        raise RuntimeError("нет ответа")
    price = {"^GSPC": 101.0, "AAPL": 110.0, "NVDA": 95.0, "XLK": 99.0}.get(symbol, 100.0)
    kind = "INDEX" if symbol.startswith("^") else "ETF" if symbol.startswith("XL") else "EQUITY"
    return {"chart": {"result": [{
        "meta": {
            "symbol": symbol, "shortName": f"{symbol} <Inc>", "instrumentType": kind,
            "regularMarketPrice": price, "chartPreviousClose": 100.0, "regularMarketTime": 1795640400,
            "currentTradingPeriod": {"regular": {"start": 1795617000, "end": 1795640400}},
            "exchangeTimezoneName": "Asia/Tokyo" if symbol == "^N225" else "America/New_York",
        },
        "indicators": {"quote": [{"close": [100.0, None, 100.5, price]}]},
    }]}}


def test_market(tmp_path):
    import json

    from finnews import market, site

    cfg = Config(db_path=str(tmp_path / "t.db"), watchlist=["AAPL"], schedule=["05:00", "21:30", "bad"])
    data = market.snapshot(cfg, get=fake_yahoo)
    assert set(data["sessions"]) == {"^GSPC", "^FTSE", "^GDAXI", "^N225", "^HSI"}  # биржи с часами
    assert data["sessions"]["^N225"] == {"start": 1795617000, "end": 1795640400, "tz": "Asia/Tokyo"}
    assert data["pulse"] == ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^GDAXI", "^N225", "^HSI", "AAPL"]
    assert "MSFT" not in data["quotes"]  # отказ по одному символу не мешает остальным
    assert data["quotes"]["^GSPC"]["name"] == "S&P 500"
    assert data["quotes"]["AAPL"]["change"] == 10.0
    assert data["quotes"]["AAPL"]["points"] == [100.0, 100.5, 110.0]
    assert "points" not in data["quotes"]["NVDA"]  # график нужен только в «пульсе»

    conn = db.connect(cfg.db_path)
    for i, title in enumerate(["Apple beats earnings", "Fed holds rates", "Nikkei hits record"]):
        db.insert_article(
            conn, url=f"https://example.com/{i}", title=title, title_key=title_key(title),
            summary="", source="Test", published=db.now_iso(),
        )
    assert score_articles(cfg, conn, client=FakeClient()) == 3
    fed =conn.execute("SELECT id FROM articles WHERE title = 'Fed holds rates'").fetchone()["id"]
    db.save_score(conn, fed, 9, [], "", sector="macro")
    db.save_market(conn, json.dumps(data))

    # обзор дня получает котировки, чтобы сверять с ними заголовки
    client = FakeClient()
    assert build_brief(cfg, conn, client=client)
    assert "S&P 500: 101.00, за день +1.00%" in client.brief_prompt
    assert "Nikkei 225: 100.00" in client.brief_prompt  # биржи Европы и Азии тоже
    assert "tech (XLK) -1.00%" in client.brief_prompt and "NVDA -5.00%" in client.brief_prompt

    page = site.render(cfg, conn)
    # часы пяти бирж: пояс и границы сессии, остальное дописывает скрипт
    assert page.count('class="exch"') == 5
    assert 'data-tz="Asia/Tokyo" data-start="1795617000" data-end="1795640400"' in page
    assert 'id="next" data-schedule="05:00,21:30"' in page  # отсчёт; мусор из расписания отброшен
    # регион: метка у новости и фильтр
    assert 'data-region="asia"' in page and 'data-region="us"' in page
    assert '<nav aria-label="region">' in page

    pulse = page.split('class="market pulse"')[1].split("</section>")[0]
    assert pulse.count('class="quote"') == 8 and "<polyline" in pulse
    assert "+1.00%" in pulse and "110.00" in pulse
    movers = page.split('class="market movers"')[1].split("</section>")[0]
    assert movers.count('class="mover"') == 2  # один вырос, один упал; остальные без изменений
    assert "AAPL &lt;Inc&gt;" in movers and "+10.00%" in movers and "−5.00%" in movers
    assert 'href="https://example.com/0"' in movers  # рост AAPL объяснён новостью из ленты
    assert "в ленте новостей об этом нет" in movers  # по NVDA новости нет
    # у сферы «Технологии» — изменение её фонда, у «Экономики» фонда нет
    assert '<span class="chg down">−1.00%</span></button>' in page
    assert page.count("−1.00%") == 1

    assert "data-sessions" not in page  # календаря бирж в снимке нет — часы считаются по будням

    # объяснение движения от модели: по AAPL новость есть, по NVDA спрашивать не о чем
    client = FakeClient()
    assert explain_movers(cfg, conn, client=client) == 1
    assert "AAPL — AAPL <Inc>, за день +10.00%" in client.movers_prompt
    assert "NVDA" not in client.movers_prompt and "Fed holds rates" not in client.movers_prompt
    movers = site.render(cfg, conn).split('class="market movers"')[1].split("</section>")[0]
    assert "Причина &lt;роста&gt;" in movers and "Лишнее" not in movers
    assert 'href="https://example.com/0"' in movers and "Apple beats earnings" not in movers
    assert "в ленте новостей об этом нет" in movers
    # объяснение относится к своему снимку котировок: к новому снимку оно не прикладывается
    db.save_market(conn, json.dumps({**data, "asof": "2026-11-27T00:00:00+00:00"}))
    movers = site.render(cfg, conn).split('class="market movers"')[1].split("</section>")[0]
    assert "Причина" not in movers and "Apple beats earnings" in movers
