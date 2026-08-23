"""Приём токена биллинга: что пускаем, что нет и почему именно так.

Проверка подписи — то место, где ошибка не выглядит ошибкой. Токен с
`alg: none` разбирается без исключений, токен чужого бренда имеет совершенно
валидную подпись, просроченный отличается от свежего одним числом. Поэтому
здесь на каждый способ пройти мимо — отдельный тест.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from app.services.billing_jwt import BillingAuthError, decode_token
from app.settings import settings

SECRET = "секрет-биллинга-для-теста"


def _token(**overrides) -> str:
    """Токен в том же виде, в каком его выписывает `auth.service.ts::sign`."""
    payload = {
        "sub": str(uuid.uuid4()),
        "email": "client@example.com",
        "brand": "videostudio",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    secret = overrides.pop("_secret", SECRET)
    alg = overrides.pop("_alg", "HS256")
    for key in ("_secret", "_alg"):
        payload.pop(key, None)
    return jwt.encode(payload, secret, algorithm=alg)


@pytest.fixture(autouse=True)
def _sso_on(monkeypatch):
    monkeypatch.setattr(settings, "billing_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "studio_brand", "")


def test_valid_token_gives_tenant_email_and_brand() -> None:
    sub = str(uuid.uuid4())
    identity = decode_token(_token(sub=sub))
    assert identity.tenant_id == sub
    assert identity.email == "client@example.com"
    assert identity.brand == "videostudio"


def test_tenant_id_is_the_billing_user_id() -> None:
    """Таблицы соответствия нет: `User.id` биллинга и есть арендатор.

    Оба — UUID, и это не совпадение: `@PrimaryGeneratedColumn("uuid")` в
    биллинге против `sa.Uuid` в схеме студии.
    """
    sub = "A1B2C3D4-0000-4000-8000-000000000000"
    assert decode_token(_token(sub=sub)).tenant_id == sub.lower()


def test_expired_token_is_refused() -> None:
    stale = _token(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200)
    with pytest.raises(BillingAuthError, match="срок"):
        decode_token(stale)


def test_token_signed_with_another_secret_is_refused() -> None:
    with pytest.raises(BillingAuthError):
        decode_token(_token(_secret="не тот секрет"))


def test_unsigned_token_is_refused() -> None:
    """`alg: none` — классическая дыра: подпись объявлена ненужной.

    Библиотека проверила бы токен ровно тем способом, который назвал сам
    токен, поэтому список алгоритмов задаём мы.
    """
    naked = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": int(time.time()) + 60},
        key="",
        algorithm="none",
    )
    with pytest.raises(BillingAuthError):
        decode_token(naked)


def test_other_algorithm_is_refused_even_with_right_secret() -> None:
    """HS512 тем же секретом — валидная подпись, но не наш алгоритм."""
    with pytest.raises(BillingAuthError):
        decode_token(_token(_alg="HS512"))


def test_token_without_exp_is_refused() -> None:
    """Токен без срока живёт вечно — украденный тоже."""
    forever = jwt.encode({"sub": str(uuid.uuid4())}, SECRET, algorithm="HS256")
    with pytest.raises(BillingAuthError):
        decode_token(forever)


def test_sub_that_is_not_uuid_is_refused() -> None:
    """`sub` уезжает в `SET LOCAL app.tenant_id`, то есть в SQL и в границу."""
    with pytest.raises(BillingAuthError, match="UUID"):
        decode_token(_token(sub="admin' or 1=1--"))


def test_foreign_brand_is_refused_when_brand_is_declared(monkeypatch) -> None:
    """Подпись верна, бренд чужой — в биллинге это другой пользователь.

    Уникальность там по паре `(email, brand)`, поэтому валидная подпись сама
    по себе не отвечает на вопрос «этому ли продукту принадлежит аккаунт».
    """
    monkeypatch.setattr(settings, "studio_brand", "videostudio")
    decode_token(_token(brand="videostudio"))  # свой проходит
    with pytest.raises(BillingAuthError, match="бренд"):
        decode_token(_token(brand="chattiq"))


def test_owner_mode_accepts_no_tokens(monkeypatch) -> None:
    """Без секрета проверять подпись нечем — принимать нечего."""
    monkeypatch.setattr(settings, "billing_jwt_secret", "")
    with pytest.raises(BillingAuthError, match="BILLING_JWT_SECRET"):
        decode_token(_token())
