"""Текущий арендатор: кто именно смотрит на данные в этой задаче.

Изоляция арендаторов делается не фильтром в коде, а row-level security в
Postgres (`docs/SAAS-PIVOT.md` §4.2). Политика на таблице читает настройку
сессии ``app.tenant_id``, а выставляет её транзакция — значит между «кто
пришёл по HTTP» и «что видит SELECT» нужен переносчик, живущий сквозь
`await`. Это ContextVar: он копируется в задачу при её создании и не течёт
между параллельными запросами, в отличие от глобальной переменной.

**Почему не параметр функции.** В кодовой базе 324 файла и 67 из них ходят
в данные проекта. Протащить `tenant_id` через все — это ровно то требование
к разработчику, чей провал разбирается в `docs/SHOTS-FIX-2026-08-23.md`:
забыть можно в одном месте, а утечёт чужой ролик.

**Режим одного арендатора.** Пока система работает у владельца на SQLite,
арендатора нет вовсе, и это не ошибка: ``current_tenant()`` вернёт ``None``,
`SET LOCAL` не выполнится, RLS не существует. Опасен другой случай —
многоарендная работа на SQLite, где политик нет физически. От него защищает
``require_isolation``.

Единственная лазейка — ``ALLOW_UNISOLATED_TENANTS``, и она существует не для
продакшена. Тестам механики кассы арендатор нужен как ключ учёта, а не как
граница безопасности: проверять сходимость баланса можно и без RLS, а
поднимать ради этого Postgres — значит не иметь возможности прогнать суиту на
машине без него. По умолчанию флаг выключен, и в SaaS его включение означает
дыру, а не удобство.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("tenant_id", default=None)


class TenantIsolationError(RuntimeError):
    """Многоарендный режим запрошен там, где изоляции не существует."""


def current_tenant() -> str | None:
    """Арендатор этой задачи. ``None`` — режим одного владельца."""
    return _current.get()


def set_tenant(tenant_id: str | None) -> None:
    """Выставить арендатора для текущей задачи (вход HTTP-запроса)."""
    _current.set(_normalize(tenant_id))


@contextmanager
def tenant_scope(tenant_id: str | None) -> Iterator[str | None]:
    """Временно подменить арендатора и вернуть прежнего на выходе.

    Нужен фоновым задачам: воркер берёт заказ чужого арендатора, выполняет
    шаг и обязан вернуть контекст, чтобы следующий заказ не унаследовал
    чужую личность.
    """
    token = _current.set(_normalize(tenant_id))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def require_isolation() -> None:
    """Убедиться, что многоарендный режим стоит на изолирующей базе.

    Вызывается там, где арендатор уже назначен. На SQLite политик RLS нет —
    и это не «пока не сделали», а «в движке отсутствует». Падать здесь
    громко правильнее, чем отдать чужой ролик: неудобство против утечки.
    """
    if current_tenant() is None:
        return
    from app.settings import settings

    if not settings.is_postgres and not settings.allow_unisolated_tenants:
        raise TenantIsolationError(
            "многоарендный режим на SQLite: row-level security в этом движке "
            "не существует, изоляция не обеспечена. Задайте DATABASE_URL "
            "на Postgres (docs/SAAS-PIVOT.md §11, этап 1)."
        )


def _normalize(raw: str | None) -> str | None:
    """Привести к каноническому UUID или отвергнуть.

    Значение уезжает в `SET LOCAL app.tenant_id` — то есть в SQL. Проверка
    формата здесь превращает «нужно не забыть экранировать» в «нечего
    экранировать»: всё, что прошло, — это 36 символов из шестнадцатеричного
    алфавита и дефисов.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"tenant_id не UUID: {value!r}") from exc
