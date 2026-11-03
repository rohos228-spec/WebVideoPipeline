"""«Аварийный стоп» убирает из очереди генерации проекты, которые ставил чат этой сессии.

Иначе проекты, ждущие в очереди, стартовали бы позже, уже после стопа. Проекты, у
которых шаг уже идёт, получают stop-ключ kill: дальше они не пойдут. Берутся все
задачи сессии, не только последняя (и после рестарта). Чужая сессия и общий стоп
очереди не трогаются. Очередь и БД — фейки.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestrator import queue as orch_queue
from app.orchestrator.agents import supervisor as sup_mod
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_PIDS = (21, 22, 23, 51, 61, 62, 63, 71, 72, 73, 74, 81)


@pytest.fixture(autouse=True)
def _no_db_recheck(monkeypatch: pytest.MonkeyPatch) -> list[list[int]]:
    seen: list[list[int]] = []

    async def _recheck(ids: list[int], session_id: str, since: float) -> list[int]:
        seen.append(list(ids))
        return []

    monkeypatch.setattr(sup_mod, "_recheck_dequeued_in_db", _recheck)
    monkeypatch.setattr(sup_mod, "QUEUE_RECHECK_DELAY_SEC", 0.0)
    return seen


@pytest.fixture(autouse=True)
def _clean_stops() -> Any:
    from app.services.step_cancel import clear_stop

    def _wipe() -> None:
        for project in _PIDS:
            clear_stop(project)

    _wipe()
    yield
    _wipe()


def _fake(stopping: tuple[int, ...] = ()) -> Any:
    """Фейк снятия с очереди: все переданные проекты чата убраны, ``stopping`` — шаг шёл."""
    seen: list[list[int]] = []

    async def _dequeue(ids: list[int], session_id: str) -> dict[str, list[int]]:
        seen.append(list(ids))
        _dequeue.sessions.append(session_id)  # type: ignore[attr-defined]
        return {"removed": list(ids), "stopping": [pid for pid in ids if pid in stopping], "kept_user": []}

    _dequeue.seen = seen  # type: ignore[attr-defined]
    _dequeue.sessions = []  # type: ignore[attr-defined]
    return _dequeue


def _runtime() -> Any:
    return build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", params={})]),
    )


def _enqueue_step(ids: list[int]) -> PlanStep:
    return PlanStep(action="queue.enqueue_many", params={"project_ids": ids, "mode": "full"}, mutating=True)


@pytest.mark.asyncio
async def test_kill_removes_session_projects_and_stops_running_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.step_cancel import is_stop_requested, stop_token

    halted: list[bool] = []
    fake = _fake(stopping=(22,))
    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", fake)
    monkeypatch.setattr(
        "app.services.sidebar_layout.set_gen_queue_halted", lambda *a, **k: halted.append(True)
    )
    runtime = _runtime()
    set_runtime(runtime)
    try:
        tasks = runtime.tasks
        mine = tasks.insert("kq-a", "поставь 21 22 23 в очередь", TaskStatus.done.value)
        tasks.insert_step(mine, 0, _enqueue_step([21, 22, 23]))
        tasks.insert_step(mine, 1, PlanStep(action="project.status", params={"project_id": 40}))
        other = tasks.insert("kq-b", "поставь 31 в очередь", TaskStatus.done.value)
        tasks.insert_step(other, 0, _enqueue_step([31]))

        payload = await runtime.supervisor._publish_kill("kq-a", mine)
        assert fake.seen == [[21, 22, 23]]  # #31 чужой сессии не попал
        assert fake.sessions == ["kq-a"]  # снимаются только номера с меткой этой сессии
        assert payload["queue_removed"] == [21, 22, 23]
        assert payload["queue_stopped"] == [22]
        assert payload["queue_removed_count"] == 3
        # У #22 шаг шёл: stop-ключ kill. Ждущим ключ не ставится (у них user_stop).
        assert is_stop_requested(22) and str(stop_token(22)).startswith("orchestrator_kill:")
        assert not is_stop_requested(21) and not is_stop_requested(23)
        assert halted == []
        events = [e.payload for e in runtime.bus.replay("kq-a", 0) if e.type == "kill_switch"]
        assert events and events[-1]["queue_removed_count"] == 3
        # Возобновление задачи снимает только свой ключ.
        runtime.supervisor._clear_project_stop(mine)
        assert not is_stop_requested(22)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_checks_every_task_of_session_not_only_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """Задача A ставит проекты в очередь, задача B меняет параллель, потом стоп: проекты A убираются."""
    fake = _fake()
    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", fake)
    runtime = _runtime()
    set_runtime(runtime)
    try:
        tasks = runtime.tasks
        first = tasks.insert("kq-s", "поставь 71 72 73 в очередь", TaskStatus.done.value)
        tasks.insert_step(first, 0, _enqueue_step([71, 72, 73]))
        last = tasks.insert("kq-s", "параллель 2", TaskStatus.done.value)
        tasks.insert_step(
            last, 0, PlanStep(action="queue.set_parallel", params={"workers": 2}, mutating=True)
        )
        payload = await runtime.supervisor._publish_kill("kq-s", last)
        assert fake.seen == [[71, 72, 73]]
        assert payload["queue_removed_count"] == 3
        # После рестарта задачи сессии в памяти нет: стоп без task_id всё равно чистит очередь.
        await runtime.supervisor._publish_kill("kq-s", None)
        assert fake.seen[-1] == [71, 72, 73]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_recheck_stops_project_started_during_stop(
    monkeypatch: pytest.MonkeyPatch, _no_db_recheck: list[list[int]]
) -> None:
    """Тик очереди успел стартовать снятый проект: повторная проверка ставит ему stop-ключ."""
    from app.services.step_cancel import is_stop_requested

    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", _fake())

    async def _recheck(ids: list[int], session_id: str, since: float) -> list[int]:
        assert session_id == "kq-r" and since > 0
        _no_db_recheck.append(list(ids))
        return [pid for pid in ids if pid == 74]

    monkeypatch.setattr(sup_mod, "_recheck_dequeued_in_db", _recheck)
    runtime = _runtime()
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("kq-r", "очередь", TaskStatus.done.value)
        runtime.tasks.insert_step(task_id, 0, _enqueue_step([74, 81]))
        payload = await runtime.supervisor._publish_kill("kq-r", task_id)
        assert payload["queue_stopped"] == [74]
        assert is_stop_requested(74) and not is_stop_requested(81)
        assert _no_db_recheck and _no_db_recheck[0] == [74, 81]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_without_queue_steps_does_not_touch_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake()
    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", fake)
    runtime = _runtime()
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("kq-c", "статус", TaskStatus.done.value)
        payload = await runtime.supervisor._publish_kill("kq-c", task_id)
        assert fake.seen == []
        assert payload["queue_removed_count"] == 0
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_queue_failure_does_not_break_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(ids: list[int], session_id: str) -> dict[str, list[int]]:
        raise RuntimeError("db locked")

    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", _boom)
    runtime = _runtime()
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("kq-d", "очередь", TaskStatus.done.value)
        runtime.tasks.insert_step(task_id, 0, _enqueue_step([51]))
        payload = await runtime.supervisor._publish_kill("kq-d", task_id)
        assert payload["queue_removed"] == []
        assert [e for e in runtime.bus.replay("kq-d", 0) if e.type == "kill_switch"]
    finally:
        set_runtime(None)


def test_queue_removed_note_plain_words() -> None:
    assert orch_queue.queue_removed_note([]) == ""
    text = orch_queue.queue_removed_note([21, 23])
    assert "убрал 2 проекта" in text and "#21, #23" in text
    assert "не стартуют" in text
    text = orch_queue.queue_removed_note([21, 22, 23], [22])
    assert "убрал 3 проекта" in text and "У #22 шаг уже шёл" in text and "не пойдут" in text


@pytest.mark.asyncio
async def test_kill_after_queue_task_finished_reports_removed(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    """Задача «поставь в очередь» закончилась, сессии в памяти нет: стоп убирает проекты и говорит сколько."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.orchestrator.auth import OrchestratorAuthMiddleware
    from app.web.routers.orchestrator_chat import router
    from tests.agents.conftest import TEST_TOKEN

    monkeypatch.setattr(sup_mod, "_dequeue_chat_from_queue", _fake())
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", params={})]),
    )
    set_runtime(runtime)
    session = "kq-e"
    try:
        task_id = runtime.tasks.insert(session, "поставь 61 62 63 в очередь", TaskStatus.done.value)
        runtime.tasks.insert_step(task_id, 0, _enqueue_step([61, 62, 63]))
        # Как после рестарта: закрытой задачи в памяти сессии нет.
        assert runtime.supervisor._session_task.get(session) is None
        assert runtime.supervisor.active_task(session) is None
        app = FastAPI()
        app.add_middleware(OrchestratorAuthMiddleware)
        app.include_router(router, prefix="/api")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "аварийный стоп", "kill_switch": True},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert r.status_code == 200
            await runtime.queue.drain()
        texts = [
            str(e.payload.get("text") or "") for e in runtime.bus.replay(session, 0) if e.type == "message"
        ]
        assert any("убрал 3 проекта" in t and "#61, #62, #63" in t for t in texts), texts
    finally:
        set_runtime(None)
