"""MiniMax: картинки (image-01) и видео (Hailuo) через HTTP API.

## Почему он тут

Три причины, каждая снимает конкретную боль конвейера:

1. **Никаких файлопомоек.** `first_frame_image` и `subject_reference`
   принимают base64 data URL, то есть стартовый кадр не надо нигде
   публиковать. У Outsee Developer API этого нет — он берёт только
   http(s)-ссылку, из-за чего в проекте завёлся откат на анонимные
   хостинги (см. `outsee_http.ensure_public_image_url`).
2. **Никакого браузера.** Всё синхронным HTTP, Chrome CDP не нужен.
3. **Одна учётка на текст и медиа.** Тот же ключ работает для
   OpenAI-совместимого `/v1/chat/completions`.

## Контракты (сверено по докам и живым вызовом 2026-08-22)

Картинка — **синхронно**::

    POST /v1/image_generation
    {"model": "image-01", "prompt": …, "aspect_ratio": "9:16",
     "response_format": "base64"}
    → {"data": {"image_base64": ["…"]}, "base_resp": {"status_code": 0}}

Живая проверка: `aspect_ratio="9:16"` даёт 720×1280 — ровно вертикаль
конвейера. Берём base64, а не url: ссылка живёт 24 часа, а артефакты
проекта переживают перезапуски.

Видео — **асинхронно, три шага**::

    POST /v1/video_generation           → {"task_id": …}
    GET  /v1/query/video_generation     → status: Preparing|Queueing|
                                          Processing|Success|Fail, file_id
    GET  /v1/files/retrieve?file_id=…   → {"file": {"download_url": …}}

Опрос — раз в 10 секунд (рекомендация доков).

## Ошибки

MiniMax отвечает HTTP 200 и кладёт ошибку в `base_resp.status_code`:
1002 — лимит, 1004 — ключ, 1008 — баланс, 1026/1027 — модерация входа /
выхода. Разбираем их явно: молча принять 200 с пустыми данными значит
записать «успех» на пустом файле.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult, OutseeImageError

_DEFAULT_BASE = "https://api.minimax.io"
_POLL_INTERVAL_S = 10.0

MINIMAX_IMAGE_MODELS: tuple[str, ...] = ("image-01", "image-01-live")
MINIMAX_VIDEO_MODELS: tuple[str, ...] = (
    "MiniMax-Hailuo-2.3",
    "MiniMax-Hailuo-2.3-Fast",
    "MiniMax-Hailuo-02",
)

# Оси, которые принимает image_generation. Всё прочее приводим к 9:16 —
# конвейер вертикальный.
_IMAGE_ASPECTS = frozenset({"1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"})

# base_resp.status_code → человекочитаемая причина.
_ERROR_CODES = {
    1000: "неизвестная ошибка",
    1001: "таймаут",
    1002: "сработал лимит запросов (RPM)",
    1004: "аутентификация не прошла — проверь MINIMAX_API_KEY",
    1008: "недостаточно средств на счёте",
    1013: "внутренняя ошибка сервиса",
    1026: "входной контент отклонён модерацией",
    1027: "сгенерированный контент отклонён модерацией",
    1039: "сработал лимит токенов (TPM)",
    2013: "неверный формат входных данных",
}

# Коды, при которых повтор имеет смысл (лимиты / временные сбои).
_RETRYABLE_CODES = frozenset({1000, 1001, 1002, 1013, 1039})


class MinimaxError(OutseeImageError):
    """Ошибка MiniMax API. Наследует OutseeImageError ради общей retry-обвязки."""


@dataclass
class _Cfg:
    key: str
    base: str


def _cfg() -> _Cfg:
    from app.settings import settings

    return _Cfg(
        key=(settings.minimax_api_key or "").strip(),
        base=(settings.minimax_base_url or _DEFAULT_BASE).rstrip("/"),
    )


def minimax_key_configured() -> bool:
    return bool(_cfg().key)


def minimax_enabled() -> bool:
    """Картинки идут через MiniMax: ключ + IMAGE_PROVIDER=minimax."""
    from app.settings import settings

    return minimax_key_configured() and (settings.image_provider or "").lower() == "minimax"


def minimax_video_enabled() -> bool:
    from app.settings import settings

    return minimax_key_configured() and (getattr(settings, "video_provider", "") or "").lower() == "minimax"


def _headers() -> dict[str, str]:
    cfg = _cfg()
    if not cfg.key:
        raise MinimaxError(
            "MINIMAX_API_KEY пуст — ключ берётся на platform.minimax.io (Account Management → API Keys)",
            context={"error_kind": "no_key", "provider": "minimax"},
        )
    return {"Authorization": f"Bearer {cfg.key}", "Content-Type": "application/json"}


def _raise_on_base_resp(payload: dict[str, Any], *, where: str) -> None:
    """MiniMax кладёт ошибку в тело при HTTP 200 — не пропускаем её молча."""
    base = payload.get("base_resp") or {}
    try:
        code = int(base.get("status_code") or 0)
    except (TypeError, ValueError):
        code = 0
    if code == 0:
        return
    reason = _ERROR_CODES.get(code, base.get("status_msg") or "неизвестно")
    raise MinimaxError(
        f"MiniMax {where}: {reason} (status_code={code})",
        context={
            "provider": "minimax",
            "provider_code": code,
            "retryable": code in _RETRYABLE_CODES,
            "error_kind": "moderation" if code in (1026, 1027) else "api",
        },
    )


def normalize_image_aspect(aspect_ratio: str | None) -> str:
    a = (aspect_ratio or "9:16").replace("_", ":").strip()
    return a if a in _IMAGE_ASPECTS else "9:16"


def studio_id_to_minimax_image_slug(studio_id: str | None) -> str:
    from app.settings import settings

    default = settings.minimax_default_image_model or "image-01"
    s = (studio_id or "").strip()
    return s if s in MINIMAX_IMAGE_MODELS else default


def studio_id_to_minimax_video_slug(studio_id: str | None) -> str:
    from app.settings import settings

    default = settings.minimax_default_video_model or "MiniMax-Hailuo-2.3"
    s = (studio_id or "").strip()
    if s in MINIMAX_VIDEO_MODELS:
        return s
    lowered = {m.lower(): m for m in MINIMAX_VIDEO_MODELS}
    return lowered.get(s.lower(), default)


def file_to_data_url(path: Path) -> str:
    """Локальный файл → data URL. Публиковать кадр наружу не нужно."""
    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _as_data_url(ref: Path | str | None) -> str | None:
    if ref is None:
        return None
    if isinstance(ref, Path):
        return file_to_data_url(ref) if ref.is_file() else None
    s = str(ref).strip()
    if not s:
        return None
    if s.startswith(("http://", "https://", "data:")):
        return s
    p = Path(s)
    return file_to_data_url(p) if p.is_file() else None


# ── картинка (синхронно) ──────────────────────────────────────────────────


async def _generate_image_inner(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    aspect_ratio: str = "9:16",
    reference_image: Path | list[Path] | None = None,
    timeout: float = 300,
    gen_id: str | None = None,
    project_id: int | None = None,
    **_kwargs: Any,
) -> GenerationResult:
    cfg = _cfg()
    model = studio_id_to_minimax_image_slug(model_slug)
    body: dict[str, Any] = {
        "model": model,
        "prompt": (prompt or "")[:1500],
        "aspect_ratio": normalize_image_aspect(aspect_ratio),
        "response_format": "base64",
        "n": 1,
    }

    refs: list[Path] = []
    if isinstance(reference_image, Path):
        refs = [reference_image]
    elif isinstance(reference_image, list):
        refs = [p for p in reference_image if isinstance(p, Path) and p.is_file()]
    if refs:
        # Идентичность персонажа: реф уходит base64, без публикации наружу.
        body["subject_reference"] = [{"type": "character", "image_file": file_to_data_url(refs[0])}]

    logger.info(
        "minimax.image model={} aspect={} refs={} project={}",
        model,
        body["aspect_ratio"],
        len(refs),
        project_id,
    )

    async with httpx.AsyncClient(timeout=float(timeout)) as client:
        resp = await client.post(f"{cfg.base}/v1/image_generation", headers=_headers(), json=body)
    if resp.status_code >= 400:
        raise MinimaxError(
            f"MiniMax image HTTP {resp.status_code}: {resp.text[:300]}",
            context={"provider": "minimax", "status_code": resp.status_code, "retryable": True},
        )
    payload = resp.json()
    _raise_on_base_resp(payload, where="image")

    images = ((payload.get("data") or {}).get("image_base64")) or []
    if not images:
        raise MinimaxError(
            "MiniMax image: ответ без image_base64",
            context={"provider": "minimax", "retryable": True},
        )
    raw = base64.b64decode(images[0])
    if len(raw) < 1024:
        raise MinimaxError(
            f"MiniMax image: подозрительно маленький файл ({len(raw)} байт)",
            context={"provider": "minimax", "retryable": True},
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return GenerationResult(file_path=out_path, raw_url=None, gen_id=str(gen_id or payload.get("id") or ""))


async def generate_image(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    project_id: int | None = None,
    **kwargs: Any,
) -> GenerationResult:
    """Картинка + учёт в media_calls."""
    from app.services.media_ledger import media_call

    model = studio_id_to_minimax_image_slug(model_slug)
    async with media_call(
        "minimax", "image", model=model, units=1.0, unit="item", project_id=project_id
    ) as call:
        result = await _generate_image_inner(
            prompt, out_path, model_slug=model_slug, project_id=project_id, **kwargs
        )
        call.external_id = result.gen_id or ""
        return result


# ── видео (асинхронно: submit → poll → retrieve) ──────────────────────────


async def _submit_video(client: httpx.AsyncClient, body: dict[str, Any]) -> str:
    cfg = _cfg()
    resp = await client.post(f"{cfg.base}/v1/video_generation", headers=_headers(), json=body)
    if resp.status_code >= 400:
        raise MinimaxError(
            f"MiniMax video HTTP {resp.status_code}: {resp.text[:300]}",
            context={"provider": "minimax", "status_code": resp.status_code, "retryable": True},
        )
    payload = resp.json()
    _raise_on_base_resp(payload, where="video submit")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise MinimaxError(
            "MiniMax video: ответ без task_id",
            context={"provider": "minimax", "retryable": True},
        )
    return task_id


async def _poll_video(client: httpx.AsyncClient, task_id: str, *, timeout: float) -> str:
    """Ждём Success, возвращаем file_id."""
    cfg = _cfg()
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(
            f"{cfg.base}/v1/query/video_generation",
            headers=_headers(),
            params={"task_id": task_id},
        )
        if resp.status_code < 400:
            payload = resp.json()
            _raise_on_base_resp(payload, where="video query")
            status = str(payload.get("status") or "")
            if status != last:
                logger.info("minimax.video task={} status={}", task_id, status)
                last = status
            if status == "Success":
                file_id = str(payload.get("file_id") or "").strip()
                if not file_id:
                    raise MinimaxError(
                        "MiniMax video: Success без file_id",
                        context={"provider": "minimax", "retryable": True},
                    )
                return file_id
            if status == "Fail":
                raise MinimaxError(
                    f"MiniMax video: задача {task_id} провалилась",
                    context={"provider": "minimax", "retryable": True},
                )
        await asyncio.sleep(_POLL_INTERVAL_S)
    raise MinimaxError(
        f"MiniMax video: задача {task_id} не завершилась за {timeout:.0f} с (последний статус {last or '?'})",
        context={"provider": "minimax", "error_kind": "timeout", "retryable": True},
    )


async def _download_file(client: httpx.AsyncClient, file_id: str, out_path: Path) -> Path:
    cfg = _cfg()
    resp = await client.get(f"{cfg.base}/v1/files/retrieve", headers=_headers(), params={"file_id": file_id})
    if resp.status_code >= 400:
        raise MinimaxError(
            f"MiniMax retrieve HTTP {resp.status_code}: {resp.text[:300]}",
            context={"provider": "minimax", "retryable": True},
        )
    payload = resp.json()
    _raise_on_base_resp(payload, where="files/retrieve")
    url = str(((payload.get("file") or {}).get("download_url")) or "").strip()
    if not url:
        raise MinimaxError(
            "MiniMax retrieve: нет download_url",
            context={"provider": "minimax", "retryable": True},
        )
    got = await client.get(url, follow_redirects=True)
    if got.status_code >= 400 or len(got.content or b"") < 1024:
        raise MinimaxError(
            f"MiniMax: скачивание видео не удалось (HTTP {got.status_code}, {len(got.content or b'')} байт)",
            context={"provider": "minimax", "retryable": True},
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(got.content)
    return out_path


async def _generate_video_inner(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    duration: int | float | None = 6,
    resolution: str | None = "1080P",
    reference_image: Path | str | None = None,
    first_frame_url: str | None = None,
    last_frame_image: Path | str | None = None,
    prompt_optimizer: bool = False,
    timeout: float = 900,
    gen_id: str | None = None,
    project_id: int | None = None,
    **_kwargs: Any,
) -> GenerationResult:
    model = studio_id_to_minimax_video_slug(model_slug)
    body: dict[str, Any] = {
        "model": model,
        "prompt": (prompt or "")[:2000],
        "duration": int(duration or 6),
        # prompt_optimizer по умолчанию у API включён и переписывает промт.
        # Конвейер строит промт сам (шаг anim_pr) — чужие правки не нужны.
        "prompt_optimizer": bool(prompt_optimizer),
    }
    res = (resolution or "1080P").strip().upper()
    body["resolution"] = res if res in {"768P", "1080P"} else "1080P"

    first = _as_data_url(reference_image) or (first_frame_url or None)
    if first:
        body["first_frame_image"] = first
    last = _as_data_url(last_frame_image)
    if last:
        body["last_frame_image"] = last

    logger.info(
        "minimax.video model={} dur={} res={} first_frame={} project={}",
        model,
        body["duration"],
        body["resolution"],
        "да" if first else "нет",
        project_id,
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        task_id = await _submit_video(client, body)
        file_id = await _poll_video(client, task_id, timeout=timeout)
        await _download_file(client, file_id, out_path)
    return GenerationResult(file_path=out_path, raw_url=None, gen_id=str(gen_id or task_id))


async def generate_video(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    duration: int | float | None = 6,
    resolution: str | None = "1080P",
    project_id: int | None = None,
    **kwargs: Any,
) -> GenerationResult:
    """Видео + учёт в media_calls.

    MiniMax берёт за КЛИП, а не за секунду, и ставка меняется с разрешением
    и длительностью. Поэтому в учёт уходит `variant` — им прайс выбирает
    нужную строку (`minimax:<model>@1080P:6`). `units` остаётся в секундах:
    это по-прежнему полезная информация о том, сколько наснимали.
    """
    from app.services.media_ledger import media_call

    model = studio_id_to_minimax_video_slug(model_slug)
    dur = int(duration or 6)
    res = (resolution or "1080P").strip().upper()
    res = res if res in {"512P", "768P", "1080P"} else "1080P"
    async with media_call(
        "minimax",
        "video",
        model=model,
        units=float(dur),
        unit="second",
        variant=f"{res}:{dur}",
        project_id=project_id,
    ) as call:
        result = await _generate_video_inner(
            prompt,
            out_path,
            model_slug=model_slug,
            duration=duration,
            resolution=res,
            project_id=project_id,
            **kwargs,
        )
        call.external_id = result.gen_id or ""
        return result
