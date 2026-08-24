"""Заведение, вход, смена пароля, отзыв доступа.

Слой между таблицей `studio_users` и формой входа. Проверяется не «работает ли
SQLAlchemy», а решения, каждое из которых легко потерять при следующей правке:
единый текст отказа, счёт вместе с учёткой, поколение токена при смене пароля.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import CreditAccount, StudioUser
from app.services import studio_users
from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER
from tests.accounts_harness import PASSWORD, configure, make_engine

# asyncio_mode = "auto" в pyproject: асинхронные тесты подхватываются сами,
# а модульная метка вешала бы её и на синхронные — pytest на это ругается.


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    # `configure`, а не выборочная подмена пары настроек. Первая версия
    # ставила только `tenant_start_credits`, и модуль проходил в одиночку, но
    # падал в общем прогоне: `create_user` заводит счёт под `tenant_scope`, а
    # `require_isolation` запрещает арендаторов на SQLite без явного
    # `ALLOW_UNISOLATED_TENANTS`. В одиночку он был выставлен утёкшей
    # настройкой соседнего модуля — то есть тест зависел от порядка и об этом
    # молчал.
    configure(monkeypatch)
    engine, factory = await make_engine(tmp_path / "users.db")
    yield factory
    await engine.dispose()


async def test_created_user_can_log_in(factory) -> None:
    async with factory() as s:
        await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        result = await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)
        assert result.identity.email == "ivan@studio.local"
        assert result.identity.role == ROLE_MEMBER


async def test_user_id_is_a_uuid_and_becomes_the_tenant(factory) -> None:
    """`id` уезжает в `SET LOCAL app.tenant_id` — форма обязана быть канонической."""
    import uuid

    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()
        assert str(uuid.UUID(user.id)) == user.id


async def test_account_is_created_together_with_the_user(factory) -> None:
    """Пользователь без счёта — это `None` при первой же котировке шага."""
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()
        uid = user.id

    async with factory() as s:
        assert await s.get(CreditAccount, uid) is not None


async def test_admin_gets_an_account_too(factory) -> None:
    """Не ради денег — ради одинаковой формы у всех пользователей."""
    async with factory() as s:
        user = await studio_users.create_user(
            s, email="boss@studio.local", password=PASSWORD, role=ROLE_ADMIN
        )
        await s.commit()
        assert await s.get(CreditAccount, user.id) is not None


async def test_email_is_stored_lowercased(factory) -> None:
    """Иначе `Ivan@` и `ivan@` станут двумя учётками одного человека."""
    async with factory() as s:
        user = await studio_users.create_user(s, email="  IVAN@Studio.Local ", password=PASSWORD)
        await s.commit()
        assert user.email == "ivan@studio.local"


async def test_login_ignores_the_case_of_the_address(factory) -> None:
    async with factory() as s:
        await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        result = await studio_users.authenticate(s, email="IVAN@STUDIO.LOCAL", password=PASSWORD)
        assert result.identity.email == "ivan@studio.local"


async def test_duplicate_address_is_refused(factory) -> None:
    async with factory() as s:
        await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        with pytest.raises(studio_users.UserError, match="уже заведена"):
            await studio_users.create_user(s, email="Ivan@Studio.local", password=PASSWORD)


async def test_weak_password_is_refused_at_creation(factory) -> None:
    async with factory() as s:
        with pytest.raises(studio_users.UserError, match="короче"):
            await studio_users.create_user(s, email="ivan@studio.local", password="korotko1")


async def test_unknown_role_is_refused(factory) -> None:
    async with factory() as s:
        with pytest.raises(studio_users.UserError, match="роль"):
            await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD, role="superuser")


async def test_refusal_text_is_the_same_for_missing_user_and_wrong_password(factory) -> None:
    """Различать эти два случая — значит отдать форме входа функцию справочника.

    Подбирающий узнаёт список заведённых адресов, не угадав ни одного пароля,
    и дальше работает только по ним.
    """
    async with factory() as s:
        await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        with pytest.raises(studio_users.UserError) as no_user:
            await studio_users.authenticate(s, email="nobody@studio.local", password=PASSWORD)
        with pytest.raises(studio_users.UserError) as bad_pwd:
            await studio_users.authenticate(s, email="ivan@studio.local", password="не тот пароль")
    assert str(no_user.value) == str(bad_pwd.value)


async def test_deactivated_user_cannot_log_in_and_looks_the_same(factory) -> None:
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await studio_users.deactivate(s, user)
        await s.commit()

    async with factory() as s:
        with pytest.raises(studio_users.UserError, match="неверный адрес или пароль"):
            await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)


async def test_deactivation_keeps_the_row(factory) -> None:
    """Строку нельзя удалять: на арендатора ссылаются проекты и проводки."""
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await studio_users.deactivate(s, user)
        await s.commit()

    async with factory() as s:
        rows = (await s.execute(select(StudioUser))).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_active is False


async def test_deactivation_bumps_the_epoch(factory) -> None:
    """Иначе выданный токен работает до истечения срока, то есть отзыва нет."""
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        before = int(user.token_epoch)
        await studio_users.deactivate(s, user)
        assert int(user.token_epoch) == before + 1


async def test_password_change_bumps_the_epoch(factory) -> None:
    """Пароль меняют по подозрению на утечку — старые сессии обязаны погаснуть."""
    new = "Qn8v-Ld3x-Bm6t-Wr2z"
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        before = int(user.token_epoch)
        await studio_users.set_password(s, user, new)
        await s.commit()
        assert int(user.token_epoch) == before + 1

    async with factory() as s:
        await studio_users.authenticate(s, email="ivan@studio.local", password=new)
        with pytest.raises(studio_users.UserError):
            await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)


async def test_weak_new_password_is_refused_and_epoch_stays(factory) -> None:
    """Отказ не должен обесценивать сессии: иначе неудачная смена — это выход."""
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        before = int(user.token_epoch)
        with pytest.raises(studio_users.UserError):
            await studio_users.set_password(s, user, "korotko1")
        assert int(user.token_epoch) == before


async def test_successful_login_stamps_the_time(factory) -> None:
    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()
        assert user.last_login_at is None

    async with factory() as s:
        await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        found = await studio_users.find_by_email(s, "ivan@studio.local")
        assert found is not None and found.last_login_at is not None


async def test_count_admins_sees_only_live_ones(factory) -> None:
    """Ноль живых админов означает систему без оператора — это надо уметь узнать."""
    async with factory() as s:
        await studio_users.create_user(s, email="a@studio.local", password=PASSWORD, role=ROLE_ADMIN)
        boss = await studio_users.create_user(s, email="b@studio.local", password=PASSWORD, role=ROLE_ADMIN)
        await studio_users.create_user(s, email="c@studio.local", password=PASSWORD)
        await s.commit()
        assert await studio_users.count_admins(s) == 2

        await studio_users.deactivate(s, boss)
        await s.commit()
        assert await studio_users.count_admins(s) == 1


async def test_start_credits_go_to_members_and_not_to_admins(factory, monkeypatch) -> None:
    """У админа нет кассы — стартовый подарок ему не имеет смысла."""
    from app.services.credit_ledger import balance_micro
    from app.settings import settings

    monkeypatch.setattr(settings, "tenant_start_credits", 5.0)
    async with factory() as s:
        member = await studio_users.create_user(s, email="m@studio.local", password=PASSWORD)
        boss = await studio_users.create_user(s, email="a@studio.local", password=PASSWORD, role=ROLE_ADMIN)
        await s.commit()
        assert await balance_micro(s, member.id) == 5_000_000
        assert await balance_micro(s, boss.id) == 0


async def test_address_without_at_sign_is_refused(factory) -> None:
    async with factory() as s:
        with pytest.raises(studio_users.UserError, match="почтовый"):
            await studio_users.create_user(s, email="ivan", password=PASSWORD)


async def test_find_by_email_on_blank_address(factory) -> None:
    """Пустой адрес — не «найди первого», а «никого».

    Ветка выглядит лишней ровно до того дня, когда форму входа отправят с
    пустым полем: `select where email == ''` вернул бы учётку, если бы такая
    в базе завелась, а завестись пустая может при прямой правке.
    """
    async with factory() as s:
        assert await studio_users.find_by_email(s, "") is None
        assert await studio_users.find_by_email(s, "   ") is None


async def test_login_rehashes_a_password_stored_with_a_weaker_profile(factory) -> None:
    """Успешный вход — единственный момент, когда открытый пароль в руках.

    Перехешируем сейчас или не перехешируем никогда: из хеша пароль не
    достать, а профиль argon2 со временем поднимают. Без этой ветки учётки,
    заведённые год назад, навсегда остаются на старых параметрах.
    """
    from argon2 import PasswordHasher

    from app.services import passwords

    async with factory() as s:
        user = await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        # Подменяем хеш на посчитанный по заведомо слабому профилю.
        user.password_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
        await s.commit()
        weak = user.password_hash

    async with factory() as s:
        result = await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)
        assert result.rehashed is True
        await s.commit()

    async with factory() as s:
        user = await studio_users.find_by_email(s, "ivan@studio.local")
        assert user.password_hash != weak
        assert passwords.needs_rehash(user.password_hash) is False
        # И пароль после перехеширования всё ещё тот же самый.
        await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)


async def test_login_does_not_rehash_a_current_hash(factory) -> None:
    """Обратная сторона: лишний argon2 на каждом входе — это 64 МиБ впустую."""
    async with factory() as s:
        await studio_users.create_user(s, email="ivan@studio.local", password=PASSWORD)
        await s.commit()

    async with factory() as s:
        result = await studio_users.authenticate(s, email="ivan@studio.local", password=PASSWORD)
        assert result.rehashed is False
