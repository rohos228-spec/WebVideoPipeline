"""Проставить непрерывность на кадры проекта после сборки сцен.

Чистая арифметика оси и предметов живёт в ``continuity``; здесь — только
сбор входа из того, что уже лежит на диске и в БД, и запись результата в
``Frame.attrs["continuity"]``. Эту строку читает ``db_frames_context``, и
она уезжает в промт картинки как факт постановки, а не как правило.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.scene_design import agents as ag
from app.services.scene_design import runner
from app.services.scene_design.continuity import (
    Violation,
    axis_for_scene,
    build_prop_ledger,
    continuity_line,
    enforce_axis,
    parse_character_ids,
    parse_prop_ids,
    resolve_axis,
)

_SUBDIV_KEY = "camera_subdivide"


async def _ordered_frames(session: AsyncSession, project: Project) -> list[Frame]:
    rows = (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars().all()
    return sorted(rows, key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))


def _attrs(frame: Frame) -> dict[str, Any]:
    return frame.attrs if isinstance(frame.attrs, dict) else {}


def _parent_uuid(frame: Frame) -> str:
    meta = _attrs(frame).get(_SUBDIV_KEY)
    if isinstance(meta, dict):
        parent = str(meta.get("parent_uuid") or "").strip()
        if parent:
            return parent
    return str(frame.uuid or "")


def _name_map(cards: Any, *, id_keys: tuple[str, ...], name_keys: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        raw_id = next((card[k] for k in id_keys if card.get(k)), None)
        name = next((str(card[k]).strip() for k in name_keys if card.get(k)), "")
        if not raw_id or not name:
            continue
        ids = parse_character_ids(raw_id) or parse_prop_ids(raw_id)
        if ids:
            out[ids[0]] = name
    return out


def _skeleton_props_by_parent(
    project: Project, parents: list[str]
) -> tuple[dict[str, list[Any]], dict[str, str]]:
    """VO-родитель → его ``предметы`` из скелета; плюс имена предметов.

    Карточки скелета идут по VO-ячейкам в том же порядке, что и кадры-родители
    (скелет пишется до дробления камерой), поэтому связываем по позиции, а не
    по номеру кадра: номера после ``renumber_frames_by_sort_key`` уже другие.
    """
    draft = runner.load_checkpoint(project, ag.SKELETON)
    if not isinstance(draft, dict):
        return {}, {}
    cards = [c for c in (draft.get("scenes") or []) if isinstance(c, dict)]
    names = _name_map(draft.get("items_seed"), id_keys=("id", "код"), name_keys=("имя", "name", "название"))
    if len(cards) != len(parents):
        logger.warning(
            "[#{}] continuity: карточек скелета {} против VO-родителей {} — предметы связываю по минимуму",
            project.id,
            len(cards),
            len(parents),
        )
    by_parent: dict[str, list[Any]] = {}
    for parent_uuid, card in zip(parents, cards, strict=False):
        props = card.get("предметы")
        if isinstance(props, list) and props:
            by_parent[parent_uuid] = props
    return by_parent, names


def _character_names(project: Project) -> dict[str, str]:
    draft = runner.load_checkpoint(project, "characters")
    cards = draft.get("characters") if isinstance(draft, dict) else None
    return _name_map(cards, id_keys=("id", "код"), name_keys=("имя", "name", "название"))


def _fold(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _shot_sides(project: Project, frames: list[Frame]) -> list[str | None]:
    """``сторона`` каждого кадра из среза camera — там же, где крупность и угол.

    Связываем по ``композиция``: локальный сборщик кладёт её в описание кадра
    целиком, а позиция в списке после ``camera_expand`` уже не совпадает —
    лестница крупностей размножает строки shot_plan. Позиционное соответствие
    оставлено запасным вариантом, когда длины сошлись.
    """
    draft = runner.load_checkpoint(project, "camera")
    shots = draft.get("shot_plan") if isinstance(draft, dict) else None
    if not isinstance(shots, list) or not shots:
        return [None] * len(frames)
    rows = [s for s in shots if isinstance(s, dict)]
    by_composition: dict[str, str] = {}
    for shot in rows:
        comp = _fold(shot.get("композиция"))
        side = shot.get("сторона")
        if comp and side is not None and comp not in by_composition:
            by_composition[comp] = str(side)
    out: list[str | None] = []
    for i, fr in enumerate(frames):
        desc = _fold(_attrs(fr).get("shot01_description") or _attrs(fr).get("shot01_action"))
        found: str | None = None
        if desc:
            for comp, side in by_composition.items():
                if comp in desc:
                    found = side
                    break
        if found is None and len(rows) == len(frames):
            raw = rows[i].get("сторона")
            found = str(raw) if raw is not None else None
        out.append(found)
    return out


def build_continuity(
    project: Project, frames: list[Frame]
) -> tuple[dict[str, str], list[Violation], dict[str, Any]]:
    """Посчитать строки непрерывности, ничего не записывая.

    Отдельно от записи, чтобы тем же кодом считал отчёт
    ``scripts/continuity_report.py``: инструмент измерения обязан считать
    ровно то же, что пайплайн, иначе он меряет себя.
    """
    parents: list[str] = []
    for fr in frames:
        pu = _parent_uuid(fr)
        if pu and pu not in parents:
            parents.append(pu)
    props_by_parent, prop_names = _skeleton_props_by_parent(project, parents)
    names = _character_names(project)
    sides = _shot_sides(project, frames)

    violations: list[Violation] = []

    # Ось ролика: кто из главной пары стоит слева по экрану. Считается один раз
    # на весь ролик, иначе сцена, где первым показали второго героя, перевернёт
    # расстановку — и герой прыгнет с левой половины кадра в правую.
    global_axis = resolve_axis(
        [{"кто_в_кадре": _attrs(fr).get("characters") or _attrs(fr).get("персонажи") or ""} for fr in frames]
    )

    # Ось — по сценам: сторона обязана быть одна на сцену.
    side_by_uuid: dict[str, str] = {}
    axis_by_uuid: dict[str, Any] = {}
    by_scene: dict[str, list[tuple[int, Frame]]] = {}
    for i, fr in enumerate(frames):
        a = _attrs(fr)
        # После db_apply сцена лежит в shot01_id_scene (FIELD_MAP); «id_scene» —
        # это имя поля в ops, до записи. Читаем оба, иначе сцена «теряется» и
        # каждый кадр становится собственной сценой — ось держать не на чем.
        sid = str(a.get("shot01_id_scene") or a.get("id_scene") or "").strip() or f"_frame_{fr.id}"
        by_scene.setdefault(sid, []).append((i, fr))
    for _sid, group in by_scene.items():
        shots = [
            {
                "uuid": str(fr.uuid or ""),
                "кто_в_кадре": _attrs(fr).get("characters") or _attrs(fr).get("персонажи") or "",
                "сторона": sides[i],
            }
            for i, fr in group
        ]
        axis = axis_for_scene(shots, global_axis)
        resolved, axis_violations = enforce_axis(shots)
        violations.extend(axis_violations)
        for shot in resolved:
            side_by_uuid[shot["uuid"]] = shot["сторона"]
            axis_by_uuid[shot["uuid"]] = axis

    # Предметы — сквозь весь ролик в хронологии, не по сценам. Карточка скелета
    # описывает VO-ячейку целиком, а камера дробит ячейку на несколько кадров:
    # событие предмета применяем один раз, на первом кадре ячейки. Иначе одно
    # «вводится» повторится на каждом ребёнке и реестр решит, что предмет
    # появляется заново и прыгает по рукам.
    seen_parents: set[str] = set()
    ledger_input: list[dict[str, Any]] = []
    for fr in frames:
        parent = _parent_uuid(fr)
        first_of_cell = parent not in seen_parents
        seen_parents.add(parent)
        ledger_input.append(
            {
                "uuid": str(fr.uuid or ""),
                "персонажи": _attrs(fr).get("characters") or _attrs(fr).get("персонажи") or "",
                "действие": _attrs(fr).get("shot01_action") or "",
                "предметы": (props_by_parent.get(parent) or []) if first_of_cell else [],
            }
        )
    ledger, prop_violations = build_prop_ledger(ledger_input, names)
    violations.extend(prop_violations)

    # Каст сцены — на случай кадров, где поле персонажей пустое или мусорное
    # («нет»): пустой список означал бы «в кадре никого», и расстановка ушла бы
    # в промт про людей, которых там нет.
    scene_cast: dict[str, list[str]] = {}
    for sid, group in by_scene.items():
        cast: list[str] = []
        for _i, fr in group:
            for cid in parse_character_ids(_attrs(fr).get("characters") or _attrs(fr).get("персонажи")):
                if cid not in cast:
                    cast.append(cid)
        scene_cast[sid] = cast
    uuid_scene = {str(fr.uuid or ""): sid for sid, group in by_scene.items() for _i, fr in group}

    lines: dict[str, str] = {}
    for fr in frames:
        uid = str(fr.uuid or "")
        lines[uid] = continuity_line(
            axis=axis_by_uuid.get(uid),
            side=side_by_uuid.get(uid, "A"),
            names=names,
            props=ledger.get(uid),
            prop_names=prop_names,
            in_frame=(
                parse_character_ids(_attrs(fr).get("characters") or _attrs(fr).get("персонажи") or "")
                or scene_cast.get(uuid_scene.get(uid, ""), [])
            ),
        )
    context = {"axis": axis_by_uuid, "side": side_by_uuid, "props": ledger, "prop_names": prop_names}
    return lines, violations, context


async def apply_continuity(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Записать ``attrs["continuity"]`` каждому кадру; вернуть отчёт с нарушениями."""
    frames = await _ordered_frames(session, project)
    if not frames:
        return {"frames": 0, "written": 0, "violations": []}

    lines, violations, _context = build_continuity(project, frames)
    written = 0
    for fr in frames:
        attrs = dict(_attrs(fr))
        line = lines.get(str(fr.uuid or ""), "")
        if line:
            attrs["continuity"] = line
            written += 1
        else:
            attrs.pop("continuity", None)
        fr.attrs = attrs
    await session.flush()

    texts = [str(v) for v in violations]
    report: dict[str, Any] = {"frames": len(frames), "written": written, "violations": texts}
    if violations:
        logger.warning(
            "[#{}] continuity: нарушений {} — {}", project.id, len(violations), "; ".join(texts[:8])
        )
    else:
        logger.info("[#{}] continuity: кадров {}, строк {}, нарушений нет", project.id, len(frames), written)
    return report
