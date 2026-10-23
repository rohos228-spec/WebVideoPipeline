"""Перенос int/chat-v1 fx/phrases7 и fx/phrases8 на живом Postgres с двумя арендаторами.

* массовый запуск: проект другого арендатора в просьбе не проверяется условиями запуска
  и не попадает в строку «не поставлены в очередь» — ни номером с причиной, ни названием;
  свои проекты проверяются от имени личности задачи и под арендатором сессии чата;
* «было → станет» правки настроек: чужой проект не читается, «менять нечего» по нему
  не говорится (значение чужого проекта не раскрывается);
* участник студии, запуская несколько проектов, не получает шаг ``queue.set_parallel``
  (окно параллели одно на всю студию, меняет только админ): только очередь и строка
  карточки; админ при той же просьбе получает шаг окна.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py` (``TEST_DATABASE_URL``,
обычная роль). Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


async def _project_of(pg, tenant: str, *, title: str, meta: dict | None = None, **fields) -> int:  # noqa: F811
    from app.models import Project, ProjectStatus
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            p = Project(
                slug=f"ln-{uuid.uuid4().hex[:8]}",
                title=title,
                topic="t",
                status=ProjectStatus.new,
                hero_mode="no_hero",
                auto_mode=True,
                meta=dict(meta or {}),
                **fields,
            )
            s.add(p)
            await s.commit()
            return p.id


def _identity(account):
    from app.services.studio_auth import StudioIdentity

    return StudioIdentity(user_id=account.user_id, email=account.email, role=account.role)


@pytest.fixture
def acting(pg):  # noqa: F811
    """Личность текущего контекста (как у задачи чата); после теста — как было."""
    from app.orchestrator import app_ref
    from app.services.studio_auth import current_identity, set_identity

    prev, prev_app = current_identity(), app_ref.current()
    app_ref.bind(pg["app"])

    def _set(account):
        ident = _identity(account) if account is not None else None
        set_identity(ident)
        return ident

    yield _set
    set_identity(prev)
    app_ref.bind(prev_app)


# Дочерний проект фабрики очередь пропускает при любом enable_auto_mode: жёсткое условие.
_CHILD = {"mass_parent_id": 1}


# ── массовый запуск: чужой проект не проверяется и не раскрывается ───────────


async def test_launch_blockers_skip_other_tenant_project(pg, acting, monkeypatch) -> None:  # noqa: F811
    from app.services.action_layer import launch_split as ls
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    ok_a = await _project_of(pg, a, title="Лисы А")
    bad_a = await _project_of(pg, a, title="Ежи А", meta=_CHILD)
    bad_b = await _project_of(pg, b, title="Секретные совы Б", meta=_CHILD)
    probed: list[int] = []
    real = ls._first_failure

    async def _spy(spec, ctx, params):
        probed.extend(params.get("project_ids") or [params.get("project_id")])
        return await real(spec, ctx, params)

    monkeypatch.setattr(ls, "_first_failure", _spy)
    params = {"project_ids": [ok_a, bad_b, bad_a], "enable_auto_mode": True, "mode": "full"}

    acting(pg["a"])
    with tenant_scope(a):
        found = await ls.launch_blockers("queue.enqueue_many", params)
    assert found is not None
    assert [item["project_id"] for item in found["blocked"]] == [bad_a], found
    assert "дочерний проект фабрики" in found["blocked"][0]["reason_ru"]
    # Проект Б не проверялся вовсе, и о нём в ответе ничего нет.
    assert bad_b not in probed and set(probed) == {ok_a, bad_a}
    assert "Секретные" not in json.dumps(found, ensure_ascii=False) and f"#{bad_b}" not in str(found)

    # Арендатор Б видит свой проект: тот же запрос от его имени проверяет только его.
    probed.clear()
    acting(pg["b"])
    with tenant_scope(b):
        mine = await ls.launch_blockers("queue.enqueue_many", params)
    assert [item["project_id"] for item in mine["blocked"]] == [bad_b]
    assert probed == [bad_b]

    # Режим учётных записей без личности: проверять не от чьего имени — ничего не читаем.
    probed.clear()
    acting(None)
    with tenant_scope(a):
        assert await ls.launch_blockers("queue.enqueue_many", params) is None
    assert probed == []


class _RealBlockers:
    """Действия чата для супервизора: условия запуска — настоящие (``launch_split``)."""

    async def launch_blockers(self, name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        from app.orchestrator.actions.executor import launch_blockers

        return await launch_blockers(name, params)


async def test_mass_launch_drop_keeps_other_tenant_id_unrevealed(pg, acting) -> None:  # noqa: F811
    from app.orchestrator.contracts import PlanStep
    from app.services.orch_tenancy import scoped_session
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    ok_a = await _project_of(pg, a, title="Лисы А")
    ok_a2 = await _project_of(pg, a, title="Волки А")
    bad_a = await _project_of(pg, a, title="Ежи А", meta=_CHILD)
    bad_b = await _project_of(pg, b, title="Секретные совы Б", meta=_CHILD)
    sup = pg["runtime"].supervisor
    step = PlanStep(
        action="queue.enqueue_many",
        params={"project_ids": [ok_a, bad_b, bad_a, ok_a2], "enable_auto_mode": True, "mode": "full"},
        mutating=True,
        risk="high",
    )
    prev = sup.actions
    sup.actions = _RealBlockers()
    try:
        acting(pg["a"])
        # Задача без арендатора в контексте: проверка берёт арендатора из сессии чата.
        with tenant_scope(None):
            out = await sup._drop_blocked_launches(
                987654, [step], f"запусти проекты {ok_a} {bad_b} {bad_a} {ok_a2}", scoped_session("chat", a)
            )
    finally:
        sup.actions = prev
    note = sup._launch_skipped.pop(987654, "")
    # Свой проект с отказом убран, номер Б остаётся в шаге: карточка откажет по нему как
    # по любому неизвестному номеру, а строка заметки его не называет.
    assert out[0].params["project_ids"] == [ok_a, bad_b, ok_a2]
    assert note.startswith(f"Не поставлен в очередь: #{bad_a} — "), note
    named = {int(x) for x in re.findall(r"#(\d+)", note)}
    for group in re.findall(r"«запусти проекты? ([\d, ]+)»", note):
        named |= {int(x) for x in re.findall(r"\d+", group)}
    assert named == {bad_a}, note
    assert "Секретные" not in note


# ── «было → станет» правки настроек: чужой проект не читается ────────────────


async def test_settings_diff_noop_only_for_own_project(pg, acting) -> None:  # noqa: F811
    from app.orchestrator.actions.executor import settings_diff
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_of(pg, a, title="А", image_generator="flux_2_pro")
    pid_b = await _project_of(pg, b, title="Б", image_generator="flux_2_pro")
    patch_a = {"project_id": pid_a, "image_generator": "flux_2_pro"}
    patch_b = {"project_id": pid_b, "image_generator": "flux_2_pro"}

    acting(pg["a"])
    with tenant_scope(a):
        own = await settings_diff("project.patch_settings_costly", patch_a)
        foreign = await settings_diff("project.patch_settings_costly", patch_b)
    assert own is not None and own["unchanged"] is True
    assert own["same_ru"] == "модель картинок уже Flux 2 Pro"
    # Чужой проект не прочитан: ни «менять нечего», ни его текущего значения.
    assert foreign is None

    acting(pg["b"])
    with tenant_scope(b):
        theirs = await settings_diff("project.patch_settings_costly", patch_b)
        other = await settings_diff("project.patch_settings_costly", patch_a)
    assert theirs is not None and theirs["unchanged"] is True
    assert other is None

    # Учётки включены, личности нет — не читаем вовсе.
    acting(None)
    with tenant_scope(a):
        assert await settings_diff("project.patch_settings_costly", patch_a) is None


# ── участник: запуск нескольких проектов без шага окна параллели ─────────────


_META = {
    "queue.enqueue_many": {"risk": "high", "read_only": False, "destructive": False, "paid": False},
    "queue.set_parallel": {"risk": "medium", "read_only": False, "destructive": False, "paid": False},
    "step.run_step": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
}


def _run_step_plan(ids: list[int]):
    from app.orchestrator.contracts import PlanStep

    return [
        PlanStep(
            action="step.run_step",
            params={"project_id": pid, "step_code": "full"},
            mutating=True,
            risk="high",
            paid=True,
        )
        for pid in ids
    ]


async def _launch_as(pg, acting, monkeypatch, account, session: str, ids: list[int]):  # noqa: F811
    from app.orchestrator.agents import supervisor as sup_mod
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.services.tenant import tenant_scope
    from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

    class _Actions(MockActions):
        def metadata_of(self, name: str) -> dict[str, Any]:
            if name not in _META:
                raise KeyError(name)
            return {"name": name, **_META[name]}

    monkeypatch.setenv("ORCH_API_TOKEN", "test-token")
    monkeypatch.setattr(sup_mod, "_queue_limit", lambda: 2)
    rt = build_runtime(
        actions=_Actions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(_run_step_plan(ids)),
        critic=ScriptedCritic([]),
    )
    set_runtime(rt)
    try:
        acting(account)
        with tenant_scope(account.user_id):
            task_id = await rt.supervisor.start_task(
                session, "запусти проекты " + ",".join(str(pid) for pid in ids) + " одновременно"
            )
            await rt.supervisor.wait(task_id, timeout=10)
            saved = json.loads(rt.tasks.get(task_id)["plan_json"])
            cards = [dict(e.payload) for e in rt.bus.replay(session, 0) if e.type == "approval_required"]
    finally:
        set_runtime(pg["runtime"])
    return saved, cards


async def test_member_mass_launch_has_no_set_parallel(pg, acting, monkeypatch) -> None:  # noqa: F811
    from app.services.orch_tenancy import scoped_session

    a = pg["a"].user_id
    ids = [await _project_of(pg, a, title=f"А{n}") for n in range(4)]
    saved, cards = await _launch_as(pg, acting, monkeypatch, pg["a"], scoped_session("ln-m", a), ids)
    assert [step["action"] for step in saved] == ["queue.enqueue_many"], saved
    assert saved[0]["params"]["project_ids"] == ids
    assert cards, "карточки запуска нет"
    warnings = cards[-1].get("warnings") or []
    assert warnings and "только админ" in warnings[0] and "до 2" in warnings[0], warnings
    assert "queue.set_parallel" not in [step.get("action") for step in cards[-1].get("steps") or []]
    assert "$" not in warnings[0]


async def test_admin_mass_launch_keeps_set_parallel(pg, acting, monkeypatch) -> None:  # noqa: F811
    from app.services.orch_tenancy import scoped_session

    admin = pg["admin"].user_id
    ids = [await _project_of(pg, admin, title=f"Адм{n}") for n in range(4)]
    saved, cards = await _launch_as(
        pg, acting, monkeypatch, pg["admin"], scoped_session("ln-adm", admin), ids
    )
    assert [step["action"] for step in saved] == ["queue.set_parallel", "queue.enqueue_many"], saved
    assert saved[0]["params"] == {"workers": 4}
    assert not any("только админ" in w for card in cards for w in card.get("warnings") or [])
