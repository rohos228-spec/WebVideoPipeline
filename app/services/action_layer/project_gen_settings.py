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


MEDIA_KINDS: dict[str, tuple[str, str]] = {
    "image": ("image_generator", "image_resolution"),
    "video": ("video_generator", "video_resolution"),
}
_KIND_RU = {"image": "Картинки", "video": "Видео"}
_COST_RU = {
    "cheaper": "дешевле",
    "same": "по той же цене",
    "costlier": "дороже",
    "unknown": "цена неизвестна",
}
# Шаги, которые берут модель картинок или видео с ноды своего типа (resolve_node_media_settings).
_MEDIA_NODE_TYPES = {"image": ("images", "hero", "items"), "video": ("videos",)}


def _catalog_model(kind: str, gen_id: str | None) -> dict | None:
    """Модель каталога нод с тем же генератором: те же цены, что у node.set_model."""
    from app.services import node_edit as ne

    field = MEDIA_KINDS[kind][0]
    for m in ne.allowed_models(kind):
        if gen_id and m.get(field) == gen_id:
            return m
    return None


def _real_resolution(kind: str, gen_id: str, res_id: Any) -> str:
    """Разрешение, с которым модель реально пойдёт (сервер клампит его по модели)."""
    from app.generation_options import clamp_image_resolution_id, effective_video_resolution_id

    if kind == "image":
        return str(clamp_image_resolution_id(gen_id, res_id) or "")
    return str(effective_video_resolution_id(gen_id, res_id) or "")


def _res_cost(kind: str, before: str, after: str) -> str:
    from app.generation_options import IMAGE_RESOLUTIONS, VIDEO_RESOLUTIONS

    order = [r.id for r in (IMAGE_RESOLUTIONS if kind == "image" else VIDEO_RESOLUTIONS)]
    if before == after:
        return "same"
    if before not in order or after not in order:
        return "unknown"
    return "costlier" if order.index(after) > order.index(before) else "cheaper"


def _worst(*costs: str) -> str:
    for cost in ("unknown", "costlier", "cheaper"):
        if cost in costs:
            return cost
    return "same"


def _media_label(kind: str, model: dict | None, gen_id: str) -> str:
    """Название модели для карточки: без сырого id, если модели нет в каталоге нод."""
    if isinstance(model, dict):
        from app.services import node_edit as ne

        return ne.model_title(model, gen_id)
    from app.services.action_layer.settings_diff import value_ru

    return value_ru("image_generator" if kind == "image" else "video_generator", gen_id)


def media_changes(current: dict, params: dict) -> list[dict]:
    """Смена модели или разрешения картинок и видео проекта: было, станет и цена.

    current - ответ GET /projects/{id}; пустое поле проекта = дефолт Studio. Цена модели - из
    того же каталога, что у смены модели ноды; разрешение выше прежнего считается дороже.
    """
    from app.services import node_edit as ne

    defaults = default_settings()
    out: list[dict] = []
    for kind, (gen_key, res_key) in MEDIA_KINDS.items():
        if gen_key not in params and res_key not in params:
            continue
        before_gen = str(current.get(gen_key) or defaults[gen_key])
        after_gen = str(params.get(gen_key) or before_gen)
        before_res = _real_resolution(kind, before_gen, current.get(res_key) or defaults[res_key])
        after_res = _real_resolution(
            kind,
            after_gen,
            str(params[res_key]).lower()
            if params.get(res_key)
            else current.get(res_key) or defaults[res_key],
        )
        if before_gen == after_gen and before_res == after_res:
            continue
        old, new = _catalog_model(kind, before_gen), _catalog_model(kind, after_gen)
        model_cost = "same" if before_gen == after_gen else ne.compare_cost(old, new)
        cost = _worst(model_cost, _res_cost(kind, before_res, after_res))
        out.append(
            {
                "kind": kind,
                "before_generator": before_gen,
                "after_generator": after_gen,
                "before_resolution": before_res,
                "after_resolution": after_res,
                "before_label": _media_label(kind, old, before_gen),
                "after_label": _media_label(kind, new, after_gen),
                "before_price_ru": ne.price_ru(old),
                "after_price_ru": ne.price_ru(new),
                "cost": cost,
                "needs_confirmation": cost not in ("cheaper", "same"),
            }
        )
    return out


def media_change_text(change: dict) -> str:
    if change["before_generator"] == change["after_generator"]:
        return (
            f"{_KIND_RU[change['kind']]} проекта: «{change['after_label']}», разрешение "
            f"{change['before_resolution'].upper()} → {change['after_resolution'].upper()} "
            f"({_COST_RU[change['cost']]})."
        )
    return (
        f"{_KIND_RU[change['kind']]} проекта: «{change['before_label']}» "
        f"{change['before_resolution'].upper()} → «{change['after_label']}» "
        f"{change['after_resolution'].upper()} ({_COST_RU[change['cost']]}; было "
        f"{change['before_price_ru']}, станет {change['after_price_ru']})."
    )


def node_overrides(meta: Any, project: dict) -> list[str]:
    """Шаги, где модель с ноды перекрывает модель проекта (то же правило, что у шагов).

    Шаги картинок, героя и предметов берут модель с ноды своего типа, видео - с ноды видео,
    если на ней явно выбрана модель (vibecode_catalog.resolve_*_generator_id).
    """
    from types import SimpleNamespace

    from app.services import node_edit as ne
    from app.services import vibecode_catalog as vc

    proj = SimpleNamespace(
        meta=meta if isinstance(meta, dict) else {},
        image_generator=project.get("image_generator"),
        video_generator=project.get("video_generator"),
    )
    notes: list[str] = []
    for kind, types in _MEDIA_NODE_TYPES.items():
        gen_key = MEDIA_KINDS[kind][0]
        want = str(project.get(gen_key) or "")
        if not want:
            continue
        for typ in types:
            node = vc.find_canvas_node(proj.meta, node_type=typ)
            if node is None:
                continue
            if kind == "image":
                got = vc.effective_image_generator_id(proj, node_type=typ)
                want_real = want
            else:
                got, want_real = vc.effective_video_generator_id(proj, node_type=typ), want
            if got == want_real:
                continue
            explicit = ne.explicit_model_id(node)
            model = ne.find_catalog_model(explicit) if explicit else ne.effective_model(node)[0]
            how = "на ней выбрана" if explicit else "по умолчанию на ней"
            notes.append(
                f"Шаг ноды «{ne.node_label(node)}» берёт модель с ноды ({how} "
                f"«{ne.model_title(model, got)}»), модель проекта на него не действует. "
                "Поменять модель этого шага можно сменой модели ноды."
            )
    return notes
