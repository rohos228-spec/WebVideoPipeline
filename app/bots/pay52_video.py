"""HTTP-клиент 52pay.com для генерации видео семейством Seedance.

Поддерживаемые модели:
  1. seedance-2.0-mini-ref (SEEDANCE_MINI_KEY):
     - prompt, seconds ("4"–"15"), aspect_ratio ("16:9"), resolution ("480p" | "720p")
     - metadata.content с ролями: first_frame, last_frame, reference_image (max 9),
       reference_video (max 3), reference_audio (max 3).
     - Аудио без картинки/видео отклоняется. Фото реального лица отклоняет.
  2. Seedance 2.0 Fast 海外 (SEEDANCE_FAST_KEY):
     - prompt (до 10 000 знаков), duration (4–15), ratio, resolution ("480p" | "720p")
     - referenceImages (до 9), referenceVideos (до 3), referenceAudios (до 3)
     - Поддержка живых лиц, плоские массивы без ролей start/end.
  3. Seedance 2.0 海外 (SEEDANCE_OVERSEAS_KEY):
     - prompt, duration (4–15), resolution, referenceImages (до 9), referenceVideos (до 3), referenceAudios (до 3)
  4. seedance-2.5 (SEEDANCE_OVERSEAS_KEY):
     - prompt, seconds (до 30), resolution ("1080p"), до 30 картинок.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult
from app.settings import settings

_POLL_INTERVAL_S = 3.0
_POLL_MAX_S = 900.0


class Pay52VideoError(RuntimeError):
    """Ошибка вызова 52pay Video API."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


def pay52_base_url() -> str:
    base = (getattr(settings, "pay52_base_url", "") or "https://api.52pay.com/v1").strip()
    return base.rstrip("/")


def pay52_video_key_for_model(model_name: str, cfg: Any | None = None) -> str:
    s = cfg or settings
    name_low = (model_name or "").lower()
    if "fast" in name_low:
        return (s.seedance_fast_key or "").strip()
    if "2.5" in name_low or "2-5" in name_low or "overseas" in name_low or "海外" in model_name:
        return (s.seedance_overseas_key or "").strip()
    if "mini" in name_low or "ref" in name_low:
        return (s.seedance_mini_key or "").strip()
    return (s.seedance_mini_key or s.seedance_fast_key or s.seedance_overseas_key or "").strip()


def pay52_configured() -> bool:
    s = settings
    return bool(
        s.seedance_mini_key
        or s.seedance_fast_key
        or s.seedance_overseas_key
        or s.pay52_gpt_key
    )


def pay52_video_configured(spec: dict[str, Any] | None = None) -> bool:
    if not spec:
        return pay52_configured()
    model = str(spec.get("model") or spec.get("id") or "")
    return bool(pay52_video_key_for_model(model))


def build_pay52_video_payload(spec: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Сборка тела запроса согласно правилам конкретной модели Seedance на 52pay."""
    model_raw = str(spec.get("model") or spec.get("id") or "seedance-2.0-mini-ref")
    prompt = str(values.get("prompt") or values.get("text") or "").strip()

    raw_dur = values.get("duration") or values.get("seconds") or 5
    try:
        dur_int = max(4, min(30, int(float(raw_dur))))
    except (ValueError, TypeError):
        dur_int = 5

    raw_ratio = str(values.get("aspect_ratio") or values.get("ratio") or "16:9").replace("_", ":")

    raw_res = str(values.get("resolution") or "720p").lower()
    if raw_res not in ("480p", "720p", "1080p", "4k"):
        raw_res = "720p"

    first_frame = values.get("first_frame_url") or values.get("first_frame")
    last_frame = values.get("last_frame_url") or values.get("last_frame")
    ref_images = values.get("reference_image_urls") or values.get("reference_images") or values.get("image_urls") or []
    if isinstance(ref_images, str):
        ref_images = [ref_images]
    ref_videos = values.get("reference_video_urls") or values.get("reference_videos") or []
    if isinstance(ref_videos, str):
        ref_videos = [ref_videos]
    ref_audios = values.get("reference_audio_urls") or values.get("reference_audios") or []
    if isinstance(ref_audios, str):
        ref_audios = [ref_audios]

    # 1. Модель seedance-2.0-mini-ref (базовый Mini)
    if "mini" in model_raw.lower() or "seedance-2.0-mini-ref" in model_raw.lower():
        content: list[dict[str, str]] = []
        img_count = 0
        if first_frame and img_count < 9:
            content.append({"role": "first_frame", "url": str(first_frame).strip()})
            img_count += 1
        if last_frame and img_count < 9:
            content.append({"role": "last_frame", "url": str(last_frame).strip()})
            img_count += 1
        for u in ref_images:
            if img_count >= 9:
                break
            su = str(u).strip()
            if su and not any(c.get("url") == su for c in content):
                content.append({"role": "reference_image", "url": su})
                img_count += 1

        vid_count = 0
        for u in ref_videos:
            if vid_count >= 3:
                break
            su = str(u).strip()
            if su:
                content.append({"role": "reference_video", "url": su})
                vid_count += 1

        aud_count = 0
        for u in ref_audios:
            if aud_count >= 3:
                break
            su = str(u).strip()
            if su:
                content.append({"role": "reference_audio", "url": su})
                aud_count += 1

        if aud_count > 0 and img_count == 0 and vid_count == 0:
            raise Pay52VideoError(
                "Seedance Mini Ref: аудио не может передаваться без изображения или видео",
                context={"provider_code": 400, "error_kind": "invalid_attachments"},
            )

        payload: dict[str, Any] = {
            "model": "seedance-2.0-mini-ref",
            "prompt": prompt,
            "seconds": str(min(15, max(4, dur_int))),
            "aspect_ratio": raw_ratio,
            "resolution": raw_res if raw_res in ("480p", "720p") else "720p",
        }
        if content:
            payload["metadata"] = {"content": content}
        return payload

    # 2. Модель Seedance 2.0 Fast 海外 (режим работы с лицами)
    if "fast" in model_raw.lower():
        all_images: list[str] = []
        if first_frame:
            all_images.append(str(first_frame).strip())
        if last_frame:
            su = str(last_frame).strip()
            if su not in all_images:
                all_images.append(su)
        for u in ref_images:
            su = str(u).strip()
            if su and su not in all_images:
                all_images.append(su)

        return {
            "model": "Seedance 2.0 Fast 海外",
            "prompt": prompt[:10000],
            "duration": min(15, max(4, dur_int)),
            "ratio": raw_ratio,
            "resolution": raw_res if raw_res in ("480p", "720p") else "720p",
            "referenceImages": all_images[:9],
            "referenceVideos": [str(u).strip() for u in ref_videos if str(u).strip()][:3],
            "referenceAudios": [str(u).strip() for u in ref_audios if str(u).strip()][:3],
        }

    # 3. seedance-2.5
    if "2.5" in model_raw or "2-5" in model_raw:
        all_images = []
        if first_frame:
            all_images.append(str(first_frame).strip())
        if last_frame:
            su = str(last_frame).strip()
            if su not in all_images:
                all_images.append(su)
        for u in ref_images:
            su = str(u).strip()
            if su and su not in all_images:
                all_images.append(su)

        return {
            "model": "seedance-2.5",
            "prompt": prompt,
            "seconds": str(min(30, max(4, dur_int))),
            "resolution": raw_res if raw_res in ("720p", "1080p") else "1080p",
            "referenceImages": all_images[:30],
        }

    # 4. Seedance 2.0 Overseas (Seedance 2.0 海外)
    all_images = []
    if first_frame:
        all_images.append(str(first_frame).strip())
    if last_frame:
        su = str(last_frame).strip()
        if su not in all_images:
            all_images.append(su)
    for u in ref_images:
        su = str(u).strip()
        if su and su not in all_images:
            all_images.append(su)

    return {
        "model": "Seedance 2.0 海外",
        "prompt": prompt,
        "duration": min(15, max(4, dur_int)),
        "resolution": raw_res if raw_res in ("480p", "720p", "1080p") else "720p",
        "referenceImages": all_images[:9],
        "referenceVideos": [str(u).strip() for u in ref_videos if str(u).strip()][:3],
        "referenceAudios": [str(u).strip() for u in ref_audios if str(u).strip()][:3],
    }


async def create_task(body: dict[str, Any], *, key: str | None = None) -> str:
    """Создаёт задачу видеогенерации через POST /v1/videos."""
    model_name = str(body.get("model") or "")
    api_key = key or pay52_video_key_for_model(model_name)
    if not api_key:
        raise Pay52VideoError(
            f"52pay: нет API-ключа для модели {model_name!r}",
            context={"provider_code": 401, "error_kind": "no_key"},
        )

    url = f"{pay52_base_url()}/videos"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=body)
        try:
            resp_data = r.json()
        except Exception:
            resp_data = {"message": (r.text or "")[:300], "code": r.status_code}
    except Exception as e:
        raise Pay52VideoError(
            f"52pay create POST failed: {type(e).__name__} ({e})",
            context={"provider_code": 504},
        ) from e

    if r.status_code >= 400 or not isinstance(resp_data, dict):
        err_msg = ""
        if isinstance(resp_data, dict):
            raw_err = resp_data.get("error")
            err_dict: dict[str, Any] = raw_err if isinstance(raw_err, dict) else resp_data
            err_msg = str(err_dict.get("message") or err_dict.get("msg") or resp_data)
        raise Pay52VideoError(
            f"52pay create failed ({r.status_code}): {err_msg[:300]}",
            context={"provider_code": r.status_code, "raw": str(resp_data)[:200]},
        )

    task_id = str(resp_data.get("task_id") or resp_data.get("id") or "").strip()
    if not task_id:
        data_block = resp_data.get("data")
        if isinstance(data_block, dict):
            task_id = str(data_block.get("task_id") or data_block.get("id") or "").strip()

    if not task_id:
        raise Pay52VideoError(
            "52pay create: в ответе нет task_id",
            context={"provider_code": 500, "raw": str(resp_data)[:200]},
        )
    return task_id


async def poll_task(
    task_id: str,
    *,
    model_name: str,
    key: str | None = None,
    timeout_s: float = _POLL_MAX_S,
) -> str:
    """Опрашивает статус задачи до завершения, возвращает URL готового видео."""
    api_key = key or pay52_video_key_for_model(model_name)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{pay52_base_url()}/videos/{task_id}"
    fallback_url = f"{pay52_base_url()}/video/generations/{task_id}"

    deadline = asyncio.get_running_loop().time() + max(60.0, timeout_s)
    net_fails = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                r = await client.get(url, headers=headers)
                net_fails = 0
            except (httpx.TransportError, httpx.TimeoutException) as e:
                net_fails += 1
                if net_fails >= 8 or asyncio.get_running_loop().time() >= deadline:
                    raise Pay52VideoError(
                        f"52pay poll {task_id}: таймаут/сбой сети ({type(e).__name__})",
                        context={"provider_code": 504, "task_id": task_id},
                    ) from e
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            try:
                data = r.json()
            except Exception:
                data = {"message": (r.text or "")[:300], "code": r.status_code}

            if not isinstance(data, dict):
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            # Извлечение статуса
            status = str(data.get("status") or "").lower()
            raw_meta = data.get("metadata")
            metadata: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}

            if status in ("completed", "success"):
                video_url = str(metadata.get("url") or data.get("url") or "").strip()
                if not video_url and isinstance(data.get("data"), dict):
                    inner_d = data["data"]
                    video_url = str(inner_d.get("url") or inner_d.get("video_url") or "").strip()
                if video_url:
                    return video_url
                # Если статус completed, но url еще не записан, подождём 1 итерацию
                await asyncio.sleep(2.0)

            if status in ("failed", "failure", "error"):
                fail_reason = str(data.get("fail_reason") or data.get("error") or data.get("message") or "Video generation failed")
                raise Pay52VideoError(
                    f"52pay генерация завершилась ошибкой: {fail_reason[:300]}",
                    context={"provider_code": 501, "task_id": task_id},
                )

            # Если основной эндпоинт вернул unknown или пустой статус, проверим fallback /video/generations/
            if status in ("unknown", ""):
                try:
                    r_fb = await client.get(fallback_url, headers=headers)
                    data_fb = r_fb.json()
                    if isinstance(data_fb, dict) and isinstance(data_fb.get("data"), dict):
                        fb_inner = data_fb["data"]
                        fb_st = str(fb_inner.get("status") or "").lower()
                        if fb_st in ("success", "completed"):
                            video_url = str(fb_inner.get("url") or fb_inner.get("video_url") or "").strip()
                            if video_url:
                                return video_url
                        elif fb_st in ("fail", "failed", "error"):
                            fail_reason = str(fb_inner.get("fail_reason") or "Video generation failed")
                            raise Pay52VideoError(
                                f"52pay генерация завершилась ошибкой: {fail_reason[:300]}",
                                context={"provider_code": 501, "task_id": task_id},
                            )
                except Exception:
                    pass

            if asyncio.get_running_loop().time() >= deadline:
                raise Pay52VideoError(
                    f"52pay poll {task_id}: превышен лимит ожидания ({timeout_s}s)",
                    context={"provider_code": 504, "task_id": task_id},
                )

            await asyncio.sleep(_POLL_INTERVAL_S)


async def run_generation(
    spec: dict[str, Any],
    payload: dict[str, Any],
    out_path: Path,
    *,
    timeout_s: float = _POLL_MAX_S,
) -> GenerationResult:
    """Полный цикл: подготовка payload, отправка, поллинг и скачивание mp4."""
    model_name = str(spec.get("model") or spec.get("id") or "")
    key = pay52_video_key_for_model(model_name)

    # Если payload ещё не сформирован под 52pay, трансформируем его
    if "metadata" not in payload and "referenceImages" not in payload and "model" not in payload:
        final_body = build_pay52_video_payload(spec, payload)
    elif payload.get("input") and isinstance(payload["input"], dict):
        final_body = build_pay52_video_payload(spec, payload["input"])
    else:
        final_body = payload

    logger.info("pay52_video: отправка задачи для модели {} (seconds/duration={})", final_body.get("model"), final_body.get("seconds") or final_body.get("duration"))
    task_id = await create_task(final_body, key=key)
    logger.info("pay52_video: задача создана (task_id={}), начинаем поллинг", task_id)

    video_url = await poll_task(task_id, model_name=str(final_body.get("model")), key=key, timeout_s=timeout_s)
    logger.info("pay52_video: видео готово, скачиваем с {}", video_url[:100])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        r = await client.get(video_url)
        if r.status_code != 200 or not r.content:
            raise Pay52VideoError(
                f"52pay: не удалось скачать готовое видео ({r.status_code})",
                context={"provider_code": r.status_code, "video_url": video_url[:150]},
            )
        out_path.write_bytes(r.content)

    logger.info("pay52_video: успешно сохранено в {} ({} bytes)", out_path.name, out_path.stat().st_size)
    return GenerationResult(file_path=out_path, raw_url=video_url, gen_id=task_id)
