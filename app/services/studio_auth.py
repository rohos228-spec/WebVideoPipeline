"""Личность студии: выдача и проверка сессионного токена.

Раньше здесь стоял `billing_jwt.py` — приём чужого токена, подписанного
`llm-gateway` (Chattiq). Решение владельца от 2026-08-24 сняло эту связь
целиком: платежей студия не принимает, продукт внутренний, и зависеть от
чужого сервиса ради строки «кто пришёл» больше незачем. Личность заводится
здесь, в `studio_users`, и токен подписывает студия сама.

**Почему подписанный токен, а не таблица сессий.** Проверка личности стоит на
входе ASGI, до роутера, — иначе новый роутер оказывается открытым по факту
невнимательности автора (это разобрано в `app/web/identity.py`). Опаковый
токен потребовал бы похода в базу за каждым запросом ещё до того, как
приложение решило, нужна ли ему база вообще, — включая статику и стримы.
Подпись проверяется без базы.

**Чем тогда закрывается отзыв.** У подписанного токена нет кнопки «погасить»,
и это его известная слабость: уволили человека — он работает до истечения
срока. Дыра закрыта двумя вещами, и обе дешёвые:

1. `token_epoch` у пользователя. Он едет в токен и сверяется с базой. Смена
   пароля или отключение учётки поднимают счётчик — все выданные токены
   становятся недействительны в тот же миг.
2. Проверка `is_active` там же. Обе проверки — один поиск по первичному ключу,
   и делает его middleware перед тем, как назначить арендатора.

То есть база всё-таки спрашивается — но за фактом «этот пользователь ещё
существует и не отозван», а не за расшифровкой личности. Разница в том, что
испорченный или чужой токен отсекается подписью и до базы не доходит вовсе.

**Алгоритм задаётся списком, а не берётся из токена.** Классическая дыра JWT —
`alg: none` и подмена HS256/RS256: злоумышленник присылает заголовок, и
библиотека послушно проверяет подпись тем способом, который он назвал. Список
разрешённых алгоритмов здесь один и захардкожен.

**`sub` обязан быть UUID.** Значение уезжает в `SET LOCAL app.tenant_id`, то
есть в SQL и в границу безопасности. Канонизация через `tenant._normalize`
превращает «нужно не забыть экранировать» в «нечего экранировать».
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Единственный допустимый алгоритм. Список, а не строка, потому что в него
#: смотрит PyJWT, и он должен быть закрытым.
ALLOWED_ALGORITHMS: tuple[str, ...] = ("HS256",)

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

#: Все роли, которые система понимает. Значение вне списка — не «неизвестная
#: роль с правами по умолчанию», а отказ: тихое понижение до `member` скрыло бы
#: опечатку в сидере, а тихое повышение — дыру.
ROLES: frozenset[str] = frozenset({ROLE_ADMIN, ROLE_MEMBER})


class AuthError(RuntimeError):
    """Токен не принят: нет, просрочен, подписан не тем, отозван."""


@dataclass(frozen=True)
class StudioIdentity:
    """Кто пришёл. `user_id` — он же `tenant_id` его данных."""

    user_id: str
    email: str
    role: str
    #: Поколение токена на момент выдачи. Сверяется с `StudioUser.token_epoch`.
    epoch: int = 0

    @property
    def tenant_id(self) -> str:
        """Синоним `user_id` для слоёв, которые говорят про изоляцию."""
        return self.user_id

    @property
    def is_admin(self) -> bool:
        """Админ. У него нет баланса — у него нет кассы вовсе."""
        return self.role == ROLE_ADMIN


#: Личность текущего запроса. Отдельно от `tenant._current`: тот отвечает за
#: SQL и знает ровно один UUID, а роль в SQL не едет и ехать не должна.
_identity: ContextVar[StudioIdentity | None] = ContextVar("studio_identity", default=None)


def current_identity() -> StudioIdentity | None:
    """Личность этой задачи. `None` — режим владельца или фоновая задача."""
    return _identity.get()


def set_identity(identity: StudioIdentity | None) -> None:
    """Назначить личность на время запроса (вход HTTP)."""
    _identity.set(identity)


def current_is_admin() -> bool:
    """Работает ли запрос от имени админа.

    Отдельная функция, а не `current_identity().is_admin`, потому что зовут её
    из кассы, где личности может не быть вовсе, и `None.is_admin` там был бы
    падением на ровном месте.
    """
    identity = _identity.get()
    return identity is not None and identity.is_admin


def new_user_id() -> str:
    """UUID нового пользователя. Он же арендатор его данных."""
    return str(uuid.uuid4())


def issue_token(identity: StudioIdentity, *, ttl_hours: int | None = None) -> str:
    """Подписать сессионный токен для этой личности."""
    import jwt  # PyJWT

    from app.settings import settings

    secret = settings.studio_session_secret.strip()
    if not secret:
        raise AuthError("STUDIO_SESSION_SECRET не задан — подписывать нечем")

    hours = int(settings.session_ttl_hours if ttl_hours is None else ttl_hours)
    now = datetime.now(tz=UTC)
    payload = {
        "sub": identity.user_id,
        "email": identity.email,
        "role": identity.role,
        "epoch": int(identity.epoch),
        "iat": now,
        "exp": now + timedelta(hours=max(1, hours)),
    }
    return jwt.encode(payload, secret, algorithm=ALLOWED_ALGORITHMS[0])


def decode_token(token: str) -> StudioIdentity:
    """Проверить подпись и срок, вернуть личность. Иначе `AuthError`.

    Функция намеренно не знает ни про HTTP, ни про FastAPI: её зовут и из
    веб-слоя, и из тестов, и однажды позовёт воркер, разбирающий задание
    чужого арендатора. Проверка `is_active` / `token_epoch` здесь НЕ делается:
    для неё нужна база, а этот слой обязан оставаться синхронным и дешёвым.
    Её место — `assert_not_revoked`.
    """
    from app.settings import settings

    secret = settings.studio_session_secret.strip()
    if not secret:
        raise AuthError(
            "STUDIO_SESSION_SECRET не задан — проверять подпись нечем. "
            "Это режим владельца, токены в нём не принимаются."
        )
    raw = (token or "").strip()
    if not raw:
        raise AuthError("токен пуст")

    import jwt  # PyJWT

    try:
        payload = jwt.decode(
            raw,
            secret,
            # Список алгоритмов задаём мы, а не заголовок токена: иначе
            # `alg: none` и подмена HS256/RS256 проходят как штатный разбор.
            algorithms=list(ALLOWED_ALGORITHMS),
            leeway=max(0, int(settings.session_leeway_sec)),
            options={"require": ["exp", "sub"], "verify_exp": True, "verify_signature": True},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("срок токена истёк") from exc
    except jwt.InvalidTokenError as exc:
        # Наружу уходит один текст на все причины: «подпись не сошлась»,
        # «нет exp», «мусор вместо base64». Различать их клиенту незачем,
        # а нам подробность видна в цепочке исключений.
        raise AuthError(f"токен не принят: {exc}") from exc

    return _identity_from_payload(payload)


def _identity_from_payload(payload: dict) -> StudioIdentity:
    from app.services.tenant import _normalize

    raw_sub = payload.get("sub")
    try:
        user_id = _normalize(str(raw_sub) if raw_sub is not None else None)
    except ValueError as exc:
        raise AuthError(f"sub не UUID: {raw_sub!r}") from exc
    if user_id is None:
        raise AuthError("в токене нет sub — некому принадлежать данным")

    role = str(payload.get("role") or "").strip()
    if role not in ROLES:
        # Неизвестная роль — не повод угадывать. Токен с ролью `аdmin`
        # (кириллическая «а») не должен ни работать, ни молча стать `member`.
        raise AuthError(f"роль {role!r} неизвестна")

    return StudioIdentity(
        user_id=user_id,
        email=str(payload.get("email") or "").strip().lower(),
        role=role,
        epoch=int(payload.get("epoch") or 0),
    )


async def assert_not_revoked(session: Any, identity: StudioIdentity) -> None:
    """Пользователь ещё существует, не отключён и токен не обесценен.

    Вторая половина проверки, для которой нужна база. Разделение не
    косметическое: подпись отсекает подделки бесплатно, и до базы доходит
    только то, что студия действительно выписывала.

    Роль берётся ИЗ БАЗЫ, а не из токена: понижение админа до участника обязано
    действовать сразу, а не после истечения срока. Токен несёт роль лишь затем,
    чтобы слой без базы (например, отказ на пути владельца) мог ответить не
    заглядывая в неё, — но решение о правах принимается по строке.
    """
    from app.models import StudioUser

    user = await session.get(StudioUser, identity.user_id)
    if user is None:
        raise AuthError("учётной записи больше нет")
    if not user.is_active:
        raise AuthError("учётная запись отключена")
    if int(user.token_epoch) != int(identity.epoch):
        raise AuthError("токен обесценен: пароль сменён или доступ отозван")
    if user.role != identity.role:
        raise AuthError("роль изменилась: нужен новый вход")


def identity_of(user: Any) -> StudioIdentity:
    """Личность из строки `StudioUser`."""
    return StudioIdentity(
        user_id=str(user.id),
        email=str(user.email or ""),
        role=str(user.role or ROLE_MEMBER),
        epoch=int(user.token_epoch or 0),
    )
