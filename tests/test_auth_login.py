"""Форма входа: выдача токена, cookie, блокировка подбора, смена пароля.

Заменяет ту часть `tests/test_sso_middleware.py`, где проверялось, что своей
формы входа нет. Форма появилась вместе с таблицей `studio_users`, и теперь
проверять надо обратное — что она работает и что её нельзя перебрать.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

# asyncio_mode = "auto" в pyproject: асинхронные тесты подхватываются сами,
# а модульная метка вешала бы её и на синхронные — pytest на это ругается.

NEW_PASSWORD = "Qn8v-Ld3x-Bm6t-Wr2z"  # gitleaks:allow — фикстура, живёт только в тестах


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "login.db")
    ah.bind_identity_session(monkeypatch, factory)

    # Счётчик неудач живёт в памяти модуля и переживает тесты внутри прогона.
    from app.web.routers import auth as auth_router

    auth_router._FAILURES.clear()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "factory": factory}
    await engine.dispose()


async def test_login_returns_a_working_token(env) -> None:
    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert res.status_code == 200
    token = res.json()["token"]
    me = await env["client"].get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["tenant_id"] == env["member"].user_id


async def test_login_sets_a_cookie_for_streams(env) -> None:
    """`EventSource` и WebSocket заголовков не ставят — им нужна cookie."""
    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert res.cookies.get("vp_session")


async def test_session_cookie_is_httponly_and_lax(env) -> None:
    """`HttpOnly` — против XSS, `SameSite=Lax` — против запросов с чужого сайта."""
    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    raw = res.headers.get("set-cookie", "").lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw


async def test_secure_flag_follows_the_setting(env, monkeypatch) -> None:
    """По умолчанию `Secure` выключен: студия живёт на localhost без TLS,
    и cookie с этим флагом там не установилась бы вовсе."""
    from app.settings import settings

    plain = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert "secure" not in plain.headers.get("set-cookie", "").lower()

    monkeypatch.setattr(settings, "session_cookie_secure", True)
    behind_tls = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert "secure" in behind_tls.headers.get("set-cookie", "").lower()


async def test_login_case_insensitive_in_the_address(env) -> None:
    res = await env["client"].post(
        "/api/auth/login", json={"email": "MEMBER@Studio.Local", "password": ah.PASSWORD}
    )
    assert res.status_code == 200


async def test_wrong_password_is_401(env) -> None:
    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
    )
    assert res.status_code == 401


async def test_unknown_address_looks_exactly_like_a_wrong_password(env) -> None:
    """Различать эти два ответа — значит отдать форме входа функцию справочника."""
    missing = await env["client"].post(
        "/api/auth/login", json={"email": "nobody@studio.local", "password": ah.PASSWORD}
    )
    wrong = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"]


async def test_brute_force_is_locked_out_after_the_threshold(env) -> None:
    """Поток попыток дорог не подбирающему, а серверу: каждая занимает ядро.

    После порога отказ приходит БЕЗ единого хеширования — это и есть смысл
    счётчика. Проверяется код 429 и заголовок `Retry-After`, по которому
    клиент понимает, что ждать, а не что пароль не тот.
    """
    from app.web.routers.auth import MAX_ATTEMPTS

    for _ in range(MAX_ATTEMPTS):
        res = await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
        )
        assert res.status_code == 401

    locked = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
    )
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


async def test_lockout_holds_even_with_the_right_password(env) -> None:
    """Иначе блокировка не блокирует: подбор просто продолжается до попадания."""
    from app.web.routers.auth import MAX_ATTEMPTS

    for _ in range(MAX_ATTEMPTS):
        await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
        )

    res = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert res.status_code == 429


async def test_successful_login_clears_the_counter(env) -> None:
    """Иначе человек, ошибившийся семь раз за месяц, однажды заперт навсегда."""
    from app.web.routers.auth import _FAILURES, MAX_ATTEMPTS

    for _ in range(MAX_ATTEMPTS - 1):
        await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
        )
    ok = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert ok.status_code == 200
    assert "member@studio.local" not in _FAILURES


async def test_lockout_is_per_address(env) -> None:
    """Заперев один адрес, нельзя запирать всех: это отказ в обслуживании."""
    from app.web.routers.auth import MAX_ATTEMPTS

    await ah.make_account(env["factory"], email="other@studio.local")
    for _ in range(MAX_ATTEMPTS + 1):
        await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
        )

    other = await env["client"].post(
        "/api/auth/login", json={"email": "other@studio.local", "password": ah.PASSWORD}
    )
    assert other.status_code == 200


async def test_logout_clears_the_cookie(env) -> None:
    await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    out = await env["client"].post("/api/auth/logout", headers=env["member"].auth)
    assert out.status_code == 200
    # Выход не отзывает токен, и ответ говорит об этом прямо, а не делает вид.
    assert out.json()["revoked_all"] is False


async def test_password_change_requires_the_current_one(env) -> None:
    """Иначе украденная сессия превращается в захват аккаунта."""
    res = await env["client"].post(
        "/api/auth/password",
        json={"current_password": "не тот", "new_password": NEW_PASSWORD},
        headers=env["member"].auth,
    )
    assert res.status_code == 401


async def test_password_change_refuses_a_weak_new_password(env) -> None:
    res = await env["client"].post(
        "/api/auth/password",
        json={"current_password": ah.PASSWORD, "new_password": "korotko1"},
        headers=env["member"].auth,
    )
    assert res.status_code == 400
    assert "короче" in res.json()["detail"]


async def test_password_change_kills_other_sessions_but_not_this_one(env) -> None:
    """Человек только что доказал, что это он: выкидывать его самого незачем.

    Все остальные выданные токены при этом обязаны погаснуть — пароль меняют
    по подозрению на утечку, и оставить их живыми значит не сделать ничего.
    """
    old_token = env["member"].token
    res = await env["client"].post(
        "/api/auth/password",
        json={"current_password": ah.PASSWORD, "new_password": NEW_PASSWORD},
        headers=env["member"].auth,
    )
    assert res.status_code == 200
    fresh = res.json()["token"]

    assert (
        await env["client"].get("/api/me", headers={"Authorization": f"Bearer {fresh}"})
    ).status_code == 200
    assert (
        await env["client"].get("/api/me", headers={"Authorization": f"Bearer {old_token}"})
    ).status_code == 401


async def test_new_password_actually_works_for_login(env) -> None:
    await env["client"].post(
        "/api/auth/password",
        json={"current_password": ah.PASSWORD, "new_password": NEW_PASSWORD},
        headers=env["member"].auth,
    )
    ok = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": NEW_PASSWORD}
    )
    assert ok.status_code == 200
    stale = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert stale.status_code == 401


async def test_password_change_needs_a_token(env) -> None:
    res = await env["client"].post(
        "/api/auth/password",
        json={"current_password": ah.PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert res.status_code == 401


async def test_password_change_after_the_account_disappeared(env) -> None:
    """Токен подписан верно, а учётки уже нет.

    Ветка выглядит недостижимой — middleware ходит в базу и отсекает такое
    раньше. Но `find_by_email` в смене пароля ищет по АДРЕСУ из токена, а не
    по id: смена адреса учётки оставляет живой токен со старым адресом, и
    ручка обязана ответить отказом, а не упасть на `None.email`.
    """
    from app.services import studio_users

    async with env["factory"]() as s:
        user = await studio_users.find_by_email(s, "member@studio.local")
        user.email = "renamed@studio.local"
        await s.commit()

    res = await env["client"].post(
        "/api/auth/password",
        json={"current_password": ah.PASSWORD, "new_password": NEW_PASSWORD},
        headers=env["member"].auth,
    )
    assert res.status_code == 401
    assert "больше нет" in res.json()["detail"]


async def test_lockout_expires_after_the_window(env, monkeypatch) -> None:
    """Блокировка временная, а не вечная.

    Без истечения восьмая опечатка за месяц запирала бы человека навсегда, и
    чинилось бы это перезапуском сервера.
    """
    from app.web.routers import auth as auth_router

    for _ in range(auth_router.MAX_ATTEMPTS):
        await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": "не тот"}
        )
    assert (
        await env["client"].post(
            "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
        )
    ).status_code == 429

    # Отматываем время последней неудачи за пределы окна.
    count, _ = auth_router._FAILURES["member@studio.local"]
    import time as _time

    auth_router._FAILURES["member@studio.local"] = (count, _time.time() - auth_router.LOCKOUT_SEC - 1)

    ok = await env["client"].post(
        "/api/auth/login", json={"email": "member@studio.local", "password": ah.PASSWORD}
    )
    assert ok.status_code == 200
