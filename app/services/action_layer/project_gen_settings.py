"""Настройки генерации для проектов, созданных из чата (project.create / create_batch).

Дефолты - те же, что Studio подставляет проекту без пройденного мастера
(``app.generation_options.DEFAULTS``, их читают шаги картинок и видео).
DEFAULTS["video_generator"] = "veo_3_fast" нет в списке VIDEO_GENERATORS (сервер
его не примет), поэтому видео-модель берётся из ``settings.outsee_default_video_model``
(тот же Veo 3.1 Lite, куда outsee_http отображает veo_3_fast).
"""

from __future__ import annotations

from typing import Any

FIELDS: tuple[str, ...] = (
    "image_generator",
    "aspect_ratio",
    "image_resolution",
    "video_generator",
    "video_resolution",
)
ASPECT_VAR = "ASPECT_RATIO_VIDEO"
_FALLBACK_VIDEO = "veo_3_1_lite"

ASPECT_SCHEMA = {
    "type": "string",
    "pattern": r"^\s*\d{1,2}\s*[:_xх]\s*\d{1,2}\s*$",
    "description": "Формат кадра, например 9:16 (вертикальный) или 16:9",
}


def _video_default() -> str:
    from app.generation_options import DEFAULTS, VIDEO_GENERATORS, VIDEO_GENERATORS_BY_ID
    from app.settings import settings

    gid = DEFAULTS.get("video_generator")
    if gid in VIDEO_GENERATORS_BY_ID:
        return str(gid)
    slug = str(getattr(settings, "outsee_default_video_model", "") or "").strip()
    for c in VIDEO_GENERATORS:
        if c.outsee_slug == slug:
            return c.id
    return _FALLBACK_VIDEO


def default_settings() -> dict[str, str]:
    from app.generation_options import DEFAULTS, clamp_image_resolution_id

    img = str(DEFAULTS["image_generator"])
    return {
        "image_generator": img,
        "aspect_ratio": str(DEFAULTS["aspect_ratio"]),
        "image_resolution": clamp_image_resolution_id(img, DEFAULTS["image_resolution"]),
        "video_generator": _video_default(),
        "video_resolution": str(DEFAULTS["video_resolution"]),
    }


def aspect_id(value: Any) -> str | None:
    """'9:16' / '9_16' / '9x16' -> id из ASPECT_RATIOS ('9_16'); неизвестное -> None."""
    from app.generation_options import ASPECT_RATIOS_BY_ID

    raw = "".join(str(value or "").split()).lower()
    for sep in (":", "x", "х"):
        raw = raw.replace(sep, "_")
    return raw if raw in ASPECT_RATIOS_BY_ID else None


def aspect_slug(aid: str) -> str:
    from app.generation_options import ASPECT_RATIOS_BY_ID

    return ASPECT_RATIOS_BY_ID[aid].outsee_slug


def validation_error(params: dict) -> str | None:
    """Русское сообщение, если названное значение не из списков Studio."""
    from app.generation_options import (
        ASPECT_RATIOS,
        IMAGE_GENERATORS,
        IMAGE_GENERATORS_BY_ID,
        VIDEO_GENERATORS,
        VIDEO_GENERATORS_BY_ID,
        VIDEO_RESOLUTIONS_BY_ID,
        allowed_image_resolution_ids,
    )

    if params.get("aspect_ratio") is not None and aspect_id(params["aspect_ratio"]) is None:
        allowed = ", ".join(c.outsee_slug for c in ASPECT_RATIOS)
        return f"формат кадра «{params['aspect_ratio']}» не поддерживается, доступно: {allowed}"
    img = params.get("image_generator")
    if img is not None and img not in IMAGE_GENERATORS_BY_ID:
        allowed = ", ".join(c.id for c in IMAGE_GENERATORS)
        return f"генератор картинок «{img}» неизвестен, доступно: {allowed}"
    vid = params.get("video_generator")
    if vid is not None and vid not in VIDEO_GENERATORS_BY_ID:
        allowed = ", ".join(c.id for c in VIDEO_GENERATORS)
        return f"генератор видео «{vid}» неизвестен, доступно: {allowed}"
    res = params.get("image_resolution")
    if res is not None:
        gen = img or default_settings()["image_generator"]
        allowed_res = allowed_image_resolution_ids(gen)
        if str(res).lower() not in allowed_res:
            return (
                f"разрешение картинок «{res}» недоступно для {gen}, доступно: "
                f"{', '.join(r.upper() for r in allowed_res)}"
            )
    vres = params.get("video_resolution")
    if vres is not None and str(vres).lower() not in VIDEO_RESOLUTIONS_BY_ID:
        return f"разрешение видео «{vres}» не поддерживается, доступно: {', '.join(VIDEO_RESOLUTIONS_BY_ID)}"
    return None


async def pre_valid(ctx, params):
    return validation_error(params)


def explicit_settings(params: dict) -> dict[str, str]:
    """Только названные оператором поля, в формате полей Project."""
    from app.generation_options import clamp_image_resolution_id

    out: dict[str, str] = {}
    for key in FIELDS:
        if params.get(key) is None:
            continue
        val = str(params[key]).strip()
        out[key] = (
            aspect_id(val) if key == "aspect_ratio" else (val.lower() if key.endswith("_resolution") else val)
        )
    if "image_generator" in out and "image_resolution" not in out:
        # Смена модели без названного разрешения: сервер клампит только в PATCH.
        out["image_resolution"] = clamp_image_resolution_id(
            out["image_generator"], default_settings()["image_resolution"]
        )
    return out


def new_project_settings(params: dict) -> dict[str, str]:
    """Дефолты Studio + названные оператором поля."""
    return {**default_settings(), **explicit_settings(params)}


def prompt_overrides_with_aspect(current: Any, aid: str) -> dict:
    """prompt_overrides проекта с vars.ASPECT_RATIO_VIDEO = формат кадра проекта.

    Промты картинок/анимации берут формат из этой переменной (prompt_composer),
    а шаги картинок/видео - из Project.aspect_ratio; держим их одинаковыми.
    """
    po = dict(current) if isinstance(current, dict) else {}
    vars_ = dict(po.get("vars") or {}) if isinstance(po.get("vars"), dict) else {}
    vars_[ASPECT_VAR] = aspect_slug(aid)
    po["vars"] = vars_
    return po


async def sync_prompt_aspect(ctx, pid: Any, current_overrides: Any, aid: str) -> None:
    await ctx.call(
        "PATCH",
        f"/projects/{pid}",
        json={"prompt_overrides": prompt_overrides_with_aspect(current_overrides, aid)},
    )
