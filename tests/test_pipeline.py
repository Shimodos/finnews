from types import SimpleNamespace

from finnews import db
from finnews.collect import clean, is_duplicate, title_key
from finnews.config import Config
from finnews.llm import Brief, BriefText, Score, ScoreBatch, build_brief, build_digest, score_articles
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
        if kw["output_format"] is Brief:
            text = BriefText(overview="Обзор <дня>", points=["Пункт"], term="ETF", term_explained="Фонд")
            brief = Brief(ru=text, uk=text.model_copy(update={"overview": "Огляд дня"}), en=text)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=brief)
        items = [
            Score(
                index=i, score=8 if "Apple" in block else 2, tickers=["aapl"], sector="tech",
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
