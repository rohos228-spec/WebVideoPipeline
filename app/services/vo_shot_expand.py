"""Визуальные шоты внутри ячейки разбивки.

Frame.voiceover_text = результат ноды разбивки. Не пишем, не режем.
Копия ячейки режется по шотам в attrs.camera_subdivide.vo_shot —
так же, как раньше резался закадр (split_text_into_parts).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_v2 import insert_frame_after
from app.services.scene_design.camera_expand import (
    already_subdivided,
    renumber_frames_by_sort_key,
    split_text_into_parts,
    vo_chunk_is_dangling,
)

_ATTR_KEY = "camera_subdivide"
_VO_SHOT_KEY = "vo_shot"
_CLAUSE_RE = re.compile(r"(?<=[.!?…])\s+")
_VO_CHARS_PER_SEC = 14.0
_MIN_SEC = 2.0
_MAX_SHOTS = 3


def is_shot_child(frame: Any) -> bool:
    """Визуальный дочерний кадр покрытия, не VO-родитель."""
    return str(_cs(frame).get("role") or "") == "shot"


_COVERAGE_SHOT_RE = re.compile(r"^(.+)-K(\d+)$")


def parent_still_suppressed(frame: Any) -> bool:
    """Явная роль «родитель» на доске: still снят, кадр остаётся в VO-сцене."""
    cs = _cs(frame)
    raw = cs.get("use_parent_still")
    if raw is False or str(raw).strip().lower() in {"0", "false", "no", "off"}:
        return True
    return str(cs.get("coverage_kind") or "").strip().lower() == "parent"


def uses_parent_still(frame: Any) -> bool:
    """Вешать PNG родителя в генерацию / рефы доски."""
    if parent_still_suppressed(frame):
        return False
    if str(_cs(frame).get("coverage_kind") or "").strip().lower() == "child":
        return True
    return is_shot_child(frame)


def coverage_shot_id(frame: Any) -> str:
    """id шота покрытия: ``кадры[0].id`` или ``camera_subdivide.shot_id``."""
    planned = planned_shots_from_attrs(frame)
    if planned:
        sid = str(planned[0].get("id") or "").strip()
        if sid:
            return sid
    return str(_cs(frame).get("shot_id") or "").strip()


def parse_coverage_shot(shot_id: str) -> tuple[str, int] | None:
    m = _COVERAGE_SHOT_RE.match((shot_id or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def coverage_parent_shot_id(frame: Any) -> str:
    """Родитель покрытия: явный parent_id из таблицы T/X, иначе prefix-K1."""
    cs = _cs(frame)
    explicit = str(cs.get("coverage_parent_id") or "").strip()
    if explicit:
        return explicit
    planned = planned_shots_from_attrs(frame)
    if planned:
        pid = str(planned[0].get("parent_id") or "").strip()
        if pid:
            return pid
    if cs.get("scene_split"):
        return ""
    parsed = parse_coverage_shot(coverage_shot_id(frame))
    if parsed is None or parsed[1] < 2:
        return ""
    return f"{parsed[0]}-K1"


def find_coverage_parent_frame(frames: list[Any], child: Any) -> Any | None:
    """Still-родитель покрытия. Без роли ``coverage_kind=child`` K2/K3 берут K1 своей ячейки."""
    if parent_still_suppressed(child):
        return None
    child_uid = str(getattr(child, "uuid", "") or "")
    cs = _cs(child)
    explicit_kind = str(cs.get("coverage_kind") or "").strip().lower() == "child"
    if is_shot_child(child) and not explicit_kind:
        uid = str(cs.get("parent_uuid") or "").strip()
        if uid and uid != child_uid:
            for fr in frames:
                if str(getattr(fr, "uuid", "") or "") == uid:
                    return fr
    parent_sid = coverage_parent_shot_id(child)
    if not parent_sid:
        return None
    for fr in frames:
        if coverage_shot_id(fr) != parent_sid:
            continue
        if child_uid and str(getattr(fr, "uuid", "") or "") == child_uid:
            continue
        return fr
    return None


def planned_shots_from_attrs(frame: Any) -> list[dict[str, Any]]:
    """Список кадров ноды «сцены → кадры» (attrs.кадры)."""
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("кадры") or attrs.get("shots")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


_PARENT_SCENE_LOCK = (
    "Image 1 is the previous coverage still of the SAME scene "
    "(layout / set / wardrobe / lighting / cast-count / prop-identity lock). "
    "Preserve: the same room architecture, furniture placement, wall color, "
    "the SAME people already visible (same body count — do not invent extras), "
    "their clothes, lighting direction, and the SAME named objects "
    "(the painting/portrait/document/table in Image 1 is that exact object, "
    "not a replacement). "
    "Change: camera position, framing, and this shot's action only. "
    "If punching in on a painting, it must be the painting already in Image 1. "
    "If Image 1 shows one person at the table, the result still shows one person. "
    "Do not invent a new location or a new person. The viewer must recognize "
    "the same place and the same people as in Image 1."
)

_CHAR_SHEET_LOCK = (
    "Image 2 is the character sheet: identity only (face/body/clothes). Do not copy the sheet pose or layout."
)


def is_coverage_child(frame: Any) -> bool:
    """Дочерний кадр покрытия: shot-ребёнок или есть родитель по T/X."""
    if is_shot_child(frame):
        return True
    return bool(coverage_parent_shot_id(frame))


def merge_parent_scene_refs(
    parent_png: Any | None,
    other: list[Any],
    *,
    max_refs: int = 2,
) -> list[Any]:
    """Слот 1 = PNG родителя (layout lock), слот 2 = персонаж."""
    if parent_png is None:
        return list(other)[:max_refs]
    out = [parent_png]
    parent_key = str(parent_png)
    for item in other:
        if str(item) == parent_key:
            continue
        out.append(item)
        if len(out) >= max_refs:
            break
    return out


def with_parent_scene_lock(
    prompt: str,
    *,
    has_parent_ref: bool,
    has_char_ref: bool = False,
) -> str:
    """Контракт Preserve/Change для image-модели, если прикреплён still родителя."""
    raw = (prompt or "").strip()
    if not has_parent_ref:
        return raw
    if raw.startswith("Image 1 is the previous coverage still"):
        return raw
    parts = [_PARENT_SCENE_LOCK]
    if has_char_ref:
        parts.append(_CHAR_SHEET_LOCK)
    return "\n".join(parts) + "\n\n" + raw


def _is_pipeline_frame(frame: Any) -> bool:
    if not str(getattr(frame, "uuid", "") or "").strip():
        return False
    attrs = getattr(frame, "attrs", None)
    if isinstance(attrs, dict) and attrs.get("from_disk_media"):
        return False
    return True


def _norm_words(text: str) -> list[str]:
    return " ".join((text or "").split()).split()


def flattened_coverage_groups(
    frames: list[Any],
) -> dict[str, list[Any]]:
    """K1+K2+… после flatten: группа по префиксу id (`5` из `5-K2`)."""
    buckets: dict[str, list[tuple[int, Any]]] = {}
    for fr in frames:
        if not _is_pipeline_frame(fr):
            continue
        parsed = parse_coverage_shot(coverage_shot_id(fr))
        if parsed is None:
            continue
        group, k = parsed
        buckets.setdefault(group, []).append((k, fr))
    out: dict[str, list[Any]] = {}
    for group, items in buckets.items():
        items.sort(key=lambda item: item[0])
        ks = [k for k, _ in items]
        if 1 not in ks or max(ks) < 2:
            continue
        out[group] = [fr for _, fr in items]
    return out


def merge_flattened_coverage_members(
    members: list[Any],
) -> tuple[Any, list[Any]]:
    """Склеить K1+K2+K3 обратно на родителя: полный закадр + кадры[]."""
    if len(members) < 2:
        return members[0], []
    parent = members[0]
    extra = list(members[1:])
    shots: list[dict[str, Any]] = []
    parts: list[str] = []
    parent_sid = ""
    for i, fr in enumerate(members):
        planned = planned_shots_from_attrs(fr)
        shot = copy.deepcopy(planned[0]) if planned else {}
        if not isinstance(shot, dict):
            shot = {}
        piece = (getattr(fr, "voiceover_text", None) or "").strip()
        parts.append(piece)
        sid = str(shot.get("id") or coverage_shot_id(fr) or "").strip()
        if i == 0:
            parent_sid = sid
        shot["id"] = sid
        shot["порядок"] = i + 1
        shot["parent_id"] = None if i == 0 else parent_sid or None
        shot["закадр"] = piece
        shots.append(shot)
    full = " ".join(" ".join(parts).split())
    if full:
        parent.voiceover_text = full
        parent.duration_seconds = vo_duration_sec(full, shots=1)
    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["кадры"] = shots
    attrs["vo_cell_full"] = full
    parent.attrs = attrs
    _flag_attrs(parent)
    _set_cs(
        parent,
        role="vo_parent",
        parent_uuid=str(getattr(parent, "uuid", "") or ""),
        shot_index=1,
        shots_in_beat=len(shots),
        shot_id=parent_sid,
    )
    return parent, extra


def _vo_parts_without_empty(text: str, n: int) -> list[str]:
    """Нарезка без пустого хвоста: лишние слоты не оставляем пустыми."""
    raw = (text or "").strip()
    parts = split_text_into_parts(raw, max(1, int(n)))
    while parts and not str(parts[-1] or "").strip():
        parts.pop()
    cleaned = [" ".join((p or "").split()) for p in parts if str(p or "").strip()]
    if cleaned:
        return cleaned
    return [raw] if raw else []


def bits_from_attrs(frame: Any) -> list[dict[str, Any]]:
    """Биты apply-ops из attrs кадра (вход для сборки кадры[])."""
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("биты") or attrs.get("bits")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def kadry_from_bits(full_vo: str, bits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 бит → 1 кадр. Склейка закадр = весь текст ячейки, без хвостов."""
    text = " ".join((full_vo or "").split())
    ordered = sorted(
        (item for item in bits if isinstance(item, dict)),
        key=lambda item: int(item.get("порядок") or 0),
    )
    if not text or not ordered:
        return []
    starts: list[int] = []
    cursor = 0
    for i, item in enumerate(ordered):
        anchor = " ".join(str(item.get("якорь") or "").split())
        idx = -1
        if anchor:
            idx = text.find(anchor, cursor)
            if idx < 0:
                idx = text.lower().find(anchor.lower(), cursor)
        if idx < 0:
            anchored = split_text_into_parts(text, len(ordered))
            while anchored and not str(anchored[-1] or "").strip():
                anchored.pop()
            if " ".join(" ".join(anchored).split()) != text:
                return []
            return _kadry_rows(ordered[: len(anchored)], anchored)
        starts.append(0 if i == 0 else idx)
        cursor = idx + max(len(anchor), 1)
    parts: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        if end < start:
            end = start
        parts.append(text[start:end].strip())
    if " ".join(" ".join(parts).split()) != text:
        fallback = split_text_into_parts(text, len(ordered))
        while fallback and not str(fallback[-1] or "").strip():
            fallback.pop()
        if " ".join(" ".join(fallback).split()) != text:
            return []
        ordered = ordered[: len(fallback)]
        return _kadry_rows(ordered, fallback)
    return _kadry_rows(ordered, parts)


def _kadry_rows(bits: list[dict[str, Any]], parts: list[str]) -> list[dict[str, Any]]:
    """1 бит = самостоятельная VO-ячейка ``Bnn-K1``, не покрытие ``B01-K2``."""
    rows: list[dict[str, Any]] = []
    for i, (item, piece) in enumerate(zip(bits, parts, strict=False)):
        sid = f"B{i + 1:02d}-K1"
        rows.append(
            {
                "id": sid,
                "порядок": i + 1,
                "parent_id": None,
                "закадр": piece,
                "действие": str(item.get("изменение") or item.get("глагол") or "").strip(),
            }
        )
    return rows


def kadry_vo_partition(full: str, planned: list[dict[str, Any]]) -> list[str] | None:
    """закадр из кадры[], только если склейка = ячейка и нет обрубков."""
    parts = [str(item.get("закадр") or "").strip() for item in planned]
    if not parts or not all(parts):
        return None
    if _norm_words(" ".join(parts)) != _norm_words(full):
        return None
    if any(vo_chunk_is_dangling(p) for p in parts):
        return None
    return parts


def resolve_shot_plan(original_vo: str, planned: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Сколько шотов: только из кадры[]. Без плана не выдумывать нарезку."""
    text = (original_vo or "").strip()
    if planned:
        partition = kadry_vo_partition(text, planned)
        if partition:
            return len(partition), partition
        need = max(1, len(planned))
        parts = _vo_parts_without_empty(text, need)
        return len(parts), parts
    return 1, [text] if text else [""]


_SCENE_CHAIN_RE = re.compile(r"(?m)^\s*\d+\.\s+\S")


def looks_like_scene_chain(text: str) -> bool:
    """Нумерованная цепь сцен: «1. место — действие» + кусок в скобках."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_SCENE_CHAIN_RE.search(raw) and "(" in raw)


def _apply_shot_meta(frame: Any, shot: dict[str, Any] | None) -> None:
    if not shot:
        return
    attrs = dict(getattr(frame, "attrs", None) or {})
    place = str(shot.get("место") or shot.get("place") or "").strip()
    action = str(shot.get("действие") or shot.get("action") or "").strip()
    if place:
        attrs["place"] = place
    if action:
        attrs["shot01_action"] = action
        existing = str(attrs.get("main_action") or attrs.get("главное_действие") or "").strip()
        # Expand не затирает цепь сцен (и любой уже записанный main_action).
        if not existing:
            attrs["main_action"] = action
    frame.attrs = attrs
    _flag_attrs(frame)
    extra: dict[str, Any] = {}
    for src, dst in (
        ("план", "план"),
        ("ракурс", "ракурс"),
        ("id", "shot_id"),
        ("parent_id", "coverage_parent_id"),
        ("сцена", "сцена"),
        ("место", "место"),
    ):
        val = shot.get(src)
        if val not in (None, ""):
            extra[dst] = val
    plan = str(shot.get("план") or "").strip()
    if plan:
        extra["крупность"] = plan
    if extra:
        _set_cs(frame, **extra)


def shots_needed_for_vo(text: str) -> int:
    """Сколько визуальных шотов на ячейку: по фразам, не больше 3."""
    raw = (text or "").strip()
    if not raw:
        return 1
    clauses = [p.strip() for p in _CLAUSE_RE.split(raw) if p.strip()]
    n = len(clauses) if len(clauses) > 1 else 1
    if n <= 1 and len(raw.split()) >= 8:
        n = 2
    return max(1, min(_MAX_SHOTS, n))


def vo_duration_sec(text: str, *, shots: int = 1) -> float:
    """Длительность ячейки по 14 символам/сек; на шот не короче 2с."""
    chars = len((text or "").strip())
    total = max(_MIN_SEC * max(1, shots), chars / _VO_CHARS_PER_SEC if chars else _MIN_SEC)
    return round(total, 2)


def _cs(frame: Any) -> dict[str, Any]:
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return {}
    raw = attrs.get(_ATTR_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _flag_attrs(frame: Any) -> None:
    state = getattr(frame, "_sa_instance_state", None)
    if state is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(frame, "attrs")


def _set_cs(frame: Any, **fields: Any) -> None:
    attrs = dict(getattr(frame, "attrs", None) or {})
    cs = dict(attrs.get(_ATTR_KEY) or {})
    cs.update(fields)
    attrs[_ATTR_KEY] = cs
    frame.attrs = attrs
    _flag_attrs(frame)


def repair_split_vo_on_parents(frames: list[Any]) -> int:
    """Вернуть полный закадр разбивки на родителя, с детей снять.

    Если expand уже нарезал Frame.voiceover_text — склеить обратно.
    Не генерирует новый текст.
    """
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    repaired = 0
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
        if len(members) <= 1:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        parts = [(getattr(m, "voiceover_text", None) or "").strip() for m in members]
        child_parts = [(getattr(m, "voiceover_text", None) or "").strip() for m in members if m is not parent]
        if not any(child_parts):
            continue
        unique = {p for p in parts if p}
        if len(unique) == 1:
            joined = next(iter(unique))
        else:
            joined = " ".join(p for p in parts if p)
        if joined:
            parent.voiceover_text = joined
        for m in members:
            if m is not parent:
                m.voiceover_text = ""
        repaired += 1
    return repaired


def apply_vo_shot_cuts(frames: list[Any]) -> int:
    """Дубликат ячейки разбивки режем по шотам. voiceover_text не трогаем."""
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    updated = 0
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        original = (getattr(parent, "voiceover_text", None) or "").strip()
        if not original:
            continue
        parts = split_text_into_parts(original, len(members))
        for i, fr in enumerate(members):
            piece = parts[i] if i < len(parts) else ""
            if str(_cs(fr).get(_VO_SHOT_KEY) or "") == piece:
                continue
            _set_cs(fr, **{_VO_SHOT_KEY: piece})
            updated += 1
    return updated


def _group_by_parent(frames: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
    return groups


def _cell_full_text(parent: Any, members: list[Any]) -> str:
    attrs = getattr(parent, "attrs", None) or {}
    full = str(attrs.get("vo_cell_full") or "").strip() if isinstance(attrs, dict) else ""
    if full:
        return full
    joined = " ".join(
        (getattr(m, "voiceover_text", None) or "").strip()
        for m in members
        if (getattr(m, "voiceover_text", None) or "").strip()
    )
    joined = " ".join(joined.split())
    if joined:
        return joined
    return (getattr(parent, "voiceover_text", None) or "").strip()


def apply_shot_voiceover_to_cells(frames: list[Any]) -> int:
    """После QC: закадр ячейки → voiceover_text каждого кадра группы.

    Режем по фразам/клаузам, не по словам. Склейка непустых
    кусков = исходная ячейка. Полный текст — в attrs.vo_cell_full.
    """
    updated = 0
    for members in _group_by_parent(frames).values():
        if not members:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        full = _cell_full_text(parent, members)
        if not full:
            continue
        planned = planned_shots_from_attrs(parent)
        parts = _vo_parts_without_empty(full, len(members))
        attrs = dict(getattr(parent, "attrs", None) or {})
        attrs["vo_cell_full"] = full
        if planned:
            for i, item in enumerate(planned):
                piece = parts[i] if i < len(parts) else ""
                if not piece:
                    continue
                if str(item.get("закадр") or "") != piece:
                    item["закадр"] = piece
                    updated += 1
            attrs["кадры"] = [item for item, piece in zip(planned, parts, strict=False) if piece] or planned[
                :1
            ]
        parent.attrs = attrs
        _flag_attrs(parent)
        for i, fr in enumerate(members):
            piece = parts[i] if i < len(parts) else ""
            if not piece:
                continue
            if (getattr(fr, "voiceover_text", None) or "") != piece:
                fr.voiceover_text = piece
                updated += 1
            _set_cs(fr, **{_VO_SHOT_KEY: piece})
    return updated


def distribute_coverage_prompts(frames: list[Any]) -> int:
    """Промты покрытия: с родителя в shot2 детей. image_prompt детей не трогаем.

    Классический shot2 на родителе не запускаем, если есть дети — иначе
    дубль PNG. Статус shot2 переезжает на ребёнка.
    """
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_STATUS_ATTR,
        SHOT2_VIDEO_PROMPT_ATTR,
    )

    updated = 0
    for members in _group_by_parent(frames).values():
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        children = [m for m in members if m is not parent]
        if not children:
            continue
        pattrs = dict(getattr(parent, "attrs", None) or {})
        listed = pattrs.get("промты_детей") or pattrs.get("child_prompts") or []
        if not isinstance(listed, list):
            listed = []
        parent_shot2 = str(pattrs.get(SHOT2_PROMPT_ATTR) or "").strip()
        parent_vid2 = str(pattrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        for i, child in enumerate(children):
            entry = listed[i] if i < len(listed) and isinstance(listed[i], dict) else {}
            img = str(
                entry.get("промт_картинки")
                or entry.get("image_prompt")
                or (parent_shot2 if i == 0 else "")
                or ""
            ).strip()
            vid = str(
                entry.get("промт_видео")
                or entry.get("animation_prompt")
                or (parent_vid2 if i == 0 else "")
                or ""
            ).strip()
            cattrs = dict(getattr(child, "attrs", None) or {})
            if img:
                cattrs[SHOT2_PROMPT_ATTR] = img
                cattrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
                updated += 1
            if vid:
                cattrs[SHOT2_VIDEO_PROMPT_ATTR] = vid
                if not str(getattr(child, "animation_prompt", None) or "").strip():
                    child.animation_prompt = vid
            child.attrs = cattrs
            _flag_attrs(child)
            if getattr(child, "image_prompt", None):
                child.image_prompt = ""
        if children:
            pattrs[SHOT2_STATUS_ATTR] = "skipped"
            parent.attrs = pattrs
            _flag_attrs(parent)
    return updated


def inherit_camera_on_children(frames: list[Any]) -> int:
    """Дети берут движение/набор с родителя; крупность уже из плана шота.

    Сирота (vo_parent нет в группе): набор из ``место``, движение — статика.
    Иначе добор меню съёмки их не видит (нет закадра) и нода падает.
    """
    updated = 0
    for members in _group_by_parent(frames).values():
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            None,
        )
        pcs = _cs(parent) if parent is not None else {}
        move = str(pcs.get("движение") or pcs.get("move") or "").strip()
        nab = str(pcs.get("набор") or pcs.get("set") or "").strip()
        if parent is None:
            donor = next(
                (
                    m
                    for m in members
                    if str(_cs(m).get("движение") or "").strip() or str(_cs(m).get("набор") or "").strip()
                ),
                None,
            )
            if donor is not None:
                dcs = _cs(donor)
                move = str(dcs.get("движение") or dcs.get("move") or "").strip()
                nab = str(dcs.get("набор") or dcs.get("set") or "").strip()
        for child in members:
            if parent is not None and child is parent:
                continue
            extra: dict[str, Any] = {}
            ccs = _cs(child)
            if not str(ccs.get("движение") or "").strip():
                if move:
                    extra["движение"] = move
                elif parent is None:
                    extra["движение"] = "статика"
            if not str(ccs.get("набор") or "").strip():
                place = str(ccs.get("место") or "").strip()
                fill = nab or place
                if fill:
                    extra["набор"] = fill
            if extra:
                _set_cs(child, **extra)
                updated += 1
    return updated


async def expand_vo_cells_into_shots(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], dict[str, Any]]:
    """Вставить визуальные шоты. Закадр разбивки не меняем; режем копию."""
    report: dict[str, Any] = {
        "skipped": False,
        "parents": 0,
        "inserted": 0,
        "repaired_vo": 0,
        "vo_shot_cuts": 0,
        "frames_before": len(frames),
        "frames_after": len(frames),
    }
    if already_subdivided(frames):
        report["skipped"] = True
        report["repaired_vo"] = repair_split_vo_on_parents(frames)
        report["vo_shot_cuts"] = apply_vo_shot_cuts(frames)
        return frames, report

    parents = [f for f in frames if f.uuid]
    parents.sort(key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))
    inserted = 0
    for parent in reversed(parents):
        original_vo = parent.voiceover_text or ""
        planned = planned_shots_from_attrs(parent)
        need, vo_parts = resolve_shot_plan(original_vo, planned)
        total_sec = vo_duration_sec(original_vo, shots=need)
        part_sec = round(total_sec / need, 2)
        start_ts = parent.start_ts
        end_ts = parent.end_ts
        parent_uuid = parent.uuid
        if need <= 1:
            _set_cs(
                parent,
                role="vo_parent",
                parent_uuid=parent_uuid,
                shot_index=1,
                shots_in_beat=1,
                vo_shot=vo_parts[0] if vo_parts else original_vo,
            )
            _apply_shot_meta(parent, planned[0] if planned else None)
            if not parent.duration_seconds:
                parent.duration_seconds = part_sec
            continue

        children: list[Frame] = []
        after_id = parent.id
        for _ in range(need - 1):
            child = await insert_frame_after(session, project, after_frame_id=after_id)
            children.append(child)
            after_id = child.id
            inserted += 1

        group = [parent, *children]
        parent.voiceover_text = original_vo
        for i, fr in enumerate(group):
            if i == 0:
                fr.start_ts = start_ts
                fr.end_ts = end_ts
            else:
                fr.start_ts = None
                fr.end_ts = None
                fr.voiceover_text = ""
            fr.duration_seconds = part_sec
            _set_cs(
                fr,
                role="vo_parent" if i == 0 else "shot",
                parent_uuid=parent_uuid,
                shot_index=i + 1,
                shots_in_beat=need,
                vo_shot=vo_parts[i] if i < len(vo_parts) else "",
            )
            _apply_shot_meta(fr, planned[i] if i < len(planned) else None)

    await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    report["parents"] = len(parents)
    report["inserted"] = inserted
    report["vo_shot_cuts"] = apply_vo_shot_cuts(ordered)
    report["frames_after"] = len(ordered)
    logger.info(
        "[#{}] vo_shot_expand: parents={} inserted={} vo_shot_cuts={} frames {}→{}",
        project.id,
        report["parents"],
        inserted,
        report["vo_shot_cuts"],
        report["frames_before"],
        report["frames_after"],
    )
    return ordered, report
