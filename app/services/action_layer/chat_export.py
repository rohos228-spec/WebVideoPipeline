from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.services.action_layer.builtin_group import _PID
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import RISK_READ, ActionSpec

ACTION_ID = "chat.export"
_MAX_DAYS = 90
_SESSION_PID = re.compile(r"studio-p(\d+)")
_PLAN_LIMIT = 8


def _since(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _project_of(session_id: str) -> int | None:
    m = _SESSION_PID.search(str(session_id or ""))
    return int(m.group(1)) if m else None


def _plan_titles(plan_json: str | None) -> list[str]:
    try:
        plan = json.loads(plan_json or "null")
    except ValueError:
        return []
    steps = plan.get("steps") if isinstance(plan, dict) else plan
    if not isinstance(steps, list):
        return []
    out: list[str] = []
    for step in steps[:_PLAN_LIMIT]:
        if isinstance(step, dict):
            out.append(str(step.get("title_ru") or step.get("action") or "")[:120])
    return [s for s in out if s]


async def fetch_rows(days: int, project_id: int | None) -> list[dict[str, Any]]:
    from app.db import session_scope

    since = _since(days)
    sql = """
        SELECT m.id, m.session_id, m.task_id, m.role, m.content, m.mode, m.created_at,
               t.status AS task_status, t.error AS task_error, t.plan_json, t.goal,
               t.created_at AS task_created, t.updated_at AS task_updated, t.project_id AS task_project
        FROM orch_messages m
        LEFT JOIN orch_tasks t ON t.id = m.task_id
        WHERE m.created_at >= :since
        ORDER BY m.created_at, m.id
    """
    async with session_scope() as session:
        res = await session.execute(text(sql), {"since": since})
        rows = [dict(r._mapping) for r in res]
    out: list[dict[str, Any]] = []
    for r in rows:
        pid = r.get("task_project") or _project_of(r["session_id"])
        if project_id is not None and pid != int(project_id):
            continue
        r["project_id"] = pid
        out.append(r)
    return out


def _fmt_time(value: Any) -> str:
    s = str(value or "")
    return s.replace("T", " ")[:19]


def _legacy_mark(mode: Any) -> str:
    return " (старый путь окна)" if str(mode or "") == "legacy" else ""


def render_md(rows: list[dict[str, Any]], days: int, project_id: int | None) -> str:
    lines = [f"# Переписка чата «ИИ-агент» за {days} дн.", ""]
    if project_id is not None:
        lines[0] += f", проект {project_id}"
    if not rows:
        lines.append("Реплик за период нет.")
        return "\n".join(lines) + "\n"
    by_project: dict[Any, list[dict[str, Any]]] = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(r)
    users = sum(1 for r in rows if r["role"] == "user")
    lines.append(f"Реплик человека: {users}, ответов чата: {len(rows) - users}, проектов: {len(by_project)}.")
    for pid, items in sorted(by_project.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        lines += ["", f"## Проект {pid}" if pid is not None else "## Без проекта"]
        seen_tasks: set[int] = set()
        for r in items:
            who = "Человек" if r["role"] == "user" else "Чат"
            lines.append(f"- {_fmt_time(r['created_at'])} **{who}**{_legacy_mark(r['mode'])}: {str(r['content']).strip()}")
            tid = r.get("task_id")
            if r["role"] == "user" and tid and tid not in seen_tasks and r.get("task_status"):
                seen_tasks.add(int(tid))
                plan = _plan_titles(r.get("plan_json"))
                detail = f"  задача #{tid}: {r['task_status']}"
                if plan:
                    detail += "; план: " + " → ".join(plan)
                if r.get("task_error"):
                    detail += f"; ошибка: {str(r['task_error'])[:300]}"
                lines.append(detail)
    return "\n".join(lines) + "\n"


def _jsonl(rows: list[dict[str, Any]]) -> str:
    keys = ("id", "created_at", "project_id", "session_id", "task_id", "role", "mode", "content",
            "task_status", "task_error", "goal", "plan_json")
    return "".join(json.dumps({k: r.get(k) for k in keys}, ensure_ascii=False) + "\n" for r in rows)


async def _chat_export(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.report_gpt import _stamp, shared_reports_dir
    from app.web.routers.project_reports import SHARED_PREFIX, shared_report_url

    days = max(1, min(_MAX_DAYS, int(params.get("days") or 7)))
    pid = params.get("project_id")
    pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
    rows = await fetch_rows(days, pid)
    base = shared_reports_dir() / f"{SHARED_PREFIX}chat_{_stamp()}"
    base.parent.mkdir(parents=True, exist_ok=True)
    md_path, jl_path = base.with_suffix(".md"), base.with_suffix(".json")
    md_path.write_text(render_md(rows, days, pid), encoding="utf-8")
    jl_path.write_text(_jsonl(rows), encoding="utf-8")
    users = sum(1 for r in rows if r["role"] == "user")
    projects = sorted({r["project_id"] for r in rows if r["project_id"] is not None})
    scope = f"проекта {pid}" if pid is not None else "всех проектов"
    text_ru = (
        f"Переписка чата за {days} дн. {scope}: реплик человека {users}, ответов чата {len(rows) - users}"
        + (f", проекты {', '.join(map(str, projects))}" if projects else "")
        + ". Файл для чтения (md) и файл для разбора (json) по ссылкам ниже."
    )
    return {
        "text_ru": text_ru,
        "days": days,
        "project_id": pid,
        "messages": len(rows),
        "user_messages": users,
        "projects": projects,
        "download_url": shared_report_url(md_path),
        "file_format": "md",
        "downloads": [
            {"url": shared_report_url(md_path), "file_format": "md"},
            {"url": shared_report_url(jl_path), "file_format": "json"},
        ],
        "path": str(Path(md_path)),
    }


def chat_export_spec() -> ActionSpec:
    from app.services.action_layer.builtin_insight import _schema

    return ActionSpec(
        id=ACTION_ID,
        title_ru="Выгрузка переписки чата",
        description_ru="Вся переписка чата «ИИ-агент» за период одним файлом: реплики человека и "
        "чата по всем проектам (или одному), к каждой просьбе — план задачи, статус и ошибка. "
        "days — за сколько дней (по умолчанию 7, до 90). Файлы md и json со ссылками. "
        "Только чтение, бесплатно.",
        params_schema=_schema(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": _MAX_DAYS, "default": 7},
                "project_id": _PID,
            },
            [],
        ),
        executor=_chat_export,
        risk=RISK_READ,
        idempotent=True,
        tags=("chat", "export", "переписка", "история", "диалоги", "журнал", "выгрузка", "чат"),
    )


_EXPORT_RE = re.compile(
    r"(?:выгруз|скин|сохран|собер|сделай|дай|отдай|покаж|экспорт|выдай|пришли)\w*\s+(?:мне\s+|всю\s+|все\s+)?"
    r"(?:перепис|истори\w*\s+(?:чата|диалог|перепис|общени)|журнал\s+чата|диалог|логи?\s+чата|перепис\w*\s+чата)"
    r"|перепис\w*\s+(?:чата\s+)?(?:за|одним|файлом|выгруз|скин)"
)
_DAYS_RE = re.compile(r"(?:за\s+)?(\d{1,2})\s*(?:дн|день|дня|дней|сут)")


def export_params(norm: str) -> dict[str, Any] | None:
    if not _EXPORT_RE.search(norm):
        return None
    days = 7
    if "сегодня" in norm or "за день" in norm:
        days = 1
    elif "вчера" in norm:
        days = 2
    elif "месяц" in norm:
        days = 30
    elif "недел" in norm:
        days = 7 if not re.search(r"две\s+недел|2\s+недел", norm) else 14
    m = _DAYS_RE.search(norm)
    if m:
        days = max(1, min(_MAX_DAYS, int(m.group(1))))
    return {"days": days}
