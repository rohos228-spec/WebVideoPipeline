from __future__ import annotations

import re

from loguru import logger

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.soft_restore import (
    group_restore,
    precondition_restore_ref,
    remember_soft_delete,
    restore_params_schema,
)
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    CostEstimate,
    JobState,
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
_GROUP_ID = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[A-Za-z0-9_\-]+$"}
_MAX_CELLS = 200

_ROLES = ["assist", "review", "transform", "extract", "compare", "gate"]
_OUTPUT_MODES = ["text", "project_file", "sidecar"]
_EMIT_KINDS = ["result", "reply_txt", "analysis", "inputs"]
_SHOT_FIELDS = [
    "img_prompt",
    "video_prompt",
    "vo",
    "action",
    "characters",
    "stitch",
    "scene",
    "set",
    "size",
    "move",
]

_NODE_RUN_STEP = "excel_gpt"


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


async def _get_project(ctx: ActionContext, pid: int) -> dict:
    return await ctx.call("GET", f"/projects/{pid}")


async def _effective_project(ctx: ActionContext, pid: int) -> dict:
    from app.services.canvas_graph import (
        GRAPH_SOURCE_PROJECT,
        canvas_graph_from_meta,
        with_effective_graph,
    )

    detail = await _get_project(ctx, pid)
    if not isinstance(detail, dict):
        return detail
    meta = detail.get("meta") if isinstance(detail.get("meta"), dict) else {}
    if canvas_graph_from_meta(meta):
        return {**detail, "graph_source": GRAPH_SOURCE_PROJECT}
    try:
        graph = await ctx.call("GET", f"/projects/{pid}/effective-graph")
    except ActionCallError:
        return detail
    if not isinstance(graph, dict):
        return detail
    return {**detail, "meta": with_effective_graph(meta, graph), "graph_source": graph.get("source")}


async def _project_exists(ctx: ActionContext, params: dict) -> str | None:
    await _get_project(ctx, params["project_id"])
    return None


def _is_running(status: str | None) -> bool:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status

    try:
        return is_running_status(ProjectStatus(status))
    except ValueError:
        return False


async def _project_idle(ctx: ActionContext, params: dict) -> str | None:
    proj = await _get_project(ctx, params["project_id"])
    if _is_running(proj.get("status")):
        return f"в проекте выполняется шаг (статус {proj.get('status')})"
    return None


async def _no_montage_jobs(ctx: ActionContext, params: dict) -> str | None:
    base = f"/projects/{params['project_id']}/montage-board"
    for suffix, title in (
        ("apply-status", "применение правок"),
        ("montage-status", "монтаж видео"),
        ("recover-outsee-status", "забор из галереи"),
    ):
        data = await ctx.call("GET", f"{base}/{suffix}")
        if ((data or {}).get("job") or {}).get("status") == "running":
            return f"идёт задача монтажа: {title}"
    return None


async def _not_managed(ctx: ActionContext, params: dict) -> str | None:
    try:
        from app.services.actions_v1 import managed_project_enabled

        managed = await managed_project_enabled(params["project_id"])
    except Exception as exc:
        return f"не удалось проверить managed-режим ({type(exc).__name__})"
    if managed:
        return (
            "проект под управляемым исполнением Actions-v1: ручной запуск шага "
            "заблокирован (409), запуск идёт только через панель Actions-v1"
        )
    return None


def _canvas_node(meta: dict | None, node_key: str) -> dict | None:
    from app.services.vibecode_catalog import find_canvas_node

    return find_canvas_node(meta or {}, node_key=node_key)


async def _node_exists(ctx: ActionContext, params: dict) -> str | None:
    proj = await _effective_project(ctx, params["project_id"])
    if _canvas_node(proj.get("meta"), params["node_key"]) is None:
        return f"нода {params['node_key']!r} не найдена на канвасе проекта"
    return None


async def _node_is_gpt(ctx: ActionContext, params: dict) -> str | None:
    from app.services.excel_gpt_node import effective_node_type

    proj = await _effective_project(ctx, params["project_id"])
    node = _canvas_node(proj.get("meta"), params["node_key"])
    if node is None:
        return f"нода {params['node_key']!r} не найдена на канвасе проекта"
    if effective_node_type(node) != _NODE_RUN_STEP:
        return "запуск доступен только для нод «Работа с GPT» (excel_gpt)"
    if params["node_key"] in [str(x) for x in ((proj.get("meta") or {}).get("disabled_nodes") or [])]:
        return "нода отключена"
    return None


def _forced_vibecode(node_key: str) -> bool:
    from app.services.node_groups import is_script_frames_qc_gpt_node

    return is_script_frames_qc_gpt_node(None, node_key=node_key)


_PRE_PROJECT = Precondition("project_exists", "Проект существует", _project_exists)
_PRE_IDLE = Precondition("project_idle", "В проекте не выполняется шаг", _project_idle)
_PRE_NO_MONTAGE = Precondition("no_montage_jobs", "Не идут задачи панели монтажа", _no_montage_jobs)
_PRE_NOT_MANAGED = Precondition(
    "not_managed", "Проект не под управляемым исполнением Actions-v1", _not_managed
)
_PRE_NODE = Precondition("node_exists", "Нода есть на канвасе проекта", _node_exists)
_PRE_NODE_GPT = Precondition("node_runnable", "Нода «Работа с GPT» существует и не отключена", _node_is_gpt)


async def _stop_project(ctx: ActionContext, params: dict) -> dict:
    try:
        data = await ctx.call("POST", f"/projects/{params['project_id']}/stop")
    except ActionCallError as exc:
        if exc.status == 400:
            return {"stopped": False, "message": str(exc.detail)}
        raise
    return {
        "stopped": True,
        "message": data.get("message"),
        "generation_still_active": data.get("generation_still_active"),
        "xlsx_stopped": data.get("xlsx_stopped"),
    }


async def _stop_project_action(ctx: ActionContext, params: dict) -> dict:
    """project.stop из чата: тот же стоп + понятный текст (обработчик stop= остаётся быстрым)."""
    out = await _stop_project(ctx, params)
    out["text_ru"] = await _stop_text(ctx, params["project_id"], out, bool(out.get("stopped")))
    return out


async def _stop_text(ctx: ActionContext, pid: int, data: dict | None, stopped: bool) -> str:
    from app.services.action_layer.builtin_insight import _status_ru

    try:
        proj = await _get_project(ctx, pid)
    except Exception:  # noqa: BLE001
        proj = {}
    name = f"#{pid}" + (f" «{proj.get('title')}»" if proj.get("title") else "")
    if not stopped:
        return f"{name}: останавливать нечего."
    status = _status_ru(str(proj.get("status"))) if proj.get("status") else ""
    tail = " Генерация у провайдера ещё дозавершается." if (data or {}).get("generation_still_active") else ""
    return f"{name}: остановлен" + (f", {status}" if status else "") + "." + tail


def _shot_base(pid: int) -> str:
    return f"/db/projects/{pid}/shot-menu"


async def _shot_get_menu(ctx: ActionContext, params: dict) -> dict:
    menu = await ctx.call("GET", _shot_base(params["project_id"]))
    cells = (menu or {}).get("cells") or []
    return {"cell_count": len(cells), "cells": cells[:_MAX_CELLS], "cells_truncated": len(cells) > _MAX_CELLS}


async def _shot_edit_cell(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call(
        "PATCH",
        f"{_shot_base(params['project_id'])}/cell",
        json={"parent_uuid": params["parent_uuid"], "voiceover": params["voiceover"]},
    )
    return data if isinstance(data, dict) else {"ok": True}


async def _shot_add_cell(ctx: ActionContext, params: dict) -> dict:
    body: dict = {"voiceover": params.get("voiceover", "")}
    if "before_index" in params:
        body["before_index"] = params["before_index"]
    data = await ctx.call("POST", f"{_shot_base(params['project_id'])}/cell", json=body)
    return data if isinstance(data, dict) else {"ok": True}


async def _shot_edit_field(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call(
        "PATCH",
        f"{_shot_base(params['project_id'])}/shot-field",
        json={"frame_uuid": params["frame_uuid"], "field": params["field"], "value": params["value"]},
    )
    return data if isinstance(data, dict) else {"ok": True}


async def _group_list(ctx: ActionContext, params: dict) -> dict:
    groups = await ctx.call("GET", "/node-groups")
    return {
        "count": len(groups),
        "groups": [
            {k: g.get(k) for k in ("id", "title", "description", "category", "node_count", "builtin")}
            for g in groups
        ],
    }


async def _group_get(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("GET", f"/node-groups/{params['group_id']}")


async def _find_group(ctx: ActionContext, group_id: str) -> dict | None:
    for g in await ctx.call("GET", "/node-groups"):
        if g.get("id") == group_id:
            return g
    return None


async def _group_exists(ctx: ActionContext, params: dict) -> str | None:
    if await _find_group(ctx, params["group_id"]) is None:
        return f"группа {params['group_id']!r} не найдена"
    return None


async def _group_is_custom(ctx: ActionContext, params: dict) -> str | None:
    group = await _find_group(ctx, params["group_id"])
    if group is None:
        return f"группа {params['group_id']!r} не найдена"
    if group.get("builtin"):
        return "группа встроенная: менять и удалять можно только пользовательские"
    return None


async def _group_id_free(ctx: ActionContext, params: dict) -> str | None:
    gid = params.get("group_id")
    if gid and await _find_group(ctx, gid) is not None:
        return f"группа {gid!r} уже есть: перезапись запрещена, укажите другой id"
    return None


async def _group_insert(ctx: ActionContext, params: dict) -> dict:
    body = {"after": params["after"]} if params.get("after") else None
    data = await ctx.call(
        "POST", f"/projects/{params['project_id']}/canvas/groups/{params['group_id']}", json=body
    )
    return {"group_id": params["group_id"], "result": data}


async def _group_from_selection(ctx: ActionContext, params: dict) -> dict:
    body = {k: params[k] for k in ("node_ids", "title", "description", "category", "group_id") if k in params}
    return await ctx.call("POST", f"/projects/{params['project_id']}/node-groups/from-selection", json=body)


async def _group_update(ctx: ActionContext, params: dict) -> dict:
    body = {k: params[k] for k in ("title", "description", "category") if k in params}
    return await ctx.call("PUT", f"/node-groups/{params['group_id']}", json=body)


async def _has_group_patch(ctx: ActionContext, params: dict) -> str | None:
    if not any(k in params for k in ("title", "description", "category")):
        return "не указано ни одного поля для изменения"
    return None


async def _group_delete(ctx: ActionContext, params: dict) -> dict:
    from app.services.node_groups import _group_to_dict, get_node_group

    gid = str(params["group_id"])
    group = get_node_group(gid)
    before = _group_to_dict(group) if group is not None else {"group_id": gid, "absent": True}
    snap = await remember_soft_delete(
        ctx,
        action_id="group.delete",
        project_id=None,
        kind="group",
        target={"group_id": gid},
        before=before,
    )
    data = await ctx.call("DELETE", f"/node-groups/{gid}")
    if isinstance(data, dict):
        return {**data, "snapshot_id": snap}
    return {"deleted": gid, "snapshot_id": snap}


async def _shots_fallback(ctx: ActionContext, pid: int) -> dict:
    """Готового отчёта группы «Кадры» нет: собираем отчёт по нодам с кадрами и промтами.

    Раньше «отчёт по кадрам с промтами» отвечал «отчёта пока нет» без единой ссылки.
    """
    from app.services.action_layer.builtin_insight import _report_nodes

    missing = (
        f"Готового отчёта группы «Кадры» у проекта #{pid} нет: его пишет последняя нода группы "
        "«Кадры» после прогона группы."
    )
    try:
        out = await _report_nodes(ctx, {"project_id": pid, "kind": "html"})
    except Exception as exc:  # noqa: BLE001 - отчёт по нодам не собрался: честно говорим, что отчёта нет
        logger.warning("shots report fallback #{} failed: {}", pid, type(exc).__name__)
        return {"exists": False, "text_ru": missing}
    summary = out.get("summary") or {}
    frames, prompts = int(summary.get("frames") or 0), int(summary.get("image_prompts") or 0)
    lead = f"{missing} Поэтому собрал отчёт по кадрам и нодам: кадров {frames}, с промтом картинки {prompts}."
    return {
        **out,
        "exists": False,
        "fallback": "report.nodes",
        "text_ru": f"{lead} {out.get('text_ru') or ''}".strip(),
    }


async def _group_shots_report(ctx: ActionContext, params: dict) -> dict:
    import html as html_lib

    pid = params["project_id"]
    url = f"/api/projects/{pid}/shots-report"
    try:
        html = await ctx.call("GET", f"/projects/{pid}/shots-report")
    except ActionCallError as exc:
        if exc.status == 404:
            return await _shots_fallback(ctx, pid)
        raise
    text = html if isinstance(html, str) else str(html)
    # Отчёт отдаём целиком файлом: в ячейках длинные поля, обрезка теряет данные.
    meta = re.search(r"<p class=[\"']?meta[\"']?>(.*?)</p>", text, re.S)
    head = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", meta.group(1))).split()) if meta else ""
    lead = f"Отчёт «Кадры» проекта #{pid}" + (f": {head.split('. ')[0]}." if head else ".")
    return {
        "exists": True,
        "chars": len(text),
        "download_url": url,
        "file_format": "html",
        "text_ru": f"{lead} Полный отчёт без сокращений: [Открыть отчёт (html)]({url})",
    }


async def _node_get_config(ctx: ActionContext, params: dict) -> dict:
    base = f"/projects/{params['project_id']}"
    described = await ctx.call("GET", f"{base}/nodes/{params['node_key']}/describe")
    out = {
        "text_ru": described.get("text_ru"),
        "model": described.get("model"),
        "params": described.get("params"),
        "node_type": described.get("node_type"),
        "forced_vibecode": _forced_vibecode(params["node_key"]),
    }
    if described.get("node_type") == _NODE_RUN_STEP:
        out["resolve"] = await ctx.call("GET", f"{base}/gpt-operator/{params['node_key']}/resolve")
    return out


_PROVIDER_RU = {
    "kie": "Kie",
    "vibecode": "Vibecode",
    "pay52": "52pay",
    "outsee": "Outsee",
    "minimax": "MiniMax",
    "unknown": "неизвестно",
}


def _text_provider(model_id: str | None) -> str:
    """Провайдер текстовых вызовов ноды: модели Claude — Vibecode, иначе выбор студии (kie / pay52 / vibecode)."""
    try:
        from app.services.anthropic_messages import is_anthropic_model
        from app.settings import settings

        if is_anthropic_model(model_id):
            return "vibecode"
        return str(settings.resolved_text_llm_provider() or "unknown")
    except Exception:
        return "unknown"


async def _node_get_llm_route(ctx: ActionContext, params: dict) -> dict:
    described = await ctx.call("GET", f"/projects/{params['project_id']}/nodes/{params['node_key']}/describe")
    model = described.get("model") or {}
    forced = bool(model.get("forced_vibecode")) or _forced_vibecode(params["node_key"])
    # «Через какого провайдера идёт нода»: без провайдера чат отвечал «в данных не указано».
    kind = str(model.get("kind") or "")
    if kind in ("image", "video"):
        # Картинки и видео идут через поставщика медиа, а не через провайдера текстовых запросов.
        from app.settings import settings

        provider = str(getattr(settings, f"{kind}_provider", "") or "unknown").strip().lower()
    else:
        provider = "vibecode" if forced else _text_provider(model.get("model_id"))
    provider_ru = _PROVIDER_RU.get(provider, provider)
    text = (
        f"Модель {model.get('model_label') or model.get('model_id') or 'не задана'}, "
        f"запросы идут через провайдера {provider_ru}."
    )
    if model.get("from_project"):
        text += " Модель взята из настроек проекта: своя на ноде не выбрана."
        if model.get("known") is False:
            text += " Такого генератора нет в каталоге студии, шаг с ним не пойдёт."
    return {
        "node_key": params["node_key"],
        "forced_vibecode": forced,
        "model_id": model.get("model_id"),
        "model_label": model.get("model_label"),
        "provider": provider,
        "text_ru": text,
        "is_default": model.get("is_default"),
        "applies": model.get("applies"),
        "note": (
            "ноды группы script_frames_qc всегда идут через Vibecode «GPT 5.6 Sol»; "
            "выбор модели на ноде игнорируется"
            if forced
            else model.get("applies_note") or "модель берётся из выбора на ноде"
        ),
    }


_CONFIG_FIELDS = (
    "role",
    "outputMode",
    "emitKinds",
    "takeFromEdges",
    "useSnapshot",
    "checkMode",
    "checkFix",
    "checkPromptSource",
)


async def _config_has_changes(ctx: ActionContext, params: dict) -> str | None:
    if not any(k in params for k in _CONFIG_FIELDS):
        return "не указано ни одного поля настройки"
    return None


async def _config_output_mode_allowed(ctx: ActionContext, params: dict) -> str | None:
    if "outputMode" in params and _forced_vibecode(params["node_key"]):
        return "режим выхода нод script_frames_qc не меняется: он нужен для записи результата в БД"
    return None


def _meta_book(proj: dict):
    from app.orchestrator.node_store.meta_book import load_book

    meta = proj.get("meta") if isinstance(proj, dict) else None
    return load_book(meta if isinstance(meta, dict) else None)


def _conflict(exc) -> ActionCallError:
    # 409 без getattr: детектор аудита считает любой getattr записью в ФС.
    return ActionCallError(409, str(exc))


async def _node_set_config(ctx: ActionContext, params: dict) -> dict:
    from app.orchestrator.node_store.meta_book import VersionConflict, checked_meta_version

    proj = await _get_project(ctx, params["project_id"])
    resolved = await ctx.call(
        "GET", f"/projects/{params['project_id']}/gpt-operator/{params['node_key']}/resolve"
    )
    cfg = (resolved or {}).get("config") if isinstance(resolved, dict) else None
    book = _meta_book(proj)
    from app.orchestrator.actions.contracts import guard_stale_edit

    guard_stale_edit(params, book.version)
    book.meta.setdefault("node_config", {})[params["node_key"]] = dict(cfg or {})
    body = {k: params[k] for k in _CONFIG_FIELDS if k in params}
    try:
        book.set_config(node_key=params["node_key"], fields=body, version=checked_meta_version(params, book))
    except VersionConflict as exc:
        raise _conflict(exc) from exc
    data = await ctx.call(
        "PATCH", f"/projects/{params['project_id']}/gpt-operator/{params['node_key']}", json=body
    )
    return {"ok": bool(data.get("ok")), "config": ((data.get("resolve") or {}).get("config")) or {}}


async def _node_set_prompt_variant(ctx: ActionContext, params: dict) -> dict:
    from app.orchestrator.node_store.meta_book import VersionConflict, checked_meta_version

    proj = await _get_project(ctx, params["project_id"])
    slot_id = params.get("slot_id", "main")
    # Файл есть и шаг его прочитает — это проверило предусловие variant_exists; здесь только
    # имя «как назвал человек» становится именем файла в библиотеке.
    step, variant, _problem = _library_choice(proj, params["node_key"], slot_id, params["variant"])
    book = _meta_book(proj)
    from app.orchestrator.actions.contracts import guard_stale_edit

    guard_stale_edit(params, book.version)
    before = (((proj.get("meta") or {}).get("prompt_slot_variants") or {}).get(params["node_key"]) or {}).get(
        slot_id
    )
    try:
        book.set_prompt_variant(
            node_key=params["node_key"],
            slot_id=slot_id,
            variant=variant,
            version=checked_meta_version(params, book),
        )
    except VersionConflict as exc:
        raise _conflict(exc) from exc
    await ctx.call(
        "PATCH",
        f"/projects/{params['project_id']}",
        json={
            "meta": {
                "prompt_slot_variants": {params["node_key"]: {slot_id: variant}},
                "meta_version": book.version,
            }
        },
    )
    return {
        "node_key": params["node_key"],
        "slot_id": slot_id,
        "variant": variant,
        "text_ru": _prompt_variant_ru(proj.get("meta"), params["node_key"], step, variant, before),
    }


HERO_STYLE_SLOT = "style"


def prompt_step_for(node_type: str, slot_id: str = "main") -> str | None:
    """Папка библиотеки промтов слота ноды — как в Node Studio (node-step-map.ts, node-prompts.ts).

    «Работа с GPT» и enrich_* читают 05_excel_gpt, слот style ноды героя — 04_hero_style.
    None — у ноды нет библиотеки промтов (картинки, видео, озвучка, сборка).
    """
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services import prompt_library as pl
    from app.services.excel_gpt_node import EXCEL_GPT_STEP_CODE, is_excel_gpt_node_type

    if is_excel_gpt_node_type(node_type):
        step = EXCEL_GPT_STEP_CODE
    elif node_type == "hero" and slot_id == HERO_STYLE_SLOT:
        step = "hero_style"
    else:
        step = NODE_TYPE_TO_STEP_CODE.get(node_type, "")
    return step if step in pl.STEP_FOLDERS else None


def _norm_variant(name: str) -> str:
    low = " ".join(name.lower().replace("ё", "е").split())
    return low[:-4].rstrip() if low.endswith(".txt") else low


def library_variant(names: list[str], raw: str) -> str | None:
    """Имя файла из библиотеки, которое назвал человек: с .md/.txt или без, регистр не важен."""
    from app.services import prompt_library as pl

    if raw in names:
        return raw
    clean = pl._clean_variant_name(raw)
    if clean in names:
        return clean
    hits = [name for name in names if _norm_variant(name) == _norm_variant(clean)]
    return hits[0] if len(hits) == 1 else None


def library_names(meta: dict | None, node_key: str, step: str) -> list[str]:
    """Список файлов, который Студия показывает слоту: у нод группы script_frames_qc — промты группы."""
    from types import SimpleNamespace

    from app.services import prompt_library as pl

    if pl.is_excel_gpt_prompt_step(step):
        group = pl._group_id_from_canvas_node(SimpleNamespace(meta=meta), node_key)
        owned = pl.list_group_owned_prompts(group)
        if owned is not None:
            return owned
    return pl.list_prompts(step)


def _library_choice(proj: dict, node_key: str, slot_id: str, raw: str) -> tuple[str | None, str, str | None]:
    """(шаг, имя файла в библиотеке, причина отказа). Причина None — шаг прочитает этот файл."""
    from app.services import prompt_library as pl
    from app.services.action_layer import builtin_insight as bi

    meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
    label = node_label_ru(meta, node_key)
    node = next((n for n in bi._canvas_nodes(meta) if str(n["id"]) == node_key), None)
    step = prompt_step_for(str((node or {}).get("type") or ""), slot_id)
    if step is None:
        return None, raw, f"у ноды «{label}» нет библиотеки промтов для слота {slot_id}"
    try:
        names = library_names(meta, node_key, step)
    except Exception as exc:  # noqa: BLE001
        return (
            step,
            raw,
            (f"библиотеку промтов ноды «{label}» не удалось прочитать ({exc}), проверить вариант нельзя"),
        )
    variant = library_variant(names, raw)
    if variant is None:
        hint = ""
        if step == "hero":
            style = library_variant(pl.list_prompts("hero_style"), raw)
            if style:
                hint = f": «{style}» — это стиль персонажа, его ставят в слот style"
        return step, raw, f"промта «{raw}» для ноды «{label}» в библиотеке нет{hint}"
    return step, variant, _unread_reason(proj, node_key, slot_id, step, variant)


def _unread_reason(proj: dict, node_key: str, slot_id: str, step: str, variant: str) -> str | None:
    """Прогоняет выбор через тот же резолвер, что и запуск шага: иначе слот запишется, а шаг
    возьмёт другой файл (выбор шага в проекте, подмена героя, сборка из блоков)."""
    import copy

    from app.services import prompt_library as pl

    if step in ("scene_d", "scene_asm"):
        # Агенты сцен читают свои файлы по имени агента, не через выбор шага.
        return None
    overrides = proj.get("prompt_overrides") if isinstance(proj.get("prompt_overrides"), dict) else {}
    meta = copy.deepcopy(proj.get("meta")) if isinstance(proj.get("meta"), dict) else {}
    slots = meta.get("prompt_slot_variants") if isinstance(meta.get("prompt_slot_variants"), dict) else {}
    node_slots = slots.get(node_key) if isinstance(slots.get(node_key), dict) else {}
    meta["prompt_slot_variants"] = {**slots, node_key: {**node_slots, slot_id: variant}}
    if pl.is_excel_gpt_prompt_step(step):
        where = {"node_key": node_key, "slot_id": slot_id}
    elif step == "hero_style":
        where = {"node_key": node_key, "slot_id": HERO_STYLE_SLOT}
    else:
        where = {}
    name, source = pl.resolve_project_prompt_with_source(overrides, step, meta=meta, **where)
    if name == variant:
        return _blocks_reason(overrides, step, variant)
    if source == "override":
        return (
            f"шаг всё равно возьмёт «{name}»: этот промт выбран для шага в самом проекте, "
            "такой выбор важнее слота ноды. Поменяй промт на ноде в Студии, там меняются "
            "оба выбора сразу"
        )
    if step == "hero":
        return f"файл «{variant}» не похож на лист персонажа, шаг героя возьмёт «{name}»"
    if step == "hero_style":
        return f"файл «{variant}» не похож на стиль персонажа, шаг героя возьмёт «{name}»"
    return f"шаг возьмёт «{name}» ({pl.PROMPT_SOURCE_LABELS.get(source, source)}), а не выбранный файл"


def _blocks_reason(overrides: dict, step: str, variant: str) -> str | None:
    """Сборка из блоков (use_blocks_v2) подменяет файл шаблоном шага, как в get_project_prompt."""
    from app.services import prompt_library as pl
    from app.services.prompt_composer import STEP_CODE_TO_COMPOSE, project_uses_blocks_v2, read_step_template
    from app.services.prompt_step_presets import resolve_prompt_preset

    step_id = STEP_CODE_TO_COMPOSE.get(step)
    if step in pl.ENRICH_STEP_CODES or not step_id or not project_uses_blocks_v2(overrides):
        return None
    preset = resolve_prompt_preset(step, variant)
    if isinstance(preset, dict) and preset.get("compose") == "file":
        return None
    try:
        read_step_template(step_id)
    except FileNotFoundError:
        return None
    return (
        "в проекте промт этого шага собирается из блоков, файл варианта шаг не читает. "
        "Выключи сборку из блоков в Студии, тогда выбор заработает"
    )


async def _variant_exists(ctx: ActionContext, params: dict) -> str | None:
    """Варианта нет в библиотеке или шаг его не прочитает — отказываем до записи."""
    proj = await _get_project(ctx, params["project_id"])
    return _library_choice(
        proj if isinstance(proj, dict) else {},
        params["node_key"],
        params.get("slot_id", "main"),
        params["variant"],
    )[2]


def node_label_ru(meta: dict | None, node_key: str) -> str:
    """Подпись ноды для человека; одинаковые подписи («Работа с GPT») различаются номером из ключа."""
    from app.services.action_layer import builtin_insight as bi

    nodes = bi._canvas_nodes(meta) if isinstance(meta, dict) else []
    # У нод нового проекта подписи нет: русское название типа, а не ключ «n_excel_gpt_1».
    labels = {
        str(n["id"]): str((n.get("data") or {}).get("label") or bi._type_ru(str(n.get("type") or "")) or n["id"])
        for n in nodes
    }
    number = "".join(ch for ch in node_key if ch.isdigit())
    if node_key in labels:
        label = labels[node_key]
        twin = list(labels.values()).count(label) > 1
    else:
        # Меты без схемы нод: тип из ключа «n_excel_gpt_2» → «Работа с GPT 2».
        label = bi._type_ru(re.sub(r"^n_|_\d+$", "", node_key)) or node_key
        twin = label != node_key
    if number and twin:
        label += " " + number
    return label.strip()


def _prompt_variant_ru(
    meta: dict | None, node_key: str, step: str | None, variant: str, before: str | None
) -> str:
    from app.services import prompt_library as pl

    label = node_label_ru(meta, node_key)
    try:
        titles = pl.load_file_meta(step) if step else {}
    except Exception:  # noqa: BLE001
        titles = {}

    def title(name: str | None) -> str:
        info = titles.get(name) if isinstance(titles.get(name), dict) else {}
        return str(info.get("title") or name or "")

    tail = f" (был «{title(before)}»)" if before and before != variant else ""
    return f"На ноде «{label}» теперь промт «{title(variant)}»{tail}."


async def _node_set_disabled(ctx: ActionContext, params: dict) -> dict:
    from app.orchestrator.node_store.meta_book import VersionConflict, checked_meta_version

    proj = await _get_project(ctx, params["project_id"])
    book = _meta_book(proj)
    from app.orchestrator.actions.contracts import guard_stale_edit

    guard_stale_edit(params, book.version)
    try:
        result = book.set_disabled(
            node_key=params["node_key"],
            disabled=bool(params["disabled"]),
            version=checked_meta_version(params, book),
        )
    except VersionConflict as exc:
        raise _conflict(exc) from exc
    await ctx.call(
        "PATCH",
        f"/projects/{params['project_id']}",
        json={"meta": {"disabled_nodes": result["disabled_nodes"], "meta_version": result["version"]}},
    )
    try:
        described = await ctx.call("GET", f"/projects/{params['project_id']}/nodes/{params['node_key']}/describe")
        label = str((described or {}).get("label") or params["node_key"])
    except Exception:
        label = str(params["node_key"])
    # Без text_ru чат отвечал полями «disabled nodes: …; rollback possible: да».
    text = (
        f"Нода «{label}» выключена: при запуске конвейера она будет пропущена."
        if result["disabled"]
        else f"Нода «{label}» снова включена."
    )
    return {
        "node_key": result["node_key"],
        "disabled": result["disabled"],
        "disabled_nodes": result["disabled_nodes"],
        "text_ru": text,
    }


async def _node_run_step(ctx: ActionContext, params: dict) -> dict:
    await ctx.call(
        "POST",
        f"/projects/{params['project_id']}/steps/{_NODE_RUN_STEP}/run",
        params={"node_key": params["node_key"], "mode": "resume"},
    )
    return {"started": True, "node_key": params["node_key"]}


async def _node_run_poll(ctx: ActionContext, params: dict, started: dict) -> JobState:
    pid = params["project_id"]
    proj = await _get_project(ctx, pid)
    status = proj.get("status")
    if _is_running(status):
        return JobState(done=False, note=f"статус проекта {status}")
    error = None
    try:
        run = await ctx.call("GET", f"/projects/{pid}/run")
        for nr in (run or {}).get("node_runs") or []:
            if nr.get("node_key") == params["node_key"] and nr.get("status") == "failed":
                error = nr.get("error") or "нода завершилась с ошибкой"
    except ActionCallError:
        pass
    if error:
        return JobState(done=True, ok=False, error=error, note=f"статус проекта {status}")
    return JobState(done=True, ok=True, note=f"статус проекта {status}", result={"final_status": status})


def _node_run_estimate(params: dict) -> CostEstimate:
    key = str(params.get("node_key") or "")
    if key.endswith("_fw_report") or "_fw_report_" in key:
        return CostEstimate(0.0, 0.0, "код-нода отчёта, LLM не вызывает")
    return CostEstimate.unknown(
        "LLM-прогон ноды идёт через Vibecode; цена не откалибрована и расход не "
        "возвращается (число вызовов зависит от числа ячеек)"
    )


def _node_run_effects(params: dict) -> str:
    return (
        "Запуск шага убивает активную генерацию проекта, снимает стоп и паузу очереди. "
        f"Нода {params['node_key']} перезапишет в БД поля сцен и кадров всех ячеек; "
        "прежние значения не сохраняются."
    )


async def _node_reset_precondition(ctx: ActionContext, params: dict) -> str | None:
    steps = await ctx.call("GET", "/projects/steps/catalog")
    if params["step_code"] not in {s.get("code") for s in steps}:
        return f"неизвестный шаг {params['step_code']!r}"
    return None


async def _node_reset(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("POST", f"/projects/{params['project_id']}/steps/{params['step_code']}/reset")
    return {"step_code": params["step_code"], "status": data.get("status")}


def _specs() -> list[ActionSpec]:
    pid_only = _schema({"project_id": _PID}, ["project_id"])
    node_only = _schema({"project_id": _PID, "node_key": _NODE_KEY}, ["project_id", "node_key"])
    group_only = _schema({"group_id": _GROUP_ID}, ["group_id"])
    return [
        ActionSpec(
            id="project.stop",
            title_ru="Остановить проект",
            description_ru="Глобальный стоп проекта: прерывает активный шаг, генерацию и задачи "
            "монтажа. Отдельного стопа только монтажа нет. Частичные результаты "
            "могут остаться. Если останавливать нечего, ответ stopped=false.",
            params_schema=pid_only,
            executor=_stop_project_action,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("project", "stop"),
        ),
        ActionSpec(
            id="shot.get_menu",
            title_ru="Прочитать меню съёмки",
            description_ru="Ячейки закадра и шоты. На пустой БД старого проекта один раз "
            "подтягивает кадры из project.xlsx.",
            params_schema=pid_only,
            executor=_shot_get_menu,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("shot",),
        ),
        ActionSpec(
            id="shot.edit_cell_voiceover",
            title_ru="Изменить закадр ячейки (меню съёмки)",
            description_ru="Записывает текст закадра ячейки в БД. Прежний текст не сохраняется.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "parent_uuid": {"type": "string", "minLength": 1, "maxLength": 80},
                    "voiceover": {"type": "string", "maxLength": 20000},
                },
                ["project_id", "parent_uuid", "voiceover"],
            ),
            executor=_shot_edit_cell,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("shot", "text"),
        ),
        ActionSpec(
            id="shot.add_cell",
            title_ru="Добавить ячейку в меню съёмки",
            description_ru="Новая пустая ячейка перед ячейкой с индексом before_index (без индекса "
            "в конец), дробный sort_key. Повтор создаст ещё одну ячейку.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "before_index": {"type": "integer", "minimum": 0},
                    "voiceover": {"type": "string", "maxLength": 20000},
                },
                ["project_id"],
            ),
            executor=_shot_add_cell,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(_PRE_PROJECT,),
            tags=("shot", "structure"),
        ),
        ActionSpec(
            id="shot.edit_field",
            title_ru="Изменить поле шота (меню съёмки)",
            description_ru="Действие, персонажи, стык, сцена, площадка, крупность, движение или "
            "промты шота: запись в БД без запуска генерации.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "frame_uuid": {"type": "string", "minLength": 1, "maxLength": 80},
                    "field": {"type": "string", "enum": _SHOT_FIELDS},
                    "value": {"type": "string", "maxLength": 8000},
                },
                ["project_id", "frame_uuid", "field", "value"],
            ),
            executor=_shot_edit_field,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("shot", "text"),
        ),
        ActionSpec(
            id="group.list",
            title_ru="Каталог групп нод",
            description_ru="Встроенные и пользовательские группы нод.",
            params_schema=_schema({}, []),
            executor=_group_list,
            risk=RISK_READ,
            idempotent=True,
            scope="global",
            tags=("group",),
        ),
        ActionSpec(
            id="group.get",
            title_ru="Состав группы нод",
            description_ru="Полный spec группы: ноды, связи, промты.",
            params_schema=group_only,
            executor=_group_get,
            risk=RISK_READ,
            idempotent=True,
            scope="global",
            preconditions=(Precondition("group_exists", "Группа существует", _group_exists),),
            tags=("group",),
        ),
        ActionSpec(
            id="group.shots_report",
            title_ru="Отчёт «Кадры» (script_frames_qc)",
            description_ru="Готовый HTML-отчёт группы «Кадры» (нода fw_report): короткая сводка и "
            "ссылка на полный отчёт без сокращений. Отчёт не пересчитывается. Готового "
            "отчёта нет — собирает отчёт по кадрам и нодам с промтами (html и xlsx).",
            params_schema=pid_only,
            executor=_group_shots_report,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("group", "report"),
        ),
        ActionSpec(
            id="group.insert",
            title_ru="Вставить группу нод в канвас",
            description_ru="Добавляет ноды группы, связи, промт-варианты и флаги в канвас проекта. "
            "Запуска нод нет. Повторная вставка добавит вторую копию.",
            params_schema=_schema(
                {"project_id": _PID, "group_id": _GROUP_ID, "after": {"type": "string", "maxLength": 80}},
                ["project_id", "group_id"],
            ),
            executor=_group_insert,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                Precondition("group_exists", "Группа существует", _group_exists),
            ),
            tags=("group", "canvas"),
        ),
        ActionSpec(
            id="group.save_from_selection",
            title_ru="Сохранить выделение как группу",
            description_ru="Записывает выбранные ноды канваса как новую пользовательскую группу "
            "(файл в node_groups/). Существующую группу не перезаписывает.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "node_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 60,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": {"type": "string", "maxLength": 1000},
                    "category": {"type": "string", "maxLength": 40},
                    "group_id": _GROUP_ID,
                },
                ["project_id", "node_ids", "title"],
            ),
            executor=_group_from_selection,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(
                _PRE_PROJECT,
                Precondition("group_id_free", "Группы с таким id ещё нет", _group_id_free),
            ),
            tags=("group",),
        ),
        ActionSpec(
            id="group.update",
            title_ru="Переименовать пользовательскую группу",
            description_ru="Название, описание или категория пользовательской группы.",
            params_schema=_schema(
                {
                    "group_id": _GROUP_ID,
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": {"type": "string", "maxLength": 1000},
                    "category": {"type": "string", "maxLength": 40},
                },
                ["group_id"],
            ),
            executor=_group_update,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            scope="global",
            preconditions=(
                Precondition("group_custom", "Группа пользовательская", _group_is_custom),
                Precondition("has_patch", "Есть что менять", _has_group_patch),
            ),
            tags=("group",),
        ),
        ActionSpec(
            id="group.delete",
            title_ru="Удалить пользовательскую группу",
            description_ru="Удаляет файл пользовательской группы. Встроенные не удаляются. "
            "Ноды, уже вставленные в проекты, остаются.",
            params_schema=group_only,
            executor=_group_delete,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            scope="global",
            destructive_effects=lambda p: (
                f"Файл группы {p['group_id']} будет удалён без возможности восстановления."
            ),
            preconditions=(Precondition("group_custom", "Группа пользовательская", _group_is_custom),),
            tags=("group",),
        ),
        ActionSpec(
            id="group.restore",
            title_ru="Вернуть пользовательскую группу из снимка",
            description_ru="Восстанавливает файл пользовательской группы по snapshot_id "
            "(или archive_ref). Встроенные группы не затрагиваются.",
            params_schema=restore_params_schema(project=False),
            executor=group_restore,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            scope="global",
            destructive_effects=lambda p: (
                "Файл пользовательской группы возвращается из снимка; подтверждение нужно, "
                "другие группы не удаляются."
            ),
            preconditions=(
                Precondition("restore_ref", "Нужен snapshot_id или archive_ref", precondition_restore_ref),
            ),
            tags=("group", "restore"),
        ),
        ActionSpec(
            id="node.get_config",
            title_ru="Настройки ноды",
            description_ru="Настройки любой ноды по-русски (text_ru): модель и действует ли она, "
            "промт, включена ли нода, ветки, повторы. Для «Работа с GPT» ещё роль, "
            "режим выхода, виды выхода, режим проверки и признак принудительного "
            "Vibecode.",
            params_schema=node_only,
            executor=_node_get_config,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NODE),
            tags=("node",),
        ),
        ActionSpec(
            id="node.get_llm_route",
            title_ru="Через какую модель идёт нода",
            description_ru="Какая модель стоит на ноде и действует ли она на шаг. Для нод "
            "script_frames_qc выбор модели игнорируется: всегда Vibecode "
            "«GPT 5.6 Sol», менять нельзя. Остальным нодам модель меняет "
            "node.set_model.",
            params_schema=node_only,
            executor=_node_get_llm_route,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NODE),
            tags=("node", "model"),
        ),
        ActionSpec(
            id="node.set_config",
            title_ru="Изменить пульт оператора ноды",
            description_ru="Роль, режим и виды выхода, «брать со стрелок», снимок и режим проверки. "
            "Только конфиг ноды, запуска нет. Для нод script_frames_qc режим выхода "
            "менять нельзя.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "node_key": _NODE_KEY,
                    "role": {"type": "string", "enum": _ROLES},
                    "outputMode": {"type": "string", "enum": _OUTPUT_MODES},
                    "emitKinds": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {"type": "string", "enum": _EMIT_KINDS},
                    },
                    "takeFromEdges": {"type": "boolean"},
                    "useSnapshot": {"type": "boolean"},
                    "checkMode": {"type": "boolean"},
                    "checkFix": {"type": "boolean"},
                    "checkPromptSource": {"type": "string", "enum": ["upstream", "agent"]},
                },
                ["project_id", "node_key"],
            ),
            executor=_node_set_config,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            future_spend_note="Режим проверки (checkMode/checkFix) и выход ноды меняют число платных "
            "вызовов при следующем запуске.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition("has_changes", "Указано хотя бы одно поле", _config_has_changes),
                Precondition(
                    "output_mode_allowed",
                    "Режим выхода не меняется у нод script_frames_qc",
                    _config_output_mode_allowed,
                ),
            ),
            tags=("node", "config", "edit"),
        ),
        ActionSpec(
            id="node.set_prompt_variant",
            title_ru="Выбрать вариант промта слота ноды",
            description_ru="Привязывает файл промта из библиотеки к слоту ноды в этом проекте (meta). "
            "Сам файл промта не меняется. variant — имя файла как в библиотеке "
            "(кириллица, пробелы, .txt в имени допустимы, .md на конце можно не писать). "
            "Стиль персонажа у ноды героя — slot_id=style. Отказ, если файла нет "
            "в библиотеке шага или шаг при запуске прочитает другой файл.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "node_key": _NODE_KEY,
                    "slot_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 60,
                        "pattern": r"^[A-Za-z0-9_\-]+$",
                    },
                    "variant": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "pattern": r'^[^/\\:*?"<>|]+$',
                    },
                },
                ["project_id", "node_key", "variant"],
            ),
            executor=_node_set_prompt_variant,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition(
                    "variant_exists",
                    "Такой вариант промта есть в библиотеке и шаг его прочитает",
                    _variant_exists,
                ),
            ),
            tags=("node", "prompt", "edit"),
        ),
        ActionSpec(
            id="node.set_disabled",
            title_ru="Отключить или включить ноду",
            description_ru="Пишет ноду в meta.disabled_nodes проекта или убирает оттуда. "
            "Отключённую ноду запуск шага не выполняет.",
            params_schema=_schema(
                {"project_id": _PID, "node_key": _NODE_KEY, "disabled": {"type": "boolean"}},
                ["project_id", "node_key", "disabled"],
            ),
            executor=_node_set_disabled,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE),
            tags=("node", "config", "edit"),
        ),
        ActionSpec(
            id="node.run_step",
            title_ru="Запустить шаг на ноде «Работа с GPT»",
            description_ru="Явный старт шага ноды (режим resume): убивает активную генерацию проекта "
            "и перезаписывает в БД выход ноды по всем ячейкам. LLM идёт через Vibecode. "
            "Заблокирован, если в проекте что-то выполняется или проект под "
            "Actions-v1. Останавливается глобальным стопом проекта.",
            params_schema=node_only,
            executor=_node_run_step,
            poll=_node_run_poll,
            stop=_stop_project,
            poll_interval_s=3.0,
            risk=RISK_DESTRUCTIVE,
            spends_money=True,
            destructive=True,
            reversible=False,
            idempotent=False,
            estimator=_node_run_estimate,
            destructive_effects=_node_run_effects,
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE, _PRE_NODE_GPT),
            tags=("node", "run"),
        ),
        ActionSpec(
            id="node.reset_from_step",
            title_ru="Сбросить шаг и всё после него",
            description_ru="Стирает результаты шага и всех шагов ниже по конвейеру и пересчитывает "
            "статус проекта. Запуска нет.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "step_code": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 40,
                        "pattern": r"^[a-z0-9_]+$",
                    },
                },
                ["project_id", "step_code"],
            ),
            executor=_node_reset,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            destructive_effects=lambda p: (
                f"Данные шага {p['step_code']} и всех последующих шагов конвейера будут удалены."
            ),
            preconditions=(
                _PRE_PROJECT,
                _PRE_NOT_MANAGED,
                _PRE_IDLE,
                _PRE_NO_MONTAGE,
                Precondition("step_known", "Шаг есть в каталоге шагов", _node_reset_precondition),
            ),
            tags=("node", "reset"),
        ),
    ]


def register_group_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
