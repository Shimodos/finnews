import argparse
import json
import logging
import os
import sys

from datetime import date, datetime, timedelta, timezone

from . import cards, db, earnings, edgar, fuel, hours, macro, market, site, telegram
from .collect import collect
from .config import load_config, yahoo_ticker_feed
from .llm import build_brief, build_digest, build_weekly, explain_movers, group_events, score_articles


def cmd_collect(cfg, conn, args) -> None:
    stats = collect(cfg, conn)
    for source, n in stats.items():
        print(f"{source}: {'недоступна' if n < 0 else f'+{n}'}")
    print(f"Всего новых: {sum(n for n in stats.values() if n > 0)}")


def cmd_score(cfg, conn, args) -> None:
    print(f"Оценено статей: {score_articles(cfg, conn)}")


def cmd_digest(cfg, conn, args) -> None:
    text, ids = build_digest(cfg, conn, hours=args.hours)
    if not text:
        print("Нет новостей для дайджеста.")
        return
    if args.dry_run or not (cfg.telegram_token and cfg.telegram_chat_id):
        if not args.dry_run:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — вывожу в консоль.\n", file=sys.stderr)
        print(text)
        return
    telegram.send(cfg.telegram_token, cfg.telegram_chat_id, text)
    db.mark_digested(conn, ids)
    conn.commit()
    print(f"Дайджест отправлен ({len(ids)} новостей).")


def cmd_run(cfg, conn, args) -> None:
    cmd_collect(cfg, conn, args)
    cmd_score(cfg, conn, args)
    cmd_digest(cfg, conn, args)


def cmd_site(cfg, conn, args) -> None:
    path = site.build(cfg, conn, hours=args.hours, out_dir=args.out)
    print(f"Страница собрана: {path.resolve()}")


def cmd_cards(cfg, conn, args) -> None:
    """Карточки для Instagram: страницы и картинки в папке cards внутри сайта."""
    total, images = cards.build(cfg, conn, hours=args.hours, out_dir=args.out)
    note = "" if images == total else " (браузер не найден или не справился — остальные только в HTML)"
    print(f"Карточек для соцсетей: {total}, картинок: {images}{note}")


def cmd_events(cfg, conn, args) -> None:
    print(f"Событий с повторами склеено: {group_events(cfg, conn, hours=args.hours)}")


def cmd_movers(cfg, conn, args) -> None:
    print(f"Лидеров дня с объяснением: {explain_movers(cfg, conn, hours=args.hours)}")


def cmd_brief(cfg, conn, args) -> None:
    done = build_brief(cfg, conn, hours=args.hours)
    print("Обзор дня готов." if done else "Нет новостей для обзора дня.")


def cmd_prices(cfg, conn, args) -> dict:
    data = market.snapshot(cfg)
    if not data["quotes"]:
        print("Котировки недоступны — страница соберётся без них.")
        return data
    data["earnings"] = earnings.upcoming(cfg)
    data["hours"] = hours.upcoming()
    db.save_market(conn, json.dumps(data, ensure_ascii=False))
    conn.commit()
    print(f"Котировок получено: {len(data['quotes'])}")
    print(f"Отчётов в календаре: {sum(len(d['items']) for d in data['earnings'])}")
    return data


def cmd_fuel(cfg, conn, args) -> None:
    """Цены на бензин: данные недельные, поэтому чаще раза в 12 часов не запрашиваем."""
    saved, when = db.get(conn, "fuel")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(when)).total_seconds() / 3600 if when else None
    if age is not None and age < MACRO_MAX_AGE_HOURS and not getattr(args, "force", False):
        print(f"Цены на бензин свежие (обновлены {age:.0f} ч назад) — пропускаю.")
        return
    data = fuel.refresh(json.loads(saved) if saved else {})
    db.put(conn, "fuel", json.dumps(data, ensure_ascii=False))
    conn.commit()
    print(f"Цен на бензин: {len(data)}")


def cmd_filings(cfg, conn, args) -> None:
    """Свежие отчёты компаний из SEC EDGAR."""
    if not cfg.sec_contact:
        print("SEC_CONTACT не задан — отчёты компаний из SEC пропускаю.")
        return
    items = edgar.recent(cfg)
    db.put(conn, "filings", json.dumps({"items": items}, ensure_ascii=False))
    conn.commit()
    print(f"Отчётов из SEC: {len(items)}")


def cmd_weekly(cfg, conn, args) -> None:
    """Обзор недели: пишется один раз, в выходные, когда торговая неделя закончилась."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    force = getattr(args, "force", False)
    if not force and today.weekday() < 5:
        print("Обзор недели пишется в выходные — пропускаю.")
        return
    if not force and db.get(conn, f"week:{monday.isoformat()}")[0]:
        print("Обзор этой недели уже есть — пропускаю.")
        return
    changes = market.week_changes([*market.INDEXES, *cfg.watchlist], monday)
    done = build_weekly(cfg, conn, monday, changes=changes)
    print("Обзор недели готов." if done else "Нет материала для обзора недели.")


MACRO_MAX_AGE_HOURS = 12  # макроданные меняются редко; чаще не запрашиваем, чтобы беречь лимиты


def cmd_macro(cfg, conn, args) -> None:
    saved, when = db.get(conn, "macro")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(when)).total_seconds() / 3600 if when else None
    if age is not None and age < MACRO_MAX_AGE_HOURS and not getattr(args, "force", False):
        print(f"Макроданные свежие (обновлены {age:.0f} ч назад) — пропускаю.")
        return
    data = macro.refresh(json.loads(saved) if saved else {})
    db.put(conn, "macro", json.dumps(data, ensure_ascii=False))
    conn.commit()
    print(f"Макропоказателей: {len(data)}")


def cmd_update(cfg, conn, args) -> None:
    """Всё для сайта: сбор, котировки, оценка с переводом, обзор дня, страница."""
    cmd_collect(cfg, conn, args)
    # Новости по лидерам дня — чтобы было чем объяснить их движение.
    leaders = market.movers(cmd_prices(cfg, conn, args))
    if leaders:
        stats = collect(cfg, conn, feeds=[yahoo_ticker_feed(s) for s in leaders])
        print(f"Новости по лидерам дня ({', '.join(leaders)}): +{sum(n for n in stats.values() if n > 0)}")
    cmd_macro(cfg, conn, args)
    cmd_fuel(cfg, conn, args)
    cmd_filings(cfg, conn, args)
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        cmd_score(cfg, conn, args)
        cmd_events(cfg, conn, args)
        cmd_movers(cfg, conn, args)
        cmd_brief(cfg, conn, args)
        cmd_weekly(cfg, conn, args)
    else:
        print("ANTHROPIC_API_KEY не задан — пропускаю оценку, перевод и обзор дня.")
    cmd_site(cfg, conn, args)
    cmd_cards(cfg, conn, args)


def cmd_chat_id(cfg, conn, args) -> None:
    if not cfg.telegram_token:
        sys.exit("Сначала задай TELEGRAM_BOT_TOKEN в .env")
    chats = telegram.find_chat_ids(cfg.telegram_token)
    if not chats:
        print("Сообщений нет. Напиши боту любое сообщение в Telegram и повтори команду.")
    for chat_id, name in chats:
        print(f"{chat_id}  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="finnews", description="Дайджест финансовых новостей")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="собрать новости из лент").set_defaults(func=cmd_collect)
    sub.add_parser("score", help="оценить важность новых статей").set_defaults(func=cmd_score)
    sub.add_parser("prices", help="получить котировки для страницы").set_defaults(func=cmd_prices)
    p = sub.add_parser("macro", help="обновить макроданные: ставки, инфляция, безработица")
    p.add_argument("--force", action="store_true", help="обновить, даже если данные свежие")
    p.set_defaults(func=cmd_macro)
    p = sub.add_parser("fuel", help="обновить цены на бензин в США и ЕС")
    p.add_argument("--force", action="store_true", help="обновить, даже если данные свежие")
    p.set_defaults(func=cmd_fuel)
    sub.add_parser("filings", help="получить свежие отчёты компаний из SEC EDGAR").set_defaults(func=cmd_filings)
    p = sub.add_parser("weekly", help="написать обзор текущей недели")
    p.add_argument("--force", action="store_true", help="написать сейчас, не дожидаясь выходных")
    p.set_defaults(func=cmd_weekly)
    for name, func, help_ in (
        ("digest", cmd_digest, "собрать и отправить дайджест"),
        ("run", cmd_run, "всё сразу: collect + score + digest"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--hours", type=int, default=24, help="за сколько часов брать новости")
        p.add_argument("--dry-run", action="store_true", help="вывести в консоль, не отправлять")
        p.set_defaults(func=func)
    for name, func, help_ in (
        ("brief", cmd_brief, "написать обзор дня на трёх языках"),
        ("events", cmd_events, "склеить новости об одном событии"),
        ("movers", cmd_movers, "объяснить движение лидеров дня по новостям о них"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--hours", type=int, default=24, help="за сколько часов брать новости")
        p.set_defaults(func=func)
    for name, func, help_ in (
        ("site", cmd_site, "собрать веб-страницу с новостями"),
        ("cards", cmd_cards, "собрать карточки для Instagram из готовых данных"),
        ("update", cmd_update, "всё для сайта: collect + prices + score + events + movers + brief + site + cards"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--hours", type=int, default=24, help="за сколько часов брать новости")
        p.add_argument("--out", default="site", help="папка для index.html")
        p.set_defaults(func=func)
    sub.add_parser("chat-id", help="показать chat_id для Telegram").set_defaults(func=cmd_chat_id)

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    try:
        args.func(cfg, conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
