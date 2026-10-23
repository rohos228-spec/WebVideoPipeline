"""Уменьшение параллели не прерывает уже идущие шаги; аварийный стоп убирает ждущих.

* шаг, который реально идёт, вне нового окна не откатывается — доигрывает;
* пока идущих не меньше N, новые проекты очереди не стартуют;
* статус «running» без живого шага (вне окна) по-прежнему сбрасывается;
* dequeue_chat_projects убирает из очереди только номера, поставленные чатом:
  ждущим user_stop, идущие отдаёт под stop-ключ; номер человека не трогает,
  общий стоп очереди не включает;
* тик очереди перед стартом перепроверяет очередь и user_stop.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import gen_queue as gq


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gq_lower.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.gen_queue.is_gen_queue_halted", lambda: False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _add(session: AsyncSession, pid: int, status: ProjectStatus) -> Project:
    from app.services.gen_queue_run import set_gen_queue_run

    p = Project(id=pid, slug=f"p{pid}", topic=f"t{pid}", status=status, auto_mode=True, meta={})
    session.add(p)
    await session.flush()
    await set_gen_queue_run(session, p, mode="until_node", target_node_type="script")
    return p


def _queue(monkeypatch, ids: list[int], n: int) -> None:
    monkeypatch.setattr("app.services.gen_queue.get_gen_queue", lambda: list(ids))
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: n)


@pytest.mark.asyncio
async def test_lowering_parallel_keeps_running_steps(session, monkeypatch) -> None:
    _queue(monkeypatch, [1, 2, 3, 4], 1)  # было 3, стало 1
    live = {1, 2, 3}
    monkeypatch.setattr("app.services.gen_queue._step_really_running", lambda pid: pid in live)
    p1 = await _add(session, 1, ProjectStatus.planning)
    p2 = await _add(session, 2, ProjectStatus.scripting)
    p3 = await _add(session, 3, ProjectStatus.splitting)
    await _add(session, 4, ProjectStatus.plan_ready)
    assert await gq.gen_queue_reconcile(session) == 0
    for p, st in ((p1, ProjectStatus.planning), (p2, ProjectStatus.scripting),
                  (p3, ProjectStatus.splitting)):
        await session.refresh(p)
        assert p.status is st


@pytest.mark.asyncio
async def test_new_starts_wait_while_running_over_limit(session, monkeypatch) -> None:
    _queue(monkeypatch, [1, 2, 3], 2)
    monkeypatch.setattr("app.services.gen_queue._step_really_running", lambda pid: pid in {2, 3})
    started: list[int] = []

    async def _start(session, project, *, queue_pos):
        started.append(project.id)
        return 1

    monkeypatch.setattr("app.services.gen_queue._start_or_advance_project", _start)

    async def _not_at_target(session, project, *, queue_pos):
        return False

    monkeypatch.setattr("app.services.gen_queue._close_slot_if_already_at_target", _not_at_target)
    await _add(session, 1, ProjectStatus.plan_ready)  # первый в окне, ждёт старта
    await _add(session, 2, ProjectStatus.scripting)
    await _add(session, 3, ProjectStatus.splitting)  # вне окна N=2, но шаг идёт
    assert await gq.gen_queue_tick(session) == 0
    assert started == []
    # Один доиграл — место освободилось, новый стартует.
    p3 = await session.get(Project, 3)
    p3.status = ProjectStatus.script_ready
    await session.flush()
    monkeypatch.setattr("app.services.gen_queue._step_really_running", lambda pid: pid == 2)
    assert await gq.gen_queue_tick(session) == 1
    assert started == [1]


@pytest.mark.asyncio
async def test_stale_running_out_of_window_still_rolled_back(session, monkeypatch) -> None:
    _queue(monkeypatch, [1, 2], 1)
    monkeypatch.setattr("app.services.gen_queue._step_really_running", lambda pid: False)
    await _add(session, 1, ProjectStatus.planning)
    p2 = await _add(session, 2, ProjectStatus.planning)
    assert await gq.gen_queue_reconcile(session) == 1
    await session.refresh(p2)
    assert p2.status is ProjectStatus.new


def _chat_owned(p: Project, owner: str | bool = "sess-a") -> None:
    meta = dict(p.meta or {})
    meta["gen_queue_run"] = {**meta["gen_queue_run"], gq.CHAT_OWNER_KEY: owner}
    p.meta = meta


@pytest.fixture
def real_queue(monkeypatch, tmp_path):
    from app.services import sidebar_layout as sl

    monkeypatch.setattr(sl.settings, "data_dir", tmp_path)
    monkeypatch.setattr("app.services.gen_queue.get_gen_queue", sl.get_gen_queue)
    monkeypatch.setattr("app.services.gen_queue._live_work", lambda pid: False)
    return sl


@pytest.mark.asyncio
async def test_dequeue_chat_projects_waiting_and_running(session, real_queue) -> None:
    sl = real_queue
    sl.set_gen_queue([1, 2, 3, 4, 5])
    await _add(session, 1, ProjectStatus.plan_ready)  # чужой слот, чат его не ставил
    p2 = await _add(session, 2, ProjectStatus.scripting)  # шаг идёт
    p3 = await _add(session, 3, ProjectStatus.plan_ready)
    p4 = await _add(session, 4, ProjectStatus.new)
    await _add(session, 5, ProjectStatus.plan_ready)  # номер ставил человек
    for p in (p2, p3, p4):
        _chat_owned(p)
    await session.flush()
    got = await gq.dequeue_chat_projects(session, [2, 3, 4, 5, 9], session_id="sess-a")
    assert got == {"removed": [2, 3, 4], "stopping": [2], "kept_user": [5]}
    assert sl.get_gen_queue() == [1, 5]
    assert sl.is_gen_queue_halted() is False
    for p in (p3, p4):
        assert (p.meta or {}).get("user_stop") is True
        assert "gen_queue_run" not in (p.meta or {})
    # Идущему user_stop не ставим: воркер пропускал бы проект и не снял бы шаг по ключу.
    assert not (p2.meta or {}).get("user_stop")
    assert "gen_queue_run" not in (p2.meta or {})
    for pid in (1, 5):
        p = await session.get(Project, pid)
        assert not (p.meta or {}).get("user_stop")
        assert "gen_queue_run" in (p.meta or {})


@pytest.mark.asyncio
async def test_dequeue_saves_user_stop_before_queue_file(session, real_queue) -> None:
    """Метка user_stop в БД раньше, чем номер снят с очереди: тик не увидит проект без метки."""
    sl = real_queue
    sl.set_gen_queue([3])
    p3 = await _add(session, 3, ProjectStatus.plan_ready)
    _chat_owned(p3)
    await session.flush()
    order: list[str] = []
    real_commit, real_set = session.commit, sl.set_gen_queue

    async def _commit():
        order.append("commit")
        await real_commit()

    def _set(ids):
        order.append("queue")
        real_set(ids)

    session.commit = _commit  # type: ignore[method-assign]
    import app.services.sidebar_layout as sl_mod
    orig = sl_mod.set_gen_queue
    sl_mod.set_gen_queue = _set
    try:
        await gq.dequeue_chat_projects(session, [3], session_id="sess-a")
    finally:
        sl_mod.set_gen_queue = orig
    assert order == ["commit", "queue"]


@pytest.mark.asyncio
async def test_tick_does_not_start_project_stopped_during_tick(session, monkeypatch, real_queue) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sl = real_queue
    sl.set_gen_queue([1, 2])
    monkeypatch.setattr("app.services.gen_queue.worker_max_parallel", lambda: 2)
    await _add(session, 1, ProjectStatus.plan_ready)
    await _add(session, 2, ProjectStatus.plan_ready)
    await session.commit()
    started: list[int] = []

    async def _not_at_target(session, project, *, queue_pos):
        if project.id == 1:
            # Стоп чата пришёл посреди тика: #1 снят с очереди, #2 получил user_stop в другой сессии.
            sl.set_gen_queue([2])
            other = async_sessionmaker(session.bind, expire_on_commit=False)
            async with other() as s2:
                p2 = await s2.get(Project, 2)
                p2.meta = {**(p2.meta or {}), "user_stop": True}
                await s2.commit()
        return False

    async def _start(session, project, *, queue_pos):
        started.append(project.id)
        return 1

    monkeypatch.setattr("app.services.gen_queue._close_slot_if_already_at_target", _not_at_target)
    monkeypatch.setattr("app.services.gen_queue._start_or_advance_project", _start)
    monkeypatch.setattr("app.services.gen_queue.gen_queue_reconcile", _zero)
    # Тик прочитал очередь [1, 2] до стопа.
    monkeypatch.setattr("app.services.gen_queue.get_gen_queue",
                        _first_then_real([1, 2], sl.get_gen_queue))
    assert await gq.gen_queue_tick(session) == 0
    assert started == []


async def _zero(session):
    return 0


def _first_then_real(first, real):
    calls = {"n": 0}

    def _get():
        calls["n"] += 1
        return list(first) if calls["n"] == 1 else real()

    return _get


@pytest.mark.asyncio
async def test_recheck_dequeued_restores_user_stop_and_reports_started(session, real_queue) -> None:
    p1 = await _add(session, 1, ProjectStatus.plan_ready)
    await _add(session, 2, ProjectStatus.scripting)  # тик успел стартовать
    p1.meta = {**(p1.meta or {}), "user_stop": False}
    await session.commit()
    assert await gq.recheck_dequeued(session, [1, 2]) == [2]
    await session.refresh(p1)
    assert p1.meta.get("user_stop") is True


@pytest.mark.asyncio
async def test_enqueue_route_marks_only_chat_owned_numbers(session, monkeypatch, real_queue) -> None:
    """Чат повторно ставит номер, который уже поставил человек: номер остаётся за человеком."""
    from contextlib import asynccontextmanager

    from app.web.routers import sidebar_layout as route

    sl = real_queue

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(route, "session_scope", _scope)
    await _add(session, 1, ProjectStatus.plan_ready)
    await _add(session, 2, ProjectStatus.plan_ready)
    await session.commit()
    sl.set_gen_queue([1])  # #1 человек поставил сам

    got = await route.enqueue_gen_queue(route.GenQueueEnqueue(project_id=1, by_chat="sess-a"))
    assert got["already_queued"] is True
    assert not gq.queued_by_chat(await session.get(Project, 1))

    got = await route.enqueue_gen_queue(route.GenQueueEnqueue(project_id=2, by_chat="sess-a"))
    assert got["already_queued"] is False
    p2 = await session.get(Project, 2)
    assert gq.queued_by_chat(p2, "sess-a") and not gq.queued_by_chat(p2, "sess-b")
    # Другая сессия ставит тот же номер ещё раз — хозяин прежний.
    await route.enqueue_gen_queue(route.GenQueueEnqueue(project_id=2, by_chat="sess-b"))
    p2 = await session.get(Project, 2)
    assert gq.queued_by_chat(p2, "sess-a") and not gq.queued_by_chat(p2, "sess-b")
    # Чат ставит свой номер ещё раз — остаётся чатовым; человек ставит сам — номер его.
    await route.enqueue_gen_queue(route.GenQueueEnqueue(project_id=2, by_chat="sess-a"))
    assert gq.queued_by_chat(await session.get(Project, 2), "sess-a")
    await route.enqueue_gen_queue(route.GenQueueEnqueue(project_id=2))
    assert not gq.queued_by_chat(await session.get(Project, 2))


@pytest.mark.asyncio
async def test_dequeue_leaves_other_session_and_legacy_marks(session, real_queue) -> None:
    """Стоп сессии A не снимает номера сессии B и старые метки ``True`` без сессии."""
    sl = real_queue
    sl.set_gen_queue([11, 12, 13])
    p11 = await _add(session, 11, ProjectStatus.plan_ready)
    p12 = await _add(session, 12, ProjectStatus.plan_ready)
    p13 = await _add(session, 13, ProjectStatus.plan_ready)
    _chat_owned(p11, "sess-a")
    _chat_owned(p12, "sess-b")
    _chat_owned(p13, True)
    await session.flush()
    got = await gq.dequeue_chat_projects(session, [11, 12, 13], session_id="sess-a")
    assert got == {"removed": [11], "stopping": [], "kept_user": [12, 13]}
    assert sl.get_gen_queue() == [12, 13]
    assert not (p12.meta or {}).get("user_stop") and not (p13.meta or {}).get("user_stop")
    assert gq.queued_by_chat(p13) and not gq.queued_by_chat(p13, "sess-a")


@pytest.mark.asyncio
async def test_manual_start_takes_chat_number(session, real_queue) -> None:
    """Ручной ▶ (снятие стопа) на номере чата: номер переходит к человеку, стоп чата его не снимет."""
    from app.services.project_control import USER_STOP_CLEARED_AT_KEY, clear_user_stop_gate

    sl = real_queue
    sl.set_gen_queue([14])
    p = await _add(session, 14, ProjectStatus.plan_ready)
    _chat_owned(p, "sess-a")
    await session.flush()
    cleared = clear_user_stop_gate(p)
    assert "chat_queue_owner" in cleared and not gq.queued_by_chat(p)
    assert (p.meta or {}).get(USER_STOP_CLEARED_AT_KEY)
    await session.flush()
    got = await gq.dequeue_chat_projects(session, [14], session_id="sess-a")
    assert got["removed"] == [] and got["kept_user"] == [14]
    assert sl.get_gen_queue() == [14]


@pytest.mark.asyncio
async def test_recheck_skips_projects_human_took_after_stop(session, real_queue) -> None:
    """Повторная проверка не ставит стоп тому, кого после стопа взял человек."""
    import time

    from app.services.project_control import clear_user_stop_gate

    sl = real_queue
    p1 = await _add(session, 1, ProjectStatus.plan_ready)
    p2 = await _add(session, 2, ProjectStatus.scripting)
    p3 = await _add(session, 3, ProjectStatus.plan_ready)
    p4 = await _add(session, 4, ProjectStatus.plan_ready)
    for p in (p1, p2, p3, p4):
        p.meta = {**(p.meta or {}), "user_stop": True}
    await session.commit()
    since = time.time() - 1
    # #1: человек нажал ▶ (стоп снят позже стопа чата), шаг уже идёт.
    clear_user_stop_gate(p1)
    p1.status = ProjectStatus.planning
    # #2: человек снова поставил номер в очередь сам; шаг идёт.
    p2.meta = {k: v for k, v in p2.meta.items() if k != "user_stop"}
    sl.set_gen_queue([2, 4])
    # #4: в очереди номер этой же сессии чата, чужая запись затёрла стоп — проверка держит.
    _chat_owned(p4, "sess-a")
    p4.meta = {**p4.meta, "user_stop": False}
    # #3: чужая запись затёрла user_stop (стоп снят не человеком) — возвращаем.
    p3.meta = {**(p3.meta or {}), "user_stop": False}
    await session.commit()
    busy = await gq.recheck_dequeued(session, [1, 2, 3, 4], session_id="sess-a", since=since)
    assert busy == []
    for p in (p1, p2, p3, p4):
        await session.refresh(p)
    assert not p1.meta.get("user_stop") and not p2.meta.get("user_stop")
    assert p3.meta.get("user_stop") is True and p4.meta.get("user_stop") is True


@pytest.mark.asyncio
async def test_chat_start_keeps_chat_number(session, real_queue) -> None:
    """▶ самого чата (слой действий) снимает стоп, но номер остаётся за чатом и метка
    «человек снял стоп» не ставится: аварийный стоп чата его по-прежнему снимает."""
    from app.services.project_control import USER_STOP_CLEARED_AT_KEY
    from app.services.project_steps import start_step

    sl = real_queue
    sl.set_gen_queue([15])
    p = await _add(session, 15, ProjectStatus.plan_ready)
    _chat_owned(p, "sess-a")
    p.meta = {**p.meta, "user_stop": True}
    await session.flush()
    with pytest.raises(Exception):  # шаг не существует: важно только снятие стопа до старта
        await start_step(session, p, "no_such_step", explicit_ui_start=True, keep_chat_owner=True)
    assert not (p.meta or {}).get("user_stop")
    assert gq.queued_by_chat(p, "sess-a")
    assert not (p.meta or {}).get(USER_STOP_CLEARED_AT_KEY)
    # ▶ человека в Studio (без keep_chat_owner) — номер переходит к человеку.
    p.meta = {**p.meta, "user_stop": True}
    with pytest.raises(Exception):
        await start_step(session, p, "no_such_step", explicit_ui_start=True)
    assert not gq.queued_by_chat(p) and (p.meta or {}).get(USER_STOP_CLEARED_AT_KEY)


@pytest.mark.asyncio
async def test_continue_button_takes_chat_number(session, real_queue, monkeypatch) -> None:
    """«Продолжить» (POST /projects/{id}/continue) снимает стоп тем же помощником, что ▶:
    метка «стоп снят» и номер очереди чата переходит к человеку."""
    from app.orchestrator import auto_advance as aa
    from app.services.project_control import USER_STOP_CLEARED_AT_KEY

    async def _no_advance(*_a, **_k):
        return False

    async def _no_release(*_a, **_k):
        return None

    monkeypatch.setattr(aa, "maybe_auto_advance", _no_advance)
    sl = real_queue
    sl.set_gen_queue([16])
    p = await _add(session, 16, ProjectStatus.plan_ready)
    _chat_owned(p, "sess-a")
    p.meta = {**p.meta, "user_stop": True}
    await session.flush()
    out = await aa.continue_project_pipeline(session, p)
    assert out["cleared"] == ["user_stop"]
    assert not (p.meta or {}).get("user_stop")
    assert not gq.queued_by_chat(p) and (p.meta or {}).get(USER_STOP_CLEARED_AT_KEY)
    got = await gq.dequeue_chat_projects(session, [16], session_id="sess-a")
    assert got["removed"] == [] and got["kept_user"] == [16]


@pytest.mark.asyncio
async def test_action_layer_header_marks_chat_start(monkeypatch) -> None:
    """Старт чата — только оператор или создатель плана ``orchestrator:task-N`` с проверенным
    токеном; панель «Действия» человека и Studio без заголовка — человек."""
    from app.services import access_guard
    from app.services.action_layer import chat_caller
    from app.web.routers.projects import _from_action_layer

    async def _creator(plan_id):
        return {"plan-chat": "orchestrator:task-7", "plan-human": "web_operator:ivan"}.get(plan_id, "")

    monkeypatch.setattr(chat_caller, "plan_creator", _creator)
    marker = access_guard.operator_token_verified.set(True)
    try:
        assert await _from_action_layer("orchestrator:task-5") is True
        assert await _from_action_layer(None) is False
        assert await _from_action_layer("web_operator:ivan") is False
        assert await _from_action_layer("web_operator:ivan", "plan-human") is False
        # Шаг плана чата, одобренный человеком: исполнитель от имени человека, план — чата.
        assert await _from_action_layer("web_operator:ivan", "plan-chat") is True
    finally:
        access_guard.operator_token_verified.reset(marker)
    assert await _from_action_layer("orchestrator:task-5") is False


@pytest.mark.asyncio
async def test_preempt_by_chat_keeps_chat_number(session, real_queue, monkeypatch) -> None:
    """▶ чата, который прерывает другой идущий шаг, номер очереди чата не забирает."""
    from app.services import project_steps
    from app.services.project_control import USER_STOP_CLEARED_AT_KEY

    for target, name in (("app.services.step_cancel", "kill_active_generation"),
                         ("app.services.step_cancel", "clear_stop"),
                         ("app.services.xlsx_flow_locks", "clear_xlsx_flow_locks")):
        monkeypatch.setattr(f"{target}.{name}", lambda *a, **k: None)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.run_sync.stop_active_running_node", _noop)
    p = await _add(session, 17, ProjectStatus.scripting)
    _chat_owned(p, "sess-a")
    p.meta = {**p.meta, "user_stop": True}
    await session.flush()
    await project_steps._preempt_running_for_manual_start(
        session, p, ProjectStatus.planning, keep_chat_owner=True
    )
    assert gq.queued_by_chat(p, "sess-a") and not p.meta.get(USER_STOP_CLEARED_AT_KEY)
    p.status = ProjectStatus.scripting
    await project_steps._preempt_running_for_manual_start(session, p, ProjectStatus.planning)
    assert not gq.queued_by_chat(p)


@pytest.mark.asyncio
async def test_continue_by_chat_keeps_chat_number(session, real_queue, monkeypatch) -> None:
    """«Продолжить» от задачи чата снимает стоп, но номер очереди остаётся за чатом."""
    from app.orchestrator import auto_advance as aa
    from app.services.project_control import USER_STOP_CLEARED_AT_KEY

    async def _no(*_a, **_k):
        return False

    monkeypatch.setattr(aa, "maybe_auto_advance", _no)
    p = await _add(session, 18, ProjectStatus.plan_ready)
    _chat_owned(p, "sess-a")
    p.meta = {**p.meta, "user_stop": True}
    await session.flush()
    out = await aa.continue_project_pipeline(session, p, by_human=False)
    assert out["cleared"] == ["user_stop"] and not p.meta.get("user_stop")
    assert gq.queued_by_chat(p, "sess-a") and not p.meta.get(USER_STOP_CLEARED_AT_KEY)


@pytest.mark.asyncio
async def test_harness_repair_owner_follows_caller(session, real_queue, monkeypatch) -> None:
    """Починка harness: от человека номер чата переходит к нему, по умолчанию (воркер/чат) — нет."""
    from app.services import agent_harness as ah

    seen: list[bool] = []

    async def _start(session, project, code, **kw):
        seen.append(kw["keep_chat_owner"])

    free = "split"  # на этой линии нет списка «бесплатных» починок: любой не запрещённый шаг

    class _Rep:
        ok = False
        checks: list = []
        repair_steps = [free]
        run_id = "r1"
        next_action = ""

    monkeypatch.setattr(ah, "verify_project_disk", lambda *a, **k: _Rep())
    monkeypatch.setattr(ah, "append_ops_event", lambda *a, **k: None)
    monkeypatch.setattr(ah, "write_ops_telemetry", lambda meta, rep: meta)
    monkeypatch.setattr("app.services.project_steps.start_step", _start)
    p = await _add(session, 19, ProjectStatus.plan_ready)
    await ah.run_harness_verify(session, p, allow_repair=True)
    await ah.run_harness_verify(session, p, allow_repair=True, keep_chat_owner=False)
    assert seen == [True, False]


@pytest.mark.asyncio
async def test_recheck_spares_chat_start_after_stop(session, real_queue) -> None:
    """Чат сам снова запустил проект после своего стопа — повторная проверка его не останавливает."""
    import time

    from app.services.project_steps import start_step

    p = await _add(session, 20, ProjectStatus.plan_ready)
    _chat_owned(p, "sess-a")
    p.meta = {**p.meta, "user_stop": True}
    await session.commit()
    since = time.time() - 1
    with pytest.raises(Exception):
        await start_step(session, p, "no_such_step", explicit_ui_start=True, keep_chat_owner=True)
    p.status = ProjectStatus.planning
    await session.commit()
    busy = await gq.recheck_dequeued(session, [20], session_id="sess-a", since=since)
    assert busy == []
    await session.refresh(p)
    assert not p.meta.get("user_stop")
