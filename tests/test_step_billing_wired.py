"""Касса, врезанная в исполнение шага: что списывается и когда не списывается.

`tests/test_step_billing.py` проверяет саму кассу как механизм. Здесь —
что она стоит там, где деньги действительно тратятся, и что она не мешает
работать там, где денег нет.

Врезка сделана в `advance_project_job` — единственную точку, через которую
проходит любое исполнение шага: и такт воркера, и ручной запуск из канваса,
и повтор после HITL. Проверять её на уровне отдельных обработчиков было бы
проверкой не того: обработчиков за двадцать, и забыть можно в любом.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CreditEntry, CreditHold, Frame, FrameStatus, Project, ProjectStatus
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Своя база и разрешённый арендатор без RLS.

    `ALLOW_UNISOLATED_TENANTS` включён законно: проверяется механика денег, а
    не граница безопасности. Саму изоляцию проверяет `test_rls_postgres.py`
    на живом Postgres — на SQLite политик нет физически.
    """
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bill.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    yield factory
    await engine.dispose()


async def _project(factory, *, frames: int = 24, status=ProjectStatus.generating_videos) -> int:
    async with factory() as s:
        p = Project(slug=f"bill-{uuid.uuid4().hex[:8]}", topic="касса", status=status)
        s.add(p)
        await s.flush()
        for n in range(1, frames + 1):
            s.add(
                Frame(
                    project_id=p.id,
                    number=n,
                    voiceover_text="а" * 40,
                    status=FrameStatus.image_prompt_ready,
                )
            )
        await s.commit()
        return int(p.id)


async def test_owner_mode_creates_no_money_at_all(db, monkeypatch) -> None:
    """На машине владельца касса не оставляет следов.

    Это половина смысла врезки: владелец платит провайдерам напрямую,
    кредитов у него нет вовсе, и списывать их не с чего. Если бы касса
    заводила счета и холды «на всякий случай», живой конвейер начал бы
    упираться в баланс, которого не существует.
    """
    from app.services.advance_runner import advance_project_job
    from app.services.noop_bot import get_worker_bot

    project_id = await _project(db)
    # Патчится имя В МОДУЛЕ-ПОТРЕБИТЕЛЕ, а не в `pipeline`: `advance_runner`
    # импортировал функцию по имени, и подмена в источнике до него не дойдёт —
    # настоящий шаг пойдёт в браузер и к провайдеру прямо из теста.
    monkeypatch.setattr("app.services.advance_runner.advance_project", _noop_advance)
    await advance_project_job(project_id, get_worker_bot(None))

    async with db() as s:
        assert (await s.execute(select(CreditHold))).scalars().all() == []
        assert (await s.execute(select(CreditEntry))).scalars().all() == []


async def test_step_volume_reads_frames_and_chars(db) -> None:
    """Объём шага касса выясняет сама, и для каждого шага он свой.

    Требовать число кадров от вызывающего значит требовать помнить, что
    «Видео» тарифицируется по кадрам, «Озвучка» — по символам, а «План» —
    никак. Забыть можно в одном месте, а недосчитается холд у всех.
    """
    from app.services.step_billing import step_volume

    project_id = await _project(db, frames=24)
    async with db() as s:
        assert await step_volume(s, project_id, "video") == (24, None)
        assert await step_volume(s, project_id, "img") == (24, None)
        frames, chars = await step_volume(s, project_id, "audio")
        assert frames is None
        assert chars == 24 * 40
        # Шаг без медийной части — объём неприменим, а не ноль.
        assert await step_volume(s, project_id, "plan") == (None, None)


async def test_hold_covers_the_video_step_and_settles_by_fact(db, monkeypatch) -> None:
    """Резерв ставится до шага, списание идёт по факту, излишек возвращается.

    Проверяется на «Видео» намеренно: это 82% себестоимости ролика и
    единственный шаг, чья цена точна до цента ещё до запуска.
    """
    from app.services import credit_ledger as cl
    from app.services.advance_runner import advance_project_job
    from app.services.noop_bot import get_worker_bot
    from app.services.tenant import tenant_scope

    tenant = str(uuid.uuid4())
    project_id = await _project(db)
    async with db() as s:
        await cl.topup(s, tenant, 100 * 10**6, memo="тест")
        await s.commit()

    monkeypatch.setattr("app.services.advance_runner.advance_project", _noop_advance)
    with tenant_scope(tenant):
        await advance_project_job(project_id, get_worker_bot(None))

    async with db() as s:
        holds = (await s.execute(select(CreditHold))).scalars().all()
        assert len(holds) == 1, "резерв под шаг не поставлен"
        assert holds[0].step_code == "video"
        assert holds[0].state == "settled", "резерв не закрыт после шага"
        # Шаг ничего не потратил — списано ноль, всё вернулось.
        cached, computed = await cl.reconcile(s, tenant)
        assert cached == computed == 100 * 10**6


async def test_failed_step_returns_the_whole_hold(db, monkeypatch) -> None:
    """Упавший шаг не стоит клиенту ничего.

    Решение владельца (§5.5): за брак системы платит платформа. Технически
    это и означает «резерв снимается целиком», а не «спишем, сколько
    успели» — попытка, не давшая результата, результатом не является.
    """
    from app.services import credit_ledger as cl
    from app.services.advance_runner import advance_project_job
    from app.services.noop_bot import get_worker_bot
    from app.services.tenant import tenant_scope

    tenant = str(uuid.uuid4())
    project_id = await _project(db)
    async with db() as s:
        await cl.topup(s, tenant, 100 * 10**6)
        await s.commit()

    async def _boom(session, project, bot):
        raise RuntimeError("провайдер отказал")

    monkeypatch.setattr("app.services.advance_runner.advance_project", _boom)
    with tenant_scope(tenant), pytest.raises(RuntimeError):
        await advance_project_job(project_id, get_worker_bot(None))

    async with db() as s:
        holds = (await s.execute(select(CreditHold))).scalars().all()
        assert [h.state for h in holds] == ["released"]
        assert await cl.balance_micro(s, tenant) == 100 * 10**6, "клиент заплатил за падение"


async def test_empty_balance_waits_instead_of_failing_the_step(db, monkeypatch) -> None:
    """Нет денег — проект ждёт пополнения, а не откатывается назад.

    Счётчик неудач воркера откатывает проект на предыдущий шаг после
    нескольких падений подряд. Если бы нехватка кредитов приходила как
    исключение шага, непополненный баланс разбирал бы работу клиента по
    кусочку каждые несколько минут.
    """
    from app.services.advance_runner import advance_project_job
    from app.services.noop_bot import get_worker_bot
    from app.services.tenant import tenant_scope

    tenant = str(uuid.uuid4())
    project_id = await _project(db)
    monkeypatch.setattr("app.services.advance_runner.advance_project", _noop_advance)

    with tenant_scope(tenant):
        result = await advance_project_job(project_id, get_worker_bot(None))

    assert result.new_status is None, "статус тронут при нулевом балансе"
    async with db() as s:
        assert (await s.execute(select(CreditHold))).scalars().all() == []


async def test_empty_balance_is_reported_outward(db, monkeypatch) -> None:
    """Ожидание пополнения обязано быть видно, а не только записано в журнал.

    Без сигнала наружу оно неотличимо от зависшего проекта: воркер тикает,
    статус не меняется, интерфейс молчит — и человек идёт жаловаться вместо
    того, чтобы пополнить баланс.
    """
    from app.services.advance_runner import _NO_CREDITS_LOGGED, advance_project_job
    from app.services.noop_bot import get_worker_bot
    from app.services.tenant import tenant_scope

    events: list[dict] = []

    async def _capture(project_id, *, event_type, payload=None):
        events.append({"project_id": project_id, "type": event_type, **(payload or {})})

    monkeypatch.setattr("app.services.event_bus.publish_project_event", _capture)
    monkeypatch.setattr("app.services.advance_runner.advance_project", _noop_advance)

    project_id = await _project(db)
    _NO_CREDITS_LOGGED.pop(project_id, None)
    with tenant_scope(str(uuid.uuid4())):
        await advance_project_job(project_id, get_worker_bot(None))

    assert [e["type"] for e in events] == ["credits_required"]
    assert events[0]["step_code"] == "video"

    # Второй тик подряд молчит: воркер тикает каждые пять секунд, и событие
    # на каждый тик — не сигнал, а шум, который перестают замечать.
    with tenant_scope(str(uuid.uuid4())):
        await advance_project_job(project_id, get_worker_bot(None))
    assert len(events) == 1


async def _noop_advance(session, project, bot) -> None:
    """Шаг, который ничего не делает: касса проверяется отдельно от шага."""
    return None
