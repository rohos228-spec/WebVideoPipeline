"""Перенос ``app/web/routers/observations.py`` (int/chat-v1 d4413ede9) на новую линию.

Проверяется стык, а не логика сервисов наблюдения (её покрывают их тесты):

* ручки подключены к ``create_app`` и отвечают по путям, которые зовут панель
  «Регистратор» (``web/src/lib/observations.ts``, ``kill-switch-section.tsx``,
  ``spend-section.tsx``, ``journal-api.ts``) и слой действий
  (``builtin_wave5._kill_engage``);
* доступ: без токена 401; участнику — наблюдение и журнал своего проекта;
  аварийный стоп, траты и сверка задач провайдеров — только админу (403);
* аварийный стоп ГЛОБАЛЬНЫЙ: одна строка на сервер, номер проекта в пути её не
  делит; включение видно из любого проекта и в ``kill_switch.is_active``;
* журнал участнику — без сумм в $.

База — временный SQLite, наблюдатель — свой экземпляр на этой базе. Живой
сервер не трогается.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Модели наблюдения, действий, решений, медиа и аварийного стопа — в Base.metadata
# до создания схемы тестовой базы.
from app.services import observation as observation_mod
from app.services import spend_guard  # noqa: F401
from app.services.observation import Observer, observation_tables

observation_tables()

from app.models import Project, ProjectStatus, Workflow, WorkflowRun  # noqa: E402
from app.settings import settings  # noqa: E402
from app.web.api import create_app  # noqa: E402
from app.web.deps import get_session  # noqa: E402
from tests import accounts_harness as ah  # noqa: E402

NODES = [
    {"id": "n1", "type": "excel_gpt", "data": {"label": "Сценарий"}},
    {"id": "n2", "type": "img_pr", "data": {"label": "Картинки"}},
]


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "observations.db")
    ah.bind_identity_session(monkeypatch, factory)
    observer = Observer(factory, body_root=tmp_path / "bodies")
    monkeypatch.setattr(observation_mod, "_observer", observer)
    from app.services import kill_switch

    kill_switch.reset_cache()

    async def _gen():
        async with factory() as s:
            yield s

    async with factory() as s:
        projects = []
        for i in range(2):
            p = Project(topic=f"obs {i}", slug=f"obs-{tmp_path.name[-6:]}-{i}", status=ProjectStatus.new)
            s.add(p)
            projects.append(p)
        await s.flush()
        wf = Workflow(name="wf", nodes=NODES, edges=[])
        s.add(wf)
        await s.flush()
        s.add(
            WorkflowRun(workflow_id=wf.id, project_id=projects[0].id, nodes_snapshot=NODES, edges_snapshot=[])
        )
        await s.commit()
        ids = [p.id for p in projects]

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {
            "client": c,
            "member": member,
            "admin": admin,
            "project": ids[0],
            "other": ids[1],
            "factory": factory,
            "app": app,
        }
    kill_switch.reset_cache()
    await engine.dispose()


def _routes(app) -> set[tuple[str, str]]:
    from fastapi.routing import APIRoute

    out = set()
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for ctx in contexts():
                out |= {(m, ctx.path_format) for m in ctx.methods or ()}
        elif isinstance(route, APIRoute):
            out |= {(m, route.path_format) for m in route.methods or ()}
    return out


def test_registrar_and_action_layer_paths_are_mounted() -> None:
    table = _routes(create_app())
    base = "/api/projects/{project_id}/observations"
    for method, path in [
        ("GET", base),
        ("POST", base),
        ("GET", base + "/kill-switch"),
        ("POST", base + "/kill-switch"),
        ("GET", base + "/spend"),
        ("GET", base + "/provider-tasks"),
        ("GET", base + "/telemetry"),
        ("GET", base + "/stream"),
        ("GET", base + "/node-coverage"),
        ("GET", base + "/launch-options"),
        ("POST", base + "/launch"),
        ("GET", base + "/auto"),
        ("GET", base + "/{execution_id}/context"),
        ("GET", base + "/{execution_id}/events"),
        ("GET", base + "/{execution_id}/diagnoses"),
        ("GET", base + "/{execution_id}/actions"),
        ("POST", base + "/{execution_id}/actions/start"),
        ("GET", base + "/{execution_id}/decisions"),
        ("GET", base + "/{execution_id}/profiles"),
        ("POST", base + "/{execution_id}/stop"),
        ("GET", "/api/projects/{project_id}/timeline"),
        ("GET", "/api/projects/{project_id}/timeline/totals"),
        ("GET", "/api/projects/{project_id}/timeline/export"),
    ]:
        assert (method, path) in table, (method, path)


async def test_reads_need_a_token(env) -> None:
    pid = env["project"]
    for path in (
        f"/api/projects/{pid}/observations",
        f"/api/projects/{pid}/observations/kill-switch",
        f"/api/projects/{pid}/timeline",
    ):
        res = await env["client"].get(path)
        assert res.status_code == 401, (path, res.status_code)


async def test_studio_tools_are_admin_only(env) -> None:
    c, pid, member = env["client"], env["project"], env["member"].auth
    for path in ("kill-switch", "spend", "provider-tasks"):
        res = await c.get(f"/api/projects/{pid}/observations/{path}", headers=member)
        assert res.status_code == 403, (path, res.status_code, res.text[:200])
    res = await c.post(
        f"/api/projects/{pid}/observations/kill-switch",
        json={"active": True, "rationale": "проверка участником"},
        headers=member,
    )
    assert res.status_code == 403, res.text
    from app.services import kill_switch

    async with env["factory"]() as s:
        assert await kill_switch.is_active(s) is False


async def test_kill_switch_is_one_per_server(env) -> None:
    c, pid, other, admin = env["client"], env["project"], env["other"], env["admin"]
    res = await c.get(f"/api/projects/{pid}/observations/kill-switch", headers=admin.auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active"] is False and body["revision"] == 0 and "watchdog" in body

    short = await c.post(
        f"/api/projects/{pid}/observations/kill-switch",
        json={"active": True, "rationale": "x"},
        headers=admin.auth,
    )
    assert short.status_code == 422

    on = await c.post(
        f"/api/projects/{pid}/observations/kill-switch",
        json={"active": True, "rationale": "тест на временной базе"},
        headers=admin.auth,
    )
    assert on.status_code == 200, on.text
    assert on.json()["active"] is True
    assert on.json()["changed_by"] == "studio_user:" + admin.user_id

    # Другой проект видит тот же стоп: состояние одно на сервер.
    seen = (await c.get(f"/api/projects/{other}/observations/kill-switch", headers=admin.auth)).json()
    assert seen["active"] is True and seen["revision"] == 1
    assert seen["history"][0]["actor"] == "studio_user:" + admin.user_id
    from app.services import kill_switch

    async with env["factory"]() as s:
        assert await kill_switch.is_active(s) is True

    off = await c.post(
        f"/api/projects/{other}/observations/kill-switch",
        json={"active": False, "rationale": "снять после теста"},
        headers=admin.auth,
    )
    assert off.status_code == 200 and off.json()["active"] is False
    async with env["factory"]() as s:
        assert await kill_switch.is_active(s) is False


async def test_kill_engage_action_gets_the_answer_it_expects(env) -> None:
    """``kill_switch.engage`` слоя действий: POST {active, rationale}, ждёт active=true."""
    from app.services.action_layer.builtin_wave5 import _kill_engage

    c, pid, admin = env["client"], env["project"], env["admin"]

    class _Ctx:
        async def call(self, method, path, *, json=None, params=None):
            res = await c.request(method, "/api" + path, json=json, params=params, headers=admin.auth)
            assert res.status_code == 200, res.text
            return res.json()

    assert await _kill_engage(_Ctx(), {"project_id": pid, "rationale": "стоп из слоя действий"}) == {
        "active": True
    }
    await c.post(
        f"/api/projects/{pid}/observations/kill-switch",
        json={"active": False, "rationale": "снять после теста"},
        headers=admin.auth,
    )


async def test_unknown_project_is_404(env) -> None:
    res = await env["client"].get("/api/projects/999999/observations/kill-switch", headers=env["admin"].auth)
    assert res.status_code == 404
    res = await env["client"].get("/api/projects/999999/timeline", headers=env["member"].auth)
    assert res.status_code == 404


async def test_spend_and_provider_tasks_for_admin(env) -> None:
    c, pid, admin = env["client"], env["project"], env["admin"].auth
    spend = await c.get(f"/api/projects/{pid}/observations/spend", headers=admin)
    assert spend.status_code == 200, spend.text
    assert {"run_id", "runs"} <= set(spend.json())
    tasks = await c.get(f"/api/projects/{pid}/observations/provider-tasks", headers=admin)
    assert tasks.status_code == 200 and tasks.json()["tasks"] == []


async def test_member_observes_own_project(env) -> None:
    c, pid, member = env["client"], env["project"], env["member"].auth
    base = f"/api/projects/{pid}/observations"
    assert (await c.get(base, headers=member)).json() == []

    bad = await c.post(base, json={"node_keys": ["nope"]}, headers=member)
    assert bad.status_code == 422, bad.text

    made = await c.post(base, json={"node_keys": ["n1"]}, headers=member)
    assert made.status_code == 201, made.text
    execution = made.json()["execution_id"]
    assert made.json()["observer_state"] == "active"
    listed = (await c.get(base, headers=member)).json()
    assert [s["execution_id"] for s in listed] == [execution]

    ctx = await c.get(f"{base}/{execution}/context", headers=member)
    assert ctx.status_code == 200, ctx.text
    data = ctx.json()
    assert data["disagreement"] is False and data["project"] is None
    assert "n1" in data["node_contexts"] and data["business_group_outcome"] in {
        "incomplete",
        "done",
        "failed",
    }
    assert not any(g.get("kind") == "project_database_not_available" for g in data["data_gaps"])

    events = (await c.get(f"{base}/{execution}/events", headers=member)).json()
    assert {"events", "next_cursor", "has_more"} <= set(events)
    actions = (await c.get(f"{base}/{execution}/actions", headers=member)).json()
    assert actions["configured"] is False and actions["records"] == []
    diag = await c.get(f"{base}/{execution}/diagnoses", headers=member)
    assert diag.status_code == 200 and diag.json()["automatic_actions"] is False
    decisions = await c.get(f"{base}/{execution}/decisions", headers=member)
    assert decisions.status_code == 200 and decisions.json()["records"] == []

    assert (await c.get(f"{base}/not-a-scope/context", headers=member)).status_code == 404
    # Чужой номер проекта для существующего наблюдения — 404.
    assert (
        await c.get(f"/api/projects/{env['other']}/observations/{execution}/context", headers=member)
    ).status_code == 404

    stop = await c.post(f"{base}/{execution}/stop", headers=member)
    assert stop.status_code == 200 and stop.json()["observer_state"] == "stopped"


async def test_member_reads_telemetry_coverage_auto_and_launch_options(env) -> None:
    c, pid, member = env["client"], env["project"], env["member"].auth
    base = f"/api/projects/{pid}/observations"
    for path in ("telemetry", "node-coverage", "auto", "launch-options"):
        res = await c.get(f"{base}/{path}", headers=member)
        assert res.status_code == 200, (path, res.text[:300])
    options = (await c.get(f"{base}/launch-options", headers=member)).json()
    assert [n["node_key"] for n in options["nodes"]] == ["n1", "n2"]
    assert len(options["graph_version"]) == 64
    # Без прогона — 404, как в старой линии.
    assert (
        await c.get(f"/api/projects/{env['other']}/observations/launch-options", headers=member)
    ).status_code == 404


async def test_managed_start_without_permission_is_refused(env) -> None:
    c, pid, member = env["client"], env["project"], env["member"].auth
    res = await c.post(
        f"/api/projects/{pid}/observations/x-exec/actions/start",
        headers=member,
        json={
            "node_key": "n1",
            "step_code": "excel_gpt",
            "rationale": "проверка",
            "idempotency_key": "idem-0001",
        },
    )
    assert res.status_code == 409 and "no active managed permission" in res.text


async def _spend_row(env, usd: float) -> None:
    from app.services.spend_guard import SpendLedger

    async with env["factory"]() as s:
        s.add(
            SpendLedger(
                day="2026-11-02",
                kind="spend",
                provider="kie",
                project_id=env["project"],
                source="test",
                usd=usd,
                note="тест",
            )
        )
        await s.commit()


async def test_journal_hides_money_from_members(env) -> None:
    await _spend_row(env, 0.1234)
    c, pid = env["client"], env["project"]
    admin_items = (await c.get(f"/api/projects/{pid}/timeline", headers=env["admin"].auth)).json()["items"]
    member_res = await c.get(f"/api/projects/{pid}/timeline", headers=env["member"].auth)
    assert member_res.status_code == 200, member_res.text
    member_items = member_res.json()["items"]
    admin_spend = [i for i in admin_items if i["kind"] == "spend"]
    member_spend = [i for i in member_items if i["kind"] == "spend"]
    assert admin_spend and admin_spend[0]["amount_usd"] == 0.1234
    assert member_spend and "amount_usd" not in member_spend[0]
    assert "0.1234" not in member_res.text

    totals_admin = (await c.get(f"/api/projects/{pid}/timeline/totals", headers=env["admin"].auth)).json()
    totals_member = (await c.get(f"/api/projects/{pid}/timeline/totals", headers=env["member"].auth)).json()
    assert isinstance(totals_admin["spend"], dict) and totals_member["spend"] is None
    assert {"counts", "disk"} <= set(totals_member)

    csv_member = await c.get(f"/api/projects/{pid}/timeline/export?format=csv", headers=env["member"].auth)
    assert csv_member.status_code == 200 and "0.1234" not in csv_member.text
    csv_admin = await c.get(f"/api/projects/{pid}/timeline/export?format=csv", headers=env["admin"].auth)
    assert csv_admin.status_code == 200 and "0.1234" in csv_admin.text
    bad = await c.get(f"/api/projects/{pid}/timeline/export?format=xml", headers=env["member"].auth)
    assert bad.status_code == 422


def test_journal_refuses_postgres_explicitly(monkeypatch) -> None:
    import pytest
    from fastapi import HTTPException

    from app.web.routers import observations

    monkeypatch.setattr(type(settings), "is_postgres", property(lambda self: True))
    with pytest.raises(HTTPException) as exc:
        observations._timeline_supported()
    assert exc.value.status_code == 501
