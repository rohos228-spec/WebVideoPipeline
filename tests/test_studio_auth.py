"""Сессионный токен: что принимается, что отвергается и почему.

Заменяет `tests/test_billing_sso.py`. Проверялось там то же самое — подпись,
срок, алгоритм, форма `sub`, — но для чужого токена, который выписывал
`llm-gateway/billing`. Токен теперь свой, и к прежнему списку добавились две
вещи, которых у чужого токена быть не могло: роль и поколение.

Здесь проверяется РАЗБОР токена, без HTTP и без базы. Что закрытие висит на
входе в приложение — `tests/test_identity_middleware.py`.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from app.services.studio_auth import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    AuthError,
    StudioIdentity,
    decode_token,
    issue_token,
)
from app.settings import settings

SECRET = "тестовый-секрет-подписи-длиною-заведомо-больше-тридцати-двух-байт"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "studio_session_secret", SECRET)


def _identity(role: str = ROLE_MEMBER, epoch: int = 0) -> StudioIdentity:
    return StudioIdentity(
        user_id=str(uuid.uuid4()),
        email="client@studio.local",
        role=role,
        epoch=epoch,
    )


def test_issued_token_reads_back_the_same_identity() -> None:
    who = _identity()
    back = decode_token(issue_token(who))
    assert back.user_id == who.user_id
    assert back.email == who.email
    assert back.role == who.role
    assert back.epoch == who.epoch


def test_user_id_is_also_the_tenant_id() -> None:
    """Один человек — один арендатор, без таблицы соответствия."""
    who = _identity()
    assert decode_token(issue_token(who)).tenant_id == who.user_id


def test_admin_is_recognized_as_admin() -> None:
    assert decode_token(issue_token(_identity(ROLE_ADMIN))).is_admin is True
    assert decode_token(issue_token(_identity(ROLE_MEMBER))).is_admin is False


def test_epoch_travels_in_the_token() -> None:
    """Поколение обязано доехать: по нему сверяется отзыв."""
    assert decode_token(issue_token(_identity(epoch=7))).epoch == 7


def test_expired_token_is_refused() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": ROLE_MEMBER,
            "epoch": 0,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="срок"):
        decode_token(token)


def test_token_signed_with_another_secret_is_refused() -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": ROLE_MEMBER, "exp": int(time.time()) + 3600},
        "совсем другой секрет, тоже длинный и тоже подходящий",
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        decode_token(token)


def test_unsigned_token_is_refused() -> None:
    """`alg: none` — первое, что пробуют. Список алгоритмов задаём мы."""
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": ROLE_ADMIN, "exp": int(time.time()) + 3600},
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        decode_token(token)


def test_token_without_exp_is_refused() -> None:
    """Вечный токен — это не сессия, а ключ, который нечем отобрать."""
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": ROLE_MEMBER}, SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(token)


def test_sub_that_is_not_uuid_is_refused() -> None:
    """`sub` уезжает в `SET LOCAL app.tenant_id` — то есть в SQL."""
    token = jwt.encode(
        {"sub": "'; drop table projects; --", "role": ROLE_MEMBER, "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="UUID"):
        decode_token(token)


def test_unknown_role_is_refused_rather_than_downgraded() -> None:
    """Роль вне списка — отказ, а не «ну пусть будет member».

    Тихое понижение спрятало бы опечатку в сидере, тихое повышение — дыру.
    Отдельно сюда попадает роль с кириллической «а» в слове admin: выглядит
    правильно, читается неправильно, и молча стать участником не должна.
    """
    for role in ("", "superuser", "аdmin"):
        token = jwt.encode(
            {"sub": str(uuid.uuid4()), "role": role, "exp": int(time.time()) + 3600},
            SECRET,
            algorithm="HS256",
        )
        with pytest.raises(AuthError, match="роль"):
            decode_token(token)


def test_owner_mode_accepts_no_tokens(monkeypatch) -> None:
    """Без секрета проверять подпись нечем — значит не принимаем ничего."""
    good = issue_token(_identity())
    monkeypatch.setattr(settings, "studio_session_secret", "")
    with pytest.raises(AuthError, match="STUDIO_SESSION_SECRET"):
        decode_token(good)


def test_owner_mode_issues_no_tokens(monkeypatch) -> None:
    monkeypatch.setattr(settings, "studio_session_secret", "")
    with pytest.raises(AuthError, match="STUDIO_SESSION_SECRET"):
        issue_token(_identity())


def test_empty_token_is_refused() -> None:
    with pytest.raises(AuthError, match="пуст"):
        decode_token("")


def test_token_without_sub_is_refused() -> None:
    """`sub` — это арендатор. Без него данным некому принадлежать.

    PyJWT требует `sub` через `options={"require": [...]}`, но пустая строка
    требование проходит: ключ есть, значение ложное. Проверка нужна отдельная.
    """
    token = jwt.encode(
        {"sub": "", "role": ROLE_MEMBER, "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AuthError):
        decode_token(token)


def test_current_identity_is_empty_outside_a_request() -> None:
    """Фоновая задача воркера личности не имеет — и не должна её унаследовать.

    ContextVar копируется в задачу при создании; если бы личность где-то
    залипала, воркер разбирал бы чужое задание от чужого имени.
    """
    from app.services.studio_auth import current_identity, set_identity

    set_identity(None)
    assert current_identity() is None

    who = _identity()
    set_identity(who)
    try:
        assert current_identity() == who
    finally:
        set_identity(None)
    assert current_identity() is None
