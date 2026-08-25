"""Упавший шаг обязан отпустить step-lease.

Ровно этого не происходило на боевом сервере 2026-08-25. Шаг падал на ошибке
БД, lease оставался в таблице, и КАЖДЫЙ следующий такт воркера писал

    [#2] advance: planning занят живым step-lease (другой процесс) — пропуск такта

при том, что процесс был тот же самый. TTL шага — час, значит одно падение
замораживало проект на час. Снаружи: «нажал «Сгенерировать» — ничего не
происходит», ни ошибки, ни движения.

Механизмов, съедавших удаление, было два, и оба молча:

1. `release` ходил сессией вызывающего. На Postgres ошибка шага переводит
   транзакцию в aborted — следующий DELETE в ней не выполняется вообще.
2. Даже выполнившись, DELETE откатывался: исключение уходит в `session_scope`
   воркера, а там `except: await session.rollback()`.

Второй пункт важен отдельно — он не зависит от СУБД. То есть на упавшем шаге
lease не освобождался никогда и нигде, и на SQLite тоже. Тест поэтому проверяет
не «Postgres против SQLite», а само правило: после падения строки быть не
должно.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.work_lease as wl
from app.models import Base, WorkLease


@pytest_asyncio.fixture
async def lease_db(tmp_path, monkeypatch):
    """Своя база и свой session_scope — как в test_work_lease.py."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(wl, "session_scope", scope)
    yield factory
    await engine.dispose()


async def _rows(factory) -> list[WorkLease]:
    async with factory() as s:
        return list((await s.execute(select(WorkLease))).scalars().all())


@pytest.mark.asyncio
async def test_release_without_session_deletes_the_row(lease_db):
    """Базовый случай: release своей короткой сессией доводит удаление до БД.

    Именно этим путём теперь идёт падение шага, поэтому он проверяется отдельно
    от пути с сессией вызывающего.
    """
    assert await wl.acquire(7, "step:plan", owner="me", ttl_s=3600) is True
    assert await wl.release(7, "step:plan", owner="me") is True
    assert await _rows(lease_db) == []


@pytest.mark.asyncio
async def test_release_through_a_rolled_back_session_loses_the_delete(lease_db):
    """Обратная сторона: сессия, которую откатят, удаление НЕ сохраняет.

    Тест закрепляет причину, а не следствие. Без него правка в
    `advance_project` выглядит перестраховкой: «ну передали сессию, ну и что».
    А цена — час простоя.
    """
    assert await wl.acquire(7, "step:plan", owner="me", ttl_s=3600) is True

    async with lease_db() as caller:
        assert await wl.release(7, "step:plan", owner="me", session=caller) is True
        # Шаг упал — вызывающий откатывается, унося с собой и удаление lease.
        await caller.rollback()

    assert len(await _rows(lease_db)) == 1, (
        "DELETE в откаченной транзакции не должен был сохраниться — "
        "если сохранился, тест ниже ничего не доказывает"
    )
    # И единица по-прежнему занята: следующий такт её не возьмёт.
    assert await wl.acquire(7, "step:plan", owner="other", ttl_s=3600) is False


@pytest.mark.asyncio
async def test_failed_step_releases_its_lease(lease_db, monkeypatch):
    """Сквозная проверка через сам `advance_project`.

    Шаг планирования падает; после того как исключение вышло наружу, строки
    lease быть не должно.
    """
    from app.models import Project, ProjectStatus

    pipeline = pytest.importorskip("app.orchestrator.pipeline")

    project = Project(id=7, slug="rolik", topic="тема", status=ProjectStatus.planning)

    async def _boom(session, proj, bot=None):
        raise RuntimeError("шаг упал так же, как на сервере")

    monkeypatch.setattr(pipeline.make_plan, "run", _boom)

    async with lease_db() as session:
        with pytest.raises(RuntimeError, match="упал так же"):
            await pipeline.advance_project(session, project, None)
        # Так же, как воркер: исключение → откат.
        await session.rollback()

    assert await _rows(lease_db) == [], (
        "после падения шага step-lease остался — проект будет пропускать такты до конца TTL (час)"
    )
    assert await wl.acquire(7, "step:plan", owner="next-tick", ttl_s=3600) is True


@pytest.mark.asyncio
async def test_successful_step_also_releases(lease_db, monkeypatch):
    """Успешный путь не сломан: там release идёт сессией вызывающего."""
    from app.models import Project, ProjectStatus

    pipeline = pytest.importorskip("app.orchestrator.pipeline")

    project = Project(id=8, slug="rolik2", topic="тема", status=ProjectStatus.planning)

    async def _ok(session, proj, bot=None):
        return None

    monkeypatch.setattr(pipeline.make_plan, "run", _ok)

    async with lease_db() as session:
        await pipeline.advance_project(session, project, None)
        await session.commit()

    assert await _rows(lease_db) == []


# ── Пересборка контейнера ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redeploy_reclaims_previous_generation(lease_db, monkeypatch):
    """Новый контейнер забирает lease прошлого поколения того же узла.

    До правки имя узла бралось из `socket.gethostname()`, а в контейнере это
    его id — он меняется при КАЖДОЙ пересборке. Owner прошлого контейнера
    выглядел как чужая машина, и обе процедуры перехвата его пропускали. Любая
    выкладка поверх идущего шага морозила проект на час.

    Второй капкан — `_pid_alive`. Он смотрит в своё пространство pid: у нового
    контейнера оно новое, и pid 6 прошлого поколения там снова занят — уже
    собственным процессом. Проверка уверенно отвечает «жив» про давно убитый
    процесс, поэтому одного стабильного имени мало.
    """
    import os

    monkeypatch.setattr(wl, "_HOST", "studio")
    monkeypatch.setattr(wl, "_HOST_IS_DECLARED", True)
    # Прошлое поколение: тот же узел, чужой pid, и этот pid «жив» — ровно как
    # в пересобранном контейнере.
    monkeypatch.setattr(wl, "_pid_alive", lambda pid: True)

    stale = f"studio:{os.getpid() + 1}:abcdef123456"
    assert await wl.acquire(9, "step:plan", owner=stale, ttl_s=3600) is True

    assert await wl.expire_dead_local_leases() == 1
    assert await wl.acquire(9, "step:plan", owner="новое-поколение", ttl_s=3600) is True


@pytest.mark.asyncio
async def test_undeclared_node_does_not_steal_live_work(lease_db, monkeypatch):
    """Без объявленного имени сосед по машине неприкосновенен.

    На машине разработчика два запущенных экземпляра делят hostname. Правило
    «наше имя + чужой pid = мертвец» там неверно, и его применение отобрало бы
    у соседа живую работу. Поэтому оно включается только объявленным
    `FLEET_NODE_NAME`.
    """
    import os

    monkeypatch.setattr(wl, "_HOST", "fedora")
    monkeypatch.setattr(wl, "_HOST_IS_DECLARED", False)
    monkeypatch.setattr(wl, "_pid_alive", lambda pid: True)

    neighbour = f"fedora:{os.getpid() + 1}:abcdef123456"
    assert await wl.acquire(9, "step:plan", owner=neighbour, ttl_s=3600) is True

    assert await wl.expire_dead_local_leases() == 0
    assert await wl.acquire(9, "step:plan", owner="я", ttl_s=3600) is False


@pytest.mark.asyncio
async def test_dead_pid_is_still_reclaimed_without_declared_name(lease_db, monkeypatch):
    """Прежнее поведение сохранено: мёртвый pid своей машины освобождается."""
    import os

    monkeypatch.setattr(wl, "_HOST", "fedora")
    monkeypatch.setattr(wl, "_HOST_IS_DECLARED", False)
    monkeypatch.setattr(wl, "_pid_alive", lambda pid: False)

    dead = f"fedora:{os.getpid() + 1}:abcdef123456"
    assert await wl.acquire(9, "step:plan", owner=dead, ttl_s=3600) is True

    assert await wl.expire_dead_local_leases() == 1


def test_declared_node_name_wins_over_hostname(monkeypatch):
    """`FLEET_NODE_NAME` задаёт имя узла и помечает его объявленным."""
    from app.settings import settings

    monkeypatch.setattr(settings, "fleet_node_name", "studio")
    assert wl._node_identity() == ("studio", True)

    monkeypatch.setattr(settings, "fleet_node_name", "")
    name, declared = wl._node_identity()
    assert declared is False
    assert name  # какой-то hostname всё же есть


@pytest.mark.asyncio
async def test_failed_step_releases_lease_for_a_persistent_project(lease_db, monkeypatch):
    """Проект, загруженный из базы, а не свежесозданный — как на сервере.

    Отличие решающее. После `session.rollback()` персистентный объект
    протухает, и любое `project.id` в `finally` идёт в базу синхронно — вне
    greenlet. Первая редакция освобождения так и упала:
    «greenlet_spawn has not been called» заменил собой ошибку шага, lease
    остался, проект простоял час при 302 пропущенных тактах. Свежесозданный
    объект из соседнего теста после rollback просто становится transient и
    хранит `id` в памяти — потому тот тест молчал.
    """
    from app.models import Project, ProjectStatus

    pipeline = pytest.importorskip("app.orchestrator.pipeline")

    async with lease_db() as s:
        s.add(Project(id=11, slug="persist", topic="тема", status=ProjectStatus.planning))
        await s.commit()

    async def _boom(session, proj, bot=None):
        raise RuntimeError("шаг упал на живом объекте")

    monkeypatch.setattr(pipeline.make_plan, "run", _boom)

    async with lease_db() as session:
        project = await session.get(Project, 11)
        assert project is not None
        # Именно исходная ошибка шага, а не подмена из finally.
        with pytest.raises(RuntimeError, match="на живом объекте"):
            await pipeline.advance_project(session, project, None)
        await session.rollback()

    assert await _rows(lease_db) == [], "lease персистентного проекта не освобождён после падения"
    assert await wl.acquire(11, "step:plan", owner="next", ttl_s=3600) is True
