"""«База занята, повтори» — один ответ на два диалекта.

В коде десяток мест ловит `database is locked` разбором текста исключения:
монтажная полоса, выравнивание звука, доска раскадровки, учёт вызовов. На
SQLite это единственный признак — драйвер отдаёт `OperationalError` со
строкой. На Postgres такой строки не будет никогда, а конкуренция никуда не
денется: она приходит кодами `40001` (сериализация), `40P01` (взаимная
блокировка), `55P03` (блокировка занята).

Опасность не в том, что проверка перестанет срабатывать, — а в том, что она
перестанет срабатывать **тихо**. Обработчик «подожди и повтори» превратится
в «упади с непонятной ошибкой», причём только под нагрузкой и только в
продакшене. Поэтому распознавание собрано в одном месте и знает оба движка.

Использование::

    try:
        await session.commit()
    except Exception as exc:
        if is_db_busy(exc):
            ...  # подождать и повторить
        raise
"""

from __future__ import annotations

from typing import Any

#: SQLSTATE-коды Postgres, означающие «повтори транзакцию».
#: 40001 serialization_failure — сериализация не удалась;
#: 40P01 deadlock_detected — взаимная блокировка, одну из сторон сняли;
#: 55P03 lock_not_available — `NOWAIT`/таймаут блокировки.
RETRYABLE_SQLSTATES: frozenset[str] = frozenset({"40001", "40P01", "55P03"})

#: Признаки занятой базы у SQLite — только по тексту, кодов драйвер не даёт.
_SQLITE_MARKERS: tuple[str, ...] = (
    "database is locked",
    "database locked",
    "database is busy",
    "database table is locked",
)


def is_db_busy(exc: BaseException | str | None) -> bool:
    """Стоит ли повторить операцию: база занята конкурентом.

    Принимает исключение или уже готовое сообщение — второе нужно тем
    местам, где текст ошибки пришёл из чужого слоя строкой.
    """
    if exc is None:
        return False
    if isinstance(exc, str):
        return _text_matches(exc)

    if _sqlstate(exc) in RETRYABLE_SQLSTATES:
        return True
    if _text_matches(str(exc)):
        return True
    cause = getattr(exc, "__cause__", None) or getattr(exc, "orig", None)
    if cause is not None and cause is not exc:
        return is_db_busy(cause)
    return False


def _sqlstate(exc: Any) -> str:
    """Код SQLSTATE, как его отдают asyncpg и psycopg. Пусто — кода нет."""
    for attr in ("sqlstate", "pgcode"):
        value = getattr(exc, attr, None)
        if value:
            return str(value).upper()
    orig = getattr(exc, "orig", None)
    if orig is not None and orig is not exc:
        return _sqlstate(orig)
    return ""


def _text_matches(message: str) -> bool:
    low = (message or "").lower()
    return any(marker in low for marker in _SQLITE_MARKERS)


def busy_hint() -> str:
    """Что сказать человеку. Причина у диалектов разная, действие одно."""
    from app.settings import settings

    if settings.is_postgres:
        return "база занята параллельной транзакцией — повтори через пару секунд"
    return "database is locked — повтори через пару секунд (Excel/доска держали SQLite)"
