"""Карточки для Instagram: пост-карусель, отдельный пост о лидерах дня и истории.

Собираются из тех же данных, что и сайт, на трёх языках. Вёрстка каждой карточки — обычный
HTML; в картинку его превращает установленный браузер (Chrome, Chromium или Edge) в режиме
без окна. Браузера нет или он не справился — остаются HTML-страницы, сборка не падает.
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from . import db
from .config import Config
from .market import movers as top_movers
from .site import LANGS, MONTHS, REGIONS, SECTORS, UI, WEEKDAYS, _loads

POST = (1080, 1350)  # пост 4:5
STORY = (1080, 1920)  # история 9:16
NEWS = 3  # сколько карточек новостей в карусели
MOVERS = 2  # сколько выросших и сколько упавших компаний на карточке лидеров

# Строки таблицы «Рынки за день»: название группы (ru, uk, en) и символы. В пост помещается
# семь строк, в историю — девять.
GROUPS = {
    "us": ("США", "США", "US"),
    "abroad": ("Европа и Азия", "Європа й Азія", "Europe & Asia"),
    "commodities": ("Сырьё", "Сировина", "Commodities"),
}
POST_ROWS = (("us", ("^GSPC", "^IXIC")), ("abroad", ("^GDAXI", "^N225", "^HSI")), ("commodities", ("BZ=F", "GC=F")))
STORY_ROWS = (
    ("us", ("^GSPC", "^IXIC", "^DJI")),
    ("abroad", ("^FTSE", "^GDAXI", "^N225", "^HSI")),
    ("commodities", ("BZ=F", "GC=F")),
)
NAMES = {  # короткие названия для сырья; у индексов название приходит с котировкой
    "BZ=F": ("Нефть Brent", "Нафта Brent", "Brent crude"),
    "GC=F": ("Золото", "Золото", "Gold"),
}

TEXT = {
    "main": ("Главное за день", "Головне за день", "Today in the markets"),
    "swipe": ("листайте →", "гортайте →", "swipe →"),
    "why": ("Почему это важно", "Чому це важливо", "Why it matters"),
    "markets": ("Рынки за день", "Ринки за день", "Markets today"),
    "asof": ("на", "на", "as of"),
    "not_advice": (
        "не совет покупать или продавать", "не порада купувати чи продавати", "not advice to buy or sell",
    ),
    "daily": ("Каждый день на сайте", "Щодня на сайті", "Every day on the site"),
    "pitch": ("Новости рынков понятным языком", "Новини ринків зрозумілою мовою", "Market news in plain language"),
    "features": (
        ("Обзор дня в трёх предложениях", "Каждая новость с оценкой важности",
         "Котировки, сырьё, валюты", "Русский, украинский, английский"),
        ("Огляд дня у трьох реченнях", "Кожна новина з оцінкою важливості",
         "Котирування, сировина, валюти", "Українська, англійська, російська"),
        ("The day in three sentences", "Every story scored by importance",
         "Quotes, commodities, currencies", "English, Ukrainian, Russian"),
    ),
    "bio": ("ссылка в профиле", "посилання в профілі", "link in bio"),
    "more": ("Подробнее", "Докладніше", "More"),
    "tags": (
        "#инвестиции #финансы #фондовыйрынок #акции #новостирынков",
        "#інвестиції #фінанси #фондовийринок #акції #новиниринків",
        "#investing #stockmarket #finance #marketnews #investingforbeginners",
    ),
}

LOGO = (
    '<svg viewBox="0 0 36 36" fill="none" stroke="#fff" stroke-width="3.6" stroke-linecap="round"'
    ' stroke-linejoin="round"><path d="M5 25l8-8 6 5 12-13"/></svg>'
)

CSS = """
:root {
  --bg: #fbfaf7; --text: #171612; --muted: #7d7a70; --line: #e6e3da; --accent: #d4412b;
  --up: #2f7d52; --down: #c0392b; --upbg: #e4f0e8; --downbg: #f8e3df; --soft: #f2efe7;
  --sans: "Golos Text", system-ui, sans-serif; --mono: "JetBrains Mono", ui-monospace, monospace;
}
.dark {
  --bg: #111110; --text: #edebe5; --muted: #8b887e; --line: #2b2a25; --accent: #ff6a4d;
  --up: #5fbf8a; --down: #ff7a66; --upbg: #1c2b22; --downbg: #32201c; --soft: #1b1a17;
}
* { box-sizing: border-box; margin: 0; }
body { background: #d9d6cc; font-family: var(--sans); }
.card {
  width: 1080px; height: 1350px; padding: 76px 80px 70px; background: var(--bg); color: var(--text);
  display: flex; flex-direction: column; position: relative; overflow: hidden;
}
.story { height: 1920px; padding: 230px 80px 330px; }
.head { display: flex; align-items: center; gap: 18px; font-family: var(--mono); font-size: 26px; color: var(--muted); letter-spacing: .02em; }
.logo { width: 56px; height: 56px; border-radius: 14px; background: var(--accent); display: grid; place-items: center; flex: none; }
.logo svg { width: 36px; height: 36px; }
.brand { font-family: var(--sans); font-weight: 600; font-size: 34px; color: var(--text); letter-spacing: -.01em; }
.head .date { margin-left: auto; text-transform: uppercase; }
.foot { margin-top: auto; display: flex; align-items: center; font-family: var(--mono); font-size: 26px; color: var(--muted); border-top: 2px solid var(--line); padding-top: 30px; }
.foot b { color: var(--text); font-weight: 500; }
.foot .next { margin-left: auto; color: var(--accent); }
.kicker { font-family: var(--mono); font-size: 28px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
.body { margin-top: 70px; display: flex; flex-direction: column; flex: 1; }
.body.tight { margin-top: 50px; }
h1 { font-size: 92px; line-height: 1.04; font-weight: 600; letter-spacing: -.025em; margin-top: 30px; }
h1.long { font-size: 78px; }
h2 { font-size: 68px; line-height: 1.08; font-weight: 600; letter-spacing: -.02em; margin-top: 28px; }
h2.long { font-size: 56px; }
p.lead { font-size: 40px; line-height: 1.38; margin-top: 44px; }
p.txt { font-size: 38px; line-height: 1.42; margin-top: 36px; }
.muted { color: var(--muted); }
.up { color: var(--up); } .down { color: var(--down); }
.chips { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 54px; }
.chip { font-family: var(--mono); font-size: 30px; padding: 14px 22px; border-radius: 14px; background: var(--soft); }
.chip.up { background: var(--upbg); } .chip.down { background: var(--downbg); }
.why { margin-top: 48px; padding: 36px 40px; background: var(--soft); border-left: 8px solid var(--accent); border-radius: 0 18px 18px 0; }
.why .label { font-family: var(--mono); font-size: 24px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
.why p { font-size: 36px; line-height: 1.4; margin-top: 14px; }
.score { display: inline-flex; align-items: baseline; gap: 6px; font-family: var(--mono); font-size: 28px; color: var(--muted); margin-left: auto; white-space: nowrap; }
.score b { font-size: 44px; color: var(--accent); font-weight: 500; }
.row { display: flex; align-items: center; gap: 20px; }
.group { font-family: var(--mono); font-size: 24px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-top: 38px; padding-bottom: 12px; border-bottom: 2px solid var(--line); display: flex; }
.group span { margin-left: auto; text-transform: none; letter-spacing: .02em; }
.q { display: flex; align-items: center; padding: 20px 0; border-bottom: 2px solid var(--line); font-size: 40px; font-weight: 500; }
.q .price { margin-left: auto; font-family: var(--mono); font-size: 30px; color: var(--muted); font-weight: 400; }
.q .pct { width: 210px; margin-left: 30px; text-align: center; font-family: var(--mono); font-size: 34px; padding: 10px 0; border-radius: 14px; font-weight: 500; }
.q .pct.up { background: var(--upbg); } .q .pct.down { background: var(--downbg); }
.mv { padding: 30px 0; border-bottom: 2px solid var(--line); }
.mv:first-of-type { margin-top: 20px; }
.mv .top { display: flex; align-items: baseline; gap: 20px; }
.mv .tk { font-family: var(--mono); font-size: 40px; font-weight: 500; }
.mv .nm { font-size: 30px; color: var(--muted); }
.mv .pc { margin-left: auto; font-family: var(--mono); font-size: 44px; font-weight: 500; }
.mv p { font-size: 32px; line-height: 1.36; margin-top: 12px; }
.big { font-size: 150px; line-height: 1.02; font-weight: 600; letter-spacing: -.03em; margin-top: 36px; }
.big.s2 { font-size: 112px; } .big.s3 { font-size: 84px; } .big.s4 { font-size: 64px; }
.cta { margin-top: 60px; align-self: flex-start; background: var(--accent); color: #fff; font-size: 44px; font-weight: 600; padding: 30px 46px; border-radius: 22px; }
.list { margin-top: 50px; font-size: 40px; line-height: 1.5; }
.list.small { font-size: 38px; line-height: 1.36; }
.list div { padding: 18px 0; border-bottom: 2px solid var(--line); display: flex; gap: 24px; }
.list div i { font-style: normal; font-family: var(--mono); color: var(--accent); font-size: 30px; padding-top: 10px; }
body.all { padding: 40px; }
body.all .sheet { display: flex; flex-wrap: wrap; gap: 40px; zoom: .3; }
body.all .cap { font: 500 60px var(--sans); margin-bottom: 20px; color: #333; }
body.one { background: none; }
body.one .wrap { display: none; } body.one .wrap.on { display: block; } body.one .cap { display: none; }
"""

# Без параметра страница показывает все карточки уменьшенными; с ?c=имя — одну в полный размер.
JS = """
const c = new URLSearchParams(location.search).get('c');
const one = c && document.querySelector('.wrap[data-id="' + c + '"]');
if (one) { document.body.className = 'one'; one.classList.add('on'); }
else document.body.className = 'all';
"""

FONTS = (
    "https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500&display=swap"
)


def _clip(text: str, limit: int) -> str:
    """Укорачивает текст до limit знаков: по границе предложения, а если не выходит — по слову."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:—-") + "…"


def _first(text: str) -> str:
    """Первое предложение."""
    return re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split()), maxsplit=1)[0]


def _topic(point: str) -> tuple[str, str]:
    """Пункт обзора вида «Китай: Госсовет заявил…» -> («Китай», «Госсовет заявил…»)."""
    head, sep, rest = str(point).partition(": ")
    return (head, rest) if sep and len(head) <= 32 else ("", str(point))


def _pct(change: float) -> tuple[str, str]:
    cls = "up" if change > 0 else "down" if change < 0 else ""
    return cls, f"{change:+.2f}%".replace("-", "−")


def _day(quote: dict, i: int) -> str:
    """День последней сделки: 25.11 (в английском — Nov 25)."""
    try:
        moment = datetime.fromisoformat(quote["time"]).astimezone()
    except (KeyError, TypeError, ValueError):
        return ""
    return f"{MONTHS['en'][moment.month - 1][:3]} {moment.day}" if LANGS[i] == "en" else f"{moment:%d.%m}"


def _news(cfg: Config, conn, hours: int) -> list[dict]:
    """Самые важные новости суток, по одной на событие; тексты — на трёх языках."""
    seen, items = set(), []
    for row in db.top_recent(conn, min_score=cfg.min_score, hours=hours, limit=30):
        key = row["event"] or row["id"]
        if key in seen:
            continue
        seen.add(key)
        i18n = _loads(row["i18n"])

        def text(lang: str, field: str, fallback: str = "") -> str:
            return str((i18n.get(lang) or {}).get(field) or fallback or "")

        items.append({
            "score": row["score"], "sector": row["sector"], "region": row["region"],
            "title": [text("ru", "title", row["title"]), text("uk", "title", row["title"]), row["title"]],
            "summary": [text("ru", "summary", row["summary"]), text("uk", "summary", row["summary"]),
                        row["summary"] or ""],
            "why": [text("ru", "why", row["reason"]), text("uk", "why"), text("en", "why")],
        })
        if len(items) == NEWS:
            break
    return items


def collect(cfg: Config, conn, *, hours: int = 24) -> dict:
    """Всё, из чего собираются карточки: обзор дня, котировки, важные новости, лидеры дня."""
    brief = _loads(db.latest_brief(conn, hours=hours))
    market = _loads(db.latest_market(conn, hours=hours))
    reasons = _loads(db.get(conn, "movers")[0])
    return {
        "now": datetime.now().astimezone(),
        "host": urlparse(cfg.site_url).netloc or "finnews.app",
        "brief": brief if all(isinstance(brief.get(lang), dict) for lang in LANGS) else {},
        "market": market,
        "quotes": market.get("quotes") if isinstance(market.get("quotes"), dict) else {},
        "news": _news(cfg, conn, hours),
        # Объяснения лидеров годятся только для того снимка котировок, по которому написаны.
        "reasons": (reasons.get("items") or {}) if reasons.get("asof") == market.get("asof") else {},
    }


def _headline(data: dict, lang: str) -> tuple[str, str]:
    """Заголовок дня и подводка; у старых обзоров их нет — тогда первое предложение обзора."""
    b = data["brief"].get(lang) or {}
    headline = str(b.get("headline") or "").strip() or _first(b.get("overview", ""))
    return _clip(headline, 110).rstrip("."), _clip(b.get("lead", ""), 170)


def _head(right: str) -> str:
    return (
        f'<div class="head"><span class="logo">{LOGO}</span><span class="brand">finnews</span>'
        f'<span class="date">{escape(right)}</span></div>'
    )


def _foot(left: str, right: str = "→") -> str:
    return f'<div class="foot">{left}<span class="next">{escape(right)}</span></div>'


def _quotes_table(data: dict, i: int, layout) -> str:
    quotes, parts = data["quotes"], []
    for group, symbols in layout:
        rows = [(s, quotes[s]) for s in symbols if s in quotes]
        if not rows:
            continue
        days = list(dict.fromkeys(filter(None, (_day(q, i) for _, q in rows))))
        when = f'{TEXT["asof"][i]} {" / ".join(days)}' if days else ""
        parts.append(f'<div class="group">{escape(GROUPS[group][i])} <span>{escape(when)}</span></div>')
        for symbol, q in rows:
            name = NAMES[symbol][i] if symbol in NAMES else q["name"]
            price = f'${q["price"]:,.2f}' if symbol in NAMES else f'{q["price"]:,.2f}'
            cls, pct = _pct(q["change"])
            parts.append(
                f'<div class="q">{escape(name)}<span class="price">{price}</span>'
                f'<span class="pct {cls}"><span class="{cls}">{pct}</span></span></div>'
            )
    return "".join(parts)


def cards(data: dict, lang: str) -> list[tuple[str, tuple[int, int], str]]:
    """Карточки на одном языке: (имя, размер, HTML). Блоки без данных пропускаются."""
    i = LANGS.index(lang)
    now, host = data["now"], escape(data["host"])
    date = f"{WEEKDAYS[lang][now.weekday()]} · " + (
        f"{MONTHS[lang][now.month - 1]} {now.day}" if lang == "en" else f"{now.day} {MONTHS[lang][now.month - 1]}"
    )
    brief = data["brief"].get(lang) or {}
    quotes = data["quotes"]
    site = f"<b>{host}</b>"
    carousel: list[tuple[str, str]] = []  # (имя, содержимое) — в шапку потом встаёт «2 / 6»

    if brief:
        headline, lead = _headline(data, lang)
        indexes = sorted(
            (q for s, q in quotes.items() if s.startswith("^")), key=lambda q: -abs(q["change"])
        )[:3]
        chips = "".join(
            f'<span class="chip {cls}"><span class="{cls}">{escape(q["name"])} {pct}</span></span>'
            for q in indexes
            for cls, pct in [_pct(q["change"])]
        )
        cover = (
            f'<div class="body"><div class="kicker">{TEXT["main"][i]}</div>'
            f'<h1{" class=long" if len(headline) > 60 else ""}>{escape(headline)}</h1>'
            + (f'<p class="lead muted">{escape(lead)}</p>' if lead else "")
            + (f'<div class="chips">{chips}</div>' if chips else "")
            + "</div>"
        )
    else:
        cover = ""

    for n, item in enumerate(data["news"], 1):
        title = _clip(item["title"][i], 120)
        labels = [
            names[i] for names in (SECTORS.get(item["sector"]), REGIONS.get(item["region"])) if names
        ]
        why = _clip(item["why"][i], 190)
        carousel.append((f"news{n}", (
            f'<div class="body"><div class="row"><span class="kicker">{escape(" · ".join(labels))}</span>'
            f'<span class="score">{escape(UI["score"][i])} <b>{int(item["score"])}</b>/10</span></div>'
            f'<h2{" class=long" if len(title) > 80 else ""}>{escape(title)}</h2>'
            f'<p class="txt">{escape(_clip(item["summary"][i], 240))}</p>'
            + (f'<div class="why"><div class="label">{TEXT["why"][i]}</div><p>{escape(why)}</p></div>' if why else "")
            + "</div>"
        )))

    table = _quotes_table(data, i, POST_ROWS)
    if table:
        carousel.append(("markets", f'<div class="body tight"><div class="kicker">{TEXT["markets"][i]}</div>{table}</div>'))

    term = str(brief.get("term") or "").strip()
    if term:
        size = "" if len(term) <= 8 else " s2" if len(term) <= 14 else " s3" if len(term) <= 24 else " s4"
        carousel.append(("term", (
            f'<div class="body"><div class="kicker">{escape(UI["term"][i])}</div>'
            f'<div class="big{size}">{escape(term)}</div>'
            f'<p class="lead">{escape(_clip(brief.get("term_explained", ""), 420))}</p></div>'
        )))

    features = "".join(f"<div><i>{n:02d}</i>{escape(t)}</div>" for n, t in enumerate(TEXT["features"][i], 1))
    carousel.append(("final", (
        f'<div class="body"><div class="kicker">{TEXT["daily"][i]}</div><h2>{TEXT["pitch"][i]}</h2>'
        f'<div class="list">{features}</div><span class="cta">{host} · {TEXT["bio"][i]}</span></div>'
    )))

    result = []
    total = len(carousel) + (1 if cover else 0)
    if cover:
        result.append(("cover", POST, f'<div class="card">{_head(date)}{cover}{_foot(site, TEXT["swipe"][i])}</div>'))
    for n, (name, body) in enumerate(carousel, 2 if cover else 1):
        dark = " dark" if name == "term" else ""
        if name == "final":
            foot = f'<div class="foot">{escape(UI["disclaimer"][i])}</div>'
        elif name == "term":
            foot = _foot(f'{site}&nbsp;· {escape(UI["nav_glossary"][i].lower())}')
        else:
            foot = _foot(site)
        result.append((name, POST, f'<div class="card{dark}">{_head(f"{n} / {total}")}{body}{foot}</div>'))

    movers = []
    # Без единого объяснения карточка пуста по смыслу — тогда её нет.
    for symbol in top_movers(data["market"], MOVERS) if data["reasons"] else []:
        q = quotes[symbol]
        why = data["reasons"].get(symbol)
        text = str(why.get(lang) or "").strip() if isinstance(why, dict) else ""
        cls, pct = _pct(q["change"])
        movers.append(
            f'<div class="mv"><div class="top"><span class="tk">{escape(symbol)}</span>'
            f'<span class="nm">{escape(_clip(q["name"], 30))}</span><span class="pc {cls}">{pct}</span></div>'
            f'<p>{escape(_clip(text, 130) if text else UI["no_reason"][i])}</p></div>'
        )
    if movers:
        result.append(("movers", POST, (
            f'<div class="card">{_head(date)}<div class="body tight">'
            f'<div class="kicker">{escape(UI["movers"][i])}</div>{"".join(movers)}</div>'
            f'{_foot(site, TEXT["not_advice"][i])}</div>'
        )))

    if brief:
        points = "".join(
            f"<div><i>{n:02d}</i><span>{f'<b>{escape(topic)}</b>: ' if topic else ''}"
            f"{escape(_clip(_first(text), 120))}</span></div>"
            for n, (topic, text) in enumerate(map(_topic, (brief.get("points") or [])[:3]), 1)
        )
        result.append(("story-main", STORY, (
            f'<div class="card story">{_head(date)}<div class="body">'
            f'<div class="kicker">{TEXT["main"][i]}</div>'
            f'<h1{" class=long" if len(headline) > 60 else ""}>{escape(headline)}</h1>'
            f'<div class="list small">{points}</div></div></div>'
        )))
    table = _quotes_table(data, i, STORY_ROWS)
    if table:
        result.append(("story-markets", STORY, (
            f'<div class="card story dark">{_head(date)}<div class="body">'
            f'<div class="kicker">{TEXT["markets"][i]}</div>{table}</div></div>'
        )))
    return result


def caption(data: dict, lang: str) -> str:
    """Подпись к посту: заголовок дня, подводка, главные события, адрес сайта, хештеги."""
    i = LANGS.index(lang)
    brief = data["brief"].get(lang) or {}
    lines = []
    if brief:
        headline, lead = _headline(data, lang)
        lines += [headline, "", *([lead, ""] if lead else [])]
        for topic, text in map(_topic, (brief.get("points") or [])[:3]):
            lines.append(f"— {f'{topic}: ' if topic else ''}{_clip(_first(text), 160)}")
        lines.append("")
    lines += [f'{TEXT["more"][i]}: {data["host"]} ({TEXT["bio"][i]})', UI["disclaimer"][i], "", TEXT["tags"][i]]
    return "\n".join(lines)


def page(items, lang: str) -> str:
    """Страница со всеми карточками одного языка; ?c=имя оставляет одну — её и снимает браузер."""
    sheet = "".join(
        f'<div class="wrap" data-id="{name}"><div class="cap">{name}</div>{html}</div>' for name, _, html in items
    )
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex">'
        f'<link rel="stylesheet" href="{FONTS}"><style>{CSS}</style></head>'
        f'<body><div class="sheet">{sheet}</div><script>{JS}</script></body></html>'
    )


def index(data: dict, files: dict) -> str:
    """Страница для владельца сайта: картинки и подписи на каждом языке — скачать и выложить."""
    sections = []
    for lang in LANGS:
        thumbs = "".join(
            f'<a href="{escape(href)}" {"download" if href.endswith(".png") else ""}>'
            + (f'<img src="{escape(href)}" alt="{name}" loading="lazy">' if href.endswith(".png") else name)
            + "</a>"
            for name, href in files[lang]
        )
        sections.append(
            f"<h2>{lang}</h2><div class=\"thumbs\">{thumbs}</div>"
            f'<textarea readonly rows="12">{escape(caption(data, lang))}</textarea>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>finnews — cards</title>'
        "<style>body{font:16px system-ui,sans-serif;margin:24px;background:#fbfaf7;color:#171612}"
        ".thumbs{display:flex;flex-wrap:wrap;gap:12px}.thumbs img{width:180px;border:1px solid #e6e3da;display:block}"
        "textarea{width:100%;max-width:720px;margin-top:12px;font:14px ui-monospace,monospace}</style></head>"
        f'<body><h1>finnews — cards · {data["now"]:%Y-%m-%d %H:%M}</h1>{"".join(sections)}</body></html>'
    )


def find_browser() -> str | None:
    """Chrome, Chromium или Edge. Путь можно задать сам — переменной FINNEWS_BROWSER."""
    custom = os.getenv("FINNEWS_BROWSER")
    if custom:
        return custom if Path(custom).exists() else shutil.which(custom)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(path).exists():
            return path
    return None


def screenshot(browser: str, url: str, path: Path, size: tuple[int, int]) -> bool:
    """Снимает страницу в PNG браузером без окна. False — картинка не получилась."""
    args = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=6000",
        f"--window-size={size[0]},{size[1]}", f"--screenshot={path}", url,
    ]
    if sys.platform.startswith("linux"):
        # На серверах сборки Chrome без этого флага не запускается; страница при этом наша же.
        args.insert(1, "--no-sandbox")
    try:
        subprocess.run(args, capture_output=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return False
    return path.exists() and path.stat().st_size > 0


def build(cfg: Config, conn, *, hours: int = 24, out_dir: str = "site", shot=None) -> tuple[int, int]:
    """Пишет site/cards: страницы карточек, картинки и index.html с подписями.

    shot(url, path, size) -> bool снимает картинку; по умолчанию — найденным браузером.
    Возвращает (карточек, картинок).
    """
    out = (Path(out_dir) / "cards").resolve()  # браузеру нужен полный путь к картинке
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    if shot is None:
        browser = find_browser()
        shot = (lambda url, path, size: screenshot(browser, url, path, size)) if browser else None
    data = collect(cfg, conn, hours=hours)
    files: dict = {}
    total = images = 0
    for lang in LANGS:
        items = cards(data, lang)
        sheet = out / f"{lang}.html"
        sheet.write_text(page(items, lang), encoding="utf-8")
        files[lang] = []
        for n, (name, size, _) in enumerate(items, 1):
            total += 1
            target = out / f"{lang}-{n:02d}-{name}.png"
            if shot and shot(f"{sheet.resolve().as_uri()}?c={name}", target, size):
                images += 1
                files[lang].append((name, target.name))
            else:
                if shot and not images:
                    shot = None  # первая же картинка не получилась — дальше не пробуем
                files[lang].append((name, f"{lang}.html?c={name}"))
    (out / "index.html").write_text(index(data, files), encoding="utf-8")
    return total, images
