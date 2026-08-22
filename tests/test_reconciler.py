"""Единая точка согласования состояния (п.8, безопасная половина).

Реконсайлеров было пять, звались из четырёх мест, порядок между ними нигде
не записан, а критерий «работа живая» существовал в двух копиях. Тесты
фиксируют то, что теперь гарантируется: порядок проходов, изоляция падений,
единый критерий живости.
"""

from __future__ import annotations

import pytest

from app.services import reconciler


@pytest.mark.asyncio
async def test_startup_runs_passes_in_declared_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Порядок — свойство reconciler, а не порядок строк в main.py."""
    calls: list[str] = []

    async def fake_pass(name: str, report, *, background: bool) -> None:
        calls.append(name)
        report.passes[name] = 0

    monkeypatch.setattr(reconciler, "_run_pass", fake_pass)

    await reconciler.reconcile(scope="startup")

    assert calls == list(reconciler.STARTUP_PASSES)
    # NodeRun снимаются последними: раньше по коду идёт откат running-проектов.
    assert calls[-1] == "node_runs"


@pytest.mark.asyncio
async def test_background_scope_runs_only_node_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """На работающем процессе meta монтажа не трогаем — джобы могут быть живыми."""
    calls: list[tuple[str, bool]] = []

    async def fake_pass(name: str, report, *, background: bool) -> None:
        calls.append((name, background))
        report.passes[name] = 0

    monkeypatch.setattr(reconciler, "_run_pass", fake_pass)

    await reconciler.reconcile(scope="background")

    assert calls == [("node_runs", True)]


@pytest.mark.asyncio
async def test_failing_pass_does_not_stop_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Падение одного прохода не должно ронять остальные и весь старт."""

    async def boom(**_kw):
        raise RuntimeError("монтаж сломался")

    async def fine(**_kw):
        return 3

    monkeypatch.setattr("app.services.montage_board_job_state.reconcile_stale_montage_jobs_on_startup", boom)
    monkeypatch.setattr("app.services.run_sync._reconcile_stale_node_runs", fine)

    report = await reconciler.reconcile(scope="startup")

    assert "montage_jobs" in report.errors
    assert "RuntimeError" in report.errors["montage_jobs"]
    assert report.passes["node_runs"] == 3
    assert report.total == 3


@pytest.mark.asyncio
async def test_report_shape() -> None:
    report = reconciler.ReconcileReport(scope="startup")
    report.passes["node_runs"] = 2
    report.errors["montage_jobs"] = "Boom: x"
    got = report.as_dict()
    assert got["scope"] == "startup"
    assert got["total"] == 2
    assert got["passes"] == {"node_runs": 2}
    assert got["errors"] == {"montage_jobs": "Boom: x"}


@pytest.mark.asyncio
async def test_unknown_pass_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reconciler, "STARTUP_PASSES", ("нет-такого",))
    report = await reconciler.reconcile(scope="startup")
    assert "нет-такого" in report.errors


# ── единый критерий живости ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_when_task_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _pid: True)
    assert await reconciler.is_work_live(1) is True


@pytest.mark.asyncio
async def test_live_when_lease_held_by_another_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вторая половина критерия: работу держит другой процесс.

    Без неё реконсайлер красил чужую живую работу в failed.
    """
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _pid: False)

    async def held(project_id: int, unit_key: str) -> bool:
        assert unit_key == "step:img"
        return True

    monkeypatch.setattr("app.services.work_lease.is_held", held)
    assert await reconciler.is_work_live(1, step_code="img") is True


@pytest.mark.asyncio
async def test_not_live_without_task_and_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _pid: False)

    async def held(_project_id: int, _unit_key: str) -> bool:
        return False

    monkeypatch.setattr("app.services.work_lease.is_held", held)
    assert await reconciler.is_work_live(1, step_code="img") is False


@pytest.mark.asyncio
async def test_lease_failure_does_not_mark_work_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступность lease-таблицы не должна выдавать мёртвую работу за живую."""
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _pid: False)

    async def boom(_project_id: int, _unit_key: str) -> bool:
        raise RuntimeError("db locked")

    monkeypatch.setattr("app.services.work_lease.is_held", boom)
    assert await reconciler.is_work_live(1, step_code="img") is False


@pytest.mark.asyncio
async def test_no_step_code_means_no_lease_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _pid: False)

    async def must_not_be_called(*_a, **_k):
        raise AssertionError("lease не должен проверяться без step_code")

    monkeypatch.setattr("app.services.work_lease.is_held", must_not_be_called)
    assert await reconciler.is_work_live(1) is False
