"""Статичная веб-страница с новостями: один файл index.html без сборки и зависимостей.

Все три языка лежат в одной странице; переключатель показывает нужный через атрибут
data-lang у <html>. Заголовок всегда ведёт на оригинал статьи.
"""

import json
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from . import db
from .config import Config

LANGS = ("ru", "uk", "en")

MONTHS = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"],
    "uk": ["січня", "лютого", "березня", "квітня", "травня", "червня",
           "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
}
WEEKDAYS = {
    "ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
    "uk": ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
SECTORS = {
    "macro": ("Экономика и ФРС", "Економіка і ФРС", "Economy & Fed"),
    "markets": ("Рынок в целом", "Ринок загалом", "Broad market"),
    "tech": ("Технологии", "Технології", "Technology"),
    "finance": ("Банки и финансы", "Банки і фінанси", "Banks & finance"),
    "energy": ("Энергетика и сырьё", "Енергетика і сировина", "Energy & commodities"),
    "health": ("Здоровье", "Охорона здоров'я", "Healthcare"),
    "consumer": ("Потребительский сектор", "Споживчий сектор", "Consumer"),
    "industry": ("Промышленность", "Промисловість", "Industrials"),
    "crypto": ("Криптовалюты", "Криптовалюти", "Crypto"),
    "other": ("Другое", "Інше", "Other"),
}
TONES = {
    "positive": ("позитив", "позитив", "positive"),
    "negative": ("негатив", "негатив", "negative"),
}
UI = {
    "all": ("Все", "Усі", "All"),
    "today": ("Сегодня", "Сьогодні", "Today"),
    "yesterday": ("Вчера", "Вчора", "Yesterday"),
    "sort_time": ("Свежие", "Свіжі", "Latest"),
    "sort_score": ("Важные", "Важливі", "Important"),
    "by_score": ("По важности", "За важливістю", "By importance"),
    "brief": ("Главное за день", "Головне за день", "Today in brief"),
    "term": ("Термин дня", "Термін дня", "Term of the day"),
    "score": ("важность", "важливість", "importance"),
    "empty": (
        "Новостей за это время нет. Запусти finnews collect.",
        "Новин за цей час немає. Запусти finnews collect.",
        "No stories for this period. Run finnews collect.",
    ),
    "no_ai": (
        "Перевод, оценка важности и обзор дня появятся после подключения ключа Claude API.",
        "Переклад, оцінка важливості та огляд дня з'являться після підключення ключа Claude API.",
        "Translation, importance scores and the daily brief appear once a Claude API key is set.",
    ),
    "disclaimer": (
        "Сайт объясняет новости и не даёт инвестиционных рекомендаций.",
        "Сайт пояснює новини і не дає інвестиційних рекомендацій.",
        "This site explains the news and does not give investment advice.",
    ),
}

CSS = """
:root {
  --bg: #fbfaf7; --text: #171612; --muted: #7d7a70; --line: #e6e3da; --accent: #d4412b;
  --up: #2f7d52; --down: #c0392b;
  --sans: "Golos Text", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111110; --text: #edebe5; --muted: #8b887e; --line: #262521; --accent: #ff6a4d;
    --up: #5fbf8a; --down: #ff7a66;
  }
}
html[data-lang="ru"] [data-l]:not([data-l="ru"]),
html[data-lang="uk"] [data-l]:not([data-l="uk"]),
html[data-lang="en"] [data-l]:not([data-l="en"]) { display: none !important; }
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 400 17px/1.5 var(--sans);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
main { max-width: 720px; margin: 0 auto; padding: 56px 20px 96px; }
.mark, .stat, nav button, .day h2, .brief h2, time, .meta, .term b, footer {
  font-family: var(--mono); font-size: 12px; letter-spacing: 0.02em;
}
.top { display: flex; justify-content: space-between; align-items: center; margin: 0 0 44px; }
.mark { color: var(--accent); margin: 0; }
nav { display: flex; flex-wrap: wrap; gap: 6px 18px; }
nav button {
  appearance: none; background: none; border: 0; padding: 4px 0; cursor: pointer;
  color: var(--muted); border-bottom: 1px solid transparent;
}
nav button:hover { color: var(--text); }
nav button[aria-pressed="true"] { color: var(--text); border-bottom-color: var(--accent); }
nav button:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.langs { gap: 6px 14px; text-transform: uppercase; }
h1 { font-size: clamp(34px, 7vw, 52px); line-height: 1.05; font-weight: 600; letter-spacing: -0.03em; margin: 0; }
h1 .weekday { display: block; color: var(--muted); font-weight: 400; }
.stat { color: var(--muted); margin: 24px 0 0; }
.note { color: var(--muted); font-size: 15px; margin: 12px 0 0; }
.brief { margin: 48px 0 0; padding: 0 0 8px; }
.brief h2, .day h2 {
  margin: 0; padding: 12px 0; border-bottom: 1px solid var(--text);
  font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em;
}
.brief .overview { font-size: 21px; line-height: 1.4; letter-spacing: -0.01em; margin: 20px 0 0; text-wrap: pretty; }
.brief ul { margin: 20px 0 0; padding: 0; list-style: none; }
.brief li { padding: 10px 0 10px 20px; border-top: 1px solid var(--line); position: relative; }
.brief li::before {
  content: ""; position: absolute; left: 2px; top: 20px; width: 6px; height: 6px;
  border-radius: 50%; background: var(--accent);
}
.term { margin: 12px 0 0; padding: 14px 0 0; border-top: 1px solid var(--line); font-size: 15px; color: var(--muted); }
.term b { display: block; font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }
.term strong { color: var(--text); font-weight: 500; }
.controls { display: grid; gap: 10px; margin: 44px 0 0; }
.day h2 { position: sticky; top: 0; z-index: 1; margin-top: 40px; background: var(--bg); }
article {
  display: grid; grid-template-columns: 64px 1fr; gap: 0 12px;
  padding: 20px 0; border-bottom: 1px solid var(--line);
}
time { color: var(--muted); padding-top: 5px; font-variant-numeric: tabular-nums; }
h3 { margin: 0; font-size: 18px; line-height: 1.35; font-weight: 500; letter-spacing: -0.01em; text-wrap: pretty; }
h3 a { color: inherit; text-decoration: none; }
h3 a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 4px; }
h3 a:visited { color: var(--muted); }
.important h3::before {
  content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent); margin-right: 9px; vertical-align: middle; translate: 0 -2px;
}
.why { margin: 6px 0 0; font-size: 15px; }
.summary {
  margin: 6px 0 0; color: var(--muted); font-size: 15px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.meta { margin: 10px 0 0; color: var(--muted); }
.meta b { font-weight: 500; color: var(--text); }
.up { color: var(--up); } .down { color: var(--down); }
.empty { margin: 56px 0 0; color: var(--muted); }
footer { margin: 56px 0 0; color: var(--muted); }
[hidden] { display: none !important; }
@media (max-width: 520px) {
  main { padding-top: 32px; }
  article { grid-template-columns: 1fr; }
  time { padding: 0 0 4px; }
}
"""

# Выбор языка до отрисовки, чтобы страница не мигала: сохранённый, иначе язык браузера.
HEAD_JS = """
(function () {
  var l = null;
  try { l = localStorage.getItem('finnews-lang'); } catch (e) {}
  if (['ru', 'uk', 'en'].indexOf(l) < 0) {
    var n = (navigator.language || 'ru').toLowerCase();
    l = n.indexOf('uk') === 0 ? 'uk' : n.indexOf('ru') === 0 ? 'ru' : 'en';
  }
  document.documentElement.dataset.lang = l;
  document.documentElement.lang = l;
})();
"""

JS = """
const root = document.documentElement;
const state = { sort: 'time', group: '' };
const arts = [...document.querySelectorAll('article')];
const home = new Map(arts.map(a => [a, a.parentElement]));
const flat = document.getElementById('flat');

function press(buttons, active) {
  buttons.forEach(b => b.setAttribute('aria-pressed', b === active));
}
function apply() {
  const byScore = state.sort === 'score';
  const list = byScore ? [...arts].sort((a, b) => b.dataset.score - a.dataset.score) : arts;
  list.forEach(a => {
    (byScore ? flat : home.get(a)).appendChild(a);
    a.hidden = !!state.group && a.dataset.group !== state.group;
  });
  document.querySelectorAll('.day').forEach(d => { d.hidden = !d.querySelector('article:not([hidden])'); });
}
for (const [attr, key] of [['sort', 'sort'], ['group', 'group']]) {
  const buttons = document.querySelectorAll('[data-' + attr + ']');
  buttons.forEach(b => b.addEventListener('click', () => {
    state[key] = b.dataset[attr];
    press(buttons, b);
    apply();
  }));
}
const langs = document.querySelectorAll('[data-setlang]');
function setLang(l) {
  root.dataset.lang = l;
  root.lang = l;
  langs.forEach(b => b.setAttribute('aria-pressed', b.dataset.setlang === l));
  try { localStorage.setItem('finnews-lang', l); } catch (e) {}
}
langs.forEach(b => b.addEventListener('click', () => setLang(b.dataset.setlang)));
setLang(root.dataset.lang);
"""


def _tr(texts, tag: str = "span", cls: str = "") -> str:
    """Один и тот же элемент на трёх языках; texts — уже экранированные (ru, uk, en)."""
    attr = f' class="{cls}"' if cls else ""
    return "".join(f'<{tag}{attr} data-l="{lang}">{text}</{tag}>' for lang, text in zip(LANGS, texts))


def _ui(key: str, tag: str = "span", cls: str = "") -> str:
    return _tr([escape(t) for t in UI[key]], tag, cls)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _date(day) -> tuple[str, str, str]:
    return (
        f"{day.day} {MONTHS['ru'][day.month - 1]}",
        f"{day.day} {MONTHS['uk'][day.month - 1]}",
        f"{MONTHS['en'][day.month - 1]} {day.day}",
    )


def _day_label(day, today) -> str:
    if day == today:
        return _ui("today")
    if day == today - timedelta(days=1):
        return _ui("yesterday")
    return _tr(_date(day))


def _loads(text: str) -> dict:
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _article(row, min_score: int, group: str) -> str:
    local = datetime.fromisoformat(row["published"]).astimezone()
    url = row["url"] if row["url"].startswith(("http://", "https://")) else "#"
    i18n = _loads(row["i18n"])

    def text(lang: str, field: str, fallback: str = "") -> str:
        return escape((i18n.get(lang) or {}).get(field) or fallback)

    scored = row["score"] is not None
    cls = ' class="important"' if scored and row["score"] >= min_score else ""
    parts = [
        f'<article data-group="{escape(group)}" data-score="{row["score"] if scored else -1}"{cls}>',
        f'<time datetime="{escape(row["published"])}">{local:%H:%M}</time>',
        "<div>",
        f'<h3><a href="{escape(url)}" target="_blank" rel="noopener noreferrer">',
        _tr([text("ru", "title", row["title"]), text("uk", "title", row["title"]), escape(row["title"])]),
        "</a></h3>",
    ]
    why = [text("ru", "why", row["reason"]), text("uk", "why"), text("en", "why")]
    if all(why):
        parts.append(_tr(why, "p", "why"))
    if row["summary"]:
        summary = escape(row["summary"])
        parts.append(_tr([text("ru", "summary") or summary, text("uk", "summary") or summary, summary], "p", "summary"))

    meta = [escape(row["source"])]
    if row["sector"] in SECTORS:
        meta.append(_tr([escape(s) for s in SECTORS[row["sector"]]]))
    if row["tickers"]:
        meta.append("<b>" + escape(row["tickers"].replace(",", " ")) + "</b>")
    if row["tone"] in TONES:
        arrow = '<span class="up">▲</span> ' if row["tone"] == "positive" else '<span class="down">▼</span> '
        meta.append(arrow + _tr(TONES[row["tone"]]))
    if scored:
        meta.append(_ui("score") + f" {row['score']}/10")
    parts.append(f'<p class="meta">{" · ".join(meta)}</p></div></article>')
    return "".join(parts)


def _brief(data: dict) -> str:
    if not all(isinstance(data.get(lang), dict) for lang in LANGS):
        return ""
    parts = ['<section class="brief">', f"<h2>{_ui('brief')}</h2>"]
    for lang in LANGS:
        b = data[lang]
        points = "".join(f"<li>{escape(str(p))}</li>" for p in b.get("points") or [])
        parts.append(
            f'<div data-l="{lang}">'
            f'<p class="overview">{escape(str(b.get("overview", "")))}</p>'
            f"<ul>{points}</ul>"
            f'<p class="term"><b>{escape(UI["term"][LANGS.index(lang)])}</b>'
            f'<strong>{escape(str(b.get("term", "")))}</strong> — {escape(str(b.get("term_explained", "")))}</p>'
            "</div>"
        )
    parts.append("</section>")
    return "".join(parts)


def _controls(rows, by_sector: bool, any_scored: bool) -> str:
    navs = []
    if any_scored:
        navs.append(
            '<nav aria-label="sort">'
            f'<button type="button" data-sort="time" aria-pressed="true">{_ui("sort_time")}</button>'
            f'<button type="button" data-sort="score" aria-pressed="false">{_ui("sort_score")}</button>'
            "</nav>"
        )
    if by_sector:
        present = {r["sector"] or "other" for r in rows}
        groups = [(k, _tr([escape(s) for s in SECTORS[k]])) for k in SECTORS if k in present]
    else:
        groups = [(s, escape(s)) for s in sorted({r["source"] for r in rows})]
    if len(groups) > 1:
        buttons = [f'<button type="button" data-group="" aria-pressed="true">{_ui("all")}</button>'] + [
            f'<button type="button" data-group="{escape(key)}" aria-pressed="false">{label}</button>'
            for key, label in groups
        ]
        navs.append(f'<nav aria-label="filter">{"".join(buttons)}</nav>')
    return f'<div class="controls">{"".join(navs)}</div>' if navs else ""


def render(cfg: Config, conn, *, hours: int = 24) -> str:
    rows = db.recent(conn, hours=hours)
    now = datetime.now().astimezone()
    today = now.date()
    any_scored = any(r["score"] is not None for r in rows)
    by_sector = any(r["sector"] in SECTORS for r in rows)

    def group(row) -> str:
        return (row["sector"] if row["sector"] in SECTORS else "other") if by_sector else row["source"]

    days: dict = {}
    for row in rows:
        day = datetime.fromisoformat(row["published"]).astimezone().date()
        days.setdefault(day, []).append(row)

    if rows:
        body = "".join(
            f'<section class="day"><h2>{_day_label(day, today)}</h2>'
            f'{"".join(_article(r, cfg.min_score, group(r)) for r in items)}</section>'
            for day, items in days.items()
        ) + f'<section class="day" id="flat" hidden><h2>{_ui("by_score")}</h2></section>'
    else:
        body = _ui("empty", "p", "empty")

    n = len(rows)
    stat = _tr([
        f"{n} {_plural(n, 'новость', 'новости', 'новостей')} за {hours} "
        f"{_plural(hours, 'час', 'часа', 'часов')} · обновлено {now:%H:%M}",
        f"{n} {_plural(n, 'новина', 'новини', 'новин')} за {hours} "
        f"{_plural(hours, 'годину', 'години', 'годин')} · оновлено {now:%H:%M}",
        f"{n} {'story' if n == 1 else 'stories'} in {hours} "
        f"{'hour' if hours == 1 else 'hours'} · updated {now:%H:%M}",
    ])
    weekday = _tr([escape(WEEKDAYS[lang][today.weekday()]) for lang in LANGS], "span", "weekday")
    langs = "".join(
        f'<button type="button" data-setlang="{lang}" aria-pressed="{str(lang == "ru").lower()}">{lang}</button>'
        for lang in LANGS
    )
    note = "" if any_scored else _ui("no_ai", "p", "note")
    return f"""<!doctype html>
<html lang="ru" data-lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>finnews — {_date(today)[0]}</title>
<script>{HEAD_JS}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
</head>
<body>
<main>
<div class="top"><p class="mark">finnews</p><nav class="langs" aria-label="language">{langs}</nav></div>
<h1>{_tr(_date(today))}{weekday}</h1>
<p class="stat">{stat}</p>
{note}
{_brief(_loads(db.latest_brief(conn, hours=hours)))}
{_controls(rows, by_sector, any_scored)}
{body}
<footer>{_ui("disclaimer")}</footer>
</main>
<script>{JS}</script>
</body>
</html>
"""


def build(cfg: Config, conn, *, hours: int = 24, out_dir: str = "site") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(render(cfg, conn, hours=hours), encoding="utf-8")
    return path
