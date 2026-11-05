"""Голос озвучки из чата: вопрос — факт, «поменяй голос на мужской» — audio.set_voice."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.style_scope import voice_plan
from app.services.action_layer.builtin_voice import current_voice_id, pick_voice, voice_text

ADAM = "pNInz6obpgDQGcFmaJgB"
SARAH = "EXAVITQu4vr4xnSDxMaL"


def test_pick_voice_by_gender_and_name() -> None:
    assert pick_voice("поменяй голос на мужской", ADAM)["id"] != ADAM
    assert pick_voice("поменяй голос на мужской", SARAH)["id"] == ADAM
    assert pick_voice("поставь голос сара", ADAM)["id"] == SARAH
    assert pick_voice("женский голос", ADAM)["gender"] == "женский"
    assert pick_voice("сделай голос получше", ADAM) is None


def test_voice_text_names_current() -> None:
    meta = {"node_step_params": {"audio": {"elevenlabs_voice_id": SARAH}}}
    assert current_voice_id(meta) == SARAH
    assert "Сара" in voice_text(meta).split(".")[0]
    assert "Адам" in voice_text({}).split(".")[0]


@pytest.mark.parametrize(
    "text, ok",
    [("поменяй голос на мужской", True), ("поставь голос Сара в проекте 4", True), ("какой голос в озвучке", False)],
)
def test_voice_plan(text: str, ok: bool) -> None:
    plan = voice_plan(text, 4)
    assert (plan is not None) is ok
    if plan:
        assert plan[0].action == "audio.set_voice" and plan[0].params["project_id"] == 4


def test_new_question_is_not_a_clarification_answer() -> None:
    from app.orchestrator.queue import answered_request

    prev = [("assistant", "Уточните, нужно ли загрузить готовый файл?"), ("user", "поменяй голос на мужской")]
    assert answered_request(prev, "какой голос в озвучке") is None
    assert answered_request(prev, "мужской") is not None


def test_delete_project_hint() -> None:
    from app.orchestrator.agents.summarizer import DELETE_PROJECT_HINT, missing_action_text

    assert DELETE_PROJECT_HINT in missing_action_text("Удаление проектов недоступно действий", "удали проект")
