"""Движок БД и фабрика сессий. Диалект выбирается по `DATABASE_URL`.

Два режима, и различаются они не косметикой:

* **SQLite** — режим владельца, одна машина, один файл. Пула нет: единственный
  writer, `NullPool` и WAL. `QueuePool` здесь давал `database is locked` при
  одновременных PATCH канваса и записи воркера.
* **Postgres** — режим SaaS. Есть пул, есть row-level security, и каждая
  транзакция обязана сообщить, от чьего имени она идёт: политика на таблице
  читает `app.tenant_id`, а выставляет его `SET LOCAL` в начале транзакции
  (`docs/SAAS-PIVOT.md` §4.1).

**`SET LOCAL`, а не `SET`.** Настройка живёт до конца транзакции и умирает
вместе с ней. Соединение возвращается в пул чистым — иначе следующий
арендатор, взявший то же соединение, унаследовал бы чужую личность, и RLS
пропустил бы его к чужим данным. Это ровно тот класс ошибки, ради защиты от
которого RLS и выбран.

**Арендатор проставляется на записи, а не вызывающим.** Политика RLS
фильтрует чтение сама, а вот `WITH CHECK` при вставке требует, чтобы строка
УЖЕ несла верный `tenant_id`, — и заполнить его должно приложение. Первая
редакция этого не делала: чтение было изолировано, а любая вставка в SaaS
падала с «new row violates row-level security policy». Нашлось только живым
прогоном: во всех тестах `tenant_id` проставляли руками, и требование
«не забудь проставить» выглядело выполненным.

Это тот же класс требования к разработчику, ради ухода от которого выбран
RLS (`docs/SAAS-PIVOT.md` §4.2): двадцать шесть мест создают артефакты, ещё
сколько-то — кадры и проекты, и забыть можно в любом. Поэтому проставление
висит на `before_flush` сессии: новая строка получает арендатора из контекста
задачи, и вызывающему помнить не о чем. Заданный явно НЕ перетирается —
запись в чужого арендатора должна упереться в `WITH CHECK`, а не быть тихо
исправлена.

**Привязка висит на начале транзакции, а не на входе в `session_scope`.**
Первая редакция ставила `SET LOCAL` в одном месте — в `session_scope`. Мимо
неё ходят шестнадцать роутеров через `deps.get_session` и двадцать пять
прямых `SessionLocal()` в шагах конвейера: у всех у них настройка осталась бы
пустой, а политика при пустой настройке показывает строки без арендатора и
только их. Отказ был бы не утечкой, а тишиной — шаг отработал, ничего не
нашёл, ничего не сказал. Поэтому привязка перенесена на событие `after_begin`
сессии: транзакция физически не может начаться, не сообщив, чья она.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.settings import settings


def _make_engine():
    if settings.is_postgres:
        # pool_pre_ping: соединение могло умереть, пока шаг ждал провайдера
        # минутами. Дешевле проверить, чем уронить шаг после генерации.
        return create_async_engine(
            settings.db_url,
            echo=False,
            future=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )

    # Гарантируем, что папка под БД существует — SQLite сам файл создаст, а
    # вот родительскую директорию нужно создать заранее.
    _db_path = settings.sqlite_path
    if not _db_path.is_absolute():
        from pathlib import Path

        _db_path = Path.cwd() / _db_path
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_async_engine(
        settings.db_url,
        echo=False,
        future=True,
        poolclass=NullPool,
        # Montage / canvas save: запас против busy writer (worker Outsee).
        connect_args={"timeout": 60},
    )


engine = _make_engine()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


if not settings.is_postgres:
    event.listens_for(engine.sync_engine, "connect")(_configure_sqlite_connection)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


#: `set_config(..., is_local => true)` — то же, что `SET LOCAL`, но с местом
#: под параметр: `SET LOCAL` плейсхолдера не принимает, а подставлять значение
#: в текст запроса не хочется даже проверенное.
_BIND_TENANT_SQL = text("select set_config('app.tenant_id', :tid, true)")


@event.listens_for(Session, "after_begin")
def _bind_tenant_on_begin(session: Session, transaction, connection) -> None:
    """Каждая транзакция сообщает, от чьего имени идёт. Без исключений.

    Слушатель повешен на класс `Session`, то есть срабатывает и для
    `AsyncSession` (внутри неё живёт обычная сессия), и для сессий, которые
    кто-то откроет напрямую. Забыть привязку нельзя: её никто не вызывает
    руками.

    На SQLite выполняется только проверка режима — политик там нет, а
    многоарендная работа на нём запрещена (`tenant.require_isolation`).
    """
    from app.services.tenant import current_tenant, require_isolation

    require_isolation()
    tenant_id = current_tenant()
    if tenant_id is None or not settings.is_postgres:
        return
    connection.execute(_BIND_TENANT_SQL, {"tid": tenant_id})


@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_new_rows(session: Session, flush_context, instances) -> None:
    """Проставить арендатора новым строкам. Без этого RLS их не пустит.

    Политика читает `app.tenant_id` и требует совпадения в `WITH CHECK`.
    Строка без арендатора нарушает политику, и вставка падает — в SaaS это
    означало бы, что не создаётся ничего вообще.

    Уже заданный `tenant_id` не трогаем: попытка записать в чужого арендатора
    обязана упереться в политику, а не быть молча исправлена на свою.
    """
    from app.services.tenant import current_tenant

    tenant_id = current_tenant()
    if tenant_id is None:
        return
    for obj in session.new:
        if getattr(obj, "tenant_id", "нет такого поля") is None:
            obj.tenant_id = tenant_id


async def bind_tenant(session: AsyncSession) -> str | None:
    """Совместимость: арендатор уже привязан событием `after_begin`.

    Оставлена как явное имя для читателя `session_scope` и как точка, где
    видно, что привязка — не забота вызывающего.
    """
    from app.services.tenant import current_tenant

    return current_tenant()


async def rebind_tenant(session: AsyncSession) -> str | None:
    """Перепривязать арендатора ВНУТРИ уже открытой транзакции.

    `after_begin` срабатывает один раз — в начале транзакции, — и этого хватает
    всем, кто получает сессию уже с назначенным арендатором. Не хватает одному
    случаю: когда арендатор появляется ПОСЛЕ первого запроса. Ровно так
    устроено заведение учётки — сперва проверка «адрес не занят» (транзакция
    открылась, арендатора нет), потом генерация UUID, и только потом вставка
    счёта, которая уже под политикой RLS.

    Без этой функции вставка падает `new row violates row-level security policy
    for table "credit_accounts"`, и падает только на Postgres: на SQLite
    политик нет, и в тестах это невидимо. Поймано `tests/test_rls_postgres.py`.

    На SQLite ничего не делает — там нечего привязывать.
    """
    from app.services.tenant import current_tenant, require_isolation

    require_isolation()
    tenant_id = current_tenant()
    if tenant_id is None or not settings.is_postgres:
        return tenant_id
    await session.execute(_BIND_TENANT_SQL, {"tid": tenant_id})
    return tenant_id


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            await bind_tenant(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
