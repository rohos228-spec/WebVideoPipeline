"""Харнесс считает базу через сессию, а не через файл SQLite.

`verify_project_disk` синхронна и исторически читала базу сама — сырым
`sqlite3.connect` по `state.db`. На сервере база Postgres, файла нет, и все
счётчики молча становились нулями. Живой прогон 2026-08-26: двенадцать промтов
анимации в базе, а гейт держит стадию с `r48_anim(filled=0 scenes=12)` —
и держит молча, час, потому что `r48` ещё и считался по строке 48 Excel-книги,
которой при учётных записях не существует.

Теперь `run_harness_verify` снимает `DbSnapshot` через SQLAlchemy и отдаёт его
в проверку; при выключенной книге источник правды для `r48` и паритета — база
(`docs/PROMPT_CONTRACT.md`, DB SoT). Старый путь по SQLite остаётся для CLI
без сессии — и первый тест закрепляет, что без снимка поведение прежнее:
это не «выключили проверку», а «дали ей правильные данные».
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services.agent_harness import DbSnapshot, run_harness_verify, verify_project_disk

N = 12


def _check(report, name):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"проверки {name} нет: {[c.name for c in report.checks]}")


def _scenes(tmp_path):
    (tmp_path / "scenes").mkdir()
    for i in range(1, N + 1):
        (tmp_path / "scenes" / f"scene_{i:02d}.png").write_bytes(b"\x89PNG")


def _snapshot():
    return DbSnapshot(frame_rows=[(i, f"закадр {i}", f"img {i}", f"anim {i}") for i in range(1, N + 1)])


def test_r48_counts_from_db_when_workbook_is_off(tmp_path, monkeypatch):
    """Снимок есть, книги нет — промты анимации считаются по базе."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    _scenes(tmp_path)

    report = verify_project_disk(1, tmp_path, "animation_prompts_ready", step=None, db=_snapshot())

    r48 = _check(report, "r48_anim")
    assert r48.ok, f"гейт держит при полной базе: {r48.detail}"
    assert "filled=12" in r48.detail
    assert _check(report, "frames_xlsx_parity").ok
    assert _check(report, "node_runs_failed").ok
    assert "anim_pr" not in report.repair_steps


def test_without_snapshot_the_old_path_still_reads_the_workbook(tmp_path, monkeypatch):
    """Без снимка — прежнее поведение: r48 по книге, а книги нет → 0.

    Закрепляет, что правка не выключила проверку, а дала ей данные. Именно
    так гейт и вёл себя на сервере.
    """
    from app.services import agent_harness
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    monkeypatch.setattr(agent_harness, "_db_path", lambda: tmp_path / "нет-такого.db")
    _scenes(tmp_path)

    report = verify_project_disk(1, tmp_path, "animation_prompts_ready", step=None, db=None)

    assert not _check(report, "r48_anim").ok
    assert "filled=0" in _check(report, "r48_anim").detail


def test_snapshot_does_not_hide_a_real_gap(tmp_path, monkeypatch):
    """Снимок с дырой — гейт держит. Иначе «правильные данные» = «всегда ок»."""
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    _scenes(tmp_path)
    snap = _snapshot()
    snap.frame_rows[3] = (4, "закадр 4", "img 4", "")  # у одного кадра нет промта анимации

    report = verify_project_disk(1, tmp_path, "animation_prompts_ready", step=None, db=snap)

    assert not _check(report, "r48_anim").ok
    assert "filled=11" in _check(report, "r48_anim").detail
    assert "anim_pr" in report.repair_steps


def test_failed_node_runs_come_from_the_snapshot(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    _scenes(tmp_path)
    snap = _snapshot()
    snap.node_runs_failed = 2

    report = verify_project_disk(1, tmp_path, "animation_prompts_ready", step=None, db=snap)
    assert not _check(report, "node_runs_failed").ok


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as s:
            yield s
            await s.commit()

    yield scope
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_harness_verify_snapshots_through_the_session(db, tmp_path, monkeypatch):
    """Сквозной путь: сессия → снимок → проверка. Без файла SQLite вовсе."""
    from app.services import agent_harness
    from app.settings import settings

    monkeypatch.setattr(settings, "xlsx_write", False)
    monkeypatch.setattr(agent_harness, "_db_path", lambda: tmp_path / "нет-такого.db")
    data_dir = tmp_path / "videos" / "rolik"
    data_dir.mkdir(parents=True)
    _scenes(data_dir)

    async with db() as s:
        p = Project(id=1, slug="rolik", topic="тема", status=ProjectStatus.animation_prompts_ready)
        s.add(p)
        await s.flush()
        for i in range(1, N + 1):
            s.add(
                Frame(
                    project_id=1,
                    number=i,
                    voiceover_text=f"vo {i}",
                    image_prompt=f"img {i}",
                    animation_prompt=f"anim {i}",
                )
            )
        await s.commit()

    async with db() as s:
        p = await s.get(Project, 1)
        monkeypatch.setattr(type(p), "data_dir", property(lambda self: data_dir), raising=False)
        report = await run_harness_verify(s, p, allow_repair=False, include_http=False)

    r48 = _check(report, "r48_anim")
    assert r48.ok, f"сквозной путь держит гейт: {r48.detail}"
