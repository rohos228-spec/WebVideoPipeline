"""Этап 3 (блок B): usage суммируется на склейках, а не берётся у первой части."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import gpt_api
from app.services.gpt_api import GptChatResult, sum_usage
from app.settings import settings


def test_sum_usage_components_and_empty():
    assert sum_usage({}, None, {}) == {}
    s = sum_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        {"prompt_tokens": 3},
        {"input_tokens": 7, "output_tokens": 1},
    )
    assert s == {
        "prompt_tokens": 13,
        "completion_tokens": 5,
        "total_tokens": 15,
        "input_tokens": 7,
        "output_tokens": 1,
    }


def _enable_vibecode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "text_llm_provider", "vibecode")
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibe.test/v1")
    monkeypatch.setattr(settings, "gpt_max_retries", 0)
    monkeypatch.setattr(settings, "gpt_structured_outputs", "off")

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)


def _sse(text: str, usage: dict) -> bytes:
    head = json.dumps(
        {"id": "c", "model": "m", "choices": [{"delta": {"content": text}, "finish_reason": None}]}
    )
    tail = json.dumps(
        {"id": "c", "model": "m", "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}
    )
    return f"data: {head}\n\ndata: {tail}\n\ndata: [DONE]\n\n".encode()


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_continuation_x2_usage_is_sum_of_three(monkeypatch):
    """Сценарий спеки: ответ из 3 кусков (исходный + 2 «продолжи») → usage = сумма."""
    _enable_vibecode(monkeypatch)
    bodies = [
        _sse('{"ops": [{"a": 1}', {"prompt_tokens": 100, "completion_tokens": 10}),
        _sse(', {"a": 2}', {"prompt_tokens": 20, "completion_tokens": 5}),
        _sse(', {"a": 3}]}', {"prompt_tokens": 30, "completion_tokens": 7}),
    ]
    calls = {"n": 0}

    def handler(request):
        i = min(calls["n"], len(bodies) - 1)
        calls["n"] += 1
        return httpx.Response(200, content=bodies[i], headers={"content-type": "text/event-stream"})

    _mock_httpx(monkeypatch, handler)
    # Этап 3: склейка сохраняется (stitch), usage — сумма трёх, не первого.
    monkeypatch.setattr(gpt_api, "stitch_llm_continuation", lambda a, b: a + b)
    # Явно GPT: дефолт vibecode теперь Claude Opus 5 (/v1/messages, без continuation).
    r = await gpt_api.chat(prompt="q", auto_pack=False, volume_complete=False, model="gpt-5.6-sol")
    assert calls["n"] == 3
    assert r.finish_reason == "stream_continued"
    assert r.usage == {"prompt_tokens": 150, "completion_tokens": 22}


@pytest.mark.asyncio
async def test_volume_continue_usage_added_to_parent(monkeypatch):
    from app.services import volume_batches

    async def fake_volume(reply_text, **kwargs):
        acc = kwargs.get("usage_acc")
        assert acc is not None
        acc.update(sum_usage(acc, {"prompt_tokens": 40, "completion_tokens": 4}))
        return reply_text + " more", True

    monkeypatch.setattr(volume_batches, "volume_complete_apply_ops_reply", fake_volume)
    parent = GptChatResult(
        text='{"ops":[]}', model="m", usage={"prompt_tokens": 100, "completion_tokens": 10}
    )
    out = await gpt_api._maybe_volume_complete_chat_result(
        parent,
        prompt="p",
        accompanying="",
        input_paths=None,
        system=None,
        history=None,
        model="m",
        temperature=None,
        timeout=10.0,
        xlsx_write_contract="apply_ops",
        volume_complete=True,
    )
    assert out.finish_reason == "volume_continued"
    assert out.usage == {"prompt_tokens": 140, "completion_tokens": 14}


@pytest.mark.asyncio
async def test_volume_batches_fills_usage_acc(tmp_path, monkeypatch):
    """Сам добор дописывает usage вызовов в usage_acc (раньше выбрасывал)."""
    from app.services.volume_batches import volume_complete_apply_ops_reply

    db = tmp_path / "db_frames.json"
    uuids = [f"{i:024x}" for i in range(5)]
    db.write_text(
        json.dumps({"frames": [{"uuid": u, "number": i + 1} for i, u in enumerate(uuids)]}),
        encoding="utf-8",
    )
    partial = {
        "ops": [
            {"frame_uuid": uuids[0], "fields": {"место": "a"}},
            {"frame_uuid": uuids[1], "fields": {"место": "b"}},
        ]
    }

    async def fake_chat(**kw):
        miss = [u for u in uuids if u not in {uuids[0], uuids[1]}]
        return GptChatResult(
            text=json.dumps({"ops": [{"frame_uuid": u, "fields": {"место": "x"}} for u in miss]}),
            model="m",
            usage={"prompt_tokens": 11, "completion_tokens": 3},
        )

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    acc: dict = {}
    _, did = await volume_complete_apply_ops_reply(
        json.dumps(partial), input_paths=[db], prompt="fill", usage_acc=acc
    )
    assert did and acc == {"prompt_tokens": 11, "completion_tokens": 3}


@pytest.mark.asyncio
async def test_pdf_chunks_usage_is_sum(monkeypatch):
    monkeypatch.setattr(gpt_api, "pdf_to_text", lambda p, **kw: "text")
    monkeypatch.setattr(gpt_api, "split_pdf_text_chunks", lambda t, **kw: ["a", "b"])

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)
    n = {"i": 0}

    async def fake_chat(**kw):
        n["i"] += 1
        return GptChatResult(
            text=f"part {n['i']}",
            model="m",
            usage={"prompt_tokens": 10 * n["i"], "completion_tokens": n["i"]},
        )

    monkeypatch.setattr(gpt_api, "chat", fake_chat)
    from pathlib import Path

    r = await gpt_api.chat_pdf_in_chunks(prompt="q", pdf_paths=[Path("x.pdf")])
    assert r.finish_reason == "chunked"
    assert r.usage == {"prompt_tokens": 30, "completion_tokens": 3}
