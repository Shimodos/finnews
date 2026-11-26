import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel

from . import db, macro, market
from .config import Config

log = logging.getLogger(__name__)

BATCH = 15  # перевод на два языка заметно увеличивает ответ, поэтому пакеты небольшие

SCORE_SYSTEM = """Ты редактор ленты финансовых новостей для начинающего частного инвестора, \
который покупает акции и ETF на долгий срок.

Для каждой новости из списка оцени важность по шкале 0-10:
- 9-10: событие, которое двигает весь рынок (решение по ставке ФРС, ЕЦБ, Банка Англии, \
Банка Японии или Народного банка Китая, данные по инфляции или занятости, кризис, крупное \
регуляторное решение);
- 7-8: важное для сектора или крупной компании (отчёт о прибыли, слияние, смена прогноза), \
либо любая значимая новость о тикере из списка инвестора;
- 4-6: полезный контекст, но без заметных последствий;
- 0-3: шум — мнения без фактов, советы «что купить сейчас», кликбейт, личные финансы, \
реклама, повтор уже известного.

tickers — биржевые тикеры компаний и фондов, которых новость касается напрямую (может быть пусто).
Новости приходят из США, Европы и Азии; шкала важности для всех регионов одна.
region — чей рынок новость затрагивает в первую очередь: us (США), europe (Европа, включая \
Великобританию), asia (Азия и Тихий океан), world (несколько регионов сразу или остальной мир).
sector — сфера: macro (экономика, центробанки, ставки, инфляция), markets (рынок в целом, индексы), \
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

BRIEF_SYSTEM = """Ты объясняешь начинающему инвестору, что сегодня происходит на мировых \
рынках: в США, Европе и Азии. Начинай с того, что сильнее всего влияет на картину в целом; \
если в Европе или Азии за сутки было что-то важное, это должно попасть в обзор. \
Он покупает акции и ETF на долгий срок и хочет понимать картину, а не получать торговые сигналы.

Тебе дают отобранные новости за последние сутки с оценкой важности. Используй только факты \
из этих новостей, не придумывай цифры и события.

Перед новостями могут идти котировки — это точные данные бирж; у каждого индекса указано, \
на какое время они сняты (биржи разных стран работают в разные часы). Если они есть:
- куда и насколько двигался рынок за день, бери только из котировок, а не из заголовков;
- цифра из заголовка, которая расходится с котировками, обычно относится к другому периоду \
(неделя, месяц, от максимума) — тогда либо прямо назови этот период, либо не приводи цифру;
- не пиши, что рынок вырос или упал, если котировки показывают обратное;
- если последняя сессия была не сегодня (выходной или праздник), скажи об этом.

Могут идти и показатели экономики (ставки, инфляция, безработица) — это официальная статистика. \
Опирайся на неё, когда объясняешь новости о ставках и ценах, но не выдавай давно известную \
цифру за событие дня.

Верни один и тот же обзор на трёх языках — ru (русский), uk (украинский), en (английский):
- overview — 2-3 предложения: общая картина дня, что двигает рынок и в какую сторону.
- points — от 3 до 5 коротких пунктов: самые важные события и почему каждое из них важно \
для обычного инвестора.
- term — один термин, который встретился в новостях. Если дан список терминов, уже \
объяснённых в прошлые дни, выбери термин не из него — в том числе не тот же термин под другим \
названием или в виде сокращения.
- term_explained — его объяснение в 2-3 предложениях простыми словами.

Правила: простой язык; не давай советов покупать или продавать конкретные бумаги; \
названия компаний и тикеры не переводи; это чистовой текст — без оговорок, самоисправлений \
и пометок для себя."""

WEEKLY_SYSTEM = """Ты пишешь обзор недели на мировых рынках для начинающего инвестора. \
Он покупает акции и ETF на долгий срок и хочет понять, чем эта неделя запомнится и что из неё \
вынести, а не получать торговые сигналы.

Тебе дают: изменение индексов за неделю (точные данные бирж), обзоры каждого дня этой недели, \
самые важные новости недели, показатели экономики и календарь отчётов на ближайшие дни. \
Используй только эти сведения, не придумывай цифры и события. Куда и насколько двигался рынок \
за неделю, бери только из недельных изменений индексов. Какие-то дни недели могут отсутствовать — \
тогда пиши по тому, что есть, и не делай вид, что знаешь остальное.

Верни один и тот же обзор на трёх языках — ru (русский), uk (украинский), en (английский):
- overview — 3-4 предложения: чем закончилась неделя и что было её главной темой.
- themes — от 3 до 5 пунктов: главные сюжеты недели, каждый — что произошло и почему это \
важно для обычного инвестора. Объединяй события одного сюжета за разные дни.
- ahead — от 2 до 4 пунктов: на что смотреть на следующей неделе (отчёты из календаря, \
ожидаемые решения и данные, о которых говорилось в новостях). Если опереться не на что, \
дай меньше пунктов.
- takeaway — 1-2 предложения: какой общий урок о том, как устроен рынок, показывает эта неделя.

Правила: простой язык; не давай советов покупать или продавать конкретные бумаги; \
названия компаний и тикеры не переводи; это чистовой текст — без оговорок, самоисправлений \
и пометок для себя."""

WEEKLY_NEWS_LIMIT = 40
TERMS_LIMIT = 90  # сколько последних «терминов дня» напоминать модели, чтобы она их не повторяла

EVENTS_SYSTEM = """Тебе дают пронумерованный список заголовков финансовых новостей за сутки. \
Найди новости об одном и том же событии: одно заявление, один отчёт, одно решение, одна сделка, \
пересказанные разными источниками или в нескольких заметках.

Новости на общую тему, но о разных событиях, не объединяй: два разных выступления \
представителей ФРС — это два события. Сомневаешься — не объединяй.

Верни только группы из двух и более новостей — номера index. Каждая новость входит \
максимум в одну группу. Если повторов нет, верни пустой список."""

EVENTS_MIN_SCORE = 4  # шум (0-3) не склеиваем: он и так внизу ленты
EVENTS_LIMIT = 80

MOVERS_SYSTEM = """Ты объясняешь начинающему инвестору, почему акции компании за день заметно \
выросли или упали. Тебе дают компании — тикер, название, изменение цены за день — и под каждой \
пронумерованные новости о ней за последние сутки.

Для каждой компании напиши одно короткое предложение на трёх языках — ru (русский), \
uk (украинский), en (английский): с чем связано движение. Опирайся только на эти новости: \
ничего не добавляй от себя, не придумывай цифры и события.

Если новости движение не объясняют — они о другом, слишком общие или говорят об обратном \
направлении, — верни для этой компании пустые строки и story = -1. Честное «неизвестно» лучше \
догадки. Не выдавай совпадение по времени за причину: пиши «после…», «на фоне…», если новость \
сама прямо не называет причину движения.

story — index новости, на которую ты опираешься сильнее всего.
Правила: простой язык; не давай советов покупать или продавать; названия компаний и тикеры \
не переводи; смысл на трёх языках одинаковый; это чистовой текст — без оговорок и самоисправлений."""

MOVERS_MIN_SCORE = 4  # как на странице: шумом (0-3) движение не объясняем
MOVERS_STORIES = 5  # сколько новостей о компании показывать модели

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
    region: Literal["us", "europe", "asia", "world"]
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


class WeekText(BaseModel):
    overview: str
    themes: list[str]
    ahead: list[str]
    takeaway: str


class Week(BaseModel):
    ru: WeekText
    uk: WeekText
    en: WeekText


class EventGroup(BaseModel):
    indexes: list[int]


class EventGroups(BaseModel):
    groups: list[EventGroup]


class MoverReason(BaseModel):
    symbol: str
    story: int
    ru: str
    uk: str
    en: str


class MoverReasons(BaseModel):
    items: list[MoverReason]


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
                    region=item.region,
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


def explain_movers(
    cfg: Config, conn, *, hours: int = 24, client: anthropic.Anthropic | None = None
) -> int:
    """Объясняет движение лидеров дня по новостям о них. Возвращает число объяснённых компаний.

    Результат лежит в хранилище под ключом movers вместе с отметкой снимка котировок (asof),
    к которому он относится: к другому снимку страница его не приложит.
    """
    try:
        data = json.loads(db.latest_market(conn, hours=hours) or "{}")
    except json.JSONDecodeError:
        return 0
    quotes = data.get("quotes") or {}
    rows = sorted(db.recent(conn, hours=hours), key=lambda r: -(r["score"] or 0))
    stories: list = []  # сквозная нумерация: index новости -> строка базы
    blocks = []
    for symbol in market.movers(data):
        related = [
            r for r in rows
            if (r["score"] or 0) >= MOVERS_MIN_SCORE and symbol in (r["tickers"] or "").split(",")
        ][:MOVERS_STORIES]
        if not related:
            continue
        lines = [f"{symbol} — {quotes[symbol]['name']}, за день {quotes[symbol]['change']:+.2f}%"]
        for r in related:
            lines.append(f"index: {len(stories)}\nзаголовок: {r['title']}\nописание: {r['summary'][:400]}")
            stories.append((symbol, r["id"]))
        blocks.append("\n".join(lines))
    items: dict = {}
    if blocks:
        client = client or anthropic.Anthropic()
        response = client.messages.parse(
            model=cfg.score_model,
            max_tokens=16000,
            output_config={"effort": "low"},
            system=MOVERS_SYSTEM,
            messages=[{"role": "user", "content": "\n\n".join(blocks)}],
            output_format=MoverReasons,
        )
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            log.warning("Движение лидеров не объяснено (stop_reason=%s)", response.stop_reason)
            return 0
        asked = {symbol for symbol, _ in stories}
        for item in response.parsed_output.items:
            symbol = item.symbol.upper()
            if symbol not in asked:
                continue
            texts = {"ru": item.ru.strip(), "uk": item.uk.strip(), "en": item.en.strip()}
            if not all(texts.values()):
                texts = {"ru": "", "uk": "", "en": ""}  # новости движение не объясняют
            # ссылка — только на новость об этой же компании
            if all(texts.values()) and 0 <= item.story < len(stories) and stories[item.story][0] == symbol:
                texts["story"] = stories[item.story][1]
            items[symbol] = texts
    db.put(conn, "movers", json.dumps({"asof": data.get("asof"), "items": items}, ensure_ascii=False))
    conn.commit()
    return sum(1 for texts in items.values() if texts["ru"])


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
    try:
        economy = macro.summary(json.loads(db.get(conn, "macro")[0] or "{}"))
    except (json.JSONDecodeError, KeyError, TypeError):
        economy = ""
    if economy:
        quotes += f"Экономика (официальная статистика):\n{economy}\n\n"
    # Термины прошлых дней — чтобы «термин дня» не повторялся и словарик пополнялся новым.
    # Сегодняшний в список не входит: при пересборке обзора в тот же день он может остаться.
    explained = []
    for brief in db.all_briefs(conn):
        if brief["day"] == now.date().isoformat():
            continue
        try:
            term = str(json.loads(brief["data"])["ru"]["term"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if term and term not in explained:
            explained.append(term)
    if explained:
        quotes += "Термины, уже объяснённые в прошлые дни: " + "; ".join(explained[:TERMS_LIMIT]) + "\n\n"
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


def build_weekly(
    cfg: Config, conn, monday: date, *, changes: dict | None = None,
    client: anthropic.Anthropic | None = None,
) -> bool:
    """Пишет обзор недели, начавшейся в понедельник monday, и сохраняет его в базу.

    changes — недельные изменения индексов из market.week_changes. False — писать не о чем.
    """
    start = datetime.combine(monday, time.min).astimezone()
    rows = db.between(
        conn,
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        (start + timedelta(days=7)).astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    rows = sorted(
        (r for r in rows if (r["score"] or 0) >= cfg.min_score), key=lambda r: -r["score"]
    )[:WEEKLY_NEWS_LIMIT]
    week = {(monday + timedelta(days=i)).isoformat() for i in range(7)}
    days = []
    for brief in reversed(db.all_briefs(conn)):
        if brief["day"] not in week:
            continue
        try:
            text = json.loads(brief["data"])["ru"]
            days.append(f"{brief['day']}: {text['overview']}\n" + "\n".join(f"- {p}" for p in text["points"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    if not rows and not days:
        return False

    parts = [f"Неделя с {monday:%d.%m.%Y} по {monday + timedelta(days=6):%d.%m.%Y}"]
    if changes:
        parts.append("Изменение за неделю:\n" + "\n".join(
            f"{item['name']}: {item['change']:+.2f}%" for item in changes.values()
        ))
    if days:
        parts.append("Обзоры по дням:\n\n" + "\n\n".join(days))
    if rows:
        parts.append("Важные новости недели:\n\n" + "\n\n".join(
            f"[{r['score']}/10] {r['published'][:10]} {r['title']}\nисточник: {r['source']}\n"
            f"описание: {r['summary'][:300]}"
            for r in rows
        ))
    try:
        economy = macro.summary(json.loads(db.get(conn, "macro")[0] or "{}"))
        snapshot = json.loads(db.latest_market(conn, hours=24 * 7) or "{}")
    except (json.JSONDecodeError, KeyError, TypeError):
        economy, snapshot = "", {}
    if economy:
        parts.append(f"Экономика (официальная статистика):\n{economy}")
    reports = [
        f"{day['date']}: " + ", ".join(f"{i['name']} ({i['symbol']})" for i in day["items"])
        for day in snapshot.get("earnings") or []
        if day["date"] > (monday + timedelta(days=4)).isoformat()
    ]
    if reports:
        parts.append("Календарь отчётов на ближайшие дни:\n" + "\n".join(reports))

    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=cfg.digest_model,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=WEEKLY_SYSTEM,
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
        output_format=Week,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise RuntimeError(f"Обзор недели не сгенерирован: stop_reason={response.stop_reason}")
    data = {"changes": changes or {}, "text": response.parsed_output.model_dump()}
    db.put(conn, f"week:{monday.isoformat()}", json.dumps(data, ensure_ascii=False))
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
