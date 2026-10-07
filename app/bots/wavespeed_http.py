"""HTTP-клиент WaveSpeed AI (модель ElevenLabs v4 TTS).

API WaveSpeed:
  - Submit: POST {WAVESPEED_API_BASE_URL}/api/v3/elevenlabs/eleven-v4
    Headers: Authorization: Bearer {WAVESPEED_API_KEY}
    Body: {"text": str, "voice_id": str, "stability": float, "similarity": float}
    Response: {"code": 200, "data": {"id": str, ...}}
  - Poll: GET {WAVESPEED_API_BASE_URL}/api/v3/predictions/{id}/result
    Headers: Authorization: Bearer {WAVESPEED_API_KEY}
    Response: {"code": 200, "data": {"status": "completed"|"processing"|..., "outputs": [str]}}
  - Download: скачивание полученного mp3 по прямой ссылке outputs[0]
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult
from app.settings import settings

_POLL_INTERVAL_S = 2.0
_POLL_MAX_S = 300.0


class WaveSpeedError(RuntimeError):
    """Ошибка вызова WaveSpeed API."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


def wavespeed_api_key() -> str:
    return (getattr(settings, "wavespeed_api_key", "") or "").strip()


def wavespeed_api_base_url() -> str:
    base = (getattr(settings, "wavespeed_api_base_url", "") or "https://api.wavespeed.ai").strip()
    return base.rstrip("/")


def wavespeed_configured() -> bool:
    return bool(wavespeed_api_key())


def _headers() -> dict[str, str]:
    key = wavespeed_api_key()
    if not key:
        raise WaveSpeedError(
            "WaveSpeed: нет API-ключа (WAVESPEED_API_KEY)",
            context={"provider_code": 401, "error_kind": "no_key"},
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _check(payload: Any, *, http_status: int, where: str) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    code = data.get("code", http_status)
    try:
        code_i = int(code) if code is not None else http_status
    except (TypeError, ValueError):
        code_i = http_status
    if code_i in (None, 200, 0):
        return data
    msg = str(data.get("message") or data.get("msg") or data.get("error") or where)
    raise WaveSpeedError(
        f"WaveSpeed {where}: code={code_i} {msg}"[:400],
        context={"provider_code": code_i, "wavespeed_msg": msg[:200]},
    )


async def create_prediction(
    endpoint: str = "/api/v3/elevenlabs/eleven-v4",
    payload: dict[str, Any] | None = None,
    *,
    timeout_s: float = 60.0,
) -> str:
    """Создаёт предсказание в WaveSpeed; возвращает prediction_id."""
    url = f"{wavespeed_api_base_url()}{endpoint}"
    body = payload or {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, headers=_headers(), json=body)
        try:
            resp_data = r.json()
        except Exception:
            resp_data = {"message": (r.text or "")[:300], "code": r.status_code}
    except Exception as e:
        raise WaveSpeedError(
            f"WaveSpeed create POST failed: {type(e).__name__} ({e})",
            context={"provider_code": 504},
        ) from e

    data = _check(resp_data, http_status=r.status_code, where="create_prediction")
    raw_inner = data.get("data")
    inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else {}
    prediction_id = str(inner.get("id") or "").strip()
    if not prediction_id:
        raise WaveSpeedError(
            "WaveSpeed create_prediction: в ответе нет prediction_id",
            context={"provider_code": 500, "raw": str(resp_data)[:200]},
        )
    return prediction_id


async def poll_prediction(
    prediction_id: str,
    *,
    timeout_s: float = _POLL_MAX_S,
) -> dict[str, Any]:
    """Опрашивает статус предсказания до завершения или ошибки."""
    url = f"{wavespeed_api_base_url()}/api/v3/predictions/{prediction_id}/result"
    deadline = asyncio.get_running_loop().time() + max(30.0, timeout_s)
    net_fails = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                r = await client.get(url, headers=_headers())
                net_fails = 0
            except (httpx.TransportError, httpx.TimeoutException) as e:
                net_fails += 1
                if net_fails >= 8 or asyncio.get_running_loop().time() >= deadline:
                    raise WaveSpeedError(
                        f"WaveSpeed poll {prediction_id}: сеть/таймаут ({type(e).__name__})",
                        context={"provider_code": 504, "prediction_id": prediction_id},
                    ) from e
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            try:
                resp_data = r.json()
            except Exception:
                resp_data = {"message": (r.text or "")[:300], "code": r.status_code}

            data = _check(resp_data, http_status=r.status_code, where=f"poll {prediction_id}")
            raw_inner = data.get("data")
            inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else {}
            status = str(inner.get("status") or "").lower()

            if status in ("completed", "succeeded"):
                return inner
            if status in ("failed", "cancelled", "timeout", "deleted"):
                err_msg = str(inner.get("error") or inner.get("message") or f"status={status}")
                raise WaveSpeedError(
                    f"WaveSpeed prediction failed: {err_msg}",
                    context={"provider_code": 500, "prediction_id": prediction_id, "status": status},
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise WaveSpeedError(
                    f"WaveSpeed: таймаут ожидания предсказания {prediction_id}",
                    context={"provider_code": 504, "prediction_id": prediction_id, "kind": "timeout"},
                )

            await asyncio.sleep(_POLL_INTERVAL_S)


async def download(url: str, out_path: Path, max_retries: int = 4) -> Path:
    """Скачивает аудиофайл с повторами при сбоях."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code >= 400 or len(r.content or b"") < 32:
                    raise WaveSpeedError(
                        f"WaveSpeed download HTTP {r.status_code} size={len(r.content or b'')}",
                        context={"provider_code": r.status_code, "kind": "download"},
                    )
                out_path.write_bytes(r.content)
                return out_path
        except Exception as e:
            last_err = e
            logger.warning(
                "wavespeed_http: download attempt {}/{} failed for {}: {}",
                attempt + 1,
                max_retries,
                url,
                e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
    if isinstance(last_err, WaveSpeedError):
        raise last_err
    raise WaveSpeedError(
        f"WaveSpeed download failed after {max_retries} attempts: {type(last_err).__name__} ({last_err})",
        context={"kind": "download"},
    ) from last_err


async def run_generation(
    payload: dict[str, Any],
    out_path: Path,
    *,
    endpoint: str = "/api/v3/elevenlabs/eleven-v4",
    timeout_s: float = _POLL_MAX_S,
) -> GenerationResult:
    """Полный цикл: create → poll → download."""
    pred_id = await create_prediction(endpoint=endpoint, payload=payload, timeout_s=60.0)
    logger.info("wavespeed_http: prediction {} создано", pred_id)
    data = await poll_prediction(pred_id, timeout_s=timeout_s)
    outputs = data.get("outputs") or []
    if not outputs or not str(outputs[0]).startswith("http"):
        raise WaveSpeedError(
            "WaveSpeed: completed без ссылок на результат",
            context={"provider_code": 500, "prediction_id": pred_id},
        )
    output_url = str(outputs[0])
    path = await download(output_url, out_path)
    logger.info("wavespeed_http: ok {} → {} ({} bytes)", pred_id, path.name, path.stat().st_size)
    return GenerationResult(file_path=path, gen_id=pred_id, raw_url=output_url)
