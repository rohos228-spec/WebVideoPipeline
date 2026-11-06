"""Составные просьбы: каждая часть доходит до плана, порядок задаёт работа, а не слова."""

from __future__ import annotations

import pytest

from app.orchestrator.agents import action_catalog, compound_scope as cs, music_scope, style_scope
from app.orchestrator.agents.frame_scope import frame_numbers, image_frame_plan
from app.orchestrator.agents.read_plan import read_step
from app.orchestrator.agents.supervisor import Supervisor, _ACT_NOW_RE, _QUESTION_ASK_RE, _is_compound


class _Rules:
    _shortcut_plan = Supervisor._shortcut_plan
    _one_shortcut = Supervisor._one_shortcut

    def _task_project_id(self, _task):
        return 10

    def _launch_allowed(self, _action):
        return True


@pytest.fixture
def rules(monkeypatch):
    async def node(_pid, typ):
        return {"image_prompts": "img_prompts_1", "script": "script_1", "music": "music_1"}.get(typ)

    async def image_node(_pid):
        return "img_prompts_1"

    async def level(_pid):
        return 30

    monkeypatch.setattr(style_scope, "_node_of_type", node)
    monkeypatch.setattr(style_scope, "_image_prompts_node", image_node)
    monkeypatch.setattr(music_scope, "_current_level", level)
    monkeypatch.setattr(action_catalog, "_studio_projects", lambda _task: [(i, "") for i in range(1, 40)])
    return _Rules()


def _actions(plan):
    return [step.action for step in plan or []]


def _ops(step):
    return [(op["type"], op["frame_number"]) for op in step.params["ops"]]


async def test_several_frames_wish_and_rebuild(rules):
    plan = await rules._shortcut_plan(1, "перерисуй кадры 2, 5 и 7, сделай их ночными и потом пересобери ролик")
    assert _actions(plan) == ["montage.queue_ops", "montage.apply_queue", "montage.run_montage"]
    assert _ops(plan[0]) == [("image_regen_correction", n) for n in (2, 5, 7)]
    assert plan[0].params["ops"][0]["prompt"] == "сделай ночными"
    assert plan[1].params["frame_numbers"] == [2, 5, 7]
    assert (plan[1].params["max_image_ops"], plan[1].params["max_video_ops"]) == (3, 0)


@pytest.mark.parametrize(
    "phrase",
    [
        "перерисуй кадр 2, а нет, лучше кадр 3",
        "перерисуй кадр 2, нет, кадр 3",
        "перерисуй кадр 3, а не кадр 2",
        "перерисуй не кадр 2, а кадр 3",
    ],
)
async def test_changed_mind_takes_the_last_frame(rules, phrase):
    plan = await rules._shortcut_plan(1, phrase)
    assert _ops(plan[0]) == [("image_regen", 3)]


async def test_changed_mind_without_number_goes_to_planner(rules):
    assert await rules._shortcut_plan(1, "сделай музыку тише, а нет, громче") is None


async def test_swap_and_redraw_keep_both_parts(rules):
    plan = await rules._shortcut_plan(1, "поменяй местами кадры 3 и 4, а кадр 6 перерисуй")
    assert _actions(plan) == ["montage.swap_slots", "montage.queue_ops"]
    assert _ops(plan[1]) == [("image_regen", 6)]


@pytest.mark.parametrize(
    "phrase",
    ["поменяй голос на женский, музыку сделай потише и собери заново", "собери заново, поменяй голос на женский"],
)
async def test_new_voice_is_recorded_before_montage(rules, phrase):
    plan = await rules._shortcut_plan(1, phrase)
    actions = _actions(plan)
    assert actions[-1] == "montage.run_montage"
    assert actions.index("audio.set_voice") < actions.index("step.run_step") < actions.index("montage.run_montage")
    audio = next(step for step in plan if step.action == "step.run_step")
    assert audio.params["step_code"] == "audio"


async def test_style_then_images_runs_prompts_first(rules):
    plan = await rules._shortcut_plan(1, "сделай стиль аниме для всех кадров и перезапусти картинки")
    assert _actions(plan) == ["project.status", "prompt.edit_text", "step.run_step", "step.run_step"]
    assert [step.params["step_code"] for step in plan[2:]] == ["img_pr", "img"]


async def test_part_without_rule_sends_whole_ask_to_planner(rules):
    goal = "подними бюджет текстов, перезапусти сценарий и потом всё до картинок"
    assert await rules._shortcut_plan(1, goal) is None
    assert _is_compound(goal)
    assert "подними бюджет текстов" in cs.compound_note(cs.split_clauses(goal))


async def test_named_project_applies_to_every_part(rules):
    plan = await rules._shortcut_plan(1, "в проекте 5 поменяй голос на женский и собери заново")
    assert {step.params["project_id"] for step in plan} == {5}


async def test_second_part_without_verb_is_kept(rules):
    plan = await rules._shortcut_plan(1, "музыку тише и голос мужской")
    assert _actions(plan) == ["montage.set_assemble_params", "audio.set_voice"]


@pytest.mark.parametrize(
    ("phrase", "parts"),
    [
        ("кит на 2 кадре кривой, перегенери", 1),
        ("сделай, чтобы во всех картинках был пластилин", 1),
        ("запусти проекты 5, 6 и 7 до видео, но не больше двух одновременно", 1),
        ("поменяй промт кадра 3 на «кот, и собери ролик»", 1),
        ("почему упал кадр 2 и что делать?", 1),
        ("как поменять голос и собрать заново", 1),
        ("подними бюджет до 1,5 доллара", 1),
        ("переделай кадр 2, убери кровь", 1),
        ("поставь голос Сара, потом перемонтируй", 2),
        ("разберись почему упало, почини и перезапусти", 3),
    ],
)
def test_split_keeps_one_ask_whole(phrase, parts):
    assert len(cs.split_clauses(phrase)) == parts


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("кадры 2, 5 и 7", [2, 5, 7]),
        ("кадры со 2 по 4", [2, 3, 4]),
        ("кадры 2-4", [2, 3, 4]),
        ("второй и третий кадр", [2, 3]),
        ("кадр 2 и кадр 5", [2, 5]),
        ("кадр 4", [4]),
    ],
)
def test_frame_numbers(text, numbers):
    assert frame_numbers(text) == numbers


def test_plural_card_text():
    plan = image_frame_plan("перерисуй кадры 2 и 3", 10)
    assert plan[0].expected.startswith("Картинки кадров 2, 3 поставлены")


def test_question_with_fix_order_may_act():
    goal = "разберись почему упал ИИ-редактор сцен, почини и перезапусти"
    assert _QUESTION_ASK_RE.search(goal) and _ACT_NOW_RE.search(goal)
    assert not _ACT_NOW_RE.search("как починить ноду")


@pytest.mark.parametrize(
    "phrase", ["где сейчас ошибки по проектам", "какие проекты упали", "покажи проблемы во всех проектах"]
)
def test_trouble_over_all_projects_reads_overview(phrase):
    step = read_step(phrase)
    assert step["action"] == "projects.overview"
    assert step["params"]["status"] == "attention"


@pytest.mark.parametrize("phrase", ["почини упавшие проекты", "почему проекты упали", "ошибки проекта 5"])
def test_action_or_one_project_is_not_overview(phrase):
    assert read_step(phrase) is None


def test_critic_context_keeps_only_current_ask():
    from app.orchestrator.agents.supervisor import _critic_context

    context = (
        "Проект #11.\nПоследние реплики этого чата — короткая просьба продолжает их:\n"
        "Человек: поменяй голос на женский\nЧат: Сделаю.\nДругая строка"
    )
    out = _critic_context(context, "музыку тише и голос мужской")
    assert "женский" not in out and "Другая строка" in out and "голос мужской" in out


def test_failure_cause_reads_error_list():
    from app.orchestrator.agents.supervisor import _failure_cause_of
    from app.orchestrator.contracts import PlanStep

    step = PlanStep(action="diag.node_failure", params={"project_id": 11}, expected="x")
    result = {"status": "ok", "result": {"title": "Работа с GPT 1", "errors": ["project_file: модель не вернула apply-ops JSON"]}}
    assert _failure_cause_of(step, result) == "Нода «Работа с GPT 1». Ошибка: project_file: модель не вернула apply-ops JSON"


def test_apply_queue_counts_stray_ops_of_selected_frames():
    from app.orchestrator.actions.executor import _queued_counts

    meta = {"montage_board": {"pending_ops": [{"type": "video_regen", "frame_number": 1}, {"type": "image_regen", "frame_number": 9}]}}
    out = _queued_counts({"frame_numbers": [1, 2], "max_image_ops": 2, "max_video_ops": 0}, meta)
    assert (out["max_image_ops"], out["max_video_ops"]) == (2, 1)
    assert _queued_counts({"max_image_ops": 1, "max_video_ops": 0}, None)["max_video_ops"] == 0


async def test_apply_queue_phrase_reads_the_board(monkeypatch):
    from app.orchestrator.agents import frame_scope as fs

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalar(self, _q):
            return {"montage_board": {"pending_ops": [{"type": "image_regen", "frame_number": 2}, {"type": "video_regen", "frame_number": 2}]}}

    from app.services.action_layer import store

    monkeypatch.setattr(store, "sessions", lambda: (lambda: _S()))
    plan = await fs.apply_queue_plan("примени очередь", 5)
    assert plan[0].action == "montage.apply_queue"
    assert plan[0].params == {"project_id": 5, "frame_numbers": [2], "max_image_ops": 1, "max_video_ops": 1}
    assert await fs.apply_queue_plan("примени правку промта", 5) is None


def test_apply_after_queue_in_same_plan_declares_added_ops():
    from app.orchestrator.contracts import PlanStep

    plan = [
        PlanStep(action="montage.queue_ops", params={"project_id": 10, "ops": [{"type": "image_regen", "frame_number": 1}, {"type": "image_regen", "frame_number": 2}]}, expected="x"),
        PlanStep(action="montage.apply_queue", params={"project_id": 10, "frame_numbers": [1, 2], "max_image_ops": 0, "max_video_ops": 0}, expected="x"),
    ]
    out = cs.sync_apply_counts(plan)
    assert (out[1].params["max_image_ops"], out[1].params["max_video_ops"]) == (2, 0)


def test_apply_frames_include_newly_queued_frame():
    from app.orchestrator.contracts import PlanStep

    plan = [
        PlanStep(action="montage.queue_ops", params={"project_id": 10, "ops": [{"type": "video_regen", "frame_number": 2}]}, expected="x"),
        PlanStep(action="montage.apply_queue", params={"project_id": 10, "frame_numbers": [1], "max_image_ops": 0, "max_video_ops": 1}, expected="x"),
    ]
    out = cs.sync_apply_counts(plan)
    assert out[1].params["frame_numbers"] == [1, 2] and out[1].params["max_video_ops"] == 2
