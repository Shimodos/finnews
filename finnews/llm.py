import logging
from datetime import datetime, timezone

import anthropic
from pydantic import BaseModel

from . import db
from .config import Config

log = logging.getLogger(__name__)

BATCH = 25

SCORE_SYSTEM = """Ты редактор ленты финансовых новостей для начинающего частного инвестора, \
который покупает акции и ETF на долгий срок.

Для каждой новости из списка оцени важность по шкале 0-10:
- 9-10: событие, которое двигает весь рынок (решение ФРС по ставке, данные по инфляции \
или занятости, кризис, крупное регуляторное решение);
- 7-8: важное для сектора или крупной компании (отчёт о прибыли, слияние, смена прогноза), \
либо любая значимая новость о тикере из списка инвестора;
- 4-6: полезный контекст, но без заметных последствий;
- 0-3: шум — мнения без фактов, советы «что купить сейчас», кликбейт, личные финансы, \
реклама, повтор уже известного.

tickers — биржевые тикеры компаний и фондов, которых новость касается напрямую (может быть пусто).
reason — одна короткая фраза по-русски, почему такая оценка.
Верни оценку для каждого index из списка."""

DIGEST_SYSTEM = """Ты пишешь ежедневный дайджест финансовых новостей на русском языке \
для начинающего инвестора. Он только начинает покупать ценные бумаги и хочет понимать, \
что происходит на рынке и почему это важно, а не получать торговые сигналы.

Тебе дают отобранные новости за последние сутки (заголовок, краткое описание, источник, \
ссылка, оценка важности). Используй только факты из этих новостей, ничего не добавляй \
от себя и не придумывай цифры.

Структура дайджеста:
1. «Главное за день» — 2-3 предложения, общая картина.
2. «События» — от 3 до 7 самых важных событий. Для каждого: что произошло, почему это \
важно для обычного инвестора, ссылка на источник. Похожие новости объединяй в одно событие.
3. «Мой список» — новости о тикерах из списка инвестора. Если таких новостей нет, \
так и напиши одной строкой.
4. «Термин дня» — один термин, который встретился в новостях, с объяснением в 2-3 \
предложениях простыми словами.

Правила:
- Пиши простым языком; термин при первом упоминании коротко поясняй в скобках.
- Не давай советов покупать или продавать конкретные бумаги.
- Формат — обычный текст для Telegram: без Markdown и HTML, заголовки разделов \
отдельной строкой заглавными буквами, ссылки как есть, отдельной строкой.
- Весь дайджест — не длиннее 3500 знаков."""


class Score(BaseModel):
    index: int
    score: int
    tickers: list[str]
    reason: str


class ScoreBatch(BaseModel):
    items: list[Score]


def _text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def score_articles(cfg: Config, conn, client: anthropic.Anthropic | None = None) -> int:
    """Оценивает ещё не оценённые статьи. Возвращает их количество."""
    rows = db.unscored(conn)
    if not rows:
        return 0
    client = client or anthropic.Anthropic()
    watchlist = ", ".join(cfg.watchlist) or "пусто"
    done = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]
        listing = "\n\n".join(
            f"index: {i}\nисточник: {r['source']}\nзаголовок: {r['title']}\nописание: {r['summary'][:400]}"
            for i, r in enumerate(batch)
        )
        response = client.messages.parse(
            model=cfg.score_model,
            max_tokens=16000,
            output_config={"effort": "low"},
            system=SCORE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"Список тикеров инвестора: {watchlist}\n\nНовости:\n\n{listing}",
                }
            ],
            output_format=ScoreBatch,
        )
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            log.warning("Пакет %d не оценён (stop_reason=%s)", start // BATCH, response.stop_reason)
            continue
        for item in response.parsed_output.items:
            if 0 <= item.index < len(batch):
                db.save_score(
                    conn,
                    batch[item.index]["id"],
                    max(0, min(10, item.score)),
                    [t.upper() for t in item.tickers],
                    item.reason,
                )
                done += 1
        conn.commit()
    return done


def build_digest(
    cfg: Config, conn, *, hours: int = 24, client: anthropic.Anthropic | None = None
) -> tuple[str, list[int]]:
    """Возвращает (текст дайджеста, id вошедших статей). Пустой текст — новостей нет."""
    rows = db.for_digest(conn, min_score=cfg.min_score, hours=hours, limit=cfg.max_digest_items)
    if not rows:
        return "", []
    client = client or anthropic.Anthropic()
    listing = "\n\n".join(
        f"[{r['score']}/10] {r['title']}\nисточник: {r['source']}\nтикеры: {r['tickers'] or '-'}\n"
        f"описание: {r['summary'][:600]}\nссылка: {r['url']}"
        for r in rows
    )
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    # fallbacks="default": если модель отклонит запрос по соображениям безопасности,
    # API сам повторит его на запасной модели.
    response = client.beta.messages.create(
        model=cfg.digest_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=DIGEST_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Дата: {today}\nСписок тикеров инвестора: "
                f"{', '.join(cfg.watchlist) or 'пусто'}\n\nНовости:\n\n{listing}",
            }
        ],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Дайджест не сгенерирован: stop_reason={response.stop_reason}")
    return _text(response), [r["id"] for r in rows]
