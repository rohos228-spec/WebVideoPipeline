"""Harness-гейт не требует книгу проекта там, где её не пишут.

Продолжение той же истории, что и `test_xlsx_optional_workbook.py`. Там отказ
убрали из `_ensure_project_xlsx`, и шаг перестал падать до вызова модели. Гейт
после шага остался нетронутым — и на живом прогоне 2026-08-25 повалил ровно то
же самое, но уже на выходе:

    Некорректный xlsx: plan harness gate failed: ['project_xlsx(missing)']

Особенно неприятно тем, что работа была СДЕЛАНА: MiniMax ответил за 74 секунды,
`general_plan` записан, статус стал `plan_ready`. И следом гейт объявил шаг
несостоявшимся, `record_step_failure` насчитал `fail 1/9`, а на третий раз
проект ушёл бы в паузу на полчаса. Пользователь при этом видит «пауза» под
готовым планом.

При учётных записях `settings.xlsx_enabled` ложно: книга — файл на диске узла,
и пользователю до него не дотянуться. То есть условие гейта не выполнялось бы
там НИКОГДА — не «иногда», а на каждом шаге каждого проекта.
"""

from __future__ import annotations

import pytest

from app.services.agent_harness import verify_project_disk


def _check(report, name):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"проверки {name} нет в отчёте: {[c.name for c in report.checks]}")


def test_missing_workbook_is_ok_when_writing_is_off(tmp_path, monkeypatch):
    """Запись выключена — отсутствие книги не претензия."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)

    report = verify_project_disk(1, tmp_path, "plan_ready", step="plan")

    check = _check(report, "project_xlsx")
    assert check.ok is True
    assert "выключена" in check.detail, "по отчёту должно быть видно, почему проверка прошла"
    assert "plan" not in report.repair_steps, "чинить нечего — файла и не должно быть"


def test_missing_workbook_is_a_failure_when_writing_is_on(tmp_path, monkeypatch):
    """Обратная сторона: там, где книгу пишут, её отсутствие — поломка.

    Без этого случая правка выше означала бы «проверку выключили», а не
    «проверку уточнили».
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", True)

    report = verify_project_disk(1, tmp_path, "plan_ready", step="plan")

    check = _check(report, "project_xlsx")
    assert check.ok is False
    assert check.detail == "missing"
    assert "plan" in report.repair_steps


def test_existing_workbook_passes_either_way(tmp_path, monkeypatch):
    """Книга на месте — проверка проходит независимо от настройки."""
    from app.settings import settings

    (tmp_path / "project.xlsx").write_bytes(b"PK\x03\x04")

    for flag in (True, False):
        monkeypatch.setattr(settings, "xlsx_write", flag)
        report = verify_project_disk(1, tmp_path, "plan_ready", step="plan")
        assert _check(report, "project_xlsx").ok is True


@pytest.mark.asyncio
async def test_gate_does_not_raise_without_workbook(tmp_path, monkeypatch):
    """Сквозная проверка: гейт после шага молчит, а не роняет шаг.

    Именно `harness_gate_or_raise` превращал отсутствующий файл в
    `RuntimeError`, который воркер засчитывал как поломку конвейера.
    """
    from types import SimpleNamespace

    from app.services import agent_harness
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)

    project = SimpleNamespace(id=1, slug="rolik", data_dir=tmp_path, status="plan_ready", meta={})

    async def _verify(session, proj, **kw):
        return agent_harness.verify_project_disk(proj.id, tmp_path, "plan_ready", step="plan")

    monkeypatch.setattr(agent_harness, "run_harness_verify", _verify)

    report = await agent_harness.harness_gate_or_raise(None, project, step="plan")
    assert _check(report, "project_xlsx").ok is True
