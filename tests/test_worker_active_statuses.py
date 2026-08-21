"""Тест F.2/F.3: единый список активных статусов покрывает все running."""

from __future__ import annotations

from app.models import ProjectStatus
from app.orchestrator.node_registry import WORKER_ACTIVE_STATUSES
from app.services.project_state import is_running_status


def test_worker_active_statuses_cover_all_running():
    running = {s for s in ProjectStatus if is_running_status(s)}
    missing = running - set(WORKER_ACTIVE_STATUSES)
    assert not missing, f"воркер не подхватит: {sorted(s.value for s in missing)}"


def test_sfx_statuses_present():
    # §9#1 карты: раньше sfx-статусы были потеряны — шаги не подхватывались.
    assert ProjectStatus.sfx_planning in WORKER_ACTIVE_STATUSES
    assert ProjectStatus.generating_sfx in WORKER_ACTIVE_STATUSES


def test_legacy_worker_refuses_to_start():
    import pytest

    from app import worker

    with pytest.raises(SystemExit):
        worker.main()
