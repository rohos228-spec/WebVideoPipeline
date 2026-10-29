"""diag.node_failure: разбор упавшей ноды до причины по файлам на диске, без LLM."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from app.services.action_layer import builtin_insight as bi
from app.services.action_layer import node_failure as nf

_UNKNOWN = (
    "# contract: vp_apply_ops\n"
    "# kind: validate\n"
    "# error: схема vp_apply_ops нарушена:\n"
    "- ops.19: Value error, кадр 653cdd43: неизвестные поля ['описание']. Канонические имена: "
    "['accent', 'shot01_description'] (русские синонимы тоже принимаются)\n"
    "- ops.20: Value error, кадр 38ac0e27: неизвестные поля ['описание']. Канонические имена: ['accent']\n"
    "# reply_len: 80\n\n"
    '{"ops": [{"frame_uuid": "653cdd43", "fields": {"описание": "ёж идёт"}}], "report": "ок"}'
)
_EMPTY = (
    "# contract: vp_apply_ops\n"
    "# kind: validate\n"
    "# error: схема vp_apply_ops нарушена:\n"
    "- Value error, пустой ops — нечего применять\n"
    "# reply_len: 60\n\n"
    '{"ops": [], "report": "Все поля уже заполнены, править нечего."}'
)

_META = {
    "canvas_graph": {
        "nodes": [
            {"id": "n_split", "type": "split", "data": {"label": "Разбивка"}},
            {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
        ],
        "edges": [{"source": "n_split", "target": "n_excel_gpt_1"}],
    }
}


def _project_dir(tmp_path, files: dict[str, str], frames: int | None = 3):
    rejects = tmp_path / "llm_rejects"
    rejects.mkdir()
    for i, (name, text) in enumerate(files.items()):
        path = rejects / name
        path.write_text(text, encoding="utf-8")
        stamp = time.time() - 60 + i
        os.utime(path, (stamp, stamp))
    if frames is not None:
        up = tmp_path / "excel_gpt_uploads" / "n_excel_gpt_1"
        up.mkdir(parents=True)
        (up / "db_frames.json").write_text(
            json.dumps({"frames": [{"uuid": str(i)} for i in range(frames)]}), encoding="utf-8"
        )
    return tmp_path


def test_parse_reject_keeps_multiline_error_and_reply():
    got = nf.parse_reject_text(_UNKNOWN)
    assert got["contract"] == "vp_apply_ops" and got["kind"] == "validate"
    assert "ops.20" in got["error"] and got["error"].startswith("схема")
    assert got["reply"].startswith('{"ops"')


def test_unknown_fields_cause_and_fixes(tmp_path):
    root = _project_dir(tmp_path, {"apply_ops_n_excel_gpt_1_a1_validate.txt": _UNKNOWN})
    rejects = nf.read_rejects(root, "n_excel_gpt_1")
    assert len(rejects) == 1 and rejects[0]["attempt"] == 1
    cause = nf.classify(errors=["схема отклонена 3 раза"], rejects=rejects, frames=3, is_check=False)
    assert cause["code"] == "unknown_fields"
    assert cause["unknown_fields"] == ["описание"]
    fixes = nf.fix_options(cause, project_id=26, node_key="n_excel_gpt_1", step_code="excel_gpt")
    actions = [f["action"] for f in fixes]
    assert actions[0] == "prompt.edit_text" and "step.run_step" in actions
    assert "«описание»" in fixes[0]["params"]["append"]
    run = next(f for f in fixes if f["action"] == "step.run_step")
    assert run["params"] == {"project_id": 26, "step_code": "excel_gpt", "node_key": "n_excel_gpt_1"}


def test_empty_ops_cause_with_check_hint(tmp_path):
    root = _project_dir(
        tmp_path,
        {f"apply_ops_n_excel_gpt_1_a{i}_validate.txt": _EMPTY for i in (1, 2, 3)},
    )
    rejects = nf.read_rejects(root, "n_excel_gpt_1")
    assert [r["attempt"] for r in rejects] == [1, 2, 3]
    assert rejects[0]["report"] == "Все поля уже заполнены, править нечего."
    plain = nf.classify(errors=[], rejects=rejects, frames=3, is_check=False)
    assert plain["code"] == "empty_ops" and plain["check_node"] is False
    fixes = nf.fix_options(plain, project_id=26, node_key="n_excel_gpt_1", step_code="excel_gpt")
    assert [f["action"] for f in fixes] == ["prompt.edit_text", "node.set_model", "step.run_step"]
    check = nf.classify(errors=[], rejects=rejects, frames=3, is_check=True)
    assert check["code"] == "empty_ops" and "проверка" in check["explain_ru"]
    check_fixes = nf.fix_options(check, project_id=26, node_key="n_excel_gpt_1", step_code="excel_gpt")
    assert check_fixes[0]["action"] is None and "prompt.edit_text" not in [f["action"] for f in check_fixes]


def test_other_causes():
    assert nf.classify(errors=[], rejects=[], frames=0, is_check=False)["code"] == "empty_input"
    assert nf.classify(errors=["HTTP 503 Service Unavailable"], rejects=[], frames=3, is_check=False)[
        "code"
    ] == ("provider_error")
    cut = [
        {"kind": "parse", "error": "не JSON", "reply": '{"ops": [{"frame_uuid": "1", "fields": {"место": "ле'}
    ]
    assert nf.classify(errors=[], rejects=cut, frames=3, is_check=False)["code"] == "truncated"
    no = [{"kind": "parse", "error": "не JSON", "reply": "Извините, я не могу помочь с этим запросом."}]
    assert nf.classify(errors=[], rejects=no, frames=3, is_check=False)["code"] == "refusal"
    other = [{"kind": "validate", "error": "ops.0: поле frame_uuid обязательно", "reply": '{"ops": [{}]}'}]
    assert nf.classify(errors=[], rejects=other, frames=3, is_check=False)["code"] == "schema_other"
    assert nf.classify(errors=["что-то"], rejects=[], frames=3, is_check=False)["code"] == "unknown"
    # «ops.519» в тексте — номер правки, а не HTTP 5xx.
    assert nf.classify(errors=["ops.519: неизвестные поля ['x']"], rejects=[], frames=3, is_check=False)[
        "code"
    ] == ("unknown_fields")


def test_rejects_of_other_node_and_input_size(tmp_path):
    root = _project_dir(
        tmp_path,
        {
            "apply_ops_n_excel_gpt_10_a1_validate.txt": _UNKNOWN,
            "apply_ops_n_excel_gpt_1_a1_validate.txt": _EMPTY,
        },
        frames=7,
    )
    rejects = nf.read_rejects(root, "n_excel_gpt_1")
    assert [r["file"] for r in rejects] == ["apply_ops_n_excel_gpt_1_a1_validate.txt"]
    assert nf.input_size(root, "n_excel_gpt_1") == {"frames": 7, "batches": 0, "file": "db_frames.json"}


def test_pick_node_prefers_failed_run_then_rejects(tmp_path):
    runs = [
        {"node_key": "n_split", "status": "done"},
        {"node_key": "n_excel_gpt_1", "status": "failed", "finished_at": "2026-10-27T19:00:00"},
    ]
    assert nf.pick_node(_META, runs, None)[0] == "n_excel_gpt_1"
    root = _project_dir(tmp_path, {"apply_ops_n_excel_gpt_1_a1_validate.txt": _EMPTY})
    key, how = nf.pick_node(_META, [], root)
    assert key == "n_excel_gpt_1" and "отклон" in how
    assert nf.pick_node({}, [], tmp_path / "nothing") == (None, "")


class _Ctx:
    async def call(self, method, path, **kw):
        raise AssertionError("в тесте HTTP не нужен")


@pytest.fixture
def wired(monkeypatch, tmp_path):
    root = _project_dir(tmp_path, {f"apply_ops_n_excel_gpt_1_a{i}_validate.txt": _EMPTY for i in (1, 2, 3)})

    async def effective(ctx, pid):
        return {"id": pid, "slug": "ezhi", "meta": _META}

    async def node_runs(ctx, pid):
        return [
            {
                "node_key": "n_excel_gpt_1",
                "node_type": "excel_gpt",
                "status": "failed",
                "attempts": 3,
                "error": "apply-ops: ответ модели отклонён 3 раза: схема vp_apply_ops нарушена — "
                + "x" * 600,
            }
        ]

    async def row(pid):
        return None

    monkeypatch.setattr(nf, "_effective_project", effective)
    monkeypatch.setattr(bi, "_node_runs", node_runs)
    monkeypatch.setattr(bi, "_project_row", row)
    monkeypatch.setattr(bi, "_transient", lambda pid, detail, r: SimpleNamespace(data_dir=root))
    monkeypatch.setattr(bi, "_model_for", lambda *a: "GPT тест")
    monkeypatch.setattr(bi, "_prompt_for", lambda *a: {"variant": "razmetka", "source": "проект"})
    monkeypatch.setattr(nf, "_is_check", lambda meta, key: False)
    return root


def test_action_auto_picks_node_and_explains(wired):
    out = asyncio.run(nf._diag_node_failure(_Ctx(), {"project_id": 26}))
    assert out["found"] and out["node_key"] == "n_excel_gpt_1"
    assert out["cause"]["code"] == "empty_ops"
    assert len(out["rejects"]) == 3 and out["input"]["frames"] == 3
    # Ошибка целиком, а не 300 знаков, как в карточке ноды.
    assert len(out["errors"][0]) > 300
    text = out["text_ru"]
    assert "Причина: модель вернула пустой список правок" in text
    assert "Все поля уже заполнены" in text and "На входе кадров: 3" in text
    assert out["suggested_phrase"].startswith("допиши в промт ноды n_excel_gpt_1 проекта 26")


def test_spec_is_read_only_and_reachable_from_chat():
    from app.orchestrator.actions import metadata_of
    from app.orchestrator.actions.executor import whitelist
    from app.services.action_layer.spec import RISK_READ, ActionRegistry

    reg = ActionRegistry()
    bi.register_insight(reg)
    spec = reg.find("diag.node_failure")
    assert spec is not None and spec.risk == RISK_READ and spec.params_schema["required"] == ["project_id"]
    assert "diag.node_failure" in whitelist()
    meta = metadata_of("diag.node_failure")
    assert meta.risk == "none" and meta.read_only and not meta.paid and not meta.destructive


@pytest.mark.parametrize(
    "text",
    [
        "сейчас нода работала, там ошибка, разберись в чём проблема",
        "найди саму причину",
        "разберись почему упала нода",
        "найди причину ошибки",
        "почему нода сломалась",
        "что не так с нодой",
    ],
)
def test_question_reads_node_failure(text):
    from app.orchestrator.agents.question_context import plan_reads

    reads = plan_reads(text, 26)
    assert ("diag.node_failure", {"project_id": 26}) in reads, reads


def test_question_named_node_and_plain_status():
    from app.orchestrator.agents.question_context import plan_reads

    reads = plan_reads("почему упала n_excel_gpt_2", 26)
    assert ("diag.node_failure", {"project_id": 26, "node_key": "n_excel_gpt_2"}) in reads
    assert "diag.node_failure" not in [a for a, _ in plan_reads("какой статус проекта", 26)]


def test_failure_fact_gets_answer_rule():
    from app.orchestrator.agents.question_context import FAILURE_RULE, Fact, system_prompt

    prompt = system_prompt([Fact("diag.node_failure", "Почему упала нода", "Причина: …")], 26)
    assert FAILURE_RULE in prompt


def test_planner_catalog_always_has_node_failure():
    from app.orchestrator.agents.action_catalog import BASE_READ_IDS
    from app.services.action_layer.planner_rules import ACTION_NOTES

    assert "diag.node_failure" in BASE_READ_IDS
    assert "fix_options" in ACTION_NOTES["diag.node_failure"]


def test_master_prompt_writes_not_from_chat():
    from app.orchestrator.actions import executor

    names = executor.whitelist()
    assert "prompt.save_master" not in names and "prompt.create_master" not in names
    # Чтение мастер-промта и правка промта одной ноды остаются.
    assert "prompt.get_master" in names and "prompt.edit_text" in names
    for name in ("prompt.save_master", "prompt.create_master"):
        res = asyncio.run(executor.execute(name, {"step_code": "script", "content": "x"}, None))
        assert res["status"] == "failed" and "мастер" in res["error"]


_NEW_FORMAT = (
    "# contract: vp_apply_ops\n"
    "# kind: validate\n"
    "# error: схема vp_apply_ops нарушена:\n"
    "- Value error, пустой ops — нечего применять\n"
    "# model: gpt-test-mini\n"
    "# input: frames=24 chars=5100\n"
    "# report: кадры уже заполнены\n"
    "# reply_len: 12\n\n"
    '{"ops": []}'
)


def test_new_header_lines_do_not_leak_into_error():
    got = nf.parse_reject_text(_NEW_FORMAT)
    assert got["error"].endswith("нечего применять")
    assert got["model"] == "gpt-test-mini" and got["report"] == "кадры уже заполнены"
    assert got["input"].startswith("frames=24")


@pytest.mark.parametrize("shared", [True, False])
def test_read_rejects_with_and_without_shared_reader(tmp_path, monkeypatch, shared):
    if not shared:
        monkeypatch.setattr(nf, "_from_shared", lambda data_dir, key: None)
    root = _project_dir(tmp_path, {"apply_ops_n_excel_gpt_1_a1_validate.txt": _NEW_FORMAT}, frames=None)
    (rej,) = nf.read_rejects(root, "n_excel_gpt_1")
    assert rej["model"] == "gpt-test-mini" and rej["input_frames"] == 24
    assert rej["report"] == "кадры уже заполнены" and "пустой ops" in rej["error"]
    assert nf.classify(errors=[], rejects=[rej], frames=None, is_check=False)["code"] == "empty_ops"


def test_failure_goal_adds_node_failure_read():
    from app.orchestrator.agents.failure_scope import failure_reads
    from app.orchestrator.contracts import PlanStep

    plan = [PlanStep(action="montage.get_board", params={"project_id": 26}, expected="доска")]
    out = failure_reads(plan, "проект 26: найди саму причину ошибки в ИИ-редакторе сцен", None)
    assert [s.action for s in out] == ["diag.node_failure", "montage.get_board"]
    assert out[0].params == {"project_id": 26}
    assert failure_reads([], "в 26 проекте найди причину, почему нода падала", None)[0].params["project_id"] == 26
    fix = [PlanStep(action="node.set_model", params={"project_id": 26}, expected="м", mutating=True)]
    assert failure_reads(fix, "найди причину ошибки в проекте 26", None) == fix


def test_failure_followup_uses_previous_project():
    from app.orchestrator.agents.failure_scope import failure_reads

    out = failure_reads([], "а можешь найти саму причину почему это случилось", None, 26)
    assert out and out[0].params == {"project_id": 26}


def test_bare_fix_after_diag_names_project():
    from app.orchestrator.agents.failure_scope import fix_after_diag_goal

    for goal in ("почини", "исправь это", "а можешь исправить", "сделай как предложил", "почини сам!"):
        assert "проекте 30" in fix_after_diag_goal(goal, 30), goal
    assert fix_after_diag_goal("почини", None) == "почини"
    assert fix_after_diag_goal("почини картинку кадра 3 в проекте 4", 30) == "почини картинку кадра 3 в проекте 4"


def test_plan_fix_from_diag_takes_first_action(monkeypatch):
    import asyncio

    from app.orchestrator.agents import failure_scope
    from app.services import orchestrator_autonomy_run as run

    async def fake(app, pid, op):
        return {"fix_options": [
            {"action": "node.output", "params": {"project_id": pid}, "title_ru": "проверить"},
            {"action": "step.run_step", "params": {"project_id": pid, "step_code": "split"}, "title_ru": "довести"},
        ]}

    monkeypatch.setattr(run, "diagnose", fake)
    plan = asyncio.run(failure_scope.plan_fix_from_diag(30))
    assert [s.action for s in plan] == ["step.run_step"]
    assert plan[0].params == {"project_id": 30, "step_code": "split"}
