"""Правка настроек без личности: plan_resolve отказывает — плана и карточки нет, шаг не идёт.

Раньше запасной путь `changed_settings_only` отдавал исходные параметры, и на карточку
шли ключи, которых человек не просил. Вызовы модели — заглушки.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_supervisor_turns_resolve_refusal_into_answer(token: str) -> None:
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.orchestrator.contracts import PlanResolveRefused, PlanStep, TaskStatus
    from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

    del token

    class _Refusing(MockActions):
        async def plan_resolve(self, name, params):
            raise PlanResolveRefused("Не могу прочитать настройки проекта: запрос пришёл без входа в студию.")

    plan = [PlanStep(action="project.patch_settings", params={"project_id": 1, "title": "x"}, mutating=True)]
    actions = _Refusing(risks={"project.patch_settings": "low"})
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(plan),
        critic=ScriptedCritic(["approve"] * 3),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("refuse-settings", "переименуй проект 1 в x")
        await rt.supervisor.wait(task_id, timeout=5)
        row = rt.tasks.get(task_id)
        assert row["status"] != TaskStatus.waiting_approval.value and row["plan_json"] == "[]"
        answers = [e.payload.get("text") for e in rt.bus.replay("refuse-settings", 0) if e.type == "message"]
        assert any("без входа в студию" in str(t) for t in answers)
        assert [c for c in actions.calls if c["name"] == "project.patch_settings"] == []
    finally:
        set_runtime(None)
