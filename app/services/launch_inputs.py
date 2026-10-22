from __future__ import annotations

from typing import Any

FRAME_STEPS: dict[str, str] = {
    "img_pr": "промты картинок",
    "img": "картинки",
    "anim_pr": "промты анимации",
    "video": "видео",
}
LAUNCH_ACTIONS = frozenset({"step.run_step"})


def needs_frames(step_code: Any) -> bool:
    return str(step_code or "") in FRAME_STEPS


def no_frames_text(step_code: Any) -> str:
    what = FRAME_STEPS.get(str(step_code or ""), "этот шаг")
    return f"Кадров нет — {what} делать не из чего, сначала разбивка на кадры (или импорт Excel)."


def board_frame_count(board: Any) -> int | None:
    frames = board.get("frames") if isinstance(board, dict) else None
    if not isinstance(frames, list):
        return None
    return sum(1 for frame in frames if isinstance(frame, dict))


async def project_frames(project_id: int) -> int | None:
    """Сколько кадров у проекта в базе; None — проекта нет или база не ответила."""
    from app.services.action_layer.builtin_insight import _frame_count, _project_row, _sessions

    if await _project_row(project_id) is None:
        return None
    return await _frame_count(_sessions(), project_id)


def _pid(raw: Any, fallback: int | None) -> int | None:
    value = raw if raw is not None else fallback
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


async def plan_refusal(plan: list[Any], project_id: int | None, *, frames=project_frames) -> str | None:
    for step in plan or []:
        if getattr(step, "action", None) not in LAUNCH_ACTIONS:
            continue
        params = getattr(step, "params", None) or {}
        code = params.get("step_code")
        if not needs_frames(code):
            continue
        pid = _pid(params.get("project_id"), project_id)
        if pid is None:
            continue
        if await frames(pid) == 0:
            return no_frames_text(code)
    return None
