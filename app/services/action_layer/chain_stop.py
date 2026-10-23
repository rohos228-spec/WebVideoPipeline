"""Точка остановки цепочки нод «Работа с GPT» в коде чата и честный итог цепочки.

Стоп-ключ Студии (meta.excel_gpt_stop_after_key) снимает только сама нода-остановка.
Если Студия всё же пошла дальше (ключ потерялся при записи meta, автопродвижение
стартовало следующий шаг), опрос это видит: нода-остановка готова в этом запуске, а в
проекте уже идёт нода, стоящая после неё по стрелкам, и пошла она в этом запуске. Тогда
чат сам останавливает проект и честно говорит, на какой ноде. Залипшие running/queued
строки чужих нод и цикл исправлений ноды-проверки (fail -> патч -> переген -> снова
проверка) выходом за точку остановки не считаются.

Если цепочка встала раньше, итог называет последнюю готовую ноду, ноду, на которой
встали, и причину из данных Студии: ошибку ноды, step_failure, auto_paused_reason
или статус ноды.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services import launch_risks as lr
from app.services.action_layer.builtin_group import node_label_ru

# Запас на разницу часов: finished_at пишет Студия, launched_at - чат.
_FRESH_SLACK_S = 2.0
_SEEN_KEY = "chain_seen_active"
_ACTIVE = ("queued", "running")
# Текст опроса без своих сведений: «завершилась с ошибкой» уже сказано статусом ноды.
_PLAIN_FAIL = ("", "Студия остановила его с ошибкой")
_STATUS_RU = {
    "pending": "не запускалась",
    "queued": "стоит в очереди и не запущена",
    "running": "ещё выполняется",
    "waiting_hitl": "ждёт решения человека",
    "waiting_approval": "ждёт одобрения трат",
    "failed": "завершилась с ошибкой",
    "skipped": "пропущена (отключена в графе)",
    "done": "готова, но не в этом запуске",
}


def chain_scope(chain: list[str], stop: str | None) -> list[str]:
    """Ноды цепочки от запускаемой до ноды-остановки включительно (без неё - вся цепочка)."""
    if stop and stop in chain:
        return chain[: chain.index(stop) + 1]
    return list(chain)


def runs_by_key(runs: list[dict] | None) -> dict[str, dict]:
    return {str(r.get("node_key")): r for r in runs or [] if isinstance(r, dict) and r.get("node_key")}


def _epoch(value: Any) -> float | None:
    """finished_at Студии: datetime.utcnow() в ISO без зоны - считаем UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def observe(started: dict | None, runs: list[dict] | None) -> None:
    """Запомнить ноды, которые в этом запуске были в очереди или в работе."""
    if not isinstance(started, dict):
        return
    seen = set(started.get(_SEEN_KEY) or [])
    seen.update(
        str(r["node_key"])
        for r in runs or []
        if isinstance(r, dict) and r.get("node_key") and r.get("status") in _ACTIVE
    )
    started[_SEEN_KEY] = sorted(seen)


def forget(started: dict | None) -> None:
    if isinstance(started, dict):
        started.pop(_SEEN_KEY, None)


def _fresh(run: dict | None, started: dict | None) -> bool:
    """Нода завершилась в этом запуске: время завершения не раньше запуска или её видели в работе."""
    if not isinstance(run, dict):
        return False
    started = started if isinstance(started, dict) else {}
    if str(run.get("node_key")) in set(started.get(_SEEN_KEY) or []):
        return True
    launched, finished = started.get("launched_at"), _epoch(run.get("finished_at"))
    if not isinstance(launched, (int, float)) or finished is None:
        return False
    return finished >= float(launched) - _FRESH_SLACK_S


def fresh_done(run: dict | None, started: dict | None) -> bool:
    return isinstance(run, dict) and run.get("status") == "done" and _fresh(run, started)


def fresh_failed(run: dict | None, started: dict | None) -> bool:
    return isinstance(run, dict) and run.get("status") == "failed" and _fresh(run, started)


def downstream_keys(meta: Any, stop: str) -> set[str]:
    """Ноды, до которых по стрелкам холста можно дойти от ноды-остановки (без неё самой)."""
    from app.services.canvas_graph import canvas_graph_from_meta

    cg = canvas_graph_from_meta(meta if isinstance(meta, dict) else None) or {}
    nxt: dict[str, list[str]] = {}
    for e in cg.get("edges") or []:
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            nxt.setdefault(str(e["source"]), []).append(str(e["target"]))
    seen: set[str] = set()
    todo = list(nxt.get(stop, []))
    while todo:
        key = todo.pop()
        if key in seen or key == stop:
            continue
        seen.add(key)
        todo.extend(nxt.get(key, []))
    return seen


def own_check_loop(meta: Any, stop: str) -> bool:
    """Идёт цикл исправлений ноды-проверки stop: её fail -> патч -> переген -> снова она.

    Студия запускает его до стоп-ключа (enrich_xlsx), он часть работы самой проверки и
    ограничен regen_budget. Маркер - узел возврата цикла в meta (vision_check_loop).
    """
    meta = meta if isinstance(meta, dict) else {}
    back = str(meta.get("vision_check_return_node") or meta.get("hero_check_return_node") or "")
    return bool(stop) and back.strip() == stop


def _active_since_launch(run: dict, started: dict | None) -> bool:
    """running/queued строка ушла в работу в этом запуске: её время не раньше запуска.

    Залипшая с прошлых прогонов строка (её Студия сама потом снимает) так не считается.
    """
    launched = (started or {}).get("launched_at") if isinstance(started, dict) else None
    moved = _epoch(run.get("updated_at")) or _epoch(run.get("started_at"))
    if not isinstance(launched, (int, float)) or moved is None:
        return False
    return moved >= float(launched) - _FRESH_SLACK_S


def _running_other_step(project_status: Any, meta: Any, after: set[str]) -> str | None:
    """Проект идёт шагом, который не нода «Работа с GPT» (герои, промты…): ключ его ноды,
    если такая нода стоит после ноды-остановки."""
    from app.models import ProjectStatus
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.services.excel_gpt_node import slot_from_running_status

    try:
        status = ProjectStatus(str(getattr(project_status, "value", project_status)))
    except ValueError:
        return None
    if slot_from_running_status(status) is not None:
        return None
    node_type = RUNNING_TO_NODE_TYPE.get(status)
    if not node_type:
        return None
    from app.services.action_layer import builtin_insight as bi

    nodes = bi._canvas_nodes(meta) if isinstance(meta, dict) else []
    return next((str(n["id"]) for n in nodes if n.get("type") == node_type and str(n["id"]) in after), None)


def overrun_node(
    stop: str, runs: list[dict] | None, started: dict | None, project_status: Any, meta: Any = None
) -> str | None:
    """Ключ ноды, которую Студия запустила после готовой ноды-остановки, иначе None.

    Нужно всё сразу: нода-остановка готова в этом запуске, проект в running-статусе и
    идёт нода, стоящая после неё по стрелкам: её строка ушла в работу в этом запуске или
    проект идёт шагом такой ноды. Повтор самой ноды-остановки и цикл исправлений
    ноды-проверки - не выход за точку остановки.
    """
    if not stop or not lr.is_running(project_status):
        return None
    by_key = runs_by_key(runs)
    if not fresh_done(by_key.get(stop), started):
        return None
    if own_check_loop(meta, stop):
        return None
    after = downstream_keys(meta, stop)
    for key, run in by_key.items():
        if key in after and run.get("status") in _ACTIVE and _active_since_launch(run, started):
            return key
    return _running_other_step(project_status, meta, after)


def failed_in_scope(scope: list[str], runs: list[dict] | None, started: dict | None) -> str | None:
    """Первая нода цепочки, упавшая в этом запуске."""
    by_key = runs_by_key(runs)
    for key in scope:
        if fresh_failed(by_key.get(key), started):
            return key
    return None


def _clip(text: Any, n: int = 400) -> str:
    return " ".join(str(text or "").split())[:n]


def studio_reason(meta: Any) -> str:
    """Причина остановки из meta Студии (step_failure / auto_paused_reason), без префикса."""
    meta = meta if isinstance(meta, dict) else {}
    fs = meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else {}
    return _clip(fs.get("last_error") or meta.get("auto_paused_reason"))


def where_stopped(
    scope: list[str], runs: list[dict] | None, started: dict | None, meta: Any
) -> tuple[str, str]:
    """Место остановки: («последняя готова …, встала на …», ошибка этой ноды).

    Пустой текст - по нодам Студии место не видно: все ноды цепочки готовы в этом запуске
    или их нет в ответе.
    """
    by_key = runs_by_key(runs)
    if not scope or not any(key in by_key for key in scope):
        return "", ""
    last_done: str | None = None
    stuck: str | None = None
    for key in scope:
        if fresh_done(by_key.get(key), started):
            last_done = key
            continue
        stuck = key
        break
    if stuck is None:
        return "", ""
    run = by_key.get(stuck) or {}
    status = str(run.get("status") or "")
    state = _STATUS_RU.get(status, f"в статусе «{status}»" if status else "не найдена в прогоне Студии")
    if status == "failed" and not _fresh(run, started):
        state = "завершилась с ошибкой ещё до этого запуска"
    if last_done:
        text = (
            f"последней готова нода «{node_label_ru(meta, last_done)}», "
            f"цепочка встала на ноде «{node_label_ru(meta, stuck)}» ({stuck}): {state}"
        )
    else:
        text = (
            f"не готова ни одна нода цепочки, встала на первой «{node_label_ru(meta, stuck)}» "
            f"({stuck}): {state}"
        )
    return text, (_clip(run.get("error")) if status == "failed" else "")


def failure_text(
    scope: list[str], runs: list[dict] | None, started: dict | None, meta: Any, studio_error: str
) -> str | None:
    """Ошибка цепочки с местом остановки, без «шаг не выполнен: » впереди; None - места не видно.

    Причина: ошибка самой ноды и текст опроса (там пауза, откат или step_failure Студии),
    без повторов; иначе step_failure / auto_paused_reason из meta.
    """
    where, node_error = where_stopped(scope, runs, started, meta)
    if not where:
        return None
    studio_error = "" if studio_error.strip() in _PLAIN_FAIL else studio_error.strip()
    if node_error and studio_error:
        if studio_error in node_error:
            return f"{where}. Причина: {node_error}"
        if node_error in studio_error:
            return f"{where}; {studio_error}"
        return f"{where}. Ошибка ноды: {node_error}; {studio_error}"
    if node_error:
        return f"{where}. Причина: {node_error}"
    if studio_error:
        return f"{where}; {studio_error}"
    reason = studio_reason(meta)
    return f"{where}. Причина: {reason}" if reason else where


def early_text(scope: list[str], runs: list[dict] | None, started: dict | None, meta: Any) -> str:
    """Почему цепочка встала раньше ноды-остановки: место и причина из Студии, или пусто."""
    where, node_error = where_stopped(scope, runs, started, meta)
    if not where:
        return ""
    reason = node_error or studio_reason(meta)
    return where + (f". Причина: {reason}" if reason else "")


def overrun_text(meta: Any, stop: str, ran: str, stopped: bool) -> str:
    """Итог для случая, когда Студия пошла дальше ноды-остановки."""
    stop_ru, ran_ru = node_label_ru(meta, stop), node_label_ru(meta, ran)
    if stopped:
        return (
            f"Студия пошла дальше ноды «{stop_ru}» и запустила «{ran_ru}» ({ran}): чат "
            "остановил проект, как кнопкой «Стоп». Частичный результат ноды "
            f"«{ran_ru}» мог остаться, дальше проект сам не пойдёт"
        )
    return (
        f"Студия пошла дальше ноды «{stop_ru}» и запустила «{ran_ru}» ({ran}), "
        "остановить проект не удалось: нажмите «Стоп» в Студии"
    )
