"""Статичная веб-страница с новостями: один файл index.html без сборки и зависимостей.

Все три языка лежат в одной странице; переключатель показывает нужный через атрибут
data-lang у <html>. Заголовок всегда ведёт на оригинал статьи.
"""

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path

from . import db
from .config import Config
from .market import EXCHANGES, SECTOR_SYMBOLS
from .market import movers as top_movers

LANGS = ("ru", "uk", "en")
STORY_MIN_SCORE = 4  # новости с оценкой 0-3 — шум, движение акции ими не объясняем
PAGES = (("archive.html", "nav_archive"), ("glossary.html", "nav_glossary"))  # ссылки в шапке

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
    "macro": ("Экономика и центробанки", "Економіка і центробанки", "Economy & central banks"),
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
REGIONS = {
    "us": ("США", "США", "US"),
    "europe": ("Европа", "Європа", "Europe"),
    "asia": ("Азия", "Азія", "Asia"),
    "world": ("Мир", "Світ", "World"),
}
TONES = {
    "positive": ("позитив", "позитив", "positive"),
    "negative": ("негатив", "негатив", "negative"),
}
UI = {
    "all": ("Все", "Усі", "All"),
    "all_regions": ("Весь мир", "Увесь світ", "All regions"),
    "today": ("Сегодня", "Сьогодні", "Today"),
    "yesterday": ("Вчера", "Вчора", "Yesterday"),
    "sort_time": ("Свежие", "Свіжі", "Latest"),
    "sort_score": ("Важные", "Важливі", "Important"),
    "by_score": ("По важности", "За важливістю", "By importance"),
    "brief": ("Главное за день", "Головне за день", "Today in brief"),
    "term": ("Термин дня", "Термін дня", "Term of the day"),
    "score": ("важность", "важливість", "importance"),
    "also": ("Ещё об этом:", "Ще про це:", "More on this:"),
    "nav_archive": ("Архив", "Архів", "Archive"),
    "nav_glossary": ("Словарик", "Словничок", "Glossary"),
    "no_archive": (
        "Архив появится после первого обзора дня.",
        "Архів з'явиться після першого огляду дня.",
        "The archive appears after the first daily brief.",
    ),
    "no_glossary": (
        "Термины появятся после первого обзора дня.",
        "Терміни з'являться після першого огляду дня.",
        "Terms appear after the first daily brief.",
    ),
    "pulse": ("Пульс рынка", "Пульс ринку", "Market pulse"),
    "movers": ("Кто двигался и почему", "Хто рухався і чому", "Who moved and why"),
    "earnings": ("Календарь отчётов", "Календар звітів", "Earnings calendar"),
    "earnings_note": (
        "Отчёт — день, когда компания раскрывает результаты квартала. В такие дни её акции "
        "часто движутся сильнее обычного.",
        "Звіт — день, коли компанія розкриває результати кварталу. У такі дні її акції "
        "часто рухаються сильніше, ніж зазвичай.",
        "An earnings report is the day a company publishes its quarterly results. Its shares "
        "often move more than usual on that day.",
    ),
    "before": ("до открытия биржи", "до відкриття біржі", "before the open"),
    "after": ("после закрытия", "після закриття", "after the close"),
    "eps": ("прогноз прибыли на акцию", "прогноз прибутку на акцію", "EPS forecast"),
    "no_story": (
        "в ленте новостей об этом нет",
        "у стрічці новин про це немає",
        "no related story in the feed",
    ),
    "quotes_note": (
        "Котировки: Yahoo Finance, на момент обновления страницы.",
        "Котирування: Yahoo Finance, на момент оновлення сторінки.",
        "Quotes: Yahoo Finance, as of the page update.",
    ),
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
.mark, .stat, nav button, .links a, .entry .when, .day h2, .brief h2, .market h2, time, .meta, .term b, footer,
.quote .name, .quote .chg, .mover .sym, .mover .pct {
  font-family: var(--mono); font-size: 12px; letter-spacing: 0.02em;
}
.top { display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: center; margin: 0 0 44px; }
.mark { margin: 0; }
.mark a { color: var(--accent); text-decoration: none; }
.links { margin-right: auto; }
.links a { color: var(--muted); text-decoration: none; padding: 4px 0; }
.links a:hover { color: var(--text); }
.entry {
  display: grid; grid-template-columns: 1fr; gap: 6px; padding: 20px 0;
  border-bottom: 1px solid var(--line); color: inherit; text-decoration: none;
}
h1 + .entry { margin-top: 40px; border-top: 1px solid var(--text); }
.entry .term-name { font-size: 18px; font-weight: 500; }
.entry .what { color: var(--muted); font-size: 15px; }
a.entry:hover .what { color: var(--text); }
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
.brief h2, .day h2, .market h2 {
  margin: 0; padding: 12px 0; border-bottom: 1px solid var(--text);
  font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em;
}
.market { margin: 48px 0 0; }
.market h2 .asof { color: var(--muted); font-weight: 400; }
.hours {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 4px 28px;
  margin: 14px 0 4px; color: var(--muted); font-family: var(--mono); font-size: 12px; letter-spacing: 0.02em;
}
.exch { display: grid; grid-template-columns: 8px 86px 1fr; gap: 0 8px; align-items: baseline; margin: 0; }
.exch .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--line); translate: 0 -1px; }
.exch.open .dot { background: var(--up); }
.exch.open .city { color: var(--text); }
.quotes {
  display: grid; grid-auto-flow: column; gap: 0 28px;
  grid-auto-columns: max(140px, calc((100% - 84px) / 4));
  overflow-x: auto; scroll-snap-type: x proximity; overscroll-behavior-x: contain;
  scrollbar-width: thin; scrollbar-color: var(--line) transparent;
  border-bottom: 1px solid var(--line);
}
.quotes.scrollable { cursor: grab; }
.quotes.dragging { cursor: grabbing; user-select: none; scroll-snap-type: none; }
.quotes:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.quote { padding: 16px 0 14px; scroll-snap-align: start; }
.quote .name { display: block; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.quote .price { display: block; margin-top: 4px; font-size: 20px; font-weight: 500; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.quote svg { display: block; width: 100%; height: 30px; margin-top: 10px; overflow: visible; }
.quote polyline { fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.quote line { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0.5; vector-effect: non-scaling-stroke; }
.mover {
  display: grid; grid-template-columns: 64px 1fr auto; gap: 2px 12px; align-items: baseline;
  padding: 14px 0; border-bottom: 1px solid var(--line);
}
.mover .sym { font-weight: 500; }
.mover .pct, .quote .chg, nav .chg { font-variant-numeric: tabular-nums; }
.mover .because { grid-column: 2 / -1; margin: 0; font-size: 15px; color: var(--muted); }
.mover .because a { color: inherit; text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 4px; }
.mover .because a:hover { color: var(--text); text-decoration-color: var(--accent); }
nav .chg { margin-left: 5px; }
.earnings .lead { margin: 14px 0 0; font-size: 15px; color: var(--muted); }
.earnings .when-day {
  margin: 22px 0 0; padding-bottom: 8px; border-bottom: 1px solid var(--line); color: var(--muted);
  font: 400 12px var(--mono); letter-spacing: 0.02em;
}
.earnings .pct { color: var(--muted); }
.earnings .because:empty { display: none; }
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
.also { margin: 6px 0 0; color: var(--muted); font-family: var(--mono); font-size: 12px; letter-spacing: 0.02em; }
.also a { color: inherit; text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 4px; }
.also a:hover { color: var(--text); text-decoration-color: var(--accent); }
.meta b { font-weight: 500; color: var(--text); }
.up { color: var(--up); } .down { color: var(--down); }
.empty { margin: 56px 0 0; color: var(--muted); }
footer { margin: 56px 0 0; color: var(--muted); }
[hidden] { display: none !important; }
@media (max-width: 520px) {
  main { padding-top: 32px; }
  article { grid-template-columns: 1fr; }
  time { padding: 0 0 4px; }
  .mover { grid-template-columns: 52px 1fr auto; }
}
"""

# Выбор языка до отрисовки, чтобы страница не мигала: сохранённый выбор, иначе английский.
HEAD_JS = """
(function () {
  var l = null;
  try { l = localStorage.getItem('finnews-lang'); } catch (e) {}
  if (['ru', 'uk', 'en'].indexOf(l) < 0) l = 'en';
  document.documentElement.dataset.lang = l;
  document.documentElement.lang = l;
})();
"""

JS = """
const root = document.documentElement;
const state = { sort: 'time', group: '', region: '' };
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
    a.hidden = (!!state.group && a.dataset.group !== state.group)
      || (!!state.region && a.dataset.region !== state.region);
  });
  document.querySelectorAll('.day').forEach(d => { d.hidden = !d.querySelector('article:not([hidden])'); });
}
for (const [attr, key] of [['sort', 'sort'], ['group', 'group'], ['region', 'region']]) {
  const buttons = document.querySelectorAll('[data-' + attr + ']');
  buttons.forEach(b => b.addEventListener('click', () => {
    state[key] = b.dataset[attr];
    press(buttons, b);
    apply();
  }));
}
// Статус бирж и отсчёт до следующего обновления считаются в браузере, по часам читателя.
const T = {
  ru: { open: 'открыта · закроется в {t}', closed: 'закрыта · откроется {d}, {t}',
        next: ' · следующее обновление через ~{t}', h: 'ч', m: 'мин' },
  uk: { open: 'відкрита · закриється о {t}', closed: 'закрита · відкриється {d}, {t}',
        next: ' · наступне оновлення через ~{t}', h: 'год', m: 'хв' },
  en: { open: 'open · closes at {t}', closed: 'closed · opens {d}, {t}',
        next: ' · next update in ~{t}', h: 'h', m: 'min' },
};
const exchanges = document.querySelectorAll('.exch');
const nextEl = document.getElementById('next');
const formats = {};
// Дата и время по часам биржи (её часовой пояс) для момента ms.
function parts(ms, tz) {
  const f = formats[tz] || (formats[tz] = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, weekday: 'short', year: 'numeric', month: 'numeric',
    day: 'numeric', hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
  }));
  const p = {};
  f.formatToParts(new Date(ms)).forEach(x => { p[x.type] = x.value; });
  return p;
}
// Момент, когда на бирже в календарный день момента ms часы показывают h:m.
function wall(ms, tz, h, m) {
  const p = parts(ms, tz);
  const guess = Date.UTC(+p.year, p.month - 1, +p.day, h, m);
  const g = parts(guess, tz);
  return guess - (Date.UTC(+g.year, g.month - 1, +g.day, +g.hour, +g.minute) - guess);
}
// Ближайшее открытие по обычному расписанию: будни, в тот же час, что и последняя сессия.
// Праздники бирж не учитываются.
function nextOpen(now, tz, start) {
  const s = parts(start, tz);
  for (let d = 0; d < 8; d++) {
    const t = wall(now + d * 864e5, tz, +s.hour, +s.minute);
    const day = parts(t, tz).weekday;
    if (t > now && day !== 'Sat' && day !== 'Sun') return t;
  }
  return null;
}
function tick() {
  const lang = root.dataset.lang, L = T[lang] || T.en, now = Date.now();
  const time = ms => new Date(ms).toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  exchanges.forEach(el => {
    const start = el.dataset.start * 1000, end = el.dataset.end * 1000;
    const isOpen = now >= start && now < end;
    let text = '';
    try {
      if (isOpen) {
        text = L.open.replace('{t}', time(end));
      } else {
        const open = now < start ? start : nextOpen(now, el.dataset.tz, start);
        if (open) {
          text = L.closed
            .replace('{d}', new Date(open).toLocaleDateString(lang, { weekday: 'short' }))
            .replace('{t}', time(open));
        }
      }
    } catch (e) {}
    el.classList.toggle('open', isOpen);
    el.hidden = !text;
    el.querySelector('.state').textContent = text;
  });
  if (nextEl) {
    const d = new Date(now);
    let best = Infinity;
    for (let add = 0; add < 2; add++) {
      for (const slot of nextEl.dataset.schedule.split(',')) {
        const [h, m] = slot.split(':').map(Number);
        const t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + add, h, m);
        if (t > now && t < best) best = t;
      }
    }
    const mins = Math.max(1, Math.round((best - now) / 6e4));
    const hh = Math.floor(mins / 60);
    nextEl.textContent = L.next.replace('{t}', (hh ? hh + ' ' + L.h + ' ' : '') + (mins % 60) + ' ' + L.m);
  }
}
setInterval(tick, 30000);

const langs = document.querySelectorAll('[data-setlang]');
function setLang(l) {
  root.dataset.lang = l;
  root.lang = l;
  tick();
  langs.forEach(b => b.setAttribute('aria-pressed', b.dataset.setlang === l));
  try { localStorage.setItem('finnews-lang', l); } catch (e) {}
}
langs.forEach(b => b.addEventListener('click', () => setLang(b.dataset.setlang)));
setLang(root.dataset.lang);

// Ленту котировок можно тянуть мышью: нажал и повёл. На тач-экранах прокрутка и так родная.
const strip = document.querySelector('.quotes');
if (strip) {
  let drag = false, startX = 0, startLeft = 0;
  const fits = () => strip.classList.toggle('scrollable', strip.scrollWidth > strip.clientWidth);
  fits();
  addEventListener('resize', fits);
  strip.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    drag = true; startX = e.clientX; startLeft = strip.scrollLeft;
    strip.setPointerCapture(e.pointerId);
    strip.classList.add('dragging');
  });
  strip.addEventListener('pointermove', e => {
    if (drag) strip.scrollLeft = startLeft - (e.clientX - startX);
  });
  const stop = () => { drag = false; strip.classList.remove('dragging'); };
  strip.addEventListener('pointerup', stop);
  strip.addEventListener('pointercancel', stop);
}
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


def _article(row, min_score: int, group: str, also=()) -> str:
    """Одна новость; also — другие заметки о том же событии, они идут ссылками под ней."""
    local = datetime.fromisoformat(row["published"]).astimezone()
    url = row["url"] if row["url"].startswith(("http://", "https://")) else "#"
    i18n = _loads(row["i18n"])

    def text(lang: str, field: str, fallback: str = "") -> str:
        return escape((i18n.get(lang) or {}).get(field) or fallback)

    scored = row["score"] is not None
    region = row["region"] if row["region"] in REGIONS else ""
    cls = ' class="important"' if scored and row["score"] >= min_score else ""
    parts = [
        f'<article data-group="{escape(group)}" data-region="{region}"'
        f' data-score="{row["score"] if scored else -1}"{cls}>',
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
    if region:
        meta.append(_tr([escape(s) for s in REGIONS[region]]))
    if row["sector"] in SECTORS:
        meta.append(_tr([escape(s) for s in SECTORS[row["sector"]]]))
    if row["tickers"]:
        meta.append("<b>" + escape(row["tickers"].replace(",", " ")) + "</b>")
    if row["tone"] in TONES:
        arrow = '<span class="up">▲</span> ' if row["tone"] == "positive" else '<span class="down">▼</span> '
        meta.append(arrow + _tr(TONES[row["tone"]]))
    if scored:
        meta.append(_ui("score") + f" {row['score']}/10")
    parts.append(f'<p class="meta">{" · ".join(meta)}</p>')
    links = [
        f'<a href="{escape(r["url"])}" target="_blank" rel="noopener noreferrer"'
        f' title="{escape(r["title"])}">{escape(r["source"])}</a>'
        for r in also
        if r["url"].startswith(("http://", "https://"))
    ]
    if links:
        parts.append(f'<p class="also">{_ui("also")} {", ".join(links)}</p>')
    parts.append("</div></article>")
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


def _pct(change: float) -> str:
    """Изменение за день: цвет, знак и проценты."""
    cls = "up" if change > 0 else "down" if change < 0 else ""
    text = f"{change:+.2f}%".replace("-", "−")
    return f'<span class="chg {cls}">{text}</span>'


def _spark(points: list, prev: float, change: float) -> str:
    """График за день без осей; пунктир — вчерашнее закрытие."""
    if len(points) < 2:
        return ""
    low, high = min(*points, prev), max(*points, prev)
    span = (high - low) or 1

    def y(value: float) -> str:
        return f"{27 - (value - low) / span * 24:.1f}"

    step = 100 / (len(points) - 1)
    line = " ".join(f"{i * step:.1f},{y(p)}" for i, p in enumerate(points))
    cls = "up" if change > 0 else "down" if change < 0 else ""
    return (
        f'<svg class="{cls}" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">'
        f'<line x1="0" x2="100" y1="{y(prev)}" y2="{y(prev)}"/><polyline points="{line}"/></svg>'
    )


def _pulse(data: dict) -> str:
    quotes = data.get("quotes") or {}
    symbols = [s for s in data.get("pulse") or [] if s in quotes]
    if not symbols:
        return ""
    # Время последней сделки по первому символу (индекс): в выходной это прошлая сессия.
    moment = datetime.fromisoformat(quotes[symbols[0]]["time"]).astimezone()
    asof = _tr([f"· {d}, {moment:%H:%M}" for d in _date(moment.date())], "span", "asof")
    cards = "".join(
        '<div class="quote">'
        f'<span class="name">{escape(q["name"] if s.startswith("^") else s)}</span>'
        f'<span class="price">{q["price"]:,.2f}</span>{_pct(q["change"])}'
        f'{_spark(q.get("points") or [], q["prev"], q["change"])}</div>'
        for s, q in ((s, quotes[s]) for s in symbols)
    )
    # Открыта ли биржа и когда откроется или закроется, дописывает скрипт — по часам читателя.
    sessions = data.get("sessions") or {}
    rows = "".join(
        f'<p class="exch" data-tz="{escape(s["tz"])}" data-start="{int(s["start"])}" data-end="{int(s["end"])}">'
        f'<span class="dot"></span><span class="city">{_tr([escape(n) for n in names])}</span>'
        '<span class="state"></span></p>'
        for symbol, names in EXCHANGES
        for s in [sessions.get(symbol) or {}]
        if s.get("start") and s.get("end") and re.fullmatch(r"[A-Za-z_]+(/[A-Za-z_+-]+)+", str(s.get("tz", "")))
    )
    hours = f'<div class="hours" id="hours">{rows}</div>' if rows else ""
    return (
        f'<section class="market pulse"><h2>{_ui("pulse")} {asof}</h2>{hours}'
        f'<div class="quotes" tabindex="0">{cards}</div></section>'
    )


def _movers(data: dict, rows, count: int = 3) -> str:
    """Лидеры роста и падения среди крупных компаний и новость из ленты, которая это объясняет."""
    symbols = top_movers(data, count)
    if not symbols:
        return ""
    stories: dict = {}
    for row in rows:
        if (row["score"] or 0) < STORY_MIN_SCORE:
            continue
        for ticker in filter(None, row["tickers"].split(",")):
            best = stories.get(ticker)
            if best is None or (row["score"] or 0) > (best["score"] or 0):
                stories[ticker] = row
    items = []
    for symbol in symbols:
        q = data["quotes"][symbol]
        row = stories.get(symbol)
        if row is not None and row["url"].startswith(("http://", "https://")):
            i18n = _loads(row["i18n"])
            titles = [
                escape((i18n.get("ru") or {}).get("title") or row["title"]),
                escape((i18n.get("uk") or {}).get("title") or row["title"]),
                escape(row["title"]),
            ]
            because = (
                f'<a href="{escape(row["url"])}" target="_blank" rel="noopener noreferrer">{_tr(titles)}</a>'
            )
        else:
            because = _ui("no_story")
        items.append(
            f'<div class="mover"><span class="sym">{escape(symbol)}</span>'
            f'<span class="co">{escape(q["name"])}</span>'
            f'<span class="pct">{_pct(q["change"])}</span>'
            f'<p class="because">{because}</p></div>'
        )
    return f'<section class="market movers"><h2>{_ui("movers")}</h2>{"".join(items)}</section>'


def _earnings(data: dict) -> str:
    """Календарь отчётов на ближайшие дни: кто, когда и какой прибыли ждут аналитики."""
    blocks = []
    for entry in data.get("earnings") or []:
        try:
            day = date.fromisoformat(entry["date"])
        except (KeyError, TypeError, ValueError):
            continue
        label = _tr([f"{WEEKDAYS[lang][day.weekday()]}, {d}" for lang, d in zip(LANGS, _date(day))])
        rows = []
        for item in entry.get("items") or []:
            eps = item.get("eps")
            forecast = (
                f'{_ui("eps")}: {"−" if eps < 0 else ""}${abs(eps):.2f}' if isinstance(eps, (int, float)) else ""
            )
            when = _ui(item["when"]) if item.get("when") in ("before", "after") else ""
            rows.append(
                f'<div class="mover"><span class="sym">{escape(str(item.get("symbol", "")))}</span>'
                f'<span class="co">{escape(str(item.get("name", "")))}</span>'
                f'<span class="pct">{when}</span>'
                f'<p class="because">{forecast}</p></div>'
            )
        if rows:
            blocks.append(f'<h3 class="when-day">{label}</h3>{"".join(rows)}')
    if not blocks:
        return ""
    return (
        f'<section class="market earnings"><h2>{_ui("earnings")}</h2>'
        f'{_ui("earnings_note", "p", "lead")}{"".join(blocks)}</section>'
    )


def _controls(rows, by_sector: bool, any_scored: bool, quotes: dict) -> str:
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
        # Рядом со сферой — изменение её отраслевого фонда за день, если котировка есть.
        groups = [
            (
                k,
                _tr([escape(s) for s in SECTORS[k]])
                + (_pct(quotes[SECTOR_SYMBOLS[k]]["change"]) if SECTOR_SYMBOLS.get(k) in quotes else ""),
            )
            for k in SECTORS
            if k in present
        ]
    else:
        groups = [(s, escape(s)) for s in sorted({r["source"] for r in rows})]
    if len(groups) > 1:
        buttons = [f'<button type="button" data-group="" aria-pressed="true">{_ui("all")}</button>'] + [
            f'<button type="button" data-group="{escape(key)}" aria-pressed="false">{label}</button>'
            for key, label in groups
        ]
        navs.append(f'<nav aria-label="filter">{"".join(buttons)}</nav>')
    regions = [k for k in REGIONS if any(r["region"] == k for r in rows)]
    if len(regions) > 1:
        buttons = [f'<button type="button" data-region="" aria-pressed="true">{_ui("all_regions")}</button>'] + [
            f'<button type="button" data-region="{k}" aria-pressed="false">'
            f'{_tr([escape(s) for s in REGIONS[k]])}</button>'
            for k in regions
        ]
        navs.insert(1 if any_scored else 0, f'<nav aria-label="region">{"".join(buttons)}</nav>')
    return f'<div class="controls">{"".join(navs)}</div>' if navs else ""


def _feed(cfg: Config, rows, today, quotes: dict) -> str:
    """Сортировка, фильтр и лента новостей по дням."""
    if not rows:
        return _ui("empty", "p", "empty")
    # Повторы одного события: показываем главную новость, остальные — ссылками под ней.
    ids = {r["id"] for r in rows}
    also: dict = {}
    for row in rows:
        if row["event"] and row["event"] != row["id"] and row["event"] in ids:
            also.setdefault(row["event"], []).append(row)
    repeats = {r["id"] for group_rows in also.values() for r in group_rows}
    rows = [r for r in rows if r["id"] not in repeats]

    any_scored = any(r["score"] is not None for r in rows)
    by_sector = any(r["sector"] in SECTORS for r in rows)

    def group(row) -> str:
        return (row["sector"] if row["sector"] in SECTORS else "other") if by_sector else row["source"]

    days: dict = {}
    for row in rows:
        day = datetime.fromisoformat(row["published"]).astimezone().date()
        days.setdefault(day, []).append(row)
    body = "".join(
        f'<section class="day"><h2>{_day_label(day, today)}</h2>'
        f'{"".join(_article(r, cfg.min_score, group(r), also.get(r["id"], ())) for r in items)}</section>'
        for day, items in days.items()
    ) + f'<section class="day" id="flat" hidden><h2>{_ui("by_score")}</h2></section>'
    return _controls(rows, by_sector, any_scored, quotes) + body


def _shell(title: str, content: str) -> str:
    """Общая обвязка страниц: шапка со ссылками и языками, шрифты, стили, скрипт."""
    langs = "".join(
        f'<button type="button" data-setlang="{lang}" aria-pressed="{str(lang == "en").lower()}">{lang}</button>'
        for lang in LANGS
    )
    links = "".join(f'<a href="{href}">{_ui(key)}</a>' for href, key in PAGES)
    return f"""<!doctype html>
<html lang="en" data-lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>finnews — {escape(title)}</title>
<script>{HEAD_JS}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
</head>
<body>
<main>
<div class="top"><p class="mark"><a href="index.html">finnews</a></p>
<nav class="links" aria-label="pages">{links}</nav>
<nav class="langs" aria-label="language">{langs}</nav></div>
{content}
</main>
<script>{JS}</script>
</body>
</html>
"""


def _count(n: int) -> list[str]:
    return [
        f"{n} {_plural(n, 'новость', 'новости', 'новостей')}",
        f"{n} {_plural(n, 'новина', 'новини', 'новин')}",
        f"{n} {'story' if n == 1 else 'stories'}",
    ]


def _heading(day) -> str:
    weekday = _tr([escape(WEEKDAYS[lang][day.weekday()]) for lang in LANGS], "span", "weekday")
    return f"<h1>{_tr(_date(day))}{weekday}</h1>"


def _brief_days(conn) -> list:
    """(дата, обзор) по всем дням, за которые есть обзор; свежие первыми."""
    result = []
    for row in db.all_briefs(conn):
        try:
            day = date.fromisoformat(row["day"])
        except ValueError:
            continue
        data = _loads(row["data"])
        if all(isinstance(data.get(lang), dict) for lang in LANGS):
            result.append((day, data))
    return result


def render_day(cfg: Config, conn, day, brief: dict) -> str:
    """Страница архива: обзор и новости одного дня (по местному времени)."""
    start = datetime.combine(day, time.min).astimezone()
    rows = db.between(
        conn,
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        (start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    content = (
        f'{_heading(day)}<p class="stat">{_tr(_count(len(rows)))}</p>{_brief(brief)}'
        f'{_feed(cfg, rows, datetime.now().astimezone().date(), {})}'
        f'<footer>{_ui("disclaimer")}</footer>'
    )
    return _shell(_date(day)[2], content)


def render_archive(conn) -> str:
    entries = "".join(
        f'<a class="entry" href="day-{day.isoformat()}.html"><span class="when">'
        + _tr([f"{d}, {escape(WEEKDAYS[lang][day.weekday()])}" for lang, d in zip(LANGS, _date(day))])
        + "</span>"
        + _tr([escape(str(data[lang].get("overview", ""))) for lang in LANGS], "span", "what")
        + "</a>"
        for day, data in _brief_days(conn)
    )
    content = f'<h1>{_ui("nav_archive")}</h1>{entries or _ui("no_archive", "p", "empty")}'
    return _shell(UI["nav_archive"][2], content + f'<footer>{_ui("disclaimer")}</footer>')


def render_glossary(conn) -> str:
    """Все «термины дня» из обзоров; повтор термина показывается один раз, самый свежий."""
    seen, entries = set(), []
    for day, data in _brief_days(conn):
        key = str(data["en"].get("term", "")).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(
            '<div class="entry"><span class="when">' + _tr(_date(day)) + "</span>"
            + _tr([escape(str(data[lang].get("term", ""))) for lang in LANGS], "strong", "term-name")
            + _tr([escape(str(data[lang].get("term_explained", ""))) for lang in LANGS], "span", "what")
            + "</div>"
        )
    content = f'<h1>{_ui("nav_glossary")}</h1>{"".join(entries) or _ui("no_glossary", "p", "empty")}'
    return _shell(UI["nav_glossary"][2], content + f'<footer>{_ui("disclaimer")}</footer>')


def render(cfg: Config, conn, *, hours: int = 24) -> str:
    rows = db.recent(conn, hours=hours)
    now = datetime.now().astimezone()
    today = now.date()
    any_scored = any(r["score"] is not None for r in rows)

    n = len(rows)
    stat = _tr([
        f"{n} {_plural(n, 'новость', 'новости', 'новостей')} за {hours} "
        f"{_plural(hours, 'час', 'часа', 'часов')} · обновлено {now:%H:%M}",
        f"{n} {_plural(n, 'новина', 'новини', 'новин')} за {hours} "
        f"{_plural(hours, 'годину', 'години', 'годин')} · оновлено {now:%H:%M}",
        f"{n} {'story' if n == 1 else 'stories'} in {hours} "
        f"{'hour' if hours == 1 else 'hours'} · updated {now:%H:%M}",
    ])
    note = "" if any_scored else _ui("no_ai", "p", "note")
    market = _loads(db.latest_market(conn, hours=hours))
    quotes = market.get("quotes") or {}
    quotes_note = f" {_ui('quotes_note')}" if quotes else ""
    # Отсчёт до следующей сборки: расписание (UTC) задаётся там же, где она запускается.
    slots = [t for t in cfg.schedule if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t)]
    countdown = f'<span id="next" data-schedule="{",".join(slots)}"></span>' if slots else ""
    content = f"""{_heading(today)}
<p class="stat">{stat}{countdown}</p>
{note}
{_pulse(market)}
{_brief(_loads(db.latest_brief(conn, hours=hours)))}
{_movers(market, rows)}
{_earnings(market)}
{_feed(cfg, rows, today, quotes)}
<footer>{_ui("disclaimer")}{quotes_note}</footer>"""
    return _shell(_date(today)[2], content)


def build(cfg: Config, conn, *, hours: int = 24, out_dir: str = "site") -> Path:
    """Пишет главную, архив по дням и словарик. Возвращает путь к главной."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(render(cfg, conn, hours=hours), encoding="utf-8")
    (out / "archive.html").write_text(render_archive(conn), encoding="utf-8")
    (out / "glossary.html").write_text(render_glossary(conn), encoding="utf-8")
    for day, brief in _brief_days(conn):
        (out / f"day-{day.isoformat()}.html").write_text(render_day(cfg, conn, day, brief), encoding="utf-8")
    return path
