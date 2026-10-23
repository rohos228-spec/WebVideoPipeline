"""Стоп чужого проекта: 404 до побочных эффектов, на живом Postgres (RLS).

Ключ стопа (файл + отмена задач процесса) — не строка базы, RLS его не
защищает. Поэтому ручки стопа сначала ищут проект под RLS арендатора и
отвечают 404, а kill чата не гасит проекты, которых задача не видит.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py`
(``TEST_DATABASE_URL``, обычная роль; ``ORCH_TEST_ON_POSTGRES=1``).
Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


async def _project_of(pg, tenant: str) -> int:  # noqa: F811
    from app.models import Project, ProjectStatus
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            p = Project(slug=f"st-{uuid.uuid4().hex[:8]}", topic="t", status=ProjectStatus.new,
                        hero_mode="no_hero")
            s.add(p)
            await s.commit()
            return p.id


@pytest.mark.parametrize("path", ["/api/projects/{pid}/stop", "/api/projects/{pid}/stages/stop"])
async def test_http_stop_of_foreign_project_is_404_without_side_effects(pg, path, monkeypatch) -> None:  # noqa: F811
    from app.services import step_cancel as sc

    cancelled: list[int] = []
    monkeypatch.setattr(sc, "cancel_advance_task", lambda pid: cancelled.append(pid) or False)
    pid_a = await _project_of(pg, pg["a"].user_id)
    sc.clear_stop(pid_a)
    try:
        res = await pg["client"].post(path.format(pid=pid_a), headers=pg["b"].auth)
        assert res.status_code == 404, res.text
        assert sc.is_stop_requested(pid_a) is False
        assert cancelled == []
        # Владелец останавливает свой проект как раньше (ключ простоя ручка снимает сама).
        res = await pg["client"].post(path.format(pid=pid_a), headers=pg["a"].auth)
        assert res.status_code == 200, res.text
        assert pid_a in cancelled
    finally:
        sc.clear_stop(pid_a)


@pytest.mark.parametrize("action", ["pause", "resume", "continue"])
async def test_other_controls_of_foreign_project_are_404(pg, action) -> None:  # noqa: F811
    from app.services import step_cancel as sc

    pid_a = await _project_of(pg, pg["a"].user_id)
    sc.clear_stop(pid_a)
    res = await pg["client"].post(f"/api/projects/{pid_a}/{action}", headers=pg["b"].auth)
    assert res.status_code == 404, res.text
    assert sc.is_stop_requested(pid_a) is False
    res = await pg["client"].delete(f"/api/projects/{pid_a}", headers=pg["b"].auth)
    assert res.status_code == 404, res.text


async def test_chat_kill_skips_foreign_project_of_running_step(pg) -> None:  # noqa: F811
    """Шаг задачи B ссылается на проект A: kill B не ставит ключ стопа на проект A."""
    from sqlalchemy import text

    from app.orchestrator.contracts import PlanStep, TaskStatus
    from app.orchestrator.db.migrations import connect
    from app.services import step_cancel as sc
    from app.services.orch_tenancy import scoped_session
    from app.services.tenant import tenant_scope

    pid_a = await _project_of(pg, pg["a"].user_id)
    b = pg["b"].user_id
    pid_b = await _project_of(pg, b)
    sup = pg["runtime"].supervisor
    for pid in (pid_a, pid_b):
        sc.clear_stop(pid)
    try:
        with tenant_scope(b):
            tid = await asyncio.to_thread(sup.tasks.insert, scoped_session("k", b), "g",
                                          TaskStatus.executing.value)
            for idx, pid in enumerate((pid_a, pid_b)):
                step = PlanStep(action="step.run_step", params={"project_id": pid, "step_code": "img"},
                                expected="x", paid=True, mutating=True)
                sid = await asyncio.to_thread(sup.tasks.insert_step, tid, idx, step)
                conn = connect()
                try:
                    conn.execute(text("UPDATE orch_steps SET status='running' WHERE id=:i"), {"i": sid})
                    conn.commit()
                finally:
                    conn.close()
            projects = await asyncio.to_thread(sup._kill_projects, tid)
        assert projects == [pid_b], projects
    finally:
        for pid in (pid_a, pid_b):
            sc.clear_stop(pid)
