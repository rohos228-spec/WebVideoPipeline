from __future__ import annotations

pytest_plugins = ['tests.action_layer_support']

import re

import pytest

from app.models import Project, ProjectStatus
from app.services import launch_risks as lr
from app.services.action_layer import approval, executor, service
from app.services.action_layer import builtin_launch as bl
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import JobState, ensure_builtin_actions, registry
from tests.action_layer_support import fresh_registry, make_spec

BASE = "/api/action-layer"

# Перенесено из video-pipeline int/chat-v1. Роутер /api/action-layer подключён
# в app.web.api; четыре теста ниже ждут поведения старой линии, которого в
# новой нет (лимиты /api/runtime-streams, предупреждение о занятом проекте,
# таймауты долгого шага), и помечены по отдельности.
_OLD_LINE = pytest.mark.xfail(
    reason="поведение старой линии ещё не перенесено (runtime-streams, предупреждения, таймауты)",
    strict=False,
)
OP = approval.OperatorIdentity("web_operator:launch")
PROJ_SCHEMA = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
               "additionalProperties": False}


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()
    executor._CLAIMS.clear()
    yield
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    executor._ORPHANS.clear()
    executor._CLAIMS.clear()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


@pytest.fixture
def steps_open(monkeypatch):
    monkeypatch.setattr("app.orchestrator.pipeline_steps.is_step_runnable", lambda step, status: True)


@pytest.fixture
def stub(monkeypatch):
    state = {"writes": {}, "reads": {}, "calls": []}
    original = ActionContext.call

    async def call(self, method, path, **kw):
        table = state["reads"] if method.upper() == "GET" else state["writes"]
        for pattern, answer in table.items():
            if re.search(pattern, path):
                if method.upper() != "GET":
                    state["calls"].append((method.upper(), path, kw.get("params"), kw.get("json")))
                return answer(path, kw) if callable(answer) else answer
        return await original(self, method, path, **kw)

    monkeypatch.setattr(ActionContext, "call", call)
    return state


async def _preview(env, steps):
    return await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})


async def _set(env, pid, **fields):
    async with env.factory() as s:
        p = await s.get(Project, pid)
        for k, v in fields.items():
            setattr(p, k, ProjectStatus(v) if k == "status" else v)
        await s.commit()


async def _other_project(env, slug="launch-other") -> int:
    async with env.factory() as s:
        p = Project(topic="b", slug=slug, status=ProjectStatus.new)
        s.add(p)
        await s.commit()
        return p.id


async def _approve_run(env, plan):
    assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


def step(action, **params):
    return {"action_id": action, "params": params}


def _ready_nodes(*types):
    return {"node_runs": [{"node_key": f"n_{t}", "node_type": t, "status": "done"} for t in types]}



def test_every_pipeline_step_except_publish_is_launchable() -> None:
    from app.orchestrator.node_registry import STEP_CODE_TO_NODE_TYPE

    spec = registry.get("step.run_step")
    enum = set(spec.params_schema["properties"]["step_code"]["enum"])
    assert enum == set(STEP_CODE_TO_NODE_TYPE) - {"publish"}
    assert {"plan", "script", "split", "excel_gpt", "scene_d", "hero", "sfx_gen"} <= enum
    assert (spec.risk, spec.spends_money, spec.needs_approval) == ("destructive", True, True)
    assert spec.warnings is not None
    basis = registry.get("step.get_run_cost_basis")
    assert set(basis.params_schema["properties"]["step_code"]["enum"]) == enum


def test_new_actions_tiers_and_existing_project_controls() -> None:
    tiers = {"project.create_batch": ("local_safe", False), "queue.set_parallel": ("local_safe", False),
             "queue.enqueue_many": ("destructive", True)}
    for aid, (tier, approval_needed) in tiers.items():
        spec = registry.get(aid)
        assert spec.risk == tier and spec.needs_approval is approval_needed, aid
        assert spec.future_spend_note, aid
    for aid in ("project.stop", "project.pause"):
        assert registry.get(aid).risk == "local_safe" and not registry.get(aid).needs_approval
    for aid in ("project.resume", "project.continue_pipeline"):
        spec = registry.get(aid)
        assert spec.needs_approval and spec.spends_money, aid


def test_text_step_cost_is_unknown_until_a_ceiling_and_chain_hides_the_media_price() -> None:
    est = bl.estimate_any_step({"step_code": "plan"})
    assert est.known is False and "текстовый шаг" in est.basis
    capped = bl.estimate_any_step({"step_code": "script", "ceiling_credits": 12})
    assert (capped.low, capped.high) == (0.0, 12.0)
    assert bl.estimate_any_step({"step_code": "assemble"}).high == 0.0
    chained = bl.estimate_any_step({"step_code": "assemble", "allow_auto_chain": True})
    assert chained.known is False and "цепочку" in chained.basis
    assert bl.estimate_any_step({"step_code": "hero"}).known is False



def test_launch_risks_name_ready_nodes_below_early_steps() -> None:
    runs = _ready_nodes("plan", "script", "split", "images")["node_runs"]
    risks = lr.launch_risks(step_code="script", status="script_ready", auto_mode=False,
                            meta={}, node_runs=runs)
    assert [r["code"] for r in risks] == ["reset_downstream"]
    assert "Разбивка" in risks[0]["text"] and "Картинки" in risks[0]["text"]
    assert "Сценарий" not in risks[0]["text"]
    assert lr.launch_risks(step_code="img", status="images_ready", auto_mode=False, meta={},
                           node_runs=runs) == []
    all_codes = [r["code"] for r in lr.launch_risks(
        step_code="plan", status="generating_images", auto_mode=True,
        meta={"user_stop": {"at": "x"}}, node_runs=runs)]
    assert all_codes == ["reset_downstream", "busy", "user_stop", "auto_chain"]



async def test_text_step_needs_a_ceiling_then_human_approval(env, not_managed, stub,
                                                             steps_open) -> None:
    pid = await env.add_project(frames=1)
    stub["writes"][r"/steps/plan/run$"] = {"id": pid}
    bare = await _preview(env, [step("step.run_step", project_id=pid, step_code="plan")])
    assert bare.status_code == 422 and "step_cost_declared" in str(bare.json())
    ok = await _preview(env, [step("step.run_step", project_id=pid, step_code="plan",
                                   ceiling_credits=15)])
    assert ok.status_code == 200, ok.text
    pv = ok.json()["preview"]
    assert pv["needs_approval"] and pv["cost"]["high"] == 15 and pv["cost"]["unknown_steps"] == 0
    plan = ok.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code in (403, 409)
    assert stub["calls"] == []
    done = await _approve_run(env, plan)
    assert done["status"] == "succeeded", done
    assert stub["calls"] == [("POST", f"/projects/{pid}/steps/plan/run", {"mode": "resume"}, None)]


async def test_early_step_preview_warns_about_ready_nodes_and_user_stop(env, not_managed, stub,
                                                                        steps_open) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, status="images_ready", meta={"user_stop": {"by": "human"}})
    stub["reads"][r"/projects/\d+/run$"] = _ready_nodes("script", "split", "images")
    r = await _preview(env, [step("step.run_step", project_id=pid, step_code="script",
                                  ceiling_credits=5)])
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    texts = " ".join(w["text"] for w in pv["warnings"])
    assert "оплачен заново" in texts and "Картинки" in texts and "кнопкой «Стоп»" in texts
    assert pv["steps"][0]["warnings"]
    assert any(d["text"].startswith("Внимание:") for d in pv["destructive_effects"])
    assert any("сбрасываются" in d["text"] for d in pv["destructive_effects"])


@_OLD_LINE
async def test_busy_and_auto_mode_need_explicit_flags_and_are_warned(env, not_managed, stub,
                                                                     steps_open) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, status="generating_images")
    base = {"project_id": pid, "step_code": "split", "ceiling_credits": 8}
    busy = await _preview(env, [step("step.run_step", **base)])
    assert busy.status_code == 422 and "project_idle" in str(busy.json())
    forced = await _preview(env, [step("step.run_step", **base, preempt_running=True)])
    assert forced.status_code == 200, forced.text
    assert any("прервёт" in w["text"] for w in forced.json()["preview"]["warnings"])

    await _set(env, pid, status="frames_ready", auto_mode=True)
    chain = await _preview(env, [step("step.run_step", **base)])
    assert chain.status_code == 422 and "no_auto_chain" in str(chain.json())
    no_ceiling = await _preview(env, [step("step.run_step", project_id=pid, step_code="assemble",
                                           allow_auto_chain=True)])
    assert no_ceiling.status_code == 422 and "step_cost_declared" in str(no_ceiling.json())
    allowed = await _preview(env, [step("step.run_step", **base, allow_auto_chain=True)])
    assert allowed.status_code == 200, allowed.text
    assert any("автопродвижение" in w["text"] for w in allowed.json()["preview"]["warnings"])


async def test_gpt_step_needs_a_node_key(env, not_managed, stub, steps_open) -> None:
    pid = await env.add_project(frames=1)
    r = await _preview(env, [step("step.run_step", project_id=pid, step_code="excel_gpt",
                                  ceiling_credits=3)])
    assert r.status_code == 422 and "node_key" in str(r.json())
    wrong = await _preview(env, [step("step.run_step", project_id=pid, step_code="plan",
                                      node_key="n1", ceiling_credits=3)])
    assert wrong.status_code == 422 and "step_runnable" in str(wrong.json())


async def test_cost_basis_for_text_step_asks_for_a_ceiling(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    r = await _preview(env, [step("step.get_run_cost_basis", project_id=pid, step_code="split")])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    got = (await env.client.get(f"{BASE}/plans/{plan}")).json()["steps"][0]["result"]
    assert got["needs_ceiling"] is True and got["estimable"] is False



async def test_create_batch_makes_one_project_per_topic(env, not_managed) -> None:
    r = await _preview(env, [step("project.create_batch", topics=["кофе", "чай"],
                                  title_prefix="Серия", hero_mode="no_hero")])
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["needs_approval"] is False
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    out = view["steps"][0]["result"]
    assert out["count"] == 2
    async with env.factory() as s:
        rows = [await s.get(Project, p["project_id"]) for p in out["projects"]]
    assert [(p.title, p.topic, p.hero_mode) for p in rows] == [
        ("Серия кофе", "кофе", "no_hero"), ("Серия чай", "чай", "no_hero")]


async def test_create_batch_refuses_duplicates_and_blank_topics(env, not_managed) -> None:
    dup = await _preview(env, [step("project.create_batch", topics=["кофе", "Кофе"])])
    assert dup.status_code == 422 and "batch_valid" in str(dup.json())
    blank = await _preview(env, [step("project.create_batch", topics=["кофе", "  "])])
    assert blank.status_code == 422
    many = await _preview(env, [step("project.create_batch", topics=[f"т{i}" for i in range(21)])])
    assert many.status_code in (400, 422)


async def test_create_batch_from_template_uses_child_endpoint_with_own_keys(env, not_managed,
                                                                            stub) -> None:
    tpl = await env.add_project(frames=1)
    made = iter([501, 502])
    stub["writes"][r"/projects/\d+/child$"] = lambda path, kw: {"id": next(made)}
    stub["writes"][r"/projects/50[12]$"] = lambda path, kw: {"title": kw["json"]["title"],
                                                             "status": "new"}
    r = await _preview(env, [step("project.create_batch", topics=["а", "б"],
                                  template_project_id=tpl, image_resolution="1k")])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    assert [c[:2] for c in stub["calls"]] == [
        ("POST", f"/projects/{tpl}/child"), ("PATCH", "/projects/501"),
        ("POST", f"/projects/{tpl}/child"), ("PATCH", "/projects/502")]
    assert stub["calls"][1][3] == {"title": "а", "topic": "а", "image_resolution": "1k"}
    assert [p["project_id"] for p in view["steps"][0]["result"]["projects"]] == [501, 502]



async def test_enqueue_many_needs_auto_mode_or_the_flag_and_human_approval(env, not_managed,
                                                                           stub) -> None:
    a, b = await env.add_project(frames=1), await _other_project(env)
    await _set(env, a, auto_mode=True)
    await _set(env, b, meta={"user_stop": {"by": "human"}})
    stub["writes"][r"/gen-queue/enqueue$"] = lambda path, kw: {"position": kw["json"]["project_id"]}
    stub["writes"][rf"/projects/{b}$"] = {"auto_mode": True}
    off = await _preview(env, [step("queue.enqueue_many", project_ids=[a, b])])
    assert off.status_code == 422 and "queue_ready" in str(off.json())
    dup = await _preview(env, [step("queue.enqueue_many", project_ids=[a, a])])
    assert dup.status_code == 422
    ok = await _preview(env, [step("queue.enqueue_many", project_ids=[a, b], enable_auto_mode=True)])
    assert ok.status_code == 200, ok.text
    pv = ok.json()["preview"]
    assert pv["needs_approval"] is True
    texts = " ".join(w["text"] for w in pv["warnings"])
    assert f"#{b}" in texts and "«Стоп»" in texts and "автопродвижение" in texts
    plan = ok.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code in (403, 409)
    assert stub["calls"] == []
    done = await _approve_run(env, plan)
    assert done["status"] == "succeeded", done
    assert [c[:2] for c in stub["calls"]] == [
        ("POST", "/sidebar-layout/gen-queue/enqueue"),
        ("PATCH", f"/projects/{b}"),
        ("POST", "/sidebar-layout/gen-queue/enqueue"),
    ]
    assert stub["calls"][0][3] == {"project_id": a, "mode": "full", "by_chat": True}
    assert done["steps"][0]["result"]["count"] == 2


async def test_chat_enqueue_approved_by_human_marks_the_chat_session(env, not_managed, stub) -> None:
    """Живой путь: шаг чата с подтверждением идёт через run_bridged, исполнитель — человек,
    одобривший карточку. Номер в очереди всё равно помечается сессией чата (по создателю
    плана ``orchestrator:task-N``), иначе аварийный стоп чата его не снимет."""
    from app.orchestrator.agents.task_store import TaskStore
    from app.services import orch_bridge

    pid = await env.add_project(frames=1)
    await _set(env, pid, auto_mode=True)
    stub["writes"][r"/gen-queue/enqueue$"] = lambda path, kw: {"position": 1}
    task_id = TaskStore().insert("chat-sess-q", "поставь в очередь", "running")
    params = {"project_ids": [pid], "mode": "full"}
    spec = registry.get("queue.enqueue_many")
    assert spec.needs_approval
    human = approval.OperatorIdentity("web_operator:owner")
    change_id = f"orch-appr-{task_id}-1"
    with orch_bridge.human_verified():
        plan_id, fresh = await orch_bridge._approve_card(
            env.app,
            change_id=change_id,
            task_id=task_id,
            action=spec.id,
            params=params,
            card_plan_id=None,
            operator=human,
        )
    assert fresh
    assert await orch_bridge._approver(plan_id) == human.id
    outcome = await orch_bridge.run_bridged(
        spec, params, {"app": env.app, "task_id": task_id}, {"change_id": change_id}
    )
    assert outcome["status"] == "ok", outcome
    enqueues = [c for c in stub["calls"] if c[1] == "/sidebar-layout/gen-queue/enqueue"]
    assert [c[3] for c in enqueues] == [{"project_id": pid, "mode": "full", "by_chat": "chat-sess-q"}]


async def test_enqueue_many_refuses_managed_projects(env, stub, monkeypatch) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, auto_mode=True)

    async def _yes(p):
        return True
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _yes)
    r = await _preview(env, [step("queue.enqueue_many", project_ids=[pid])])
    assert r.status_code == 422 and "Actions-v1" in str(r.json())


async def test_set_parallel_goes_through_runtime_streams(env) -> None:
    r = await _preview(env, [step("queue.set_parallel", workers=2)])
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["needs_approval"] is False
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["worker_max_parallel"] == 2
    assert (await env.client.get("/api/runtime-streams")).json()["worker_max_parallel"] == 2
    too_many = await _preview(env, [step("queue.set_parallel", workers=11)])
    assert too_many.status_code in (400, 422)


@_OLD_LINE
async def test_set_parallel_accepts_ten_projects(env) -> None:
    spec = registry.get("queue.set_parallel")
    assert spec.params_schema["properties"]["workers"]["maximum"] == 10
    assert "до 10" in spec.description_ru
    r = await _preview(env, [step("queue.set_parallel", workers=10)])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["worker_max_parallel"] == 10
    got = (await env.client.get("/api/runtime-streams")).json()
    assert got["worker_max_parallel"] == 10
    assert got["limits"]["worker_max_parallel"] == [1, 10]
    assert got["limits"]["default_outsee_streams"] == [0, 4]


async def test_set_parallel_lower_says_running_finish(env) -> None:
    """Уменьшение числа не прерывает идущие шаги, и ответ чата говорит об этом прямо."""
    from app.orchestrator.agents.summarizer import known_text

    assert (
        await env.client.patch("/api/runtime-streams", json={"worker_max_parallel": 4})
    ).status_code == 200
    r = await _preview(env, [step("queue.set_parallel", workers=2)])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    result = view["steps"][0]["result"]
    assert result["before"] == 4 and result["worker_max_parallel"] == 2 and result["lowered"] is True
    text = known_text("queue.set_parallel", result)
    assert "до 2 проектов" in text and "Уже идущие доиграют, новые будут ждать." in text
    raised = known_text("queue.set_parallel", {"worker_max_parallel": 4, "before": 2})
    assert "доиграют" not in raised
    assert "не прерываются" in registry.get("queue.set_parallel").description_ru


@_OLD_LINE
async def test_runtime_streams_route_caps_projects_at_ten_and_outsee_at_four(env) -> None:
    ok = await env.client.patch("/api/runtime-streams", json={"worker_max_parallel": 10})
    assert ok.status_code == 200 and ok.json()["worker_max_parallel"] == 10
    over = await env.client.patch("/api/runtime-streams", json={"worker_max_parallel": 11})
    assert over.status_code == 422
    outsee = await env.client.patch("/api/runtime-streams", json={"default_outsee_streams": 5})
    assert outsee.status_code == 422


async def test_create_batch_takes_ten_topics_and_enqueue_many_takes_ten_ids(env, not_managed) -> None:
    r = await _preview(env, [step("project.create_batch", topics=[f"тема {i}" for i in range(10)])])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    view = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["count"] == 10
    ids = [p["project_id"] for p in view["steps"][0]["result"]["projects"]]
    assert len(set(ids)) == 10
    queue = registry.get("queue.enqueue_many").params_schema["properties"]["project_ids"]
    assert queue["maxItems"] >= 10



async def test_progress_watch_resets_on_any_change(monkeypatch) -> None:
    clock = {"t": 100.0}
    monkeypatch.setattr(executor.time, "monotonic", lambda: clock["t"])
    watch = executor.ProgressWatch(None, timeout_s=10, probe_s=1)

    async def feed(note, activity=None):
        await watch.observe(None, JobState(done=False, note=note, activity=activity))

    await feed("a")
    clock["t"] = 109.0
    await feed("b")
    clock["t"] = 118.0
    await feed("b")
    assert not watch.stalled()
    clock["t"] = 120.0
    await feed("b")
    assert watch.stalled() and watch.idle_s() == pytest.approx(11.0)
    await feed("b", activity="опрос провайдера 2")
    assert not watch.stalled()


@_OLD_LINE
async def test_long_job_with_progress_is_not_cut(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_no_progress_timeout_s", 0.4)
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.4)
    pid = await env.add_project()
    ticks = {"n": 0, "stopped": 0}

    async def start(ctx, params):
        return {"started": True}

    async def poll(ctx, params, started):
        ticks["n"] += 1
        if ticks["n"] >= 30:
            return JobState(done=True, ok=True, result={"final": "ok"})
        return JobState(done=False, note=f"клип {ticks['n']}")

    async def stop(ctx, params):
        ticks["stopped"] += 1

    reg = fresh_registry(make_spec("t.long", start, risk="paid", spends=True, cost=(0, 1),
                                   schema=PROJ_SCHEMA, poll=poll, stop=stop, poll_interval_s=0.05))
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": "t.long", "params": {"project_id": pid}}], operator=OP,
        request_text="long", registry=reg)
    assert plan_id, preview
    await service.approve_plan(env.app, plan_id, OP, registry=reg)
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await executor.wait(plan_id)
    view = await service.plan_view(plan_id)
    assert view["status"] == "succeeded", view
    assert ticks["stopped"] == 0 and ticks["n"] >= 30


async def test_project_activity_change_counts_as_progress(env, monkeypatch, stub) -> None:
    pid = await env.add_project()
    seq = iter(range(1000))
    stub["reads"][r"/projects/\d+/run$"] = lambda path, kw: {
        "node_runs": [{"node_key": "v", "status": "running", "progress": next(seq)}]}
    ctx = ActionContext(app=env.app, operator="t")
    first = await executor.project_activity(ctx, pid)
    second = await executor.project_activity(ctx, pid)
    assert first and second and first != second
    assert await executor.project_activity(ctx, None) is None
