"""Тесты регистрации и сброса пароля через одноразовый код (OTP)."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import EmailVerification, StudioUser
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

STRONG_PWD = "mK8#vL2$xP9!qW4@"  # gitleaks:allow
NEW_STRONG_PWD = "zR7*tB3&yN5^vX1#"  # gitleaks:allow


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "reg.db")
    ah.bind_identity_session(monkeypatch, factory)

    from app.web.routers import auth as auth_router

    auth_router._FAILURES.clear()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="existing@studio.local")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "factory": factory}
    await engine.dispose()


async def test_register_send_code_and_confirm_success(env) -> None:
    client = env["client"]
    factory = env["factory"]
    email = "newuser@studio.local"

    # 1. Запрос кода
    res = await client.post(
        "/api/auth/register/send-code",
        json={"email": email, "password": STRONG_PWD, "display_name": "Тестовый Юзер"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # 2. Достаем проверочный код из базы
    async with factory() as session:
        records = (
            await session.execute(
                select(EmailVerification).where(
                    EmailVerification.email == email, EmailVerification.purpose == "register"
                )
            )
        ).scalars().all()
        assert len(records) == 1

        # Для подтверждения нужен открытый код, но в базе он захеширован.
        # В тестах или при симуляции генерируем и проверяем хеш или задаем известный код:
        from app.services.verification_codes import hash_otp

        # Заменим хеш на хеш известного кода "123456"
        records[0].code_hash = hash_otp("123456")
        await session.commit()

    # 3. Подтверждаем регистрацию
    confirm_res = await client.post(
        "/api/auth/register/confirm",
        json={
            "email": email,
            "code": "123456",
            "password": STRONG_PWD,
            "display_name": "Тестовый Юзер",
        },
    )
    assert confirm_res.status_code == 200
    data = confirm_res.json()
    assert data["ok"] is True
    assert data["email"] == email
    token = data["token"]
    assert token

    # 4. Проверяем, что токен работает и пользователь создан
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    # 5. Проверяем, что код удален (нельзя переиспользовать)
    async with factory() as session:
        left = (
            await session.execute(
                select(EmailVerification).where(
                    EmailVerification.email == email, EmailVerification.purpose == "register"
                )
            )
        ).scalars().all()
        assert len(left) == 0


async def test_register_duplicate_email_rejected(env) -> None:
    client = env["client"]
    res = await client.post(
        "/api/auth/register/send-code",
        json={"email": "existing@studio.local", "password": STRONG_PWD},
    )
    assert res.status_code == 400
    assert "уже зарегистрирована" in res.json()["detail"]


async def test_register_weak_password_rejected(env) -> None:
    client = env["client"]
    # 1. Слишком короткий пароль (< 6 символов) отсекается Pydantic (422)
    res_short = await client.post(
        "/api/auth/register/send-code",
        json={"email": "weak@studio.local", "password": "123"},
    )
    assert res_short.status_code == 422

    # 2. Пароль из списка запрещенных отсекается assert_strong (400)
    res_weak = await client.post(
        "/api/auth/register/send-code",
        json={"email": "weak@studio.local", "password": "123456789012"},
    )
    assert res_weak.status_code == 400
    assert "списках подбора" in res_weak.json()["detail"]


async def test_register_wrong_code_rejected(env) -> None:
    client = env["client"]
    email = "wrongcode@studio.local"

    await client.post(
        "/api/auth/register/send-code",
        json={"email": email, "password": STRONG_PWD},
    )

    confirm_res = await client.post(
        "/api/auth/register/confirm",
        json={"email": email, "code": "000000", "password": STRONG_PWD},
    )
    assert confirm_res.status_code == 400
    assert "Неверный код" in confirm_res.json()["detail"]


async def test_register_rate_limit(env) -> None:
    client = env["client"]
    email = "ratelimit@studio.local"

    first = await client.post(
        "/api/auth/register/send-code",
        json={"email": email, "password": STRONG_PWD},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/register/send-code",
        json={"email": email, "password": STRONG_PWD},
    )
    assert second.status_code == 429
    assert "через" in second.json()["detail"]


async def test_reset_password_flow(env) -> None:
    client = env["client"]
    factory = env["factory"]
    email = "existing@studio.local"

    # 1. Запрос сброса пароля
    res = await client.post(
        "/api/auth/reset-password/send-code",
        json={"email": email},
    )
    assert res.status_code == 200

    # 2. Подменяем хеш на известный код в тестовой базе
    async with factory() as session:
        from app.services.verification_codes import hash_otp

        record = (
            await session.execute(
                select(EmailVerification).where(
                    EmailVerification.email == email, EmailVerification.purpose == "reset_password"
                )
            )
        ).scalar_one()
        record.code_hash = hash_otp("654321")
        await session.commit()

    # 3. Подтверждаем сброс пароля
    confirm_res = await client.post(
        "/api/auth/reset-password/confirm",
        json={"email": email, "code": "654321", "new_password": NEW_STRONG_PWD},
    )
    assert confirm_res.status_code == 200
    assert confirm_res.json()["ok"] is True

    # 4. Вход со старым паролем больше не работает
    login_old = await client.post(
        "/api/auth/login",
        json={"email": email, "password": ah.PASSWORD},
    )
    assert login_old.status_code == 401

    # 5. Вход с новым паролем работает
    login_new = await client.post(
        "/api/auth/login",
        json={"email": email, "password": NEW_STRONG_PWD},
    )
    assert login_new.status_code == 200
    assert login_new.json()["token"]
