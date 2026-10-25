from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.services.action_layer import store
from app.services.action_layer.builtin_group import (
    _CONFIG_FIELDS,
    _PRE_IDLE,
    _PRE_NODE,
    _effective_project,
    _get_project,
    _is_running,
)
from app.services.action_layer.builtin_montage import _PID, _PRE_PROJECT, _base
from app.services.action_layer.context import ActionCallError, ActionContext, FileUpload, UploadRejected
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    Precondition,
)

SNAPSHOT_DIR = "action_layer_snapshots"
NOTE_SUFFIX = (
    " Прежнее значение сохраняется снимком в журнале (id возвращается в результате); откат: history.rollback."
)


@dataclass(frozen=True)
class Handler:
    kind: str
    target_of: Callable[[dict], dict]
    read: Callable[[ActionContext, dict], Awaitable[Any]]
    write: Callable[[ActionContext, dict, Any], Awaitable[None]] | None
    restorable: Callable[[dict, Any], tuple[bool, str]]
    scope_project: bool = True


def _always(ok: bool, note: str = "") -> Callable[[dict, Any], tuple[bool, str]]:
    return lambda _t, _b: (ok, note)


async def _board_frames(ctx: ActionContext, pid: int) -> list[dict]:
    board = await ctx.call("GET", _base(pid))
    return list((board or {}).get("frames") or [])


async def _graph_frame(ctx: ActionContext, pid: int, uuid_: str) -> dict | None:
    graph = await ctx.call("GET", f"/db/projects/{pid}/graph")
    for fr in (graph or {}).get("frames") or []:
        if isinstance(fr, dict) and str(fr.get("uuid") or "") == str(uuid_):
            return fr
    return None


async def _vo_read(ctx, t):
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("frame_id") or -1) == int(t["frame_id"]):
            return str(fr.get("voiceover_text") or "")
    raise RuntimeError(f"кадр frame_id={t['frame_id']} не найден на доске")


async def _vo_write(ctx, t, value):
    await ctx.call(
        "PATCH", f"{_base(t['project_id'])}/frames/{t['frame_id']}/voiceover", json={"text": value}
    )


_COVERAGE_FIELD = {
    "coverage_plan": "shot_plan",
    "coverage_action": "shot_action",
    "coverage_angle": "shot_angle",
    "coverage_move": "shot_move",
    "coverage_kind": "shot_kind",
    "coverage_stitch": "shot_stitch",
    "coverage_light": "scene_lighting",
}
_COVERAGE_RESTORABLE = frozenset({"coverage_plan", "coverage_action", "coverage_angle", "coverage_move"})


async def _cov_read(ctx, t):
    field = _COVERAGE_FIELD[t["type"]]
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("number") or 0) == int(t["frame_number"]):
            return fr.get(field)
    raise RuntimeError(f"кадра {t['frame_number']} нет на доске")


async def _cov_write(ctx, t, value):
    body = {"type": t["type"], "frame_number": t["frame_number"], t["type"].removeprefix("coverage_"): value}
    await ctx.call("POST", f"{_base(t['project_id'])}/coverage", json=body)


def _cov_restorable(t: dict, before: Any) -> tuple[bool, str]:
    if t["type"] not in _COVERAGE_RESTORABLE:
        return False, (
            "значение на доске нормализовано или производно от других полей: "
            "записано для истории, точный откат не гарантируется"
        )
    if not isinstance(before, str) or not before.strip():
        return False, "прежнее значение пустое: откат пустым значением сервер может не принять"
    return True, ""


_STRUCT_PREFIXES = ("scene_", "shot_", "anchor", "vo_scene")


async def _struct_read(ctx, t):
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("number") or 0) == int(t["frame_number"]):
            return {k: v for k, v in fr.items() if k.startswith(_STRUCT_PREFIXES)}
    raise RuntimeError(f"кадра {t['frame_number']} нет на доске")


async def _cell_read(ctx, t):
    fr = await _graph_frame(ctx, t["project_id"], t["parent_uuid"])
    if fr is None:
        raise RuntimeError(f"кадр {t['parent_uuid']!r} не найден")
    return str(fr.get("voiceover_text") or "")


async def _cell_write(ctx, t, value):
    await ctx.call(
        "PATCH",
        f"/db/projects/{t['project_id']}/shot-menu/cell",
        json={"parent_uuid": t["parent_uuid"], "voiceover": value},
    )


_FIELD_COLUMN = {"img_prompt": "image_prompt", "video_prompt": "animation_prompt", "vo": "voiceover_text"}
_FIELD_ATTR = {
    "action": "shot01_action",
    "characters": "characters",
    "stitch": "scene_transition",
    "scene": "id_scene",
}
_FIELD_CAMERA = {"size": "крупность", "move": "движение"}


async def _field_read(ctx, t):
    fr = await _graph_frame(ctx, t["project_id"], t["frame_uuid"])
    if fr is None:
        raise RuntimeError(f"кадр {t['frame_uuid']!r} не найден")
    field = t["field"]
    attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
    if field in _FIELD_COLUMN:
        return fr.get(_FIELD_COLUMN[field])
    if field in _FIELD_ATTR:
        return attrs.get(_FIELD_ATTR[field])
    if field in _FIELD_CAMERA:
        cs = attrs.get("camera_subdivide") if isinstance(attrs.get("camera_subdivide"), dict) else {}
        return cs.get(_FIELD_CAMERA[field])
    if field == "set":
        cs = attrs.get("camera_subdivide") if isinstance(attrs.get("camera_subdivide"), dict) else {}
        return {"place": attrs.get("place"), "camera_set": cs.get("набор")}
    raise RuntimeError(f"поле {field!r} не читается из графа")


async def _field_write(ctx, t, value):
    await ctx.call(
        "PATCH",
        f"/db/projects/{t['project_id']}/shot-menu/shot-field",
        json={"frame_uuid": t["frame_uuid"], "field": t["field"], "value": "" if value is None else value},
    )


def _field_restorable(t: dict, before: Any) -> tuple[bool, str]:
    if t["field"] == "set":
        return False, "поле «набор» хранится в двух местах: записано для истории, откат вручную"
    if before is not None and not isinstance(before, str):
        return False, "прежнее значение не строка (список): записано для истории, откат вручную"
    return True, ""


async def _prompt_read(ctx, t):
    from app.services.action_layer.builtin_wave3 import _read_prompt

    data = await _read_prompt(ctx, t)
    if data is None:
        raise RuntimeError("промт не найден")
    return str(data.get("content") or "")


async def _prompt_write(ctx, t, value):
    from app.services.action_layer.builtin_wave3 import _prompt_path

    await ctx.call("PUT", _prompt_path(t), json={"content": value})


_FILE_SLOTS = {
    "voice": ("montage_voice_path", "upload-voice", "voice"),
    "music": ("bgm_path", "upload-music", "music"),
}


def _data_root() -> Path:
    from app.settings import settings

    return Path(settings.data_dir).resolve()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _file_read(ctx, t):
    meta_key = _FILE_SLOTS[t["slot"]][0]
    proj = await _get_project(ctx, t["project_id"])
    raw = (proj.get("meta") or {}).get(meta_key)
    if not raw:
        return {"exists": False}
    path = Path(str(raw))
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return {"exists": False, "meta_path": str(raw), "missing_on_disk": True}
    root = _data_root()
    if root not in real.parents or not real.is_file():
        return {"exists": False, "meta_path": str(raw), "outside_data_dir": True}
    return {
        "exists": True,
        "name": real.name,
        "size": real.stat().st_size,
        "sha256": _sha256_file(real),
        "real_path": str(real),
    }


def _backup_file(state: dict) -> dict:
    src = Path(state["real_path"])
    folder = _data_root() / SNAPSHOT_DIR / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    shutil.copy2(src, dst)
    if _sha256_file(dst) != state["sha256"]:
        raise RuntimeError("копия прежнего файла не совпала с оригиналом по sha256")
    return {
        "exists": True,
        "name": src.name,
        "size": state["size"],
        "sha256": state["sha256"],
        "backup": dst.relative_to(_data_root()).as_posix(),
    }


def _public_file_state(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "real_path"}


async def _file_write(ctx, t, value):
    if not isinstance(value, dict) or not value.get("backup"):
        raise RuntimeError("в снимке нет копии файла")
    _meta_key, endpoint, policy = _FILE_SLOTS[t["slot"]]
    up = FileUpload.from_data_path(policy, value["backup"])
    if hashlib.sha256(up.content).hexdigest() != value.get("sha256"):
        raise UploadRejected("копия файла изменена после снимка (sha256): откат отклонён")
    await ctx.call("POST", f"{_base(t['project_id'])}/{endpoint}", files={"file": up}, timeout=300.0)


def _file_restorable(_t: dict, before: Any) -> tuple[bool, str]:
    if isinstance(before, dict) and before.get("backup"):
        return True, ""
    return False, "прежнего файла не было: удалить файл эндпоинтом нельзя, откат невозможен"


_AGENT_KIND = "check_agent"
_AGENT_CFG = ("checkMode", "checkPromptSource", "transport")
_AGENT_SOURCES = ("upload", "builtin", "none")


def _agent_base(t: dict) -> str:
    return f"/projects/{t['project_id']}/gpt-operator/{t['node_key']}"


async def _agent_read(ctx, t):
    data = await ctx.call("GET", f"{_agent_base(t)}/resolve")
    cfg = (data or {}).get("config") if isinstance(data, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    try:
        view = await ctx.call("GET", f"{_agent_base(t)}/check-agent")
    except ActionCallError as exc:
        if exc.status != 404:
            raise
        view = None
    view = view if isinstance(view, dict) else {}
    source = str(view.get("source") or "none")
    state: dict[str, Any] = {
        "source": source if source in _AGENT_SOURCES else "none",
        "config": {key: cfg.get(key) for key in _AGENT_CFG},
    }
    if state["source"] == "upload":
        text = str(view.get("text") or "")
        state.update(
            file_name=str(view.get("fileName") or "check_agent.txt"),
            chars=len(text),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
        )
    elif state["source"] == "builtin":
        state["builtin"] = view.get("step") or view.get("fileName")
    return state


async def _agent_write(ctx, t, value):
    if not isinstance(value, dict) or value.get("source") not in _AGENT_SOURCES:
        raise RuntimeError("снимок агента проверки повреждён")
    if value["source"] == "upload":
        # Текст агента лежит в самом снимке (до 1 МБ, как и лимит загрузки агента).
        content = str(value.get("text") or "").encode("utf-8")
        if not content or hashlib.sha256(content).hexdigest() != value.get("sha256"):
            raise UploadRejected("текст агента в снимке не совпал с sha256: откат отклонён")
        up = FileUpload.build("check_agent", str(value.get("file_name") or "check_agent.txt"), content)
        await ctx.call("POST", f"{_agent_base(t)}/check-agent", files={"file": up}, timeout=120.0)
    else:
        await ctx.call("DELETE", f"{_agent_base(t)}/check-agent")
    cfg = value.get("config") if isinstance(value.get("config"), dict) else {}
    body = {key: cfg[key] for key in _AGENT_CFG if cfg.get(key) is not None}
    if body:
        await ctx.call("PATCH", _agent_base(t), json=body)


def _agent_restorable(_t: dict, before: Any) -> tuple[bool, str]:
    if not isinstance(before, dict) or before.get("source") not in _AGENT_SOURCES:
        return False, "прежний агент проверки не прочитан, откат невозможен"
    if before["source"] == "upload" and not before.get("text"):
        return False, "текста прежнего агента нет, откат невозможен"
    return True, ""


async def _variant_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    slots = (proj.get("meta") or {}).get("prompt_slot_variants") or {}
    node = slots.get(t["node_key"]) if isinstance(slots, dict) else None
    if not isinstance(node, dict):
        return None
    return node.get(t["slot_id"])


async def _variant_write(ctx, t, value):
    await ctx.call(
        "PATCH",
        f"/projects/{t['project_id']}",
        json={"meta": {"prompt_slot_variants": {t["node_key"]: {t["slot_id"]: value}}}},
    )


async def _config_read(ctx, t):
    data = await ctx.call("GET", f"/projects/{t['project_id']}/gpt-operator/{t['node_key']}/resolve")
    cfg = (data or {}).get("config") if isinstance(data, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    return {key: cfg.get(key) for key in _CONFIG_FIELDS}


async def _config_write(ctx, t, value):
    if not isinstance(value, dict):
        raise RuntimeError("снимок конфига ноды повреждён")
    body = {key: value[key] for key in _CONFIG_FIELDS if key in value and value[key] is not None}
    await ctx.call("PATCH", f"/projects/{t['project_id']}/gpt-operator/{t['node_key']}", json=body)


async def _disabled_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    cur = [str(item) for item in ((proj.get("meta") or {}).get("disabled_nodes") or [])]
    return str(t["node_key"]) in cur


async def _disabled_write(ctx, t, value):
    proj = await _get_project(ctx, t["project_id"])
    cur = [str(item) for item in ((proj.get("meta") or {}).get("disabled_nodes") or [])]
    key = str(t["node_key"])
    updated = (
        cur if (value and key in cur) else ([*cur, key] if value else [item for item in cur if item != key])
    )
    await ctx.call("PATCH", f"/projects/{t['project_id']}", json={"meta": {"disabled_nodes": updated}})


async def _assemble_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    nsp = (proj.get("meta") or {}).get("node_step_params") or {}
    bucket = nsp.get("assemble") if isinstance(nsp, dict) else None
    return dict(bucket) if isinstance(bucket, dict) else {}


async def _assemble_write(ctx, t, value):
    proj = await _get_project(ctx, t["project_id"])
    nsp = (proj.get("meta") or {}).get("node_step_params") or {}
    current = nsp.get("assemble") if isinstance(nsp, dict) else None
    current = current if isinstance(current, dict) else {}
    wanted = dict(value) if isinstance(value, dict) else {}
    patch = dict(wanted)
    for key in current:
        if key not in wanted:
            patch[key] = None
    await ctx.call(
        "PATCH", f"/projects/{t['project_id']}", json={"meta": {"node_step_params": {"assemble": patch}}}
    )


async def _node_meta(ctx, t) -> dict:
    from app.services import node_edit as ne

    proj = await _effective_project(ctx, t["project_id"])
    meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
    ne.require_node(meta, t["node_key"])
    return meta


def _node_path(t: dict, suffix: str) -> str:
    return f"/projects/{t['project_id']}/nodes/{t['node_key']}/{suffix}"


def _node_target(p: dict) -> dict:
    return {"project_id": p["project_id"], "node_key": p["node_key"]}


async def _model_read(ctx, t):
    from app.services import node_edit as ne

    meta = await _node_meta(ctx, t)
    return ne.canonical_model_id(ne.explicit_model_id(ne.find_node(meta, t["node_key"])))


async def _model_write(ctx, t, value):
    from app.services import node_edit as ne

    meta = await _node_meta(ctx, t)
    current = ne.canonical_model_id(ne.explicit_model_id(ne.find_node(meta, t["node_key"])))
    entry = ne.revert_target(meta, t["node_key"], "model")
    before = ne.canonical_model_id(((entry or {}).get("before") or {}).get("model_id"))
    after = ne.canonical_model_id(((entry or {}).get("after") or {}).get("model_id"))
    if entry is not None and before == value and after == current:
        # Тот же шаг, что «верни прежнюю модель»: история ноды снимает правку со стека,
        # и следующий откат пойдёт глубже, а не вернёт модель обратно.
        await ctx.call("POST", _node_path(t, "model/revert"))
        return
    await ctx.call("PATCH", _node_path(t, "model"), json={"model_id": value, "allow_costlier": True})


def _params_target(p: dict) -> dict:
    from app.services import node_edit as ne

    names = sorted({ne.raw_param_name(k) for k in ne.PARAM_NAMES if p.get(k) is not None})
    edges = sorted(str(b["edge_id"]) for b in p.get("branches") or [] if isinstance(b, dict))
    return {"project_id": p["project_id"], "node_key": p["node_key"], "params": names, "edges": edges}


async def _params_read(ctx, t):
    from app.services import node_edit as ne

    meta = await _node_meta(ctx, t)
    return ne.raw_params(meta, t["node_key"], list(t.get("params") or []), t.get("edges"))


async def _params_write(ctx, t, value):
    from app.services import node_edit as ne

    meta = await _node_meta(ctx, t)
    if ne.params_entry_restores(ne.revert_target(meta, t["node_key"], "params"), value):
        # Откат последней правки идёт через историю ноды, чтобы следующий возврат шёл глубже.
        await ctx.call("POST", _node_path(t, "params/revert"))
        return
    if not isinstance(value, dict):
        raise RuntimeError("снимок настроек ноды повреждён")
    await ctx.call("POST", _node_path(t, "params/restore"), json={"values": value})


def _settings_target(p: dict) -> dict:
    from app.services.action_layer.builtin_wave5 import _SETTINGS_KEYS

    keys = {k for k in _SETTINGS_KEYS if k in p}
    if "image_generator" in keys:
        # Смена генератора подрезает разрешение картинок: откат возвращает оба поля.
        keys.add("image_resolution")
    return {"project_id": p["project_id"], "keys": sorted(keys)}


async def _settings_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    return {k: proj.get(k) for k in t["keys"]}


async def _settings_write(ctx, t, value):
    from app.services.action_layer import project_gen_settings as pgs

    if not isinstance(value, dict):
        raise RuntimeError("снимок настроек проекта повреждён")
    body = {k: value[k] for k in t["keys"] if k in value}
    data = await ctx.call("PATCH", f"/projects/{t['project_id']}", json=body)
    aid = body.get("aspect_ratio")
    if aid and pgs.aspect_id(aid):
        await pgs.sync_prompt_aspect(
            ctx, t["project_id"], (data or {}).get("prompt_overrides"), pgs.aspect_id(aid)
        )


HANDLERS: dict[str, Handler] = {
    "montage.set_voiceover": Handler(
        "voiceover",
        lambda p: {"project_id": p["project_id"], "frame_id": p["frame_id"]},
        _vo_read,
        _vo_write,
        _always(True),
    ),
    "montage.set_coverage": Handler(
        "coverage",
        lambda p: {"project_id": p["project_id"], "frame_number": p["frame_number"], "type": p["type"]},
        _cov_read,
        _cov_write,
        _cov_restorable,
    ),
    "montage.set_anchors": Handler(
        "scene_structure",
        lambda p: {"project_id": p["project_id"], "frame_number": p["frame_number"]},
        _struct_read,
        None,
        _always(
            False,
            "структурная правка: шоты пересобираются, номера кадров "
            "могут сдвинуться; прежнее состояние записано для истории",
        ),
    ),
    "montage.set_scene_chain": Handler(
        "scene_structure",
        lambda p: {"project_id": p["project_id"], "frame_number": p["frame_number"]},
        _struct_read,
        None,
        _always(False, "структурная правка: шоты пересобираются; прежнее состояние записано для истории"),
    ),
    "shot.edit_cell_voiceover": Handler(
        "cell_voiceover",
        lambda p: {"project_id": p["project_id"], "parent_uuid": p["parent_uuid"]},
        _cell_read,
        _cell_write,
        _always(True),
    ),
    "shot.edit_field": Handler(
        "shot_field",
        lambda p: {"project_id": p["project_id"], "frame_uuid": p["frame_uuid"], "field": p["field"]},
        _field_read,
        _field_write,
        _field_restorable,
    ),
    "montage.upload_voice": Handler(
        "file_voice",
        lambda p: {"project_id": p["project_id"], "slot": "voice"},
        _file_read,
        _file_write,
        _file_restorable,
    ),
    "montage.upload_music": Handler(
        "file_music",
        lambda p: {"project_id": p["project_id"], "slot": "music"},
        _file_read,
        _file_write,
        _file_restorable,
    ),
    "prompt.save_master": Handler(
        "master_prompt",
        lambda p: {"step_code": p["step_code"], "name": p["name"]},
        _prompt_read,
        _prompt_write,
        _always(True),
        scope_project=False,
    ),
    "node.set_prompt_variant": Handler(
        "prompt_slot",
        lambda p: {
            "project_id": p["project_id"],
            "node_key": p["node_key"],
            "slot_id": p.get("slot_id") or "main",
        },
        _variant_read,
        _variant_write,
        _always(True),
    ),
    "node.set_config": Handler(
        "node_config",
        lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _config_read,
        _config_write,
        _always(True),
    ),
    "node.set_disabled": Handler(
        "node_disabled",
        lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _disabled_read,
        _disabled_write,
        _always(True),
    ),
    "montage.set_assemble_params": Handler(
        "assemble_params",
        lambda p: {"project_id": p["project_id"]},
        _assemble_read,
        _assemble_write,
        _always(True),
    ),
    "node.set_model": Handler("node_model", _node_target, _model_read, _model_write, _always(True)),
    "node.set_model_costly": Handler("node_model", _node_target, _model_read, _model_write, _always(True)),
    "node.set_params": Handler("node_params", _params_target, _params_read, _params_write, _always(True)),
    "node.set_params_costly": Handler(
        "node_params", _params_target, _params_read, _params_write, _always(True)
    ),
    "project.patch_settings": Handler(
        "project_settings", _settings_target, _settings_read, _settings_write, _always(True)
    ),
    "project.patch_settings_costly": Handler(
        "project_settings", _settings_target, _settings_read, _settings_write, _always(True)
    ),
    "node.upload_check_agent": Handler(
        _AGENT_KIND,
        lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _agent_read,
        _agent_write,
        _agent_restorable,
    ),
    "node.clear_check_agent": Handler(
        _AGENT_KIND,
        lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _agent_read,
        _agent_write,
        _agent_restorable,
    ),
}

_FILE_KINDS = frozenset({"file_voice", "file_music"})


def _compare_value(kind: str, value: Any) -> Any:
    if kind in _FILE_KINDS and isinstance(value, dict):
        return {"exists": value.get("exists"), "sha256": value.get("sha256")}
    if kind == _AGENT_KIND and isinstance(value, dict):
        return {"source": value.get("source"), "sha256": value.get("sha256"), "config": value.get("config")}
    return value


def _wrap_executor(action_id: str, h: Handler, orig):
    async def executor(ctx: ActionContext, params: dict) -> dict:
        if not isinstance(ctx, ActionContext):
            return await orig(ctx, params)
        target = h.target_of(params)
        try:
            before = await h.read(ctx, target)
        except Exception as exc:
            raise RuntimeError(
                f"не удалось прочитать прежнее значение, правка не начата: {type(exc).__name__}: {exc}"
            ) from exc
        stored_before = before
        if h.kind in _FILE_KINDS and isinstance(before, dict) and before.get("exists"):
            stored_before = _backup_file(before)
        elif isinstance(before, dict):
            stored_before = _public_file_state(before)
        result = await orig(ctx, params)
        try:
            after = await h.read(ctx, target)
        except Exception:
            after = None
        if isinstance(after, dict):
            after = _public_file_state(after)
        ok, note = h.restorable(target, stored_before)
        pid = target.get("project_id") if h.scope_project else None
        sid = await store.save_snapshot(
            plan_id=ctx.plan_id or "",
            step_index=ctx.step_index,
            action_id=action_id,
            project_id=pid,
            kind=h.kind,
            target=target,
            before=stored_before,
            after=after,
            restorable=bool(ok and h.write is not None),
            note=note,
            actor=ctx.operator,
        )
        out = dict(result) if isinstance(result, dict) else {"result": result}
        out["snapshot_id"] = sid
        out["rollback_possible"] = bool(ok and h.write is not None)
        return out

    executor.history_wrapped = True
    return executor


def wrap_catalog(registry: ActionRegistry) -> list[str]:
    done: list[str] = []
    for action_id, h in HANDLERS.items():
        spec = registry.find(action_id)
        if spec is None or getattr(spec.executor, "history_wrapped", False):
            continue
        desc = spec.description_ru
        for old in (
            " Прежний текст не сохраняется.",
            " Прежняя версия остаётся в истории промта, но восстановить её из каталога нельзя.",
        ):
            desc = desc.replace(old, "")
        effects = spec.destructive_effects
        if action_id == "prompt.save_master" and effects is not None:
            inner = effects

            def effects(p, _inner=inner):
                return (
                    _inner(p).replace("; прежняя версия остаётся только в истории версий Studio.", "")
                    + "; прежний текст сохраняется снимком слоя, откат: history.rollback."
                )

        registry.unregister(action_id)
        registry.register(
            replace(
                spec,
                executor=_wrap_executor(action_id, h, spec.executor),
                description_ru=desc.rstrip() + NOTE_SUFFIX,
                destructive_effects=effects,
            )
        )
        done.append(action_id)
    return done


def _snapshot_json(row) -> dict:
    return {
        "snapshot_id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "action_id": row.action_id,
        "plan_id": row.plan_id,
        "project_id": row.project_id,
        "kind": row.kind,
        "target": row.target,
        "before": _clip(row.before),
        "after": _clip(row.after),
        "restorable": bool(row.restorable),
        "note": row.note,
        "restored_at": row.restored_at.isoformat() if row.restored_at else None,
    }


def _clip(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, dict) and isinstance(value.get("text"), str) and len(value["text"]) > limit:
        return {**value, "text": value["text"][:limit] + "…"}
    return value


async def _list_snapshots(ctx: ActionContext, params: dict) -> dict:
    rows = await store.list_snapshots(params.get("project_id"), limit=params.get("limit", 20))
    return {"count": len(rows), "snapshots": [_snapshot_json(r) for r in rows]}


async def _pre_rollback(ctx: ActionContext, params: dict) -> str | None:
    row = await store.get_snapshot(params["snapshot_id"])
    if row is None:
        return f"снимка {params['snapshot_id']} нет"
    if row.restored_at is not None:
        return "этот снимок уже откатан"
    if not row.restorable:
        return row.note or "для этого снимка автоматический откат невозможен"
    h = _handler_for_kind(row.kind)
    if h is None or h.write is None:
        return f"откат вида {row.kind!r} не поддерживается"
    if row.project_id is not None:
        proj = await _get_project(ctx, row.project_id)
        if _is_running(proj.get("status")):
            return f"в проекте выполняется шаг (статус {proj.get('status')})"
    return None


def _handler_for_kind(kind: str) -> Handler | None:
    for h in HANDLERS.values():
        if h.kind == kind:
            return h
    return None


async def _rollback(ctx: ActionContext, params: dict) -> dict:
    row = await store.get_snapshot(params["snapshot_id"])
    if row is None or row.restored_at is not None or not row.restorable:
        raise RuntimeError("снимок недоступен для отката")
    h = _handler_for_kind(row.kind)
    if h is None or h.write is None:
        raise RuntimeError(f"откат вида {row.kind!r} не поддерживается")
    target = dict(row.target or {})
    current_full = await h.read(ctx, target)
    current = _public_file_state(current_full) if isinstance(current_full, dict) else current_full
    if not params.get("force") and _compare_value(h.kind, current) != _compare_value(h.kind, row.after):
        if h.kind == "master_prompt":
            raise ActionCallError(409, "текст мастер-промта изменился после снимка: откат отклонён")
        raise RuntimeError(
            "значение изменилось после правки (его поправили ещё раз): откат "
            "перезатёр бы чужую правку; повторите с force=true, если это нужно"
        )
    saved = current
    if h.kind in _FILE_KINDS and isinstance(current_full, dict) and current_full.get("exists"):
        saved = _backup_file(current_full)
    await h.write(ctx, target, row.before)
    reverse_id = await store.save_snapshot(
        plan_id=ctx.plan_id or "",
        step_index=ctx.step_index,
        action_id="history.rollback",
        project_id=row.project_id,
        kind=row.kind,
        target=target,
        before=saved,
        after=row.before,
        restorable=h.write is not None and h.restorable(target, saved)[0],
        note=f"откат снимка {row.id}",
        actor=ctx.operator,
    )
    await store.mark_snapshot_restored(row.id, ctx.operator, plan_id=ctx.plan_id or "")
    return {
        "restored_snapshot_id": row.id,
        "action_id": row.action_id,
        "kind": row.kind,
        "reverse_snapshot_id": reverse_id,
        "text_ru": await _rollback_text(ctx, row.kind, target, row.before, current),
    }


def agent_state_ru(state: Any) -> str:
    """Агент проверки из снимка простыми словами: свой файл, встроенный или никакого."""
    if not isinstance(state, dict):
        return "неизвестно"
    if state.get("source") == "upload":
        return f"свой агент «{state.get('file_name') or 'check_agent.txt'}» ({state.get('chars')} символов)"
    if state.get("source") == "builtin":
        return "встроенный агент проверки"
    return "без агента проверки"


_SETTING_RU = {
    "title": "название",
    "topic": "тема",
    "hero_mode": "режим героя",
    "image_generator": "модель картинок",
    "image_resolution": "разрешение картинок",
    "video_generator": "модель видео",
    "video_resolution": "разрешение видео",
    "aspect_ratio": "формат кадра",
}


def _setting_value_ru(key: str, value: Any) -> str:
    """Название из списков Studio вместо id: «GPT Image 2», 2K, 9:16."""
    from app.generation_options import (
        ASPECT_RATIOS_BY_ID,
        IMAGE_GENERATORS_BY_ID,
        IMAGE_RESOLUTIONS_BY_ID,
        VIDEO_GENERATORS_BY_ID,
        VIDEO_RESOLUTIONS_BY_ID,
    )

    if value in (None, ""):
        return "по умолчанию"
    choices = {
        "image_generator": IMAGE_GENERATORS_BY_ID,
        "image_resolution": IMAGE_RESOLUTIONS_BY_ID,
        "video_generator": VIDEO_GENERATORS_BY_ID,
        "video_resolution": VIDEO_RESOLUTIONS_BY_ID,
        "aspect_ratio": ASPECT_RATIOS_BY_ID,
    }.get(key, {})
    choice = choices.get(str(value))
    if choice is None:
        return f"«{value}»" if key in ("title", "topic") else str(value)
    return f"«{choice.label.lstrip('+ ').strip()}»" if key.endswith("_generator") else choice.label


def _settings_rollback_text(before: Any) -> str:
    values = before if isinstance(before, dict) else {}
    parts = [f"{_SETTING_RU.get(k, k)} {_setting_value_ru(k, v)}" for k, v in values.items()]
    return (
        ("Вернул прежние настройки проекта: " + ", ".join(parts) + ". Этот откат тоже можно отменить.")
        if parts
        else "Вернул прежние настройки проекта."
    )


async def _rollback_text(ctx: ActionContext, kind: str, target: dict, before: Any, after: Any) -> str:
    if kind == "project_settings":
        return _settings_rollback_text(before)
    if kind not in ("prompt_slot", "node_model", "node_params", _AGENT_KIND) or (not target.get("node_key")):
        return "Правку откатил: вернул прежнее значение. Этот откат тоже можно отменить."
    from app.orchestrator.agents.approval_steps import variant_title
    from app.services import node_edit as ne
    from app.services.action_layer.builtin_group import node_label_ru

    try:
        meta = (await _get_project(ctx, target.get("project_id"))).get("meta")
    except Exception:  # noqa: BLE001
        meta = None
    label = node_label_ru(meta, str(target["node_key"]))
    if kind == "node_model":
        key = str(target["node_key"])
        now = ne.model_ru(before, meta, key)
        was = ne.model_ru(after, meta, key)
        if before and after:
            return f"На ноде «{label}» вернул модель {now} (была {was})."
        # Модель по умолчанию называем по имени: «по умолчанию (GPT 5.6 Sol)».
        return f"На ноде «{label}» вернул модель {now}, до этого была модель {was}."
    if kind == "node_params":
        names = ", ".join(ne.PARAM_TITLE_RU.get(k, k) for k in target.get("params") or [])
        return f"На ноде «{label}» вернул прежние настройки ({names})."
    if kind == _AGENT_KIND:
        cfg = before.get("config") if isinstance(before, dict) else None
        mode = ""
        if isinstance(cfg, dict) and cfg.get("checkMode") is not None:
            mode = " Режим проверки " + ("включён." if cfg.get("checkMode") else "выключен.")
        return (
            f"На ноде «{label}» вернул прежнего агента проверки: {agent_state_ru(before)} "
            f"(был {agent_state_ru(after)}).{mode} Этот возврат тоже можно отменить."
        )
    now = variant_title(str(before)) if before else "«по умолчанию»"
    was = f" (был {variant_title(str(after))})" if after else ""
    return f"На ноде «{label}» вернул прежний промт {now}{was}."


async def last_agent_snapshot(project_id: int, node_key: str):
    """Последний снимок смены агента проверки ноды (загрузка, сброс или их откат)."""
    rows = await store.list_snapshots(int(project_id), limit=200)
    for row in rows:
        target = row.target if isinstance(row.target, dict) else {}
        if row.kind == _AGENT_KIND and str(target.get("node_key") or "") == node_key:
            return row
    return None


async def _pre_agent_revert(ctx: ActionContext, params: dict) -> str | None:
    from app.services.action_layer.builtin_group import node_label_ru

    row = await last_agent_snapshot(params["project_id"], params["node_key"])
    if row is None or row.restored_at is not None:
        try:
            meta = (await _get_project(ctx, params["project_id"])).get("meta")
        except Exception:  # noqa: BLE001
            meta = None
        return (
            f"у ноды «{node_label_ru(meta, params['node_key'])}» нет сохранённого прежнего "
            "агента проверки: его не меняли через чат"
        )
    if not row.restorable:
        return row.note or "для этой смены агента возврат невозможен"
    current = await _agent_read(ctx, dict(row.target or {}))
    if _compare_value(_AGENT_KIND, _public_file_state(current)) != _compare_value(_AGENT_KIND, row.after):
        return (
            "агента проверки уже поменяли после последней смены через чат: возврат "
            "перезаписал бы это изменение"
        )
    return None


async def _revert_agent(ctx: ActionContext, params: dict) -> dict:
    row = await last_agent_snapshot(params["project_id"], params["node_key"])
    if row is None:
        raise RuntimeError("снимка агента проверки нет")
    out = await _rollback(ctx, {"snapshot_id": row.id})
    return {
        **out,
        "node_key": params["node_key"],
        "agent_before": agent_state_ru(row.after),
        "agent_after": agent_state_ru(row.before),
    }


def history_specs() -> list[ActionSpec]:
    S = ActionSpec
    return [
        S(
            id="node.revert_check_agent",
            title_ru="Вернуть прежнего агента проверки ноды",
            description_ru="Возвращает агента проверки ноды «Работа с GPT», который стоял до "
            "последней загрузки или сброса агента через чат: свой файл агента, "
            "встроенного агента или никакого, вместе с прежним режимом проверки. "
            "Отказ, если агента после этого меняли в Студии. Сам возврат тоже "
            "можно отменить повтором этого действия.",
            params_schema={
                "type": "object",
                "properties": {
                    "project_id": _PID,
                    "node_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                        "pattern": r"^[A-Za-z0-9_.:\-]+$",
                        "description": "id ноды на канвасе",
                    },
                },
                "required": ["project_id", "node_key"],
                "additionalProperties": False,
            },
            executor=_revert_agent,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            reversible=True,
            future_spend_note="Следующие запуски проверки пойдут с прежним агентом и прежним "
            "режимом проверки.",
            preconditions=(
                _PRE_PROJECT,
                _PRE_IDLE,
                _PRE_NODE,
                Precondition(
                    "agent_revert_possible",
                    "Есть снимок прежнего агента, агента после него не меняли",
                    _pre_agent_revert,
                ),
            ),
            tags=("node", "check", "rollback"),
        ),
        S(
            id="history.list_snapshots",
            title_ru="Показать прежние значения (снимки правок)",
            description_ru="Список снимков, которые слой сохранил перед правками закадра, покрытия, "
            "полей шота, файлов озвучки и музыки, мастер-промтов, моделей и настроек нод, "
            "настроек проекта: что было, что стало "
            "и можно ли откатить. Читает журнал слоя, проект не меняет.",
            params_schema={
                "type": "object",
                "properties": {
                    "project_id": _PID,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": [],
                "additionalProperties": False,
            },
            executor=_list_snapshots,
            risk=RISK_READ,
            idempotent=True,
            scope="global",
            tags=("history", "rollback"),
        ),
        S(
            id="history.rollback",
            title_ru="Откатить правку по журналу",
            description_ru="Возвращает прежнее значение из снимка тем же эндпоинтом, которым шла "
            "правка. Если значение уже поменяли после правки, откат отклоняется "
            "(текущее содержимое сверяется с after снимка и отвечает 409; "
            "force=true перезапишет). Сам откат тоже оставляет снимок, поэтому обратим. "
            "Якоря, цепочка сцены и поле «набор» автоматически не откатываются.",
            params_schema={
                "type": "object",
                "properties": {
                    "snapshot_id": {"type": "integer", "minimum": 1},
                    "force": {"type": "boolean"},
                },
                "required": ["snapshot_id"],
                "additionalProperties": False,
            },
            executor=_rollback,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            scope="global",
            destructive_effects=lambda p: (
                "Вернётся значение, которое было до правки; текущее значение сохранится в журнале "
                "правок."
                + (" Проверка «значение не меняли после правки» отключена." if p.get("force") else "")
            ),
            preconditions=(
                Precondition(
                    "snapshot_restorable", "Снимок существует, не откатан и поддерживает откат", _pre_rollback
                ),
            ),
            tags=("history", "rollback"),
        ),
    ]
