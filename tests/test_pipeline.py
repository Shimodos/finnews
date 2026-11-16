from types import SimpleNamespace

from finnews import db
from finnews.collect import clean, is_duplicate, title_key
from finnews.config import Config
from finnews.llm import Score, ScoreBatch, build_digest, score_articles
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
        items = [
            Score(index=i, score=8 if "Apple" in block else 2, tickers=["aapl"], reason="тест")
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
