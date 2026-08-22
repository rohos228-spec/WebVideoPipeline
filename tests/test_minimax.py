"""MiniMax: картинки, видео, текстовый провайдер.

Зачем провайдер заведён: он принимает стартовый кадр как base64 data URL,
поэтому отпадает вся история с публикацией кадров наружу (Yandex S3 /
анонимные файлохостинги), и не нужен Chrome. Один ключ на текст и медиа.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.bots import minimax as mm
from app.settings import Settings, settings

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 4000

_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "minimax_api_key", "test-key")
    monkeypatch.setattr(settings, "minimax_base_url", "https://api.minimax.io")
    monkeypatch.setattr(settings, "minimax_default_image_model", "image-01")
    monkeypatch.setattr(settings, "minimax_default_video_model", "MiniMax-Hailuo-2.3")


def _mock_client(routes, captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        for path, resp in routes:
            if path in str(request.url):
                return resp() if callable(resp) else resp
        return httpx.Response(404, json={"base_resp": {"status_code": 1000}})

    class _Factory:
        def __init__(self, *_a, **_k) -> None:
            self._c = _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._c.__aenter__()

        async def __aexit__(self, *a) -> None:
            await self._c.__aexit__(*a)

    return _Factory


def _ok_image() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "img-1",
            "data": {"image_base64": [base64.b64encode(_JPEG).decode()]},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )


# ── вспомогательное ───────────────────────────────────────────────────────


def test_aspect_normalised_to_vertical() -> None:
    """Конвейер вертикальный: неизвестная ось приводится к 9:16."""
    assert mm.normalize_image_aspect(None) == "9:16"
    assert mm.normalize_image_aspect("9_16") == "9:16"
    assert mm.normalize_image_aspect("42:1") == "9:16"
    assert mm.normalize_image_aspect("16:9") == "16:9"


def test_local_file_becomes_data_url(tmp_path: Path) -> None:
    """Главное свойство провайдера: реф уходит base64, без публикации наружу."""
    p = tmp_path / "frame.png"
    p.write_bytes(b"\x89PNG" + b"\x00" * 100)
    url = mm.file_to_data_url(p)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")


def test_video_slug_falls_back_to_default() -> None:
    assert mm.studio_id_to_minimax_video_slug("MiniMax-Hailuo-02") == "MiniMax-Hailuo-02"
    assert mm.studio_id_to_minimax_video_slug("minimax-hailuo-02") == "MiniMax-Hailuo-02"
    assert mm.studio_id_to_minimax_video_slug("нет-такой") == "MiniMax-Hailuo-2.3"


# ── ошибки в теле при HTTP 200 ────────────────────────────────────────────


def test_base_resp_error_is_raised() -> None:
    """MiniMax кладёт ошибку в тело при HTTP 200 — молча принимать нельзя."""
    with pytest.raises(mm.MinimaxError, match="аутентификация"):
        mm._raise_on_base_resp({"base_resp": {"status_code": 1004}}, where="image")


def test_moderation_code_marked_and_not_retryable() -> None:
    with pytest.raises(mm.MinimaxError) as ei:
        mm._raise_on_base_resp({"base_resp": {"status_code": 1027}}, where="video")
    assert ei.value.context["error_kind"] == "moderation"
    assert ei.value.context["retryable"] is False


def test_rate_limit_is_retryable() -> None:
    with pytest.raises(mm.MinimaxError) as ei:
        mm._raise_on_base_resp({"base_resp": {"status_code": 1002}}, where="image")
    assert ei.value.context["retryable"] is True


def test_zero_status_is_success() -> None:
    mm._raise_on_base_resp({"base_resp": {"status_code": 0}}, where="image")


# ── картинка ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_writes_file_and_sends_base64_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        mm.httpx, "AsyncClient", _mock_client([("/v1/image_generation", _ok_image())], captured)
    )

    out = tmp_path / "f.png"
    res = await mm.generate_image("тестовый промт", out, aspect_ratio="9:16", project_id=1)

    assert res.file_path == out
    assert out.read_bytes() == _JPEG
    body = json.loads(captured[0].content)
    assert body["model"] == "image-01"
    assert body["aspect_ratio"] == "9:16"
    # base64, а не url: ссылка MiniMax живёт 24 часа, артефакты — дольше
    assert body["response_format"] == "base64"
    assert captured[0].headers["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_image_reference_goes_as_subject_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Реф персонажа — base64 в subject_reference, никакой публикации наружу."""
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        mm.httpx, "AsyncClient", _mock_client([("/v1/image_generation", _ok_image())], captured)
    )

    ref = tmp_path / "c01.png"
    ref.write_bytes(b"\x89PNG" + b"\x00" * 200)

    await mm.generate_image("промт", tmp_path / "f.png", reference_image=ref)

    body = json.loads(captured[0].content)
    subj = body["subject_reference"][0]
    assert subj["type"] == "character"
    assert subj["image_file"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_image_empty_payload_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = httpx.Response(200, json={"data": {}, "base_resp": {"status_code": 0}})
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client([("/v1/image_generation", resp)], []))
    with pytest.raises(mm.MinimaxError, match="image_base64"):
        await mm.generate_image("промт", tmp_path / "f.png")


@pytest.mark.asyncio
async def test_image_tiny_payload_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """200 с огрызком вместо картинки — брак, а не «кадр готов»."""
    resp = httpx.Response(
        200,
        json={
            "data": {"image_base64": [base64.b64encode(b"nope").decode()]},
            "base_resp": {"status_code": 0},
        },
    )
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client([("/v1/image_generation", resp)], []))
    with pytest.raises(mm.MinimaxError, match="маленький файл"):
        await mm.generate_image("промт", tmp_path / "f.png")


# ── видео ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_submit_poll_retrieve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[httpx.Request] = []
    mp4 = b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 4000
    routes = [
        (
            "/v1/video_generation",
            httpx.Response(200, json={"task_id": "T1", "base_resp": {"status_code": 0}}),
        ),
        (
            "/v1/query/video_generation",
            httpx.Response(200, json={"status": "Success", "file_id": "F1", "base_resp": {"status_code": 0}}),
        ),
        (
            "/v1/files/retrieve",
            httpx.Response(
                200, json={"file": {"download_url": "https://cdn/x.mp4"}, "base_resp": {"status_code": 0}}
            ),
        ),
        ("cdn/x.mp4", httpx.Response(200, content=mp4)),
    ]
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client(routes, captured))

    ref = tmp_path / "start.png"
    ref.write_bytes(b"\x89PNG" + b"\x00" * 200)
    out = tmp_path / "clip.mp4"

    res = await mm.generate_video("анимация", out, duration=6, reference_image=ref, project_id=2)

    assert res.file_path == out
    assert out.read_bytes() == mp4
    body = json.loads(captured[0].content)
    # Стартовый кадр — base64, никакой заливки на хостинг
    assert body["first_frame_image"].startswith("data:image/png;base64,")
    assert body["duration"] == 6
    # Конвейер сам строит промт на шаге anim_pr — чужой оптимизатор не нужен
    assert body["prompt_optimizer"] is False


@pytest.mark.asyncio
async def test_video_reports_pricing_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MiniMax берёт за клип: в учёт уходит variant «<разрешение>:<секунды>»."""
    routes = [
        ("/v1/video_generation", httpx.Response(200, json={"task_id": "T", "base_resp": {"status_code": 0}})),
        (
            "/v1/query/video_generation",
            httpx.Response(200, json={"status": "Success", "file_id": "F", "base_resp": {"status_code": 0}}),
        ),
        (
            "/v1/files/retrieve",
            httpx.Response(
                200, json={"file": {"download_url": "https://cdn/y.mp4"}, "base_resp": {"status_code": 0}}
            ),
        ),
        ("cdn/y.mp4", httpx.Response(200, content=b"\x00" * 5000)),
    ]
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client(routes, []))

    seen: list[dict] = []

    async def fake_record(**kw):
        seen.append(kw)
        return 1

    monkeypatch.setattr("app.services.media_ledger.record", fake_record)

    await mm.generate_video("анимация", tmp_path / "c.mp4", duration=6, resolution="768P")

    assert seen[0]["variant"] == "768P:6"
    assert seen[0]["unit"] == "second"
    assert seen[0]["units"] == 6.0


@pytest.mark.asyncio
async def test_unknown_resolution_falls_back_to_1080p(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = [
        ("/v1/video_generation", httpx.Response(200, json={"task_id": "T", "base_resp": {"status_code": 0}})),
        (
            "/v1/query/video_generation",
            httpx.Response(200, json={"status": "Success", "file_id": "F", "base_resp": {"status_code": 0}}),
        ),
        (
            "/v1/files/retrieve",
            httpx.Response(
                200, json={"file": {"download_url": "https://cdn/z.mp4"}, "base_resp": {"status_code": 0}}
            ),
        ),
        ("cdn/z.mp4", httpx.Response(200, content=b"\x00" * 5000)),
    ]
    captured: list[httpx.Request] = []
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client(routes, captured))
    seen: list[dict] = []

    async def fake_record(**kw):
        seen.append(kw)
        return 1

    monkeypatch.setattr("app.services.media_ledger.record", fake_record)

    await mm.generate_video("анимация", tmp_path / "c.mp4", duration=6, resolution="4K")

    assert seen[0]["variant"] == "1080P:6"
    assert json.loads(captured[0].content)["resolution"] == "1080P"


@pytest.mark.asyncio
async def test_video_fail_status_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = [
        (
            "/v1/video_generation",
            httpx.Response(200, json={"task_id": "T1", "base_resp": {"status_code": 0}}),
        ),
        (
            "/v1/query/video_generation",
            httpx.Response(200, json={"status": "Fail", "base_resp": {"status_code": 0}}),
        ),
    ]
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client(routes, []))
    with pytest.raises(mm.MinimaxError, match="провалилась"):
        await mm.generate_video("анимация", tmp_path / "c.mp4", duration=6)


@pytest.mark.asyncio
async def test_video_submit_without_task_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = [("/v1/video_generation", httpx.Response(200, json={"base_resp": {"status_code": 0}}))]
    monkeypatch.setattr(mm.httpx, "AsyncClient", _mock_client(routes, []))
    with pytest.raises(mm.MinimaxError, match="task_id"):
        await mm.generate_video("анимация", tmp_path / "c.mp4")


# ── текстовый провайдер ───────────────────────────────────────────────────


def test_text_provider_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    s = Settings()
    assert s.resolved_text_llm_provider() == "minimax"
    assert s.text_llm_is_minimax is True
    assert s.gpt_api_effective_base_url == "https://api.minimax.io/v1"
    assert s.gpt_chat_path_effective == "/chat/completions"
    # responses-режим у MiniMax нет — только chat/completions
    assert s.gpt_api_mode_effective == "chat"
    assert s.gpt_model_effective == "MiniMax-M3"
    assert s.gpt_api_effective_key == "k"


def test_reasoning_split_always_on() -> None:
    """Без флага блок <think> приезжает прямо в content и ломает парсеры."""
    from app.services.gpt_api import _minimax_body_tweaks

    body: dict = {}
    _minimax_body_tweaks(body, False)
    assert body["reasoning_split"] is True
    assert "response_format" not in body


def test_json_request_sets_json_object() -> None:
    """Флаг ставим, но MiniMax его НЕ соблюдает — гарантия только в контрактах."""
    from app.services.gpt_api import _minimax_body_tweaks

    body: dict = {}
    _minimax_body_tweaks(body, True)
    assert body["response_format"] == {"type": "json_object"}


def test_minimax_not_in_structured_relays() -> None:
    """MiniMax не enforce'ит схему — в allowlist structured outputs ему нельзя."""
    relays = (settings.gpt_structured_relays or "").lower()
    assert "minimax" not in relays
