from __future__ import annotations

import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

PINS_KEY = "canvas_pins"
HISTORY_KEY = "node_edit_history"
SERVER_OWNED_META_KEYS = (PINS_KEY, HISTORY_KEY)
HISTORY_LIMIT = 20

MODEL_FIELD = "modelId"
EFFORT_FIELD = "reasoningEffort"
PIN_NODE_FIELDS = (MODEL_FIELD, EFFORT_FIELD)
KIND_TEXT, KIND_IMAGE, KIND_VIDEO = "text", "image", "video"

TEXT_MODEL_NODE_TYPES = frozenset(
    {
        "plan",
        "script",
        "split",
        "scene_design",
        "sd_agent",
        "sd_assemble",
        "excel_gpt",
        "enrich_1",
        "enrich_2",
        "enrich_3",
        "enrich_4",
        "enrich_5",
        "image_prompts",
        "animation_prompts",
    }
)
IMAGE_MODEL_NODE_TYPES = frozenset({"images", "hero", "items"})
VIDEO_MODEL_NODE_TYPES = frozenset({"videos"})
PER_NODE_TEXT_TYPES = frozenset({"excel_gpt", "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5"})
FREE_NODE_TYPES = frozenset(
    {
        "topic",
        "storage",
        "assemble",
        "shot_menu",
        "excel_feed",
        "hitl_gate",
        "hitl_hero",
        "hitl_images",
        "hitl_videos",
        "hitl_final",
    }
)
NO_PROMPT_VARIANT_TYPES = frozenset({"scene_design", "sd_agent", "sd_assemble", "hero"})

CHECK_CFG_KEYS = ("checkMode", "checkFix", "emitKinds", "outputMode", "checkPromptSource")
RETRY_PROFILES = ("safe", "fast")
EDGE_KINDS = ("after", "pass", "fail", "gate")
SAFE_EDGE_SWITCHES = frozenset({("after", "pass"), ("after", "gate"), ("pass", "gate"), ("gate", "pass")})
PARAM_NAMES = (
    "disabled",
    "check_mode",
    "check_fix",
    "prompt_variant",
    "retry_profile",
    "branches",
    "reasoning_effort",
)
# Уровень рассуждений: в чате три значения; на ноде пишется в виде пикера холста (low/med/high).
REASONING_EFFORTS = ("low", "medium", "high")
EFFORT_TO_CANVAS = {"low": "low", "medium": "med", "high": "high"}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2, "xhigh": 3}
EFFORT_RU = {
    "low": "низкий (быстрее и дешевле)",
    "medium": "средний",
    "high": "высокий (по умолчанию)",
    "xhigh": "максимальный",
    # «Уровень рассуждений: off» модель читала как «отключён и не поддерживается» и отказывала.
    "off": "не задан (можно поставить низкий, средний или высокий)",
}
EFFORT_PROVIDERS = frozenset({"vibecode", "kie"})

KIND_RU = {KIND_TEXT: "текстовая модель", KIND_IMAGE: "модель картинок", KIND_VIDEO: "модель видео"}
TYPE_RU = {
    "plan": "Сценарий",
    "script": "Закадровый текст",
    "split": "Разбивка на блоки",
    "excel_gpt": "Работа с GPT",
    "image_prompts": "Промты картинок",
    "animation_prompts": "Промты анимации",
    "images": "Картинки",
    "videos": "Видео",
    "hero": "Персонажи",
    "items": "Предметы",
    "audio": "Озвучка",
    "music": "Музыка",
    "assemble": "Монтаж",
    "publish": "Публикация",
    "storage": "Хранилище",
    "topic": "Тема",
    "sd_agent": "Агент дизайна сцен",
    "sd_assemble": "Сборка дизайна сцен",
    "scene_design": "Дизайн сцен",
    "sfx_plan": "План звуков",
    "sfx_gen": "Звуки",
    "hitl_gate": "Проверка человеком",
}
EDGE_KIND_RU = {
    "after": "обычная связь",
    "pass": "ветка «ок»",
    "fail": "ветка «не ок»",
    "gate": "ветка «если ок»",
}
PARAM_TITLE_RU = {
    "disabled": "отключение ноды",
    "check_mode": "проверка (агент проверки)",
    "check_fix": "режим проверки «чинить»",
    "prompt_variant": "вариант промта",
    "retry_profile": "повторы при сбое (для всего проекта)",
    "branches": "ветки ок / не ок",
    "reasoning_effort": "уровень рассуждений модели",
}
RETRY_RU = {
    "safe": "обычные: 3 цикла по 3 попытки, пауза 30 минут",
    "fast": "короткие: 2 цикла по 2 попытки, пауза 3 минуты",
}


class NodeEditError(ValueError):
    def __init__(self, code: str, message: str, *, http: int = 422, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.details = details or {}

    def to_detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _graph(meta: dict | None) -> dict | None:
    raw = (meta or {}).get("canvas_graph") if isinstance(meta, dict) else None
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        return None
    return raw


def find_node(meta: dict | None, node_key: str) -> dict | None:
    cg = _graph(meta)
    if cg is None:
        return None
    for n in cg["nodes"]:
        if isinstance(n, dict) and str(n.get("id") or "") == node_key:
            return n
    return None


def require_node(meta: dict | None, node_key: str) -> dict:
    node = find_node(meta, node_key)
    if node is None:
        raise NodeEditError("node_not_found", f"Ноды «{node_key}» нет на холсте проекта.", http=404)
    return node


def node_type(node: dict) -> str:
    from app.services.excel_gpt_node import effective_node_type

    return effective_node_type(node)


def node_label(node: dict) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    label = str(data.get("label") or "").strip()
    typ = node_type(node)
    return label or TYPE_RU.get(typ, typ or str(node.get("id") or ""))


def model_kind_for_type(typ: str) -> str | None:
    if typ in TEXT_MODEL_NODE_TYPES:
        return KIND_TEXT
    if typ in IMAGE_MODEL_NODE_TYPES:
        return KIND_IMAGE
    if typ in VIDEO_MODEL_NODE_TYPES:
        return KIND_VIDEO
    return None


def is_forced_vibecode(meta: dict | None, node_key: str) -> bool:
    from app.services.node_groups import is_script_frames_qc_gpt_node

    return bool(is_script_frames_qc_gpt_node(meta or {}, node_key=node_key))


def explicit_model_id(node: dict | None) -> str | None:
    from app.services.vibecode_catalog import read_node_model_fields

    return read_node_model_fields(node)[0]


def canonical_model_id(model_id: str | None) -> str | None:
    from app.services.vibecode_catalog import find_model

    if not model_id:
        return None
    found = find_model(model_id)
    return found["id"] if found else str(model_id)


def find_catalog_model(model_id: str | None) -> dict | None:
    from app.services.vibecode_catalog import find_model

    return find_model(model_id) if model_id else None


def effective_model(node: dict) -> tuple[dict | None, bool]:
    from app.services.vibecode_catalog import default_model_id_for_node_type

    explicit = explicit_model_id(node)
    mid = explicit or default_model_id_for_node_type(str(node.get("type") or node_type(node)))
    return find_catalog_model(mid), explicit is None


def default_model_title(meta: dict | None, node_key: str) -> str:
    """Название модели, на которой нода идёт без своей модели; пусто, если ноды нет."""
    from app.services.vibecode_catalog import default_model_id_for_node_type

    node = find_node(meta, node_key)
    if node is None:
        return ""
    mid = default_model_id_for_node_type(str(node.get("type") or node_type(node)))
    return model_title(find_catalog_model(mid), mid)


def model_ru(model_id: str | None, meta: dict | None, node_key: str) -> str:
    """«GPT 5.5» для своей модели ноды; «по умолчанию (GPT 5.6 Sol)», если своей нет."""
    if model_id:
        return f"«{model_title(find_catalog_model(model_id), model_id)}»"
    name = default_model_title(meta, node_key)
    return f"по умолчанию ({name})" if name else "по умолчанию"


def allowed_models(kind: str) -> list[dict]:
    from app.services.vibecode_catalog import models_for_channel

    out = []
    for m in models_for_channel():
        if m.get("kind") != kind:
            continue
        if kind == KIND_IMAGE and not m.get("image_generator"):
            continue
        if kind == KIND_VIDEO and not m.get("video_generator"):
            continue
        out.append(m)
    return out


def resolve_model_ref(kind: str, ref: str | None) -> dict | None:
    found = find_catalog_model(ref)
    if found is not None:
        return found
    want = " ".join(str(ref or "").lower().split())
    if not want:
        return None
    for m in allowed_models(kind):
        if " ".join(str(m.get("label") or "").lower().split()) == want:
            return m
    return None


_PRICE_KEYS = {
    KIND_TEXT: ("input_usd_per_m", "output_usd_per_m"),
    KIND_IMAGE: ("usd_per_image",),
    KIND_VIDEO: ("usd_per_video",),
}


def price_points(model: dict | None) -> tuple[float, ...] | None:
    if not isinstance(model, dict):
        return None
    keys = _PRICE_KEYS.get(str(model.get("kind") or ""))
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    if not keys:
        return None
    vals = [pricing.get(k) for k in keys]
    if any(v is None for v in vals):
        return None
    try:
        return tuple(float(v) for v in vals)
    except (TypeError, ValueError):
        return None


def compare_cost(current: dict | None, new: dict | None) -> str:
    a, b = price_points(current), price_points(new)
    if a is None or b is None or len(a) != len(b):
        return "unknown"
    if a == b:
        return "same"
    if all(y <= x for x, y in zip(a, b, strict=True)):
        return "cheaper"
    return "costlier"


def price_ru(model: dict | None) -> str:
    pts = price_points(model)
    if pts is None:
        return "цена неизвестна"
    kind = str((model or {}).get("kind") or "")
    if kind == KIND_TEXT:
        return f"${pts[0]:g} за 1 млн входных токенов, ${pts[1]:g} за 1 млн выходных"
    if kind == KIND_IMAGE:
        return f"${pts[0]:g} за картинку"
    return f"${pts[0]:g} за ролик"


def model_title(model: dict | None, model_id: str | None = None) -> str:
    if isinstance(model, dict):
        return str(model.get("label") or model.get("id") or model_id or "")
    return str(model_id or "не задана")


def _first_of_type(meta: dict | None, typ: str) -> str | None:
    from app.services.vibecode_catalog import find_canvas_node

    node = find_canvas_node(meta or {}, node_type=typ)
    return str(node.get("id")) if isinstance(node, dict) else None


def model_applies(meta: dict | None, node: dict) -> tuple[bool, str]:
    key = str(node.get("id") or "")
    typ = node_type(node)
    kind = model_kind_for_type(typ)
    if kind is None:
        return False, "у этой ноды нет выбора модели"
    if kind == KIND_TEXT and is_forced_vibecode(meta, key):
        return False, (
            "нода из группы «Кадры» всегда идёт через GPT 5.6 Sol (Vibecode), модель на ноде не учитывается"
        )
    if typ in PER_NODE_TEXT_TYPES:
        return True, "модель берётся с этой ноды при её запуске"
    first = _first_of_type(meta, typ)
    if first == key:
        return True, "модель берётся с этой ноды"
    if typ in ("sd_agent", "sd_assemble"):
        return False, (
            "при общем запуске дизайна сцен модель берётся с первой ноды этого "
            "типа; с этой ноды только при точечном запуске агента"
        )
    first_node = find_node(meta, first) if first else None
    name = node_label(first_node) if first_node else str(first)
    return False, (f"шаг берёт модель с первой ноды этого типа («{name}»), эта нода модель шага не меняет")


def node_effort(node: dict) -> str:
    from app.services.vibecode_catalog import read_node_reasoning_effort

    return read_node_reasoning_effort(node) or "high"


def _effort_raw(node: dict | None) -> str | None:
    data = (node or {}).get("data") if isinstance((node or {}).get("data"), dict) else {}
    raw = str(data.get(EFFORT_FIELD) or data.get("reasoning_effort") or "").strip()
    return raw or None


def effort_applies(meta: dict | None, node: dict) -> tuple[bool, str]:
    """Доходит ли уровень рассуждений с ноды до запроса (vibecode chat или kie Responses)."""
    from app.services.gpt_api import is_anthropic_model
    from app.services.pay52_routes import resolve_text

    key = str(node.get("id") or "")
    if model_kind_for_type(node_type(node)) != KIND_TEXT:
        return False, "у этой ноды нет текстовой модели"
    if is_forced_vibecode(meta, key):
        return False, (
            "у нод группы «Кадры» уровень рассуждений задан кодом (высокий), настройка на ноде не учитывается"
        )
    applies, note = model_applies(meta, node)
    if not applies:
        return False, note
    model, _ = effective_model(node)
    if not isinstance(model, dict):
        return False, "модели ноды нет в каталоге"
    api_model = str(model.get("api_model") or model.get("id") or "")
    if str(model.get("provider") or "") not in EFFORT_PROVIDERS or is_anthropic_model(api_model):
        return False, f"модель «{model_title(model)}» не принимает уровень рассуждений с ноды"
    if resolve_text(api_model) is not None:
        return False, (
            f"запросы модели «{model_title(model)}» идут через 52pay, уровень с ноды там не используется"
        )
    return True, ""


def model_state(meta: dict | None, node_key: str) -> dict:
    node = require_node(meta, node_key)
    typ = node_type(node)
    kind = model_kind_for_type(typ)
    model, is_default = effective_model(node) if kind else (None, True)
    applies, note = model_applies(meta, node)
    return {
        "node_key": node_key,
        "node_type": typ,
        "label": node_label(node),
        "kind": kind,
        "explicit_model_id": explicit_model_id(node),
        "model_id": (model or {}).get("id"),
        "model_label": model_title(model),
        "is_default": is_default,
        "price_ru": price_ru(model) if model else "",
        "forced_vibecode": kind == KIND_TEXT and is_forced_vibecode(meta, node_key),
        "applies": applies,
        "applies_note": note,
    }


def model_options(meta: dict | None, node_key: str, image_generator: str | None = None) -> dict:
    state = project_image_model({"image_generator": image_generator}, model_state(meta, node_key))
    current = find_catalog_model(state["model_id"])
    options = []
    for m in allowed_models(state["kind"]) if state["kind"] else []:
        cost = compare_cost(current, m)
        options.append(
            {
                "id": m["id"],
                "label": m["label"],
                "price_ru": price_ru(m),
                "cost_vs_current": cost,
                "needs_confirmation": cost not in ("cheaper", "same"),
            }
        )
    return {"current": state, "options": options}


def plan_model_change(meta: dict | None, node_key: str, model_id: str | None) -> dict:
    node = require_node(meta, node_key)
    typ = node_type(node)
    kind = model_kind_for_type(typ)
    label = node_label(node)
    if kind is None:
        raise NodeEditError("no_model", f"У ноды «{label}» нет выбора модели.")
    if kind == KIND_TEXT and is_forced_vibecode(meta, node_key):
        raise NodeEditError(
            "forced_model",
            (
                f"Нода «{label}» из группы «Кадры» всегда работает на GPT 5.6 Sol через Vibecode: "
                "смена модели на ней ни на что не повлияет."
            ),
        )
    current, _ = effective_model(node)
    before_explicit = explicit_model_id(node)
    if model_id is None:
        from app.services.vibecode_catalog import default_model_id_for_node_type

        new = find_catalog_model(default_model_id_for_node_type(str(node.get("type") or typ)))
        after_explicit = None
    else:
        new = resolve_model_ref(kind, model_id)
        if new is None or new.get("kind") != kind or new["id"] not in {m["id"] for m in allowed_models(kind)}:
            names = ", ".join(m["label"] for m in allowed_models(kind)[:40])
            raise NodeEditError(
                "model_not_allowed",
                (f"Модели «{model_id}» нет в списке для этой ноды ({KIND_RU[kind]}). Можно: {names}."),
                details={"allowed": [m["id"] for m in allowed_models(kind)]},
            )
        after_explicit = new["id"]
    if canonical_model_id(before_explicit) == after_explicit:
        raise NodeEditError("same_model", f"На ноде «{label}» уже стоит «{model_title(new)}».")
    cost = compare_cost(current, new)
    applies, note = model_applies(meta, node)
    return {
        "node_key": node_key,
        "node_label": label,
        "node_type": typ,
        "kind": kind,
        "before_model_id": before_explicit,
        "after_model_id": after_explicit,
        "before_label": model_title(current),
        "after_label": model_title(new),
        "before_price_ru": price_ru(current),
        "after_price_ru": price_ru(new),
        "cost": cost,
        "needs_confirmation": cost not in ("cheaper", "same"),
        "applies": applies,
        "applies_note": note,
    }


def model_change_text(plan: dict) -> str:
    cost_ru = {
        "cheaper": "дешевле",
        "same": "по той же цене",
        "costlier": "дороже",
        "unknown": "цена неизвестна",
    }[plan["cost"]]
    head = (
        f"Нода «{plan['node_label']}»: модель «{plan['before_label']}» → "
        f"«{plan['after_label']}» ({cost_ru}; было {plan['before_price_ru']}, "
        f"станет {plan['after_price_ru']})."
    )
    tail = "" if plan["applies"] else f" Внимание: {plan['applies_note']}."
    return head + tail


def _pin(meta: dict, scope: str, key: str, field: str, value: Any, prev: Any) -> None:
    pins = meta.get(PINS_KEY) if isinstance(meta.get(PINS_KEY), dict) else {}
    pins = copy.deepcopy(pins)
    bucket = dict(pins.get(scope) or {})
    entry = dict(bucket.get(key) or {})
    if field in entry and isinstance(entry[field], dict):
        prev = entry[field].get("prev")
    if prev == value:
        entry.pop(field, None)
    else:
        entry[field] = {"value": value, "prev": prev, "at": _now()}
    if entry:
        bucket[key] = entry
    else:
        bucket.pop(key, None)
    if bucket:
        pins[scope] = bucket
    else:
        pins.pop(scope, None)
    if pins:
        meta[PINS_KEY] = pins
    else:
        meta.pop(PINS_KEY, None)


def _set_node_model(meta: dict, node_key: str, model_id: str | None) -> None:
    for n in meta["canvas_graph"]["nodes"]:
        if isinstance(n, dict) and str(n.get("id") or "") == node_key:
            data = dict(n.get("data") or {})
            data.pop("model_id", None)
            if model_id is None:
                data.pop(MODEL_FIELD, None)
            else:
                data[MODEL_FIELD] = model_id
                data["modelChannel"] = "stable"
            n["data"] = data
            return


def _push_history(meta: dict, node_key: str, entry: dict) -> None:
    hist = copy.deepcopy(meta.get(HISTORY_KEY)) if isinstance(meta.get(HISTORY_KEY), dict) else {}
    items = list(hist.get(node_key) or [])
    items.append(entry)
    hist[node_key] = items[-HISTORY_LIMIT:]
    meta[HISTORY_KEY] = hist


def last_history(meta: dict | None, node_key: str, kind: str) -> dict | None:
    hist = (meta or {}).get(HISTORY_KEY) if isinstance(meta, dict) else None
    items = (hist or {}).get(node_key) if isinstance(hist, dict) else None
    for entry in reversed(items or []):
        if isinstance(entry, dict) and entry.get("kind") == kind:
            return entry
    return None


def revert_target(meta: dict | None, node_key: str, kind: str) -> dict | None:
    """Правка вида kind, которую вернёт следующий откат: история читается как стек.

    Каждый откат (запись с revert_of) снимает со стека верхнюю правку, поэтому откаты
    подряд идут вглубь, пока есть история. Старые записи, где откат отменял прошлый
    откат, возвращают снятую правку обратно на стек.
    """
    hist = (meta or {}).get(HISTORY_KEY) if isinstance(meta, dict) else None
    items = (hist or {}).get(node_key) if isinstance(hist, dict) else None
    stack: list[dict] = []
    undone: list[tuple[dict, dict]] = []
    for entry in items or []:
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            continue
        ref = entry.get("revert_of")
        if not ref:
            stack.append(entry)
            undone.clear()
        elif stack and stack[-1].get("at") == ref:
            undone.append((entry, stack.pop()))
        elif undone and undone[-1][0].get("at") == ref:
            stack.append(undone.pop()[1])
    return stack[-1] if stack else None


def apply_model_change(
    meta: dict, node_key: str, model_id: str | None, *, actor: str = "", revert_of: str | None = None
) -> tuple[dict, dict]:
    plan = plan_model_change(meta, node_key, model_id)
    new_meta = copy.deepcopy(meta)
    before = plan["before_model_id"]
    after = plan["after_model_id"]
    _set_node_model(new_meta, node_key, after)
    _pin(new_meta, "nodes", node_key, MODEL_FIELD, after, canonical_model_id(before))
    entry = {
        "kind": "model",
        "before": {"model_id": before},
        "after": {"model_id": after},
        "at": _now(),
        "by": actor[:120],
    }
    if revert_of:
        entry["revert_of"] = revert_of
    _push_history(new_meta, node_key, entry)
    return new_meta, plan


def plan_model_revert(meta: dict | None, node_key: str) -> dict:
    node = require_node(meta, node_key)
    entry = revert_target(meta, node_key, "model")
    if entry is None:
        raise NodeEditError(
            "nothing_to_revert",
            f"У ноды «{node_label(node)}» нет сохранённой прежней модели: "
            "все смены модели из истории уже возвращены.",
        )
    current = canonical_model_id(explicit_model_id(node))
    if current != canonical_model_id((entry.get("after") or {}).get("model_id")):
        raise NodeEditError(
            "changed_since",
            (
                f"Модель ноды «{node_label(node)}» уже поменяли после последней правки: "
                "возврат перезаписал бы чужое изменение."
            ),
            http=409,
        )
    return {"entry": entry, "target_model_id": (entry.get("before") or {}).get("model_id")}


def reconcile_canvas_pins(base: dict | None, merged: dict) -> dict:
    pins = (base or {}).get(PINS_KEY) if isinstance(base, dict) else None
    if not isinstance(pins, dict) or not pins:
        return merged
    cg = merged.get("canvas_graph")
    if not isinstance(cg, dict) or not isinstance(cg.get("nodes"), list):
        return merged
    cg = copy.deepcopy(cg)
    out_pins = copy.deepcopy(pins)
    nodes = {str(n.get("id") or ""): n for n in cg["nodes"] if isinstance(n, dict)}
    for key, fields in list((out_pins.get("nodes") or {}).items()):
        node = nodes.get(key)
        if node is None or not isinstance(fields, dict):
            out_pins["nodes"].pop(key, None)
            continue
        for field, pin in list(fields.items()):
            if field not in PIN_NODE_FIELDS or not isinstance(pin, dict):
                fields.pop(field, None)
                continue
            incoming = (
                canonical_model_id(explicit_model_id(node)) if field == MODEL_FIELD else _effort_raw(node)
            )
            if incoming == pin.get("value"):
                fields.pop(field, None)
            elif incoming == pin.get("prev"):
                data = dict(node.get("data") or {})
                if field == MODEL_FIELD:
                    data.pop("model_id", None)
                    if pin.get("value") is None:
                        data.pop(MODEL_FIELD, None)
                    else:
                        data[MODEL_FIELD] = pin["value"]
                        data["modelChannel"] = "stable"
                else:
                    data.pop("reasoning_effort", None)
                    if pin.get("value") is None:
                        data.pop(EFFORT_FIELD, None)
                    else:
                        data[EFFORT_FIELD] = pin["value"]
                node["data"] = data
            else:
                fields.pop(field, None)
        if not fields:
            out_pins["nodes"].pop(key, None)
    edges = {str(e.get("id") or ""): e for e in cg.get("edges") or [] if isinstance(e, dict)}
    for eid, fields in list((out_pins.get("edges") or {}).items()):
        edge = edges.get(eid)
        pin = (fields or {}).get("kind") if isinstance(fields, dict) else None
        if edge is None or not isinstance(pin, dict):
            out_pins["edges"].pop(eid, None)
            continue
        incoming = _edge_kind(edge)
        if incoming == pin.get("value"):
            out_pins["edges"].pop(eid, None)
        elif incoming == pin.get("prev"):
            data = dict(edge.get("data") or {})
            data["kind"] = pin["value"]
            edge["data"] = data
            edge.pop("kind", None)
        else:
            out_pins["edges"].pop(eid, None)
    for scope in ("nodes", "edges"):
        if scope in out_pins and not out_pins[scope]:
            out_pins.pop(scope)
    out = dict(merged)
    out["canvas_graph"] = cg
    if out_pins:
        out[PINS_KEY] = out_pins
    else:
        out.pop(PINS_KEY, None)
    return out


def _edge_kind(edge: dict) -> str:
    from app.services.gpt_operator import edge_kind_of

    return edge_kind_of(edge)


def _excel_cfg(meta: dict | None, node_key: str) -> dict:
    cfgs = (meta or {}).get("excel_gpt_nodes") if isinstance(meta, dict) else None
    cfg = cfgs.get(node_key) if isinstance(cfgs, dict) else None
    return dict(cfg) if isinstance(cfg, dict) else {}


def _operator_cfg(meta: dict | None, node_key: str) -> dict:
    from app.services.gpt_operator import operator_config

    return operator_config(SimpleNamespace(meta=meta or {}), node_key)


def _effective_retry_profile(meta: dict | None) -> str:
    from app.services.step_failure_policy import wait_profile

    return wait_profile(SimpleNamespace(meta=meta or {})).name


def skipped_keys(meta: dict | None, disabled: list[str] | None = None) -> set[str]:
    from app.services.disabled_nodes import node_type_from_key

    keys = [str(x) for x in (disabled if disabled is not None else (meta or {}).get("disabled_nodes") or [])]
    types = {t for t in (node_type_from_key(k) for k in keys) if t}
    out = set(keys)
    for n in (_graph(meta) or {}).get("nodes") or []:
        if isinstance(n, dict) and str(n.get("type") or "") in types:
            out.add(str(n.get("id") or ""))
    return out


def _prompt_step(typ: str) -> str | None:
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services.prompt_library import STEP_FOLDERS

    if typ in NO_PROMPT_VARIANT_TYPES:
        return None
    step = NODE_TYPE_TO_STEP_CODE.get(typ)
    return step if step in STEP_FOLDERS else None


def resolve_prompt(state: dict, node: dict) -> dict | None:
    from app.services import prompt_library as pl

    typ = node_type(node)
    step = _prompt_step(typ)
    if step is None:
        return None
    key = str(node.get("id") or "")
    excel = pl.is_excel_gpt_prompt_step(step)
    try:
        name, source = pl.resolve_project_prompt_with_source(
            state.get("prompt_overrides") or {},
            step,
            meta=state.get("meta") or {},
            node_key=key if excel else None,
            slot_id="main" if excel else None,
        )
    except Exception:
        return {"step": step, "variant": None, "source": "error"}
    return {"step": step, "variant": name, "source": source}


def _variant_exists(step: str, variant: str) -> bool:
    from app.services import prompt_library as pl

    if not variant or not pl.is_valid_prompt_name(variant):
        return False
    if pl.is_excel_gpt_prompt_step(step):
        return pl.excel_gpt_prompt_exists(variant)
    return pl.prompt_path(step, variant).is_file()


def read_params(state: dict, node_key: str) -> dict:
    meta = state.get("meta") or {}
    node = require_node(meta, node_key)
    typ = node_type(node)
    out: dict[str, Any] = {
        "disabled": node_key in skipped_keys(meta),
        "retry_profile": _effective_retry_profile(meta),
    }
    if typ == "excel_gpt":
        cfg = _operator_cfg(meta, node_key)
        out.update(
            check_mode=bool(cfg.get("checkMode")), check_fix=bool(cfg.get("checkFix")), role=cfg.get("role")
        )
    if model_kind_for_type(typ) == KIND_TEXT:
        out["reasoning_effort"] = node_effort(node)
    prompt = resolve_prompt(state, node)
    if prompt is not None:
        out["prompt_variant"] = prompt.get("variant")
        out["prompt_source"] = prompt.get("source")
    out["branches"] = {
        str(e.get("id")): _edge_kind(e)
        for e in (_graph(meta) or {}).get("edges") or []
        if isinstance(e, dict) and str(e.get("source") or "") == node_key and e.get("id")
    }
    return out


def _raw(meta: dict, node_key: str, param: str, branches: list[str] | None = None) -> Any:
    if param == "disabled":
        return [str(x) for x in (meta.get("disabled_nodes") or [])]
    if param in ("check_mode", "check_fix"):
        cfg = _excel_cfg(meta, node_key)
        return {k: copy.deepcopy(cfg.get(k)) for k in CHECK_CFG_KEYS}
    if param == "prompt_variant":
        slots = meta.get("prompt_slot_variants") if isinstance(meta.get("prompt_slot_variants"), dict) else {}
        node_slots = slots.get(node_key) if isinstance(slots.get(node_key), dict) else {}
        return node_slots.get("main")
    if param == "retry_profile":
        return meta.get("step_failure_profile")
    if param == "reasoning_effort":
        return _effort_raw(find_node(meta, node_key))
    if param == "branches":
        want = set(branches or [])
        return {
            str(e.get("id")): _edge_kind(e)
            for e in (_graph(meta) or {}).get("edges") or []
            if isinstance(e, dict) and str(e.get("id") or "") in want
        }
    raise KeyError(param)


def raw_param_name(param: str) -> str:
    """Ключ, под которым настройка лежит в истории: проверка и «чинить» хранятся вместе."""
    return "check_mode" if param == "check_fix" else param


def raw_params(meta: dict, node_key: str, params: list[str], branches: list[str] | None = None) -> dict:
    """Сырые значения настроек ноды в том виде, в каком их пишет история правок."""
    return {param: _raw(meta, node_key, param, branches) for param in params}


def params_entry_restores(entry: dict | None, before: dict | None) -> bool:
    """Откат записи entry вернёт ровно значения before (все её настройки есть в before)."""
    if not isinstance(entry, dict) or not isinstance(before, dict):
        return False
    for param, value in (entry.get("before") or {}).items():
        if param not in before:
            return False
        if param == "branches":
            have = before.get("branches") or {}
            if any(have.get(eid) != kind for eid, kind in (value or {}).items()):
                return False
        elif before[param] != value:
            return False
    return True


def _write_raw(meta: dict, node_key: str, param: str, value: Any) -> None:
    if param == "disabled":
        meta["disabled_nodes"] = list(value or [])
    elif param in ("check_mode", "check_fix"):
        cfgs = (
            copy.deepcopy(meta.get("excel_gpt_nodes"))
            if isinstance(meta.get("excel_gpt_nodes"), dict)
            else {}
        )
        cfg = dict(cfgs.get(node_key) or {})
        for k in CHECK_CFG_KEYS:
            v = (value or {}).get(k)
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = copy.deepcopy(v)
        cfgs[node_key] = cfg
        meta["excel_gpt_nodes"] = cfgs
    elif param == "prompt_variant":
        slots = (
            copy.deepcopy(meta.get("prompt_slot_variants"))
            if isinstance(meta.get("prompt_slot_variants"), dict)
            else {}
        )
        node_slots = dict(slots.get(node_key) or {})
        if value is None:
            node_slots.pop("main", None)
        else:
            node_slots["main"] = value
        if node_slots:
            slots[node_key] = node_slots
        else:
            slots.pop(node_key, None)
        meta["prompt_slot_variants"] = slots
    elif param == "retry_profile":
        if value is None:
            meta.pop("step_failure_profile", None)
        else:
            meta["step_failure_profile"] = value
    elif param == "reasoning_effort":
        for n in meta["canvas_graph"]["nodes"]:
            if isinstance(n, dict) and str(n.get("id") or "") == node_key:
                prev = _effort_raw(n)
                data = dict(n.get("data") or {})
                data.pop("reasoning_effort", None)
                if value is None:
                    data.pop(EFFORT_FIELD, None)
                else:
                    data[EFFORT_FIELD] = value
                n["data"] = data
                _pin(meta, "nodes", node_key, EFFORT_FIELD, value, prev)
                return
    elif param == "branches":
        cg = meta["canvas_graph"]
        edges = []
        for e in cg.get("edges") or []:
            eid = str(e.get("id") or "") if isinstance(e, dict) else ""
            if eid in (value or {}):
                prev = _edge_kind(e)
                data = dict(e.get("data") or {})
                data["kind"] = value[eid]
                e = {**e, "data": data}
                e.pop("kind", None)
                _pin(meta, "edges", eid, "kind", value[eid], prev)
            edges.append(e)
        meta["canvas_graph"] = {**cg, "edges": edges}


def _check_cfg_after(meta: dict, node_key: str, check_mode: bool | None, check_fix: bool | None) -> dict:
    cfg = _excel_cfg(meta, node_key)
    if check_mode is not None:
        cfg["checkMode"] = bool(check_mode)
        if check_mode:
            if not (isinstance(cfg.get("emitKinds"), list) and cfg.get("emitKinds")):
                cfg["emitKinds"] = ["inputs", "reply_txt"]
            if not str(cfg.get("outputMode") or "").strip():
                cfg["outputMode"] = "text"
            cfg.setdefault("checkFix", True)
            cfg.setdefault("checkPromptSource", "upstream")
    if check_fix is not None:
        cfg["checkFix"] = bool(check_fix)
    return {k: copy.deepcopy(cfg.get(k)) for k in CHECK_CFG_KEYS}


def _onoff(v: Any) -> str:
    return "включена" if v else "выключена"


def plan_params_change(state: dict, node_key: str, changes: dict) -> dict:
    meta = state.get("meta") or {}
    node = require_node(meta, node_key)
    typ = node_type(node)
    label = node_label(node)
    unknown = sorted(set(changes) - set(PARAM_NAMES))
    if unknown:
        raise NodeEditError("bad_param", f"Неизвестные параметры: {', '.join(unknown)}.")
    if not any(changes.get(p) is not None for p in PARAM_NAMES):
        raise NodeEditError("no_changes", "Не указано, что менять.")
    cur = read_params(state, node_key)
    items: list[dict] = []
    new_meta = copy.deepcopy(meta)
    raw_before: dict[str, Any] = {}
    raw_after: dict[str, Any] = {}

    def add(param: str, before: str, after: str, costlier: bool, note: str = "") -> None:
        items.append(
            {
                "param": param,
                "title_ru": PARAM_TITLE_RU[param],
                "before_ru": before,
                "after_ru": after,
                "needs_confirmation": costlier,
                "note_ru": note,
            }
        )

    if changes.get("disabled") is not None:
        want = bool(changes["disabled"])
        keys = [str(x) for x in (meta.get("disabled_nodes") or [])]
        after_keys = [k for k in keys if k != node_key] + ([node_key] if want else [])
        before_skip = skipped_keys(meta, keys)
        after_skip = skipped_keys(meta, after_keys)
        if (node_key in before_skip) == want:
            raise NodeEditError("same_value", f"Нода «{label}» уже {'отключена' if want else 'включена'}.")
        if (node_key in after_skip) != want:
            raise NodeEditError(
                "no_effect",
                (
                    f"Нода «{label}» останется отключённой: отключение записано по типу ноды "
                    "другой записью, снимите его сначала там."
                ),
            )
        extra = sorted((after_skip - before_skip) - {node_key}) if want else []
        note = ""
        costly = False
        if want and extra:
            costly = True
            names = ", ".join(node_label(find_node(meta, k) or {"id": k}) for k in extra[:10])
            note = f"вместе с ней будут пропускаться ноды того же типа: {names}"
        if not want and typ not in FREE_NODE_TYPES:
            costly = True
            note = "нода снова будет запускаться, это платные вызовы"
        add(
            "disabled",
            "отключена" if not want else "работает",
            "отключена" if want else "работает",
            costly,
            note,
        )
        raw_before["disabled"] = keys
        raw_after["disabled"] = after_keys
        _write_raw(new_meta, node_key, "disabled", after_keys)

    if changes.get("check_mode") is not None or changes.get("check_fix") is not None:
        if typ != "excel_gpt":
            raise NodeEditError(
                "not_applicable",
                (
                    f"Проверка (агент проверки) настраивается только у нод «Работа с GPT»; "
                    f"«{label}» — другая нода."
                ),
            )
        before_raw = _raw(meta, node_key, "check_mode")
        after_raw = _check_cfg_after(meta, node_key, changes.get("check_mode"), changes.get("check_fix"))
        n_items = len(items)
        if changes.get("check_mode") is not None and bool(changes["check_mode"]) != cur.get("check_mode"):
            on = bool(changes["check_mode"])
            add(
                "check_mode",
                _onoff(cur.get("check_mode")),
                _onoff(on),
                on,
                "проверка добавляет платные вызовы модели при каждом запуске" if on else "",
            )
        if changes.get("check_fix") is not None and bool(changes["check_fix"]) != cur.get("check_fix"):
            on = bool(changes["check_fix"])
            add(
                "check_fix",
                "чинить" if cur.get("check_fix") else "только отчёт",
                "чинить" if on else "только отчёт",
                on,
                "в режиме «чинить» проверка правит данные и может вызывать модель повторно" if on else "",
            )
        if len(items) > n_items:
            raw_before["check_mode"] = before_raw
            raw_after["check_mode"] = after_raw
            _write_raw(new_meta, node_key, "check_mode", after_raw)

    if changes.get("prompt_variant") is not None:
        variant = str(changes["prompt_variant"]).strip()
        from app.services import prompt_library as pl

        step = _prompt_step(typ)
        if step is None:
            raise NodeEditError("not_applicable", f"У ноды «{label}» нет выбора варианта промта по слоту.")
        variant = pl._clean_variant_name(variant)
        if not _variant_exists(step, variant):
            raise NodeEditError("variant_not_found", f"Варианта промта «{variant}» нет для шага этой ноды.")
        trial = copy.deepcopy(new_meta)
        _write_raw(trial, node_key, "prompt_variant", variant)
        got = resolve_prompt({**state, "meta": trial}, node) or {}
        if got.get("variant") != variant:
            raise NodeEditError(
                "no_effect",
                (
                    f"Вариант «{variant}» не будет использован: у шага выбран другой источник "
                    f"промта ({got.get('source') or 'неизвестно'}). Выбор в слоте ноды ничего "
                    "не изменит."
                ),
            )
        if cur.get("prompt_variant") == variant:
            raise NodeEditError("same_value", f"У ноды «{label}» уже выбран «{variant}».")
        add("prompt_variant", str(cur.get("prompt_variant") or "по умолчанию"), variant, False)
        raw_before["prompt_variant"] = _raw(meta, node_key, "prompt_variant")
        raw_after["prompt_variant"] = variant
        _write_raw(new_meta, node_key, "prompt_variant", variant)

    if changes.get("retry_profile") is not None:
        prof = str(changes["retry_profile"]).strip().lower()
        if prof not in RETRY_PROFILES:
            raise NodeEditError("bad_value", "Профиль повторов: safe (обычный) или fast (короткий).")
        cur_prof = cur.get("retry_profile")
        if cur_prof == prof and meta.get("step_failure_profile") == prof:
            raise NodeEditError("same_value", "Такой профиль повторов уже стоит.")
        more = prof == "safe" and cur_prof != "safe"
        add(
            "retry_profile",
            RETRY_RU.get(str(cur_prof), str(cur_prof)),
            RETRY_RU[prof],
            more,
            "больше повторов: упавший платный шаг будет повторяться дольше"
            if more
            else "настройка общая для всех шагов проекта",
        )
        raw_before["retry_profile"] = _raw(meta, node_key, "retry_profile")
        raw_after["retry_profile"] = prof
        _write_raw(new_meta, node_key, "retry_profile", prof)

    if changes.get("branches"):
        _plan_branches(meta, node, changes["branches"], add, raw_before, raw_after, new_meta)

    if changes.get("reasoning_effort") is not None:
        want = str(changes["reasoning_effort"]).strip().lower()
        if want not in REASONING_EFFORTS:
            raise NodeEditError(
                "bad_value", "Уровень рассуждений: low (низкий), medium (средний) или high (высокий)."
            )
        ok, why = effort_applies(meta, node)
        if not ok:
            raise NodeEditError(
                "no_effect", (f"Уровень рассуждений ноды «{label}» ни на что не повлияет: {why}.")
            )
        cur_effort = node_effort(node)
        if cur_effort == want:
            raise NodeEditError(
                "same_value", f"У ноды «{label}» уже стоит уровень рассуждений «{EFFORT_RU[want]}»."
            )
        more = EFFORT_RANK[want] > EFFORT_RANK.get(cur_effort, EFFORT_RANK["high"])
        add(
            "reasoning_effort",
            EFFORT_RU.get(cur_effort, cur_effort),
            EFFORT_RU[want],
            more,
            "больше рассуждений: больше токенов, запуски ноды дороже и дольше"
            if more
            else "ответы будут короче и могут стать менее тщательными",
        )
        raw_before["reasoning_effort"] = _raw(meta, node_key, "reasoning_effort")
        raw_after["reasoning_effort"] = EFFORT_TO_CANVAS[want]
        _write_raw(new_meta, node_key, "reasoning_effort", EFFORT_TO_CANVAS[want])

    if not items:
        raise NodeEditError("same_value", f"У ноды «{label}» уже такие настройки.")
    return {
        "node_key": node_key,
        "node_label": label,
        "node_type": typ,
        "items": items,
        "needs_confirmation": any(i["needs_confirmation"] for i in items),
        "raw_before": raw_before,
        "raw_after": raw_after,
        "meta_after": new_meta,
    }


def _plan_branches(
    meta: dict, node: dict, branches: Any, add, raw_before: dict, raw_after: dict, new_meta: dict
) -> None:
    from app.orchestrator.graph.validate import validate_workflow_graph
    from app.services.gpt_operator import BRANCHING_ROLES

    key = str(node.get("id") or "")
    label = node_label(node)
    if not isinstance(branches, list):
        raise NodeEditError("bad_value", "Ветки задаются списком: какая связь и какой вид.")
    out_edges = {
        str(e.get("id") or ""): e
        for e in (_graph(meta) or {}).get("edges") or []
        if isinstance(e, dict) and str(e.get("source") or "") == key
    }
    role = _operator_cfg(meta, key).get("role") if node_type(node) == "excel_gpt" else None
    want: dict[str, str] = {}
    for b in branches:
        if not isinstance(b, dict):
            raise NodeEditError("bad_value", "Ветка задаётся парой: связь и вид.")
        eid = str(b.get("edge_id") or "")
        kind = str(b.get("kind") or "").strip().lower()
        if kind not in EDGE_KINDS:
            raise NodeEditError("bad_value", "Вид связи: after, pass, fail или gate.")
        if eid not in out_edges:
            raise NodeEditError("edge_not_found", f"У ноды «{label}» нет исходящей связи «{eid}».")
        if kind != "after" and role not in BRANCHING_ROLES:
            raise NodeEditError(
                "not_applicable",
                (
                    f"Ветки «ок / не ок» бывают только у нод «Работа с GPT» с ролью проверки, "
                    f"сравнения или шлагбаума; у «{label}» такой роли нет."
                ),
            )
        want[eid] = kind
    before = {eid: _edge_kind(out_edges[eid]) for eid in want}
    changed = {eid: k for eid, k in want.items() if before[eid] != k}
    if not changed:
        return
    trial = copy.deepcopy(new_meta)
    _write_raw(trial, key, "branches", changed)
    cg = trial["canvas_graph"]
    check = validate_workflow_graph(list(cg.get("nodes") or []), list(cg.get("edges") or []))
    if not check.get("valid"):
        raise NodeEditError(
            "graph_invalid",
            "Граф после смены веток неверный: " + "; ".join(str(x) for x in check.get("errors") or [])[:500],
        )
    for eid, kind in changed.items():
        target = find_node(meta, str(out_edges[eid].get("target") or ""))
        tname = node_label(target) if target else str(out_edges[eid].get("target"))
        costly = (before[eid], kind) not in SAFE_EDGE_SWITCHES
        add(
            "branches",
            f"связь к «{tname}»: {EDGE_KIND_RU[before[eid]]}",
            f"связь к «{tname}»: {EDGE_KIND_RU[kind]}",
            costly,
            "нода по этой связи может запускаться чаще (повторы по «не ок» или без условия)"
            if costly
            else "нода по этой связи будет запускаться только по вердикту",
        )
    raw_before["branches"] = {eid: before[eid] for eid in changed}
    raw_after["branches"] = dict(changed)
    _write_raw(new_meta, key, "branches", changed)


def params_change_text(plan: dict) -> str:
    parts = [
        f"{i['title_ru']}: {i['before_ru']} → {i['after_ru']}"
        + (f" ({i['note_ru']})" if i.get("note_ru") else "")
        for i in plan["items"]
    ]
    return f"Нода «{plan['node_label']}»: " + "; ".join(parts) + "."


def apply_params_change(
    state: dict, node_key: str, changes: dict, *, actor: str = "", revert_of: str | None = None
) -> tuple[dict, dict]:
    plan = plan_params_change(state, node_key, changes)
    new_meta = plan.pop("meta_after")
    entry = {
        "kind": "params",
        "before": plan["raw_before"],
        "after": plan["raw_after"],
        "at": _now(),
        "by": actor[:120],
    }
    if revert_of:
        entry["revert_of"] = revert_of
    _push_history(new_meta, node_key, entry)
    return new_meta, plan


def revert_params(state: dict, node_key: str, *, actor: str = "") -> tuple[dict, dict]:
    meta = state.get("meta") or {}
    node = require_node(meta, node_key)
    entry = revert_target(meta, node_key, "params")
    if entry is None:
        raise NodeEditError(
            "nothing_to_revert",
            f"У ноды «{node_label(node)}» нет сохранённых прежних настроек: "
            "все правки настроек из истории уже возвращены.",
        )
    after = entry.get("after") or {}
    before = entry.get("before") or {}
    for param, value in after.items():
        cur = _raw(meta, node_key, param, list((value or {}).keys()) if param == "branches" else None)
        if cur != value:
            raise NodeEditError(
                "changed_since",
                (
                    f"Настройку «{PARAM_TITLE_RU.get(param, param)}» ноды «{node_label(node)}» "
                    "уже поменяли после последней правки: возврат перезаписал бы чужое изменение."
                ),
                http=409,
            )
    new_meta = copy.deepcopy(meta)
    items = []
    for param, value in before.items():
        if param == "branches":
            _write_raw(new_meta, node_key, "branches", value or {})
        else:
            _write_raw(new_meta, node_key, param, value)
        items.append({"param": param, "title_ru": PARAM_TITLE_RU.get(param, param)})
    _push_history(
        new_meta,
        node_key,
        {
            "kind": "params",
            "before": after,
            "after": before,
            "at": _now(),
            "by": actor[:120],
            "revert_of": entry.get("at"),
        },
    )
    return new_meta, {"node_key": node_key, "node_label": node_label(node), "items": items}


RAW_PARAM_NAMES = ("disabled", "check_mode", "prompt_variant", "retry_profile", "branches")


def _check_raw_values(meta: dict, node: dict, values: dict) -> None:
    label = node_label(node)
    unknown = sorted(set(values) - set(RAW_PARAM_NAMES))
    if unknown or not values:
        raise NodeEditError("bad_param", f"Неизвестные настройки в снимке: {', '.join(unknown) or 'пусто'}.")
    disabled = values.get("disabled", [])
    if not isinstance(disabled, list) or not all(isinstance(x, str) for x in disabled):
        raise NodeEditError("bad_param", "Снимок отключённых нод повреждён.")
    check = values.get("check_mode", {})
    if not isinstance(check, dict) or set(check) - set(CHECK_CFG_KEYS):
        raise NodeEditError("bad_param", f"Снимок проверки ноды «{label}» повреждён.")
    variant = values.get("prompt_variant")
    if variant is not None and not isinstance(variant, str):
        raise NodeEditError("bad_param", f"Снимок варианта промта ноды «{label}» повреждён.")
    if values.get("retry_profile") not in (None, "safe", "fast"):
        raise NodeEditError("bad_param", "Снимок повторов при сбое повреждён.")
    branches = values.get("branches", {})
    if not isinstance(branches, dict):
        raise NodeEditError("bad_param", f"Снимок веток ноды «{label}» повреждён.")
    own = {
        str(e.get("id") or "")
        for e in (_graph(meta) or {}).get("edges") or []
        if isinstance(e, dict) and str(e.get("source") or "") == str(node.get("id"))
    }
    for eid, kind in branches.items():
        if eid not in own:
            raise NodeEditError("edge_not_found", f"Связи {eid} у ноды «{label}» больше нет.")
        if kind not in EDGE_KIND_RU:
            raise NodeEditError("bad_param", f"Снимок ветки {eid} повреждён.")


def restore_params(
    state: dict, node_key: str, values: dict, *, expected: dict | None = None, actor: str = ""
) -> tuple[dict, dict]:
    """Ставит сырые значения настроек из снимка журнала (откат не по порядку истории ноды).

    Пишет обычную запись истории, поэтому «верни прежние настройки» вернёт и это.
    """
    meta = state.get("meta") or {}
    node = require_node(meta, node_key)
    _check_raw_values(meta, node, values)
    names = list(values)
    current = raw_params(meta, node_key, names, list((values.get("branches") or {}).keys()))
    if expected is not None and current != {k: expected.get(k) for k in names}:
        raise NodeEditError(
            "changed_since",
            (
                f"Настройки ноды «{node_label(node)}» уже поменяли после правки: возврат "
                "перезаписал бы чужое изменение."
            ),
            http=409,
        )
    new_meta = copy.deepcopy(meta)
    items = []
    for param in names:
        _write_raw(new_meta, node_key, param, values[param] if param != "branches" else values[param] or {})
        items.append({"param": param, "title_ru": PARAM_TITLE_RU.get(param, param)})
    _push_history(
        new_meta,
        node_key,
        {
            "kind": "params",
            "before": current,
            "after": copy.deepcopy(values),
            "at": _now(),
            "by": actor[:120],
        },
    )
    return new_meta, {"node_key": node_key, "node_label": node_label(node), "items": items}


def project_image_model(state: dict, ms: dict) -> dict:
    """Нода картинок без своей модели идёт на генераторе из настроек проекта, а не на модели каталога.

    Так считает шаг (``vibecode_catalog.resolve_image_generator_id``): без этого описание
    показывало «Nano Banana 2 (по умолчанию)», а проект шёл на ``image_generator`` проекта.
    """
    gid = str(state.get("image_generator") or "").strip()
    if ms.get("kind") != KIND_IMAGE or not ms.get("is_default") or not gid:
        return ms
    from app.generation_options import IMAGE_GENERATORS_BY_ID

    gen = IMAGE_GENERATORS_BY_ID.get(gid)
    model = next((m for m in allowed_models(KIND_IMAGE) if m.get("image_generator") == gid), None)
    return {
        **ms,
        "model_id": (model or {}).get("id") or gid,
        "model_label": model_title(model) if model else (gen.label if gen else gid),
        "price_ru": price_ru(model) if model else "",
        "is_default": False,
        "from_project": True,
        "known": gen is not None,
        "applies_note": "своя модель на ноде не выбрана, шаг берёт генератор из настроек проекта",
    }


def describe_node(state: dict, node_key: str) -> dict:
    meta = state.get("meta") or {}
    node = require_node(meta, node_key)
    typ = node_type(node)
    label = node_label(node)
    lines = [f"Нода «{label}» ({TYPE_RU.get(typ, typ)})."]
    ms = project_image_model(state, model_state(meta, node_key))
    if ms["kind"]:
        if ms["forced_vibecode"]:
            lines.append(
                "Модель: всегда GPT 5.6 Sol через Vibecode (группа «Кадры»), выбор на ноде не учитывается."
            )
        elif ms.get("from_project"):
            lines.append(
                f"Модель: {ms['model_label']} — из настроек проекта, своя модель на ноде не выбрана"
                + (f", {ms['price_ru']}" if ms["price_ru"] else "")
                + "."
            )
            if not ms["known"]:
                lines.append(
                    "Важно: такого генератора картинок нет в каталоге студии, шаг картинок с ним не пойдёт. "
                    "Поставьте модель на ноде «Картинки» или смените генератор в настройках проекта."
                )
        else:
            lines.append(
                f"Модель: {ms['model_label']}"
                + (" (по умолчанию)" if ms["is_default"] else "")
                + f", {ms['price_ru']}."
            )
            if not ms["applies"]:
                lines.append(f"Важно: {ms['applies_note']}.")
    else:
        lines.append("Модели у этой ноды нет.")
    params = read_params(state, node_key)
    if "reasoning_effort" in params:
        ok, why = effort_applies(meta, node)
        effort = params["reasoning_effort"]
        lines.append(
            f"Уровень рассуждений: {EFFORT_RU.get(effort, effort)}"
            + ("." if ok else f" (не действует: {why}).")
        )
    lines.append("Состояние: " + ("отключена, шаг пропускается." if params["disabled"] else "включена."))
    if "role" in params:
        lines.append(
            f"Роль: {params['role']}. Проверка: {_onoff(params['check_mode'])}"
            + (
                f", режим {'«чинить»' if params['check_fix'] else '«только отчёт»'}."
                if params["check_mode"]
                else "."
            )
        )
    if params.get("prompt_variant"):
        src = {
            "slot": "выбран на ноде",
            "preferred": "выбран на ноде",
            "override": "выбран для шага проекта",
            "global": "общий для всех проектов",
            "default": "по умолчанию",
            "fallback": "первый найденный",
        }.get(str(params.get("prompt_source")), str(params.get("prompt_source")))
        lines.append(f"Промт: «{params['prompt_variant']}» ({src}).")
    for eid, kind in params["branches"].items():
        edge = next(
            (
                e
                for e in (_graph(meta) or {}).get("edges") or []
                if isinstance(e, dict) and str(e.get("id") or "") == eid
            ),
            {},
        )
        target = find_node(meta, str(edge.get("target") or ""))
        lines.append(
            f"Связь к «{node_label(target) if target else edge.get('target')}»: "
            f"{EDGE_KIND_RU.get(kind, kind)}."
        )
    lines.append(
        f"Повторы при сбое (общие для проекта): "
        f"{RETRY_RU.get(params['retry_profile'], params['retry_profile'])}."
    )
    last = revert_target(meta, node_key, "model")
    if last:
        b = model_title(find_catalog_model((last.get("before") or {}).get("model_id")), "по умолчанию")
        a = model_title(find_catalog_model((last.get("after") or {}).get("model_id")), "по умолчанию")
        lines.append(f"Смену модели «{b}» → «{a}» можно вернуть.")
    if revert_target(meta, node_key, "params"):
        lines.append("Правку настроек из истории ноды можно вернуть.")
    return {
        "node_key": node_key,
        "node_type": typ,
        "label": label,
        "model": ms,
        "params": params,
        "text_ru": "\n".join(lines),
    }


async def set_model_on_project(
    project: Any, node_key: str, model_id: str | None, *, allow_costlier: bool, actor: str
) -> dict:
    meta = project.meta if isinstance(project.meta, dict) else {}
    plan = plan_model_change(meta, node_key, model_id)
    if plan["needs_confirmation"] and not allow_costlier:
        raise NodeEditError(
            "needs_confirmation",
            (
                model_change_text(plan) + " Новая модель дороже или её цена неизвестна: "
                "нужно подтверждение человека."
            ),
            http=409,
            details={"plan": plan},
        )
    new_meta, plan = apply_model_change(meta, node_key, model_id, actor=actor)
    project.meta = new_meta
    return {**plan, "text_ru": model_change_text(plan)}


def project_state_of(project: Any) -> dict:
    return {
        "id": getattr(project, "id", None),
        "meta": project.meta if isinstance(project.meta, dict) else {},
        "image_generator": getattr(project, "image_generator", None),
        "prompt_overrides": project.prompt_overrides
        if isinstance(getattr(project, "prompt_overrides", None), dict)
        else {},
    }
