"""diag.content_checks: сходится ли результат шага по содержанию — для чата.

«Проверь результат разбивки», «всё ли сходится в кадрах», «проверь промты картинок»:
проверки ``content_checks`` по текущим данным проекта (склейка закадра кадров =
закадровый текст, число кадров, разбивка без модели, герои cNN в промтах картинок) и
последний отчёт, записанный воркером после шага. Без LLM. Только чтение: правки — из
``fix_options`` отдельными действиями.
"""

from __future__ import annotations

from typing import Any

from app.services import content_checks as cc
from app.services.action_layer import builtin_insight as bi
from app.services.action_layer.builtin_group import _PID
from app.services.action_layer.builtin_montage import _PRE_PROJECT
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import RISK_READ, ActionSpec

ACTION_ID = "diag.content_checks"
_STEP = {"type": "string", "enum": list(cc.STEPS)}


async def _load(pid: int, steps: list[str] | None) -> dict[str, Any]:
    """Отчёты проверок по свежим данным и сохранённые после шагов."""
    from sqlalchemy import func, select

    from app.models import Frame, Project

    async with bi._sessions()() as s:
        project = await s.get(Project, pid)
        if project is None:
            return {"found": False}
        if steps is None:
            prompts = await s.scalar(
                select(func.count())
                .select_from(Frame)
                .where(Frame.project_id == pid, Frame.image_prompt.is_not(None), Frame.image_prompt != "")
            )
            # Склейка закадра входит в проверку разбивки; промты — если они уже есть.
            steps = ["split", *(["img_pr"] if prompts else [])]
        reports = [
            await cc.run_checks(s, project, step, with_vo=step == "split" or "split" not in steps)
            for step in steps
        ]
        return {
            "found": True,
            "reports": reports,
            "stored": cc.stored_reports(project.meta if isinstance(project.meta, dict) else {}),
        }


async def _diag_content_checks(ctx: ActionContext, params: dict) -> dict:
    pid = int(params["project_id"])
    step = str(params.get("step") or "").strip() or None
    data = await _load(pid, [step] if step else None)
    if not data.get("found"):
        return {"project_id": pid, "found": False, "text_ru": f"Проект #{pid} не найден."}
    reports = data["reports"]
    frames = max((int(r.get("frames") or 0) for r in reports), default=0)
    if not frames:
        return {
            "project_id": pid,
            "found": True,
            "clean": True,
            "reports": reports,
            "fix_options": [],
            "text_ru": f"В проекте #{pid} ещё нет кадров — проверять нечего.",
        }
    lines: list[str] = []
    fixes: list[dict] = []
    for report in reports:
        lines.append(cc.summary_text(report))
        fixes += (
            [f for f in cc.fix_options(report, pid) if f.get("action") != ACTION_ID]
            if not report["ok"]
            else []
        )
    stored = data.get("stored") or {}
    for report in reports:
        last = stored.get(report["step"])
        if last and last.get("at"):
            verdict = "без расхождений" if last.get("ok") else f"находок {len(last.get('findings') or [])}"
            lines.append(
                f"После шага «{cc.STEP_RU.get(report['step'])}» ({last['at']}) проверка дала: {verdict}."
            )
    usable = [f for f in fixes if f.get("phrase_ru")]
    if fixes:
        lines.append(
            "Что можно сделать: "
            + "; ".join(
                f["title_ru"] + (f" — «{bi._clip(f['phrase_ru'], 160)}»" if f.get("phrase_ru") else "")
                for f in fixes[:8]
            )
            + "."
        )
    out = {
        "project_id": pid,
        "found": True,
        # Не «ok»: слой действий читает ok=False как отказ действия, а находки — не отказ.
        "clean": all(r["ok"] for r in reports),
        "reports": reports,
        "fix_options": fixes,
        "suggested_phrase": usable[0]["phrase_ru"] if usable else None,
        "text_ru": " ".join(lines),
    }
    return bi._safe(out)


def content_checks_spec() -> ActionSpec:
    return ActionSpec(
        id=ACTION_ID,
        title_ru="Проверка результата по содержанию",
        description_ru="Сходится ли результат шага по смыслу, а не только по формату: склейка "
        "закадра всех кадров = закадровый текст (нет потерь, дублей, пересказа), число кадров "
        "как просили в сценарии, разбивка не локальная без модели, герои cNN в промтах "
        "картинок есть в реестре и совпадают с персонажами кадра. step: split (разбивка), "
        "enrich (после «Работы с GPT»), img_pr (промты картинок); без step — разбивка и, если "
        "есть, промты. Плюс итог проверки после шага и варианты исправления существующими "
        "действиями. Без LLM, только чтение.",
        params_schema=bi._schema({"project_id": _PID, "step": _STEP}, ["project_id"]),
        executor=_diag_content_checks,
        risk=RISK_READ,
        idempotent=True,
        preconditions=(_PRE_PROJECT,),
        tags=(
            "diag",
            "check",
            "content",
            "проверь",
            "проверка",
            "сходится",
            "разбивка",
            "кадры",
            "закадр",
            "промты",
            "картинок",
            "результат",
        ),
    )
