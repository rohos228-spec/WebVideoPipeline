from __future__ import annotations

from app.services.action_layer.builtin_group import (
    _PRE_IDLE,
    _PRE_NODE,
    _PRE_PROJECT,
    _effective_project,
)
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    CostEstimate,
    Precondition,
)

_PID = {"type": "integer", "minimum": 1, "description": "ID проекта"}
_NODE_KEY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 80,
    "pattern": r"^[A-Za-z0-9_.:\-]+$",
    "description": "id ноды на канвасе",
}
_MODEL_ID = {
    "type": ["string", "null"],
    "minLength": 1,
    "maxLength": 120,
    "description": "id или название модели из каталога ноды; null = модель по умолчанию",
}
_BRANCHES = {
    "type": "array",
    "minItems": 1,
    "maxItems": 20,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["edge_id", "kind"],
        "properties": {
            "edge_id": {"type": "string", "minLength": 1, "maxLength": 120},
            "kind": {"type": "string", "enum": ["after", "pass", "fail", "gate"]},
        },
    },
}
_PARAM_PROPS = {
    "disabled": {"type": "boolean"},
    "check_mode": {"type": "boolean"},
    "check_fix": {"type": "boolean"},
    "prompt_variant": {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[^/\\:*?\"<>|]+$"},
    "retry_profile": {"type": "string", "enum": ["safe", "fast"]},
    "branches": _BRANCHES,
    "reasoning_effort": {
        "type": "string",
        "enum": ["low", "medium", "high"],
        "description": "уровень рассуждений текстовой модели ноды (vibecode, kie)",
    },
}
_EDIT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["find", "replace"],
    "properties": {
        "find": {"type": "string", "minLength": 1, "maxLength": 20000},
        "replace": {"type": "string", "maxLength": 20000},
    },
}


def _FREE(params: dict) -> CostEstimate:
    return CostEstimate.free("смена настройки ноды: платных вызовов нет")


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _state(proj: dict) -> dict:
    return {
        "id": proj.get("id"),
        "meta": proj.get("meta") if isinstance(proj.get("meta"), dict) else {},
        "prompt_overrides": proj.get("prompt_overrides")
        if isinstance(proj.get("prompt_overrides"), dict)
        else {},
    }


def _node_path(params: dict, suffix: str) -> str:
    return f"/projects/{params['project_id']}/nodes/{params['node_key']}/{suffix}"


def _changes(params: dict) -> dict:
    return {k: params[k] for k in _PARAM_PROPS if k in params}


async def _model_plan(ctx: ActionContext, params: dict) -> dict:
    from app.services import node_edit as ne

    proj = await _effective_project(ctx, params["project_id"])
    return ne.plan_model_change(_state(proj)["meta"], params["node_key"], params.get("model_id"))


async def _pre_model_ok(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    try:
        await _model_plan(ctx, params)
    except ne.NodeEditError as exc:
        return exc.message
    return None


async def _pre_model_not_costlier(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    try:
        plan = await _model_plan(ctx, params)
    except ne.NodeEditError as exc:
        return exc.message
    if plan["needs_confirmation"]:
        return (
            ne.model_change_text(plan) + " Модель дороже текущей или её цена неизвестна: "
            "используйте «Сменить модель ноды (дороже или цена неизвестна)» (нужно подтверждение)."
        )
    return None


async def _set_model(ctx: ActionContext, params: dict, *, costly: bool) -> dict:
    from app.services.action_layer.builtin_group import _get_project, node_label_ru

    data = await ctx.call(
        "PATCH",
        _node_path(params, "model"),
        json={"model_id": params.get("model_id"), "allow_costlier": costly},
    )
    out = {
        k: data.get(k)
        for k in (
            "ok",
            "node_key",
            "node_label",
            "before_model_id",
            "after_model_id",
            "before_label",
            "after_label",
            "cost",
            "applies",
            "applies_note",
            "text_ru",
        )
    }
    try:
        meta = (await _get_project(ctx, params["project_id"])).get("meta")
        out["node_label"] = node_label_ru(meta, params["node_key"])
    except Exception:  # noqa: BLE001
        pass
    return out


async def _set_model_cheap(ctx: ActionContext, params: dict) -> dict:
    return await _set_model(ctx, params, costly=False)


async def _set_model_costly(ctx: ActionContext, params: dict) -> dict:
    return await _set_model(ctx, params, costly=True)


def _model_effects(params: dict) -> str:
    from app.services import node_edit as ne

    mid = params.get("model_id")
    found = ne.find_catalog_model(mid) if mid else None
    name = ne.model_title(found, mid) if mid else "модель по умолчанию"
    price = ne.price_ru(found) if found else "цена неизвестна"
    return (
        f"На ноде {params['node_key']} будет поставлена модель «{name}» ({price}). Она "
        "дороже текущей или её цена неизвестна: следующие запуски ноды могут стоить больше. "
        "Прежняя модель сохраняется, вернуть её можно действием «Вернуть прежнюю модель "
        "ноды»."
    )


async def _pre_model_revert(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    proj = await _effective_project(ctx, params["project_id"])
    try:
        ne.plan_model_revert(_state(proj)["meta"], params["node_key"])
    except ne.NodeEditError as exc:
        return exc.message
    return None


async def _revert_model(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.builtin_group import _get_project, node_label_ru

    data = await ctx.call("POST", _node_path(params, "model/revert"))
    out = {k: data.get(k) for k in ("ok", "node_key", "before_model_id", "after_model_id", "text_ru")}
    try:
        meta = (await _get_project(ctx, params["project_id"])).get("meta")
        label = node_label_ru(meta, params["node_key"])
    except Exception:  # noqa: BLE001
        label = data.get("node_label") or params["node_key"]
    after, before = data.get("after_label"), data.get("before_label")
    if after:
        # Возврат к модели по умолчанию: after_model_id пуст, подпись есть всегда.
        was = f" (была «{before}»)" if before and before != after else ""
        out["text_ru"] = f"На ноде «{label}» вернул модель «{after}»{was}."
    return out


async def _params_plan(ctx: ActionContext, params: dict) -> dict:
    from app.services import node_edit as ne

    proj = await _effective_project(ctx, params["project_id"])
    return ne.plan_params_change(_state(proj), params["node_key"], _changes(params))


async def _pre_params_ok(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    try:
        await _params_plan(ctx, params)
    except ne.NodeEditError as exc:
        return exc.message
    return None


async def _pre_params_safe(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    try:
        plan = await _params_plan(ctx, params)
    except ne.NodeEditError as exc:
        return exc.message
    if plan["needs_confirmation"]:
        why = "; ".join(i["note_ru"] for i in plan["items"] if i["needs_confirmation"] and i.get("note_ru"))
        return (
            f"{ne.params_change_text(plan)} Нужна кнопка подтверждения ({why}): "
            "используйте «Изменить настройки ноды с подтверждением»."
        )
    return None


async def _set_params(ctx: ActionContext, params: dict, *, costly: bool) -> dict:
    data = await ctx.call(
        "PATCH", _node_path(params, "params"), json={**_changes(params), "allow_costlier": costly}
    )
    return {k: data.get(k) for k in ("ok", "node_key", "items", "text_ru")}


async def _set_params_cheap(ctx: ActionContext, params: dict) -> dict:
    return await _set_params(ctx, params, costly=False)


async def _set_params_costly(ctx: ActionContext, params: dict) -> dict:
    return await _set_params(ctx, params, costly=True)


def _params_effects(params: dict) -> str:
    from app.services import node_edit as ne

    names = ", ".join(ne.PARAM_TITLE_RU[k] for k in _changes(params))
    return (
        f"У ноды {params['node_key']} изменятся: {names}. Изменение может увеличить число "
        "платных запусков (повторы, проверка, ветки) или затронуть другие ноды того же "
        "типа. Прежние значения сохраняются, вернуть: «Вернуть прежние настройки ноды»."
    )


async def _pre_params_revert(ctx: ActionContext, params: dict) -> str | None:
    from app.services import node_edit as ne

    proj = await _effective_project(ctx, params["project_id"])
    try:
        ne.revert_params(_state(proj), params["node_key"])
    except ne.NodeEditError as exc:
        return exc.message
    return None


async def _revert_params(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("POST", _node_path(params, "params/revert"))
    return {k: data.get(k) for k in ("ok", "node_key", "items", "text_ru")}


PROMPT_SHOWN_CHARS = 1500
_PROMPT_PART = {
    "type": "string",
    "enum": ["start", "end"],
    "description": "какую часть промта показать: start — начало (по умолчанию), end — конец, последние строки",
}


async def _describe(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("GET", _node_path(params, "describe"))
    if not isinstance(data, dict):
        return data
    text = await _prompt_excerpt(ctx, params)
    if text:
        data = {
            **data,
            "prompt_text": text,
            "text_ru": (str(data.get("text_ru") or "").rstrip() + "\n" + text).strip(),
        }
    return data


async def _prompt_excerpt(ctx: ActionContext, params: dict) -> str:
    """«Какой промт стоит у ноды»: сам текст текущего промта, до PROMPT_SHOWN_CHARS знаков.

    Без него ответ был «промт default, текста в данных нет». Нода без промта или файл не
    прочитан — пусто, остальное описание ноды остаётся."""
    try:
        base = await ctx.call(
            "GET",
            "/change-control/prompt-base",
            params={"project_id": params["project_id"], "node_key": params["node_key"]},
        )
    except Exception:  # noqa: BLE001 — у ноды может не быть промта (картинки, видео)
        return ""
    if not isinstance(base, dict) or base.get("source") == "missing":
        return ""
    body = str(base.get("text") or "").strip()
    if not body:
        return ""
    total = int(base.get("text_chars") or len(body))
    # «Покажи конец промта»: последние строки. Обрезанный сервером текст — не настоящий конец.
    end = params.get("prompt_part") == "end" and not base.get("truncated")
    cut = _tail_cut(body) if end else body[:PROMPT_SHOWN_CHARS].rstrip()
    name = str(base.get("variant") or "").strip()
    head = f"Текст промта «{name}»" if name else "Текст промта"
    # Правка клиента или проекта поверх системного текста: говорим, чей текст показан.
    if base.get("text_level") in ("project", "tenant", "brand") and base.get("text_level_ru"):
        head += f" ({base['text_level_ru']})"
    which = "последние" if end else "первые"
    tail = f"\n(показаны {which} {len(cut)} из {total} знаков)" if len(cut) < total else ""
    return f"{head}:\n{cut}{tail}"


def _tail_cut(body: str) -> str:
    """Конец промта до PROMPT_SHOWN_CHARS знаков, с начала строки, а не с середины слова."""
    if len(body) <= PROMPT_SHOWN_CHARS:
        return body
    cut = body[-PROMPT_SHOWN_CHARS:]
    line = cut.find("\n")
    if 0 <= line < PROMPT_SHOWN_CHARS // 2:
        cut = cut[line + 1 :]
    return cut.strip()


_COST_RU = {"cheaper": "дешевле", "same": "так же", "costlier": "дороже", "unknown": "цена неизвестна"}


def _models_text_ru(data: dict) -> str:
    cur = data.get("current") or {}
    lines = [
        f"Сейчас на ноде «{cur.get('label') or cur.get('node_key')}»: "
        f"{cur.get('model_label') or cur.get('model_id') or 'модель по умолчанию'}"
        + (f" ({cur['price_ru']})" if cur.get("price_ru") else "")
        + "."
    ]
    options = [o for o in data.get("options") or [] if isinstance(o, dict)]
    if not options:
        lines.append("Других моделей для этой ноды в каталоге нет.")
    for o in options:
        mark = _COST_RU.get(str(o.get("cost_vs_current") or ""), "")
        lines.append(
            f"- {o.get('label') or o.get('id')} (id {o.get('id')}): {mark}"
            + (f", {o['price_ru']}" if o.get("price_ru") else "")
            + (", нужно подтверждение" if o.get("needs_confirmation") else "")
        )
    return "\n".join(lines)


async def _list_models(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("GET", _node_path(params, "model-options"))
    if isinstance(data, dict) and not data.get("text_ru"):
        data = {**data, "text_ru": _models_text_ru(data)}
    return data


_TEXT_SOURCES = ("text", "edits", "append")
APPLY_HINT_RU = (
    "Чтобы применить, подтвердите карточку применения в чате, напишите «примени правку промта» или "
    "нажмите кнопку в «Регистратор» → «Контролируемые изменения». Передумали — «отмени предложение правки»."
)
_SHOWN_LINES = 6
_LINE_LIMIT = 160


async def _pre_one_text_source(ctx: ActionContext, params: dict) -> str | None:
    if sum(1 for key in _TEXT_SOURCES if key in params) != 1:
        return "укажите ровно одно: полный новый текст, список замен или текст, который дописать в конец"
    return None


async def _new_prompt_text(ctx: ActionContext, params: dict) -> tuple[str, dict]:
    base = await ctx.call(
        "GET",
        "/change-control/prompt-base",
        params={"project_id": params["project_id"], "node_key": params["node_key"]},
    )
    if base.get("source") == "missing":
        raise ValueError(base.get("missing_reason") or "файл текущего промта не найден")
    if "text" in params:
        return params["text"], base
    if base.get("truncated"):
        raise ValueError("промт слишком длинный для правки заменами: пришлите полный текст")
    text = str(base.get("text") or "")
    if "append" in params:
        tail = str(params["append"]).strip()
        if not tail:
            raise ValueError("текст для дописывания пустой")
        return (text.rstrip("\n") + "\n" + tail if text.strip() else tail), base
    for i, edit in enumerate(params["edits"], 1):
        n = text.count(edit["find"])
        if n != 1:
            raise ValueError(
                f"замена {i}: фрагмент "
                + ("не найден в промте" if n == 0 else f"встречается {n} раз, уточните его")
            )
        text = text.replace(edit["find"], edit["replace"], 1)
    return text, base


def _propose_kwargs(params: dict, text: str) -> dict:
    return {
        "tier": "T2",
        "kind": "prompt_text",
        "rationale": params["rationale"],
        "candidate": {"text": text},
        "project_id": params["project_id"],
        "node_key": params["node_key"],
    }


async def _pre_prompt_edit(ctx: ActionContext, params: dict) -> str | None:
    from app.services.action_layer.context import ActionCallError
    from app.services.change_control import service
    from app.services.change_control.errors import ChangeRefused
    from app.services.change_control.gateway import CtxGateway

    if await _pre_one_text_source(ctx, params):
        return None
    try:
        text, _base = await _new_prompt_text(ctx, params)
        await service.prepare_proposal(
            CtxGateway(ctx), proposer=ctx.operator, **_propose_kwargs(params, text)
        )
    except ChangeRefused as exc:
        return str(exc)
    except ActionCallError as exc:
        detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
        return f"промт ноды недоступен для правки: {detail}"
    except ValueError as exc:
        return str(exc)
    return None


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= _LINE_LIMIT else line[: _LINE_LIMIT - 1].rstrip() + "…"


def changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    import difflib

    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+") and line[1:].strip():
            added.append(_clip(line[1:]))
        elif line.startswith("-") and line[1:].strip():
            removed.append(_clip(line[1:]))
    return added, removed


def _lines_ru(head: str, mark: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    out = [head] + [f"{mark} {line}" for line in lines[:_SHOWN_LINES]]
    if len(lines) > _SHOWN_LINES:
        out.append(f"…и ещё строк: {len(lines) - _SHOWN_LINES}")
    return out


def prompt_edit_text_ru(
    node: str,
    before_chars: object,
    after_chars: int,
    added: list[str],
    removed: list[str],
    project_id: object = None,
) -> str:
    # Ответ читают и в чате студии, где «этот проект» ни о чём: называем номер, когда он есть.
    where = f"проекта #{project_id}" if isinstance(project_id, int) and project_id > 0 else "этого проекта"
    lines = [
        f"Подготовил правку промта ноды {node} только для {where}, общий промт не меняется.",
        f"Было: {before_chars} символов, стало: {after_chars}.",
    ]
    lines += _lines_ru("Добавлено:", "+", added)
    lines += _lines_ru("Убрано:", "−", removed)
    lines.append("Промт пока не изменён. " + APPLY_HINT_RU)
    return "\n".join(lines)


async def _prompt_edit(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.builtin_change import _proposer
    from app.services.change_control import service
    from app.services.change_control.gateway import CtxGateway

    text, base = await _new_prompt_text(ctx, params)
    view = await service.propose(
        CtxGateway(ctx),
        proposer=await _proposer(ctx),
        plan_id=ctx.plan_id or "",
        **_propose_kwargs(params, text),
    )
    diff = str(view.get("diff") or "")
    added, removed = changed_lines(str(base.get("text") or ""), text)
    # Не "diff": по этому полю ядро оркестратора считает шаг готовой правкой и требует версию.
    return {
        "proposal_id": view["id"],
        "status": view["status"],
        "digest": view["digest"],
        "target": view["target"],
        "proposal_diff": diff[:4000],
        "diff_truncated": len(diff) > 4000,
        "before_chars": base.get("text_chars"),
        "after_chars": len(text),
        "deduplicated": bool(view.get("deduplicated")),
        "node_key": params["node_key"],
        "added_lines": added[:20],
        "removed_lines": removed[:20],
        "apply_step": {
            "action_id": "change.apply",
            "params": {"proposal_id": view["id"], "expected_digest": view["digest"]},
        },
        "text_ru": prompt_edit_text_ru(
            params["node_key"], base.get("text_chars"), len(text), added, removed, params.get("project_id")
        ),
    }


_MANY_LIMIT = 20
_PIDS = {
    "type": "array",
    "minItems": 1,
    "maxItems": _MANY_LIMIT,
    "items": {"type": "integer", "minimum": 1},
    "description": "ID проектов",
}
_VARIANT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 120,
    "pattern": r"^[A-Za-z0-9_.\-]+$",
    "description": "имя варианта промта из библиотеки",
}


def _ids_ru(ids: list[int]) -> str:
    return ", ".join(f"#{pid}" for pid in ids)


def _reason_ru(why: object) -> str:
    return " ".join(str(why).split()).rstrip(".")


def _error_ru(exc: Exception) -> str:
    from app.services.action_layer.context import ActionCallError

    detail = exc.detail if isinstance(exc, ActionCallError) else str(exc)
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("code") or detail
    return _reason_ru(detail)


async def _model_one_check(ctx: ActionContext, one: dict) -> str | None:
    from app.services.action_layer.builtin_group import _project_idle

    return await _project_idle(ctx, one) or await _pre_model_ok(ctx, one)


async def _variant_one_check(ctx: ActionContext, one: dict) -> str | None:
    from app.services.action_layer.builtin_group import _project_idle

    # Проверка node_edit по графу проекта: вариант есть в библиотеке, нода его читает, он не стоит уже.
    return await _project_idle(ctx, one) or await _pre_params_ok(
        ctx, {"project_id": one["project_id"], "node_key": one["node_key"], "prompt_variant": one["variant"]}
    )


def _one(params: dict, pid: int, field: str) -> dict:
    return {"project_id": pid, "node_key": params["node_key"], field: params.get(field)}


async def _skips(ctx: ActionContext, params: dict, field: str, check) -> dict[int, str]:
    """Почему проект пропускается: занят, нет ноды, правка неприменима или уже стоит."""
    out: dict[int, str] = {}
    for pid in params["project_ids"]:
        try:
            why = await check(ctx, _one(params, pid, field))
        except RuntimeError as exc:
            why = _error_ru(exc)
        if why:
            out[pid] = _reason_ru(why)
    return out


def _pre_any(field: str, check) -> Precondition:
    async def run(ctx: ActionContext, params: dict) -> str | None:
        skips = await _skips(ctx, params, field, check)
        if len(skips) < len(params["project_ids"]):
            return None
        return "ни в одном проекте правку сделать нельзя: " + "; ".join(
            f"#{pid} - {why}" for pid, why in skips.items()
        )

    return Precondition("some_project_fits", "Хотя бы в одном проекте правку можно сделать", run)


async def _model_warnings(ctx: ActionContext, params: dict) -> list[str]:
    from app.services import node_edit as ne

    skips = await _skips(ctx, params, "model_id", _model_one_check)
    out = [f"Проект #{pid} будет пропущен: {why}." for pid, why in skips.items()]
    dearer: list[int] = []
    for pid in params["project_ids"]:
        if pid in skips:
            continue
        try:
            if (await _model_plan(ctx, _one(params, pid, "model_id")))["needs_confirmation"]:
                dearer.append(pid)
        except (RuntimeError, ne.NodeEditError):
            continue
    if dearer:
        out.append(f"Новая модель дороже текущей или её цена неизвестна в проектах {_ids_ru(dearer)}.")
    return out


async def _variant_warnings(ctx: ActionContext, params: dict) -> list[str]:
    skips = await _skips(ctx, params, "variant", _variant_one_check)
    return [f"Проект #{pid} будет пропущен: {why}." for pid, why in skips.items()]


async def _run_many(ctx: ActionContext, params: dict, field: str, check, apply) -> dict:
    """Правка по проектам по очереди: отказ одного проекта не останавливает остальные."""
    changed: list[dict] = []
    skipped: list[dict] = []
    for pid in params["project_ids"]:
        one = _one(params, pid, field)
        try:
            why = await check(ctx, one)
            if not why:
                changed.append({"project_id": pid, **await apply(ctx, one)})
                continue
        except RuntimeError as exc:
            why = _error_ru(exc)
        skipped.append({"project_id": pid, "reason": _reason_ru(why)})
    return {
        "node_key": params["node_key"],
        field: params.get(field),
        "count": len(changed),
        "changed": changed,
        "skipped": skipped,
    }


def _projects_ru(n: int) -> str:
    return f"{n} " + ("проекте" if n % 10 == 1 and n % 100 != 11 else "проектах")


def _skipped_ru(skipped: list[dict]) -> list[str]:
    if not skipped:
        return []
    return ["Не менял: " + "; ".join(f"#{s['project_id']} - {s['reason']}" for s in skipped) + "."]


async def _model_apply(ctx: ActionContext, one: dict) -> dict:
    from app.services.action_layer.builtin_wave5_history import HANDLERS, _wrap_executor

    # Как у одиночной смены модели: снимок прежней модели проекта, откат history.rollback.
    # По нему «верни как было в проекте N» и «что ты сделал сегодня» видят правку.
    run = _wrap_executor("node.set_model_costly", HANDLERS["node.set_model_costly"], _set_model_costly)
    out = await run(ctx, one)
    return {
        k: out.get(k)
        for k in (
            "node_label",
            "before_label",
            "after_label",
            "after_model_id",
            "cost",
            "applies",
            "applies_note",
            "snapshot_id",
            "rollback_possible",
        )
    }


async def _set_model_many(ctx: ActionContext, params: dict) -> dict:
    out = await _run_many(ctx, params, "model_id", _model_one_check, _model_apply)
    changed = out["changed"]
    lines: list[str] = []
    if changed:
        first = changed[0]
        was = [
            f"#{c['project_id']}"
            + (
                f" (была «{c['before_label']}»)"
                if c.get("before_label") not in (None, c.get("after_label"))
                else ""
            )
            for c in changed
        ]
        lines.append(
            f"На ноде «{first.get('node_label') or params['node_key']}» поставил модель "
            f"«{first.get('after_label')}» в {_projects_ru(len(changed))}: " + ", ".join(was) + "."
        )
        idle = [c for c in changed if c.get("applies") is False]
        if idle:
            lines.append(
                f"Внимание, {_ids_ru([c['project_id'] for c in idle])}: {idle[0].get('applies_note')}."
            )
        from app.services.action_layer.chat_undo import many_model_phrase

        phrase = many_model_phrase(
            [c["project_id"] for c in changed], first.get("node_label") or params["node_key"]
        )
        lines.append(f"Прежняя модель каждого проекта сохранена, вернуть все разом: «{phrase}».")
    else:
        lines.append("Модель ни в одном проекте не поменял.")
    lines += _skipped_ru(out["skipped"])
    return {**out, "text_ru": "\n".join(lines)}


async def _variant_apply(ctx: ActionContext, one: dict) -> dict:
    from app.services.action_layer.builtin_group import _node_set_prompt_variant
    from app.services.action_layer.builtin_wave5_history import HANDLERS, _wrap_executor

    # Как у одиночного выбора промта: снимок прежнего варианта, откат history.rollback.
    run = _wrap_executor(
        "node.set_prompt_variant", HANDLERS["node.set_prompt_variant"], _node_set_prompt_variant
    )
    out = await run(
        ctx, {"project_id": one["project_id"], "node_key": one["node_key"], "variant": one["variant"]}
    )
    return {k: out.get(k) for k in ("text_ru", "snapshot_id", "rollback_possible")}


async def _set_prompt_variant_many(ctx: ActionContext, params: dict) -> dict:
    out = await _run_many(ctx, params, "variant", _variant_one_check, _variant_apply)
    changed = out["changed"]
    lines: list[str] = []
    if changed:
        lines.append(f"Поменял промт ноды в {_projects_ru(len(changed))}:")
        lines += [f"- **#{c['project_id']}** — {c.get('text_ru') or 'промт поставлен.'}" for c in changed]
        from app.services.action_layer.chat_undo import project_undo_phrase

        lines.append(
            "Прежний промт каждого проекта сохранён, вернуть в одном проекте: "
            f"«{project_undo_phrase(changed[0]['project_id'], 'промт')}»."
        )
    else:
        lines.append("Промт ни в одном проекте не поменял.")
    lines += _skipped_ru(out["skipped"])
    return {**out, "text_ru": "\n".join(lines)}


async def _revert_one_check(ctx: ActionContext, one: dict) -> str | None:
    from app.services.action_layer.builtin_group import _project_idle

    return await _project_idle(ctx, one) or await _pre_model_revert(ctx, one)


async def _revert_apply(ctx: ActionContext, one: dict) -> dict:
    out = await _revert_model(ctx, one)
    return {k: out.get(k) for k in ("before_model_id", "after_model_id", "text_ru")}


async def _revert_warnings(ctx: ActionContext, params: dict) -> list[str]:
    skips = await _skips(ctx, params, "", _revert_one_check)
    return [f"Проект #{pid} будет пропущен: {why}." for pid, why in skips.items()]


async def _revert_model_many(ctx: ActionContext, params: dict) -> dict:
    out = await _run_many(ctx, params, "", _revert_one_check, _revert_apply)
    out.pop("", None)
    changed = out["changed"]
    lines: list[str] = []
    if changed:
        lines.append(f"Вернул прежнюю модель в {_projects_ru(len(changed))}:")
        lines += [f"- **#{c['project_id']}** — {c.get('text_ru') or 'модель возвращена.'}" for c in changed]
    else:
        lines.append("Модель ни в одном проекте не вернул.")
    lines += _skipped_ru(out["skipped"])
    return {**out, "text_ru": "\n".join(lines)}


def _model_many_effects(params: dict) -> str:
    from app.services import node_edit as ne

    mid = params.get("model_id")
    found = ne.find_catalog_model(mid) if mid else None
    name = ne.model_title(found, mid) if mid else "модель по умолчанию"
    price = ne.price_ru(found) if found else "цена неизвестна"
    return (
        f"В проектах {_ids_ru(params['project_ids'])} на ноде {params['node_key']} будет "
        f"поставлена модель «{name}» ({price}): следующие запуски ноды пойдут на ней и могут "
        "стоить больше. Занятые проекты и проекты, где правка невозможна, пропускаются. "
        "Прежняя модель каждого проекта сохраняется: «Вернуть прежнюю модель ноды»."
    )


def _variant_many_effects(params: dict) -> str:
    return (
        f"В проектах {_ids_ru(params['project_ids'])} нода {params['node_key']} будет читать "
        f"промт «{params['variant']}», сам файл промта не меняется. Занятые проекты и проекты, "
        "где правка невозможна, пропускаются. Прежний промт каждого проекта сохраняется "
        "снимком: «Откатить правку по журналу»."
    )


def _many_specs() -> list[ActionSpec]:
    from app.services.action_layer.builtin_launch import _uniq_ints

    model_many = _schema(
        {"project_ids": _PIDS, "node_key": _NODE_KEY, "model_id": _MODEL_ID},
        ["project_ids", "node_key", "model_id"],
    )
    variant_many = _schema(
        {"project_ids": _PIDS, "node_key": _NODE_KEY, "variant": _VARIANT},
        ["project_ids", "node_key", "variant"],
    )
    revert_many = _schema({"project_ids": _PIDS, "node_key": _NODE_KEY}, ["project_ids", "node_key"])
    return [
        ActionSpec(
            id="node.set_model_many",
            title_ru="Сменить модель ноды сразу в нескольких проектах",
            description_ru="Одна нода в нескольких проектах (до 20) получает одну модель, одно "
            "подтверждение на все. Каждый проект меняется как одиночной сменой "
            "модели; занятый проект или проект, где правка невозможна, пропускается, "
            "остальные меняются. Прежняя модель каждого проекта: node.revert_model.",
            params_schema=model_many,
            executor=_set_model_many,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            estimator=_FREE,
            scope="global",
            destructive_effects=_model_many_effects,
            warnings=_model_warnings,
            future_spend_note="Следующие запуски ноды в этих проектах пойдут на новой модели.",
            preconditions=(_uniq_ints("project_ids"), _pre_any("model_id", _model_one_check)),
            tags=("node", "model", "batch"),
        ),
        ActionSpec(
            id="node.set_prompt_variant_many",
            title_ru="Поставить промт ноды сразу в нескольких проектах",
            description_ru="Одна нода в нескольких проектах (до 20) получает один вариант промта из "
            "библиотеки, одно подтверждение на все. Каждый проект меняется как "
            "одиночным выбором промта; занятый проект или проект, где правка "
            "невозможна, пропускается, остальные меняются. Прежний вариант каждого "
            "проекта сохраняется снимком: history.rollback.",
            params_schema=variant_many,
            executor=_set_prompt_variant_many,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            estimator=_FREE,
            scope="global",
            destructive_effects=_variant_many_effects,
            warnings=_variant_warnings,
            future_spend_note="Следующие запуски ноды в этих проектах пойдут с другим промтом.",
            preconditions=(_uniq_ints("project_ids"), _pre_any("variant", _variant_one_check)),
            tags=("node", "prompt", "batch"),
        ),
        ActionSpec(
            id="node.revert_model_many",
            title_ru="Вернуть прежнюю модель ноды сразу в нескольких проектах",
            description_ru="Одна нода в нескольких проектах (до 20) возвращает модель, стоявшую до "
            "последней смены, одно подтверждение на все. Каждый проект как "
            "node.revert_model; занятый проект или проект без прежней модели "
            "пропускается, остальные возвращаются.",
            params_schema=revert_many,
            executor=_revert_model_many,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            reversible=True,
            scope="global",
            warnings=_revert_warnings,
            future_spend_note="Следующие запуски ноды в этих проектах пойдут на прежней модели.",
            preconditions=(_uniq_ints("project_ids"), _pre_any("", _revert_one_check)),
            tags=("node", "model", "rollback", "batch"),
        ),
    ]


def _specs() -> list[ActionSpec]:
    node_only = _schema({"project_id": _PID, "node_key": _NODE_KEY}, ["project_id", "node_key"])
    model_schema = _schema(
        {"project_id": _PID, "node_key": _NODE_KEY, "model_id": _MODEL_ID},
        ["project_id", "node_key", "model_id"],
    )
    params_schema = _schema(
        {"project_id": _PID, "node_key": _NODE_KEY, **_PARAM_PROPS}, ["project_id", "node_key"]
    )
    pre_model = Precondition(
        "model_allowed", "Модель есть в каталоге для этой ноды и реально на неё влияет", _pre_model_ok
    )
    pre_params = Precondition("params_valid", "Параметры есть у ноды и код их читает", _pre_params_ok)
    return [
        ActionSpec(
            id="node.describe",
            title_ru="Показать настройки ноды",
            description_ru="Текущие настройки ноды по-русски: модель и цена, действует ли модель "
            "на шаг, вариант промта и откуда он, включена ли нода, проверка, "
            "ветки ок / не ок, повторы при сбое, что можно вернуть, и текст промта: "
            "начало, а для «покажи конец / последние строки / хвост промта» — "
            "prompt_part=end. Ничего не меняет.",
            params_schema=_schema(
                {"project_id": _PID, "node_key": _NODE_KEY, "prompt_part": _PROMPT_PART},
                ["project_id", "node_key"],
            ),
            executor=_describe,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NODE),
            tags=("node", "config", "model"),
        ),
        ActionSpec(
            id="node.list_models",
            title_ru="Какие модели можно поставить на ноду",
            description_ru="Модели из каталога для этой ноды (текст, картинки или видео) с ценой и "
            "пометкой: дешевле, так же, дороже текущей или цена неизвестна.",
            params_schema=node_only,
            executor=_list_models,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NODE),
            tags=("node", "model"),
        ),
        ActionSpec(
            id="node.set_model",
            title_ru="Сменить модель ноды (не дороже текущей)",
            description_ru="Ставит модель на одну ноду проекта точечно, граф целиком не "
            "переписывается, открытый холст правку не откатит. Только если новая "
            "модель не дороже текущей; иначе действие «Сменить модель ноды на более "
            "дорогую». Прежняя модель сохраняется: node.revert_model.",
            params_schema=model_schema,
            executor=_set_model_cheap,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            reversible=True,
            future_spend_note="Следующие запуски ноды пойдут на новой модели.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition(
                    "model_not_costlier",
                    "Модель есть в каталоге ноды и не дороже текущей",
                    _pre_model_not_costlier,
                ),
            ),
            tags=("node", "model"),
        ),
        ActionSpec(
            id="node.set_model_costly",
            title_ru="Сменить модель ноды (дороже или цена неизвестна)",
            description_ru="То же, что смена модели ноды, но для модели дороже текущей или с "
            "неизвестной ценой: только после подтверждения человека. Прежняя "
            "модель сохраняется: node.revert_model.",
            params_schema=model_schema,
            executor=_set_model_costly,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            estimator=_FREE,
            destructive_effects=_model_effects,
            future_spend_note="Следующие запуски ноды могут стоить дороже.",
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, pre_model),
            tags=("node", "model"),
        ),
        ActionSpec(
            id="node.revert_model",
            title_ru="Вернуть прежнюю модель ноды",
            description_ru="Возвращает модель, которая стояла на ноде до последней смены. "
            "Повторный возврат идёт на шаг глубже по истории ноды. Отказ, если "
            "модель после этого уже меняли.",
            params_schema=node_only,
            executor=_revert_model,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            reversible=True,
            future_spend_note="Следующие запуски ноды пойдут на прежней модели.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition(
                    "revert_possible",
                    "Есть прежняя модель, текущую после правки не меняли",
                    _pre_model_revert,
                ),
            ),
            tags=("node", "model", "rollback"),
        ),
        ActionSpec(
            id="node.set_params",
            title_ru="Изменить настройки ноды (без роста расходов)",
            description_ru="Точечно: отключить ноду, выключить проверку или режим «чинить», "
            "выбрать вариант промта ноды, короткие повторы при сбое (для всего "
            "проекта), ветки ок / не ок у проверяющих нод «Работа с GPT», понизить "
            "уровень рассуждений (reasoning_effort: low, medium, high; «пусть думает слабее» — ниже) текстовой "
            "модели ноды на vibecode или kie. Только "
            "то, что код реально читает, и только если расходы не растут; иначе "
            "«Изменить настройки ноды с подтверждением». Откат: node.revert_params.",
            params_schema=params_schema,
            executor=_set_params_cheap,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            reversible=True,
            future_spend_note="Меняет, какие шаги и сколько раз запустятся дальше.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition("params_safe", "Параметры допустимы и не увеличивают расходы", _pre_params_safe),
            ),
            tags=("node", "config"),
        ),
        ActionSpec(
            id="node.set_params_costly",
            title_ru="Изменить настройки ноды с подтверждением",
            description_ru="Те же настройки, что «Изменить настройки ноды», когда изменение может "
            "увеличить расходы (включить проверку или ноду, длинные повторы, ветка "
            "«не ок» или связь без условия, повысить уровень рассуждений — «пусть думает сильнее / глубже») или "
            "затронуть другие ноды. Только после "
            "подтверждения человека. Откат: node.revert_params.",
            params_schema=params_schema,
            executor=_set_params_costly,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            estimator=_FREE,
            destructive_effects=_params_effects,
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, pre_params),
            tags=("node", "config"),
        ),
        ActionSpec(
            id="node.revert_params",
            title_ru="Вернуть прежние настройки ноды",
            description_ru="Возвращает значения, которые были до последней правки настроек "
            "ноды. Повторный возврат идёт на шаг глубже по истории ноды. Отказ, "
            "если их уже меняли после этого.",
            params_schema=node_only,
            executor=_revert_params,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            reversible=True,
            future_spend_note="Возвращает прежние шаги и число повторов для следующих запусков.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition(
                    "revert_possible", "Есть прежние настройки, их после правки не меняли", _pre_params_revert
                ),
            ),
            tags=("node", "config", "rollback"),
        ),
        ActionSpec(
            id="prompt.edit_text",
            title_ru="Предложить правку текста промта ноды (только в одном проекте)",
            description_ru="Готовит правку текста промта одной ноды только для этого проекта: "
            "append - дописать требование в конец, edits - замены фрагментов, text - "
            "полный новый текст. Проходит проверки (метки, "
            "длина, секреты), показывает было/стало и создаёт предложение изменения. "
            "Сам промт не меняется: применяет человек действием «Применить "
            "предложение изменения» (change.apply) с подтверждением, откат через "
            "change.rollback. Общий промт не трогается.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "node_key": _NODE_KEY,
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "text": {"type": "string", "minLength": 40, "maxLength": 200000},
                    "edits": {"type": "array", "minItems": 1, "maxItems": 20, "items": _EDIT},
                    "append": {"type": "string", "minLength": 1, "maxLength": 4000},
                },
                ["project_id", "node_key", "rationale"],
            ),
            executor=_prompt_edit,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(
                _PRE_PROJECT,
                _PRE_NODE,
                Precondition(
                    "one_text_source",
                    "Задано ровно одно: текст, замены или дописывание",
                    _pre_one_text_source,
                ),
                Precondition(
                    "prompt_edit_valid", "Правка проходит проверки контура изменений", _pre_prompt_edit
                ),
            ),
            tags=("prompt", "change"),
        ),
    ]


def register_node_edit_actions(registry: ActionRegistry) -> None:
    from app.services.action_layer import builtin_wave5_history as hist

    registry.register_many([*_specs(), *_many_specs()])
    # Смена модели и настроек тоже пишет снимок: «откати последнее изменение» их видит.
    hist.wrap_catalog(registry)
