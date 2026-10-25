from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Connection

from app.services import notification_log

CHANNEL_CHAT = "chat"
LEVELS = ("info", "success", "warning", "error")
TITLE_LIMIT = 200
TEXT_LIMIT = 2000
LABEL_LIMIT = 80
MAX_ACTIONS = 4
DEFAULT_DEDUP_S = 900
FOREVER_S = 0
RATE_WINDOW_S = 300
RATE_MAX = 6
EVENT_TYPE = "chat_notice"

CHAT_NOTICES_DDL = """
CREATE TABLE IF NOT EXISTS chat_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    project_id INTEGER,
    log_id INTEGER,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    dedup_key TEXT,
    actions TEXT NOT NULL DEFAULT '[]',
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_chat_notices_project ON chat_notices (project_id, id);
CREATE INDEX IF NOT EXISTS ix_chat_notices_dedup ON chat_notices (dedup_key, ts);
"""

_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_STATS = {"written": 0, "deduped": 0, "rate_limited": 0, "failed": 0, "dropped_actions": 0}
_ensured: set[str] = set()
_recent: dict[tuple, float] = {}
_rate: dict[tuple, list[float]] = {}
_tasks: set[asyncio.Task] = set()

TELEGRAM_KINDS = frozenset({"watchdog_stalled", "observer_silent", "observer_silent_resolved"})


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def reset() -> None:
    with _LOCK:
        for k in _STATS:
            _STATS[k] = 0
        _recent.clear()
        _rate.clear()
    _ensured.clear()


def _bump(name: str, n: int = 1) -> None:
    with _LOCK:
        _STATS[name] += n


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ts(dt: datetime) -> str:
    return dt.isoformat(sep=" ")


def _iso(ts: str | None) -> str | None:
    if not ts:
        return None
    return ts.replace(" ", "T") + "Z"


def _clip(value: Any, limit: int) -> str:
    from app.services.redact import redact_text

    s = redact_text("" if value is None else str(value)).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


_EMPTY_COUNT = re.compile(r"(?<!\d)0 из 0(?!\d)")
_EMPTY_TAIL = re.compile(r":\s*\.?\s*$")
_EMPTY_WRAP = re.compile(r"\s*(«\s*»|\(\s*\)|\[\s*\])")


def _empty_part(part: str, last: bool) -> bool:
    core = part.strip()
    if not core.strip(" .;:,—-"):
        return True
    if _EMPTY_COUNT.search(core):
        return True
    if re.search(r":\s*\.\s*$", core):
        return True
    return last and _EMPTY_TAIL.search(core) is not None


def tidy_text(text: Any) -> str:
    from app.services.plain_ru import plain_ru

    raw = plain_ru(text)
    lines: list[str] = []
    source = raw.splitlines()
    for index, line in enumerate(source):
        cleaned = _EMPTY_WRAP.sub("", line).rstrip()
        parts = cleaned.split("; ")
        kept = [
            part.strip()
            for pos, part in enumerate(parts)
            if not _empty_part(part, index == len(source) - 1 and pos == len(parts) - 1)
        ]
        joined = "; ".join(kept).strip()
        if not joined:
            continue
        if cleaned.endswith(".") and not joined.endswith((".", "!", "?", "…")):
            joined += "."
        lines.append(joined)
    return "\n".join(lines)


# Ключ advisory-блокировки Postgres для записи уведомлений: проверка дубля и
# вставка должны идти одной критической секцией и между процессами тоже.
_PG_WRITE_LOCK_KEY = 0x63686174  # "chat"


def _connect() -> Connection:
    """Соединение SQLAlchemy к базе приложения (SQLite-файл или Postgres)."""
    eng = notification_log.engine()
    notification_log.ensure_sqlite_schema(
        eng, CHAT_NOTICES_DDL + notification_log.NOTIFICATION_LOG_DDL, notification_log.scope_key(), _ensured
    )
    return eng.connect()


def _begin_exclusive(con: Connection) -> None:
    """Писатель один на базу: ``BEGIN IMMEDIATE`` на SQLite, advisory lock на Postgres."""
    if con.dialect.name == "sqlite":
        con.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        con.execute(sa_text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PG_WRITE_LOCK_KEY})


def clean_actions(actions: Any) -> list[dict]:
    if not actions:
        return []
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    try:
        ensure_builtin_actions()
    except Exception as exc:
        logger.warning("chat_notice: action catalog unavailable: {}", type(exc).__name__)
        _bump("dropped_actions", len(actions))
        return []
    out: list[dict] = []
    for item in actions:
        if len(out) >= MAX_ACTIONS:
            _bump("dropped_actions")
            continue
        if not isinstance(item, dict):
            _bump("dropped_actions")
            continue
        action_id = str(item.get("action_id") or "")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        spec = registry.find(action_id)
        if spec is None:
            _bump("dropped_actions")
            logger.warning("chat_notice: unknown action {} dropped", action_id[:80])
            continue
        problems = registry.validate_params(action_id, params)
        if problems:
            _bump("dropped_actions")
            logger.warning("chat_notice: bad params for {} dropped: {}", action_id, list(problems)[:3])
            continue
        label = _clip(item.get("label") or spec.title_ru, LABEL_LIMIT)
        out.append(
            {
                "action_id": action_id,
                "label": label,
                "params": dict(params),
                "risk": spec.risk,
                "needs_approval": bool(spec.needs_approval),
            }
        )
    return out


def _row_to_notice(r: Any) -> dict:
    from app.services.plain_ru import plain_ru

    d = dict(r)
    try:
        acts = json.loads(d.get("actions") or "[]")
    except ValueError:
        acts = []
    return {
        "id": int(d["id"]),
        "project_id": d.get("project_id"),
        "level": d.get("level") or "info",
        "title": plain_ru(d.get("title") or ""),
        "text": plain_ru(d.get("text") or ""),
        "kind": d.get("kind") or "",
        "created_at": _iso(d.get("ts")),
        "suggested_actions": acts if isinstance(acts, list) else [],
        "read": bool(d.get("read_at")),
        "read_at": _iso(d.get("read_at")),
    }


def _dedup_hit(con: Connection, dedup_key: str, window_s: int, now: datetime) -> bool:
    if window_s and window_s > 0:
        row = con.execute(
            sa_text("SELECT 1 FROM chat_notices WHERE dedup_key = :key AND ts >= :since LIMIT 1"),
            {"key": dedup_key, "since": _ts(now - timedelta(seconds=window_s))},
        ).fetchone()
    else:
        row = con.execute(
            sa_text("SELECT 1 FROM chat_notices WHERE dedup_key = :key LIMIT 1"), {"key": dedup_key}
        ).fetchone()
    return row is not None


def _notice_row(con: Connection, notice_id: int) -> Any:
    return (
        con.execute(sa_text("SELECT * FROM chat_notices WHERE id = :id"), {"id": int(notice_id)})
        .mappings()
        .fetchone()
    )


def _write(
    *, project_id, level, kind, title, text, dedup_key, window_s, actions, run_id
) -> tuple[str, dict | None]:
    now = _now()
    ts = _ts(now)
    with _WRITE_LOCK:
        con = _connect()
        try:
            _begin_exclusive(con)
            if dedup_key and _dedup_hit(con, dedup_key, window_s, now):
                con.rollback()
                return "deduped", None
            if not _rate_ok(project_id, kind):
                con.rollback()
                return "rate_limited", None
            log_id = None
            if notification_log.enabled():
                body = text if text.startswith(title.rstrip(".")) else (f"{title}. {text}" if text else title)
                log_id = notification_log.insert_row(
                    con,
                    (
                        ts,
                        project_id,
                        str(run_id) if run_id else None,
                        CHANNEL_CHAT,
                        kind,
                        body[: notification_log.TEXT_LIMIT],
                        1,
                        None,
                    ),
                )
            notice_id = int(
                con.execute(
                    sa_text(
                        "INSERT INTO chat_notices (ts, project_id, log_id, level, kind, title, text, dedup_key, actions) "
                        "VALUES (:ts, :project_id, :log_id, :level, :kind, :title, :text, :dedup_key, :actions) "
                        "RETURNING id"
                    ),
                    {
                        "ts": ts,
                        "project_id": project_id,
                        "log_id": log_id,
                        "level": level,
                        "kind": kind,
                        "title": title,
                        "text": text,
                        "dedup_key": dedup_key,
                        "actions": json.dumps(actions, ensure_ascii=False),
                    },
                ).scalar_one()
            )
            con.commit()
            row = _notice_row(con, notice_id)
            return "written", _row_to_notice(row)
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def _scope() -> str:
    return notification_log.scope_key()


def _seen_recently(dedup_key: str, window_s: int) -> bool:
    with _LOCK:
        at = _recent.get((_scope(), dedup_key))
    if at is None:
        return False
    return not window_s or window_s <= 0 or time.monotonic() - at < window_s


def _remember(dedup_key: str) -> None:
    with _LOCK:
        _recent[(_scope(), dedup_key)] = time.monotonic()
        if len(_recent) > 5000:
            for k in sorted(_recent, key=lambda key: _recent[key])[:1000]:
                _recent.pop(k, None)


def _rate_ok(project_id: int | None, kind: str) -> bool:
    key = (_scope(), project_id, kind)
    now = time.monotonic()
    with _LOCK:
        hits = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_MAX:
            _rate[key] = hits
            return False
        hits.append(now)
        _rate[key] = hits
        return True


async def publish(notice_dict: dict) -> None:
    from app.services.event_bus import get_bus
    from app.services.tenant import current_tenant

    # Метка арендатора: WebSocket (`member_money.ws_event_for_viewer`) не шлёт уведомление
    # чужому клиенту из общего канала `global` и маскирует суммы участнику.
    evt = {
        "type": EVENT_TYPE,
        "project_id": notice_dict.get("project_id"),
        "notice": notice_dict,
        "tenant_id": current_tenant(),
    }
    bus = get_bus()
    if notice_dict.get("project_id") is not None:
        await bus.publish(f"projects.{notice_dict['project_id']}", evt)
    await bus.publish("global", evt)


async def send(
    project_id: int | None,
    level: str,
    title: str,
    text: str = "",
    *,
    kind: str,
    dedup_key: str | None = None,
    dedup_window_s: int = DEFAULT_DEDUP_S,
    suggested_actions: list[dict] | None = None,
    run_id: str | None = None,
) -> tuple[str, dict | None]:
    try:
        pid = int(project_id) if project_id is not None else None
        lvl = level if level in LEVELS else "info"
        k = (str(kind or "notice"))[:80]
        t = _clip(tidy_text(title), TITLE_LIMIT) or "Уведомление"
        body = _clip(tidy_text(text), TEXT_LIMIT)
        key = str(dedup_key)[:200] if dedup_key else None
        if key and _seen_recently(key, dedup_window_s):
            _bump("deduped")
            return "deduped", None
        actions = clean_actions(suggested_actions)
        status, made = await asyncio.to_thread(
            _write,
            project_id=pid,
            level=lvl,
            kind=k,
            title=t,
            text=body,
            dedup_key=key,
            window_s=dedup_window_s,
            actions=actions,
            run_id=run_id,
        )
    except Exception as exc:
        _bump("failed")
        logger.warning("chat_notice: not written ({}): {}: {}", kind, type(exc).__name__, exc)
        return "failed", None
    _bump(status)
    if key and status in {"written", "deduped"}:
        _remember(key)
    if made is None:
        return status, None
    try:
        await publish(made)
    except Exception as exc:
        logger.warning("chat_notice: publish failed: {}", type(exc).__name__)
    return status, made


async def notice(
    project_id: int | None,
    level: str,
    title: str,
    text: str = "",
    *,
    kind: str,
    dedup_key: str | None = None,
    dedup_window_s: int = DEFAULT_DEDUP_S,
    suggested_actions: list[dict] | None = None,
    run_id: str | None = None,
) -> dict | None:
    return (
        await send(
            project_id,
            level,
            title,
            text,
            kind=kind,
            dedup_key=dedup_key,
            dedup_window_s=dedup_window_s,
            suggested_actions=suggested_actions,
            run_id=run_id,
        )
    )[1]


def notice_soon(*args: Any, **kwargs: Any) -> asyncio.Task | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(notice(*args, **kwargs))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def _list_sync(
    project_id: int | None, since_id: int, limit: int, include_global: bool, unread_only: bool
) -> list[dict]:
    if not notification_log.on_postgres() and not notification_log._db_path().exists():
        return []
    con = _connect()
    try:
        where = ["id > :since_id"]
        args: dict[str, Any] = {"since_id": int(since_id), "limit": int(limit)}
        if project_id is not None:
            if include_global:
                where.append("(project_id = :project_id OR project_id IS NULL)")
            else:
                where.append("project_id = :project_id")
            args["project_id"] = int(project_id)
        if unread_only:
            where.append("read_at IS NULL")
        if since_id > 0:
            sql = f"SELECT * FROM chat_notices WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT :limit"
            rows = list(con.execute(sa_text(sql), args).mappings())
        else:
            sql = f"SELECT * FROM chat_notices WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :limit"
            rows = list(reversed(list(con.execute(sa_text(sql), args).mappings())))
        return [_row_to_notice(r) for r in rows]
    finally:
        con.close()


async def list_notices(
    project_id: int | None = None,
    since_id: int = 0,
    limit: int = 50,
    *,
    include_global: bool = True,
    unread_only: bool = False,
) -> list[dict]:
    return await asyncio.to_thread(
        _list_sync,
        project_id,
        max(0, int(since_id)),
        max(1, min(int(limit), 200)),
        include_global,
        unread_only,
    )


def _mark_sync(notice_id: int) -> dict | None:
    with _WRITE_LOCK:
        con = _connect()
        try:
            con.execute(
                sa_text("UPDATE chat_notices SET read_at = :at WHERE id = :id AND read_at IS NULL"),
                {"at": _ts(_now()), "id": int(notice_id)},
            )
            con.commit()
            row = _notice_row(con, notice_id)
            return _row_to_notice(row) if row is not None else None
        finally:
            con.close()


async def mark_read(notice_id: int) -> dict | None:
    return await asyncio.to_thread(_mark_sync, notice_id)


def _act(action_id: str, label: str, **params: Any) -> dict:
    return {"action_id": action_id, "label": label, "params": params}


def _step_of(status_value: str | None):
    if not status_value:
        return None
    try:
        from app.models import ProjectStatus
        from app.orchestrator.pipeline_steps import step_by_running_status

        return step_by_running_status(ProjectStatus(status_value))
    except Exception:
        return None


def _media_step(code: str | None) -> bool:
    try:
        from app.services.action_layer.node_run_cost import MEDIA_STEPS

        return bool(code) and code in MEDIA_STEPS
    except Exception:
        return False


def _is_finish(prev_status: str | None, new_status: str | None) -> bool:
    try:
        from app.models import ProjectStatus
        from app.services.project_state import is_running_status

        prev, new = ProjectStatus(prev_status), ProjectStatus(new_status)
    except Exception:
        return False
    if not is_running_status(prev) or is_running_status(new):
        return False
    return new not in {ProjectStatus.paused, ProjectStatus.failed, ProjectStatus.new}


async def step_done(project_id: int, prev_status: str, new_status: str) -> dict | None:
    if not _is_finish(prev_status, new_status):
        return None
    step = _step_of(prev_status)
    name = step.title if step else "Шаг"
    if new_status == "assembled":
        return await notice(
            project_id,
            "success",
            "Ролик готов",
            f"Финальная сборка проекта {project_id} завершена. Видео можно смотреть и публиковать.",
            kind="video_ready",
            dedup_key=f"video_ready:{project_id}",
            dedup_window_s=60,
        )
    if new_status == "published":
        return await notice(
            project_id,
            "success",
            "Ролик опубликован",
            f"Публикация проекта {project_id} завершена.",
            kind="published",
            dedup_key=f"published:{project_id}",
            dedup_window_s=60,
        )
    return await notice(
        project_id,
        "success",
        f"Шаг «{name}» завершён",
        f"Проект {project_id}: шаг «{name}» выполнен.",
        kind="step_done",
        dedup_key=f"step_done:{project_id}:{new_status}",
        dedup_window_s=60,
    )


def montage_finished_soon(project_id: int, status: str, error: str | None = None) -> None:
    if status == "done":
        notice_soon(
            project_id,
            "success",
            "Ролик готов",
            f"Монтаж проекта {project_id} собран. Видео можно смотреть и публиковать.",
            kind="video_ready",
            dedup_key=f"video_ready:{project_id}",
            dedup_window_s=60,
        )
    elif status == "error":
        notice_soon(
            project_id,
            "error",
            "Монтаж не собрался",
            (f"Причина: {error}. " if error else "") + "Подробности на доске монтажа.",
            kind="montage_failed",
            dedup_key=f"montage_failed:{project_id}",
            dedup_window_s=300,
            suggested_actions=[_act("montage.montage_status", "Статус монтажа", project_id=int(project_id))],
        )


_ABANDON_REASON_RU = {
    "restart_chain": "слишком много автоматических перезапусков подряд",
    "fatal_config": "ошибка настройки, повтор не поможет",
    "retries_exhausted": "исчерпаны все автоматические попытки",
}


async def step_failure(project: Any, action: str, error: BaseException | None = None) -> dict | None:
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    raw_fs = meta.get("step_failure")
    fs: dict[str, Any] = raw_fs if isinstance(raw_fs, dict) else {}
    running = fs.get("last_running")
    step = _step_of(running)
    name = step.title if step else "шаг"
    code = step.code if step else None
    reason = str(fs.get("last_error") or (f"{type(error).__name__}: {error}" if error else ""))[:400]
    err_code = str(fs.get("last_error_code") or "")
    pid = int(project.id)
    detail = f"Причина: {reason}" if reason else ""
    actions: list[dict] = []
    if action == "retry":
        return await notice(
            pid,
            "warning",
            f"Шаг «{name}» упал, повторяю сам",
            detail,
            kind="step_failure_retry",
            dedup_key=f"step_failure_retry:{pid}:{running}",
            dedup_window_s=600,
        )
    if action == "sleep":
        return await notice(
            pid,
            "warning",
            f"Шаг «{name}»: несколько ошибок подряд, пауза",
            "Проект отдыхает и потом повторит шаг сам. " + detail,
            kind="step_failure_sleep",
            dedup_key=f"step_failure_sleep:{pid}:{running}",
            dedup_window_s=600,
        )
    if action == "abandon":
        why = _ABANDON_REASON_RU.get(
            str(fs.get("abandoned_reason") or ""), "автоматические попытки закончились"
        )
        if _media_step(code):
            actions = [
                _act("step.get_run_cost_basis", "Сколько стоит повтор", project_id=pid, step_code=code),
                _act("step.run_step", "Повторить шаг (с подтверждением)", project_id=pid, step_code=code),
            ]
        return await notice(
            pid,
            "error",
            f"Проект на паузе: шаг «{name}» остановлен",
            f"Причина остановки: {why}. {detail} Продолжить может только человек.",
            kind="step_failure_abandon",
            dedup_key=f"step_failure_abandon:{pid}:{running}",
            dedup_window_s=600,
            suggested_actions=actions,
        )
    if action == "pause_infra":
        if err_code == "img_pr_no_scene_data":
            # last_error уже готовый текст: каким кадрам и какой шаг запустить.
            return await notice(
                pid,
                "error",
                f"Проект на паузе: шаг «{name}»",
                f"Причина: {reason}" if reason else "Причина: у сцен нет исходных данных.",
                kind="step_failure_pause_no_source",
                dedup_key=f"step_failure_pause_no_source:{pid}:{running}",
                dedup_window_s=600,
            )
        if err_code in {"gpt_credits", "media_credits"}:
            why = "у провайдера закончились деньги на балансе"
        elif err_code == "infra_db_locked":
            why = "база данных долго была занята"
        else:
            why = "браузер для генерации не восстановился"
        return await notice(
            pid,
            "error",
            f"Проект на паузе: шаг «{name}»",
            f"Причина: {why}. {detail} Исправьте причину и продолжите проект вручную.",
            kind="step_failure_pause_infra",
            dedup_key=f"step_failure_pause_infra:{err_code or 'other'}:{pid}:{running}",
            dedup_window_s=600,
        )
    return None


async def project_paused(project_id: int, title: str, text: str, *, kind: str) -> dict | None:
    return await notice(
        project_id, "warning", title, text, kind=kind, dedup_key=f"{kind}:{project_id}", dedup_window_s=600
    )


def project_paused_soon(project_id: int, title: str, text: str, *, kind: str) -> None:
    notice_soon(
        project_id, "warning", title, text, kind=kind, dedup_key=f"{kind}:{project_id}", dedup_window_s=600
    )


_PROVIDER_RU = {"kie": "Kie", "outsee": "Outsee"}


def unknown_outcome_soon(provider: str, project_id: int | None, key: str | None) -> None:
    if project_id is None:
        return
    who = _PROVIDER_RU.get(provider, provider)
    text = (
        f"Запрос в {who} по кадру {key or '—'} ушёл, а ответа нет. Задача могла быть принята и оплачена, "
        "поэтому повторно сама не отправляется. Проверьте историю и баланс у провайдера. "
        "Если списания не было, блокировку можно снять вручную с подтверждением."
    )
    notice_soon(
        project_id,
        "warning",
        f"Неизвестный исход отправки в {who}",
        text,
        kind="unknown_outcome",
        dedup_key=f"unknown_outcome:{provider}:{project_id}",
        dedup_window_s=1800,
        suggested_actions=[
            _act("media.list_unknown_outcomes", "Показать блокировки", project_id=int(project_id))
        ],
    )


_INCIDENT_ACTIONS = {"uncertain_request": "media.list_unknown_outcomes"}


def owner_scope(tenant_id: Any):
    """Писать уведомление от имени клиента, чья это строка (инцидент, выполнение).

    Фоновые проходы (сторож, диагностика) идут без арендатора: без этой обёртки
    уведомление о проекте клиента записалось бы строкой владельца (RLS спрятал бы
    её от клиента) и не ушло бы ему живым событием. Уже стоящий арендатор не меняется.
    """
    import contextlib

    from app.services.tenant import current_tenant, tenant_scope

    if tenant_id is None or current_tenant() is not None:
        return contextlib.nullcontext()
    return tenant_scope(str(tenant_id))


async def incident(row: Any, text: str) -> str:
    d = row.details or {}
    severity = str(getattr(row, "severity", "") or d.get("severity") or "error")
    level = "error" if severity in {"error", "critical"} else "warning"
    title = str(d.get("title") or "Нужно решение человека")
    actions = []
    act_id = _INCIDENT_ACTIONS.get(str(row.type))
    if act_id and row.project_id is not None:
        actions.append(_act(act_id, "Показать блокировки", project_id=int(row.project_id)))
    with owner_scope(getattr(row, "tenant_id", None)):
        status, _ = await send(
            row.project_id,
            level,
            title,
            text,
            kind="incident",
            dedup_key="incident:" + str(row.incident_id),
            dedup_window_s=FOREVER_S,
            suggested_actions=actions,
            run_id=getattr(row, "execution_id", None),
        )
    return status


async def from_notify(kind: str, text: str, project_id: int | None) -> dict | None:
    if kind not in TELEGRAM_KINDS:
        return None
    level = "success" if kind == "observer_silent_resolved" else "error"
    first = (text or "").split(". ")[0][:TITLE_LIMIT]
    return await notice(
        project_id,
        level,
        first or "Сторож",
        text,
        kind=kind,
        dedup_key=f"{kind}:{project_id}:{first}",
        dedup_window_s=600,
    )
