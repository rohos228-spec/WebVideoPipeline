"""Этап 3 (блок E): бюджет-предохранитель, пауза с причиной, API дашборда."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, LlmCall, Project, ProjectStatus
from app.services import gpt_api, llm_ledger as ledger
from app.services.llm_ledger import BudgetExhausted
from app.services.llm_override import LlmAccountingContext, use_accounting
from app.services.step_failure_policy import record_step_failure
from app.settings import settings


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
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

    monkeypatch.setattr(ledger, "session_scope", scope)
    ledger._spent_cache.clear()
    ledger._budget_cache.clear()
    ledger._unpersisted_spent.clear()
    monkeypatch.setattr(ledger, "_failed_inserts", 0)
    yield factory
    await engine.dispose()


async def _add_project(factory, *, meta: dict | None = None) -> int:
    import uuid

    async with factory() as s:
        p = Project(
            slug=f"b-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.planning,
            auto_mode=False,
            meta=meta or {},
        )
        s.add(p)
        await s.commit()
        return p.id


async def _add_call(factory, project_id: int, cost: float, **kw) -> None:
    async with factory() as s:
        s.add(
            LlmCall(
                project_id=project_id,
                node_key=kw.get("node_key", "n"),
                logical_call_id="l",
                model=kw.get("model", "m"),
                cost_usd=cost,
                result=kw.get("result", "ok"),
                unbilled=kw.get("unbilled", False),
                contract_rejected=kw.get("contract_rejected", False),
                prompt_tokens=kw.get("prompt_tokens"),
                completion_tokens=kw.get("completion_tokens"),
            )
        )
        await s.commit()


# ── check_budget ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_budget_raises_when_spent_reaches_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "llm_budget_usd", 1.0)
    pid = await _add_project(db)
    await _add_call(db, pid, 0.6)
    await ledger.check_budget(pid)  # 0.6 < 1.0
    await _add_call(db, pid, 0.5)
    ledger.invalidate_budget_cache(pid)
    with pytest.raises(BudgetExhausted) as ei:
        await ledger.check_budget(pid)
    assert ei.value.spent_usd == pytest.approx(1.1) and ei.value.budget_usd == 1.0


@pytest.mark.asyncio
async def test_budget_meta_override_and_zero_disables(db, monkeypatch):
    monkeypatch.setattr(settings, "llm_budget_usd", 1.0)
    pid = await _add_project(db, meta={"llm_budget_usd": 5.0})
    await _add_call(db, pid, 2.0)
    await ledger.check_budget(pid)  # override 5.0 > 2.0
    pid0 = await _add_project(db, meta={"llm_budget_usd": 0})
    await _add_call(db, pid0, 100.0)
    await ledger.check_budget(pid0)  # 0 = выключен для проекта
    await ledger.check_budget(None)  # adhoc — бюджета нет


@pytest.mark.asyncio
async def test_unpersisted_spent_counts_toward_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "llm_budget_usd", 1.0)
    pid = await _add_project(db)
    ledger._unpersisted_spent[pid] = 1.5  # INSERT падали — консервативно
    with pytest.raises(BudgetExhausted):
        await ledger.check_budget(pid)


# ── chat(): проверка ДО платного вызова и перед retry ────────────────────


def _enable_kie(monkeypatch, retries: int = 0) -> None:
    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(settings, "tokenrouter_api_key", "")
    monkeypatch.setattr(settings, "gpt_api_key", "k")
    monkeypatch.setattr(settings, "gpt_base_url", "https://gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", "/v1/chat/completions")
    monkeypatch.setattr(settings, "gpt_api_mode", "chat")
    monkeypatch.setattr(settings, "gpt_max_retries", retries)
    monkeypatch.setattr(settings, "gpt_proxy_url", None)
    monkeypatch.setattr(settings, "gpt_structured_outputs", "off")
    monkeypatch.setattr(gpt_api, "_PROXY_LOGGED", False)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)


def _mock_httpx(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("transport", None)
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_chat_raises_before_http_when_exhausted(db, monkeypatch):
    monkeypatch.setattr(settings, "llm_budget_usd", 1.0)
    pid = await _add_project(db)
    await _add_call(db, pid, 2.0)
    _enable_kie(monkeypatch)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 40}}]})

    _mock_httpx(monkeypatch, handler)
    with use_accounting(LlmAccountingContext(project_id=pid, node_key="plan")):
        with pytest.raises(BudgetExhausted):
            await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False)
    assert hits["n"] == 0  # платного вызова не было


@pytest.mark.asyncio
async def test_chat_rechecks_budget_before_retry(db, monkeypatch):
    """Первая попытка проходит (spent<budget), её стоимость пробивает бюджет →
    retry после 500 не уходит в HTTP."""
    monkeypatch.setattr(settings, "llm_budget_usd", 0.001)
    pid = await _add_project(db)
    _enable_kie(monkeypatch, retries=3)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(500, text="boom")

    _mock_httpx(monkeypatch, handler)
    # Ошибочный вызов без usage — cost 0; подложим стоимость через
    # unpersisted, как будто первая попытка оплачена.
    orig_record = ledger.record

    async def record_and_spend(**kw):
        rid = await orig_record(**kw)
        ledger._unpersisted_spent[pid] += 0.01
        return rid

    monkeypatch.setattr(ledger, "record", record_and_spend)
    with use_accounting(LlmAccountingContext(project_id=pid, node_key="plan")):
        with pytest.raises(BudgetExhausted):
            await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False)
    assert hits["n"] == 1  # вторая попытка не ушла в сеть


# ── re-raise в контурах с широким except ─────────────────────────────────


@pytest.mark.asyncio
async def test_volume_complete_reraises_budget(monkeypatch):
    from app.services import volume_batches

    async def boom(reply_text, **kwargs):
        raise BudgetExhausted(project_id=1, spent_usd=2, budget_usd=1)

    monkeypatch.setattr(volume_batches, "volume_complete_apply_ops_reply", boom)
    with pytest.raises(BudgetExhausted):
        await gpt_api._maybe_volume_complete_chat_result(
            gpt_api.GptChatResult(text="{}", model="m"),
            prompt="p", accompanying="", input_paths=None, system=None,
            history=None, model="m", temperature=None, timeout=1.0,
            xlsx_write_contract="apply_ops", volume_complete=True,
        )


@pytest.mark.asyncio
async def test_pdf_chunks_reraise_budget(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(gpt_api, "pdf_to_text", lambda p, **kw: "text")
    monkeypatch.setattr(gpt_api, "split_pdf_text_chunks", lambda t, **kw: ["a" * 2000, "b"])

    async def boom(**kw):
        raise BudgetExhausted(project_id=1, spent_usd=2, budget_usd=1)

    monkeypatch.setattr(gpt_api, "chat", boom)
    with pytest.raises(BudgetExhausted):  # не «фрагмент пропущен»
        await gpt_api.chat_pdf_in_chunks(prompt="q", pdf_paths=[Path("x.pdf")])


# ── step_failure_policy: пауза с причиной без sleep-циклов ───────────────


@pytest.mark.asyncio
async def test_policy_pauses_with_reason(db):
    pid = await _add_project(db)
    async with db() as session:
        p = await session.get(Project, pid)
        with patch(
            "app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock
        ):
            action = await record_step_failure(
                session,
                p,
                error=BudgetExhausted(project_id=pid, spent_usd=12.5, budget_usd=10.0),
            )
        await session.commit()
    assert action == "pause_budget"
    async with db() as s:
        p = await s.get(Project, pid)
    assert p.status is ProjectStatus.paused
    pr = p.meta["pause_reason"]
    assert pr["code"] == "budget_exhausted"
    assert pr["spent_usd"] == 12.5 and pr["budget_usd"] == 10.0
    assert pr["node"] == "planning"
    fs = p.meta["step_failure"]
    assert "sleep_until" not in fs and not fs.get("total_fails")


# ── API дашборда + поднятие бюджета ──────────────────────────────────────


@pytest_asyncio.fixture
async def client(db):
    from app.web.api import create_app
    from app.web.deps import get_session

    app = create_app()

    async def _gen():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_costs_api_aggregates(client, db, monkeypatch):
    monkeypatch.setattr(settings, "llm_budget_usd", 1.0)
    pid = await _add_project(db)
    await _add_call(db, pid, 0.5, node_key="check_1", model="gpt-5.6-sol", prompt_tokens=100, completion_tokens=10)
    await _add_call(db, pid, 0.2, node_key="check_1", model="gpt-5.6-sol", result="error", unbilled=True)
    await _add_call(db, pid, 0.1, node_key="plan", model="kimi-k3", contract_rejected=True)
    await _add_call(db, None, 0.3, node_key="adhoc", model="kimi-k3")

    r = await client.get(f"/api/projects/{pid}/llm-costs")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"]["calls"] == 3 and d["total"]["cost_usd"] == pytest.approx(0.8)
    assert d["total"]["failed"] == 2  # error ∪ contract_rejected
    assert d["total"]["unbilled"] == 1
    nodes = {n["node_key"]: n for n in d["nodes"]}
    assert nodes["check_1"]["calls"] == 2 and nodes["plan"]["contract_rejected"] == 1
    assert {m["model"] for m in d["models"]} == {"gpt-5.6-sol", "kimi-k3"}
    assert d["budget"]["budget_usd"] == 1.0 and d["budget"]["exhausted"] is False

    r = await client.get("/api/llm-costs/projects")
    d = r.json()
    assert [p["project_id"] for p in d["projects"]] == [pid]
    assert d["adhoc"]["calls"] == 1 and d["adhoc"]["cost_usd"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_budget_endpoint_raises_budget_and_clears_pause(client, db):
    pid = await _add_project(
        db,
        meta={"pause_reason": {"code": "budget_exhausted", "spent_usd": 2, "budget_usd": 1}},
    )
    r = await client.post(f"/api/projects/{pid}/llm-budget", json={"budget_usd": 25})
    assert r.status_code == 200, r.text
    assert r.json()["pause_reason_cleared"] is True
    async with db() as s:
        p = await s.get(Project, pid)
    assert p.meta["llm_budget_usd"] == 25.0 and "pause_reason" not in p.meta
    r = await client.post(f"/api/projects/{pid}/llm-budget", json={"budget_usd": -1})
    assert r.status_code == 400
