"""Отчёт по данным, которые записали ноды «Работа с GPT»: кадры, сцены, персонажи, промты.

Только чтение и бесплатно: модель не вызывается, в базу ничего не пишется. Откуда данные:

* кадры — из базы проекта (живые, без архивных). Если в базе у проекта кадров нет или там только
  заглушки («Кадр 1» без текста), кадры читаются из листа «план» его project.xlsx: так лежат
  проекты, перенесённые с прежней линии, где кадры жили в отдельных файлах. Файл только читается,
  импорта в базу нет;
* что записала каждая нода — из её последнего прогона (node_trace) или сохранённого ответа
  (excel_gpt_uploads/<нода>/gpt_reply.txt), тем же разбором, что ``report_gpt``; номера кадров для
  uuid из ответа — по кадрам проекта и по входу ноды (db_frames.json);
* персонажи — карточки из ответов нод, иначе персонажи проекта в базе, иначе лист «Персонажи»
  project.xlsx; сцены — карточки из ответов нод, иначе по номерам сцен в кадрах.

Какие колонки показать — ``report_data_format`` (туда же ляжет формат заказчика).
Нода ещё не запускалась или ничего не записала — так и пишем, ничего не придумываем.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.services.action_layer import report_data_format as fmt
from app.services.action_layer import report_gpt as rg
from app.services.action_layer.context import ActionCallError, ActionContext

ACTION_ID = "report.gpt_data"
_KINDS = ("chat", "json", "xlsx", "html")
# Строка листа «план» с персонажами кадра (R8, см. db_apply.FIELD_ALIASES «персонажи»).
_ROW_CHARACTERS = 8
_PLACEHOLDER_VO = re.compile(r"^\s*кадр\s*\d+\s*$", re.IGNORECASE)
_PLACEHOLDER_ATTRS = frozenset({"from_disk_media"})
_FRAME_COLUMNS_DB = ("voiceover_text", "meaning", "image_prompt", "animation_prompt", "duration_seconds")


# ── Кадры проекта ──


def _canon(key: Any) -> str:
    from app.services.db_apply import FIELD_ALIASES

    raw = str(key).strip()
    return FIELD_ALIASES.get(raw.lower().replace(" ", "_")) or raw


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return rg.cell_text(value).strip()


def _frame_from_row(fr: Any) -> dict:
    fields: dict[str, str] = {}
    attrs = fr.attrs if isinstance(getattr(fr, "attrs", None), dict) else {}
    for key, value in attrs.items():
        text = _text(value)
        if text and key not in _PLACEHOLDER_ATTRS:
            fields.setdefault(_canon(key), text)
    for key in _FRAME_COLUMNS_DB:
        text = _text(getattr(fr, key, None))
        if text:
            fields[key] = text
    return {"number": int(fr.number or 0), "uuid": str(getattr(fr, "uuid", None) or ""), "fields": fields}


def _has_content(frames: list[dict]) -> bool:
    for f in frames:
        fields = f["fields"]
        if any(k != "voiceover_text" and v for k, v in fields.items()):
            return True
        if fields.get("voiceover_text") and not _PLACEHOLDER_VO.match(fields["voiceover_text"]):
            return True
    return False


async def _db_frames(pid: int, data_dir: Any) -> list[dict]:
    """Живые кадры проекта из единой базы (под RLS: только свой арендатор)."""
    from sqlalchemy import select

    from app.models import Frame
    from app.services.action_layer import builtin_insight as bi
    from app.services.db_v2 import frame_is_archived

    del data_dir  # на этой линии отдельного project.db нет
    try:
        async with bi._sessions()() as s:
            rows = (await s.scalars(select(Frame).where(Frame.project_id == pid))).all()
    except SQLAlchemyError:
        return []
    live = [fr for fr in rows if not frame_is_archived(fr)]
    return sorted((_frame_from_row(fr) for fr in live), key=lambda f: f["number"])


def _xlsx_rows() -> dict[str, int]:
    from app.services import xlsx_v8_import as v8
    from app.services.db_apply import _ATTR_EXCEL_ROWS

    rows = dict(_ATTR_EXCEL_ROWS)
    rows.update(
        {
            "characters": _ROW_CHARACTERS,
            "voiceover_text": v8.ROW_VOICEOVER_V8,
            "image_prompt": v8.ROW_IMAGE_PROMPT_V8,
            "image_prompt_shot2": v8.ROW_IMAGE_PROMPT_2_V8,
            "animation_prompt": v8.ROW_VIDEO_PROMPT_V8,
            "animation_prompt_shot2": v8.ROW_VIDEO_PROMPT_2_V8,
            "duration_seconds": v8.ROW_DURATION_V8,
        }
    )
    return rows


def xlsx_frames(path: Path) -> list[dict]:
    """Кадры листа «план» project.xlsx (кадр — колонка), только чтение файла."""
    from openpyxl import load_workbook

    from app.services import xlsx_v8_import as v8

    try:
        if not path.is_file():
            return []
        wb = load_workbook(filename=str(path), data_only=True)
    except Exception:  # noqa: BLE001 - битый или занятый файл: кадров из него нет
        return []
    try:
        ws = v8._resolve_plan_sheet(wb)
        if ws is None:
            return []
        rows = _xlsx_rows()
        out = []
        for number, col in v8._plan_scene_columns_ordered(ws):
            fields = {}
            for key, row in rows.items():
                text = v8._cell_text(ws, row, col)
                if text and not v8._is_formula_cell_text(text):
                    fields[key] = text
            out.append({"number": int(number), "uuid": "", "fields": fields})
        return out
    finally:
        wb.close()


async def load_frames(pid: int, data_dir: Any) -> tuple[list[dict], str]:
    """Кадры отчёта и откуда они: база, а без настоящих кадров в базе — project.xlsx."""
    db = await _db_frames(pid, data_dir)
    if db and _has_content(db):
        return db, "из базы проекта"
    if data_dir:
        sheet = await asyncio.to_thread(xlsx_frames, Path(data_dir) / "project.xlsx")
        if sheet and len(sheet) >= len(db):
            return sheet, "из project.xlsx, в базе кадров нет"
    return db, "из базы проекта"


# ── Что записали ноды ──


def _input_uuids(data_dir: Any, node_key: str) -> dict[str, int]:
    """uuid → номер кадра из входа ноды (excel_gpt_uploads/<нода>/db_frames.json)."""
    root = (Path(data_dir) / "excel_gpt_uploads").resolve()
    path = (root / str(node_key) / "db_frames.json").resolve()
    if path.parent.parent != root:
        return {}
    data = rg._read_json(path)
    frames = data.get("frames") if isinstance(data, dict) else data
    out: dict[str, int] = {}
    for f in frames if isinstance(frames, list) else []:
        if isinstance(f, dict) and f.get("uuid") and str(f.get("number") or "").isdigit():
            out[str(f["uuid"])] = int(f["number"])
    return out


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _trace_mtime(data_dir: Any, node_key: str) -> float:
    """Когда писался последний прогон ноды в node_trace (0 — прогона нет)."""
    from app.services.node_trace import _safe_name, trace_root

    node_dir = trace_root(data_dir) / _safe_name(str(node_key))
    try:
        runs = sorted(
            p
            for p in node_dir.iterdir()
            if p.is_dir() and not p.name.endswith(".del") and any(p.glob("call_*"))
        )
    except OSError:
        return 0.0
    if not runs:
        return 0.0
    return max((_mtime(f) for f in runs[-1].rglob("*") if f.is_file()), default=_mtime(runs[-1]))


def _reply_is_newer(data_dir: Any, node_key: str) -> bool:
    """Сохранённый ответ свежее дневника прогона: нода перезапускалась без дневника.

    Тогда в дневнике старый прогон, а кадры проекта заполнены по сохранённому ответу.
    """
    reply = Path(data_dir) / "excel_gpt_uploads" / str(node_key) / "gpt_reply.txt"
    trace = _trace_mtime(data_dir, node_key)
    return bool(trace) and _mtime(reply) > trace


def _writes_and_replies(data_dir: Any, node_key: str, index: rg.FrameIndex) -> tuple[dict, list[dict]]:
    """Что записала нода и разобранные ответы: свежий из дневника прогона и сохранённого ответа."""
    if _reply_is_newer(data_dir, node_key):
        text = rg.read_reply(data_dir, node_key)
        writes = rg.writes_from_reply(text, index)
        writes.update(run={}, source="последний ответ" if writes["parsed"] else "")
        texts = [text]
    else:
        writes = rg.node_writes(data_dir, node_key, index)
        texts = [c.get("reply") for c in rg.read_batches(data_dir, node_key).get("calls") or []]
        if not texts:
            texts = [rg.read_reply(data_dir, node_key)]
    return writes, [d for d in (rg._parse(t) for t in texts) if d is not None]


def _card_text(card: dict, *keys: str) -> str:
    for key in keys:
        text = _text(card.get(key))
        if text:
            return text
    return ""


def _character_card(card: Any, label: str) -> dict | None:
    if not isinstance(card, dict):
        return None
    cid = _card_text(card, "id", "code", "character_id")
    name = _card_text(card, "имя", "name")
    if not cid and not name:
        return None
    return {
        "id": cid,
        "name": name,
        "look": _card_text(card, "внешность", "look", "appearance"),
        "clothes": _card_text(card, "одежда", "clothes"),
        "char": _card_text(card, "характер", "char", "character"),
        "source": label,
    }


def _scene_card(card: Any, label: str) -> dict | None:
    if not isinstance(card, dict):
        return None
    sid = _card_text(card, "id_scene", "scene_id", "id")
    if not sid:
        return None
    return {
        "id": sid,
        "place": _card_text(card, "место", "place"),
        "sense": _card_text(card, "смысл_сцены", "scene_sense", "смысл"),
        "characters": _card_text(card, "персонажи_сцены", "персонажи", "characters"),
        "start": _card_text(card, "start_words", "scene_start_words"),
        "end": _card_text(card, "end_words", "scene_end_words"),
        "source": label,
    }


def node_status(meta: dict, key: str, run: dict | None) -> str:
    from app.services.action_layer import builtin_insight as bi

    if key in [str(x) for x in meta.get("disabled_nodes") or []]:
        return "отключена"
    if run:
        status = str(run.get("status") or "")
        return bi._NODE_STATUS_RU.get(status, status or "не запускалась")
    return "не запускалась"


def empty_note(label: str, status_ru: str) -> str:
    """Честно, почему у ноды нет данных."""
    if status_ru in ("не запускалась", "ждёт", "в очереди", "отключена"):
        tail = " (отключена)" if status_ru == "отключена" else ""
        return f"нода «{label}» ещё не запускалась{tail}, данных нет"
    if status_ru == "выполняется":
        return f"нода «{label}» сейчас выполняется, записанных данных пока нет"
    if status_ru == "ошибка":
        return f"нода «{label}» запускалась, но упала с ошибкой, данных нет"
    return f"нода «{label}» ({status_ru}) не оставила сохранённого ответа с данными"


def node_data(data_dir: Any, key: str, label: str, status_ru: str, index: rg.FrameIndex) -> dict:
    writes, replies = _writes_and_replies(data_dir, key, index)
    characters: dict[str, dict] = {}
    scenes: dict[str, dict] = {}
    for data in replies:
        for raw in data.get("characters") if isinstance(data.get("characters"), list) else []:
            card = _character_card(raw, label)
            if card:
                characters[card["id"] or card["name"]] = card
        for raw in data.get("scenes") if isinstance(data.get("scenes"), list) else []:
            card = _scene_card(raw, label)
            if card:
                scenes[card["id"]] = card
    values = {
        int(k): {f: v for f, v in vals.items() if str(v).strip()}
        for k, vals in (writes.get("values") or {}).items()
        if isinstance(k, int) or str(k).isdigit()
    }
    fields: list[str] = []
    for names in writes["frames"].values():
        fields.extend(n for n in names if n not in fields)
    has_data = bool(values or characters or scenes or writes["project_fields"])
    return {
        "node_key": key,
        "label": label,
        "status_ru": status_ru,
        "values": values,
        "fields": fields,
        "fields_ru": ", ".join(rg.tpl.field_label(n) for n in fields),
        "frames_written": len(values),
        "characters_cards": list(characters.values()),
        "scenes_cards": list(scenes.values()),
        "characters": len(characters),
        "scenes": len(scenes),
        "has_data": has_data,
        "note": "" if has_data else empty_note(label, status_ru),
        "source": writes.get("source") or "",
    }


# ── Персонажи проекта без ответа ноды ──


async def _db_characters(pid: int) -> list[dict]:
    from sqlalchemy import select

    from app.models import Entity
    from app.services.action_layer import builtin_insight as bi
    from app.services.excel_characters import characters_from_entities

    try:
        async with bi._sessions()() as s:
            rows = (
                await s.scalars(select(Entity).where(Entity.project_id == pid, Entity.type == "character"))
            ).all()
    except SQLAlchemyError:
        return []
    return [
        {"id": c.id, "name": c.name, "look": c.look, "clothes": c.clothes, "char": c.char, "source": "база"}
        for c in characters_from_entities(list(rows))
    ]


def _sheet_characters(data_dir: Any) -> list[dict]:
    from app.services.excel_characters import parse_persons_sheet

    try:
        found = parse_persons_sheet(Path(data_dir) / "project.xlsx")
    except Exception:  # noqa: BLE001 - нет файла или листа
        return []
    return [
        {
            "id": c.id,
            "name": c.name,
            "look": c.look,
            "clothes": c.clothes,
            "char": c.char,
            "source": "project.xlsx",
        }
        for c in found
    ]


# ── Сборка строк ──


def _value(fields: dict[str, str], key: str) -> str:
    for name in fmt.FRAME_COLUMNS[key][1]:
        text = fields.get(name)
        if text:
            return text
    return ""


def _with_names(codes: str, names: dict[str, str]) -> str:
    """«c01, c02» → «c01 (Сипуха), c02»: имя персонажа рядом с его кодом."""
    if not codes or not names:
        return codes
    parts = [p.strip() for p in re.split(r"[,;]\s*", codes) if p.strip()]
    if not parts or not all(re.fullmatch(r"[a-zа-я]?\d{1,3}|[a-z]+_\d+", p, re.IGNORECASE) for p in parts):
        return codes
    return ", ".join(f"{p} ({names[p]})" if names.get(p) else p for p in parts)


def _scene_fields() -> frozenset[str]:
    from app.services.db_apply import _ATTR_EXCEL_ROWS

    return frozenset(_ATTR_EXCEL_ROWS) | {"characters", "lighting", "scene_lighting"}


def build_rows(frames: list[dict], nodes: list[dict], keys: list[str], *, only_node: bool) -> list[dict]:
    """Строка на кадр: значения кадра, пустое дополняется тем, что записали ноды.

    Отчёт по одной ноде (``only_node``) — только её значения (закадр кадра оставляем для понимания).
    """
    names = {c["id"]: c["name"] for n in nodes for c in n["characters_cards"] if c["id"] and c["name"]}
    by_number = {f["number"]: f for f in frames}
    numbers = list(by_number)
    if not frames:
        # Кадров проекта не прочитать: строки — по тому, что записали ноды. Есть кадры —
        # записи в кадры, которых сейчас нет (архивные, пересобранные), в таблицу не идут.
        for n in nodes:
            numbers.extend(k for k in n["values"] if k not in numbers)
    scene_ids = {n["node_key"]: {c["id"] for c in n["scenes_cards"]} for n in nodes}
    rows = []
    for number in numbers:
        frame = by_number.get(number) or {"fields": {}}
        sid = _value(frame["fields"], "scene")
        merged: dict[str, str] = {}
        writers = []
        by_scene = False
        for n in nodes:
            vals = n["values"].get(number) or {}
            # Разметка сцен пишет в кадры не ops, а сценами (shots по словам закадра): кадр её сцены —
            # тоже её запись.
            in_scene = bool(sid) and sid in scene_ids[n["node_key"]]
            if vals or in_scene:
                writers.append(n["label"])
                merged.update(vals)
                by_scene = by_scene or in_scene
        if only_node:
            # Из кадра — только поля разметки сцен; промты и закадр пишут другие шаги.
            base = {k: v for k, v in frame["fields"].items() if k in _scene_fields()} if by_scene else {}
            if frame["fields"].get("voiceover_text"):
                base.setdefault("voiceover_text", frame["fields"]["voiceover_text"])
            fields = {**base, **merged}
        else:
            fields = {**merged, **frame["fields"]}
        row: dict[str, Any] = {"frame": number}
        for key in keys:
            if key == "frame":
                continue
            if key == "nodes":
                row[key] = ", ".join(writers)
            elif key == "characters":
                row[key] = _with_names(_value(fields, key), names)
            else:
                row[key] = _value(fields, key)
        row["_has"] = any(row.get(k) for k in keys if k not in ("frame", "voiceover_text", "nodes"))
        row["_by"] = bool(writers)
        row["_chars"] = _value(fields, "characters")
        rows.append(row)
    return sorted(rows, key=lambda r: r["frame"])


def _span(numbers: list[int]) -> str:
    nums = sorted(set(numbers))
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in [*nums[1:], None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def scene_rows(nodes: list[dict], frames: list[dict], frame_rows: list[dict]) -> list[dict]:
    cards: dict[str, dict] = {}
    for n in nodes:
        for c in n["scenes_cards"]:
            cards[c["id"]] = dict(c)
    in_frames: dict[str, list[int]] = {}
    place: dict[str, str] = {}
    chars: dict[str, str] = {}
    for f in frames:
        sid = _value(f["fields"], "scene")
        if sid:
            in_frames.setdefault(sid, []).append(f["number"])
            place.setdefault(sid, _value(f["fields"], "place"))
            chars.setdefault(sid, _value(f["fields"], "characters"))
    for r in frame_rows:
        sid = r.get("scene") or ""
        if sid and r["frame"] not in in_frames.get(sid, []):
            in_frames.setdefault(sid, []).append(r["frame"])
    for sid, nums in in_frames.items():
        card = cards.setdefault(
            sid,
            {
                "id": sid,
                "place": place.get(sid, ""),
                "sense": "",
                "characters": chars.get(sid, ""),
                "source": "кадры",
            },
        )
        card["frames"] = _span(nums)
    return list(cards.values())


_CODE = re.compile(r"(?<![\w])([a-zа-я]{1,3}\d{1,3})(?![\w])", re.IGNORECASE)


def character_rows(nodes: list[dict], frame_rows: list[dict], fallback: list[dict]) -> list[dict]:
    """Персонажи: карточки из ответов нод, иначе из проекта; без карточек — коды из кадров."""
    cards: dict[str, dict] = {}
    for n in nodes:
        for c in n["characters_cards"]:
            cards[c["id"] or c["name"]] = dict(c)
    if not cards:
        cards = {c["id"] or c["name"]: dict(c) for c in fallback}
    if not cards:
        for r in frame_rows:
            for code in _CODE.findall(str(r.get("_chars") or "")):
                cards.setdefault(
                    code, {"id": code, "name": "", "look": "карточки персонажа нет", "source": "кадры"}
                )
    for card in cards.values():
        cid = card.get("id") or ""
        nums = [
            r["frame"]
            for r in frame_rows
            if cid and re.search(rf"(?<![\w]){re.escape(cid)}(?![\w])", str(r.get("_chars") or ""))
        ]
        card["frames"] = _span(nums)
    return list(cards.values())


# ── Вывод ──


def _chat_table(rows: list[dict], columns: tuple[tuple[str, str], ...]) -> tuple[str, int]:
    """Таблица для чата: не больше CHAT_ROWS строк и CHAT_TABLE_CHARS знаков."""
    head = "| " + " | ".join(label for _k, label in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    lines, used = [head, sep], len(head) + len(sep)
    shown = 0
    for r in rows[: fmt.CHAT_ROWS]:
        cells = [
            rg.clip(r.get(k), fmt.CHAT_CELL).replace("|", "/") if r.get(k) not in (None, "") else "—"
            for k, _l in columns
        ]
        line = "| " + " | ".join(cells) + " |"
        if shown and used + len(line) > fmt.CHAT_TABLE_CHARS:
            break
        lines.append(line)
        used += len(line)
        shown += 1
    return "\n".join(lines), shown


def _html(model: dict) -> str:
    from app.services.action_layer import report_html as rh

    def table(columns, rows, empty):
        body = [
            "<tr>"
            + "".join(
                f"<td class=val>{rh._raw(r.get(k) if r.get(k) not in (None, '') else '—')}</td>"
                for k, _l in columns
            )
            + "</tr>"
            for r in rows
        ]
        return rh._table([label for _k, label in columns], body, empty)

    body = "".join(
        [
            rh._box(
                "Ноды «Работа с GPT»",
                table(fmt.NODE_COLUMNS, model["nodes"], "Нод «Работа с GPT» нет."),
                "nodes",
            ),
            rh._box(
                fmt.CHARACTERS_SHEET,
                table(fmt.CHARACTER_COLUMNS, model["characters"], "Персонажей нет."),
                "characters",
            ),
            rh._box(fmt.SCENES_SHEET, table(fmt.SCENE_COLUMNS, model["scenes"], "Сцен нет."), "scenes"),
            rh._box(fmt.FRAMES_SHEET, table(model["columns"], model["rows"], "Кадров нет."), "frames"),
        ]
    )
    toc = [("nodes", "Ноды"), ("characters", "Персонажи"), ("scenes", "Сцены"), ("frames", "Кадры")]
    meta_line = f"Проект #{model['project_id']} «{model['title']}» · {model['stamp']} · {model['source']}"
    return rh._page(f"{fmt.TITLE} — #{model['project_id']}", fmt.TITLE, meta_line, toc, body)


def _node_line(n: dict) -> str:
    if not n["has_data"]:
        return n["note"][:1].upper() + n["note"][1:] + "."
    parts = [n["status_ru"]]
    if n["frames_written"]:
        parts.append(
            f"записала кадров {n['frames_written']}"
            + (f" ({rg.clip(n['fields_ru'], 80)})" if n["fields_ru"] else "")
        )
    if n["characters"]:
        parts.append(f"персонажей {n['characters']}")
    if n["scenes"]:
        parts.append(f"сцен {n['scenes']}")
    return f"«{n['label']}»: " + ", ".join(parts) + "."


def _node_lines(nodes: list[dict]) -> list[str]:
    """Строка на ноду; отработавшие ноды без сохранённого ответа — одной строкой."""
    if not nodes:
        return ["- В проекте нет нод «Работа с GPT»."]
    silent = [n for n in nodes if not n["has_data"] and n["status_ru"] == "готово"]
    lines = [f"- {_node_line(n)}" for n in nodes if len(silent) < 2 or n not in silent]
    if len(silent) >= 2:
        names = ", ".join(f"«{n['label']}»" for n in silent)
        lines.append(f"- Отработали, но сохранённого ответа с данными не оставили: {names}.")
    return lines


def _character_line(c: dict) -> str:
    who = " ".join(p for p in (c.get("id"), c.get("name")) if p) or "без имени"
    tail = c.get("look") or c.get("char") or ""
    where = f" (кадры {c['frames']})" if c.get("frames") else ""
    return f"- {who}{where}" + (f": {rg.clip(tail, 120)}" if tail else "")


def resolve_node(want: str, gpt_keys: list[str]) -> str:
    """«n_excel_gpt_2» из чата → ключ ноды на канвасе с тем же номером (excel_gpt_2)."""
    if not want or want in gpt_keys:
        return want
    m = re.search(r"(\d+)$", want)
    if m:
        same = [k for k in gpt_keys if re.search(rf"(?<!\d){m.group(1)}$", k) and "_fw_" not in k]
        if len(same) == 1:
            return same[0]
    return want


def _scene_line(c: dict) -> str:
    where = f" (кадры {c['frames']})" if c.get("frames") else ""
    what = c.get("place") or c.get("sense") or ""
    return f"- {c['id']}{where}" + (f": {rg.clip(what, 110)}" if what else "")


async def report_gpt_data(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer import builtin_insight as bi

    pid = int(params["project_id"])
    kind = params.get("kind") or "chat"
    if kind not in _KINDS:
        kind = "chat"
    # Строка проекта под RLS: чужой проект (другой арендатор) не найден, ни имени, ни данных.
    row = await bi._project_row(pid)
    if row is None:
        raise ActionCallError(404, f"Проект #{pid} не найден.")
    detail = await bi._effective_project(ctx, pid)
    meta = bi._meta(detail)
    project = bi._transient(pid, detail, row)
    data_dir = project.data_dir
    title = rg.clip(detail.get("title") or detail.get("topic") or row.title or row.topic, 80)
    labels = bi._labels(meta)
    runs = {str(nr.get("node_key")): nr for nr in await bi._node_runs(ctx, pid)}
    gpt_keys = [str(n["id"]) for n in bi._canvas_nodes(meta) if rg.is_gpt_node(n)]
    want = resolve_node(str(params.get("node_key") or "").strip(), gpt_keys)
    if want and want not in gpt_keys:
        known = ", ".join(f"«{labels.get(k, k)}» ({k})" for k in gpt_keys) or "нет"
        raise ActionCallError(404, f"В проекте #{pid} нет ноды «Работа с GPT» {want}. Есть: {known}.")
    keys = fmt.columns_for(params.get("preset"), params.get("columns"))
    columns = fmt.labelled(keys)

    frames, source = await load_frames(pid, data_dir)
    index = rg.FrameIndex(num_by_uuid={f["uuid"]: f["number"] for f in frames if f["uuid"]})
    for key in gpt_keys:
        for uid, num in _input_uuids(data_dir, key).items():
            index.num_by_uuid.setdefault(uid, num)
    nodes = [
        node_data(data_dir, key, labels.get(key, key), node_status(meta, key, runs.get(key)), index)
        for key in gpt_keys
    ]
    picked = [n for n in nodes if n["node_key"] == want] if want else nodes
    frame_rows = build_rows(frames, picked, keys, only_node=bool(want))
    if want:
        frame_rows = [r for r in frame_rows if r["_by"]]
    known = {f["number"] for f in frames}
    gone = sorted({k for n in picked for k in n["values"] if frames and k not in known})
    fallback = [] if want else (await _db_characters(pid) or _sheet_characters(data_dir))
    characters = character_rows(picked, frame_rows, fallback)
    scenes = scene_rows(picked, [] if want else frames, frame_rows)
    with_data = sum(1 for r in frame_rows if r["_has"])
    any_data = bool(with_data or characters or scenes or any(n["has_data"] for n in picked))

    scope = f"ноды «{picked[0]['label']}»" if want else "работы с GPT"
    head = (
        f"{fmt.TITLE.replace('работы с GPT', scope)}, проект #{pid} «{title}»: кадров {len(frame_rows)}"
        f", сцен {len(scenes)}, персонажей {len(characters)}."
    )
    node_lines = _node_lines(picked)
    if gone:
        node_lines.append(
            f"- Записи в кадры, которых в проекте сейчас нет (архивные или пересобранные): "
            f"{len(gone)}, в таблицу не вошли."
        )
    summary = {
        "frames": len(frame_rows),
        "frames_with_data": with_data,
        "scenes": len(scenes),
        "characters": len(characters),
        "gpt_nodes": len(picked),
        "nodes_without_data": [n["label"] for n in picked if not n["has_data"]],
        "frames_source": source,
        "written_to_missing_frames": len(gone),
        "columns": [label for _k, label in columns],
    }
    clean_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in frame_rows]
    out: dict[str, Any] = {
        "project_id": pid,
        "format": kind,
        "node_key": want or None,
        "summary": summary,
        "nodes": [
            {
                k: n[k]
                for k in (
                    "node_key",
                    "label",
                    "status_ru",
                    "frames_written",
                    "fields_ru",
                    "characters",
                    "scenes",
                    "has_data",
                    "note",
                    "source",
                )
            }
            for n in picked
        ],
        "characters": characters,
        "scenes": scenes,
        "columns": [{"key": k, "label": label} for k, label in columns],
    }
    if not any_data:
        # Данных нет: честно и без файлов. Кадры разбивки назовём, но придумывать сцены не будем.
        tail = (
            f"Кадров разбивки {len(frames)} ({source}), сцен и персонажей GPT в них не записал."
            if frames
            else "Кадров в проекте пока нет."
        )
        out.update(rows=clean_rows if kind == "json" else len(clean_rows), empty=True)
        out["text_ru"] = "\n".join([f"Проект #{pid} «{title}»: данных работы с GPT нет.", *node_lines, tail])
        return bi._safe(out)
    if kind == "json":
        out.update(rows=clean_rows, text_ru=head)
        return bi._safe(out)

    from app.web.routers.project_reports import report_url

    base = Path(data_dir) / "reports" / f"{fmt.FILE_PREFIX}_{pid}_{rg._stamp()}"
    xlsx_path, html_path = base.with_suffix(".xlsx"), base.with_suffix(".html")
    pairs = [
        ("Проект", f"#{pid} {title}"),
        ("Сформирован", datetime.now().isoformat(timespec="seconds")),
        ("Откуда кадры", source),
        ("Кадров", len(frame_rows)),
        ("Кадров с данными", with_data),
        ("Сцен", len(scenes)),
        ("Персонажей", len(characters)),
        ("Колонки", ", ".join(summary["columns"])),
    ]
    pairs += [(f"Нода «{n['label']}»", _node_line(n)) for n in picked]
    rg.write_workbook(
        xlsx_path,
        [
            (fmt.FRAMES_SHEET, columns, clean_rows),
            (fmt.CHARACTERS_SHEET, fmt.CHARACTER_COLUMNS, characters),
            (fmt.SCENES_SHEET, fmt.SCENE_COLUMNS, scenes),
            (fmt.NODES_SHEET, fmt.NODE_COLUMNS, out["nodes"]),
        ],
        pairs,
    )
    html_path.write_text(
        _html(
            bi._safe(
                {
                    "project_id": pid,
                    "title": title,
                    "stamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
                    "source": f"кадры {source}",
                    "columns": columns,
                    "rows": clean_rows,
                    "nodes": out["nodes"],
                    "characters": characters,
                    "scenes": scenes,
                }
            )
        ),
        encoding="utf-8",
    )
    html_url, xlsx_url = report_url(pid, html_path), report_url(pid, xlsx_path)
    primary = xlsx_path if kind == "xlsx" else html_path
    out.update(
        path=str(primary),
        rows=len(clean_rows),
        download_url=report_url(pid, primary),
        file_name=primary.name,
        file_format=primary.suffix.lstrip("."),
        html_path=str(html_path),
        xlsx_path=str(xlsx_path),
        downloads=[{"url": xlsx_url, "file_format": "xlsx"}, {"url": html_url, "file_format": "html"}],
    )
    link = f"Файлы: [Скачать отчёт (xlsx)]({xlsx_url}) · [Скачать отчёт (html)]({html_url})"
    table, shown = _chat_table(clean_rows, columns)
    parts = [head, link, *node_lines]
    if characters:
        parts.append("Персонажи:")
        parts += [_character_line(c) for c in characters[: fmt.CHAT_CHARACTERS]]
        if len(characters) > fmt.CHAT_CHARACTERS:
            parts.append(f"- и ещё {len(characters) - fmt.CHAT_CHARACTERS} (в файле).")
    if scenes:
        parts.append("Сцены:")
        parts += [_scene_line(c) for c in scenes[: fmt.CHAT_SCENES]]
        if len(scenes) > fmt.CHAT_SCENES:
            parts.append(f"- и ещё {len(scenes) - fmt.CHAT_SCENES} (в файле).")
    if clean_rows:
        parts.append("")
        parts.append(table)
        if shown < len(clean_rows):
            parts.append(f"\nПоказаны первые {shown} из {len(clean_rows)} кадров, полный отчёт в файле.")
    out["text_ru"] = "\n".join(parts)
    return bi._safe(out)
