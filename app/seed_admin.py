"""Завести администратора студии. Один раз на установку.

    python3 -m app.seed_admin                          # пароль сгенерируется
    python3 -m app.seed_admin --email me@studio.ru
    python3 -m app.seed_admin --password-stdin         # прочитать из потока
    python3 -m app.seed_admin --reset-password         # сменить существующему

**Пароль по умолчанию генерируется, а не задан константой.** Заводить админа с
известным паролем и надеяться, что его сменят, — это способ получить установку
с паролем `admin` через полгода. Сгенерированный пароль печатается один раз,
при заведении, и больше нигде не хранится: в базе лежит argon2id-хеш, из
которого его не достать.

**Почему пароль печатается в терминал, а не пишется в файл.** Файл кто-нибудь
закоммитит. Терминал — тоже не сейф (история команд, скроллбек), поэтому есть
`--password-stdin`: пароль приходит потоком и в аргументах процесса не виден.
Печать используется тогда, когда своего пароля ещё нет, — а это ровно один раз.

**Идемпотентность.** Повторный запуск не заводит второго админа и не трогает
пароль существующего: сидер, меняющий пароль на каждый запуск, однажды сотрёт
рабочий доступ при перезапуске установки. Смена — явным `--reset-password`.

**Чем генерируется.** Двадцать символов из алфавита без похожих начертаний
(`0/O`, `1/l/I` исключены) — 116 бит, и они разбиты на группы по пять, чтобы
пароль можно было прочитать вслух и перенабрать без ошибок. Парольную фразу из
словаря сюда просилась, но честный диктионарий на 4096 слов — это файл на сотню
килобайт в репозитории; словарь на несколько десятков слов дал бы 24 бита,
то есть пароль слабее случайных шести символов при виде надёжного.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

#: Алфавит без похожих начертаний: `0`/`O`, `1`/`l`/`I` из него выброшены.
#: Пароль читают вслух и перенабирают руками — пара, которую нельзя различить
#: на глаз, превращается в «не подходит пароль» без всякой причины.
_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: Длина сгенерированного пароля. Двадцать символов из 57-символьного алфавита
#: — 116 бит: столько не подбирают ни онлайн, ни офлайн по argon2id-хешу.
_GENERATED_LENGTH = 20

#: По сколько символов в группе. Группы по пять читаются вслух без потери
#: места; сплошная строка из двадцати — нет.
_GROUP = 5


def generate_password(length: int = _GENERATED_LENGTH) -> str:
    """Случайный пароль, разбитый дефисами на читаемые группы.

    Дефисы входят в пароль и хешируются вместе с ним: убрать их «для
    красоты» при вводе — значит получить другой пароль, и лучше, чтобы это
    было видно сразу, а не через месяц при попытке войти с другой машины.
    """
    size = max(12, int(length))
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(size))
    return "-".join(raw[i : i + _GROUP] for i in range(0, size, _GROUP))


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        pwd = sys.stdin.read().strip()
        if not pwd:
            raise SystemExit("на входе пусто: --password-stdin ждёт пароль потоком")
        return pwd
    if args.password:
        return args.password
    return ""


async def run(args: argparse.Namespace) -> int:
    from app.db import SessionLocal
    from app.services import studio_users
    from app.services.studio_auth import ROLE_ADMIN
    from app.settings import settings

    if not settings.accounts_enabled:
        print(
            "STUDIO_SESSION_SECRET не задан — учётные записи выключены, и заведённый\n"
            "админ никуда не сможет войти. Сгенерировать секрет:\n"
            "    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'\n"
            "и положить в .env как STUDIO_SESSION_SECRET.",
            file=sys.stderr,
        )
        return 2

    email = studio_users.normalize_email(args.email)
    given = _read_password(args)
    role = args.role if hasattr(args, "role") and args.role else ROLE_ADMIN

    async with SessionLocal() as session:
        existing = await studio_users.find_by_email(session, email)

        if existing is not None and not args.reset_password:
            admins = await studio_users.count_admins(session)
            print(
                f"учётка {email} уже заведена (роль {existing.role}, "
                f"живых админов: {admins}). Пароль не тронут.\n"
                "Сменить: python3 -m app.seed_admin --reset-password"
            )
            return 0

        password = given or generate_password()
        generated = not given

        if existing is not None:
            try:
                await studio_users.set_password(session, existing, password)
            except studio_users.UserError as exc:
                print(f"пароль не принят: {exc}", file=sys.stderr)
                return 1
            existing.role = role
            existing.is_active = True
            action = "пароль сменён"
        else:
            try:
                await studio_users.create_user(
                    session,
                    email=email,
                    password=password,
                    role=role,
                    display_name=args.name,
                )
            except studio_users.UserError as exc:
                print(f"завести не вышло: {exc}", file=sys.stderr)
                return 1
            action = "учётка заведена"

        await session.commit()

    print(f"{action}: {email} (роль {role})")
    if generated:
        print()
        print(f"    пароль: {password}")
        print()
        print("Он показан один раз и нигде не сохранён — в базе только argon2id-хеш.")
        print("Смена: POST /api/auth/password или python3 -m app.seed_admin --reset-password")
    print()
    if role == ROLE_ADMIN:
        print("У админа нет баланса — у него нет кассы: шаги не тарифицируются вовсе.")
    else:
        print("Для роли member включена касса и изоляция арендатора (RLS).")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбор аргументов отдельно от исполнения.

    Нужно тестам: `main` заворачивает всё в `asyncio.run`, а внутри уже
    работающего цикла (а тесты асинхронные) это `RuntimeError`. Разделение
    даёт им точку входа, которую можно просто `await`.
    """
    parser = argparse.ArgumentParser(description="Завести учётную запись студии")
    parser.add_argument("--email", default="admin@studio.local", help="адрес учётки")
    parser.add_argument("--name", default="Администратор", help="отображаемое имя")
    parser.add_argument(
        "--role",
        default="admin",
        choices=["admin", "member"],
        help="роль: admin (без кассы) или member (с балансом и RLS)",
    )
    parser.add_argument("--password", default="", help="пароль (виден в списке процессов)")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="прочитать пароль из стандартного потока — не виден в аргументах",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="сменить пароль существующей учётке и вернуть ей роль админа",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
