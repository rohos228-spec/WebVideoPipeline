"""Этап 3 (блок D): prompt_version_hash в записях + contract_rejected колбэком."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contracts.policy import run_with_contract
from app.models import Base, LlmCall
from app.services import gpt_api
from app.services import llm_ledger as ledger
from app.services.input_hash import prompt_version_hash
from app.settings import settings


@pytest_asyncio.fixture
async def ledger_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
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


def _enable_kie_chat(monkeypatch) -> None:
    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(settings, "gpt_api_key", "test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "https://gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", "/v1/chat/completions")
    monkeypatch.setattr(settings, "gpt_api_mode", "chat")
    monkeypatch.setattr(settings, "gpt_max_retries", 0)
    monkeypatch.setattr(settings, "gpt_proxy_url", None)
    monkeypatch.setattr(settings, "gpt_structured_outputs", "off")
    monkeypatch.setattr(gpt_api, "_PROXY_LOGGED", False)


def _mock_httpx(monkeypatch, text: str = "ответ модели достаточной длины") -> None:
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "model": "gpt-5.6-sol",
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)


# ── prompt_version_hash ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bound_prompt_hash_reaches_row(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)
    _mock_httpx(monkeypatch)
    h = prompt_version_hash("МАСТЕР-ПРОМПТ", hints=["hint"])
    with ledger.bind_prompt_hash(h):
        await gpt_api.chat(prompt="dynamic payload", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert rows[0].prompt_version_hash == h  # не хэш prompt-аргумента


@pytest.mark.asyncio
async def test_fallback_prompt_hash_from_outer_prompt(ledger_db, monkeypatch):
    _enable_kie_chat(monkeypatch)
    _mock_httpx(monkeypatch)
    await gpt_api.chat(prompt="bare prompt", auto_pack=False, volume_complete=False)
    rows = await _rows(ledger_db)
    assert rows[0].prompt_version_hash == prompt_version_hash("bare prompt")


def test_bind_prompt_hash_fallback_does_not_override():
    with ledger.bind_prompt_hash("explicit"):
        with ledger.bind_prompt_hash("fb", fallback=True):
            assert ledger.current_prompt_hash() == "explicit"
    assert ledger.current_prompt_hash() == ""


# ── contract_rejected ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejected_attempt_marked_success_not(ledger_db, monkeypatch):
    from app.contracts import APPLY_OPS

    _enable_kie_chat(monkeypatch)
    replies = iter(
        [
            "Извините, вот отчёт без JSON.",
            '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"текст"}}]}',
        ]
    )

    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "c",
                "model": "gpt-5.6-sol",
                "choices": [{"message": {"content": next(replies)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)

    async def call(feedback):
        r = await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False)
        return r.text

    res = await run_with_contract(contract=APPLY_OPS, call=call, label="t")
    assert res.attempts == 2
    rows = await _rows(ledger_db)
    assert [r.contract_rejected for r in rows] == [True, False]
    assert all(r.result == "ok" for r in rows)  # HTTP был успешен


@pytest.mark.asyncio
async def test_capture_scope_does_not_leak_after_exception():
    with pytest.raises(RuntimeError):
        with ledger.capture_attempt():
            raise RuntimeError("boom")
    assert ledger._attempt_rows.get() is None


@pytest.mark.asyncio
async def test_mark_contract_rejected_best_effort(monkeypatch):
    @asynccontextmanager
    async def broken():
        raise RuntimeError("locked")
        yield  # pragma: no cover

    monkeypatch.setattr(ledger, "session_scope", broken)
    await ledger.mark_contract_rejected([1, 2])  # не бросает
