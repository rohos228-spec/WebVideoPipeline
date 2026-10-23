"""Что поменяет правка настроек проекта: человеческие названия, было → станет.

Карточка «Изменить модель или разрешение проекта — проект #1» не говорила, что меняется,
а «В проекте #5 поменяются image_generator» показывала ключ. Здесь ключи и значения
переводятся в слова («модель картинок: GPT Image 2 → Flux 2 Pro»), а правка, где всё уже
так, как просят, распознаётся как пустая: «модель картинок уже GPT Image 2, менять нечего».
Пустое поле проекта для моделей и разрешений — значение Студии по умолчанию, но пустым
поле считается «уже так» только для просьбы «по умолчанию»: явная модель — это правка.
"""

from __future__ import annotations

from typing import Any

KEYS: tuple[str, ...] = (
    "title",
    "topic",
    "hero_mode",
    "image_generator",
    "image_resolution",
    "video_generator",
    "video_resolution",
    "aspect_ratio",
)
KEY_RU: dict[str, str] = {
    "title": "название",
    "topic": "тема",
    "hero_mode": "режим героя",
    "image_generator": "модель картинок",
    "image_resolution": "разрешение картинок",
    "video_generator": "модель видео",
    "video_resolution": "разрешение видео",
    "aspect_ratio": "формат кадра",
}
_HERO_RU = {"hero": "с героем", "no_hero": "без героя", "auto": "автоматически"}
_MEDIA_KEYS = ("image_generator", "image_resolution", "video_generator", "video_resolution", "aspect_ratio")


def _defaults() -> dict[str, str]:
    try:
        from app.services.action_layer import project_gen_settings as pgs

        return pgs.default_settings()
    except Exception:
        return {}


def _generator_label(kind: str, gen_id: str) -> str:
    try:
        from app.services import node_edit as ne
        from app.services.action_layer import project_gen_settings as pgs

        label = str(ne.model_title(pgs._catalog_model(kind, gen_id), gen_id) or "").strip()
    except Exception:
        label = ""
    if label and label != gen_id:
        return label
    try:
        from app.generation_options import IMAGE_GENERATORS_BY_ID, VIDEO_GENERATORS_BY_ID

        found = (IMAGE_GENERATORS_BY_ID if kind == "image" else VIDEO_GENERATORS_BY_ID).get(gen_id)
        if found is not None:
            return found.label.lstrip("+ ").strip()
    except Exception:
        pass
    return unknown_model_ru(gen_id)


_ID_WORDS = {"gpt": "GPT", "vip": "VIP", "hd": "HD", "ai": "AI", "sd": "SD", "xl": "XL", "pro": "Pro"}
_UNKNOWN_ID_MAX = 60


def unknown_model_ru(gen_id: str) -> str:
    """Модели нет в каталоге этой Студии: id в карточку сырым не идёт.

    ``gpt_image_2_vip`` → «GPT Image 2 VIP (нет в каталоге)». Человек видит, что модель
    у проекта стоит старая или чужая, и правка её заменит. Длинное имя режется до 60 знаков.
    """
    raw = " ".join(str(gen_id or "").split())
    if not raw:
        return "не задано"
    words = [w for w in raw.replace("-", "_").replace(" ", "_").split("_") if w]
    if not words:
        return "не задано"
    nice = []
    for word in words:
        low = word.lower()
        if low in _ID_WORDS:
            nice.append(_ID_WORDS[low])
        elif low[:-1].isdigit() and low.endswith("k"):
            nice.append(low.upper())
        elif word.islower():
            nice.append(word.capitalize())
        else:
            nice.append(word)
    name = " ".join(nice)
    if len(name) > _UNKNOWN_ID_MAX:
        # Как у остальных значений карточки: не длиннее 60 знаков.
        name = name[: _UNKNOWN_ID_MAX - 1].rstrip() + "…"
    return f"{name} (нет в каталоге)"


def value_ru(key: str, value: Any) -> str:
    """Значение настройки словами: «GPT Image 2», «2K», «9:16», «без героя»."""
    raw = " ".join(str(value if value is not None else "").split())
    if not raw:
        return "не задано"
    if key == "image_generator":
        return _generator_label("image", raw)
    if key == "video_generator":
        return _generator_label("video", raw)
    if key.endswith("_resolution"):
        return raw.upper() if raw.lower().endswith("k") else raw.lower()
    if key == "aspect_ratio":
        try:
            from app.services.action_layer import project_gen_settings as pgs

            aid = pgs.aspect_id(raw)
            return pgs.aspect_slug(aid) if aid else raw
        except Exception:
            return raw
    if key == "hero_mode":
        return _HERO_RU.get(raw, raw)
    return f"«{raw[:60]}»"


def _same(key: str, before: Any, after: Any) -> bool:
    if before is None or after is None:
        return False
    if key == "aspect_ratio":
        try:
            from app.services.action_layer import project_gen_settings as pgs

            aid = pgs.aspect_id(after)
            return aid is not None and aid == pgs.aspect_id(before)
        except Exception:
            return False
    left, right = " ".join(str(before).split()), " ".join(str(after).split())
    return left.lower() == right.lower() if key.endswith("_resolution") else left == right


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def diff(project: dict[str, Any] | None, params: dict[str, Any]) -> list[dict[str, Any]]:
    """По ключу правки: было, станет, совпадает ли.

    ``same`` — строго: пустое поле проекта и явная модель — разные вещи («закрепить GPT Image 2»
    в проекте без своей модели — правка). ``same_default`` — пустое поле считается значением
    по умолчанию: так сверяем просьбу «поставь модель по умолчанию».
    """
    project = project if isinstance(project, dict) else {}
    defaults = _defaults()
    out: list[dict[str, Any]] = []
    for key in KEYS:
        if key not in params:
            continue
        raw = project.get(key)
        after = params[key]
        media = key in _MEDIA_KEYS
        blank_before, blank_after = _blank(raw), _blank(after)
        before = defaults.get(key) if blank_before and media else raw
        effective_after = defaults.get(key) if blank_after and media else after
        # Пустое против явного — правка; оба пустые — «уже так».
        same = (blank_before and blank_after) if (blank_before or blank_after) else _same(key, raw, after)
        out.append(
            {
                "key": key,
                "before": before,
                "after": after,
                "blank_before": blank_before and media,
                "blank_after": blank_after and media,
                "same": same,
                "same_default": same or _same(key, before, effective_after),
                "default": media and _same(key, defaults.get(key), effective_after),
            }
        )
    return out


def _default_ru(key: str) -> str:
    value = _defaults().get(key)
    return f"по умолчанию ({value_ru(key, value)})" if value else "по умолчанию"


def _before_ru(item: dict[str, Any]) -> str:
    if item.get("blank_before"):
        return _default_ru(item["key"])
    return value_ru(item["key"], item["before"]) if item["before"] is not None else "не задано"


def _after_ru(item: dict[str, Any], as_default: bool) -> str:
    if item.get("blank_after") or (as_default and item.get("default")):
        return _default_ru(item["key"])
    return value_ru(item["key"], item["after"])


def _skipped(item: dict[str, Any], as_default: bool) -> bool:
    return bool(item["same_default"] if as_default else item["same"])


def change_text(items: list[dict[str, Any]], as_default: bool = False) -> str:
    """«модель картинок: Flux 2 Pro → GPT Image 2»; совпадающие ключи не пишем.

    ``as_default`` — человек просил значение по умолчанию: «→ по умолчанию (GPT Image 2)».
    """
    parts = [
        f"{KEY_RU[item['key']]}: {_before_ru(item)} → {_after_ru(item, as_default)}"
        for item in items
        if not _skipped(item, as_default)
    ]
    return "; ".join(parts)


def same_text(items: list[dict[str, Any]], as_default: bool = False) -> str:
    """«модель картинок уже GPT Image 2» — для правки, где менять нечего."""
    return ", ".join(
        f"{KEY_RU[item['key']]} уже {_before_ru(item)}" for item in items if _skipped(item, as_default)
    )


def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Оба прочтения правки: как есть и «просили по умолчанию» — выбирает чат по словам человека."""
    return {
        "unchanged": bool(items) and all(item["same"] for item in items),
        "change_ru": change_text(items),
        "same_ru": same_text(items),
        "unchanged_default": bool(items) and all(item["same_default"] for item in items),
        "change_default_ru": change_text(items, True),
        "same_default_ru": same_text(items, True),
    }


def target_text(params: dict[str, Any]) -> str:
    """Без данных проекта: «модель картинок → GPT Image 2»."""
    return "; ".join(f"{KEY_RU[key]} → {value_ru(key, params[key])}" for key in KEYS if key in params)


async def settings_diff(params: dict[str, Any]) -> dict[str, Any] | None:
    """Сверка правки с проектом: {"unchanged", "change_ru", "same_ru"}; None — проект не прочитан.

    Проект читается внутренним вызовом от имени личности задачи (``ActionContext`` берёт её
    из контекста, роль не повышается): ручка проекта отвечает под арендатором задачи, чужой
    проект — 404, и «было» чужого проекта в карточку не попадает. В режиме учётных записей
    без личности не читаем вовсе (``changed_settings_only`` в этом случае откажет сам).
    """
    if params.get("project_id") is None or not any(key in params for key in KEYS):
        return None
    from loguru import logger

    from app.orchestrator import app_ref
    from app.services.action_layer.builtin_wave5 import _get_project
    from app.services.action_layer.context import ActionContext
    from app.services.studio_auth import current_identity
    from app.settings import settings

    if settings.accounts_enabled and current_identity() is None:
        return None
    try:
        ctx = ActionContext(app=app_ref.current(), operator="system", read_only=True)
        project = await _get_project(ctx, params["project_id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings diff: project #{} not read: {}", params.get("project_id"), exc)
        return None
    return summary(diff(project, params))
