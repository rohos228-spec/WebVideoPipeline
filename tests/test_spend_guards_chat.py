"""Деньги и стоп в чате: резерв LLM чата при потолке, потолок цепочки, рубильник nostream."""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.services import actions_v1 as _actions_v1  # noqa: F401 - action_execution_controls в Base.metadata
from app.services import kill_switch, request_context, spend_guard
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: sessions)
    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", sessions)
    # На этой линии полей SPEND_*_CAP_USD в Settings нет, spend_guard читает их через getattr.
    monkeypatch.setitem(settings.__dict__, "spend_daily_cap_usd", 5.0)
    monkeypatch.setitem(settings.__dict__, "spend_project_cap_usd", 0.0)
    monkeypatch.setitem(settings.__dict__, "spend_run_cap_usd", 0.0)
    monkeypatch.delenv("PAID_GATE_CHAT_CONTROL_RESERVE_USD", raising=False)
    from app.services import disk_guard

    monkeypatch.setattr(disk_guard, "check", lambda *a, **k: disk_guard.DiskCheck(True, "ok", "ok"))
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    yield sessions
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    await engine.dispose()


async def _spent(sessions, usd: float, *, run_id: str = "", project_id: int | None = None) -> None:
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        s.add(
            spend_guard.SpendLedger(
                day=spend_guard._today(),
                kind="actual",
                provider="kie",
                project_id=project_id,
                run_id=run_id,
                source="media_native",
                usd=usd,
            )
        )
        await s.commit()


async def test_daily_cap_blocks_paid_step_but_chat_control_takes_reserve(db) -> None:
    await _spent(db, 6.0)
    with pytest.raises(spend_guard.PaidCallBlocked) as paid:
        await spend_guard.paid_gate(provider="kie", what="image submit", estimate_usd=0.02)
    assert paid.value.code == "spend_cap_daily"
    # Обычный ход чата (не стоп) тоже стоит за потолком.
    with pytest.raises(spend_guard.PaidCallBlocked):
        await spend_guard.paid_gate(provider="kie", what="orchestrator llm", estimate_usd=0.02)
    ticket = await spend_guard.paid_gate(
        provider="kie", what="orchestrator llm", estimate_usd=0.02, control=True
    )
    ticket.release()
    assert spend_guard.chat_control_used_usd() == pytest.approx(0.02)
    # Платный шаг после хода чата всё так же заблокирован: резерв ему не достаётся.
    with pytest.raises(spend_guard.PaidCallBlocked):
        await spend_guard.paid_gate(provider="kie", what="image submit", estimate_usd=0.02, control=False)


async def test_chat_control_reserve_is_small_and_runs_out(db, monkeypatch) -> None:
    monkeypatch.setenv("PAID_GATE_CHAT_CONTROL_RESERVE_USD", "0.05")
    await _spent(db, 6.0)
    for _ in range(2):
        (
            await spend_guard.paid_gate(
                provider="kie", what="orchestrator llm", estimate_usd=0.02, control=True
            )
        ).release()
    with pytest.raises(spend_guard.PaidCallBlocked) as blocked:
        await spend_guard.paid_gate(provider="kie", what="orchestrator llm", estimate_usd=0.02, control=True)
    assert blocked.value.code == "spend_cap_daily"


async def test_precheck_does_not_spend_reserve(db) -> None:
    await _spent(db, 6.0)
    await spend_guard.paid_gate(
        provider="kie", what="orchestrator llm", estimate_usd=0.02, control=True, register=False
    )
    assert spend_guard.chat_control_used_usd() == 0.0


async def test_chat_control_does_not_pass_kill_switch(db) -> None:
    await kill_switch.set_state(db, active=True, actor="test-operator", rationale="stop test")
    with pytest.raises(spend_guard.PaidCallBlocked) as blocked:
        await spend_guard.paid_gate(provider="kie", what="orchestrator llm", estimate_usd=0.02, control=True)
    assert blocked.value.code == spend_guard.BLOCK_KILL_SWITCH


def test_transport_marks_chat_control_from_context() -> None:
    from app.orchestrator.routing import transport as tr

    src = inspect.getsource(tr.HttpxTransport.complete)
    assert "control=spend_guard.chat_control_active()" in src
    assert spend_guard.chat_control_active() is False
    with spend_guard.chat_control(True):
        assert spend_guard.chat_control_active() is True
        with spend_guard.chat_control(False):
            assert spend_guard.chat_control_active() is True
    assert spend_guard.chat_control_active() is False


@pytest.mark.parametrize(
    "text, expected",
    [
        ("останови все проекты", True),
        ("стоп", True),
        ("аварийный стоп", True),
        ("поставь проект 12 на паузу", True),
        ("какой статус проекта?", True),
        ("что с генерацией", True),
        ("отмени шаг", True),
        ("запусти генерацию картинок", False),
        ("сделай отчёт по кадрам", False),
        ("отменённые кадры перегенерируй", False),
    ],
)
def test_control_request_phrases(text: str, expected: bool) -> None:
    from app.orchestrator.routing.classifier import is_control_request

    assert is_control_request(text) is expected


def test_queue_and_supervisor_wrap_turns_in_chat_control() -> None:
    from app.orchestrator.agents.supervisor import Supervisor
    from app.orchestrator.queue import MessageQueue

    assert "chat_control(" in inspect.getsource(MessageQueue._classify)
    assert "chat_control(is_control_request(goal))" in inspect.getsource(Supervisor._drive)


async def test_chain_budget_stops_chain_over_ceiling(db, monkeypatch) -> None:
    monkeypatch.setitem(settings.__dict__, "spend_daily_cap_usd", 0.0)
    meta = {spend_guard.CHAIN_BUDGET_META: spend_guard.chain_budget_meta(7, 400, "plan1-0")}
    run_id, cap = spend_guard.chain_budget(meta)
    assert run_id == "chain:7:plan1-0" and cap == pytest.approx(2.0)
    with spend_guard.chain_budget_scope(meta):
        assert request_context.get_run_id() == run_id
        ticket = await spend_guard.paid_gate(
            provider="kie", what="image submit", estimate_usd=0.5, project_id=7
        )
        ticket.release()
        await _spent(db, 1.8, run_id=run_id, project_id=7)
        with pytest.raises(spend_guard.PaidCallBlocked) as blocked:
            await spend_guard.paid_gate(provider="kie", what="image submit", estimate_usd=0.5, project_id=7)
    assert blocked.value.code == "spend_cap_run"
    assert "цепочки, запущенной из чата" in blocked.value.message_ru
    # Вне цепочки тот же проект потолком цепочки не ограничен.
    assert request_context.get_run_id() != run_id
    (
        await spend_guard.paid_gate(provider="kie", what="image submit", estimate_usd=0.5, project_id=7)
    ).release()


def test_chain_budget_ignores_bad_meta() -> None:
    key = spend_guard.CHAIN_BUDGET_META
    assert spend_guard.chain_budget(None) is None
    assert spend_guard.chain_budget({}) is None
    assert spend_guard.chain_budget({key: {"run_id": "native:1:x", "cap_usd": 1}}) is None
    assert spend_guard.chain_budget({key: {"run_id": "chain:1:x", "cap_usd": "nan"}}) is None
    assert spend_guard.chain_budget({key: {"run_id": "chain:1:x", "cap_usd": 0}}) is None
    with spend_guard.chain_budget_scope({}) as bound:
        assert bound is None
    assert spend_guard.execution_of("chain:1:x") is None


async def test_launch_passes_chain_ceiling_to_step_run(monkeypatch) -> None:
    from app.services.action_layer import builtin_launch as bl
    from app.services.action_layer.context import ActionContext

    calls: list[tuple] = []

    async def verify(ctx, params):
        return None

    async def call(self, method, path, *, params=None, json=None, files=None, timeout=120.0):
        calls.append((method, path, dict(params or {})))
        return {}

    monkeypatch.setattr(bl, "_verify_any_cost", verify)
    monkeypatch.setattr(ActionContext, "call", call)
    ctx = ActionContext(app=None, plan_id="p-abc", step_index=2)
    await bl._run_any_step(
        ctx, {"project_id": 7, "step_code": "images", "allow_auto_chain": True, "ceiling_credits": 400}
    )
    await bl._run_any_step(ctx, {"project_id": 7, "step_code": "images", "ceiling_credits": 400})
    # Запуск сначала читает проект (GET, отметки честного итога шага): смотрим только POST запуска.
    runs = [c for c in calls if c[0] == "POST"]
    assert runs[0][2]["run_cap_credits"] == 400 and runs[0][2]["run_tag"] == "p-abc-2"
    assert "run_cap_credits" not in runs[1][2] and "run_tag" not in runs[1][2]


async def test_step_run_endpoint_sets_and_clears_chain_budget(monkeypatch) -> None:
    from types import SimpleNamespace

    import sqlalchemy.orm.attributes as attrs

    from app.web.routers import projects as pr

    project = SimpleNamespace(id=7, meta={"keep": 1}, status=SimpleNamespace(value="script_ready"))
    events: list = []

    class Sess:
        async def get(self, model, pid):
            return project

        async def refresh(self, obj):
            return None

    async def commit(session):
        events.append(("commit", dict(project.meta or {}).get(spend_guard.CHAIN_BUDGET_META)))

    async def start(session, p, step_code, **kwargs):
        events.append(("start", dict(p.meta or {}).get(spend_guard.CHAIN_BUDGET_META)))

    async def noop(*_a, **_k):
        return None

    async def unmanaged(project_id):
        return False

    import app.services.actions_v1 as av1

    monkeypatch.setattr(av1, "managed_project_enabled", unmanaged)
    monkeypatch.setattr(pr, "commit_with_retry", commit)
    monkeypatch.setattr(pr, "start_step", start)
    monkeypatch.setattr(pr, "sync_run_for_project", noop)
    monkeypatch.setattr(pr, "publish_project_event", noop)
    monkeypatch.setattr(pr, "project_to_detail", lambda p: {"meta": dict(p.meta or {})})
    monkeypatch.setattr(attrs, "flag_modified", lambda *_a, **_k: None)

    await pr.run_project_step(
        7, "images", dry_run=False, run_cap_credits=400, run_tag="p-abc-2", session=Sess()
    )
    first_start = next(ev for ev in events if ev[0] == "start")
    assert events[0][0] == "commit" and events[0][1]["run_id"] == "chain:7:p-abc-2"
    assert first_start[1]["cap_usd"] == pytest.approx(2.0)
    assert project.meta["keep"] == 1

    events.clear()
    await pr.run_project_step(7, "images", dry_run=False, session=Sess())
    assert spend_guard.CHAIN_BUDGET_META not in project.meta
    assert next(ev for ev in events if ev[0] == "start")[1] is None


def test_worker_tick_runs_inside_chain_budget() -> None:
    from app.services import advance_runner

    src = inspect.getsource(advance_runner.advance_project_job)
    assert src.index("chain_budget_scope(chain_meta)") < src.index(
        "await advance_project(session, project, bot)"
    )
    # Управляемый запуск не переписывается на run_id цепочки.
    assert 'run_id.startswith("native:")' in src


def test_single_nostream_definition_checks_kill_switch() -> None:
    from pathlib import Path

    from app.services import gpt_api

    text = Path(gpt_api.__file__).read_text(encoding="utf-8")
    assert text.count("async def _chat_completions_nostream(") == 1
    assert "check_api_allowed" in inspect.getsource(inspect.unwrap(gpt_api._chat_completions_nostream))


async def test_nostream_salvage_refused_by_kill_switch(monkeypatch) -> None:
    from app.services import api_tracker_hook, gpt_api

    async def gate(**kw):
        return spend_guard.PaidTicket(None, 0.0)

    def blocked(provider: str = "", model: str = "") -> None:
        raise api_tracker_hook.ApiEmergencyBlockedError("Вызовы API временно заблокированы (тест).")

    def no_http(*a, **k):
        raise AssertionError("HTTP при включённом рубильнике")

    monkeypatch.setattr(spend_guard, "paid_gate", gate)
    monkeypatch.setattr(api_tracker_hook, "check_api_allowed", blocked)
    monkeypatch.setattr(gpt_api, "_async_client", no_http)
    with pytest.raises(api_tracker_hook.ApiEmergencyBlockedError):
        await gpt_api._chat_completions_nostream(
            url="http://x/v1/chat/completions", headers={}, body={}, timeout=5.0, use_model="m"
        )
