"""Заведение, проверка и отзыв учётных записей студии.

Слой между таблицей `studio_users` и всем, что хочет знать «кто это и пустить
ли». Роутер входа, сидер админа и будущая страница управления людьми зовут
одни и те же функции — иначе правило «пароль проверяется вот так» разъезжается
по трём местам и в одном из них однажды забудут `dummy_verify`.

**Заведение счёта идёт вместе с учёткой.** Пользователь без строки в
`credit_accounts` существовать не должен: первая же котировка шага полезет за
балансом и получит `None`. Раньше счёт заводился при первом входе с токеном
биллинга (`tenant_provisioning.ensure_tenant`), потому что регистрации у студии
не было вовсе. Теперь регистрация есть, и момент известен точно — здесь.

**Админу счёт тоже заводится.** Не ради денег: у него их нет и не будет
(`step_billing` не тарифицирует админа вовсе). Ради того, чтобы у всех
пользователей была одинаковая форма и ни один запрос не встретил `None` там,
где ожидается счёт. Пустой счёт с нулём — это дешевле, чем ветка «а если админ»
в каждом читателе баланса.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.db import rebind_tenant
from app.models import StudioUser
from app.services import passwords
from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER, ROLES, StudioIdentity, identity_of, new_user_id
from app.services.tenant import tenant_scope


class UserError(RuntimeError):
    """Учётку завести нельзя: занятый адрес, неизвестная роль, слабый пароль."""


@dataclass(frozen=True)
class LoginResult:
    """Итог проверки пары адрес/пароль."""

    identity: StudioIdentity
    #: Хеш пересчитан по актуальному профилю argon2 — строку надо сохранить.
    rehashed: bool = False


def normalize_email(email: str) -> str:
    """Один адрес — одна учётка. Регистр и пробелы значения не имеют."""
    return (email or "").strip().lower()


async def create_user(
    session: Any,
    *,
    email: str,
    password: str,
    role: str = ROLE_MEMBER,
    display_name: str = "",
) -> StudioUser:
    """Завести учётку и счёт под ней. Пароль проверяется на стойкость."""
    from app.services import credit_ledger as cl

    addr = normalize_email(email)
    if not addr or "@" not in addr:
        raise UserError(f"адрес {email!r} не похож на почтовый")
    if role not in ROLES:
        raise UserError(f"роль {role!r} неизвестна; есть: {', '.join(sorted(ROLES))}")
    if await find_by_email(session, addr) is not None:
        raise UserError(f"учётка {addr} уже заведена")

    try:
        passwords.assert_strong(password)
    except passwords.WeakPasswordError as exc:
        raise UserError(str(exc)) from exc

    user = StudioUser(
        id=new_user_id(),
        email=addr,
        password_hash=passwords.hash_password(password),
        role=role,
        display_name=(display_name or "").strip(),
        is_active=True,
        token_epoch=0,
    )
    session.add(user)
    # flush, а не commit: вызывающий решает границу транзакции. Но id нужен
    # прямо сейчас — счёт заводится под него.
    await session.flush()

    # Арендатор назначается ЯВНО, и это не перестраховка. `studio_users` вне
    # RLS — это список арендаторов, а не их данные, — а `credit_accounts` под
    # политикой, и `WITH CHECK` требует совпадения `app.tenant_id`. Учётку
    # заводит админ или сидер, то есть контекст на входе либо чужой, либо
    # пустой, и вставка счёта упиралась бы в политику:
    # «new row violates row-level security policy for table credit_accounts».
    # Поймано прогоном `tests/test_rls_postgres.py` на живом Postgres; на
    # SQLite политик нет, и там это было незаметно.
    with tenant_scope(user.id):
        # Транзакция открылась ещё на проверке «адрес не занят», когда
        # арендатора не было, — а `after_begin` срабатывает один раз. Одного
        # `tenant_scope` поэтому мало: нужна явная перепривязка.
        await rebind_tenant(session)
        await cl.ensure_account(session, user.id)
        await _grant_start_credits(session, user)
    return user


async def find_by_email(session: Any, email: str) -> StudioUser | None:
    """Учётка по адресу. Регистр не важен — колонка хранится в нижнем."""
    addr = normalize_email(email)
    if not addr:
        return None
    found = await session.execute(select(StudioUser).where(StudioUser.email == addr))
    return found.scalar_one_or_none()


async def authenticate(session: Any, *, email: str, password: str) -> LoginResult:
    """Проверить пару адрес/пароль. Не сошлось — `UserError` с общим текстом.

    Текст отказа один на все случаи: «нет такого адреса», «пароль неверный» и
    «учётка отключена» снаружи неразличимы намеренно. Различать их — значит
    отдать форме входа функцию справочника: подбирающий узнаёт, какие адреса
    заведены, не угадав ни одного пароля.

    Время ответа тоже одинаково: по несуществующему адресу считается фиктивный
    хеш (`passwords.dummy_verify`). Без этого «нет такого» отвечается мгновенно,
    а «есть, но пароль не тот» — за десятки миллисекунд, и список аккаунтов
    вычитывается секундомером.
    """
    refusal = "неверный адрес или пароль"

    user = await find_by_email(session, email)
    if user is None or not user.is_active or not user.password_hash:
        passwords.dummy_verify()
        raise UserError(refusal)

    if not passwords.verify_password(password, user.password_hash):
        raise UserError(refusal)

    rehashed = False
    if passwords.needs_rehash(user.password_hash):
        # Единственный момент, когда открытый пароль в руках, — вот этот.
        # Перехешируем сейчас или не перехешируем никогда.
        user.password_hash = passwords.hash_password(password)
        rehashed = True

    from app.models import _now

    user.last_login_at = _now()
    return LoginResult(identity=identity_of(user), rehashed=rehashed)


async def set_password(session: Any, user: StudioUser, new_password: str) -> None:
    """Сменить пароль и обесценить все выданные токены.

    Поднятие `token_epoch` — не перестраховка. Пароль меняют по двум поводам:
    плановой ротации и подозрению на утечку. Во втором случае оставить старые
    сессии живыми значит не сделать ничего.
    """
    try:
        passwords.assert_strong(new_password)
    except passwords.WeakPasswordError as exc:
        raise UserError(str(exc)) from exc
    user.password_hash = passwords.hash_password(new_password)
    user.token_epoch = int(user.token_epoch or 0) + 1


async def deactivate(session: Any, user: StudioUser) -> None:
    """Закрыть вход, сохранив данные и историю расходов.

    Строка не удаляется: на арендатора ссылаются проекты, проводки и журналы
    вызовов. Удаление осиротило бы их и стёрло бы историю трат вместе с
    человеком, а `token_epoch` закрывает доступ ровно так же немедленно.
    """
    user.is_active = False
    user.token_epoch = int(user.token_epoch or 0) + 1


async def count_admins(session: Any) -> int:
    """Сколько живых админов. Ноль означает систему без оператора."""
    found = await session.execute(
        select(func.count())
        .select_from(StudioUser)
        .where(StudioUser.role == ROLE_ADMIN, StudioUser.is_active.is_(True))
    )
    return int(found.scalar_one())


async def _grant_start_credits(session: Any, user: StudioUser) -> None:
    """Стартовый подарок, если он настроен. Админу не нужен — у него нет кассы."""
    from app.services import credit_ledger as cl
    from app.services.credits import price_micro
    from app.settings import settings

    if user.role == ROLE_ADMIN:
        return
    start = float(getattr(settings, "tenant_start_credits", 0.0) or 0.0)
    if start <= 0:
        return
    # Подарок задан В КРЕДИТАХ, а не в себестоимости: это цена, которую
    # человек может потратить, поэтому маржа к нему не применяется.
    amount = price_micro(start, with_margin=False)
    if amount > 0:
        await cl.topup(session, user.id, amount, memo="стартовый баланс при заведении учётки")


# ── Админ по арендатору — для кассы в воркере ────────────────────────────
#
# `current_is_admin()` знает личность только внутри HTTP-запроса. Воркер
# идёт без запроса, с одним `tenant_id`, и для него админ неотличим от
# клиента с нулём на счету: в интерфейсе «∞», а в журнале «шаг video ждёт
# пополнения — доступно 0 кр». Так первый живой прогон встал на видео
# (2026-08-26), при том что все дешёвые шаги прошли бесплатным уровнем.
#
# Кэш — на минуту. Роль меняется редко, а спрашивать базу на каждом такте
# воркера (раз в пять секунд) незачем. Минута — потолок опоздания, с которым
# отобранная роль ещё считается админской.
_ADMIN_CACHE: dict[str, tuple[float, bool]] = {}
_ADMIN_CACHE_TTL = 60.0


async def tenant_is_admin(session: Any, tenant_id: str | None) -> bool:
    """Арендатор — активный админ? Для кассы там, где личности запроса нет."""
    import time

    if not tenant_id:
        return False
    now = time.monotonic()
    hit = _ADMIN_CACHE.get(tenant_id)
    if hit is not None and now - hit[0] < _ADMIN_CACHE_TTL:
        return hit[1]
    row = (
        await session.execute(select(StudioUser.role, StudioUser.is_active).where(StudioUser.id == tenant_id))
    ).first()
    is_admin = bool(row and row[0] == ROLE_ADMIN and row[1])
    _ADMIN_CACHE[tenant_id] = (now, is_admin)
    return is_admin


def forget_admin_cache(tenant_id: str | None = None) -> None:
    """Сбросить кэш роли — после смены роли или в тестах."""
    if tenant_id is None:
        _ADMIN_CACHE.clear()
    else:
        _ADMIN_CACHE.pop(tenant_id, None)
