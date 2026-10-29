"""Автономность оркестратора при сбое ноды: настройки, правило «без карточки», хук сбоя."""

from __future__ import annotations

import asyncio
import itertools
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import chat_notice
from app.services import orchestrator_autonomy as oa
from app.services import orchestrator_autonomy_run as run
from tests.action_layer_support import env  # noqa: F401

PID = 41
KEY = "n_excel_gpt_1"
OWNER = "local_web_operator"
RUN = {
    "action": "step.run_step",
    "params": {"project_id": PID, "step_code": "excel_gpt", "node_key": KEY},
    "title_ru": "Повторить запуск ноды",
}
MODELS = {"action": "node.set_model", "params": {"project_id": PID, "node_key": KEY}, "title_ru": "Модель"}
PROMPT = {
    "action": "prompt.edit_text",
    "params": {"project_id": PID, "node_key": KEY, "rationale": "r", "append": "a"},
    "title_ru": "Правка промта",
}


def _cfg(**over) -> dict:
    return oa.normalize({**oa.DEFAULTS, "updated_by": OWNER, **over})


# ---------- настройки ----------


def test_defaults_are_all_with_confirmation():
    cfg = oa.load()
    assert {k: cfg[k] for k in oa.SWITCHES} == dict.fromkeys(oa.SWITCHES, False)
    assert cfg["max_auto_retries_per_node"] == 2 and cfg["auto_spend_limit_usd"] == 0.5
    view = oa.public_view(cfg)
    assert view["modes"] == dict.fromkeys(oa.SWITCHES, "с подтверждением")


def test_save_is_partial_and_per_tenant():
    oa.save({"auto_retry": True, "max_auto_retries_per_node": 3}, actor=OWNER)
    oa.save({"auto_model_switch": True}, actor=OWNER)
    cfg = oa.load()
    assert cfg["auto_retry"] and cfg["auto_model_switch"] and not cfg["auto_fix_on_failure"]
    assert cfg["max_auto_retries_per_node"] == 3 and cfg["updated_by"] == OWNER
    other = oa.load("11111111-1111-1111-1111-111111111111")
    assert not other["auto_retry"] and other["updated_by"] == ""


def test_normalize_rejects_junk_values():
    cfg = oa.normalize({"auto_retry": "true", "max_auto_retries_per_node": 99, "auto_spend_limit_usd": -1})
    assert cfg["auto_retry"] is False
    assert cfg["max_auto_retries_per_node"] == 2 and cfg["auto_spend_limit_usd"] == 0.5


@pytest.fixture
def client():
    from app.web.routers import orchestrator_chat as router

    api = FastAPI()
    api.include_router(router.router, prefix="/api")
    return TestClient(api)


def test_api_defaults_and_roundtrip(client):
    got = client.get("/api/orchestrator/chat/autonomy")
    assert got.status_code == 200
    body = got.json()
    assert body["auto_retry"] is False and body["auto_fix_on_failure"] is False
    assert body["defaults"]["auto_spend_limit_usd"] == 0.5 and body["updated_by"] is None
    put = client.put(
        "/api/orchestrator/chat/autonomy", json={"auto_fix_on_failure": True, "auto_spend_limit_usd": 1.25}
    )
    assert put.status_code == 200
    assert (
        put.json()["auto_fix_on_failure"] is True
        and put.json()["modes"]["auto_fix_on_failure"] == "автономно"
    )
    again = client.get("/api/orchestrator/chat/autonomy").json()
    assert again["auto_fix_on_failure"] is True and again["auto_spend_limit_usd"] == 1.25
    assert again["auto_retry"] is False and again["updated_by"] == OWNER


@pytest.mark.parametrize(
    "body",
    [
        {"auto_retry": "yes"},
        {"auto_retry": 1},
        {"max_auto_retries_per_node": 6},
        {"auto_spend_limit_usd": 100},
        {"something_else": True},
    ],
)
def test_api_rejects_bad_values(client, body):
    assert client.put("/api/orchestrator/chat/autonomy", json=body).status_code == 422
    assert oa.load()["auto_retry"] is False


def test_api_needs_a_human(client, monkeypatch):
    from app.services import orch_bridge

    monkeypatch.setattr(orch_bridge, "human_operator", lambda scope: None)
    got = client.put("/api/orchestrator/chat/autonomy", json={"auto_retry": True})
    assert got.status_code == 403 and oa.load()["auto_retry"] is False


# ---------- правило ----------


def _decide(cfg, options=(MODELS, RUN), **kw):
    args = {
        "cause": {"code": "truncated"},
        "fix_options": list(options),
        "cfg": cfg,
        "retries_done": 0,
        "current_price": (1.0, 2.0),
        "new_price": (1.0, 2.0),
        "new_model_id": "gpt-x",
        "retry_cost_usd": 0.5,
    }
    args.update(kw)
    return oa.decide_fix(**args)


@pytest.mark.parametrize("fix,retry,switch", list(itertools.product([False, True], repeat=3)))
def test_policy_matrix(fix, retry, switch):
    d = _decide(_cfg(auto_fix_on_failure=fix, auto_retry=retry, auto_model_switch=switch))
    auto = {s.action for s in d.auto_steps}
    assert ("step.run_step" in auto) == (fix and retry)
    assert ("node.set_model" in auto) == (fix and switch)
    # Смена модели идёт раньше повтора, всё прочее — карточки.
    assert [s.action for s in d.steps] == ["node.set_model", "step.run_step"]


@pytest.mark.parametrize(
    "new_price,action,auto",
    [
        ((1.0, 2.0), "node.set_model", True),
        ((0.5, 1.0), "node.set_model", True),
        ((1.0, 3.0), "node.set_model_costly", False),
        ((0.5, 3.0), "node.set_model_costly", False),
        # Цена неизвестна: обычная смена её не пропустит, карточка «дороже или цена неизвестна».
        (None, "node.set_model_costly", False),
    ],
)
def test_model_switch_only_when_not_dearer(new_price, action, auto):
    d = _decide(_cfg(auto_fix_on_failure=True, auto_model_switch=True), new_price=new_price)
    step = d.steps[0]
    assert step.action == action and step.auto is auto
    if new_price is None:
        assert "неизвестна" in step.why_ru


def test_unknown_current_price_is_a_card():
    d = _decide(_cfg(auto_fix_on_failure=True, auto_model_switch=True), current_price=None)
    assert not d.steps[0].auto


def test_no_candidate_model_offers_list_card():
    d = _decide(_cfg(auto_fix_on_failure=True, auto_model_switch=True), new_model_id=None)
    assert d.steps[0].action == "node.list_models" and not d.steps[0].auto


@pytest.mark.parametrize(
    "kw,why",
    [
        ({"retries_done": 2}, "2 из 2"),
        ({"retry_cost_usd": None}, "неизвестна"),
        ({"retry_cost_usd": 0.51}, "выше потолка"),
        ({"cause": {"code": "empty_input"}}, "тот же результат"),
        ({"cause": {"code": "empty_ops", "check_node": True}}, "тот же результат"),
    ],
)
def test_retry_cap_cost_and_hopeless_cause(kw, why):
    d = _decide(_cfg(auto_fix_on_failure=True, auto_retry=True), options=(RUN,), **kw)
    assert not d.steps[0].auto and why in d.steps[0].why_ru


def test_retry_below_cap_is_auto():
    d = _decide(_cfg(auto_fix_on_failure=True, auto_retry=True), options=(RUN,), retries_done=1)
    assert d.steps[0].auto


def test_prompt_edit_is_always_a_card():
    cfg = _cfg(auto_fix_on_failure=True, auto_retry=True, auto_model_switch=True)
    d = _decide(cfg, options=(PROMPT, MODELS, RUN))
    assert [s.action for s in d.card_steps] == ["prompt.edit_text"]
    assert not any(s.action.startswith("prompt.") for s in d.auto_steps)


def test_blocked_or_unknown_owner_turns_everything_into_cards():
    all_on = {"auto_fix_on_failure": True, "auto_retry": True, "auto_model_switch": True}
    assert not _decide(_cfg(**all_on), blocked_reason="кончились деньги").auto_steps
    assert not _decide(_cfg(**all_on, updated_by="")).auto_steps


def test_price_not_higher():
    assert oa.price_not_higher(1.0, 1.0) is True
    assert oa.price_not_higher((1, 2), (1, 1)) is True
    assert oa.price_not_higher((1, 2), (2, 1)) is False
    assert oa.price_not_higher(None, 1) is None
    assert oa.price_not_higher((1, 2), (1,)) is None


# ---------- текст ошибки для модели ----------


def test_feedback_file_roundtrip_and_ttl(tmp_path):
    oa.write_feedback(tmp_path, KEY, "схема нарушена")
    assert oa.read_feedback(tmp_path, KEY) == "схема нарушена"
    old = time.time() - oa.FEEDBACK_TTL_S - 10
    os.utime(oa.feedback_path(tmp_path, KEY), (old, old))
    assert oa.read_feedback(tmp_path, KEY) == ""
    assert not oa.feedback_path(tmp_path, KEY).exists()


def test_retry_counter_resets():
    oa.set_retries(PID, KEY, 2)
    assert oa.retries_done(PID, KEY) == 2
    oa.set_retries(PID, KEY, 0)
    assert oa.retries_done(PID, KEY) == 0


# ---------- хук сбоя ----------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "sqlite_path", tmp_path / "notices-autonomy.db")
    chat_notice.reset()
    state = {"fid": ["7:t1"], "launch": [], "actions": [], "done": True, "launch_ok": True}

    async def diagnose(app, pid, operator):
        return {
            "found": True,
            "node_key": KEY,
            "title": "Работа с GPT",
            "errors": ["схема отклонена 3 раза"],
            "cause": {"code": "truncated", "title_ru": "ответ модели оборван", "explain_ru": "Оборвался."},
            "fix_options": [MODELS, RUN],
        }

    async def failed_run(pid, key):
        return {"id": 7, "failure_id": state["fid"][0], "error": "x"}

    async def project(pid):
        return SimpleNamespace(id=pid, meta={}, tenant_id=None, data_dir=tmp_path)

    async def priced(app, params, operator, diag):
        return "plan-run", 0.5

    async def launch(app, plan_id, operator, *, approve):
        state["launch"].append((plan_id, operator, approve))
        if len(state["fid"]) > 1:
            state["fid"].pop(0)
        if state["launch_ok"]:
            return {"ok": True, "started": True, "error": "", "credits": 20}
        return {"ok": False, "started": True, "error": "схема отклонена"}

    async def run_action(app, action, params, operator, note):
        state["actions"].append(action)
        return {"ok": True, "started": True, "error": ""}

    async def node_done(pid, key):
        return state["done"]

    monkeypatch.setattr(run, "diagnose", diagnose)
    monkeypatch.setattr(run, "failed_run", failed_run)
    monkeypatch.setattr(run, "_project", project)
    monkeypatch.setattr(run, "_priced_retry", priced)
    monkeypatch.setattr(run, "_launch", launch)
    monkeypatch.setattr(run, "run_action", run_action)
    monkeypatch.setattr(run, "node_done", node_done)
    monkeypatch.setattr(
        run,
        "model_candidate",
        lambda meta, key, tried: {
            "current_model_id": "a",
            "current_price": (1.0, 2.0),
            "new_model_id": "b" if "b" not in tried else None,
            "new_label": "B",
            "new_price": (1.0, 2.0),
        },
    )
    return state


async def _notices() -> list[dict]:
    return await chat_notice.list_notices(PID, 0, 50)


async def test_switches_off_reports_once_with_card_and_runs_nothing(wired):
    first = await run.handle_failure(PID, "sleep")
    second = await run.handle_failure(PID, "sleep")
    assert first["status"] == "reported" and second["status"] == "deduped"
    items = await _notices()
    assert len(items) == 1
    note = items[0]
    assert note["kind"] == "node_failure" and "упала" in note["title"]
    assert "Предлагаю" in note["text"]
    acts = {a["action_id"]: a for a in note["suggested_actions"]}
    assert acts["step.run_step"]["needs_approval"] is True
    assert acts["step.run_step"]["params"]["stop_after_node_key"] == KEY
    assert wired["launch"] == [] and wired["actions"] == []


async def test_auto_retry_runs_once_and_reports_done(wired):
    oa.save({"auto_fix_on_failure": True, "auto_retry": True}, actor=OWNER)
    got = await run.handle_failure(PID, "sleep")
    assert got["status"] == "fixed"
    assert wired["launch"] == [("plan-run", OWNER, True)] and wired["actions"] == []
    again = await run.handle_failure(PID, "sleep")
    assert again["status"] == "deduped" and len(wired["launch"]) == 1
    texts = [n["text"] for n in await _notices()]
    assert any("Чиню сам" in t for t in texts)
    assert any("перезапустил ноду" in t and "теперь готово" in t for t in texts)
    assert oa.retries_done(PID, KEY) == 0


async def test_failed_model_switch_is_reverted_and_retries_stop_at_cap(wired):
    oa.save(
        {
            "auto_fix_on_failure": True,
            "auto_retry": True,
            "auto_model_switch": True,
            "max_auto_retries_per_node": 1,
        },
        actor=OWNER,
    )
    wired["launch_ok"] = False
    wired["fid"] = ["7:t1", "7:t2"]
    got = await run.handle_failure(PID, "sleep")
    assert len(wired["launch"]) == 1
    assert wired["actions"] == ["node.set_model", "node.revert_model"]
    assert [r["failure_id"] for r in got["rounds"]] == ["7:t1", "7:t2"]
    assert got["rounds"][1]["auto"] == []  # второй круг: лимит автоповторов, только карточка
    texts = [n["text"] for n in await _notices()]
    assert any("не помогло" in t and "Вернул прежнюю модель" in t for t in texts)
    assert any("автоповторов уже 1 из 1" in t for t in texts)


async def test_money_pause_never_runs_by_itself(wired):
    oa.save({"auto_fix_on_failure": True, "auto_retry": True, "auto_model_switch": True}, actor=OWNER)
    got = await run.handle_failure(PID, "pause_budget")
    assert got["status"] == "reported" and wired["launch"] == [] and wired["actions"] == []
    assert "бюджет" in (await _notices())[0]["text"]


async def test_hook_ignores_soft_retry_and_never_raises(monkeypatch):
    assert run.on_step_failure(PID, "retry") is None
    assert run.on_step_failure(PID, "stopped") is None

    async def boom(pid, action):
        raise RuntimeError("оркестратор недоступен")

    monkeypatch.setattr(run, "handle_failure", boom)
    task = run.on_step_failure(PID, "sleep")
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    assert task.exception() is None


# ---------- без карточки через настоящий слой действий ----------


@pytest.fixture
def paid_stub(monkeypatch):
    """«step.run_step»-заглушка: платная и разрушающая, как настоящая, без сети и денег."""
    from app.services.action_layer import executor, service
    from tests.action_layer_support import fresh_registry, make_spec

    calls: list[dict] = []

    async def _run(ctx, params):
        calls.append(dict(params))
        return {"text_ru": "нода готова"}

    schema = {
        "type": "object",
        "properties": {"project_id": {"type": "integer"}, "ceiling_credits": {"type": "number"}},
        "additionalProperties": False,
    }
    spec = make_spec(
        "step.run_step",
        _run,
        risk="destructive",
        spends=True,
        destructive=True,
        cost=(0.0, 100.0),
        schema=schema,
    )
    reg = fresh_registry(spec)
    monkeypatch.setattr(service, "default_registry", reg)
    monkeypatch.setattr(executor, "default_registry", reg)
    return calls


async def test_approval_on_behalf_of_owner_runs_paid_plan(env, paid_stub):  # noqa: F811
    from app.services.action_layer import service

    pid = await env.add_project(frames=1)
    plan_id, cost = await run._priced_retry(
        env.app, {"project_id": pid, "ceiling_credits": 100.0}, OWNER, {"node_key": KEY}
    )
    assert plan_id and cost == 0.5  # 100 кредитов × $0.005
    got = await run._launch(env.app, plan_id, OWNER, approve=True)
    assert got["ok"] and paid_stub == [{"project_id": pid, "ceiling_credits": 100.0}]
    view = await service.plan_view(plan_id)
    assert view["status"] == "succeeded"
    assert [a["operator"] for a in view["approvals"]] == [OWNER]
    assert "Автономный режим" in view["request_text"]


async def test_paid_plan_without_approval_is_refused(env, paid_stub):  # noqa: F811
    pid = await env.add_project(frames=1)
    plan_id, _cost = await run._priced_retry(
        env.app, {"project_id": pid, "ceiling_credits": 100.0}, OWNER, {"node_key": KEY}
    )
    got = await run._launch(env.app, plan_id, OWNER, approve=False)
    assert not got["ok"] and not got["started"] and paid_stub == []


async def test_robot_identity_cannot_approve(env, paid_stub):  # noqa: F811
    pid = await env.add_project(frames=1)
    plan_id, _cost = await run._priced_retry(
        env.app, {"project_id": pid, "ceiling_credits": 100.0}, "system", {"node_key": KEY}
    )
    got = await run._launch(env.app, plan_id, "system", approve=True)
    assert not got["started"] and paid_stub == []


async def test_retry_feedback_reaches_the_model_prompt(tmp_path, monkeypatch):
    from openpyxl import Workbook

    from app.contracts.policy import FEEDBACK_HEADER
    from app.services import gpt_operator_client as goc

    wb = Workbook()
    wb.active.title = "план"
    wb.save(tmp_path / "project.xlsx")
    wb.close()
    prompts: list[str] = []

    async def fake_chat(**kw):
        prompts.append(kw.get("prompt") or "")
        return SimpleNamespace(text='{"ops":[{"frame_uuid":"u1","fields":{"закадр":"новый"}}]}')

    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: True)
    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    monkeypatch.setattr("app.services.gpt_api.collect_result_urls", lambda text: [])
    kw = {
        "project_dir": tmp_path,
        "node_key": KEY,
        "role": "assist",
        "output_mode": "project_file",
        "prompt": "заполни",
        "accompanying": "",
        "input_paths": [tmp_path / "project.xlsx"],
    }
    await goc.run_operator_api(**kw)
    oa.write_feedback(tmp_path, KEY, "ответ модели оборван")
    await goc.run_operator_api(**kw)
    assert FEEDBACK_HEADER not in prompts[0]
    assert prompts[1].startswith("заполни") and f"{FEEDBACK_HEADER}\nответ модели оборван" in prompts[1]


def test_upstream_step_is_a_card_with_its_own_reason_not_a_retry():
    # Пустой вход: первым идёт разбивка (другой платный шаг), а не повтор упавшей ноды.
    split = {"action": "step.run_step", "params": {"project_id": 1, "step_code": "split"}, "upstream": True, "title_ru": "Довести до разбивки"}
    for auto_retry in (False, True):
        d = _decide(_cfg(auto_fix_on_failure=True, auto_retry=auto_retry), options=(split, RUN), cause={"code": "empty_input"})
        by_step = {s.params.get("step_code"): s for s in d.steps}
        assert not by_step["split"].auto and "не повтор" in by_step["split"].why_ru
        assert "автоповтор" not in by_step["split"].why_ru
        assert any(s is not by_step["split"] and s.action == "step.run_step" for s in d.steps)
        assert d.steps[0] is by_step["split"]


def test_retry_params_skip_upstream_and_upstream_keeps_its_step():
    from app.services import orchestrator_autonomy_run as run

    split = {"action": "step.run_step", "params": {"project_id": 30, "step_code": "split"}, "upstream": True}
    rerun = {"action": "step.run_step", "params": {"project_id": 30, "step_code": "excel_gpt", "node_key": "n_excel_gpt_1"}}
    card = run.retry_params({"fix_options": [split, rerun]}, None)
    assert card["step_code"] == "excel_gpt" and card["stop_after_node_key"] == "n_excel_gpt_1"
    assert run._with_run_params(split, card)["params"]["step_code"] == "split"
    assert run._with_run_params(rerun, card)["params"] is card
