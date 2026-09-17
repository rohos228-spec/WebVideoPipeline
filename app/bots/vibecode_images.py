"""Картинки через vibecode.moe (OpenAI-совместимый /v1/images/generations).

Нужен, т.к. часть image-моделей шлюза (GPT Image 2.5) не обслуживается
ни Outsee API, ни Kie. Файл скачивается с тем же Bearer-ключом
(без ключа отдаёт 401). Сверено живьём 2026-09-17.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult
from app.settings import settings

_GENERATE_PATH = "/v1/images/generations"


def vibecode_images_configured() -> bool:
    return bool((settings.vibecode_api_key or "").strip())


def _headers() -> dict[str, str]:
    key = (settings.vibecode_api_key or "").strip()
    if not key:
        raise ValueError("VIBECODE_API_KEY пуст — задай ключ vibecode.moe в .env")
    return {"Authorization": f"Bearer {key}"}


async def generate_image(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str = "gpt-image-2.5",
    size: str = "1024x1024",
    timeout: float = 300.0,
    project_id: int | None = None,
    gen_id: str | None = None,
) -> GenerationResult:
    """Одна картинка: POST generations → download URL → файл."""
    base = (settings.vibecode_base_url or "https://vibecode.moe/v1").strip().rstrip("/")
    body: dict[str, Any] = {
        "model": (model_slug or "gpt-image-2.5").strip(),
        "prompt": prompt,
        "size": size,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}{_GENERATE_PATH}", headers=_headers(), json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"vibecode images HTTP {r.status_code}: {(r.text or '')[:300]}")
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        raise RuntimeError("vibecode images: не-JSON ответ шлюза") from None
    items = payload.get("data") if isinstance(payload, dict) else None
    url = ""
    if isinstance(items, list) and items:
        first = items[0] if isinstance(items[0], dict) else {}
        url = str(first.get("url") or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"vibecode images: нет URL в ответе ({str(payload)[:200]})")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        dl = await client.get(url, headers=_headers())
    if dl.status_code >= 400 or len(dl.content or b"") < 32:
        raise RuntimeError(f"vibecode images download HTTP {dl.status_code}")
    out_path.write_bytes(dl.content)
    logger.info(
        "vibecode images: ok {} → {} ({} bytes) project={}",
        model_slug,
        out_path.name,
        len(dl.content),
        project_id,
    )
    return GenerationResult(file_path=out_path, gen_id=gen_id, raw_url=url)
