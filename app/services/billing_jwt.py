"""Личность приходит из биллинга подписанным токеном. Здесь его проверка.

Единственный источник личности в SaaS — `llm-gateway/billing`
(`docs/SAAS-PIVOT.md` §3.3, §4.1). Он уже умеет регистрацию, подтверждение
почты, восстановление пароля и мультибрендовость; переписывать это заново —
три месяца и юридический риск на приёме платежей. Студия свою форму входа не
заводит: она принимает то, что подписал биллинг.

**Что именно приходит.** `billing/src/auth/auth.service.ts::sign` кладёт в
токен ровно три поля:

    { sub: user.id, email: user.email, brand: user.brand }

плюс `iat`/`exp` от `@nestjs/jwt` (срок — `JWT_EXPIRES_IN`, по умолчанию 7d).
Подпись HS256 общим секретом `JWT_SECRET`. `User.id` — нативный UUID
(`@PrimaryGeneratedColumn("uuid")`), поэтому он же становится `tenant_id`
студии без всякой таблицы соответствия: одна личность, один арендатор.

**Три вещи, которые обязана делать проверка, и почему каждая.**

1. *Алгоритм задаётся списком, а не берётся из токена.* Классическая дыра
   JWT — `alg: none` и подмена HS256/RS256: злоумышленник присылает заголовок,
   и библиотека послушно проверяет подпись тем способом, который он назвал.
   Список разрешённых алгоритмов здесь один и захардкожен.

2. *`sub` обязан быть UUID.* Значение уезжает в `SET LOCAL app.tenant_id`,
   то есть в SQL и в границу безопасности. Канонизация через
   `tenant._normalize` превращает «нужно не забыть экранировать» в «нечего
   экранировать».

3. *Бренд сверяется.* Биллинг мультибрендовый: уникальность пользователя —
   по паре `(email, brand)`, и токен, выписанный для аккаунта другого бренда,
   имеет совершенно валидную подпись. Пустить его в студию значило бы отдать
   продукт соседней воронке. Сверка включается настройкой `STUDIO_BRAND`.

Всё, что не прошло, — это `BillingAuthError`, который веб-слой отдаёт как
401. Причина в тексте нужна оператору в логе; клиенту хватает кода.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Единственный допустимый алгоритм. Список, а не строка, потому что в него
#: смотрит PyJWT, и он должен быть закрытым.
ALLOWED_ALGORITHMS: tuple[str, ...] = ("HS256",)


class BillingAuthError(RuntimeError):
    """Токен не принят: нет, просрочен, подписан не тем, не того бренда."""


@dataclass(frozen=True)
class BillingIdentity:
    """Кто пришёл. `tenant_id` — он же `User.id` биллинга."""

    tenant_id: str
    email: str
    brand: str

    @property
    def is_anonymous(self) -> bool:
        return not self.tenant_id


def decode_token(token: str) -> BillingIdentity:
    """Проверить подпись и срок, вернуть личность. Иначе `BillingAuthError`.

    Функция намеренно не знает ни про HTTP, ни про FastAPI: её зовут и из
    веб-слоя, и из тестов, и однажды позовёт воркер, разбирающий задание
    чужого арендатора.
    """
    from app.settings import settings

    secret = settings.billing_jwt_secret.strip()
    if not secret:
        raise BillingAuthError(
            "BILLING_JWT_SECRET не задан — проверять подпись нечем. "
            "Это режим владельца, токены в нём не принимаются."
        )
    raw = (token or "").strip()
    if not raw:
        raise BillingAuthError("токен пуст")

    import jwt  # PyJWT

    try:
        payload = jwt.decode(
            raw,
            secret,
            # Список алгоритмов задаём мы, а не заголовок токена: иначе
            # `alg: none` и подмена HS256/RS256 проходят как штатный разбор.
            algorithms=list(ALLOWED_ALGORITHMS),
            leeway=max(0, int(settings.billing_jwt_leeway_sec)),
            options={"require": ["exp", "sub"], "verify_exp": True, "verify_signature": True},
        )
    except jwt.ExpiredSignatureError as exc:
        raise BillingAuthError("срок токена истёк") from exc
    except jwt.InvalidTokenError as exc:
        # Наружу уходит один текст на все причины: «подпись не сошлась»,
        # «нет exp», «мусор вместо base64». Различать их клиенту незачем,
        # а нам подробность видна в цепочке исключений.
        raise BillingAuthError(f"токен не принят: {exc}") from exc

    return _identity_from_payload(payload)


def _identity_from_payload(payload: dict) -> BillingIdentity:
    from app.services.tenant import _normalize
    from app.settings import settings

    raw_sub = payload.get("sub")
    try:
        tenant_id = _normalize(str(raw_sub) if raw_sub is not None else None)
    except ValueError as exc:
        # `User.id` в биллинге — `@PrimaryGeneratedColumn("uuid")`. Если sub
        # не UUID, это либо чужой эмитент, либо биллинг сменил тип ключа —
        # и то и другое означает, что арендатора выводить не из чего.
        raise BillingAuthError(f"sub не UUID: {raw_sub!r}") from exc
    if tenant_id is None:
        raise BillingAuthError("в токене нет sub — некому принадлежать данным")

    brand = str(payload.get("brand") or "").strip()
    expected = settings.studio_brand.strip()
    if expected and brand != expected:
        raise BillingAuthError(
            f"токен бренда {brand!r}, студия обслуживает {expected!r}: в биллинге это разные пользователи"
        )

    return BillingIdentity(
        tenant_id=tenant_id,
        email=str(payload.get("email") or "").strip(),
        brand=brand,
    )
