"""Ручки движка подключены к приложению новой линии и закрыты для всех, кроме админа.

Движок (чат оркестратора, слой действий, контроль изменений, отчёты проектов)
перенесён из video-pipeline int/chat-v1. Здесь проверяется не логика движка —
её покрывают тесты action_layer/change_control/agents, — а стык с новой линией:

* каждый роутер движка достижим через ``create_app`` и отвечает админу;
* без токена — 401 от ``IdentityMiddleware``; участнику — 404 (как всем
  инструментам владельца: существование ручки тоже сведения);
* пути движка не совпадают с ручками новой линии и не перехвачены ими;
* фоновый старт движка (закрытие прерванного, истечение одобрений) идёт по
  ``ENGINE_STARTUP``, по умолчанию включён, а таблицы движка создаются всегда;
* таблицы движка учтены в списках арендатора — иначе на Postgres
  ``assert_rls_or_die`` роняет старт;
* шаг плана в режиме учётных записей проходит ``IdentityMiddleware`` на
  внутреннем вызове от имени того, кто запустил план, и не выше.
"""

from __future__ import annotations

from collections import Counter

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

# Импорт до `make_engine`: модели слоя действий и контроля изменений должны
# попасть в `Base.metadata` раньше, чем тестовая база создаст схему.
import app.services.action_layer.attachments  # noqa: F401
import app.services.action_layer.router  # noqa: F401
import app.services.change_control.router  # noqa: F401
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.settings import Settings, settings
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

REPORT_NAME = "projects_report_mount.md"

#: Ручка чтения на каждый подключённый роутер движка и то, по чему видно, что
#: ответил именно он, а не чужой маршрут с похожим путём.
ENGINE_READS: dict[str, tuple[str, str]] = {
    "action_layer": ("/api/action-layer/catalog", "actions"),
    "action_layer_db": ("/api/action-layer/plans", "items"),
    "action_layer_attachments": ("/api/action-layer/attachments/rules", ""),
    "change_control": ("/api/change-control/allow-list", ""),
    "change_control_db": ("/api/change-control/proposals", "items"),
    "orchestrator_chat": ("/api/orchestrator/chat/mount-check/status", "session"),
    "project_reports": (f"/api/projects/reports/{REPORT_NAME}", ""),
}

ENGINE_PREFIXES = ("/api/action-layer", "/api/change-control", "/api/orchestrator/chat")


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """Режим учётных записей: админ, участник, рантайм оркестратора без моделей."""
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "engine-mount.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    # Рантайм на заглушках: сборка прод-рантайма тянет провайдеров моделей,
    # а чтение статуса сессии их не трогает.
    set_runtime(build_runtime())
    reports = settings.data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / REPORT_NAME).write_text("# отчёт\n", encoding="utf-8")

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "admin": admin, "app": app}
    set_runtime(None)
    await engine.dispose()


@pytest.mark.parametrize("name", sorted(ENGINE_READS))
async def test_engine_read_needs_a_token(env, name) -> None:
    path, _ = ENGINE_READS[name]
    res = await env["client"].get(path)
    assert res.status_code == 401, f"{path} без токена отдал {res.status_code}"


@pytest.mark.parametrize("name", sorted(ENGINE_READS))
async def test_engine_read_is_closed_to_members(env, name) -> None:
    """Участнику — 404, как у всех инструментов владельца в новой линии.

    Для /api/action-layer, /api/change-control, /api/orchestrator отказ даёт
    `IdentityMiddleware` (префиксов нет в TENANT_ALLOWED_PREFIXES), для отчётов
    под открытым участникам /api/projects — зависимость роутера.
    """
    path, _ = ENGINE_READS[name]
    res = await env["client"].get(path, headers=env["member"].auth)
    assert res.status_code == 404, f"{path} отдал участнику {res.status_code}"


@pytest.mark.parametrize("name", sorted(ENGINE_READS))
async def test_engine_read_answers_the_admin(env, name) -> None:
    path, key = ENGINE_READS[name]
    res = await env["client"].get(path, headers=env["admin"].auth)
    assert res.status_code == 200, f"{path} отдал админу {res.status_code}: {res.text[:300]}"
    if key:
        assert key in res.json(), f"{path} ответил не ручкой движка: {res.text[:300]}"


async def test_engine_writes_are_not_swallowed_by_the_api_catch_all(env) -> None:
    """POST в ручку движка доходит до неё, а не до `api_write_not_found`.

    Пустое тело — 422 от проверки модели; 404 означал бы, что роутер подключён
    после catch-all или не подключён вовсе.
    """
    for path in ("/api/change-control/proposals", "/api/action-layer/reserves/release"):
        res = await env["client"].post(path, json={}, headers=env["admin"].auth)
        assert res.status_code == 422, f"{path}: {res.status_code} {res.text[:200]}"


async def test_router_dependency_refuses_a_member_even_past_the_middleware() -> None:
    """Вторая стена: если путь когда-нибудь откроют участникам, роутер не пустит."""
    from starlette.requests import Request

    from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER, StudioIdentity
    from app.web.engine_access import require_engine_operator

    def _req(role: str | None) -> Request:
        scope = {"type": "http", "headers": [], "path": "/api/action-layer/catalog"}
        if role is not None:
            scope["studio_identity"] = StudioIdentity(
                user_id="00000000-0000-0000-0000-000000000001",
                email="x@studio.local",
                role=role,
            )
        return Request(scope)

    old = settings.studio_session_secret
    settings.studio_session_secret = ah.SECRET
    try:
        with pytest.raises(HTTPException) as no_one:
            await require_engine_operator(_req(None))
        assert no_one.value.status_code == 401
        with pytest.raises(HTTPException) as member:
            await require_engine_operator(_req(ROLE_MEMBER))
        assert member.value.status_code == 404
        assert await require_engine_operator(_req(ROLE_ADMIN)) is None
    finally:
        settings.studio_session_secret = old


# ── маршруты ────────────────────────────────────────────────────────────────


def _route_table(app) -> list[tuple[str, str, str]]:
    """(метод, путь, модуль обработчика) для всех HTTP-маршрутов приложения.

    FastAPI до 0.13x держит маршруты плоским списком `APIRoute`, новые версии —
    обёртками подключённых роутеров с `effective_route_contexts()`. Тест обязан
    видеть оба вида, иначе на одной из версий он проверял бы пустой список.
    """
    from fastapi.routing import APIRoute

    out: list[tuple[str, str, str]] = []

    def _add(path: str, methods, endpoint) -> None:
        for method in methods or ():
            out.append((method, path, getattr(endpoint, "__module__", "")))

    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for ctx in contexts():
                _add(ctx.path_format, ctx.methods, ctx.endpoint)
        elif isinstance(route, APIRoute):
            _add(route.path_format, route.methods, route.endpoint)
    return out


def test_engine_routes_do_not_collide_with_the_new_line() -> None:
    table = _route_table(create_app())
    engine_rows = [row for row in table if row[1].startswith(ENGINE_PREFIXES)]
    assert len(engine_rows) > 20, "ручки движка не подключены"

    dup = [key for key, n in Counter((m, p) for m, p, _ in table).items() if n > 1]
    assert dup == [], f"один и тот же метод и путь у двух ручек: {dup}"

    engine_modules = (
        "app.services.action_layer.",
        "app.services.change_control.",
        "app.web.routers.orchestrator_chat",
    )
    foreign = [row for row in engine_rows if not row[2].startswith(engine_modules)]
    assert foreign == [], f"под префиксом движка чужие ручки: {foreign}"


def test_engine_routes_are_admin_guarded_in_code() -> None:
    """Каждая ручка движка берёт проверку роли, а не надеется на middleware."""
    from fastapi.routing import APIRoute

    from app.services.action_layer.router import router as al_router
    from app.services.change_control.router import router as cc_router
    from app.web.engine_access import engine_operator_token, require_engine_operator

    def _calls(dependant) -> set:
        seen = {dependant.call}
        for sub in dependant.dependencies:
            seen |= _calls(sub)
        return seen

    for router in (al_router, cc_router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            calls = _calls(route.dependant)
            assert engine_operator_token in calls or require_engine_operator in calls, route.path


# ── старт ───────────────────────────────────────────────────────────────────


def test_engine_startup_is_on_by_default(monkeypatch) -> None:
    """Закрытие прерванного и истечение одобрений — защита, а не опция."""
    monkeypatch.delenv("ENGINE_STARTUP", raising=False)
    assert Settings.model_fields["engine_startup"].default is True
    assert Settings(_env_file=None).engine_startup is True


async def test_engine_startup_off_touches_nothing(monkeypatch) -> None:
    from app.orchestrator.agents import runtime as rt_mod
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", False)

    def _boom():
        raise AssertionError("рантайм движка поднят при ENGINE_STARTUP=0")

    monkeypatch.setattr(rt_mod, "get_runtime", _boom)
    assert await api._start_engine() is None


class _FakeSupervisor:
    def __init__(self) -> None:
        self.settled = 0

    async def settle_after_restart(self) -> list[int]:
        self.settled += 1
        return [7]


class _FakeRuntime:
    def __init__(self) -> None:
        self.supervisor = _FakeSupervisor()
        self.started = 0

    async def start(self) -> list[int]:
        self.started += 1
        return [3]


@pytest.mark.parametrize("recover", [False, True])
async def test_engine_startup_on_settles_or_recovers_and_starts_expiry(monkeypatch, recover) -> None:
    from app.orchestrator.agents import approval_expiry
    from app.orchestrator.agents import runtime as rt_mod
    from app.services import orch_bridge
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", True)
    if recover:
        monkeypatch.setenv("ORCH_RECOVER_ON_START", "1")
    else:
        monkeypatch.delenv("ORCH_RECOVER_ON_START", raising=False)
    fake = _FakeRuntime()
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: fake)
    plans_closed: list[bool] = []

    async def _close():
        plans_closed.append(True)
        return ["p1"]

    monkeypatch.setattr(orch_bridge, "close_interrupted_plans", _close)
    sentinel = object()
    started_with: list[object] = []

    def _expiry(supervisor):
        started_with.append(supervisor)
        return sentinel

    monkeypatch.setattr(approval_expiry, "start_approval_expiry", _expiry)

    assert await api._start_engine() is sentinel
    assert started_with == [fake.supervisor]
    if recover:
        assert (fake.started, fake.supervisor.settled, plans_closed) == (1, 0, [])
    else:
        assert (fake.started, fake.supervisor.settled, plans_closed) == (0, 1, [True])


async def test_engine_startup_failure_does_not_stop_the_studio(monkeypatch) -> None:
    from app.orchestrator.agents import approval_expiry
    from app.orchestrator.agents import runtime as rt_mod
    from app.web import api

    monkeypatch.setattr(settings, "engine_startup", True)
    monkeypatch.delenv("ORCH_RECOVER_ON_START", raising=False)

    def _broken():
        raise RuntimeError("нет базы движка")

    monkeypatch.setattr(rt_mod, "get_runtime", _broken)
    monkeypatch.setattr(approval_expiry, "start_approval_expiry", lambda s: None)
    assert await api._start_engine() is None


ENGINE_TABLES = {
    "action_layer_plans",
    "action_layer_steps",
    "action_layer_history",
    "action_layer_attachments",
    "change_proposals",
    "config_versions",
    "config_pointers",
}


async def test_engine_schema_is_created_on_an_empty_database(tmp_path, monkeypatch) -> None:
    import app.db as app_db
    from app.web import api

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr(app_db, "engine", engine)
    try:
        await api._ensure_engine_schema()
        await api._ensure_engine_schema()  # повтор не падает
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    assert tables >= ENGINE_TABLES, ENGINE_TABLES - tables


async def _lifespan(app) -> list[dict]:
    received = [{"type": "lifespan.startup"}]
    sent: list[dict] = []

    async def receive():
        return received.pop(0) if received else {"type": "lifespan.shutdown"}

    async def send(message):
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    return sent


async def test_studio_starts_with_engine_mounted_and_background_off(monkeypatch) -> None:
    """Полный lifespan в режиме владельца: старт и остановка без отказа.

    Таблицы движка на месте (ручки рабочие), а фоновая часть движка не
    запускалась — флаг выключен явно (ENGINE_STARTUP=0).
    """
    import app.db as app_db
    from app.orchestrator.agents import runtime as rt_mod

    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "engine_startup", False)

    def _boom():
        raise AssertionError("рантайм движка поднят при ENGINE_STARTUP=0")

    monkeypatch.setattr(rt_mod, "get_runtime", _boom)

    sent = await _lifespan(create_app())
    kinds = [m.get("type") for m in sent]
    assert "lifespan.startup.complete" in kinds, sent
    assert "lifespan.startup.failed" not in kinds, sent

    async with app_db.engine.connect() as conn:
        tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    assert tables >= ENGINE_TABLES, ENGINE_TABLES - tables


async def test_studio_starts_with_engine_background_on_by_default(monkeypatch) -> None:
    """Флаг не трогаем: прерванное закрывается, истечение одобрений запущено и
    гасится на остановке."""
    import asyncio

    from app.orchestrator.agents import approval_expiry
    from app.orchestrator.agents import runtime as rt_mod
    from app.services import orch_bridge

    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "engine_startup", Settings.model_fields["engine_startup"].default)
    monkeypatch.delenv("ORCH_RECOVER_ON_START", raising=False)
    fake = _FakeRuntime()
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: fake)

    async def _close():
        return []

    monkeypatch.setattr(orch_bridge, "close_interrupted_plans", _close)
    loops: list[asyncio.Task] = []

    def _expiry(supervisor):
        task = asyncio.create_task(asyncio.sleep(3600), name="orch-approval-expiry-test")
        loops.append(task)
        return task

    monkeypatch.setattr(approval_expiry, "start_approval_expiry", _expiry)

    sent = await _lifespan(create_app())
    kinds = [m.get("type") for m in sent]
    assert "lifespan.startup.complete" in kinds, sent
    assert fake.supervisor.settled == 1
    assert len(loops) == 1 and loops[0].cancelled()


# ── изоляция арендаторов: таблицы движка учтены ─────────────────────────────


def test_engine_tables_are_classified_for_the_rls_startup_check() -> None:
    """На Postgres `assert_rls_or_die` падает на любой неучтённой таблице.

    Модели движка регистрируются в `Base.metadata` при `create_app` (роутеры)
    и при импорте модулей движка, поэтому сверка идёт после них, а не на
    голых моделях новой линии, как в `test_tenant_isolation`.
    """
    import importlib

    from app.models import Base
    from app.services.tenant_tables import (
        ENGINE_TABLES,
        INFRA_TABLES,
        TENANT_TABLES,
        all_isolated,
        check_coverage,
    )

    create_app()
    for name in (
        "app.services.actions_recovery",
        "app.services.actions_v1",
        "app.services.decision_config",
        "app.services.decision_state",
        "app.services.diagnosis",
        "app.services.kill_switch",
        "app.services.managed_launch",
        "app.services.media_jobs",
        "app.services.observation",
        "app.services.outsee_idempotency",
        "app.services.profiles_state",
        "app.services.spend_guard",
    ):
        importlib.import_module(name)
    assert check_coverage() == []

    stale = sorted(ENGINE_TABLES - set(Base.metadata.tables))
    assert stale == [], f"в ENGINE_TABLES таблицы, которых нет в моделях: {stale}"
    assert not ENGINE_TABLES & (set(all_isolated()) | INFRA_TABLES)
    with_tenant = sorted(t for t in ENGINE_TABLES if "tenant_id" in Base.metadata.tables[t].c)
    assert with_tenant == [], f"у таблицы движка есть tenant_id, её место в TENANT_TABLES: {with_tenant}"
    assert not set(TENANT_TABLES) & ENGINE_TABLES


async def test_rls_startup_check_passes_coverage_with_engine_mounted(monkeypatch) -> None:
    """Тот же путь, что в lifespan на Postgres, минус живая база.

    Состояние политик подставлено «всё в порядке»: проверяется ровно то, что
    раньше роняло старт, — список забытых таблиц после подключения движка.
    """
    from app.services import rls_check

    async def _clean(session):
        return rls_check.RlsReport(dialect="postgresql", role="studio_app")

    monkeypatch.setattr(rls_check, "check_rls", _clean)
    create_app()
    await rls_check.assert_rls_or_die(None)


# ── внутренние вызовы шагов плана в режиме учётных записей ──────────────────


def _whoami_spec(action_id: str):
    """Шаг, который делает то же, что любой HTTP-шаг: зовёт API студии изнутри."""
    from app.services.action_layer.spec import ActionSpec

    async def _run(ctx, params):
        me = await ctx.call("GET", "/me")
        return {"email": me.get("email"), "role": me.get("role")}

    return ActionSpec(
        id=action_id, title_ru="Кто я", description_ru="Проверка личности внутреннего вызова",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        executor=_run, risk="read")


async def _run_plan_as(env, operator_id: str, *, identity):
    """Создать, одобрить и выполнить план из одного шага `t.whoami`.

    `identity` — личность задачи, в которой план запускается: личность
    запроса админа или `None` для фоновой задачи без запроса.
    """
    import asyncio

    from app.services.action_layer import approval, executor, service
    from app.services.action_layer.spec import ActionRegistry
    from app.services.studio_auth import set_identity

    reg = ActionRegistry()
    reg.register(_whoami_spec("t.whoami"))
    who = approval.OperatorIdentity(operator_id)
    plan_id, preview = await service.create_plan(
        env["app"], [{"action_id": "t.whoami", "params": {}}], operator=who,
        request_text="кто я", registry=reg)
    assert plan_id, preview
    if preview.get("needs_approval"):
        await service.approve_plan(env["app"], plan_id, who, registry=reg)
    await service.prepare_run(plan_id, who, registry=reg)

    async def _launch():
        set_identity(identity)
        executor.launch(env["app"], plan_id, who.id, registry=reg)

    await asyncio.create_task(_launch())
    await executor.wait(plan_id)
    return await service.plan_view(plan_id)


def _identity_of(account):
    from app.services.studio_auth import StudioIdentity

    return StudioIdentity(user_id=account.user_id, email=account.email, role=account.role)


async def test_plan_step_calls_the_api_as_the_admin_who_started_it(env) -> None:
    admin = env["admin"]
    view = await _run_plan_as(env, f"studio_user:{admin.user_id}", identity=_identity_of(admin))
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"] == {"email": admin.email, "role": "admin"}


async def test_background_plan_step_uses_the_live_admin_operator(env) -> None:
    """После запроса личности в задаче нет — берётся оператор плана из базы."""
    admin = env["admin"]
    view = await _run_plan_as(env, f"studio_user:{admin.user_id}", identity=None)
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"] == {"email": admin.email, "role": "admin"}


OPERATORS_WITHOUT_ADMIN = {
    "member": None,  # подставляется из фикстуры
    "nobody": "studio_user:00000000-0000-4000-8000-000000000999",
    "orchestrator": "orchestrator:task-1",
}


@pytest.mark.parametrize("who", sorted(OPERATORS_WITHOUT_ADMIN))
async def test_background_plan_step_is_not_elevated(env, who) -> None:
    """Участник, неизвестный и безличный оператор не получают токен админа."""
    operator = OPERATORS_WITHOUT_ADMIN[who] or f"studio_user:{env['member'].user_id}"
    view = await _run_plan_as(env, operator, identity=None)
    assert view["status"] == "failed", view
    assert "без личности" in str(view["steps"][0].get("error")), view


async def test_disabled_admin_loses_the_internal_call(env) -> None:
    """Отключение учётки действует и на план, запущенный до отключения."""
    from sqlalchemy import update

    import app.db as app_db
    from app.models import StudioUser

    admin = env["admin"]
    async with app_db.SessionLocal() as s:
        await s.execute(update(StudioUser).where(StudioUser.id == admin.user_id).values(is_active=False))
        await s.commit()
    view = await _run_plan_as(env, f"studio_user:{admin.user_id}", identity=None)
    assert view["status"] == "failed", view


async def test_internal_call_restores_the_task_identity(env) -> None:
    """Middleware внутреннего вызова не оставляет задаче чужую личность и арендатора."""
    from app.services.action_layer.context import ActionContext
    from app.services.studio_auth import current_identity, set_identity
    from app.services.tenant import current_tenant, set_tenant

    admin = env["admin"]
    set_identity(None)
    set_tenant(None)
    ctx = ActionContext(app=env["app"], operator=f"studio_user:{admin.user_id}", read_only=True)
    me = await ctx.call("GET", "/me")
    assert me["email"] == admin.email
    assert current_identity() is None and current_tenant() is None


async def test_plan_run_through_the_router_reaches_the_api_in_accounts_mode(env) -> None:
    """Сквозной путь: админ создаёт, одобряет и запускает план HTTP-ручками."""
    from app.services.action_layer import executor
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    registry.register(_whoami_spec("t.whoami_http"))
    try:
        c, auth = env["client"], env["admin"].auth
        res = await c.post("/api/action-layer/plans/preview", headers=auth,
                           json={"steps": [{"action_id": "t.whoami_http", "params": {}}]})
        assert res.status_code == 200, res.text
        plan_id = res.json()["plan_id"]
        if res.json()["preview"].get("needs_approval"):
            ok = await c.post(f"/api/action-layer/plans/{plan_id}/approve", headers=auth)
            assert ok.status_code == 200, ok.text
        run = await c.post(f"/api/action-layer/plans/{plan_id}/run", headers=auth)
        assert run.status_code == 200, run.text
        await executor.wait(plan_id)
        view = (await c.get(f"/api/action-layer/plans/{plan_id}", headers=auth)).json()
    finally:
        registry.unregister("t.whoami_http")
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"] == {"email": env["admin"].email, "role": "admin"}
