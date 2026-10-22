"""Отчёты по данным нод «Работа с GPT» и сводный отчёт по нескольким проектам.

Цифры собирает код (без модели). Вид таблиц и подписи — в report_template.py.
Что нода записала, берём из её сохранённого ответа (excel_gpt_uploads/<нода>/gpt_reply.txt):
тот же разбор apply-ops, что применяет ответ к базе, но без записи.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.services.action_layer import report_template as tpl
from app.services.action_layer.context import ActionCallError, ActionContext

_REPLY_NAME = "gpt_reply.txt"
_REPLY_MAX_BYTES = 4 * 1024 * 1024
_CHAT_LINE_MAX = 220


# ── Общие помощники рендера ──


def clip(value: Any, n: int = 200) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def md_cell(value: Any) -> str:
    return clip(value, 160).replace("|", "/") if value not in (None, "") else "—"


def markdown_table(rows: list[dict], columns: tuple[tuple[str, str], ...]) -> str:
    head = "| " + " | ".join(label for _k, label in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(md_cell(r.get(k)) for k, _l in columns) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def xlsx_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "@")):
        return " " + value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    return value


def write_workbook(
    path: Path,
    sheets: list[tuple[str, tuple[tuple[str, str], ...], list[dict]]],
    summary: list[tuple[str, Any]],
    *,
    widths: dict[str, dict[str, int]] | None = None,
) -> None:
    """Один xlsx: лист «Итог» последним, на каждом листе шапка жирным и закреплена."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    first = True
    for name, columns, rows in sheets:
        ws = wb.active if first else wb.create_sheet(name)
        ws.title = name
        first = False
        ws.append([label for _k, label in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rows:
            ws.append([xlsx_value(r.get(k)) for k, _l in columns])
        fixed = (widths or {}).get(name) or {}
        for i, (key, label) in enumerate(columns, start=1):
            letter = get_column_letter(i)
            longest = max([len(str(label))] + [len(str(xlsx_value(r.get(key)) or "")) for r in rows[:200]])
            ws.column_dimensions[letter].width = fixed.get(letter) or min(
                max(10, longest + 2), tpl.COLUMN_WIDTH_MAX
            )
        ws.freeze_panes = "A2"
    total = wb.active if first else wb.create_sheet(tpl.SUMMARY_SHEET)
    total.title = tpl.SUMMARY_SHEET
    for name, value in summary:
        total.append([name, xlsx_value(value)])
    total.column_dimensions["A"].width = 36
    total.column_dimensions["B"].width = 60
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── Что записала нода «Работа с GPT» ──


def read_reply(data_dir: str | Path, node_key: str) -> str | None:
    """Сохранённый ответ ноды; только внутри папки проекта, без обхода путей."""
    root = Path(data_dir).resolve()
    base = (root / "excel_gpt_uploads").resolve()
    path = (base / str(node_key) / _REPLY_NAME).resolve()
    if path.parent.parent != base or not path.is_file():
        return None
    try:
        if path.stat().st_size > _REPLY_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _canon_field(raw: Any) -> str:
    from app.services.db_apply import FIELD_ALIASES

    key = str(raw).strip()
    return FIELD_ALIASES.get(key.lower().replace(" ", "_")) or key


def _frame_number(ref: Any, num_by_uuid: dict[str, int]) -> int | str:
    text = str(ref if ref is not None else "").strip()
    if text in num_by_uuid:
        return num_by_uuid[text]
    if text.isdigit():
        return int(text)
    return f"? {text[:12]}" if text else "?"


def _card_name(card: Any, *keys: str) -> str:
    if not isinstance(card, dict):
        return clip(card, 60)
    parts = [clip(card.get(k), 60) for k in keys if str(card.get(k) or "").strip()]
    return " — ".join(dict.fromkeys(parts)) or "без названия"


def writes_from_reply(text: str | None, num_by_uuid: dict[str, int]) -> dict:
    """Разбор ответа: кадр → поля, поля проекта, персонажи, сцены. Ничего не пишет."""
    out: dict[str, Any] = {
        "parsed": False,
        "frames": {},
        "project_fields": [],
        "replace_frames": None,
        "characters": [],
        "scenes": [],
        "partial": False,
    }
    if not text:
        return out
    from app.services.db_apply import extract_apply_ops_json

    try:
        data = extract_apply_ops_json(text)
    except Exception:  # noqa: BLE001 - кривой ответ не роняет отчёт
        data = None
    if not isinstance(data, dict):
        return out
    out["parsed"] = True
    out["partial"] = bool(data.get("_salvaged_partial"))
    ops = data.get("ops") if isinstance(data.get("ops"), list) else data.get("actions")
    frames: dict[Any, list[str]] = {}
    values: dict[Any, dict[str, str]] = {}
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        target = op.get("target") or "frame"
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        names = [_canon_field(k) for k in fields]
        if target == "frame":
            num = _frame_number(op.get("frame_uuid") or op.get("frame_number") or op.get("кадр"), num_by_uuid)
            known = frames.setdefault(num, [])
            known.extend(n for n in names if n not in known)
            slot = values.setdefault(num, {})
            for raw, value in fields.items():
                slot[_canon_field(raw)] = "" if value is None else str(value)
        elif target == "project":
            out["project_fields"].extend(n for n in names if n not in out["project_fields"])
        elif target == "replace_frames":
            specs = op.get("frames") or op.get("кадры") or []
            out["replace_frames"] = len(specs) if isinstance(specs, list) else 0
    out["frames"] = dict(sorted(frames.items(), key=lambda kv: (isinstance(kv[0], str), kv[0])))
    out["values"] = values
    chars = data.get("characters") if isinstance(data.get("characters"), list) else []
    out["characters"] = [_card_name(c, "id", "code", "имя", "name") for c in chars]
    scenes = data.get("scenes") if isinstance(data.get("scenes"), list) else []
    out["scenes"] = [_card_name(s, "id_scene", "id", "scene_id", "место", "place", "title") for s in scenes]
    return out


def _labels_ru(names: list[str]) -> str:
    return ", ".join(tpl.field_label(n) for n in names)


def prompt_title(variant: str | None) -> str:
    if not variant:
        return ""
    from app.services import prompt_library as pl

    try:
        titles = pl.load_file_meta(pl.EXCEL_GPT_UNIFIED_STEP)
    except Exception:  # noqa: BLE001
        titles = {}
    info = titles.get(variant) if isinstance(titles.get(variant), dict) else {}
    title = str(info.get("title") or "").strip()
    if not title or title == variant or variant in title:
        return title or variant
    return f"{title} ({variant})"


async def frame_numbers(pid: int) -> dict[str, int]:
    from sqlalchemy import select

    from app.models import Frame
    from app.services.action_layer import builtin_insight as bi

    try:
        async with bi._sessions()() as s:
            rows = (await s.execute(select(Frame.uuid, Frame.number).where(Frame.project_id == pid))).all()
    except SQLAlchemyError:
        return {}
    return {str(u): int(n) for u, n in rows if u}


def is_gpt_node(node: dict | None, node_type: str = "") -> bool:
    from app.services.excel_gpt_node import is_excel_gpt_node_type

    raw = str((node or {}).get("type") or node_type or "")
    return is_excel_gpt_node_type(raw)


def gpt_nodes(project: Any, detail: dict, cards: dict[str, dict], num_by_uuid: dict[str, int]) -> list[dict]:
    """Строки по нодам «Работа с GPT» в порядке канваса."""
    from app.services.action_layer import builtin_insight as bi
    from app.services.action_layer.builtin_group import node_label_ru

    meta = bi._meta(detail)
    out: list[dict] = []
    for node in bi._canvas_nodes(meta):
        key = str(node["id"])
        if not is_gpt_node(node):
            continue
        card = cards.get(key) or {}
        variant = (card.get("prompt") or {}).get("variant") if card else None
        if not card:
            raw, eff = bi._node_types(node)
            variant = (bi._prompt_for(detail, key, raw, eff) or {}).get("variant")
        writes = writes_from_reply(read_reply(project.data_dir, key), num_by_uuid)
        fields: list[str] = []
        for names in writes["frames"].values():
            fields.extend(n for n in names if n not in fields)
        note = ""
        if not writes["parsed"]:
            note = "сохранённого ответа с данными нет" if card.get("status_ru") != "не запускалась" else ""
        elif writes["partial"]:
            note = "ответ был обрезан, разобрана целая часть"
        if writes["project_fields"]:
            note = "; ".join(x for x in (note, "проект: " + _labels_ru(writes["project_fields"])) if x)
        if writes["replace_frames"] is not None:
            note = "; ".join(x for x in (note, f"пересобрала кадры: {writes['replace_frames']}") if x)
        out.append(
            {
                "node_key": key,
                "label": node_label_ru(meta, key),
                "prompt_variant": variant or "",
                "prompt_title": prompt_title(variant),
                "model": card.get("model") or "",
                "status_ru": card.get("status_ru") or "не запускалась",
                "frames_written": len(writes["frames"]),
                "fields": fields,
                "fields_ru": _labels_ru(fields),
                "frames": {str(k): v for k, v in writes["frames"].items()},
                "values": {str(k): v for k, v in (writes.get("values") or {}).items()},
                "characters": writes["characters"],
                "characters_count": len(writes["characters"]),
                "scenes": writes["scenes"],
                "scenes_count": len(writes["scenes"]),
                "project_fields": writes["project_fields"],
                "note": note,
            }
        )
    return out


def gpt_frame_rows(nodes: list[dict]) -> list[dict]:
    """Поле кадра — строка, значение целиком. Поле, которое потом записала другая нода, помечено:
    в кадре осталось её значение, а это пропало («последний побеждает»)."""
    later: dict[tuple[str, str], str] = {}
    rows: list[dict] = []
    for n in reversed(nodes):
        for frame, names in n["frames"].items():
            vals = (n.get("values") or {}).get(str(frame)) or {}
            for name in names:
                key = (str(frame), name)
                rows.append(
                    {
                        "label": n["label"],
                        "frame": frame,
                        "field": name,
                        "field_ru": tpl.field_label(name),
                        "value": vals.get(name, ""),
                        "overwritten": f"да, потом «{later[key]}»" if key in later else "",
                    }
                )
            for name in names:
                later.setdefault((str(frame), name), n["label"])
    order = {n["label"]: i for i, n in enumerate(nodes)}
    return sorted(
        rows, key=lambda r: (order.get(r["label"], 0), (isinstance(r["frame"], str), r["frame"]), r["field"])
    )


def gpt_list_rows(nodes: list[dict]) -> list[dict]:
    rows = [{"label": n["label"], "kind": "персонаж", "name": c} for n in nodes for c in n["characters"]]
    rows += [{"label": n["label"], "kind": "сцена", "name": s} for n in nodes for s in n["scenes"]]
    return rows


def gpt_text(nodes: list[dict]) -> str:
    """Короткий текст сверху: по одной строке на ноду."""
    if not nodes:
        return ""
    done = sum(1 for n in nodes if n["status_ru"] == "готово")
    lines = [f"{tpl.GPT_SECTION_TITLE}: нод {len(nodes)}, готово {done}."]
    for n in nodes:
        parts = [f"промт «{n['prompt_title']}»" if n["prompt_title"] else "", n["model"], n["status_ru"]]
        what = []
        if n["frames_written"]:
            what.append(
                f"заполнила кадров {n['frames_written']}"
                + (f" ({clip(n['fields_ru'], 80)})" if n["fields_ru"] else "")
            )
        if n["characters_count"]:
            what.append(f"персонажей {n['characters_count']}")
        if n["scenes_count"]:
            what.append(f"сцен {n['scenes_count']}")
        line = f"{n['label']}: " + ", ".join(p for p in parts if p)
        line += (". " + ", ".join(what) + ".") if what else "."
        lines.append(clip(line, _CHAT_LINE_MAX))
    return "\n".join(lines)


async def project_counts(pid: int, meta: dict) -> dict:
    """Персонажи в базе и сцены в реестре — для итога отчёта."""
    from sqlalchemy import func, select

    from app.models import Entity
    from app.services.action_layer import builtin_insight as bi

    chars = None
    try:
        async with bi._sessions()() as s:
            chars = int(
                await s.scalar(
                    select(func.count(Entity.id)).where(Entity.project_id == pid, Entity.type == "character")
                )
                or 0
            )
    except SQLAlchemyError:
        chars = None
    reg = meta.get("scene_registry")
    scenes = (
        sum(1 for r in reg if not (isinstance(r, dict) and r.get("активен") is False))
        if isinstance(reg, list)
        else 0
    )
    return {"characters_in_project": chars, "scenes_in_registry": scenes}


# ── Отчёт по нескольким проектам ──


def shared_reports_dir() -> Path:
    from app.settings import settings

    return Path(settings.data_dir) / "reports"


async def _project_ids(params: dict) -> tuple[list[int], int]:
    from sqlalchemy import func, select

    from app.models import Project
    from app.services.action_layer import builtin_insight as bi

    ids = [int(x) for x in params.get("project_ids") or []]
    if ids:
        return list(dict.fromkeys(ids))[: tpl.MAX_PROJECTS], len(set(ids))
    async with bi._sessions()() as s:
        total = int(await s.scalar(select(func.count(Project.id))) or 0)
        rows = (await s.scalars(select(Project.id).order_by(Project.id.desc()).limit(tpl.MAX_PROJECTS))).all()
    return sorted(int(r) for r in rows), total


async def _grouped(pids: list[int]) -> dict[str, dict[int, float]]:
    from sqlalchemy import func, select, text

    from app.models import Artifact, ArtifactKind, Frame
    from app.services.action_layer import builtin_insight as bi

    out: dict[str, dict[int, float]] = {"frames": {}, "images": {}, "videos": {}, "spent": {}}
    if not pids:
        return out
    from app.services.frame_store import session_factory_for

    async def count(s, ids: list[int]) -> None:
        # Счёт по проектам из единой базы; max — на случай повторного прохода по тем же id.
        for pid, n in (
            await s.execute(
                select(Frame.project_id, func.count(Frame.id))
                .where(Frame.project_id.in_(ids))
                .group_by(Frame.project_id)
            )
        ).all():
            out["frames"][int(pid)] = max(out["frames"].get(int(pid), 0), int(n))
        for pid, kind, n in (
            await s.execute(
                select(Artifact.project_id, Artifact.kind, func.count(Artifact.id))
                .where(
                    Artifact.project_id.in_(ids),
                    Artifact.kind.in_([ArtifactKind.scene_image, ArtifactKind.scene_video]),
                )
                .group_by(Artifact.project_id, Artifact.kind)
            )
        ).all():
            kind_v = kind.value if hasattr(kind, "value") else str(kind)
            bucket = "images" if kind_v == ArtifactKind.scene_image.value else "videos"
            out[bucket][int(pid)] = max(out[bucket].get(int(pid), 0), int(n))

    for pid in pids:
        try:
            async with (await session_factory_for(pid))() as s:
                await count(s, [pid])
        except Exception:  # noqa: BLE001
            continue
    try:
        async with bi._sessions()() as s:
            await count(s, pids)
            marks = ", ".join(f":p{i}" for i in range(len(pids)))
            rows = (
                await s.execute(
                    text(
                        f"SELECT project_id, SUM(usd) FROM spend_ledger WHERE project_id IN ({marks}) "
                        "GROUP BY project_id"
                    ),
                    {f"p{i}": p for i, p in enumerate(pids)},
                )
            ).all()
            for pid, usd in rows:
                out["spent"][int(pid)] = float(usd or 0.0)
    except SQLAlchemyError:
        pass
    return out


async def _project_detail(ctx: ActionContext, pid: int, row: Any) -> dict:
    from app.services.action_layer.builtin_group import _effective_project

    try:
        detail = await _effective_project(ctx, pid)
        if isinstance(detail, dict):
            return detail
    except ActionCallError:
        pass
    return {
        "id": pid,
        "title": row.title,
        "topic": row.topic,
        "meta": dict(row.meta or {}),
        "prompt_overrides": dict(row.prompt_overrides or {}),
    }


async def _project_row_report(ctx: ActionContext, pid: int, row: Any, counts: dict) -> dict:
    from app.services.action_layer import builtin_insight as bi

    if row is None:
        return {
            "project_id": pid,
            "title": "проект не найден",
            "status_ru": "—",
            "found": False,
            "gpt_nodes": [],
        }
    detail = await _project_detail(ctx, pid, row)
    meta = bi._meta(detail)
    status = bi._row_status(row) or str(detail.get("status") or "")
    steps = bi._pipeline(status, meta)
    done = sum(1 for s in steps if s["state"] == "готово")
    runs = {str(nr.get("node_key")): nr for nr in await bi._node_runs(ctx, pid)}
    by_key = {str(n["id"]): n for n in bi._canvas_nodes(meta)}
    project = bi._transient(pid, detail, row)
    cards: dict[str, dict] = {}
    for key, node in by_key.items():
        if is_gpt_node(node):
            cards[key] = bi._node_card(
                project, detail, key, node, runs.get(key), checks=[], cost=None, detailed=False
            )
    nodes = gpt_nodes(project, detail, cards, await frame_numbers(pid))
    gpt = "; ".join(
        f"{n['label']}: {n['prompt_title'] or 'промт по умолчанию'} ({n['status_ru']})" for n in nodes
    )
    spent = counts["spent"].get(pid)
    return {
        "project_id": pid,
        "found": True,
        "title": clip(detail.get("title") or row.title or row.topic, 80),
        "status": status,
        "status_ru": bi._status_ru(status),
        "steps": f"{done} из {len(steps)}",
        "steps_done": done,
        "steps_total": len(steps),
        "gpt": gpt or "нет",
        "gpt_nodes": [
            {
                k: n[k]
                for k in (
                    "node_key",
                    "label",
                    "prompt_variant",
                    "prompt_title",
                    "status_ru",
                    "frames_written",
                    "characters_count",
                    "scenes_count",
                )
            }
            for n in nodes
        ],
        "frames": int(counts["frames"].get(pid, 0)),
        "images": int(counts["images"].get(pid, 0)),
        "videos": int(counts["videos"].get(pid, 0)),
        "spent_usd": None if spent is None else round(spent, 4),
        "updated_at": bi._iso(getattr(row, "updated_at", None)),
    }


async def report_projects(ctx: ActionContext, params: dict) -> dict:
    from app.models import Project
    from app.services.action_layer import builtin_insight as bi

    fmt = params.get("kind", "chat")
    pids, total = await _project_ids(params)
    counts = await _grouped(pids)
    rows: list[dict] = []
    for pid in pids:
        try:
            async with bi._sessions()() as s:
                row = await s.get(Project, pid)
        except SQLAlchemyError:
            row = None
        rows.append(await _project_row_report(ctx, pid, row, counts))
    found = [r for r in rows if r.get("found")]
    spent = sum(r["spent_usd"] or 0.0 for r in found)
    missing = [r["project_id"] for r in rows if not r.get("found")]
    head = (
        f"{tpl.PROJECTS_TITLE}: {len(found)}"
        + (
            f" (последние {len(pids)} из {total})"
            if not params.get("project_ids") and total > len(pids)
            else ""
        )
        + f". Кадров {sum(r['frames'] for r in found)}, картинок {sum(r['images'] for r in found)}, "
        f"видео {sum(r['videos'] for r in found)}, траты ${round(spent, 4)} (оценка)."
    )
    if missing:
        head += " Не найдены: " + ", ".join(f"#{p}" for p in missing) + "."
    summary = {
        "projects": len(found),
        "missing": missing,
        "frames": sum(r["frames"] for r in found),
        "images": sum(r["images"] for r in found),
        "videos": sum(r["videos"] for r in found),
        "spent_usd_estimate": round(spent, 4),
    }
    out: dict[str, Any] = {"format": fmt, "project_ids": pids, "summary": summary}
    if fmt == "json":
        out.update(rows=rows, text_ru=head)
        return bi._safe(out)
    pairs = [
        ("Отчёт", tpl.PROJECTS_TITLE),
        ("Сформирован", datetime.now().isoformat(timespec="seconds")),
        ("Проектов", len(found)),
        ("Кадров", summary["frames"]),
        ("Картинок", summary["images"]),
        ("Видео", summary["videos"]),
        ("Траты, $ (оценка)", summary["spent_usd_estimate"]),
    ]
    if missing:
        pairs.append(("Не найдены", ", ".join(f"#{p}" for p in missing)))
    from app.web.routers.project_reports import SHARED_PREFIX, shared_report_url

    path = shared_reports_dir() / f"{SHARED_PREFIX}{_stamp()}.xlsx"
    write_workbook(path, [(tpl.PROJECTS_SHEET, tpl.PROJECTS_COLUMNS, rows)], pairs)
    url = shared_report_url(path)
    out.update(path=str(path), download_url=url, file_name=path.name, rows=len(rows), file_format="xlsx")
    shown = rows[: tpl.CHAT_ROWS]
    table = markdown_table(shown, tpl.PROJECTS_CHAT_COLUMNS)
    tail = f"\n\nЕщё проектов: {len(rows) - len(shown)} (все в файле)." if len(rows) > len(shown) else ""
    gpt_lines = [
        f"- #{r['project_id']}: {clip(r['gpt'], 160)}" for r in shown if r.get("gpt") not in (None, "", "нет")
    ]
    gpt_text_md = ("\n\n" + tpl.GPT_SECTION_TITLE + ":\n" + "\n".join(gpt_lines)) if gpt_lines else ""
    out["markdown"] = f"## {tpl.PROJECTS_TITLE}\n\n{head}\n\n{table}{tail}{gpt_text_md}"
    out["text_ru"] = head + f" Файл отчёта готов: [Скачать отчёт (xlsx)]({url})"
    return bi._safe(out)
