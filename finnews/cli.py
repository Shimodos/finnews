import argparse
import logging
import sys

from . import db, telegram
from .collect import collect
from .config import load_config
from .llm import build_digest, score_articles


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
    for name, func, help_ in (
        ("digest", cmd_digest, "собрать и отправить дайджест"),
        ("run", cmd_run, "всё сразу: collect + score + digest"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--hours", type=int, default=24, help="за сколько часов брать новости")
        p.add_argument("--dry-run", action="store_true", help="вывести в консоль, не отправлять")
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
