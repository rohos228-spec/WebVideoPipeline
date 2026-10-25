"""Честный итог запущенного шага: дошёл ли проект до результата на самом деле.

Опрос шага видит только, что проект вышел из running-статуса. Этого мало:
очередь генерации откатывает running-статус назад (rollback_running_for_queue),
кнопка «Стоп» оставляет user_stop, проверка Студии после готового шага ставит
паузу с auto_paused_reason, а нода «Работа с GPT» из completed_keys становится
готовой без вызова GPT. Здесь по данным Студии решаем, выполнен ли шаг.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.services import launch_risks as lr
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import JobState

# Сколько ждём после готового шага у проекта с автопродвижением: проверка Студии
# (GPT-ревью, harness gate) идёт уже при ready-статусе и может поставить паузу.
SETTLE_S = 90.0
# Запас на разницу часов записей Студии (и файловой системы) и времени запуска.
_FRESH_SLACK_S = 2.0
_SETTLE_KEYS = ("settle_status", "settle_since")
# Начало текста «шаг не сделан»: запуск шага подставляет вместо него имя проекта и шага.
NOT_DONE_HEADS = ("шаг не выполнен: ", "нода не выполнена: ")


def launch_marks(step_code: str, before: dict | None, after: dict | None, launched_at: float) -> dict:
    """Что запомнить при запуске: статус до, running-статус шага и время запуска.

    running-статус берём из ответа запуска (граф проекта: нода GPT идёт как enriching_N),
    иначе из меню шагов. Время берём до запроса запуска: пока он отвечает, воркер уже
    может писать файлы ноды.
    """
    after_status = str((after or {}).get("status") or "")
    running = after_status if lr.is_running(after_status) else _menu_running(step_code)
    out: dict[str, Any] = {"launched_at": float(launched_at)}
    if (before or {}).get("status"):
        out["status_before"] = str(before["status"])
    if running:
        out["running_status"] = running
    return out


def _menu_running(step_code: str) -> str | None:
    from app.orchestrator.pipeline_steps import step_by_code

    step = step_by_code(step_code)
    return step.running_status.value if step is not None and step.running_status else None


def _order(status: str) -> int | None:
    from app.models import ProjectStatus
    from app.orchestrator.pipeline_steps import status_order

    try:
        return status_order(ProjectStatus(status))
    except ValueError:
        return None


def _status_ru(status: str) -> str:
    from app.services.action_layer.builtin_insight import _status_ru as ru

    return ru(status)


def _clip(text: Any, n: int = 400) -> str:
    return " ".join(str(text or "").split())[:n]


def stop_reason(meta: Any) -> str:
    """Почему Студия остановила шаг: её же запись в meta (step_failure / auto_paused_reason)."""
    meta = meta if isinstance(meta, dict) else {}
    fs = meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else {}
    why = _clip(fs.get("last_error") or meta.get("auto_paused_reason"))
    return f". Причина: {why}" if why else ""


def _stop_flag(project_id: int) -> bool:
    from app.services.step_cancel import stop_flag_path

    try:
        return stop_flag_path(int(project_id)).exists()
    except OSError:
        return False


def _in_gen_queue(project_id: int) -> bool:
    from app.services.sidebar_layout import get_gen_queue

    try:
        return int(project_id) in get_gen_queue()
    except (OSError, ValueError):
        return False


def rollback_reason(project_id: int, started: dict | None, final_status: str, meta: Any) -> str | None:
    """Текст, если статус после шага не дальше его running-статуса (шаг откатили), иначе None."""
    running = str((started or {}).get("running_status") or "")
    if not running or not final_status or final_status == running:
        return None
    got, need = _order(final_status), _order(running)
    if got is None or need is None or got > need:
        return None
    meta = meta if isinstance(meta, dict) else {}
    back = f"проект вернулся на «{_status_ru(final_status)}»"
    if lr.user_stopped(meta):
        return f"шаг не выполнен: проект остановлен кнопкой «Стоп», {back}"
    stop_file, queued = _stop_flag(project_id), _in_gen_queue(project_id)
    if stop_file and queued:
        return (
            f"шаг не выполнен: очередь генерации откатила его, {back}. Так бывает, когда "
            "проект выпадает из окна одновременных запусков очереди"
        )
    fs = meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else {}
    if fs.get("last_error") and str(fs.get("last_running") or "") == running:
        return f"шаг не выполнен: ошибка шага, {back}. Причина: {_clip(fs['last_error'])}"
    if stop_file:
        return f"шаг не выполнен: шаг прерван остановкой проекта, {back}"
    if queued:
        return f"шаг не выполнен: очередь генерации откатила его, {back}"
    return f"шаг не выполнен: статус проекта откатился назад, {back}"


def _forget_settle(started: dict | None) -> None:
    if isinstance(started, dict):
        for key in _SETTLE_KEYS:
            started.pop(key, None)


def _settling(started: dict | None, final_status: str, detail: dict) -> bool:
    """Проект с автопродвижением после готового шага: ждём, не поставит ли проверка паузу."""
    if not isinstance(started, dict) or not detail.get("auto_mode"):
        return False
    now = time.time()
    if started.get("settle_status") != final_status:
        started["settle_status"], started["settle_since"] = final_status, now
    return now - float(started.get("settle_since") or now) < SETTLE_S


async def honest_state(ctx, params: dict, started: dict | None, state: JobState) -> JobState:
    """Итог опроса с проверкой паузы, ошибки, отката статуса и стопа."""
    if not state.done:
        ready = (started or {}).get("settle_status")
        _forget_settle(started)
        if ready:
            # Проверка пропустила готовый шаг, и Студия сама запустила следующий: наш шаг
            # выполнен. Иначе опрос ждал бы всю цепочку и чужую паузу списал бы на этот шаг.
            return JobState(
                done=True,
                ok=True,
                result={"final_status": ready},
                note=f"шаг дошёл до «{_status_ru(ready)}», Студия запустила следующий",
            )
        return state
    if not state.ok:
        return state
    final = str((state.result or {}).get("final_status") or "")
    try:
        detail = await ctx.call("GET", f"/projects/{params['project_id']}")
    except ActionCallError:
        detail = None
    detail = detail if isinstance(detail, dict) else {}
    meta = detail.get("meta")
    if final == "failed":
        _forget_settle(started)
        return replace(
            state, ok=False, error="шаг не выполнен: Студия остановила его с ошибкой" + stop_reason(meta)
        )
    if final == "paused":
        _forget_settle(started)
        return replace(state, ok=False, error="шаг не выполнен: проект на паузе" + stop_reason(meta))
    why = rollback_reason(params["project_id"], started, final, meta)
    if why:
        _forget_settle(started)
        return replace(state, ok=False, error=why)
    if _settling(started, final, detail):
        return JobState(
            done=False,
            note=f"шаг дошёл до «{_status_ru(final)}», ждём проверку "
            "Студии: она может поставить проект на паузу",
        )
    _forget_settle(started)
    return state


# Код-ноды на месте «Работы с GPT»: отчёт и стиль собираются программой, GPT не вызывают.
_CODE_NODE_TAILS = ("_fw_report", "_fw_style")
_SKIP_TEXT = "нода не выполнена: GPT не вызывался, Студия сочла ноду уже выполненной и оставила прежний ответ"
_SKIP_BY_FILES_TEXT = (
    "нода не выполнена: похоже, GPT не вызывался, Студия сочла ноду уже "
    "выполненной и оставила прежний ответ. Записей Студии о вызовах GPT нет, "
    "судим только по тому, что в папке ноды нет файлов новее запуска"
)


def _code_node(key: str) -> bool:
    return any(key.endswith(tail) or f"{tail}_" in key for tail in _CODE_NODE_TAILS)


def _epoch(value: Any) -> float | None:
    """Время из записи Студии в секундах. Время без пояса считаем местным (так пишет дневник нод)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _meta_reply_at(meta: dict, key: str) -> float | None:
    """Когда нода последний раз сохранила ответ GPT: результат ноды и её отметка ответа.

    Обе записи Студия делает только после ответа модели, на пропуске ноды из completed_keys
    их никто не пишет.
    """
    marks: list[float] = []
    for bucket, field in (("gpt_operator_results", "at"), ("excel_gpt_nodes", "lastReplyAt")):
        entries = meta.get(bucket) if isinstance(meta.get(bucket), dict) else {}
        entry = entries.get(key) if isinstance(entries.get(key), dict) else {}
        at = _epoch(entry.get(field))
        if at is not None:
            marks.append(at)
    return max(marks, default=None)


def _trace_call_at(data_dir: Any, key: str) -> float | None:
    """Начало последнего прогона ноды в дневнике нод, если в нём был хоть один вызов GPT."""
    from app.services.node_trace import _safe_name, read_run, trace_root

    folder = trace_root(data_dir) / _safe_name(key)
    try:
        runs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.endswith(".del"))
    except OSError:
        return None
    marks = [_epoch(run.get("started")) for run in map(read_run, runs) if int(run.get("calls") or 0) > 0]
    return max((m for m in marks if m is not None), default=None)


async def _observed_call_at(pid: int, key: str) -> float | None:
    """Последний вызов модели этой нодой по журналу наблюдения (мастер-база).

    model_start/model_end пишет обёртка запроса к модели с ключом ноды, на пропуске ноды из
    completed_keys их нет. Журнал ведётся только при включённом наблюдении и доставляется из
    очереди с задержкой, поэтому он лишь подтверждает вызов, а отсутствие записи ничего не значит.
    Журнал трат здесь не помогает: его request_id — id такта воркера, а не вызова модели.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.action_layer import builtin_insight as bi
    from app.services.action_layer.report_costs import _when

    marks: list[float] = []
    for table in ("observation_events", "observation_outbox"):
        try:
            async with bi._sessions()() as s:
                rows = (
                    await s.execute(
                        text(
                            f"SELECT created_at FROM {table} WHERE project_id = :p AND node_key = :k "
                            "AND kind IN ('model_start', 'model_end')"
                        ),
                        {"p": int(pid), "k": key},
                    )
                ).all()
        except SQLAlchemyError:
            continue
        # В журнале наблюдения время без пояса и в UTC (datetime.utcnow).
        marks += [m.replace(tzinfo=UTC).timestamp() for m in (_when(r[0]) for r in rows) if m is not None]
    return max(marks, default=None)


def _newest_file_at(folder) -> float:
    try:
        return max((p.stat().st_mtime for p in folder.iterdir() if p.is_file()), default=0.0)
    except OSError:
        return 0.0


async def gpt_skipped(ctx, params: dict, started: dict | None) -> str | None:
    """Нода «Работа с GPT» стала готовой без вызова GPT после запуска: текст для чата, иначе None.

    Ищем след вызова модели именно этой нодой после запуска: сохранённый ответ ноды в данных
    проекта, прогон в дневнике нод с вызовами, вызов модели нодой в журнале наблюдения. Нет ни одного —
    Студия закрыла ноду без вызова (пропуск из completed_keys и лечение NodeRun в done).
    Только если данных проекта прочитать нельзя, судим по времени файлов в папке ноды и говорим об этом.
    """
    launched = (started or {}).get("launched_at")
    if not isinstance(launched, (int, float)) or not params.get("node_key"):
        return None
    from app.services.action_layer import builtin_insight as bi
    from app.services.excel_gpt_node import upload_dir

    pid, key = params["project_id"], str(params["node_key"])
    if _code_node(key):
        return None
    try:
        detail = await ctx.call("GET", f"/projects/{pid}")
    except ActionCallError:
        return None
    if not isinstance(detail, dict):
        return None
    row = await bi._project_row(pid)
    project = bi._transient(pid, detail, row)
    # Ответ ноды смотрим и в meta из detail (GET /projects/{id}), и в строке проекта: на этой
    # линии база одна, но detail может нести эффективную meta холста, а строка — сохранённую.
    metas = [bi._meta(detail)]
    if row is not None and isinstance(row.meta, dict):
        metas.append(row.meta)
    since = float(launched) - _FRESH_SLACK_S
    marks = [_meta_reply_at(meta, key) for meta in metas]
    marks += [_trace_call_at(project.data_dir, key), await _observed_call_at(pid, key)]
    if any(m is not None and m >= since for m in marks):
        return None
    if any(metas):
        return _SKIP_TEXT
    if _newest_file_at(upload_dir(project, key)) >= since:
        return None
    return _SKIP_BY_FILES_TEXT
