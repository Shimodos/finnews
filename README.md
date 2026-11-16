# finnews

Ежедневный дайджест финансовых новостей в Telegram: собирает RSS-ленты, убирает дубли,
оценивает важность каждой новости и пишет короткую сводку на русском для начинающего инвестора.

## Как это работает

1. `collect` — читает ленты (Yahoo Finance, MarketWatch, CNBC, ФРС и ленты по тикерам из
   твоего списка), убирает дубли по ссылке и похожести заголовков, складывает в SQLite.
2. `score` — модель Claude Haiku ставит каждой новости оценку важности 0-10 и привязывает тикеры.
3. `digest` — модель Claude Sonnet пишет дайджест из новостей с оценкой не ниже порога
   и отправляет его в Telegram.

## Установка

Нужен Python 3.11+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
cp .env.example .env
```

Заполни `.env`:

- `ANTHROPIC_API_KEY` — ключ из https://console.anthropic.com/ (раздел API Keys).
- `TELEGRAM_BOT_TOKEN` — напиши в Telegram боту @BotFather команду `/newbot`, он выдаст токен.
- `TELEGRAM_CHAT_ID` — напиши своему новому боту любое сообщение, затем выполни
  `uv run finnews chat-id` и скопируй число.
- `WATCHLIST` — тикеры через запятую, за которыми ты следишь.

## Запуск

Всё сразу (сбор, оценка, дайджест):

```bash
uv run finnews run
```

Посмотреть дайджест в консоли, ничего не отправляя:

```bash
uv run finnews run --dry-run
```

Отдельные шаги: `finnews collect`, `finnews score`, `finnews digest`.

## Расписание

Чтобы дайджест приходил каждое утро в 8:00, добавь в cron (`crontab -e`), подставив путь к проекту:

```
0 * * * * cd /path/to/finnews && uv run finnews collect
0 8 * * * cd /path/to/finnews && uv run finnews run
```

Первая строка собирает новости каждый час, чтобы ничего не выпало из лент между дайджестами.

## Настройка

- Ленты — список `FEEDS` в `finnews/config.py`.
- Порог важности — `FINNEWS_MIN_SCORE` (по умолчанию 6).
- Тон и структура дайджеста — `DIGEST_SYSTEM` в `finnews/llm.py`.

## Тесты

```bash
uv run pytest
```
