"""Проверка содержания после шагов: склейка закадра, число кадров, герои в промтах, чат."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import chat_notice
from app.services import content_checks as cc
from app.services import content_checks_run as ccr
from app.services import orchestrator_autonomy as oa
from app.services import retry_feedback as rf

VO = (
    "Ночью молодой ёж выходит на тропу. Вдруг в темноте он слышит шорох. "
    "Убегать поздно — и тогда ёж превращается в колючий шар. Лиса уходит ни с чем."
)
FRAMES = [
    "Ночью молодой ёж выходит на тропу.",
    "Вдруг в темноте он слышит шорох.",
    "Убегать поздно — и тогда ёж превращается в колючий шар.",
    "Лиса уходит ни с чем.",
]
OWNER = "local_web_operator"


def _cells(texts: list[str]) -> list[dict]:
    return [{"number": i + 1, "uuid": f"u{i + 1}", "text": t} for i, t in enumerate(texts)]


# ---------- склейка закадра ----------


def test_concat_ok_ignores_spaces_stress_and_typography():
    texts = list(FRAMES)
    texts[2] = "Убегать  по́здно - и тогда еж превращается в колючий шар."
    assert cc.vo_findings(VO, _cells(texts), step="split") == []


def test_changed_word_in_one_frame_is_fixed_from_voiceover():
    texts = list(FRAMES)
    texts[2] = texts[2].replace("поздно", "рано")
    [f] = cc.vo_findings(VO, _cells(texts), step="split")
    assert f["check"] == "vo_concat" and f["frames"] == [3]
    assert "«рано» вместо «поздно»" in f["detail_ru"]
    assert f["fixes"] == [{"number": 3, "uuid": "u3", "was": texts[2], "text": FRAMES[2]}]
    opts = cc.fix_options(cc.make_report("split", [f], frames=4), 26)
    edit = opts[0]
    assert edit["action"] == "shot.edit_cell_voiceover" and edit["deterministic"]
    assert edit["params"] == {"project_id": 26, "parent_uuid": "u3", "voiceover": FRAMES[2]}
    assert "step.run_step" not in [o["action"] for o in opts]


def test_lost_text_between_frames_needs_rerun_with_feedback():
    texts = [FRAMES[0], FRAMES[1], FRAMES[3]]
    [f] = cc.vo_findings(VO, _cells(texts), step="split")
    assert "пропал текст между кадрами 2 и 3" in f["detail_ru"]
    assert "колючий шар" in f["detail_ru"] and f["fixes"] == []
    report = cc.make_report("split", [f], frames=3)
    run = [o for o in cc.fix_options(report, 26) if o["action"] == "step.run_step"]
    assert run and run[0]["params"] == {"project_id": 26, "step_code": "split"}
    assert "колючий шар" in cc.feedback_text(report)


def test_duplicated_frame_is_reported():
    texts = [*FRAMES, FRAMES[3]]
    [f] = cc.vo_findings(VO, _cells(texts), step="split")
    assert f["frames"] == [4, 5]
    assert "кадры 4–5 повторяют одну фразу" in f["detail_ru"]


def test_after_enrich_no_split_rerun_is_offered():
    texts = [FRAMES[0], FRAMES[1], FRAMES[3]]
    report = cc.make_report("enrich", cc.vo_findings(VO, _cells(texts), step="enrich"), frames=3)
    assert [o["action"] for o in cc.fix_options(report, 26)] == ["diag.content_checks"]


def test_split_contract_problems_feed_the_repair():
    good = [{"voiceover_text": t} for t in FRAMES]
    assert cc.split_contract_problems(VO, good) == []
    bad = cc.split_contract_problems(VO, good[:2] + good[3:])
    assert len(bad) == 1 and "дословно совпадать" in bad[0] and "колючий шар" in bad[0]


# ---------- число кадров ----------


@pytest.mark.parametrize(
    "plan,meta,want",
    [
        ("Ролик на минуту. Разбей на 18 кадров.", {}, 18),
        ("Количество кадров: 12", {}, 12),
        ("Кадр 1: ёж. Кадр 2: лиса. Кадр 3: шар.", {}, None),
        ("Через 2 кадра лиса уходит.", {}, None),
        ("", {"node_step_params": {"split": {"frames_count": 7}}}, 7),
    ],
)
def test_requested_frame_count_only_when_explicit(plan, meta, want):
    assert cc.requested_frame_count(SimpleNamespace(general_plan=plan, meta=meta)) == want


def test_count_findings():
    assert cc.count_findings(18, None) == [] and cc.count_findings(18, 18) == []
    [f] = cc.count_findings(16, 18)
    assert f["check"] == "frame_count" and "Кадров 16, а в сценарии просили ровно 18" in f["detail_ru"]


# ---------- герои в промтах ----------

REG = {"c01": "Внешность c01: ёж, серые иглы", "c02": "Внешность c02: рыжая лиса"}


def _frame(n, prompt, chars):
    return {"number": n, "uuid": f"f{n}", "prompt": prompt, "characters": chars}


def test_characters_good():
    frames = [
        _frame(1, "Референс: c01 — ёж\nФон: тропа", "c01"),
        _frame(2, "Референс: c01 — ёж; c02 — лиса\nФон: кусты", "c01, c02"),
        _frame(3, "Фон: пустая тропа", None),
    ]
    assert cc.character_findings(frames, REG) == []


def test_unknown_character_marks_frame_for_regen():
    [f] = cc.character_findings([_frame(5, "Референс: c07 — сова\nФон: лес", "c01")], REG)
    assert "c07, которого нет в реестре" in f["detail_ru"] and f["regen"] and not f["fixed_prompt"]
    opts = cc.fix_options(cc.make_report("img_pr", [f], frames=5), 26)
    assert [o["action"] for o in opts] == ["shot.edit_field", "step.run_step", "diag.content_checks"]
    assert opts[0]["params"]["value"] == "" and not opts[0].get("deterministic")
    assert opts[1]["params"] == {"project_id": 26, "step_code": "img_pr"}


def test_stale_canon_line_is_dropped_deterministically():
    prompt = "Референс: c01 — ёж\nФон: тропа\nВнешность c02: рыжая лиса\nДействие: ёж идёт"
    [f] = cc.character_findings([_frame(4, prompt, "c01")], REG)
    assert f["severity"] == "warning" and "c02, а в кадре по разметке c01" in f["detail_ru"]
    assert "c02" not in f["fixed_prompt"] and not f["regen"]
    [edit, _again] = cc.fix_options(cc.make_report("img_pr", [f], frames=4), 26)
    assert edit["action"] == "shot.edit_field" and edit["deterministic"]
    assert edit["params"]["field"] == "img_prompt" and edit["params"]["value"] == f["fixed_prompt"]


def test_missing_second_hero_gets_canon_line():
    [f] = cc.character_findings([_frame(2, "Референс: c01 — ёж\nФон: кусты", "c01, c02")], REG)
    assert "не описан c02" in f["detail_ru"] and f["severity"] == "error"
    assert "Внешность c02: рыжая лиса" in f["fixed_prompt"]


# ---------- проверки по базе и после шага ----------


async def _seed(texts, *, prompts=None, chars=None, vo=VO, status="frames_ready"):
    from app.db import session_scope
    from app.models import Entity, Frame, Project, ProjectStatus

    async with session_scope() as s:
        p = Project(slug=f"cc-{len(texts)}-{status}", topic="ежи", status=ProjectStatus(status), meta={})
        s.add(p)
        await s.flush()
        for code, look in (("c01", "ёж, серые иглы"), ("c02", "рыжая лиса")):
            s.add(Entity(project_id=p.id, type="character", code=code, name=code, attrs={"look": look}))
        for i, t in enumerate(texts):
            s.add(
                Frame(
                    project_id=p.id,
                    number=i + 1,
                    uuid=f"u{i + 1}",
                    voiceover_text=t,
                    image_prompt=(prompts or {}).get(i + 1),
                    attrs={"characters": (chars or {}).get(i + 1, "c01")},
                )
            )
        await s.commit()
        p.data_dir.mkdir(parents=True, exist_ok=True)
        (p.data_dir / "voiceover.txt").write_text(vo, encoding="utf-8")
        return p.id


async def test_check_after_step_writes_meta_report():
    from app.db import session_scope
    from app.models import Project

    pid = await _seed([FRAMES[0], FRAMES[1], FRAMES[3]])
    async with session_scope() as s:
        p = await s.get(Project, pid)
        assert await cc.check_after_step(s, p, "planning") is None
        report = await cc.check_after_step(s, p, "splitting")
        await s.commit()
    assert report["step"] == "split" and not report["ok"]
    async with session_scope() as s:
        stored = cc.stored_reports((await s.get(Project, pid)).meta)
    assert stored["split"]["digest"] == report["digest"]


async def test_run_checks_img_pr_finds_foreign_hero():
    from app.db import session_scope
    from app.models import Project

    prompts = {1: "Референс: c01 — ёж", 2: "Референс: c03 — сова", 3: "Фон: тропа", 4: "Референс: c01"}
    pid = await _seed(FRAMES, prompts=prompts)
    async with session_scope() as s:
        report = await cc.run_checks(s, await s.get(Project, pid), "img_pr")
    assert report["ok"] is False
    assert [f["frames"] for f in report["findings"]] == [[2]]


# ---------- чат: уведомление, решение, повторная проверка ----------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    from app.services import orchestrator_autonomy_run as run
    from app.settings import settings

    monkeypatch.setattr(settings, "sqlite_path", tmp_path / "notices-content.db")
    chat_notice.reset()
    state: dict = {
        "actions": [],
        "fresh": {"ok": True, "findings": [], "step": "split"},
        "status": "frames_ready",
    }

    async def project(pid):
        return SimpleNamespace(id=pid, meta={}, tenant_id=None, data_dir=tmp_path, status=state["status"])

    async def run_action(app, action, params, operator, note):
        state["actions"].append((action, params))
        return {"ok": True, "started": True, "error": ""}

    async def recheck(pid, step):
        return state["fresh"]

    monkeypatch.setattr(run, "_project", project)
    monkeypatch.setattr(run, "run_action", run_action)
    monkeypatch.setattr(run, "_app", lambda app: None)
    monkeypatch.setattr(ccr, "recheck", recheck)
    monkeypatch.setattr(
        ccr,
        "_busy_reason",
        lambda p: "в проекте идёт шаг, правки подождут" if p.status == "splitting" else "",
    )
    return state


def _report():
    texts = list(FRAMES)
    texts[2] = texts[2].replace("поздно", "рано")
    return cc.make_report("split", cc.vo_findings(VO, _cells(texts), step="split"), frames=4)


def _set(monkeypatch, **over):
    cfg = oa.normalize({**oa.DEFAULTS, "updated_by": OWNER, **over})
    monkeypatch.setattr(oa, "load", lambda tenant=None: cfg)


async def _notices(pid):
    return await chat_notice.list_notices(pid, 0, 50)


async def test_switches_off_card_once_nothing_runs(wired, monkeypatch):
    _set(monkeypatch)
    rep = _report()
    first = await ccr.handle_report(26, rep)
    again = await ccr.handle_report(26, rep)
    assert first["status"] == "reported" and again["notice"] == "deduped"
    assert wired["actions"] == []
    [n] = await _notices(26)
    assert n["kind"] == ccr.KIND and "«рано» вместо «поздно»" in n["text"]
    assert [a["action_id"] for a in n["suggested_actions"]][:1] == ["shot.edit_cell_voiceover"]


async def test_auto_fix_edits_cell_and_reports_recheck(wired, monkeypatch):
    _set(monkeypatch, auto_fix_on_failure=True)
    out = await ccr.handle_report(26, _report())
    assert out["status"] == "fixed" and out["auto"] == ["shot.edit_cell_voiceover"]
    assert wired["actions"] == [
        ("shot.edit_cell_voiceover", {"project_id": 26, "parent_uuid": "u3", "voiceover": FRAMES[2]})
    ]
    kinds = {n["kind"]: n for n in await _notices(26)}
    assert "Повторная проверка: всё сходится" in kinds[ccr.KIND_FIX]["text"]


async def test_busy_project_gets_card_only(wired, monkeypatch):
    _set(monkeypatch, auto_fix_on_failure=True)
    wired["status"] = "splitting"
    out = await ccr.handle_report(26, _report())
    assert out["auto"] == [] and wired["actions"] == []


async def test_hook_ignores_clean_report():
    assert ccr.on_report(26, {"ok": True, "step": "split"}) is None


def test_decide_fix_deterministic_edit_rule():
    edit = {
        "action": "shot.edit_field",
        "params": {"project_id": 1},
        "title_ru": "канон",
        "deterministic": True,
    }
    plain = {"action": "shot.edit_field", "params": {"project_id": 1}, "title_ru": "очистить"}
    cfg_on = oa.normalize({**oa.DEFAULTS, "updated_by": OWNER, "auto_fix_on_failure": True})
    d = oa.decide_fix(cause={"code": "content_check"}, fix_options=[edit, plain], cfg=cfg_on, retries_done=0)
    assert [s.auto for s in d.steps] == [True, False]
    d = oa.decide_fix(
        cause={"code": "content_check"}, fix_options=[edit], cfg=oa.normalize({}), retries_done=0
    )
    assert not d.auto_steps
    blocked = oa.decide_fix(
        cause={"code": "content_check"},
        fix_options=[edit],
        cfg=cfg_on,
        retries_done=0,
        blocked_reason="занято",
    )
    assert not blocked.auto_steps and blocked.steps[0].why_ru == "занято"


# ---------- повтор с текстом ошибки и замечания ревьюера ----------


def test_step_feedback_only_on_worker_retry(tmp_path):
    fs = {"last_running": "splitting", "last_error": "склейка не сошлась", "total_fails": {"splitting": 1}}
    p = SimpleNamespace(data_dir=tmp_path, meta={"step_failure": fs})
    assert "склейка не сошлась" in rf.step_feedback(p, "split", "splitting")
    assert rf.step_feedback(p, "split", "scripting") == ""
    manual = SimpleNamespace(data_dir=tmp_path, meta={"step_failure": {**fs, "total_fails": {}}})
    assert rf.step_feedback(manual, "split", "splitting") == ""
    oa.write_feedback(tmp_path, "split", "кадр 3: рано вместо поздно")
    assert "рано вместо поздно" in rf.step_feedback(manual, "split", "splitting")


def test_review_hints_go_to_their_step_and_are_cleared(tmp_path):
    p = SimpleNamespace(
        data_dir=tmp_path, meta={"auto_fix_hints": ["короче вступление"], "auto_fix_hints_for": "planning"}
    )
    block = rf.prompt_addendum(p, "plan", "planning")
    assert rf.REVIEW_HEADER in block and "- короче вступление" in block
    assert rf.prompt_addendum(p, "split", "splitting") == ""
    assert not rf.clear_review_hints(p, "splitting") and rf.clear_review_hints(p, "planning")
    assert "auto_fix_hints" not in p.meta


async def test_split_repair_gets_lost_text_and_result_is_verified(tmp_path, monkeypatch):
    from app.models import Project
    from app.services import xlsx_step_runners as xsr

    project = Project(slug="split-check", topic="t", hero_mode="full_auto", meta={})
    project.id = 32
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "voiceover.txt").write_text(VO, encoding="utf-8")
    prompt_file = project.data_dir / "tmp_gpt" / "prompt_split.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("prompt", encoding="utf-8")
    replies = [[FRAMES[0], FRAMES[1], FRAMES[3]], FRAMES]
    seen: list[str] = []

    async def _ask(msg, *_a, **_kw) -> str:
        seen.append(msg)
        frames = replies[min(len(seen), len(replies)) - 1]
        return json.dumps({"ops": [{"target": "replace_frames", "frames": [{"закадр": t} for t in frames]}]})

    async def _lock(_pid, _step, fn):
        return await fn()

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)
    monkeypatch.setattr(xsr.cx, "write_split_prompt_file", lambda *_a, **_kw: prompt_file)
    monkeypatch.setattr(xsr.cx, "chat_message", lambda *_a, **_kw: "go")

    result = await xsr.run_split_xlsx(project)
    assert len(seen) == 2 and "колючий шар" in seen[1]
    assert result.split_verified is True and result.split_source == "llm" and not result.degraded_no_llm
    assert len(result.frames_spec) == 4


async def test_split_wrong_count_with_right_text_is_accepted(tmp_path, monkeypatch):
    from app.models import Project
    from app.services import xlsx_step_runners as xsr

    project = Project(slug="split-count", topic="t", hero_mode="full_auto", meta={})
    project.id = 33
    project.general_plan = "Ровно 3 кадра."
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "voiceover.txt").write_text(VO, encoding="utf-8")
    prompt_file = project.data_dir / "tmp_gpt" / "prompt_split.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("prompt", encoding="utf-8")
    calls: list[str] = []

    async def _ask(msg, *_a, **_kw) -> str:
        calls.append(msg)
        return json.dumps({"ops": [{"target": "replace_frames", "frames": [{"закадр": t} for t in FRAMES]}]})

    async def _lock(_pid, _step, fn):
        return await fn()

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)
    monkeypatch.setattr(xsr.cx, "write_split_prompt_file", lambda *_a, **_kw: prompt_file)
    monkeypatch.setattr(xsr.cx, "chat_message", lambda *_a, **_kw: "go")

    result = await xsr.run_split_xlsx(project)
    assert len(calls) > 1 and "ровно 3" in calls[1]
    assert result.split_verified is True and not result.degraded_no_llm and len(result.frames_spec) == 4


# ---------- чтение из чата ----------


@pytest.mark.parametrize(
    "text,step",
    [
        ("проверь результат разбивки", "split"),
        ("всё ли сходится в кадрах", None),
        ("проверь промты картинок", "img_pr"),
        ("не потерялся ли текст в кадрах?", None),
    ],
)
def test_chat_phrases_route_to_content_checks(text, step):
    from app.orchestrator.agents.question_context import plan_reads

    want = {"project_id": 26, **({"step": step} if step else {})}
    assert ("diag.content_checks", want) in plan_reads(text, 26)


def test_chat_other_questions_do_not_route_here():
    from app.orchestrator.agents.question_context import plan_reads

    for text in ("какой статус проекта", "почему упала нода", "проверь результат n_excel_gpt_1"):
        assert "diag.content_checks" not in [a for a, _ in plan_reads(text, 26)], text


def test_spec_read_only_free_and_in_catalog():
    from app.orchestrator.actions import metadata_of
    from app.orchestrator.actions.executor import whitelist
    from app.orchestrator.agents.action_catalog import BASE_READ_IDS
    from app.orchestrator.agents.question_context import CONTENT_RULE, Fact, system_prompt
    from app.services.action_layer import builtin_insight as bi
    from app.services.action_layer.planner_catalog import SYNONYMS
    from app.services.action_layer.planner_rules import ACTION_NOTES
    from app.services.action_layer.spec import RISK_READ, ActionRegistry

    reg = ActionRegistry()
    bi.register_insight(reg)
    spec = reg.find("diag.content_checks")
    assert spec is not None and spec.risk == RISK_READ and spec.params_schema["required"] == ["project_id"]
    assert "diag.content_checks" in whitelist()
    meta = metadata_of("diag.content_checks")
    assert meta.risk == "none" and meta.read_only and not meta.paid and not meta.destructive
    assert "diag.content_checks" in BASE_READ_IDS and "провер" in SYNONYMS["diag.content_checks"]
    assert "fix_options" in ACTION_NOTES["diag.content_checks"]
    assert CONTENT_RULE in system_prompt([Fact("diag.content_checks", "x", "y")], 26)


async def test_chat_action_reports_findings_and_phrases(monkeypatch):
    from app.services.action_layer import content_checks_diag as ccd

    texts = list(FRAMES)
    texts[2] = texts[2].replace("поздно", "рано")
    report = cc.make_report("split", cc.vo_findings(VO, _cells(texts), step="split"), frames=4)

    async def load(pid, steps):
        assert steps is None
        return {
            "found": True,
            "reports": [report],
            "stored": {"split": {**report, "at": "2026-10-29T10:00:00+00:00"}},
        }

    monkeypatch.setattr(ccd, "_load", load)
    out = await ccd._diag_content_checks(SimpleNamespace(), {"project_id": 26})
    assert out["clean"] is False and "ok" not in out and out["fix_options"][0]["action"] == "shot.edit_cell_voiceover"
    assert "«рано» вместо «поздно»" in out["text_ru"] and "После шага «разбивка на кадры»" in out["text_ru"]
    assert out["suggested_phrase"].startswith("в проекте 26 поставь закадр кадра 3")
    from app.orchestrator.actions.executor import _refusal

    # Находки — не отказ: с ok=False чат писал «действие отказалось выполняться».
    assert _refusal(out) is None


async def test_node_failure_retry_feedback_reaches_step_runner(tmp_path, monkeypatch):
    """Автоповтор упавшей разбивки: текст ошибки лежит и под кодом шага, который читает раннер."""
    from app.services import orchestrator_autonomy_run as run

    seen: dict = {}

    async def project(pid):
        return SimpleNamespace(id=pid, meta={}, data_dir=tmp_path)

    async def launch(app, plan_id, operator, *, approve):
        seen["split"] = rf.step_feedback(SimpleNamespace(data_dir=tmp_path, meta={}), "split", "splitting")
        return {"ok": True, "started": True, "error": ""}

    async def node_done(pid, key):
        return True

    monkeypatch.setattr(run, "_project", project)
    monkeypatch.setattr(run, "_launch", launch)
    monkeypatch.setattr(run, "node_done", node_done)
    diag = {"step": "split", "cause": {"title_ru": "сбой"}, "errors": ["склейка закадра кадров не совпала"]}
    res = await run._retry(None, 41, "n_split", diag, "plan-1", OWNER)
    assert res["ok"] and "склейка закадра кадров не совпала" in seen["split"]
    assert oa.read_feedback(tmp_path, "split") == "" and oa.read_feedback(tmp_path, "n_split") == ""
    oa.set_retries(41, "n_split", 0)


def test_node_failure_names_content_mismatch_and_offers_the_check():
    from app.services.action_layer import node_failure as nf

    err = (
        "ответ прошёл схему, но не прошёл проверку:\n- склейка поля «закадр» всех кадров подряд "
        "должна дословно совпадать с закадровым текстом (voiceover.txt): пропал текст"
    )
    cause = nf.classify(
        errors=[], rejects=[{"kind": "validate", "error": err, "reply": "{}"}], frames=None, is_check=False
    )
    assert cause["code"] == "content_mismatch"
    fixes = nf.fix_options(cause, project_id=26, node_key="n_split", step_code="split")
    assert [f["action"] for f in fixes] == ["diag.content_checks", "step.run_step", "node.set_model"]


async def test_auto_rerun_supersedes_cell_edits_and_gets_feedback(wired, monkeypatch, tmp_path):
    from app.services import orchestrator_autonomy_run as run

    _set(monkeypatch, auto_fix_on_failure=True, auto_retry=True)
    seen: dict = {}

    async def priced(app, params, operator, diag):
        return "plan-split", 0.1

    async def launch(app, plan_id, operator, *, approve):
        seen["plan"] = plan_id
        seen["feedback"] = oa.read_feedback(tmp_path, "split")
        return {"ok": True, "started": True, "error": ""}

    monkeypatch.setattr(run, "_priced_retry", priced)
    monkeypatch.setattr(run, "_launch", launch)
    rep = _report()
    rep = cc.make_report("split", [*rep["findings"], *cc.count_findings(4, 3)], frames=4)
    out = await ccr.handle_report(27, rep)
    assert out["status"] == "fixed" and "step.run_step" in out["auto"]
    assert wired["actions"] == [] and seen["plan"] == "plan-split"
    assert "Кадров 4, а в сценарии просили ровно 3" in seen["feedback"]
    assert oa.read_feedback(tmp_path, "split") == ""
    assert oa.retries_done(27, ccr.retry_key("split")) == 0


def test_summary_text_prompts_names_heroes_first():
    from app.services.content_checks import summary_text

    vo = {"check": "vo_concat", "title_ru": "Закадр кадров не сходится", "detail_ru": ""}
    hero = {"check": "prompt_characters", "title_ru": "Кадр 3: в промте герой c07", "detail_ru": ""}
    text = summary_text({"step": "img_pr", "ok": False, "findings": [vo]})
    assert text.startswith("Герои в промтах картинок совпадают")
    text = summary_text({"step": "img_pr", "ok": False, "findings": [vo, hero]})
    assert text.startswith("Герои в промтах картинок не совпали") and text.index("c07") < text.index("Закадр")
    assert "Герои" not in summary_text({"step": "split", "ok": True, "findings": []})
