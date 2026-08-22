"""Центральный harness-гейт реально включён в `maybe_auto_advance`.

Гейт по умолчанию был выключен autouse-фикстурой (`harness_gate_disabled
= True`), т.е. зелёный CI ничего не гарантировал. Теперь он включён, а
opt-out — маркер `@pytest.mark.no_harness_gate`.

Одного переворота дефолта мало: если ни один тест не проходит гейт
насквозь, «включение» фиктивно — все тесты просто останавливаются на
первой же плохой проверке данных. Здесь проверяется именно проводка:
гейт ON + отчёт без плохих проверок → продвижение идёт; гейт ON + плохая
проверка → продвижения нет, счётчик растёт, на 3-м фейле проект в paused;
плохая проверка из observability-списка → не блокирует.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator import auto_advance
from app.services.agent_harness import HarnessCheck, HarnessReport
from app.settings import settings


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _report(*checks: HarnessCheck) -> HarnessReport:
    return HarnessReport(
        project_id=1,
        run_id="test",
        ok=all(c.ok for c in checks),
        status="script_ready",
        checks=list(checks),
    )


def _patch_verify(monkeypatch: pytest.MonkeyPatch, report: HarnessReport) -> list[int]:
    """Подменяет проверки диска; возвращает счётчик вызовов."""
    calls: list[int] = []

    async def _fake(session, project, **kw):  # noqa: ANN001
        calls.append(1)
        return report

    monkeypatch.setattr("app.services.agent_harness.run_harness_verify", _fake)
    return calls


def _project() -> Project:
    return Project(
        id=1,
        slug="gate",
        topic="gate",
        status=ProjectStatus.script_ready,
        auto_mode=True,
        meta={},
    )


async def test_gate_is_on_by_default_in_tests() -> None:
    """Фикстура conftest действительно включает гейт (без маркера)."""
    assert settings.harness_gate_disabled is False


@pytest.mark.asyncio
async def test_gate_passes_when_checks_ok(session: AsyncSession, monkeypatch) -> None:
    calls = _patch_verify(monkeypatch, _report(HarnessCheck(name="project_xlsx", ok=True)))
    p = _project()
    session.add(p)
    await session.flush()

    allowed = await auto_advance._harness_gate(session, p, ProjectStatus.script_ready)

    assert calls, "гейт не вызвал проверки — значит, он выключен"
    assert allowed is True
    assert p.status is ProjectStatus.script_ready
    assert "harness_gate_fails" not in (p.meta or {})


@pytest.mark.asyncio
async def test_gate_blocks_and_counts_on_bad_check(session: AsyncSession, monkeypatch) -> None:
    _patch_verify(monkeypatch, _report(HarnessCheck(name="project_xlsx", ok=False, detail="missing")))
    p = _project()
    session.add(p)
    await session.flush()

    allowed = await auto_advance._harness_gate(session, p, ProjectStatus.script_ready)

    assert allowed is False
    assert (p.meta or {}).get("harness_gate_fails", {}).get("script_ready") == 1
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_gate_pauses_on_third_failure(session: AsyncSession, monkeypatch) -> None:
    _patch_verify(monkeypatch, _report(HarnessCheck(name="project_xlsx", ok=False, detail="missing")))
    p = _project()
    session.add(p)
    await session.flush()

    for _ in range(3):
        assert await auto_advance._harness_gate(session, p, ProjectStatus.script_ready) is False

    assert p.status is ProjectStatus.paused
    assert "harness gate" in (p.meta or {}).get("auto_paused_reason", "")


@pytest.mark.asyncio
async def test_observability_checks_do_not_block(session: AsyncSession, monkeypatch) -> None:
    """Мягкие проверки — телеметрия, а не гейт."""
    soft = sorted(auto_advance._HARNESS_GATE_OBSERVABILITY)[0]
    _patch_verify(monkeypatch, _report(HarnessCheck(name=soft, ok=False, detail="noisy")))
    p = _project()
    session.add(p)
    await session.flush()

    assert await auto_advance._harness_gate(session, p, ProjectStatus.script_ready) is True


@pytest.mark.asyncio
@pytest.mark.no_harness_gate
async def test_marker_disables_gate(session: AsyncSession, monkeypatch) -> None:
    """Opt-out маркер: проверки диска не вызываются вовсе."""
    calls = _patch_verify(monkeypatch, _report(HarnessCheck(name="project_xlsx", ok=False)))
    p = _project()
    session.add(p)
    await session.flush()

    assert await auto_advance._harness_gate(session, p, ProjectStatus.script_ready) is True
    assert calls == []
