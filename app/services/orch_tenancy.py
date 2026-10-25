"""Арендатор в движке оркестратора: сессии чата и фоновые проходы.

Строки движка закрыты RLS (ревизия 0020), но у движка есть и память
процесса: супервизор держит «сессия → задача», шина — подписчиков и
нумерацию событий по имени сессии. Имя сессии выбирает клиент, и два
клиента, назвавшие чат одинаково («main»), без разделения попали бы в одну
очередь, одну нумерацию событий и одну карточку одобрения. Поэтому имя,
которое уходит внутрь движка, несёт арендатора: ``<сессия>.<tenant>``.
Точки нет в алфавите внешнего имени (``[A-Za-z0-9_-]``), так что подделать
чужое внутреннее имя снаружи нельзя. В режиме владельца (арендатора нет)
имя не меняется — поведение прежнее.

Фоновые проходы (подъём после рестарта, истечение одобрений, закрытие
прерванных планов, сжатие событий) идут так же, как воркер конвейера:
сперва без арендатора — строки владельца, потом от имени каждого
арендатора. Без этого под RLS проход видел бы только строки владельца и
молча не трогал бы клиентские.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from loguru import logger

from app.services.tenant import current_tenant, tenant_scope

T = TypeVar("T")

#: Разделитель внутреннего имени. Не входит в алфавит внешнего имени сессии.
SEPARATOR = "."


def scoped_session(session: str, tenant_id: str | None = None) -> str:
    """Внутреннее имя сессии для движка: внешнее + арендатор."""
    tenant = tenant_id if tenant_id is not None else current_tenant()
    if tenant is None:
        return session
    return f"{session}{SEPARATOR}{tenant}"


def public_session(internal: str) -> str:
    """Внешнее имя сессии — то, что знает клиент."""
    head, sep, _tail = str(internal).rpartition(SEPARATOR)
    return head if sep else str(internal)


def session_tenant(internal: str) -> str | None:
    """Арендатор из внутреннего имени. ``None`` — сессия владельца."""
    _head, sep, tail = str(internal).rpartition(SEPARATOR)
    return tail if sep else None


def owns_session(internal: str) -> bool:
    """Сессия принадлежит текущему арендатору (владелец — своим)."""
    return session_tenant(internal) == current_tenant()


async def tenants_to_serve(*, active_only: bool = False) -> list[str | None]:
    """Кому служит фоновый проход движка: владелец и каждый арендатор.

    SQLite — всегда ``[None]``: многоарендной работы там нет. На Postgres
    список арендаторов — это `studio_users` (id учётки и есть её
    `tenant_id`): таблица вне RLS и читается без арендатора.
    """
    from app.settings import settings

    if not settings.is_postgres:
        return [None]
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import StudioUser

    query = select(StudioUser.id).order_by(StudioUser.id)
    if active_only:
        query = query.where(StudioUser.is_active.is_(True))
    with tenant_scope(None):
        async with SessionLocal() as session:
            ids = (await session.scalars(query)).all()
    return [None, *[str(i) for i in ids]]


async def _identity_for(tenant_id: str):
    """Личность владельца данных арендатора — для внутренних вызовов движка.

    Арендатор и есть учётка (`StudioIdentity.tenant_id == user_id`), поэтому
    фоновой задаче можно назначить ровно эту личность с её же ролью: права
    не повышаются. Отключённая учётка — ``None``: её задачи без личности
    упрутся в отказ внутреннего вызова, а не пройдут от чужого имени.
    """
    from app.db import SessionLocal
    from app.models import StudioUser
    from app.services.studio_auth import identity_of

    with tenant_scope(None):
        async with SessionLocal() as session:
            user = await session.get(StudioUser, tenant_id)
    if user is None or not user.is_active:
        return None
    return identity_of(user)


async def for_each_tenant(job: Callable[[], Awaitable[list[T]]], *, what: str) -> list[T]:
    """Выполнить проход от имени владельца и каждого арендатора по очереди.

    Задачи, порождённые внутри прохода (``asyncio.create_task``), копируют
    контекст и уносят арендатора и личность с собой. Ошибка одного
    арендатора не останавливает остальных.
    """
    from app.services.studio_auth import current_identity, set_identity

    out: list[T] = []
    for tenant in await tenants_to_serve():
        previous = current_identity()
        try:
            with tenant_scope(tenant):
                set_identity(await _identity_for(tenant) if tenant is not None else None)
                out.extend(await job())
        except Exception:  # noqa: BLE001
            logger.exception("orch {}: проход арендатора {} не выполнен", what, tenant or "владелец")
        finally:
            set_identity(previous)
    return out


async def for_each_tenant_with_work(
    has_work: Callable[[], Awaitable[bool]],
    job: Callable[[], Awaitable[list[T]]],
    *,
    what: str,
) -> list[T]:
    """Как ``for_each_tenant``, но только живые учётки и только те, у кого есть работа.

    ``has_work`` — дешёвая проверка под RLS арендатора (например, «есть активное
    выполнение»): арендатор без работы пропускается до поиска личности и полного прохода.
    Частые проходы (наблюдатель) так не платят за каждую учётку студии на каждом такте.
    """
    from app.services.studio_auth import current_identity, set_identity

    out: list[T] = []
    for tenant in await tenants_to_serve(active_only=True):
        previous = current_identity()
        try:
            with tenant_scope(tenant):
                if not await has_work():
                    continue
                set_identity(await _identity_for(tenant) if tenant is not None else None)
                out.extend(await job())
        except Exception:  # noqa: BLE001
            logger.exception("orch {}: проход арендатора {} не выполнен", what, tenant or "владелец")
        finally:
            set_identity(previous)
    return out


def _norm_tenant(value: object) -> str | None:
    """UUID арендатора в одном виде: Postgres отдаёт с дефисами, SQLite хранит 32 hex."""
    text_value = str(value or "").strip().lower().replace("-", "")
    return text_value or None


def own_projects(tenant_id: str | None, ids: list[int] | None = None) -> list[tuple[int, str]]:
    """Проекты арендатора (id и название), синхронно и только чтение.

    Для фактов и подсказок движка, которые иначе прочли бы общую базу или общий
    файл очереди. Чтение под арендатором (на Postgres — RLS) и ещё сверка
    арендатора строки: на SQLite политик нет. Владелец (``None``) — только строки
    владельца. ``ids`` — оставить только эти номера (порядок как в ``ids``).
    """
    from sqlalchemy import text as sql_text

    from app.orchestrator.db.engine import get_engine

    if ids is not None and not ids:
        return []
    want = _norm_tenant(tenant_id)
    query = sql_text("SELECT id, COALESCE(title, topic, '') AS title, tenant_id FROM projects ORDER BY id")
    with tenant_scope(tenant_id), get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()
    found = {int(pid): str(title or "") for pid, title, owner in rows if _norm_tenant(owner) == want}  # type: ignore[call-overload]
    if ids is None:
        return list(found.items())
    return [(int(pid), found[int(pid)]) for pid in ids if int(pid) in found]
