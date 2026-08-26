"""Этап 3 (блок A): хук учёта в нижнем HTTP-слое gpt_api + контексты."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, LlmCall
from app.services import gpt_api
from app.services import llm_ledger as ledger
from app.services.llm_override import LlmAccountingContext, use_accounting
from app.settings import settings


@pytest_asyncio.fixture
async def ledger_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hook.db'}")
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
    yield factory
    await engine.dispose()


async def _rows(factory) -> list[LlmCall]:
    async with factory() as s:
        return list((await s.execute(select(LlmCall).order_by(LlmCall.id))).scalars())


def _enable_kie_chat(monkeypatch, *, retries: int = 0) -> None:
    """Non-stream chat/completions путь (kie) — _chat_completions_plain."""
    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(settings, "tokenrouter_api_key", "")
    monkeypatch.setattr(settings, "gpt_api_key", "test-key")
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
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)


def _ok_payload(text: str = "ответ модели достаточной длины") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "gpt-5.6-sol",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@pytest.mark.asyncio
async def test_success_writes_one_row(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)

    def handler(request):
        return httpx.Response(200, json=_ok_payload())

    _mock_httpx(monkeypatch, handler)
    result = await gpt_api.chat(prompt="q", model="gpt-5.6-sol", auto_pack=False, volume_complete=False)
    assert result.text
    rows = await _rows(ledger_db)
    assert len(rows) == 1
    row = rows[0]
    assert row.result == "ok" and row.error_kind == ""
    assert row.relay == "gw.test" and row.endpoint == "chat"
    assert row.model == "gpt-5.6-sol" and row.served_model == "gpt-5.6-sol"
    assert (row.prompt_tokens, row.completion_tokens) == (100, 20)
    assert row.cost_usd > 0 and not row.unbilled
    assert row.logical_call_id and row.response_id == "chatcmpl-1"
    # вне обвязки шага — adhoc
    assert row.project_id is None and row.node_key == "adhoc"


@pytest.mark.asyncio
async def test_retries_are_separate_rows_same_logical_id(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch, retries=2)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_ok_payload())

    _mock_httpx(monkeypatch, handler)
    await gpt_api.chat(prompt="q", model="gpt-5.6-sol", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert [r.result for r in rows] == ["error", "error", "ok"]
    assert rows[0].error_kind == "http_500" and rows[0].unbilled
    assert len({r.logical_call_id for r in rows}) == 1


@pytest.mark.asyncio
async def test_sequential_calls_get_different_logical_ids(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)

    def handler(request):
        return httpx.Response(200, json=_ok_payload())

    _mock_httpx(monkeypatch, handler)
    await gpt_api.chat(prompt="a", auto_pack=False, volume_complete=False)
    await gpt_api.chat(prompt="b", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert len(rows) == 2
    # Этап 3 [панель 3/3]: последовательные внешние операции — разные id.
    assert rows[0].logical_call_id != rows[1].logical_call_id


@pytest.mark.asyncio
async def test_nested_call_inherits_parent_logical_id(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)

    def handler(request):
        return httpx.Response(200, json=_ok_payload())

    _mock_httpx(monkeypatch, handler)
    # Вложенность: рекурсивные слои (adaptive/volume/packed) зовут тот же
    # публичный chat() внутри уже открытого скоупа.
    with ledger.logical_call_scope() as parent_id:
        await gpt_api.chat(prompt="a", auto_pack=False, volume_complete=False)
        await gpt_api.chat(prompt="b", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert [r.logical_call_id for r in rows] == [parent_id, parent_id]


@pytest.mark.asyncio
async def test_failed_call_recorded_with_error_kind(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)

    def handler(request):
        raise httpx.ConnectTimeout("no route")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(gpt_api.GptApiError):
        await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert len(rows) == 1
    assert rows[0].result == "error" and rows[0].error_kind == "timeout"
    assert rows[0].unbilled and rows[0].prompt_tokens is None


@pytest.mark.asyncio
async def test_accounting_context_reaches_row(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)

    def handler(request):
        return httpx.Response(200, json=_ok_payload())

    _mock_httpx(monkeypatch, handler)
    with use_accounting(LlmAccountingContext(project_id=42, node_key="check_2")):
        await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert rows[0].project_id == 42 and rows[0].node_key == "check_2"


@pytest.mark.asyncio
async def test_vibecode_stream_path_records(ledger_db, monkeypatch):
    """SSE-путь (_chat_completions_stream) тоже пишет строку с usage."""
    monkeypatch.setattr(settings, "text_llm_provider", "vibecode")
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibe.test/v1")
    monkeypatch.setattr(settings, "gpt_max_retries", 0)
    monkeypatch.setattr(settings, "gpt_structured_outputs", "off")

    sse = (
        'data: {"id":"c1","model":"gpt-5.6-sol","choices":[{"delta":{"content":"длинный ответ модели без обреза"},"finish_reason":null}]}\n\n'
        'data: {"id":"c1","model":"gpt-5.6-sol","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":11,"completion_tokens":7}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    _mock_httpx(monkeypatch, handler)
    # Явно GPT: дефолт vibecode теперь Claude Opus 5, а тот идёт в /v1/messages.
    await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False, model="gpt-5.6-sol")
    rows = await _rows(ledger_db)
    assert len(rows) == 1
    assert rows[0].endpoint == "chat" and rows[0].relay == "vibe.test"
    assert (rows[0].prompt_tokens, rows[0].completion_tokens) == (11, 7)


def test_logical_scope_guard_unit():
    with ledger.logical_call_scope() as a:
        with ledger.logical_call_scope() as b:
            assert b == a  # вложенный скоуп не перетирает родителя
        assert ledger.current_logical_call_id() == a
    with ledger.logical_call_scope() as c:
        assert c != a  # новый внешний скоуп — новый id
