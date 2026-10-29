"""Сверка: какая модель задана на ноде и какая реально ушла провайдеру.

Источники только для чтения: meta проекта (canvas_graph из строки проекта единой базы) и
studio-live.log. В логе три вида строк:

- ``node_llm|generation_llm: #<pid> <node> → <label> (<model>)`` — привязка текстовой модели к шагу;
- ``text_llm.chat → <provider> model=<model>`` — запрос к текстовому провайдеру
  (без номера проекта: относим к ближайшей ещё не закрытой привязке);
- ``outsee_api.image|video model=… project=<pid>``, ``kie_http: generate image|video
  <id> … project=<pid>``, ``kie_kling: createTask model=… project=<pid>`` — запросы
  картинок и видео, номер проекта в строке есть, ноды нет.

Текстовый вызов без номера проекта относим к самой ранней привязке, у которой ещё
не было вызова. Если за последние ``ACTIVE_WINDOW`` до вызова шли привязки других
проектов, сопоставление помечаем как неточное.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

ACTIVE_WINDOW = timedelta(minutes=15)
BIND_TTL = timedelta(minutes=30)

_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_BIND = re.compile(r"(?:node|generation)_llm: #(\d+) (\S+) → (.+) \(([^()]+)\)\s*$")
_TEXT = re.compile(r"text_llm\.chat → (.+?) model=(\S+)")
_OUTSEE = re.compile(r"outsee_api\.(image|video) model=(\S+).* project=(\d+)")
_KIE = re.compile(r"kie_http: generate (image|video) (\S+) \(model=[^)]*\).* project=(\d+)")
_KLING = re.compile(r"kie_kling: createTask model=(\S+).* project=(\d+)")

TEXT, IMAGE, VIDEO = "text", "image", "video"
MATCH, DRIFT, UNSURE, NO_CALL, NOT_RUN = "match", "mismatch", "unsure", "no_call", "not_run"
VERDICT_RU = {
    MATCH: "совпадает",
    DRIFT: "не совпадает",
    UNSURE: "не совпадает (сопоставление неточное)",
    NO_CALL: "вызова провайдера не видно",
    NOT_RUN: "в логе прогона нет",
}


@dataclass
class Bind:
    at: datetime
    project_id: int
    node: str
    label: str
    model: str
    sent: Call | None = None
    unsure: bool = False


@dataclass
class Call:
    at: datetime
    kind: str
    model: str
    provider: str
    project_id: int | None = None


@dataclass
class LogScan:
    binds: list[Bind] = field(default_factory=list)
    media: list[Call] = field(default_factory=list)
    text_calls: int = 0
    unmatched_text_calls: int = 0


def _stamp(line: str) -> datetime | None:
    m = _STAMP.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _message(line: str) -> str:
    parts = line.split(" | ", 3)
    return parts[3].strip() if len(parts) == 4 else line.strip()


def same_text_model(a: str | None, b: str | None) -> bool:
    """gpt-5.6-sol и gpt-5-6-sol — одна модель в записи разных провайдеров."""

    def norm(v: str | None) -> str:
        return str(v or "").strip().lower().replace(".", "-").replace("_", "-")

    return bool(norm(a)) and norm(a) == norm(b)


def same_media_model(a: str | None, b: str | None) -> bool:
    from app.services.media_route import canonical_media_id

    return bool(canonical_media_id(a)) and canonical_media_id(a) == canonical_media_id(b)


def scan_log(lines: Iterable[str]) -> LogScan:
    out = LogScan()
    pending: list[Bind] = []
    for line in lines:
        at = _stamp(line)
        if at is None:
            continue
        msg = _message(line)
        if m := _BIND.search(msg):
            bind = Bind(
                at=at,
                project_id=int(m.group(1)),
                node=m.group(2),
                label=m.group(3).strip(),
                model=m.group(4).strip(),
            )
            out.binds.append(bind)
            pending.append(bind)
            continue
        if m := _TEXT.search(msg):
            out.text_calls += 1
            pending = [b for b in pending if at - b.at <= BIND_TTL]
            if not pending:
                out.unmatched_text_calls += 1
                continue
            bind = pending.pop(0)
            bind.sent = Call(
                at=at, kind=TEXT, model=m.group(2), provider=m.group(1).strip(), project_id=bind.project_id
            )
            bind.unsure = any(
                b.project_id != bind.project_id and timedelta(0) <= at - b.at <= ACTIVE_WINDOW
                for b in out.binds
            )
            continue
        if m := _OUTSEE.search(msg):
            out.media.append(
                Call(at=at, kind=m.group(1), model=m.group(2), provider="outsee", project_id=int(m.group(3)))
            )
        elif m := _KIE.search(msg):
            out.media.append(
                Call(at=at, kind=m.group(1), model=m.group(2), provider="kie", project_id=int(m.group(3)))
            )
        elif m := _KLING.search(msg):
            # kling-2.6/image-to-video → kling-2-6, как id генератора в Студии.
            model = m.group(1).split("/", 1)[0].replace(".", "-")
            out.media.append(Call(at=at, kind=VIDEO, model=model, provider="kie", project_id=int(m.group(2))))
    return out


def _canvas_nodes(meta: dict) -> list[dict]:
    from app.services.canvas_graph import canvas_graph_from_meta

    cg = canvas_graph_from_meta(meta) or {}
    return [n for n in cg.get("nodes") or [] if isinstance(n, dict) and str(n.get("id") or "").strip()]


def _node_kind(eff: str) -> str | None:
    from app.services.action_layer.builtin_insight import _TEXT_TYPES
    from app.services.vibecode_catalog import IMAGE_NODE_TYPES, VIDEO_NODE_TYPES

    if eff.startswith("hitl_"):
        return None
    if eff in IMAGE_NODE_TYPES:
        return IMAGE
    if eff in VIDEO_NODE_TYPES:
        return VIDEO
    if eff in _TEXT_TYPES or eff.startswith("enrich_"):
        return TEXT
    return None


def _configured_text(meta: dict, key: str, eff: str) -> tuple[str, str]:
    from app.services.node_groups import is_script_frames_qc_gpt_node
    from app.services.vibecode_catalog import resolve_node_choice

    if is_script_frames_qc_gpt_node(meta, node_key=key):
        return "gpt-5.6-sol", "GPT 5.6 Sol (52pay · high), закреплена за группой"
    choice = resolve_node_choice(meta, node_key=key, node_type=eff) or {}
    model = str(choice.get("api_model") or choice.get("id") or "")
    return model, str(choice.get("label") or model)


def _configured_media(project: Any, key: str, eff: str, kind: str) -> tuple[str, str]:
    from app.bots.outsee_http import studio_id_to_outsee_image_slug, studio_id_to_outsee_video_slug
    from app.generation_options import IMAGE_GENERATORS_BY_ID, VIDEO_GENERATORS_BY_ID
    from app.services.media_route import image_provider_for, video_provider_for
    from app.services.vibecode_catalog import effective_image_generator_id, effective_video_generator_id

    if kind == IMAGE:
        gid = effective_image_generator_id(project, node_key=key, node_type=eff)
        choice = IMAGE_GENERATORS_BY_ID.get(gid)
        slug = choice.outsee_slug if choice else gid.replace("_", "-")
        if image_provider_for(slug) == "outsee":
            return studio_id_to_outsee_image_slug(gid), choice.label if choice else gid
        return slug, choice.label if choice else gid
    gid = effective_video_generator_id(project, node_key=key, node_type=eff)
    choice = VIDEO_GENERATORS_BY_ID.get(gid)
    slug = choice.outsee_slug if choice else gid.replace("_", "-")
    if video_provider_for(slug) == "outsee":
        return studio_id_to_outsee_video_slug(slug), choice.label if choice else gid
    return slug, choice.label if choice else gid


def _last_bind(scan: LogScan, pid: int, key: str, eff: str, raw: str) -> Bind | None:
    names = {key, eff, raw}
    for bind in reversed(scan.binds):
        if bind.project_id == pid and bind.node in names:
            return bind
    return None


def _last_media(scan: LogScan, pid: int, kind: str) -> Call | None:
    for call in reversed(scan.media):
        if call.project_id == pid and call.kind == kind:
            return call
    return None


def _text_row(project: Any, meta: dict, key: str, raw: str, eff: str, scan: LogScan) -> dict:
    configured, label = _configured_text(meta, key, eff)
    bind = _last_bind(scan, int(project.id), key, eff, raw)
    row = {
        "kind": TEXT,
        "configured": configured,
        "configured_label": label,
        "bound": bind.model if bind else None,
        "bound_at": bind.at.isoformat(sep=" ") if bind else None,
        "sent": None,
        "sent_provider": None,
        "sent_at": None,
        "note": "",
    }
    if bind is None:
        row["verdict"] = NOT_RUN
        return row
    if bind.sent is None:
        row["verdict"] = NO_CALL
        return row
    row.update(
        sent=bind.sent.model, sent_provider=bind.sent.provider, sent_at=bind.sent.at.isoformat(sep=" ")
    )
    if same_text_model(bind.sent.model, bind.model):
        row["verdict"] = MATCH
    else:
        row["verdict"] = UNSURE if bind.unsure else DRIFT
    if bind.unsure:
        row["note"] = "в это время шли другие проекты"
    if not same_text_model(configured, bind.model):
        row["note"] = "; ".join(filter(None, [row["note"], "на ноде сейчас другая модель, чем в прогоне"]))
    return row


def _media_row(project: Any, key: str, eff: str, kind: str, scan: LogScan) -> dict:
    configured, label = _configured_media(project, key, eff, kind)
    call = _last_media(scan, int(project.id), kind)
    row = {
        "kind": kind,
        "configured": configured,
        "configured_label": label,
        "bound": None,
        "bound_at": None,
        "sent": call.model if call else None,
        "sent_provider": call.provider if call else None,
        "sent_at": call.at.isoformat(sep=" ") if call else None,
        "note": "",
    }
    if call is None:
        row["verdict"] = NOT_RUN
        return row
    row["verdict"] = MATCH if same_media_model(call.model, configured) else DRIFT
    row["note"] = "в логе нет ноды, последний вызов по проекту"
    if row["verdict"] == DRIFT and kind == VIDEO and same_media_model(call.model, "kling-2-6"):
        row["note"] += "; Kling 2.6 — запасная модель после ошибок основной"
    return row


def project_rows(project: Any, scan: LogScan) -> list[dict]:
    """Строки сверки по всем нодам проекта с моделью (текст, картинки, видео)."""
    from app.services.excel_gpt_node import effective_node_type

    meta = project.meta if isinstance(project.meta, dict) else {}
    rows = []
    for node in _canvas_nodes(meta):
        key, raw = str(node["id"]), str(node.get("type") or "")
        eff = effective_node_type(node) or raw
        kind = _node_kind(eff) or _node_kind(raw)
        if kind is None:
            continue
        row = (
            _text_row(project, meta, key, raw, eff, scan)
            if kind == TEXT
            else _media_row(project, key, eff, kind, scan)
        )
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        rows.append(
            {
                "project_id": int(project.id),
                "node_key": key,
                "node_type": eff,
                "title": str(data.get("label") or data.get("title") or eff),
                **row,
                "verdict_ru": VERDICT_RU[row["verdict"]],
            }
        )
    return rows


def summary(rows: list[dict]) -> dict[str, int]:
    out = {v: 0 for v in VERDICT_RU}
    for r in rows:
        out[r["verdict"]] += 1
    return out
