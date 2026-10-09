"""REST: генерации мультимедиа через kie.ai (универсальный каталог).

Модели/формы/валидация/цены — в app/services/kie_catalog.py.
Задания — в очередь через create_jobs (provider="kie"), результат — в
data/generations (глобальная Create-история; привязка к проектам — «В проект»).
"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from app.bots import kie_http, wavespeed_http
from app.services import kie_catalog
from app.services.create_jobs import enqueue_generation, get_job
from app.settings import settings

router = APIRouter(prefix="/kie-create", tags=["kie-create"])

_EXT_BY_RESULT = {"video": ".mp4", "image": ".png", "audio": ".mp3", "text": ".txt"}
_MEDIA_BY_RESULT = {"video": "video", "image": "image", "audio": "audio", "text": "audio"}


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    """Каталог + формы + значения по умолчанию — всё для picker-модели."""
    return {
        **kie_catalog.catalog_for_ui(),
        # Флаг готовности важен до первого вызова /generate.
        "configured": kie_http.kie_configured(),
        "wavespeed_configured": wavespeed_http.wavespeed_configured(),
    }


@router.get("/credits")
async def get_credits() -> dict[str, Any]:
    from app.services.studio_auth import current_is_admin

    if not current_is_admin():
        return {
            "configured": kie_http.kie_configured(),
            "credits": None,
            "usd": None,
        }
    credits = await kie_http.get_credits()
    return {
        "configured": kie_http.kie_configured(),
        "credits": credits,
        "usd": round(credits * kie_catalog.CREDIT_USD, 2) if credits is not None else None,
    }


@router.post("/estimate")
async def post_estimate(body: dict[str, Any]) -> dict[str, Any]:
    spec = kie_catalog.get_model(str((body or {}).get("model_id") or ""))
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown model_id")
    values = (body or {}).get("values") or {}
    return kie_catalog.estimate_credits(spec, values)


@router.post("/upload")
async def post_upload(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    """Загрузка файла → публичный URL kie (рефы image/video/audio генераций)."""
    if not kie_http.kie_configured():
        raise HTTPException(status_code=503, detail="kie API не настроен (KIE_API_KEY)")
    content = await file.read()
    if not content or len(content) < 16:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="файл > 200 МБ")
    name = Path(file.filename or "file.bin").name
    name = re.sub(r"[^\w.\-]+", "_", name)[:80] or "file.bin"
    url = await kie_http.upload_file(content, name)
    return {"url": url, "filename": name, "bytes": len(content)}


def _find_text(obj: Any) -> str:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in ("lyrics", "text", "content", "lyric") and isinstance(v, str) and v.strip():
                return v
        for v in obj.values():
            found = _find_text(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_text(it)
            if found:
                return found
    return ""


async def _ensure_kie_asset_url(val: Any, filename_prefix: str = "asset") -> Any:
    """Если значение — data: URL или локальный файл, загружает его в KIE и возвращает публичный URL."""
    if isinstance(val, list):
        res = []
        for item in val:
            res.append(await _ensure_kie_asset_url(item, filename_prefix=filename_prefix))
        return res
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s or s.startswith(("http://", "https://", "asset://")):
        return val

    # 1. Base64 data: URL
    if s.startswith("data:"):
        header, _, b64_data = s.partition(",")
        if not b64_data:
            return val
        mime = "image/png"
        if ":" in header and ";" in header:
            mime = header.split(":")[1].split(";")[0].strip()
        ext = ".png"
        if "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif "webp" in mime:
            ext = ".webp"
        elif "gif" in mime:
            ext = ".gif"
        elif "mp4" in mime:
            ext = ".mp4"
        elif "mp3" in mime or "mpeg" in mime:
            ext = ".mp3"
        elif "wav" in mime:
            ext = ".wav"
        try:
            content = base64.b64decode(b64_data)
        except Exception as e:
            logger.warning("kie_create: failed to decode base64: {}", e)
            return val
        filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}{ext}"
        try:
            uploaded_url = await kie_http.upload_file(content, filename=filename)
            logger.info("kie_create: auto-uploaded data URL to {}", uploaded_url)
            return uploaded_url
        except Exception as e:
            logger.error("kie_create: upload_file failed for data URL: {}", e)
            raise HTTPException(status_code=502, detail=f"Не удалось загрузить файл в KIE: {e}")

    # 2. Локальный путь (/api/files?path=... или путь на диске)
    local_path: Path | None = None
    if s.startswith("/api/files?path="):
        try:
            parsed = urlparse(s)
            qp = parse_qs(parsed.query).get("path")
            if qp and qp[0]:
                p = Path(qp[0])
                if p.is_file():
                    local_path = p
        except Exception:
            pass
    elif s.startswith("/api/generations/"):
        rel = s.replace("/api/generations/", "")
        p = Path("data/generations") / rel
        if p.is_file():
            local_path = p
    else:
        p = Path(s)
        if p.is_file():
            local_path = p
        else:
            p2 = Path(settings.data_dir) / s
            if p2.is_file():
                local_path = p2

    if local_path and local_path.is_file():
        content = local_path.read_bytes()
        filename = f"{filename_prefix}_{local_path.name}"
        try:
            uploaded_url = await kie_http.upload_file(content, filename=filename)
            logger.info("kie_create: auto-uploaded local file {} to {}", local_path, uploaded_url)
            return uploaded_url
        except Exception as e:
            logger.error("kie_create: upload_file failed for local file {}: {}", local_path, e)
            raise HTTPException(status_code=502, detail=f"Не удалось загрузить файл в KIE: {e}")

    return val


@router.post("/generate")
async def post_generate(body: dict[str, Any]) -> dict[str, Any]:
    model_id = str((body or {}).get("model_id") or "").strip()
    spec = kie_catalog.get_model(model_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown model_id {model_id!r}")
    values = (body or {}).get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="values must be object")

    # Автоматически загружаем data: URL и локальные файлы в KIE CDN до валидации каталога
    fields_spec = {f["name"]: f for f in (spec.get("fields") or [])}
    for k, v in list(values.items()):
        f_spec = fields_spec.get(k)
        is_file_field = (
            (f_spec and f_spec.get("kind") in kie_catalog._FILE_KINDS)
            or "image" in k.lower()
            or "frame" in k.lower()
            or "file" in k.lower()
            or "url" in k.lower()
        )
        if is_file_field and v:
            values[k] = await _ensure_kie_asset_url(v, filename_prefix=k)

    errors = kie_catalog.validate_values(spec, values)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors[:6]))
    is_wavespeed = spec.get("api") == "wavespeed"
    if is_wavespeed:
        if not wavespeed_http.wavespeed_configured():
            raise HTTPException(status_code=503, detail="WaveSpeed API не настроен (WAVESPEED_API_KEY)")
    elif not kie_http.kie_configured():
        raise HTTPException(status_code=503, detail="kie API не настроен (KIE_API_KEY)")

    payload = kie_catalog.build_payload(spec, values)
    quote = kie_catalog.estimate_credits(spec, values)
    result_kind = str(spec.get("result") or "video")
    media = _MEDIA_BY_RESULT.get(result_kind, "video")
    ext = _EXT_BY_RESULT.get(result_kind, ".mp4")
    prompt = str(values.get("prompt") or values.get("text") or "")[:2000]

    from app.db import session_scope
    from app.services import credit_ledger as cl
    from app.services.credits import price_micro
    from app.services.studio_auth import current_is_admin
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    is_admin = current_is_admin()
    cost_usd = quote.get("usd", 0.0)
    credits_num = quote.get("credits", 0.0)
    hold_amount_micro = int(round(credits_num * 1_000_000))
    if hold_amount_micro <= 0:
        hold_amount_micro = price_micro(cost_usd)
    hold_id: str | None = None

    if tenant and not is_admin:
        async with session_scope() as session:
            try:
                hold = await cl.open_hold(
                    session,
                    tenant,
                    project_id=0,
                    step_code=f"kie_{media}",
                    amount_micro=hold_amount_micro,
                )
                hold_id = hold.id
            except cl.InsufficientCredits as exc:
                raise HTTPException(
                    status_code=402,
                    detail=f"Недостаточно кредитов: {exc}",
                ) from exc

    async def run(out_path: Path):
        try:
            if is_wavespeed:
                res = await wavespeed_http.run_generation(payload, out_path)
            elif result_kind == "text":
                task_id = await kie_http.create_task(str(spec.get("api")), spec.get("endpoint"), payload)
                data = await kie_http.poll_task(str(spec.get("api")), task_id)
                text = _find_text(data) or str(data)[:4000]
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(text, encoding="utf-8")
                from app.bots.outsee import GenerationResult

                res = GenerationResult(file_path=out_path, gen_id=task_id, raw_url=None)
            else:
                res = await kie_http.run_generation(spec, payload, out_path)

            if hold_id:
                async with session_scope() as session:
                    await cl.settle_hold(
                        session,
                        hold_id,
                        cost_usd=cost_usd,
                        ref_table="create_generations",
                        ref_ids=[],
                        memo=f"{'WaveSpeed' if is_wavespeed else 'KIE'} генерация {media} ({spec['label']})",
                    )
            return res
        except Exception:
            if hold_id:
                async with session_scope() as session:
                    await cl.release_hold(session, hold_id, memo=f"Сбой {'WaveSpeed' if is_wavespeed else 'KIE'} генерации {media}")
            raise

    job_provider = "wavespeed" if is_wavespeed else "kie"
    job = await enqueue_generation(
        media=media,
        model=spec["label"],
        provider=job_provider,
        prompt=prompt,
        ext=ext,
        params={"model_id": model_id, "values": values},
        quote={"credits": quote["credits"], "usd": quote["usd"], "note": quote["note"]},
        run=run,
    )
    logger.info(
        "kie-create: generate {} ({}) → job {} (~{} кр. / ${})",
        model_id,
        spec["label"],
        job.id,
        quote["credits"],
        quote["usd"],
    )
    return {"job": job.to_dict(), "estimate": quote}


@router.get("/jobs/{job_id}")
async def kie_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown", "ok": False}
    return job.to_dict()
