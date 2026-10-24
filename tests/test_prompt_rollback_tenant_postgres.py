"""Правка промта из чата и её откат на живом Postgres с двумя арендаторами.

Предложение, применённая версия и журнал чата (шаги change.apply задач беседы) — данные
арендатора: таблицы change control и слоя действий закрыты RLS. Здесь проверяется то, что
можно проверить только сервером:

* «примени правку промта» у арендатора Б не видит ожидающего предложения А;
* «откати правку промта» у Б не находит применённой версии А, а прямой откат по её цели —
  отказ (текущей версии для Б нет), и промт А не меняется;
* у А откат находится по словам человека и возвращает промт к исходному тексту, все версии
  записаны от имени А.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py` (``TEST_DATABASE_URL``,
обычная роль). Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from tests.change_control_support import HUMAN, FakeGateway, prompts_root  # noqa: F401
from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import _scalar_as, pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")

CHAT = "orchestrator:task-7"
NODE = "n_plan"


class _Gateway(FakeGateway):
    def __init__(self, project_id: int) -> None:
        super().__init__()
        self.project_id = project_id
        self.state["id"] = project_id

    async def get(self, project_id: int) -> dict:
        return await super().get(1 if project_id == self.project_id else -1)

    async def patch(self, project_id: int, body: dict) -> None:
        assert project_id == self.project_id
        await super().patch(1, body)


async def _journal(result: dict) -> None:
    """Шаг change.apply задачи чата — как его пишет исполнитель слоя действий."""
    from app.services.action_layer import store

    plan_id = uuid.uuid4().hex
    async with store.sessions()() as s:
        s.add(store.ActionPlan(id=plan_id, operator=CHAT, plan_hash=uuid.uuid4().hex,
                               status=store.PLAN_SUCCEEDED, risk_max="destructive", needs_approval=True))
        s.add(store.ActionPlanStep(plan_id=plan_id, idx=0, action_id="change.apply",
                                   params={"proposal_id": result["proposal_id"]},
                                   status=store.STEP_SUCCEEDED, idempotency_key=uuid.uuid4().hex,
                                   result=result, finished_at=datetime.utcnow()))
        await s.commit()


async def test_prompt_apply_and_rollback_are_tenant_scoped(pg, prompts_root) -> None:  # noqa: F811
    from app.services.action_layer import chat_proposals, chat_undo
    from app.services.action_layer.builtin_change import version_ref
    from app.services.change_control import service
    from app.services.change_control.errors import ChangeRefused
    from app.services.tenant import tenant_scope

    a, b = pg["a"].user_id, pg["b"].user_id
    pid = 900000 + uuid.uuid4().int % 90000
    gw = _Gateway(pid)

    with tenant_scope(a):
        original = (await service.prompt_base(gw, pid, NODE))["text"]
        view = await service.propose(gw, tier="T2", kind="prompt_text", project_id=pid, node_key=NODE,
                                     rationale="короче", proposer=CHAT,
                                     candidate={"text": original.rstrip("\n") + "\nПиши коротко."})
        assert [item["id"] for item in await chat_proposals.pending([CHAT])] == [view["id"]]
        plan = await chat_proposals.resolve("примени правку промта", operators=[CHAT])
        assert [s["action"] for s in plan.steps] == ["change.apply"]

    with tenant_scope(b):
        assert await chat_proposals.pending([CHAT]) == []
        plan = await chat_proposals.resolve("примени правку промта", operators=[CHAT])
        assert plan.steps == [] and "нечего" in plan.reason

    with tenant_scope(a):
        out = await service.apply(gw, view["id"], view["digest"], approved_by=HUMAN)
        out["snapshot_ref"] = version_ref(out["target"], out["previous_version"])
        await _journal(out)
        changed = (await service.prompt_base(gw, pid, NODE))["text"]
        assert changed.rstrip().endswith("Пиши коротко.")
        plan = await chat_undo.resolve("откати правку промта", operators=[CHAT])
        assert plan is not None and [s["action"] for s in plan.steps] == ["change.rollback"]
        assert plan.steps[0]["params"] == {"target": out["target"], "expected_version": out["version"]}

    with tenant_scope(b):
        assert await chat_proposals.chat_changes(operators=[CHAT]) == []
        plan = await chat_undo.resolve("откати правку промта", operators=[CHAT])
        assert plan is None or plan.steps == []
        with pytest.raises(ChangeRefused):
            await service.rollback(gw, out["target"], out["version"], approved_by=HUMAN)
        assert await _scalar_as(pg["factory"], b, "select count(*) from config_versions where target = :t",
                                t=out["target"]) == 0

    with tenant_scope(a):
        assert (await service.prompt_base(gw, pid, NODE))["text"] == changed
        rolled = await service.rollback(gw, out["target"], out["version"], approved_by=HUMAN)
        assert rolled["version"] == out["version"] + 1
        assert (await service.prompt_base(gw, pid, NODE))["text"] == original
    sql = "select count(*) from config_versions where target = :t and tenant_id = :a"
    assert await _scalar_as(pg["factory"], a, sql, t=out["target"], a=a) == 3
    sql = "select count(*) from change_proposals where id = :p and tenant_id = :a"
    assert await _scalar_as(pg["factory"], a, sql, p=view["id"], a=a) == 1
