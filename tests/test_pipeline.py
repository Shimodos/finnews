from types import SimpleNamespace

from finnews import db
from finnews.collect import clean, is_duplicate, title_key
from finnews.config import Config
from finnews.llm import (
    Brief, BriefText, EventGroup, EventGroups, Score, ScoreBatch,
    build_brief, build_digest, group_events, score_articles,
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
    glossary = (out / "glossary.html").read_text(encoding="utf-8")
    assert "ETF" in glossary and "Фонд" in glossary
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
