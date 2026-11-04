"""Чат открыт участникам студии: сведения всей студии им не отдаются.

После того как чат оркестратора и слой действий открыли роли `member`,
два чтения остались студийными:

* `logs.tail` без проекта — хвост общего лога сервера, где строки ВСЕХ
  клиентов (номера проектов, слаги, брифы);
* `spend.summary` — общая касса провайдеров (`spend_ledger`, без RLS):
  траты всей студии, потолки и себестоимость вызовов.

Себестоимость не показывается участнику и в карточках нод и отчётах
(`report.nodes`, `node.output`, `report.projects`) — та же ловушка, что
`/api/llm-costs` (`app/web/identity.py`). Здесь — механика без базы;
сквозная проверка через HTTP на живом Postgres —
`tests/test_orch_tenant_postgres.py`.
"""

from __future__ import annotations

import pytest

from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER, StudioIdentity, set_identity
from app.services.tenant import tenant_scope

MEMBER = StudioIdentity(
    user_id="00000000-0000-0000-0000-00000000000a", email="a@studio.local", role=ROLE_MEMBER
)
ADMIN = StudioIdentity(
    user_id="00000000-0000-0000-0000-0000000000ad", email="ad@studio.local", role=ROLE_ADMIN
)
FOREIGN_LINE = (
    "2026-10-22 10:00:00 | ERROR | project #77 tenant-b-secret-slug failed: prompt 'B confidential brief'"
)
OWN_LINE = "2026-10-22 10:00:01 | ERROR | project #5 render failed"


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    """Режим учётных записей и папка данных с общим логом сервера."""
    from app.settings import settings
    from tests import accounts_harness as ah

    ah.configure(monkeypatch)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "backend.log").write_text(f"{FOREIGN_LINE}\n{OWN_LINE}\n", encoding="utf-8")
    yield
    set_identity(None)


def _as(identity: StudioIdentity | None, tenant: str | None):
    set_identity(identity)
    return tenant_scope(tenant)


def _spec(action_id: str):
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    spec = registry.find(action_id)
    assert spec is not None
    return spec


def test_member_view_matrix(accounts) -> None:
    from app.services.action_layer.plan import member_view

    with _as(MEMBER, MEMBER.user_id):
        assert member_view() is True
    with _as(ADMIN, ADMIN.user_id):
        assert member_view() is False
    # Проход владельца в фоне: ни личности, ни арендатора.
    with _as(None, None):
        assert member_view() is False
    # Арендатор без личности (учётка отключена) — как участник.
    with _as(None, MEMBER.user_id):
        assert member_view() is True


def test_owner_mode_has_no_member(monkeypatch) -> None:
    from app.services.action_layer.plan import admin_only_refused, member_view
    from app.settings import settings

    monkeypatch.setattr(settings, "studio_session_secret", "")
    with _as(MEMBER, MEMBER.user_id):
        assert member_view() is False
        assert admin_only_refused("spend.summary", {}) is False
    set_identity(None)


def test_admin_only_refused_by_action_and_params(accounts) -> None:
    from app.services.action_layer.plan import ADMIN_ONLY_MESSAGE, ADMIN_ONLY_READ_MESSAGE, admin_only_message
    from app.services.action_layer.plan import admin_only_refused as refused

    with _as(MEMBER, MEMBER.user_id):
        assert refused("kill_switch.engage", {}) is True
        assert refused("spend.summary", {}) is True
        assert refused("spend.summary", {"project_id": 5}) is True
        assert refused("logs.tail", {"kind": "all"}) is True
        assert refused("logs.tail", None) is True
        assert refused("logs.tail", {"project_id": 5}) is False
        assert refused("project.status", {"project_id": 5}) is False
    with _as(ADMIN, ADMIN.user_id):
        for action_id in ("kill_switch.engage", "spend.summary", "logs.tail"):
            assert refused(action_id, {}) is False
    assert admin_only_message("kill_switch.engage") == ADMIN_ONLY_MESSAGE
    assert admin_only_message("logs.tail") == ADMIN_ONLY_READ_MESSAGE


async def test_member_cannot_tail_the_server_log(accounts) -> None:
    """Без проекта лог общий: участнику отказ, строка чужого клиента не уходит."""
    spec = _spec("logs.tail")
    with _as(MEMBER, MEMBER.user_id):
        with pytest.raises(PermissionError, match="только админу"):
            await spec.executor(None, {"kind": "all"})
    with _as(ADMIN, ADMIN.user_id):
        out = await spec.executor(None, {"kind": "all"})
    texts = " ".join(row["text"] for row in out["lines"])
    assert "tenant-b-secret-slug" in texts and "project #5" in texts


async def test_member_spend_summary_is_refused_before_reading_the_ledger(accounts, monkeypatch) -> None:
    from app.services.action_layer import builtin_insight

    def _no_db():
        raise AssertionError("касса прочитана для участника")

    monkeypatch.setattr(builtin_insight, "_sessions", _no_db)
    spec = _spec("spend.summary")
    with _as(MEMBER, MEMBER.user_id):
        for params in ({}, {"project_id": 5}):
            with pytest.raises(PermissionError, match="только админу"):
                await spec.executor(None, params)


async def test_member_sees_no_cost_of_nodes(accounts, monkeypatch) -> None:
    """Себестоимость нод и проекта: участнику пусто, кассу не читаем вовсе."""
    from app.services.action_layer import builtin_insight

    def _no_db():
        raise AssertionError("касса прочитана для участника")

    monkeypatch.setattr(builtin_insight, "_sessions", _no_db)
    with _as(MEMBER, MEMBER.user_id):
        assert await builtin_insight._node_costs(5) == ({}, None)


def test_report_columns_without_cost() -> None:
    from app.services.action_layer import builtin_insight, report_gpt
    from app.services.action_layer import report_template as tpl

    assert "cost_usd" in {key for key, _ in builtin_insight._REPORT_COLUMNS}
    assert "cost_usd" not in {
        key for key, _ in builtin_insight._without_cost(builtin_insight._REPORT_COLUMNS)
    }
    assert "spent_usd" not in {key for key, _ in report_gpt._without_spent(tpl.PROJECTS_COLUMNS)}
    assert "spent_usd" not in {key for key, _ in report_gpt._without_spent(tpl.PROJECTS_CHAT_COLUMNS)}


def test_chat_question_reads_for_member_and_admin(accounts) -> None:
    from app.orchestrator.agents.question_context import plan_reads

    question = "почему упало и сколько потрачено, покажи логи"
    with _as(MEMBER, MEMBER.user_id):
        global_reads = [action for action, _ in plan_reads(question, None)]
        project_reads = [action for action, _ in plan_reads(question, 5)]
    assert "logs.tail" not in global_reads and "spend.summary" not in global_reads
    assert "projects.overview" in global_reads
    assert "logs.tail" in project_reads and "spend.summary" not in project_reads
    with _as(ADMIN, ADMIN.user_id):
        admin_reads = [action for action, _ in plan_reads(question, None)]
    assert "logs.tail" in admin_reads and "spend.summary" in admin_reads


async def test_chat_read_runner_refuses_studio_reads_to_member(accounts) -> None:
    from app.orchestrator.agents.question_context import run_read_action

    with _as(MEMBER, MEMBER.user_id):
        with pytest.raises(PermissionError):
            await run_read_action("logs.tail", {"kind": "all"}, None)
