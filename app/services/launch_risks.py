from __future__ import annotations

from collections.abc import Iterable
from typing import Any

EARLY_STEPS: tuple[str, ...] = ("plan", "script", "split")
EXCEL_GPT_TITLE = "Работа с GPT"

RISK_RESET = "reset_downstream"
RISK_BUSY = "busy"
RISK_USER_STOP = "user_stop"
RISK_AUTO_CHAIN = "auto_chain"
RISK_NO_FRAMES = "no_frames"
STOP_HINT = "Остановить: кнопка «Стоп задачи» или аварийный стоп в «Регистраторе»."


def step_title(step_code: str) -> str:
    from app.orchestrator.pipeline_steps import step_by_code

    if step_code == "excel_gpt":
        return EXCEL_GPT_TITLE
    step = step_by_code(step_code)
    return step.title if step is not None else step_code


def _node_title(node_type: str) -> str:
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE

    if node_type == "excel_gpt":
        return EXCEL_GPT_TITLE
    code = NODE_TYPE_TO_STEP_CODE.get(node_type)
    return step_title(code) if code else node_type


def downstream_ready(step_code: str, node_runs: Iterable[dict]) -> list[str]:
    from app.orchestrator.node_registry import STEP_CODE_TO_NODE_TYPE
    from app.services.run_sync import NODE_TYPE_ORDER

    if step_code not in EARLY_STEPS:
        return []
    start = NODE_TYPE_ORDER.index(STEP_CODE_TO_NODE_TYPE[step_code])
    titles: list[str] = []
    for nr in node_runs or []:
        if not isinstance(nr, dict) or str(nr.get("status") or "") != "done":
            continue
        node_type = str(nr.get("node_type") or "")
        below = node_type == "excel_gpt" or (
            node_type in NODE_TYPE_ORDER and NODE_TYPE_ORDER.index(node_type) > start
        )
        if below:
            title = _node_title(node_type)
            if title not in titles:
                titles.append(title)
    return titles


def user_stopped(meta: Any) -> bool:
    meta = meta if isinstance(meta, dict) else {}
    return meta.get("user_stop") is not None or meta.get("mass_lane_user_stop") is not None


def is_running(status: Any) -> bool:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status

    try:
        return is_running_status(ProjectStatus(getattr(status, "value", status)))
    except ValueError:
        return False


def launch_risks(
    *,
    step_code: str,
    status: Any,
    auto_mode: bool,
    meta: Any,
    node_runs: Iterable[dict],
    frames: int | None = None,
) -> list[dict]:
    from app.services.launch_inputs import needs_frames, no_frames_text

    title = step_title(step_code)
    out: list[dict] = []
    if frames == 0 and needs_frames(step_code):
        out.append({"code": RISK_NO_FRAMES, "text": no_frames_text(step_code)})
    ready = downstream_ready(step_code, node_runs)
    if ready:
        out.append(
            {
                "code": RISK_RESET,
                "text": (
                    f"Шаг «{title}» сбросит готовые ноды ниже по цепочке ({', '.join(ready)}): их результат "
                    "пропадёт, и повторный прогон этих нод будет оплачен заново."
                ),
            }
        )
    if is_running(status):
        out.append(
            {
                "code": RISK_BUSY,
                "text": (
                    f"В проекте сейчас идёт другой шаг (статус {getattr(status, 'value', status)}): запуск "
                    "прервёт его, уже потраченное на него не вернётся."
                ),
            }
        )
    if user_stopped(meta):
        out.append(
            {
                "code": RISK_USER_STOP,
                "text": ("Проект остановлен человеком кнопкой «Стоп»: запуск снимет эту остановку."),
            }
        )
    if auto_mode:
        out.append(
            {
                "code": RISK_AUTO_CHAIN,
                "text": (
                    "Включено автопродвижение: после этого шага воркер сам запустит все следующие шаги, "
                    "в том числе платные."
                ),
            }
        )
    return out


def node_runs_as_dicts(node_runs: Iterable[Any]) -> list[dict]:
    rows: list[dict] = []
    for nr in node_runs or []:
        status = getattr(nr, "status", None)
        rows.append(
            {
                "node_key": getattr(nr, "node_key", None),
                "node_type": getattr(nr, "node_type", None),
                "status": getattr(status, "value", status),
            }
        )
    return rows
