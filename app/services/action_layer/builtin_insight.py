from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.services.action_layer.builtin_group import _NODE_KEY, _PID, _effective_project
from app.services.action_layer.builtin_montage import _PRE_PROJECT
from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import RISK_READ, ActionRegistry, ActionSpec, Precondition

INSIGHT_IDS: tuple[str, ...] = (
    "project.status",
    "node.output",
    "diag.incidents",
    "logs.tail",
    "spend.summary",
    "projects.overview",
    "report.nodes",
    "report.projects",
    "report.gpt_data",
)

_STEP_RU = {
    "plan": "сценарий",
    "script": "закадровый текст",
    "split": "разбивка на кадры",
    "scene_d": "дизайн сцен",
    "scene_asm": "сборка сцен",
    "hero": "персонажи",
    "items": "предметы",
    "enrich_1": "работа с GPT 1",
    "enrich_2": "работа с GPT 2",
    "enrich_3": "работа с GPT 3",
    "enrich_4": "работа с GPT 4",
    "enrich_5": "работа с GPT 5",
    "excel_gpt": "работа с GPT",
    "img_pr": "промты картинок",
    "img": "картинки",
    "anim_pr": "промты видео",
    "video": "видео",
    "audio": "озвучка",
    "music": "музыка",
    "sfx_plan": "план звуков",
    "sfx_gen": "звуки",
    "assemble": "монтаж",
    "publish": "публикация",
}
_TYPE_RU = {
    "plan": "План",
    "script": "Закадровый текст",
    "split": "Разбивка",
    "hero": "Персонажи",
    "items": "Предметы",
    "image_prompts": "Промты картинок",
    "images": "Картинки",
    "animation_prompts": "Промты анимации",
    "videos": "Видео",
    "audio": "Озвучка",
    "music": "Музыка",
    "assemble": "Сборка",
    "publish": "Публикация",
    "topic": "Тема",
    "storage": "Хранилище",
    "excel_gpt": "Работа с GPT",
    "sd_agent": "Агент сцен",
    "sd_assemble": "Сборка сцен",
    "scene_design": "Дизайн сцен",
    "sfx_plan": "План звуков",
    "sfx_gen": "Звуки",
    "shot_menu": "Меню съёмки",
    "excel_feed": "Вход Excel",
}
_NODE_STATUS_RU = {
    "pending": "ждёт",
    "queued": "в очереди",
    "running": "выполняется",
    "waiting_hitl": "ждёт решения человека",
    "done": "готово",
    "failed": "ошибка",
    "skipped": "пропущена",
}
_PROJECT_STATUS_RU = {
    "new": "новый",
    "paused": "на паузе",
    "failed": "ошибка",
    "assembled": "смонтирован",
    "published": "опубликован",
}
_TEXT_TYPES = frozenset(
    {
        "excel_gpt",
        "plan",
        "script",
        "split",
        "image_prompts",
        "sfx_plan",
        "animation_prompts",
        "sd_agent",
        "sd_assemble",
        "scene_design",
    }
)
_IMAGE_TYPES = frozenset({"images", "hero", "items"})
_VIDEO_TYPES = frozenset({"videos"})
_CHECK_STEP = {
    "img_pr_vo_coverage": "img_pr",
    "anim_pr_coverage": "anim_pr",
    "excel_gpt_gate": "excel_gpt",
    "scenes_png": "img",
    "videos_mp4": "video",
    "heroes_png": "hero",
    "excel_hero_fields_sane": "hero",
    "hero_quality": "hero",
    "final_mp4": "assemble",
    "frames_xlsx_parity": "split",
    "project_xlsx": "split",
    "voiceover_clean": "script",
}
_RETRY_RU = {
    "gated": "один повтор возможен только по решению человека",
    "never": "повтор запрещён",
    "human_only": "решает человек: докачать уже оплаченное можно, новую отправку нельзя",
}
_VERDICT_RU = {"pass": "проверка пройдена", "fail": "проверка не пройдена"}
_ARTIFACT_LIMIT = 500
_LOG_TAIL_BYTES = 4 * 1024 * 1024
_LOG_LINE_MAX = 300
_LOG_ERROR = re.compile(r"\b(?:ERROR|CRITICAL)\b|Traceback")
_LOG_WARNING = re.compile(r"\bWARNING\b")
_LOG_STAMP = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\S*\s*")
_LOG_CLOCK = re.compile(r"\b(\d{2}:\d{2}):\d{2}\b")
_LOG_SOURCE = re.compile(
    r"^\|?\s*(?:CRITICAL|ERROR|WARNING|INFO|DEBUG|SUCCESS|TRACE)\s*\|\s*\S+\s+-\s+(.+)$", re.S
)
_LOG_ERROR_TEXTS = 5
_LOG_ERROR_CLIP = 220
_CHAT_ROWS = 30
_REPORT_COLUMNS = (
    ("node_key", "Нода"),
    ("title", "Название"),
    ("type_ru", "Тип"),
    ("status_ru", "Статус"),
    ("attempts", "Попыток"),
    ("model", "Модель"),
    ("prompt", "Вариант промта"),
    ("checks", "Проверки"),
    ("result", "Результат"),
    ("cost_usd", "Стоимость, $ (оценка)"),
    ("cost_how", "Как посчитана стоимость"),
    ("errors", "Ошибки"),
)
_OVERVIEW_GROUPS = ("running", "attention", "queue")


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _clip(value: Any, n: int = 200) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _safe(value: Any) -> Any:
    try:
        from app.services.observation import redact

        return redact(value)
    except ImportError:
        return value


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _step_ru(code: str) -> str:
    return _STEP_RU.get(code, code)


def _type_ru(node_type: str) -> str:
    if node_type.startswith("hitl_"):
        return "Проверка человеком"
    if node_type.startswith("enrich_"):
        return "Работа с GPT"
    return _TYPE_RU.get(node_type, node_type or "нода")


def _status_ru(status: str) -> str:
    from app.models import ProjectStatus
    from app.orchestrator.node_registry import (
        NODE_TYPE_TO_STEP_CODE,
        READY_TO_NODE_TYPE,
        RUNNING_TO_NODE_TYPE,
    )

    if status in _PROJECT_STATUS_RU:
        return _PROJECT_STATUS_RU[status]
    try:
        st = ProjectStatus(status)
    except ValueError:
        return status or "неизвестно"
    if st in RUNNING_TO_NODE_TYPE:
        return "идёт шаг: " + _step_ru(NODE_TYPE_TO_STEP_CODE[RUNNING_TO_NODE_TYPE[st]])
    if st in READY_TO_NODE_TYPE:
        return "готов шаг: " + _step_ru(NODE_TYPE_TO_STEP_CODE[READY_TO_NODE_TYPE[st]])
    return status


def _is_running(status: str) -> bool:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status

    try:
        return is_running_status(ProjectStatus(status))
    except ValueError:
        return False


def _meta(detail: dict | None) -> dict:
    meta = (detail or {}).get("meta")
    return meta if isinstance(meta, dict) else {}


def _canvas_nodes(meta: dict) -> list[dict]:
    from app.services.canvas_graph import canvas_graph_from_meta

    cg = canvas_graph_from_meta(meta) or {}
    return [n for n in cg.get("nodes") or [] if isinstance(n, dict) and str(n.get("id") or "").strip()]


def _node_types(node: dict | None, fallback: str = "") -> tuple[str, str]:
    from app.services.excel_gpt_node import effective_node_type

    if not isinstance(node, dict):
        return fallback, fallback
    return str(node.get("type") or fallback), effective_node_type(node) or fallback


def _node_title(node: dict | None, eff: str) -> str:
    data = (node or {}).get("data") if isinstance((node or {}).get("data"), dict) else {}
    return _clip(data.get("label") or data.get("title") or _type_ru(eff), 80)


def _step_code(raw: str, eff: str) -> str:
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services.excel_gpt_node import EXCEL_GPT_STEP_CODE, is_excel_gpt_node_type

    if eff in NODE_TYPE_TO_STEP_CODE:
        return NODE_TYPE_TO_STEP_CODE[eff]
    if is_excel_gpt_node_type(raw):
        return EXCEL_GPT_STEP_CODE
    return eff


def _labels(meta: dict) -> dict[str, str]:
    """Подписи нод канваса; одинаковые («Работа с GPT» дважды) различаются номером из ключа.

    Без номера две ноды n_excel_gpt_1 и n_excel_gpt_2 в ответе чата выглядели одной
    строкой два раза («Работа с GPT», «Работа с GPT»).
    """
    raw: dict[str, str] = {}
    for n in _canvas_nodes(meta):
        _raw, eff = _node_types(n)
        raw[str(n["id"])] = _node_title(n, eff)
    counts: dict[str, int] = {}
    for title in raw.values():
        counts[title] = counts.get(title, 0) + 1
    out: dict[str, str] = {}
    for key, title in raw.items():
        number = "".join(ch for ch in key if ch.isdigit())
        out[key] = f"{title} {number}" if counts[title] > 1 and number else title
    return out


def _sessions():
    from app.services.action_layer import store

    return store.sessions()


async def _project_row(pid: int):
    from app.models import Project

    try:
        async with _sessions()() as s:
            return await s.get(Project, pid)
    except SQLAlchemyError:
        return None


def _transient(pid: int, detail: dict, row) -> Any:
    from app.models import Project

    slug = str(detail.get("slug") or (row.slug if row is not None else "") or f"project-{pid}")
    return Project(
        id=pid,
        slug=slug,
        topic=str(detail.get("topic") or ""),
        meta=_meta(detail),
        prompt_overrides=dict(detail.get("prompt_overrides") or {}),
        batch_id=row.batch_id if row is not None else None,
        batch_slug=row.batch_slug if row is not None else None,
    )


async def _node_runs(ctx: ActionContext, pid: int) -> list[dict]:
    try:
        run = await ctx.call("GET", f"/projects/{pid}/run")
    except ActionCallError:
        return []
    return [nr for nr in (run or {}).get("node_runs") or [] if isinstance(nr, dict)]


async def _managed(pid: int) -> bool | None:
    try:
        from sqlalchemy import select

        from app.services.actions_v1 import ActionExecutionControl, get_permission

        if get_permission(pid) is not None:
            return True
        async with _sessions()() as s:
            return (
                await s.scalar(
                    select(ActionExecutionControl.execution_id)
                    .where(ActionExecutionControl.project_id == pid, ActionExecutionControl.enabled.is_(True))
                    .limit(1)
                )
                is not None
            )
    except Exception:
        return None


def _step_failure(meta: dict) -> dict | None:
    fs = meta.get("step_failure")
    if not isinstance(fs, dict):
        fs = meta.get("failure_state")
    if not isinstance(fs, dict) or not (
        fs.get("last_error") or fs.get("sleep_until") or fs.get("total_fails")
    ):
        return None
    last = str(fs.get("last_running") or "")
    totals = fs.get("total_fails") or 0
    if isinstance(totals, dict):
        counts = [int(v) for v in totals.values() if isinstance(v, (int, float))]
        fails = int(totals.get(last) or 0) if last in totals else max(counts, default=0)
    else:
        fails = int(totals) if isinstance(totals, (int, float, str)) and str(totals).isdigit() else 0
    return {
        "status": last or None,
        "step": _status_ru(last) if last else None,
        "error": _clip(fs.get("last_error"), 300) or None,
        "code": fs.get("last_error_code") or None,
        "sleep_until": _iso(fs.get("sleep_until")),
        "fails": fails,
    }


def _pipeline(status: str, meta: dict) -> list[dict]:
    from app.orchestrator.node_registry import (
        LINEAR_NODE_TYPES,
        NODE_TYPE_TO_READY,
        NODE_TYPE_TO_RUNNING,
        NODE_TYPE_TO_STEP_CODE,
    )
    from app.services.excel_gpt_node import is_excel_gpt_node_type, slot_index_from_node

    nodes = _canvas_nodes(meta)
    present: set[str] = set()
    for n in nodes:
        raw, eff = _node_types(n)
        present.add(eff)
        if is_excel_gpt_node_type(raw) and eff == raw and slot_index_from_node(n):
            present.add(f"enrich_{slot_index_from_node(n)}")
    types = [t for t in LINEAR_NODE_TYPES if not nodes or t in present]
    ordered: list[str] = []
    for t in types:
        ordered += [NODE_TYPE_TO_RUNNING[t].value, NODE_TYPE_TO_READY[t].value]
    cur = status
    if cur not in ordered and cur in ("paused", "failed"):
        back = str(meta.get("paused_from_status") or "")
        fs = _step_failure(meta) or {}
        cur = back if back in ordered else str(fs.get("status") or "")
    pos = max((i for i, s in enumerate(ordered) if s == cur), default=-1)
    hold = {"paused": "на паузе", "failed": "ошибка"}.get(status)
    out = []
    for i, t in enumerate(types):
        run_idx, ready_idx = 2 * i, 2 * i + 1
        if ready_idx <= pos:
            state = "готово"
        elif run_idx == pos:
            state = hold or "идёт"
        else:
            state = "ждёт"
        code = NODE_TYPE_TO_STEP_CODE.get(t, t)
        out.append({"step": code, "title": _step_ru(code), "state": state})
    return out


def _current_next(steps: list[dict], status: str) -> tuple[dict | None, dict | None]:
    active = [i for i, s in enumerate(steps) if s["state"] not in ("готово", "ждёт")]
    if active:
        i = active[0]
        return steps[i], next((s for s in steps[i + 1 :] if s["state"] == "ждёт"), None)
    done = [i for i, s in enumerate(steps) if s["state"] == "готово"]
    if done:
        i = done[-1]
        return steps[i], next((s for s in steps[i + 1 :] if s["state"] == "ждёт"), None)
    return None, (steps[0] if steps and status != "failed" else None)


def _incident_item(row, details: dict, labels: dict[str, str]) -> dict:
    from app.services.diagnosis import TITLES, outcome_for

    closed = row.status == "resolved_confirmed"
    outcome = details.get("outcome") if isinstance(details.get("outcome"), dict) else None
    if outcome is None:
        try:
            outcome = outcome_for({**details, "type": row.type, "status": row.status})
        except Exception:
            outcome = {}
    cause = details.get("cause") if isinstance(details.get("cause"), dict) else {}
    rec = details.get("recommendation") if isinstance(details.get("recommendation"), dict) else {}
    safe = details.get("safe_retry") if isinstance(details.get("safe_retry"), dict) else {}
    retry = _RETRY_RU.get(str(outcome.get("paid_retry") or ""), "решает человек")
    if safe.get("reason"):
        retry = f"{retry}. {_clip(safe['reason'], 200)}"
    node = row.node_key or ""
    return {
        "id": row.incident_id,
        "type": row.type,
        "title": details.get("title") or TITLES.get(row.type, row.type),
        "node": labels.get(node) or node or None,
        "node_key": node or None,
        "fact": _clip(details.get("fact"), 300) or None,
        "cause": _clip(cause.get("hypothesis"), 200) or "причина не установлена",
        "retry": retry,
        "what_to_do": _clip(outcome.get("summary_ru") or rec.get("title"), 300) or None,
        "recommendation": rec.get("title") or None,
        "blocking": bool(details.get("blocking")) and not closed,
        "state": "закрыт" if closed else "открыт",
        "detected_at": _iso(row.detected_at),
    }


async def _incidents(
    pid: int,
    *,
    include_closed: bool = False,
    node_key: str | None = None,
    limit: int = 20,
    labels: dict[str, str] | None = None,
) -> tuple[list[dict], int]:
    from sqlalchemy import select

    from app.services.diagnosis import Incident

    try:
        async with _sessions()() as s:
            q = select(Incident).where(Incident.project_id == pid)
            if node_key:
                q = q.where(Incident.node_key == node_key)
            rows = (await s.scalars(q.order_by(Incident.detected_at.desc()).limit(500))).all()
    except SQLAlchemyError:
        return [], 0
    items = []
    for row in rows:
        d = row.details if isinstance(row.details, dict) else {}
        closed = row.status == "resolved_confirmed"
        stale = d.get("currently_relevant") is False or d.get("historical_attempt") or d.get("echo_of")
        if not include_closed and (closed or stale):
            continue
        items.append(_incident_item(row, d, labels or {}))
    return items[:limit], len(items)


async def _feed(pid: int, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    from app.services import timeline as tl

    items: list[dict] = []
    try:
        async with _sessions()() as s:
            for sev in ("error", "warn"):
                res = await tl.timeline(s, pid, severity=sev, limit=limit)
                items.extend(res.get("items") or [])
    except Exception:
        return []
    items.sort(key=lambda it: str(it.get("ts") or ""), reverse=True)
    return [
        {
            "when": it.get("ts"),
            "level": "ошибка" if it.get("severity") == "error" else "предупреждение",
            "title": _clip(it.get("title_ru"), 200),
            "detail": _clip(it.get("detail_ru"), 200) or None,
        }
        for it in items[:limit]
    ]


def _harness_checks(meta: dict) -> tuple[list[dict], dict]:
    from app.services.agent_harness import read_ops_telemetry

    tel = read_ops_telemetry(meta)
    checks = [
        {
            "name": str(c.get("name") or ""),
            "ok": bool(c.get("ok")),
            "detail": _clip(c.get("detail"), 160) or None,
        }
        for c in tel.get("checks") or []
        if isinstance(c, dict)
    ]
    return checks, tel


def _model_for(meta: dict, key: str, node: dict | None, raw: str, eff: str) -> str:
    from app.services.excel_gpt_node import is_excel_gpt_node_type
    from app.services.node_groups import is_script_frames_qc_gpt_node

    try:
        if is_script_frames_qc_gpt_node(meta, node_key=key):
            return "GPT 5.6 Sol (Vibecode, задано группой)"
    except Exception:
        pass
    if not (eff in _TEXT_TYPES or eff in _IMAGE_TYPES or eff in _VIDEO_TYPES or is_excel_gpt_node_type(raw)):
        return ""
    from app.services.vibecode_catalog import (
        default_model_id_for_node_type,
        find_model,
        read_node_model_fields,
    )

    mid, channel = read_node_model_fields(node)
    explicit = bool(mid)
    mid = mid or default_model_id_for_node_type(raw or eff)
    try:
        found = find_model(mid, channel=channel)
    except Exception:
        found = None
    label = str((found or {}).get("label") or mid)
    return label if explicit else f"{label} (по умолчанию)"


def _prompt_for(detail: dict, key: str, raw: str, eff: str) -> dict | None:
    from app.services.excel_gpt_node import EXCEL_GPT_STEP_CODE, is_excel_gpt_node_type

    if not (eff in _TEXT_TYPES or eff == "hero" or is_excel_gpt_node_type(raw)):
        return None
    step = EXCEL_GPT_STEP_CODE if is_excel_gpt_node_type(raw) else _step_code(raw, eff)
    try:
        from app.services.prompt_library import PROMPT_SOURCE_LABELS, resolve_project_prompt_with_source

        name, source = resolve_project_prompt_with_source(
            dict(detail.get("prompt_overrides") or {}), step, meta=_meta(detail), node_key=key
        )
    except Exception:
        return None
    return {"variant": name, "source": PROMPT_SOURCE_LABELS.get(source, source)}


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _artifacts(project, key: str, *, sample: int = 5) -> dict:
    from app.services.gpt_operator import files_from_source_node

    try:
        files = files_from_source_node(project, key, limit=_ARTIFACT_LIMIT)
    except Exception as exc:
        return {"count": 0, "more": False, "sample": [], "error": type(exc).__name__}
    root = Path(project.data_dir)
    return {
        "count": len(files),
        "more": len(files) >= _ARTIFACT_LIMIT,
        "sample": [_rel(Path(p), root) for p in files[:sample]],
    }


def _gate(project, meta: dict, key: str, raw: str, *, excerpt: bool) -> dict | None:
    from app.services.excel_gpt_node import is_excel_gpt_node_type, upload_dir

    if not is_excel_gpt_node_type(raw):
        return None
    from app.services.gpt_operator import hydrate_check_result_from_disk

    results = meta.get("gpt_operator_results") if isinstance(meta.get("gpt_operator_results"), dict) else {}
    entry = dict(results.get(key) or {}) if isinstance(results.get(key), dict) else {}
    try:
        entry = hydrate_check_result_from_disk(project, key, entry)
    except Exception:
        pass
    verdict = str(entry.get("gateStatus") or "").strip().lower()
    analysis = entry.get("analysis") if isinstance(entry.get("analysis"), dict) else {}
    cfg = (
        (meta.get("excel_gpt_nodes") or {}).get(key)
        if isinstance(meta.get("excel_gpt_nodes"), dict)
        else None
    )
    summary = analysis.get("summary") or ((cfg or {}).get("lastSummary") if isinstance(cfg, dict) else "")
    text = ""
    if excerpt:
        report = upload_dir(project, key) / "check_report.txt"
        try:
            if report.is_file():
                text = report.read_text(encoding="utf-8", errors="replace")[:800]
        except OSError:
            text = ""
    if not verdict and not summary and not text:
        return None
    out = {
        "verdict": verdict or None,
        "verdict_ru": _VERDICT_RU.get(verdict),
        "summary": _clip(summary, 300) or None,
        "at": _iso(entry.get("at")),
    }
    if excerpt:
        out["report_excerpt"] = text or None
    return out


def _trace(project, key: str) -> dict | None:
    try:
        from app.services.node_trace import _safe_name, read_run, trace_root
    except ImportError:
        return None

    folder = trace_root(project.data_dir) / _safe_name(key)
    try:
        runs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.endswith(".del"))
    except OSError:
        return None
    if not runs:
        return None
    run = read_run(runs[-1])
    return {
        "status": run.get("status"),
        "started": run.get("started"),
        "finished": run.get("finished"),
        "calls": run.get("calls"),
        "counts": run.get("counts") or {},
        "error": _clip(run.get("error"), 300) or None,
        "runs_kept": len(runs),
    }


async def _node_costs(
    pid: int, *, node_runs: list[dict] | None = None, canvas: list[dict] | None = None
) -> tuple[dict[str, float], float | None]:
    """Траты по нодам (только по связи, см. report_costs) и всего по проекту.

    Участнику — ничего: это себестоимость вызовов из общей кассы провайдеров, а не
    его списания (`app/services/action_layer/plan.py::member_view`). Пустой ответ
    здесь убирает траты из карточки ноды и отчётов одним местом.
    """
    from app.services.action_layer import report_costs
    from app.services.action_layer.plan import member_view

    if member_view():
        return {}, None
    split = await report_costs.node_costs(
        pid, node_runs=node_runs, step_nodes=report_costs.step_nodes_of(canvas or [])
    )
    return split.by_node, split.total


def _node_card(
    project,
    detail: dict,
    key: str,
    node: dict | None,
    nr: dict | None,
    *,
    checks: list[dict],
    cost: float | None,
    detailed: bool,
) -> dict:
    meta = _meta(detail)
    raw, eff = _node_types(node, str((nr or {}).get("node_type") or ""))
    step = _step_code(raw, eff)
    disabled = key in [str(x) for x in meta.get("disabled_nodes") or []]
    if disabled:
        status = "отключена"
    elif nr:
        status = _NODE_STATUS_RU.get(str(nr.get("status")), str(nr.get("status")))
    else:
        status = "не запускалась"
    errors: list[str] = []
    if nr and nr.get("error"):
        errors.append(_clip(nr.get("error"), 300))
    trace = _trace(project, key)
    if trace and trace.get("status") == "failed" and trace.get("error"):
        if trace["error"] not in errors:
            errors.append(trace["error"])
    fs = _step_failure(meta)
    if fs and fs.get("error") and fs.get("status"):
        from app.orchestrator.node_registry import NODE_TYPE_TO_RUNNING

        running = NODE_TYPE_TO_RUNNING.get(eff)
        if running is not None and running.value == fs["status"] and fs["error"] not in errors:
            errors.append(fs["error"])
    own_checks = [c for c in checks if _CHECK_STEP.get(c["name"]) == step]
    gate = _gate(project, meta, key, raw, excerpt=detailed)
    arts = _artifacts(project, key, sample=10 if detailed else 3)
    card = {
        "node_key": key,
        "title": _labels(meta).get(key) or _node_title(node, eff),
        "type": eff or raw,
        "type_ru": _type_ru(eff or raw),
        "step": step,
        "status_ru": status,
        "attempts": int((nr or {}).get("attempts") or 0),
        "progress": _clip((nr or {}).get("progress_text"), 160) or None,
        "model": _model_for(meta, key, node, raw, eff) or None,
        "prompt": _prompt_for(detail, key, raw, eff),
        "harness": own_checks,
        "gate": gate,
        "artifacts": arts,
        "cost_usd": _money(cost),
        "errors": errors,
        "on_canvas": node is not None,
    }
    if detailed:
        card.update(
            trace=trace,
            started_at=_iso((nr or {}).get("started_at")),
            finished_at=_iso((nr or {}).get("finished_at")),
        )
    return card


def _checks_text(card: dict) -> str:
    ok = sum(1 for c in card["harness"] if c["ok"])
    bad = sum(1 for c in card["harness"] if not c["ok"])
    parts = []
    if ok or bad:
        parts.append(f"ок {ok} / не ок {bad}")
    gate = card.get("gate") or {}
    if gate.get("verdict_ru"):
        parts.append(gate["verdict_ru"])
    return "; ".join(parts)


def _result_text(card: dict) -> str:
    gate = card.get("gate") or {}
    if gate.get("summary"):
        return _clip(gate["summary"], 120)
    if card.get("progress"):
        return card["progress"]
    arts = card.get("artifacts") or {}
    if arts.get("count"):
        return f"файлов: {arts['count']}{'+' if arts.get('more') else ''}"
    return ""


def _row_status(row) -> str:
    if row is None:
        return ""
    raw = row.status
    return str(raw.value if hasattr(raw, "value") else raw or "")


async def _frame_count(factory, pid: int) -> int | None:
    from sqlalchemy import func, select

    from app.models import Frame

    try:
        async with factory() as s:
            return int(await s.scalar(select(func.count(Frame.id)).where(Frame.project_id == pid)) or 0)
    except Exception:
        return None


def _failure_reason(fs: dict | None) -> str:
    if not fs or not fs.get("error"):
        return ""
    step = str(fs.get("step") or "").removeprefix("идёт шаг: ")
    head = f"шаг «{step}» упал" if step else "ошибка шага"
    return _clip(f"{head}: {fs['error']}", 300)


def _pause_info(status: str, meta: dict) -> dict:
    """Пауза и её причина по meta проекта (одна база: отдельного project.db нет).

    Порядок: записанная причина, ошибка шага, остановка пользователем, ожидание
    ручного запуска. Причина при статусе не на паузе — старая отметка, её отдаём отдельно.
    """
    paused = status in ("paused", "failed")
    fs = _step_failure(meta)
    recorded = meta.get("auto_paused_reason")
    stop = bool(meta.get("user_stop") or meta.get("mass_lane_user_stop"))
    await_start = bool(meta.get("auto_await_manual_start"))
    reason = None
    if paused:
        causes = [
            _clip(recorded, 300),
            _failure_reason(fs),
            "остановлен пользователем" if stop else "",
            "ждёт ручного запуска" if await_start else "",
        ]
        reason = next((c for c in causes if c), "причина не записана")
    return {
        "paused": paused,
        "reason": reason,
        "user_stop": stop,
        "await_manual_start": await_start,
        "stale_reason": _clip(recorded, 160) if recorded and not paused else None,
        "sleep_until": (fs or {}).get("sleep_until"),
    }


async def _status_sources(pid: int, row, recomputed: str) -> dict:
    listed = _row_status(row)
    out = {
        "studio_list": listed or None,
        "studio_list_ru": _status_ru(listed) if listed else None,
        "recomputed": recomputed or None,
        "recomputed_ru": _status_ru(recomputed) if recomputed else None,
        "mismatch": bool(listed and recomputed and listed != recomputed),
        "frames_studio_db": None,
    }
    if out["mismatch"]:
        out["frames_studio_db"] = await _frame_count(_sessions(), pid)
    return out


def _mismatch_text(src: dict) -> str:
    text = (
        f"Внимание: в списке проектов Студии статус «{src['studio_list_ru']}», а пересчёт по данным "
        f"проекта при открытии даёт «{src['recomputed_ru']}» — статус и данные расходятся."
    )
    if src.get("frames_studio_db") is not None:
        text += f" Кадров в базе: {src['frames_studio_db']}."
    return text + " Здесь показан статус, как в списке; сам статус я не меняю."


def _pause_lines(pause: dict, fs: dict | None) -> list[str]:
    lines = []
    if pause["reason"]:
        lines.append(f"Причина паузы: {pause['reason']}.")
    if pause["user_stop"]:
        lines.append("Остановлен пользователем.")
    if pause["stale_reason"]:
        lines.append(f"Сейчас проект не на паузе, осталась старая отметка паузы: {pause['stale_reason']}.")
    if fs and fs.get("error") and fs["error"] not in (pause["reason"] or ""):
        lines.append(f"Последняя ошибка шага: {fs['error']}")
    return lines


async def _project_status(ctx: ActionContext, params: dict) -> dict:
    pid = params["project_id"]
    detail = await _effective_project(ctx, pid)
    meta = _meta(detail)
    recomputed = str(detail.get("status") or "")
    sources = await _status_sources(pid, await _project_row(pid), recomputed)
    status = sources["studio_list"] or recomputed
    steps = _pipeline(status, meta)
    cur, nxt = _current_next(steps, status)
    runs = await _node_runs(ctx, pid)
    running = [str(nr.get("node_key")) for nr in runs if nr.get("status") == "running"]
    failed = [str(nr.get("node_key")) for nr in runs if nr.get("status") == "failed"]
    fs = _step_failure(meta)
    pause = _pause_info(status, meta)
    managed = await _managed(pid)
    _items, open_incidents = await _incidents(pid, limit=1)
    labels = _labels(meta)
    done = sum(1 for s in steps if s["state"] == "готово")
    title = _clip(detail.get("title") or detail.get("topic"), 80)
    out = {
        "project_id": pid,
        "title": title,
        "status": status,
        "status_ru": _status_ru(status),
        "auto_mode": bool(detail.get("auto_mode")),
        "managed": managed,
        "current_step": cur,
        "next_step": nxt,
        "progress": {"done": done, "total": len(steps), "steps": steps},
        "pause": pause,
        "failure": fs,
        "generation_active": bool(detail.get("generation_active")),
        "active_node": meta.get("active_excel_gpt_node_key") or None,
        "running_nodes": [labels.get(k, k) for k in running],
        "failed_nodes": [labels.get(k, k) for k in failed],
        "queue_position": detail.get("gen_queue_position"),
        "open_incidents": open_incidents,
        "updated_at": _iso(detail.get("updated_at")),
        "status_sources": sources,
    }
    lines = [f"Проект #{pid} «{title}»: {out['status_ru']}."]
    if sources["mismatch"]:
        lines.append(_mismatch_text(sources))
    if cur:
        lines.append(f"Текущий шаг: {cur['title']} ({cur['state']}).")
    if nxt:
        lines.append(f"Следующий шаг: {nxt['title']}.")
    lines.append(f"Готово шагов: {done} из {len(steps)}.")
    lines += _pause_lines(pause, fs)
    if out["generation_active"] or running:
        lines.append("Идёт генерация" + (f": {', '.join(out['running_nodes'])}." if running else "."))
    if failed:
        lines.append(f"Ноды с ошибкой: {', '.join(out['failed_nodes'])}.")
    if open_incidents:
        lines.append(f"Открытых инцидентов: {open_incidents}.")
    if managed:
        lines.append("Проект под управляемым исполнением: чтение доступно, ручной запуск закрыт.")
    out["text_ru"] = " ".join(lines)
    return _safe(out)


async def _node_output(ctx: ActionContext, params: dict) -> dict:
    pid, key = params["project_id"], params["node_key"]
    detail = await _effective_project(ctx, pid)
    meta = _meta(detail)
    nodes = {str(n["id"]): n for n in _canvas_nodes(meta)}
    runs = {str(nr.get("node_key")): nr for nr in await _node_runs(ctx, pid)}
    node, nr = nodes.get(key), runs.get(key)
    if node is None and nr is None:
        return {
            "found": False,
            "node_key": key,
            "known_nodes": sorted(nodes)[:40],
            "text_ru": f"Нода {key} не найдена ни на канвасе, ни в запусках проекта #{pid}.",
        }
    project = _transient(pid, detail, await _project_row(pid))
    checks, tel = _harness_checks(meta)
    costs, _total = await _node_costs(pid, node_runs=list(runs.values()), canvas=list(nodes.values()))
    card = _node_card(project, detail, key, node, nr, checks=checks, cost=costs.get(key), detailed=True)
    incidents, _n = await _incidents(pid, node_key=key, limit=5, labels=_labels(meta))
    card.update(found=True, incidents=incidents, harness_at=_iso(tel.get("updated_at")))
    lines = [f"Нода «{card['title']}» ({card['type_ru']}): {card['status_ru']}."]
    if card["model"]:
        lines.append(f"Модель: {card['model']}.")
    if card["prompt"]:
        lines.append(f"Промт: {card['prompt']['variant']} ({card['prompt']['source']}).")
    checks_text = _checks_text(card)
    if checks_text:
        lines.append(f"Проверки: {checks_text}.")
    arts = card["artifacts"]
    lines.append(f"Файлов результата: {arts['count']}{'+' if arts.get('more') else ''}.")
    if card["cost_usd"] is not None:
        lines.append(f"Траты по ноде (оценка): ${card['cost_usd']}.")
    if card["errors"]:
        lines.append("Ошибки: " + " | ".join(card["errors"][:3]))
    if incidents:
        lines.append(f"Открытых инцидентов по ноде: {len(incidents)}.")
    card["text_ru"] = " ".join(lines)
    return _safe(card)


async def _diag_incidents(ctx: ActionContext, params: dict) -> dict:
    pid = params["project_id"]
    limit = int(params.get("limit", 10))
    include_closed = params.get("status") == "all"
    row = await _project_row(pid)
    labels = _labels(row.meta if row is not None and isinstance(row.meta, dict) else {})
    items, total = await _incidents(pid, include_closed=include_closed, limit=limit, labels=labels)
    feed = await _feed(pid, limit)
    blocking = sum(1 for i in items if i["blocking"])
    lines = [
        f"Проект #{pid}: "
        + (f"инцидентов {total}" if total else "открытых инцидентов нет")
        + (f", блокирующих {blocking}" if blocking else "")
        + "."
    ]
    for it in items[:5]:
        where = f" (нода «{it['node']}»)" if it["node"] else ""
        lines.append(
            f"{it['title']}{where}: {it['fact'] or it['title']}. Повтор: {it['retry']}. "
            f"Что делать: {it['what_to_do'] or 'решает человек'}."
        )
    if feed:
        lines.append(f"В ленте последних ошибок и предупреждений: {len(feed)}.")
    out = {
        "project_id": pid,
        "total": total,
        "shown": len(items),
        "blocking": blocking,
        "incidents": items,
        "feed": feed,
        "text_ru": " ".join(lines),
    }
    return _safe(out)


def _log_files() -> list[Path]:
    from app.settings import settings

    root = Path(settings.data_dir)
    out: list[Path] = []
    for pattern in ("backend*.log", "studio-live*.log"):
        try:
            found = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        except OSError:
            found = []
        out.extend(found)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def _tail_lines(path: Path) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _LOG_TAIL_BYTES:
                fh.seek(size - _LOG_TAIL_BYTES)
                fh.readline()
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def _project_marker(pid: int, slug: str | None) -> re.Pattern:
    parts = [rf"(?<![\w/])#{pid}(?!\d)", rf"projects/{pid}(?!\d)", rf"project_id[=: \"']+{pid}(?!\d)"]
    if slug and len(slug) >= 8:
        parts.append(re.escape(slug))
    return re.compile("|".join(parts))


def _log_row(name: str, line: str, level: str) -> dict:
    message = _LOG_STAMP.sub("", line, count=1).strip() or line.strip()
    found = _LOG_CLOCK.search(line)
    return {
        "file": name,
        "text": _clip(line, _LOG_LINE_MAX),
        "level": level,
        "message": message,
        "time": found.group(1) if found else "",
    }


def _fold_warnings(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for index, row in enumerate(rows):
        key = ("w", row["file"], row["message"]) if row["level"] == "warning" else ("x", index)
        group = groups.get(key)
        if group is None:
            groups[key] = {**row, "count": 1, "first": row["time"], "index": index}
            continue
        group.update(count=group["count"] + 1, time=row["time"], index=index)
    folded = sorted(groups.values(), key=lambda g: g["index"])
    for group in folded:
        if group["count"] > 1:
            span = f" с {group['first']} по {group['time']}" if group["first"] and group["time"] else ""
            group["text"] = _clip(
                f"{group['count']} раз{span}: {_log_human(group['message'])}", _LOG_LINE_MAX
            )
    return folded


def _log_human(message: str) -> str:
    found = _LOG_SOURCE.match(message)
    return found.group(1).strip() if found else message


def _pick_lines(rows: list[dict], limit: int) -> list[dict]:
    errs = [row for row in rows if row["level"] == "error"][-limit:]
    rest = [row for row in rows if row["level"] != "error"]
    room = limit - len(errs)
    picked = errs + (rest[-room:] if room > 0 else [])
    return sorted(picked, key=lambda row: row["index"])


async def _logs_tail(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.plan import refuse_admin_only

    # Без проекта — общий лог сервера со строками всех клиентов: только админу.
    refuse_admin_only("logs.tail", params)
    pid = params.get("project_id")
    level = params.get("kind", "warnings")
    limit = int(params.get("limit", 40))
    slug = None
    if pid is not None:
        row = await _project_row(pid)
        slug = row.slug if row is not None else None
    marker = _project_marker(pid, slug) if pid is not None else None
    files = _log_files()
    matched: list[dict] = []
    errors = warnings = 0
    for path in files:
        for line in _tail_lines(path):
            if marker is not None and not marker.search(line):
                continue
            is_err = bool(_LOG_ERROR.search(line))
            is_warn = bool(_LOG_WARNING.search(line))
            if level == "errors" and not is_err:
                continue
            if level == "warnings" and not (is_err or is_warn):
                continue
            errors += is_err
            warnings += is_warn and not is_err
            matched.append(_log_row(path.name, line, "error" if is_err else "warning" if is_warn else "info"))
    shown = _pick_lines(_fold_warnings(matched), limit)
    scope = f"проекта #{pid}" if pid is not None else "сервера"
    if not files:
        text_ru = "Файлов логов в папке данных нет: сервер пишет только в консоль."
    elif not matched:
        text_ru = f"В хвосте логов {scope} подходящих строк нет."
    else:
        text_ru = (
            f"Логи {scope}: найдено строк {len(matched)} (ошибок {errors}, "
            f"предупреждений {warnings}), показаны последние {len(shown)}"
            + (
                "; одинаковые предупреждения свёрнуты в одну строку."
                if any(row["count"] > 1 for row in shown)
                else "."
            )
        )
        texts = [_log_human(row["message"]) for row in matched if row["level"] == "error"][-_LOG_ERROR_TEXTS:]
        if texts:
            text_ru += " Последние ошибки: " + " | ".join(_clip(t, _LOG_ERROR_CLIP) for t in texts)
    shown = [{k: v for k, v in row.items() if k not in ("message", "index", "first")} for row in shown]
    out = {
        "project_id": pid,
        "kind": level,
        "files": [p.name for p in files],
        "matched": len(matched),
        "errors": errors,
        "warnings": warnings,
        "shown": len(shown),
        "lines": shown,
        "text_ru": text_ru,
    }
    return _safe(out)


_ASSISTANT_SOURCE = "orch_llm"


def _spend_window(rows) -> dict:
    from app.services.spend_guard import unified_kind

    acc = {"actual": 0.0, "estimated": 0.0, "reserved": 0.0}
    total = 0.0
    unknown = 0
    assistant = 0.0
    assistant_rows = 0
    for kind, source, note, usd in rows:
        usd = float(usd or 0.0)
        total += usd
        if source == _ASSISTANT_SOURCE:
            assistant += usd
            assistant_rows += 1
        uk = unified_kind(kind, source, note)
        if uk in acc:
            acc[uk] += usd
        if "cost_unknown=true" in str(note or "").lower():
            unknown += 1
    is_estimate = abs(acc["estimated"]) > 1e-9 or abs(acc["reserved"]) > 1e-9 or unknown > 0
    return {
        "usd": _money(total),
        "fact_usd": _money(acc["actual"]),
        "estimate_usd": _money(acc["estimated"]),
        "reserved_usd": _money(acc["reserved"]),
        "unknown_cost_rows": unknown,
        "rows": len(rows),
        "is_estimate": is_estimate,
        "label": "оценка" if is_estimate else "факт",
        "assistant_usd": _money(assistant),
        "assistant_rows": assistant_rows,
    }


def _usd_text(value: float | None) -> str:
    v = float(value or 0.0)
    if v == 0:
        return "$0"
    return f"${v:.4f}".rstrip("0").rstrip(".") if abs(v) < 0.01 else f"${v:.2f}"


def _spend_line(head: str, window: dict) -> str:
    if not window["rows"]:
        return f"{head}: трат не записано."
    line = f"{head}: {_usd_text(window['usd'])} ({window['label']})"
    if window["assistant_rows"]:
        line += (
            f", из них работа помощника (модель): {_usd_text(window['assistant_usd'])} "
            f"(оценка по токенам, вызовов {window['assistant_rows']})"
        )
    return line + "."


async def _spend_summary(ctx: ActionContext, params: dict) -> dict:
    from sqlalchemy import text

    from app.services import spend_guard
    from app.services.action_layer.plan import refuse_admin_only

    # Общая касса студии и себестоимость: участнику не показывается.
    refuse_admin_only("spend.summary", params)
    pid = params.get("project_id")
    day = spend_guard._today()
    cols = "SELECT kind, source, note, usd FROM spend_ledger WHERE "
    try:
        async with _sessions()() as s:
            today = _spend_window((await s.execute(text(cols + "day = :d"), {"d": day})).all())
            project = run = None
            run_id = ""
            if pid is not None:
                project = _spend_window((await s.execute(text(cols + "project_id = :p"), {"p": pid})).all())
                run_id = str(
                    await s.scalar(
                        text(
                            "SELECT run_id FROM spend_ledger WHERE project_id = :p AND run_id != '' "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"p": pid},
                    )
                    or ""
                )
                if run_id:
                    run = _spend_window((await s.execute(text(cols + "run_id = :r"), {"r": run_id})).all())
    except SQLAlchemyError:
        empty = _spend_window([])
        today, project, run, run_id = empty, (empty if pid is not None else None), None, ""
    caps = spend_guard.caps()
    caps_ru = {k: ("без лимита" if not v or v <= 0 else f"${v:g}") for k, v in caps.items()}
    lines = [_spend_line(f"Сегодня ({day}), вся фабрика", today)]
    if project is not None:
        lines.append(_spend_line(f"Проект #{pid}, за всё время", project))
    if run is not None:
        lines.append(_spend_line("Последний запуск", run))
    if any(w and w["unknown_cost_rows"] for w in (today, project, run)):
        lines.append("Часть вызовов без известной цены: сумма занижена, это оценка.")
    lines.append(
        "Потолки: день "
        + caps_ru["daily"]
        + ", проект "
        + caps_ru["project"]
        + ", запуск "
        + caps_ru["run"]
        + "."
    )
    out = {
        "project_id": pid,
        "day": day,
        "today": today,
        "project": project,
        "run": run,
        "run_id": run_id or None,
        "caps_usd": caps,
        "caps_ru": caps_ru,
        "text_ru": " ".join(lines),
    }
    return _safe(out)


async def _project_brief(row, managed: set[int], queue: list[int]) -> dict:
    meta = row.meta if isinstance(row.meta, dict) else {}
    status = _row_status(row)
    fs = _step_failure(meta)
    pause = _pause_info(status, meta)
    steps = _pipeline(status, meta)
    cur, _nxt = _current_next(steps, status)
    # Название целиком: в таблице чата обрезанное «3 факта…» не узнать.
    return {
        "id": row.id,
        "title": _clip(row.title or row.topic, 300),
        "status": status,
        "status_ru": _status_ru(status),
        "step": cur["title"] if cur else None,
        "pause_reason": _clip(pause["reason"], 160) or None,
        "user_stop": pause["user_stop"],
        "error": _clip((fs or {}).get("error"), 160) or None,
        "error_status": (fs or {}).get("status"),
        "queue_position": queue.index(row.id) + 1 if row.id in queue else None,
        "auto_mode": bool(row.auto_mode),
        "managed": row.id in managed,
        "updated_at": _iso(row.updated_at),
    }


def _error_is_current(item: dict) -> bool:
    """Ошибка относится к шагу, на котором проект стоит сейчас (идёт или готов).

    Ошибку стирает только успешный шаг; после ручного сброса или отката статуса старая
    ошибка другого шага остаётся в данных и не должна держать проект во «внимании».
    """
    if not item.get("error"):
        return False
    failed_at = str(item.get("error_status") or "")
    if not failed_at:
        return True
    from app.models import ProjectStatus
    from app.orchestrator.node_registry import READY_TO_NODE_TYPE, RUNNING_TO_NODE_TYPE

    def node_of(status: str) -> str | None:
        try:
            st = ProjectStatus(status)
        except ValueError:
            return None
        return RUNNING_TO_NODE_TYPE.get(st) or READY_TO_NODE_TYPE.get(st)

    return failed_at == item["status"] or (
        node_of(failed_at) is not None and node_of(failed_at) == node_of(item["status"])
    )


def _needs_attention(item: dict) -> bool:
    # Ошибка шага при статусе «готово» (пустые промты после повтора) — тоже внимание:
    # в общем списке она видна, а в «требуют внимания» проекта не было.
    return item["status"] in ("paused", "failed") or bool(item["pause_reason"] or _error_is_current(item))


def _overview_keep(item: dict, want: str | None) -> bool:
    if not want:
        return True
    if want == "running":
        return _is_running(item["status"])
    if want == "attention":
        return _needs_attention(item)
    if want == "queue":
        return item["queue_position"] is not None
    return item["status"] == want


def _brief_line(item: dict, in_queue: bool) -> str:
    extra = item["pause_reason"] or item["error"] or ""
    place = f"{item['queue_position']}. " if in_queue else ""
    return (
        f"{place}#{item['id']} «{item['title']}»: {item['status_ru']}"
        + (f" — {_clip(extra, 100)}" if extra else "")
        + "."
    )


async def _projects_overview(ctx: ActionContext, params: dict) -> dict:
    from sqlalchemy import select

    from app.models import Project
    from app.services.gen_queue import own_gen_queue, worker_max_parallel
    from app.services.sidebar_layout import get_gen_queue

    want = params.get("status")
    limit = int(params.get("limit", 30))
    managed: set[int] = set()
    async with _sessions()() as s:
        # Файл очереди общий для студии: места и размер очереди — только среди своих
        # проектов арендатора (владелец видит всю очередь).
        queue = await own_gen_queue(s, get_gen_queue())
        q = select(Project).order_by(Project.updated_at.desc(), Project.id.desc())
        if want == "queue":
            q = q.where(Project.id.in_(queue or [-1]))
        try:
            from app.services.actions_v1 import ActionExecutionControl

            managed = set(
                (
                    await s.scalars(
                        select(ActionExecutionControl.project_id).where(
                            ActionExecutionControl.enabled.is_(True)
                        )
                    )
                ).all()
            )
        except SQLAlchemyError:
            await s.rollback()
            managed = set()
        rows = (await s.scalars(q.limit(2000))).all()
    items = [i for i in [await _project_brief(r, managed, queue) for r in rows] if _overview_keep(i, want)]
    if want == "queue":
        items.sort(key=lambda i: i["queue_position"])
    total = len(items)
    items = items[:limit]
    attention = sum(1 for i in items if _needs_attention(i))
    running = sum(1 for i in items if _is_running(i["status"]))
    head = f"Проектов: {total}" + (f" (фильтр: {want})" if want else "")
    lines = [f"{head}; показано {len(items)}, идут сейчас {running}, требуют внимания {attention}."]
    if want == "queue":
        lines.append(
            f"В очереди генерации {len(queue)}, одновременно идут не больше {worker_max_parallel()}."
        )
    # Строк ровно столько, сколько «показано»: срез короче шапки давал ответ
    # «данных по остальным нет» про проекты, которые в выборке были.
    lines += [_brief_line(i, want == "queue") for i in items]
    out = {"filter": want, "total": total, "shown": len(items), "projects": items, "text_ru": " ".join(lines)}
    if want == "queue":
        out["queue"] = {"size": len(queue), "max_parallel": worker_max_parallel()}
    return _safe(out)


def _md_cell(value: Any) -> str:
    from app.services.action_layer.report_gpt import md_cell

    return md_cell(value)


def _row_for(card: dict) -> dict:
    prompt = card.get("prompt") or {}
    return {
        "node_key": card["node_key"],
        "title": card["title"],
        "type_ru": card["type_ru"],
        "status_ru": card["status_ru"],
        "attempts": card["attempts"],
        "model": card["model"] or "",
        "prompt": prompt.get("variant") or "",
        "checks": _checks_text(card),
        "result": _result_text(card),
        "cost_usd": card["cost_usd"],
        "cost_how": card.get("cost_how") or "",
        "errors": " | ".join(card["errors"][:2]),
    }


def _markdown(rows: list[dict], columns: tuple[tuple[str, str], ...]) -> str:
    from app.services.action_layer.report_gpt import markdown_table

    return markdown_table(rows, columns)


_NODES_WIDTHS = {
    "A": 24,
    "B": 28,
    "C": 18,
    "D": 18,
    "E": 9,
    "F": 28,
    "G": 24,
    "H": 26,
    "I": 40,
    "J": 14,
    "K": 20,
    "L": 60,
}


def _without_cost(columns: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(col for col in columns if col[0] not in ("cost_usd", "cost_how"))


def _write_xlsx(
    path: Path,
    rows: list[dict],
    summary: list[tuple[str, Any]],
    gpt: list[dict] | None = None,
    columns: tuple[tuple[str, str], ...] = _REPORT_COLUMNS,
) -> None:
    from app.services.action_layer import report_gpt as rg
    from app.services.action_layer import report_template as tpl

    sheets = [("Ноды", columns, rg.with_no_data(rows, ("cost_usd",)))]
    if gpt:
        sheets += [
            (tpl.GPT_SHEET, tpl.GPT_NODE_COLUMNS, rg.with_no_data(gpt, ("cost_usd",))),
            (tpl.GPT_FRAMES_SHEET, tpl.GPT_FRAME_COLUMNS, rg.gpt_frame_rows(gpt)),
        ]
        lists = rg.gpt_list_rows(gpt)
        if lists:
            sheets.append((tpl.GPT_CHARACTERS_SHEET, tpl.GPT_LIST_COLUMNS, lists))
    rg.write_workbook(path, sheets, summary, widths={"Ноды": _NODES_WIDTHS})


def _gpt_markdown(gpt: list[dict], *, full: bool) -> str:
    from app.services.action_layer import report_gpt as rg
    from app.services.action_layer import report_template as tpl

    if not gpt:
        return ""
    out = f"\n\n### {tpl.GPT_SECTION_TITLE}\n\n" + rg.markdown_table(
        gpt, tpl.GPT_NODE_COLUMNS if full else tpl.GPT_CHAT_COLUMNS
    )
    frames = rg.gpt_frame_rows(gpt)
    if full and frames:
        out += "\n\n" + rg.markdown_table(frames, tpl.GPT_FRAME_COLUMNS)
    return out


def _trace_runs(data_dir: Any) -> tuple[list[dict], list[dict]]:
    """Прогоны нод из node_trace и дневник правок программы последнего прогона каждой ноды."""
    try:
        from app.services.node_trace import latest_diary, list_runs
    except ImportError:
        return [], []

    try:
        runs = list_runs(data_dir)
    except OSError:
        runs = []
    try:
        diary = latest_diary(data_dir)
    except (OSError, ValueError):
        diary = []
    return runs, diary


def _has_frames_group(canvas: list[dict]) -> bool:
    """На канвасе есть группа нод «Кадры» (n_excel_gpt_fw_*): в отчёт идут её схема и правила."""
    try:
        from app.services.node_rules import NODE_FLOW, node_local_key

        group = {f.key for f in NODE_FLOW}
        return any(node_local_key(str(n["id"])) in group and str(n["id"]) not in group for n in canvas)
    except ImportError:
        return False


async def _report_nodes(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer import report_costs, report_html
    from app.services.action_layer import report_gpt as rg

    pid = params["project_id"]
    fmt = params.get("kind", "chat")
    detail = await _effective_project(ctx, pid)
    meta = _meta(detail)
    project = _transient(pid, detail, await _project_row(pid))
    canvas = _canvas_nodes(meta)
    runs = {str(nr.get("node_key")): nr for nr in await _node_runs(ctx, pid)}
    checks, tel = _harness_checks(meta)
    # report_costs.node_costs сам отдаёт участнику пусто (total=None): колонки трат уйдут ниже.
    split = await report_costs.node_costs(
        pid, node_runs=list(runs.values()), step_nodes=report_costs.step_nodes_of(canvas)
    )
    costs, spent = split.by_node, split.total
    keys = [str(n["id"]) for n in canvas] + [k for k in runs if k not in {str(n["id"]) for n in canvas}]
    by_key = {str(n["id"]): n for n in canvas}
    cards = []
    for k in keys:
        card = _node_card(
            project, detail, k, by_key.get(k), runs.get(k), checks=checks, cost=costs.get(k), detailed=False
        )
        card["cost_how"] = ", ".join(split.how.get(k) or [])
        cards.append(card)
    cards = _safe(cards)
    rows = [_row_for(c) for c in cards]
    index = await rg.frame_index(pid, project.data_dir)
    gpt = _safe(rg.gpt_nodes(project, detail, {c["node_key"]: c for c in cards}, index))
    gpt_text = rg.gpt_text(gpt)
    extra = await rg.project_counts(pid, meta)
    counts: dict[str, int] = {}
    for c in cards:
        counts[c["status_ru"]] = counts.get(c["status_ru"], 0) + 1
    attributed = split.attributed
    title = _clip(detail.get("title") or detail.get("topic"), 80)
    summary = {
        "nodes": len(cards),
        "by_status": counts,
        "with_errors": sum(1 for c in cards if c["errors"]),
        "checks_failed": sum(1 for c in cards for h in c["harness"] if not h["ok"]),
        "spent_usd_estimate": _money(spent),
        "attributed_usd_estimate": _money(attributed),
        "unattributed_usd_estimate": _money(None if spent is None else max(spent - attributed, 0.0)),
        "nodes_without_cost_link": sum(
            1 for c in cards if c["cost_usd"] is None and c["status_ru"] not in report_costs.IDLE_STATUSES
        ),
        "harness_at": _iso(tel.get("updated_at")),
        "gpt_nodes": len(gpt),
        "frames": len(index.frames),
        "archived_frames": len(index.archived_numbers),
        "image_prompts": sum(1 for f in index.frames if f.get("image_prompt")),
        **extra,
    }
    status_text = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    head = (
        f"Отчёт по нодам проекта #{pid} «{title}»: нод {len(cards)}"
        + (f" ({status_text})" if status_text else "")
        + f", с ошибками {summary['with_errors']}."
    )
    if spent is not None:
        head += f" Траты проекта ${summary['spent_usd_estimate']} (оценка)"
        # «из них по нодам $0» без разнесённых трат только путает — пишем, лишь если есть что показать.
        head += f", из них по нодам ${summary['attributed_usd_estimate']}." if attributed else "."
    intro = head + (f"\n{gpt_text}" if gpt_text else "")
    # Трат нет (участник студии, `_node_costs`) — нет и колонки с ними.
    columns = _REPORT_COLUMNS if spent is not None else _without_cost(_REPORT_COLUMNS)
    # «Какие поля перезаписала вторая работа с GPT» — ответ из того же расчёта, что колонка html.
    over_text = rg.overwrite_text(gpt) if len(gpt) > 1 else ""
    if over_text:
        intro += f"\n{over_text}"
    out: dict[str, Any] = {"project_id": pid, "format": fmt, "summary": summary, "gpt": gpt}
    if fmt == "json":
        out["rows"] = rows
        out["text_ru"] = intro
        return _safe(out)
    if fmt == "md":
        out["markdown"] = (
            f"## Отчёт по нодам проекта #{pid} «{title}»\n\n{intro}\n\n"
            + _markdown(rg.with_no_data(rows, ("cost_usd",)), columns)
            + _gpt_markdown(gpt, full=True)
        )
        out["text_ru"] = intro
        return _safe(out)
    # chat, html и xlsx: оба файла пишутся всегда, чтобы из чата можно было скачать полный отчёт.
    base = Path(project.data_dir) / "reports" / f"nodes_report_{pid}_{rg._stamp()}"
    xlsx_path, html_path = base.with_suffix(".xlsx"), base.with_suffix(".html")
    pairs = [
        ("Проект", f"#{pid} {title}"),
        ("Сформирован", datetime.now().isoformat(timespec="seconds")),
        ("Нод", len(cards)),
        ("С ошибками", summary["with_errors"]),
        ("Проверок не пройдено", summary["checks_failed"]),
        ("Траты проекта, $ (оценка)", summary["spent_usd_estimate"]),
        ("Из них по нодам, $ (оценка)", summary["attributed_usd_estimate"]),
        ("Не разнесено по нодам, $ (оценка)", summary["unattributed_usd_estimate"]),
        ("Нод «Работа с GPT»", len(gpt)),
        ("Ячеек (без архивных)", summary["frames"]),
        ("Архивных ячеек не показано", summary["archived_frames"]),
        ("Персонажей в проекте", extra["characters_in_project"]),
        ("Сцен в реестре", extra["scenes_in_registry"]),
    ]
    if spent is None:
        pairs = [pair for pair in pairs if not pair[0].startswith(("Траты", "Из них"))]
    pairs += [(f"Статус: {k}", v) for k, v in counts.items()]
    if gpt_text:
        pairs.append(("Кратко", gpt_text))
    _write_xlsx(xlsx_path, rows, pairs, gpt, columns)
    trace_runs, diary = _trace_runs(project.data_dir)
    html_text = report_html.render_nodes_html(
        _safe(
            {
                "project_id": pid,
                "title": title,
                "slug": project.slug,
                "stamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "rows": rows,
                "gpt": gpt,
                "frames": index.frames,
                "archived_frames": summary["archived_frames"],
                "runs": trace_runs,
                "diary": diary,
                "with_rules": _has_frames_group(canvas),
                # Участнику трат нет (`_node_costs`): ни колонок, ни раздела.
                "show_costs": spent is not None,
                "costs": {
                    "total": spent,
                    "attributed": attributed,
                    "unattributed": summary["unattributed_usd_estimate"],
                    "by_source": split.by_source,
                }
                if spent is not None
                else {},
            }
        )
    )
    html_path.write_text(html_text, encoding="utf-8")
    from app.web.routers.project_reports import report_url

    html_url, xlsx_url = report_url(pid, html_path), report_url(pid, xlsx_path)
    primary = xlsx_path if fmt == "xlsx" else html_path
    out.update(
        path=str(primary),
        rows=len(rows),
        download_url=report_url(pid, primary),
        file_name=primary.name,
        file_format=primary.suffix.lstrip("."),
        html_path=str(html_path),
        xlsx_path=str(xlsx_path),
        downloads=[{"url": html_url, "file_format": "html"}, {"url": xlsx_url, "file_format": "xlsx"}],
    )
    link = f"Файл отчёта готов: [Скачать отчёт (html)]({html_url}) · [Скачать отчёт (xlsx)]({xlsx_url})"
    # Ссылка сразу после первой строки: длинный текст в чате обрезается с конца.
    out["text_ru"] = (
        f"{head} {link}" + (f"\n{gpt_text}" if gpt_text else "") + (f"\n{over_text}" if over_text else "")
    )
    if fmt in ("xlsx", "html"):
        return _safe(out)
    short_cols = (
        ("title", "Нода"),
        ("status_ru", "Статус"),
        ("model", "Модель"),
        ("checks", "Проверки"),
        ("result", "Результат"),
        ("cost_usd", "$ (оценка)"),
    )
    if spent is None:
        short_cols = _without_cost(short_cols)
    shown = rg.with_no_data(rows[:_CHAT_ROWS], ("cost_usd",))
    table = _markdown([{**r, "title": f"{r['title']} ({r['node_key']})"} for r in shown], short_cols)
    tail = f"\n\nЕщё нод: {len(rows) - len(shown)} (полный отчёт в файле)." if len(rows) > len(shown) else ""
    errors = [f"- {c['title']}: {c['errors'][0]}" for c in cards if c["errors"]][:5]
    err_text = ("\n\nОшибки:\n" + "\n".join(errors)) if errors else ""
    out["markdown"] = f"{intro}\n\n{table}{tail}{err_text}{_gpt_markdown(gpt, full=False)}\n\n{link}"
    return _safe(out)


async def _report_gpt_data(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.report_gpt_data import report_gpt_data

    return await report_gpt_data(ctx, params)


async def _report_projects(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.report_gpt import report_projects

    return await report_projects(ctx, params)


async def _project_if_given(ctx: ActionContext, params: dict) -> str | None:
    if params.get("project_id") is None:
        return None
    await ctx.call("GET", f"/projects/{params['project_id']}")
    return None


_PRE_PROJECT_OPTIONAL = Precondition("project_exists", "Проект существует (если указан)", _project_if_given)


def _status_filter_enum() -> list[str]:
    from app.models import ProjectStatus

    return [*_OVERVIEW_GROUPS, *(s.value for s in ProjectStatus)]


def _specs() -> list[ActionSpec]:
    pid_only = _schema({"project_id": _PID}, ["project_id"])
    limit = {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}
    return [
        ActionSpec(
            id="project.status",
            title_ru="Статус проекта",
            description_ru="Где сейчас проект: статус, текущий и следующий шаг, сколько шагов готово, "
            "пауза и её причина, последняя ошибка шага, идёт ли генерация, открытые "
            "инциденты. Только чтение, работает и в управляемом режиме.",
            params_schema=pid_only,
            executor=_project_status,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("project", "status", "статус", "состояние", "прогресс", "шаг", "пауза", "где"),
        ),
        ActionSpec(
            id="node.output",
            title_ru="Результат ноды",
            description_ru="Что сделала нода: статус, попытки, модель, вариант промта, проверки "
            "(harness и вердикт проверки), файлы результата, дневник вызовов, ошибки, "
            "инциденты и траты по ноде (оценка). Только чтение.",
            params_schema=_schema({"project_id": _PID, "node_key": _NODE_KEY}, ["project_id", "node_key"]),
            executor=_node_output,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("node", "output", "результат", "выход", "нода", "ответ", "проверка", "ошибка"),
        ),
        ActionSpec(
            id="diag.incidents",
            title_ru="Инциденты и диагнозы проекта",
            description_ru="Открытые инциденты диагностики: факт, причина, можно ли повторять, что "
            "делать; плюс последние ошибки и предупреждения из ленты событий. "
            "status=all добавляет закрытые. Только чтение.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "status": {"type": "string", "enum": ["open", "all"], "default": "open"},
                    "limit": limit,
                },
                ["project_id"],
            ),
            executor=_diag_incidents,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=(
                "diag",
                "incident",
                "инцидент",
                "диагноз",
                "диагностика",
                "ошибка",
                "сломалось",
                "почему",
                "упал",
                "причина",
            ),
        ),
        ActionSpec(
            id="logs.tail",
            title_ru="Последние записи журнала сервера",
            description_ru="Последние строки логов сервера (backend и studio-live): по проекту, если "
            "указан, только ошибки или ошибки с предупреждениями. Секреты скрыты. "
            "Только чтение.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "kind": {"type": "string", "enum": ["errors", "warnings", "all"], "default": "warnings"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
                },
                [],
            ),
            executor=_logs_tail,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT_OPTIONAL,),
            scope="global",
            tags=("logs", "лог", "логи", "журнал", "ошибка", "предупреждение", "traceback"),
        ),
        ActionSpec(
            id="spend.summary",
            title_ru="Сводка трат",
            description_ru="Траты по журналу расходов: сегодня, по проекту и по последнему запуску "
            "проекта, с пометкой «оценка», где цена неточна, и текущие потолки "
            "(0 = без лимита). Только чтение.",
            params_schema=_schema({"project_id": _PID}, []),
            executor=_spend_summary,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT_OPTIONAL,),
            scope="global",
            tags=("spend", "траты", "расход", "деньги", "потрачено", "стоимость", "бюджет", "сколько"),
        ),
        ActionSpec(
            id="projects.overview",
            title_ru="Список проектов со статусами",
            description_ru="Все проекты: номер, название, статус по базе проекта, шаг, пауза и её "
            "причина или ошибка, место в очереди, когда обновлён. Фильтр status: "
            "running (идут), attention (пауза, ошибка или статус расходится), queue "
            "(пачка в очереди генерации по порядку) или точный статус. Только чтение.",
            params_schema=_schema(
                {
                    "status": {"type": "string", "enum": _status_filter_enum()},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
                },
                [],
            ),
            executor=_projects_overview,
            risk=RISK_READ,
            idempotent=True,
            scope="global",
            tags=("projects", "overview", "проекты", "список", "все", "обзор", "статусы"),
        ),
        ActionSpec(
            id="report.nodes",
            title_ru="Отчёт по нодам проекта",
            description_ru="Отчёт в виде отчёта «Кадры»: прогоны нод, таблица по всем нодам (тип, "
            "статус, модель, вариант промта, проверки, результат, стоимость по нодам — "
            "оценка, где нет связи с тратами — «нет данных»), кто пишет одно поле, по "
            "нодам «Работа с GPT» всё, что записали все пачки (кадры и поля целиком, "
            "персонажи, сцены), по ячейкам без архивных. Цифры собирает код. kind: chat "
            "(коротко + файлы html и xlsx), html или xlsx (оба файла в папке проекта "
            "reports), md или json.",
            params_schema=_schema(
                {
                    "project_id": _PID,
                    "kind": {
                        "type": "string",
                        "enum": ["chat", "md", "json", "xlsx", "html"],
                        "default": "chat",
                    },
                },
                ["project_id"],
            ),
            executor=_report_nodes,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("report", "nodes", "отчёт", "отчет", "ноды", "таблица", "excel", "итог", "сводка", "gpt"),
        ),
        ActionSpec(
            id="report.projects",
            title_ru="Отчёт по нескольким проектам",
            description_ru="Сводка по нескольким проектам в виде отчёта «Кадры»: название, статус, "
            "сколько шагов готово, кадров без архивных, картинок, видео, траты (оценка); "
            "по каждому проекту прогоны нод и ноды «Работа с GPT» с промтами и стоимостью. "
            "project_ids: номера проектов (1..20); без них последние 20 проектов. "
            "Файлы html и xlsx для скачивания из чата. Только чтение.",
            params_schema=_schema(
                {
                    "project_ids": {"type": "array", "items": _PID, "minItems": 1, "maxItems": 20},
                    "kind": {"type": "string", "enum": ["chat", "json", "xlsx", "html"], "default": "chat"},
                },
                [],
            ),
            executor=_report_projects,
            risk=RISK_READ,
            idempotent=True,
            scope="global",
            tags=(
                "report",
                "projects",
                "отчёт",
                "отчет",
                "проекты",
                "несколько",
                "все",
                "сводка",
                "таблица",
                "excel",
            ),
        ),
    ]


def _gpt_data_spec() -> ActionSpec:
    from app.services.action_layer import report_data_format as fmt

    return ActionSpec(
        id="report.gpt_data",
        title_ru="Отчёт по данным работы с GPT",
        description_ru="Что записали ноды «Работа с GPT» проекта: по кадрам (№, закадр, сцена, место, "
        "персонажи, действие, промты картинок и видео), список персонажей и сцен. node_key — одна "
        "нода (n_excel_gpt_1), иначе все. preset: standard (по умолчанию), scenes_characters "
        "(«только сцены и персонажи»), image_prompts («с промтами картинок»), animation_prompts, "
        "full; columns — свои колонки. Таблица в чате и файлы xlsx и html. Только чтение, бесплатно.",
        params_schema=_schema(
            {
                "project_id": _PID,
                "node_key": _NODE_KEY,
                "preset": {"type": "string", "enum": list(fmt.PRESETS), "default": fmt.DEFAULT_PRESET},
                "columns": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(fmt.FRAME_COLUMNS)},
                    "maxItems": len(fmt.FRAME_COLUMNS),
                },
                "kind": {"type": "string", "enum": ["chat", "json", "xlsx", "html"], "default": "chat"},
            },
            ["project_id"],
        ),
        executor=_report_gpt_data,
        risk=RISK_READ,
        idempotent=True,
        preconditions=(_PRE_PROJECT,),
        tags=(
            "report",
            "gpt",
            "гпт",
            "отчёт",
            "отчет",
            "данные",
            "сцены",
            "персонажи",
            "герои",
            "кадры",
            "промты",
            "выгрузка",
            "xlsx",
        ),
    )


def register_insight(registry: ActionRegistry) -> None:
    registry.register_many([*_specs(), _gpt_data_spec()])
