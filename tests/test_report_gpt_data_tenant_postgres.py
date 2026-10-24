"""Отчёт по данным работы с GPT (report.gpt_data) на живом Postgres с двумя арендаторами.

Отчёт читает только проект арендатора, от имени которого спрашивают: чужой проект «не найден»,
без его названия, кадров и персонажей, даже если номер назван явно. Кадры и персонажи читаются
под RLS.

Postgres подаётся как для `tests/test_orch_tenant_postgres.py` (``TEST_DATABASE_URL``, обычная
роль без BYPASSRLS). Пропускается без ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import uuid

import pytest

from tests.orch_pg_support import TEST_DB_URL
from tests.test_orch_tenant_postgres import pg  # noqa: F401  фикстура

pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL не задан — живого Postgres нет")


class _Ctx:
    """Внутренние вызовы отчёта: деталей и прогонов нод нет (строку проекта отчёт берёт из базы)."""

    operator = "test"
    plan_id = None
    project_id = None

    async def call(self, method, path, **_kw):
        return [] if "node-runs" in path or path.endswith("/run") else {}


async def _project_with_frames(pg, tenant: str, *, title: str, line: str, character: str) -> int:  # noqa: F811
    from app.models import Entity, Frame, Project, ProjectStatus
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with pg["factory"]() as s:
            p = Project(
                slug=f"gd-{uuid.uuid4().hex[:8]}",
                title=title,
                topic="t",
                status=ProjectStatus.new,
                hero_mode="no_hero",
                auto_mode=True,
                meta={},
            )
            s.add(p)
            await s.flush()
            for i in (1, 2):
                s.add(
                    Frame(
                        project_id=p.id,
                        number=i,
                        voiceover_text=f"{line} {i}",
                        attrs={"персонажи": "c01", "место": f"{line} место"},
                        uuid=uuid.uuid4().hex[:24],
                    )
                )
            s.add(Entity(project_id=p.id, type="character", code="c01", name=character))
            await s.commit()
            return p.id


async def test_report_gpt_data_reads_only_own_tenant(pg, monkeypatch, tmp_path) -> None:  # noqa: F811
    from app.services.action_layer.context import ActionCallError
    from app.services.action_layer.report_gpt_data import report_gpt_data
    from app.services.tenant import tenant_scope
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    a, b = pg["a"].user_id, pg["b"].user_id
    pid_a = await _project_with_frames(pg, a, title="Лисы клиента А", line="лиса", character="Рыжая")
    pid_b = await _project_with_frames(pg, b, title="Секретный ролик Б", line="тайна", character="Агент Б")

    with tenant_scope(a):
        own = await report_gpt_data(_Ctx(), {"project_id": pid_a, "kind": "json"})
        with pytest.raises(ActionCallError) as err:
            await report_gpt_data(_Ctx(), {"project_id": pid_b, "kind": "json"})
    assert err.value.status == 404
    assert "Секретный" not in str(err.value) and "тайна" not in str(err.value)
    assert [(r["frame"], r["voiceover_text"], r["place"]) for r in own["rows"]] == [
        (1, "лиса 1", "лиса место"),
        (2, "лиса 2", "лиса место"),
    ]
    assert [(c["id"], c["name"]) for c in own["characters"]] == [("c01", "Рыжая")]
    assert "тайна" not in str(own) and "Агент Б" not in str(own)

    # Тот же номер от имени владельца проекта Б — его данные, и только его.
    with tenant_scope(b):
        theirs = await report_gpt_data(_Ctx(), {"project_id": pid_b, "kind": "json"})
    assert [r["voiceover_text"] for r in theirs["rows"]] == ["тайна 1", "тайна 2"]
    assert "лиса" not in str(theirs) and "Рыжая" not in str(theirs)

    # Без арендатора (владелец студии в режиме учётных записей) клиентских проектов не видно.
    with tenant_scope(None), pytest.raises(ActionCallError):
        await report_gpt_data(_Ctx(), {"project_id": pid_a, "kind": "json"})
