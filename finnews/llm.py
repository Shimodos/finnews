import json
import logging
from datetime import datetime, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel

from . import db, market
from .config import Config

log = logging.getLogger(__name__)

BATCH = 15  # перевод на два языка заметно увеличивает ответ, поэтому пакеты небольшие

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
sector — сфера: macro (экономика, ФРС, ставки, инфляция), markets (рынок в целом, индексы), \
tech, finance (банки, страхование, финансы), energy (энергетика и сырьё), health, \
consumer (розница, товары и услуги для людей), industry (промышленность, транспорт), crypto, other.
tone — как новость скорее сказывается на затронутых бумагах или рынке: positive, negative \
или neutral. Если из новости это не следует — neutral.
title_ru, title_uk — точный перевод заголовка на русский и украинский, без добавлений.
summary_ru, summary_uk — перевод описания на русский и украинский; если описания нет — пустая строка.
why_ru, why_uk, why_en — одно короткое предложение на русском, украинском и английском: \
что эта новость значит для обычного долгосрочного инвестора. Простыми словами, без советов \
покупать или продавать. Смысл на трёх языках одинаковый.
Названия компаний и тикеры не переводи. Верни оценку для каждого index из списка."""

BRIEF_SYSTEM = """Ты объясняешь начинающему инвестору, что сегодня происходит на рынке США. \
Он покупает акции и ETF на долгий срок и хочет понимать картину, а не получать торговые сигналы.

Тебе дают отобранные новости за последние сутки с оценкой важности. Используй только факты \
из этих новостей, не придумывай цифры и события.

Перед новостями могут идти котировки — это точные данные биржи за последнюю торговую сессию. \
Если они есть:
- куда и насколько двигался рынок за день, бери только из котировок, а не из заголовков;
- цифра из заголовка, которая расходится с котировками, обычно относится к другому периоду \
(неделя, месяц, от максимума) — тогда либо прямо назови этот период, либо не приводи цифру;
- не пиши, что рынок вырос или упал, если котировки показывают обратное;
- если последняя сессия была не сегодня (выходной или праздник), скажи об этом.

Верни один и тот же обзор на трёх языках — ru (русский), uk (украинский), en (английский):
- overview — 2-3 предложения: общая картина дня, что двигает рынок и в какую сторону.
- points — от 3 до 5 коротких пунктов: самые важные события и почему каждое из них важно \
для обычного инвестора.
- term — один термин, который встретился в новостях.
- term_explained — его объяснение в 2-3 предложениях простыми словами.

Правила: простой язык; не давай советов покупать или продавать конкретные бумаги; \
названия компаний и тикеры не переводи."""

EVENTS_SYSTEM = """Тебе дают пронумерованный список заголовков финансовых новостей за сутки. \
Найди новости об одном и том же событии: одно заявление, один отчёт, одно решение, одна сделка, \
пересказанные разными источниками или в нескольких заметках.

Новости на общую тему, но о разных событиях, не объединяй: два разных выступления \
представителей ФРС — это два события. Сомневаешься — не объединяй.

Верни только группы из двух и более новостей — номера index. Каждая новость входит \
максимум в одну группу. Если повторов нет, верни пустой список."""

EVENTS_MIN_SCORE = 4  # шум (0-3) не склеиваем: он и так внизу ленты
EVENTS_LIMIT = 80

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


Sector = Literal[
    "macro", "markets", "tech", "finance", "energy", "health", "consumer", "industry", "crypto", "other"
]


class Score(BaseModel):
    index: int
    score: int
    tickers: list[str]
    sector: Sector
    tone: Literal["positive", "negative", "neutral"]
    title_ru: str
    title_uk: str
    summary_ru: str
    summary_uk: str
    why_ru: str
    why_uk: str
    why_en: str


class ScoreBatch(BaseModel):
    items: list[Score]


class BriefText(BaseModel):
    overview: str
    points: list[str]
    term: str
    term_explained: str


class Brief(BaseModel):
    ru: BriefText
    uk: BriefText
    en: BriefText


class EventGroup(BaseModel):
    indexes: list[int]


class EventGroups(BaseModel):
    groups: list[EventGroup]


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
                    item.why_ru,
                    sector=item.sector,
                    tone=item.tone,
                    i18n=json.dumps(
                        {
                            "ru": {"title": item.title_ru, "summary": item.summary_ru, "why": item.why_ru},
                            "uk": {"title": item.title_uk, "summary": item.summary_uk, "why": item.why_uk},
                            "en": {"why": item.why_en},
                        },
                        ensure_ascii=False,
                    ),
                )
                done += 1
        conn.commit()
    return done


def group_events(
    cfg: Config, conn, *, hours: int = 24, client: anthropic.Anthropic | None = None
) -> int:
    """Находит новости об одном событии и склеивает их. Возвращает число групп."""
    rows = db.top_recent(conn, min_score=EVENTS_MIN_SCORE, hours=hours, limit=EVENTS_LIMIT)
    if len(rows) < 2:
        return 0
    client = client or anthropic.Anthropic()
    listing = "\n\n".join(
        f"index: {i}\nисточник: {r['source']}\nзаголовок: {r['title']}" for i, r in enumerate(rows)
    )
    response = client.messages.parse(
        model=cfg.score_model,
        max_tokens=16000,
        output_config={"effort": "low"},
        system=EVENTS_SYSTEM,
        messages=[{"role": "user", "content": f"Новости:\n\n{listing}"}],
        output_format=EventGroups,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("Повторы не склеены (stop_reason=%s)", response.stop_reason)
        return 0
    used: set[int] = set()
    groups: list[list[int]] = []
    for group in response.parsed_output.groups:
        # rows уже отсортированы по важности и свежести: меньший index — главная новость
        indexes = sorted({i for i in group.indexes if 0 <= i < len(rows) and i not in used})
        if len(indexes) >= 2:
            used.update(indexes)
            groups.append([rows[i]["id"] for i in indexes])
    db.set_events(conn, groups, hours=hours)
    conn.commit()
    return len(groups)


def build_brief(
    cfg: Config, conn, *, hours: int = 24, client: anthropic.Anthropic | None = None
) -> bool:
    """Пишет обзор дня на трёх языках и сохраняет его в базу. False — новостей для обзора нет."""
    rows = db.top_recent(conn, min_score=cfg.min_score, hours=hours, limit=cfg.max_digest_items)
    if not rows:
        return False
    client = client or anthropic.Anthropic()
    listing = "\n\n".join(
        f"[{r['score']}/10] {r['title']}\nисточник: {r['source']}\nтикеры: {r['tickers'] or '-'}\n"
        f"описание: {r['summary'][:600]}"
        for r in rows
    )
    now = datetime.now().astimezone()
    try:
        quotes = market.summary(json.loads(db.latest_market(conn, hours=hours) or "{}"))
    except (json.JSONDecodeError, KeyError, TypeError):
        quotes = ""
    quotes = f"Котировки:\n{quotes}\n\n" if quotes else ""
    response = client.messages.parse(
        model=cfg.digest_model,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=BRIEF_SYSTEM,
        messages=[
            {"role": "user", "content": f"Дата: {now:%d.%m.%Y}\n\n{quotes}Новости:\n\n{listing}"}
        ],
        output_format=Brief,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise RuntimeError(f"Обзор дня не сгенерирован: stop_reason={response.stop_reason}")
    db.save_brief(conn, now.date().isoformat(), response.parsed_output.model_dump_json())
    conn.commit()
    return True


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
