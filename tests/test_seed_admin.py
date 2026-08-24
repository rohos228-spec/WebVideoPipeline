"""Сидер администратора: пароль, идемпотентность, отказ без секрета.

Сидер — это тот код, который запускают один раз на установке и который никто
никогда не читает. Поэтому его поведение должно быть проверено целиком: ошибка
здесь означает либо установку без оператора, либо установку с известным всем
паролем.
"""

from __future__ import annotations

import math

import pytest
import pytest_asyncio

from app import seed_admin
from app.services import studio_users
from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER
from tests import accounts_harness as ah

# asyncio_mode = "auto" в pyproject: асинхронные тесты подхватываются сами,
# а модульная метка вешала бы её и на синхронные — pytest на это ругается.


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "seed.db")
    ah.bind_identity_session(monkeypatch, factory)
    yield factory
    await engine.dispose()


# ── генератор пароля ─────────────────────────────────────────────────────────


def test_generated_password_passes_the_strength_check() -> None:
    """Сидер, который генерирует непринимаемый пароль, — это сломанный сидер."""
    from app.services import passwords

    for _ in range(20):
        passwords.assert_strong(seed_admin.generate_password())


def test_generated_password_is_not_repeated() -> None:
    """Двадцать вызовов — двадцать разных паролей. Иначе это константа."""
    seen = {seed_admin.generate_password() for _ in range(20)}
    assert len(seen) == 20


def test_generated_password_has_enough_entropy() -> None:
    """Заявленная стойкость обязана быть арифметически верной.

    Проверка стоит здесь потому, что первая версия этого генератора брала
    четыре слова из словаря на 64 слова — 24 бита — и объявляла в документации
    51. Такая ошибка не видна ни на глаз, ни прогоном: пароли выглядят
    одинаково солидно.
    """
    bits = seed_admin._GENERATED_LENGTH * math.log2(len(seed_admin._ALPHABET))
    assert bits >= 100, f"пароль даёт {bits:.0f} бит"


def test_generated_password_avoids_lookalike_characters() -> None:
    """`0`/`O` и `1`/`l`/`I` не различить на глаз при перенаборе."""
    assert not (set("0O1lI") & set(seed_admin._ALPHABET))


def test_generated_password_is_grouped_for_reading() -> None:
    """Сплошная строка из двадцати символов не диктуется по телефону."""
    assert seed_admin.generate_password().count("-") >= 3


# ── заведение ────────────────────────────────────────────────────────────────


async def test_seed_creates_an_admin(env) -> None:
    code = await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local"]))
    assert code == 0

    async with env() as s:
        user = await studio_users.find_by_email(s, "boss@studio.local")
        assert user is not None
        assert user.role == ROLE_ADMIN
        assert user.is_active is True
        assert user.password_hash.startswith("$argon2id$")


async def test_seeded_admin_can_actually_log_in(env, capsys) -> None:
    """Главное свойство сидера: напечатанный пароль обязан работать.

    Без этой проверки сидер мог бы печатать один пароль, а хешировать другой —
    и выяснилось бы это на живой установке, где чинить уже нечем.
    """
    await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local"]))
    printed = capsys.readouterr().out
    password = next(line.split("пароль:")[1].strip() for line in printed.splitlines() if "пароль:" in line)

    async with env() as s:
        result = await studio_users.authenticate(s, email="boss@studio.local", password=password)
    assert result.identity.is_admin is True


async def test_seed_is_idempotent(env, capsys) -> None:
    """Повторный запуск не заводит второго админа и не трогает пароль.

    Сидер, меняющий пароль на каждый запуск, однажды сотрёт рабочий доступ при
    перезапуске установки — а запускают его как раз в тот момент, когда что-то
    пошло не так.
    """
    await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local"]))
    first = capsys.readouterr().out
    password = next(line.split("пароль:")[1].strip() for line in first.splitlines() if "пароль:" in line)

    assert await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local"])) == 0
    second = capsys.readouterr().out
    assert "уже заведена" in second
    assert "пароль:" not in second

    async with env() as s:
        assert await studio_users.count_admins(s) == 1
        await studio_users.authenticate(s, email="boss@studio.local", password=password)


async def test_given_password_is_not_printed(env, capsys) -> None:
    """Свой пароль печатать незачем — он уже у человека в руках."""
    await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local", "--password", ah.PASSWORD]))
    assert ah.PASSWORD not in capsys.readouterr().out


async def test_weak_given_password_is_refused(env, capsys) -> None:
    code = await seed_admin.run(
        seed_admin.parse_args(["--email", "boss@studio.local", "--password", "korotko1"])
    )
    assert code == 1
    assert "короче" in capsys.readouterr().err

    async with env() as s:
        assert await studio_users.find_by_email(s, "boss@studio.local") is None


# ── смена пароля ─────────────────────────────────────────────────────────────


async def test_reset_password_changes_it(env, capsys) -> None:
    await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local", "--password", ah.PASSWORD]))
    capsys.readouterr()

    new = "Qn8v-Ld3x-Bm6t-Wr2z"
    assert (
        await seed_admin.run(
            seed_admin.parse_args(["--email", "boss@studio.local", "--reset-password", "--password", new])
        )
        == 0
    )

    async with env() as s:
        await studio_users.authenticate(s, email="boss@studio.local", password=new)
        with pytest.raises(studio_users.UserError):
            await studio_users.authenticate(s, email="boss@studio.local", password=ah.PASSWORD)


async def test_reset_restores_the_admin_role_and_access(env) -> None:
    """Сидер зовут, когда доступ потерян: «сменил пароль, но остался member» —
    худший исход, потому что выглядит как успех."""
    async with env() as s:
        user = await studio_users.create_user(
            s, email="boss@studio.local", password=ah.PASSWORD, role=ROLE_MEMBER
        )
        await studio_users.deactivate(s, user)
        await s.commit()

    new = "Qn8v-Ld3x-Bm6t-Wr2z"
    assert (
        await seed_admin.run(
            seed_admin.parse_args(["--email", "boss@studio.local", "--reset-password", "--password", new])
        )
        == 0
    )

    async with env() as s:
        user = await studio_users.find_by_email(s, "boss@studio.local")
        assert user.role == ROLE_ADMIN
        assert user.is_active is True
        await studio_users.authenticate(s, email="boss@studio.local", password=new)


# ── отказы ───────────────────────────────────────────────────────────────────


async def test_seed_refuses_without_a_session_secret(env, monkeypatch, capsys) -> None:
    """Завести админа, который никуда не войдёт, — хуже, чем отказать.

    Он выглядел бы заведённым, и разбираться пришлось бы на форме входа, где
    видно только «неверный адрес или пароль».
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "studio_session_secret", "")
    assert await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local"])) == 2
    assert "STUDIO_SESSION_SECRET" in capsys.readouterr().err

    async with env() as s:
        assert await studio_users.find_by_email(s, "boss@studio.local") is None


async def test_password_from_stdin_is_used(env, monkeypatch, capsys) -> None:
    """Пароль в аргументах виден в списке процессов — для этого и есть поток."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(f"{ah.PASSWORD}\n"))
    assert (
        await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local", "--password-stdin"])) == 0
    )
    assert ah.PASSWORD not in capsys.readouterr().out

    async with env() as s:
        await studio_users.authenticate(s, email="boss@studio.local", password=ah.PASSWORD)


async def test_empty_stdin_is_refused(env, monkeypatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    with pytest.raises(SystemExit):
        await seed_admin.run(seed_admin.parse_args(["--email", "boss@studio.local", "--password-stdin"]))
