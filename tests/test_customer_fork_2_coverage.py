"""Перенос из форка заказчика (merge/customer-fork-2): ветки без покрытия.

Файл добивает `diff-cov` по перенесённой логике: парсеры ответов LLM,
восстановление артефактов с диска, сброс шагов, сетевые фоллбэки и CLI
мониторинга. Сети, Chrome и ffmpeg здесь нет — только tmp_path и моки.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
)


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    """Отдельная SQLite на тест — как в tests/test_reset_step.py."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cov.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _stat_raiser(monkeypatch: pytest.MonkeyPatch, name_prefix: str) -> None:
    """Заставить `Path.stat()` падать OSError для файлов с данным префиксом.

    `Path.is_file()` зовёт `stat(follow_symlinks=...)` и глотает OSError, а
    прикладной код — голый `p.stat()`. Различаем по наличию kwarg, иначе
    ветка «файл исчез между glob и stat» недостижима.
    """
    orig = Path.stat
    missing = object()

    def _fake(self: Path, *, follow_symlinks: Any = missing):
        if follow_symlinks is missing:
            if self.name.startswith(name_prefix):
                raise OSError("файл исчез между glob и stat")
            return orig(self)
        return orig(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _fake)


# ── db_apply: цитаты сцены не нашлись ─────────────────────────────────────


def test_scene_span_in_text_returns_none_when_start_missing() -> None:
    from app.services.db_apply import _scene_span_in_text

    full = "первый кадр. второй кадр. третий кадр."
    # start_offset за пределами вхождения, и с начала строки тоже не находится
    assert _scene_span_in_text(full, "четвёртый", "кадр") is None
    # конец не найден после начала
    assert _scene_span_in_text(full, "третий", "нулевой") is None
    # начало уехало за offset — второй проход с нуля находит
    assert _scene_span_in_text(full, "первый", "второй", 20) == (0, 19)


# ── xlsx_step_runners: парсеры ответов и крышка на кадры ──────────────────


def test_extract_general_plan_skips_broken_json_candidate() -> None:
    """Первый ```json``` блок битый — берём следующий, а не падаем."""
    from app.services.xlsx_step_runners import extract_general_plan_from_gpt_reply

    plan = "План ролика. " + "Подробности сцены. " * 20
    reply = "```json\n{битый, не json}\n```\n\n" + '```json\n{"general_plan": "' + plan + '"}\n```'
    assert extract_general_plan_from_gpt_reply(reply).startswith("План ролика.")


def test_clamp_parent_frames_accepts_plain_strings() -> None:
    """Модель отдала список строк вместо объектов — склейка всё равно идёт."""
    from app.services.xlsx_step_runners import MAX_PARENT_FRAMES, clamp_parent_frames

    spec: list[Any] = [f"фраза номер {i}" for i in range(MAX_PARENT_FRAMES + 5)]
    out = clamp_parent_frames(spec)
    assert len(out) == MAX_PARENT_FRAMES
    assert all(isinstance(x, dict) for x in out)
    # закадр сохранён слово в слово и в том же порядке
    joined = " ".join(str(d["закадр"]) for d in out)
    assert joined.split() == " ".join(spec).split()


def test_clamp_parent_frames_survives_non_numeric_duration() -> None:
    """Длительность пришла строкой — склейка не роняет шаг."""
    from app.services.xlsx_step_runners import MAX_PARENT_FRAMES, clamp_parent_frames

    spec = [{"закадр": f"фраза {i}", "длительность": "три"} for i in range(MAX_PARENT_FRAMES + 2)]
    out = clamp_parent_frames(spec)
    assert len(out) == MAX_PARENT_FRAMES
    # значение осталось нетронутым, а не превратилось в 0.0
    assert out[0]["длительность"] == "три"


def test_clamp_parent_frames_uses_default_key_when_absent() -> None:
    """Ни одного известного ключа закадра — работаем по дефолтному «закадр»."""
    from app.services.xlsx_step_runners import MAX_PARENT_FRAMES, clamp_parent_frames

    spec = [{"что-то своё": f"x{i}"} for i in range(MAX_PARENT_FRAMES + 1)]
    out = clamp_parent_frames(spec)
    assert len(out) == MAX_PARENT_FRAMES
    assert "закадр" in out[0]


def test_clamp_parent_frames_noop_under_cap() -> None:
    from app.services.xlsx_step_runners import clamp_parent_frames

    spec = [{"закадр": "раз"}, {"закадр": "два"}]
    assert clamp_parent_frames(spec) is spec


# ── generate_images: временный сетевой сбой возвращает кадр в очередь ──────


def test_requeue_frame_on_transient_repairs_broken_counter() -> None:
    """В attrs мусор вместо числа — счётчик начинается с нуля, а не падает."""
    from app.orchestrator.steps.generate_images import (
        TRANSIENT_RETRIES_ATTR,
        _requeue_frame_on_transient,
    )

    frame = Frame(project_id=1, number=1)
    frame.status = FrameStatus.image_generated
    frame.attrs = {TRANSIENT_RETRIES_ATTR: "две штуки"}

    assert _requeue_frame_on_transient(frame, OSError("connection reset"), is_shot2=False) is True
    assert frame.attrs[TRANSIENT_RETRIES_ATTR] == 1
    assert frame.status is FrameStatus.image_prompt_ready


def test_requeue_frame_on_transient_stops_after_cap() -> None:
    from app.orchestrator.steps.generate_images import (
        MAX_TRANSIENT_NET_RETRIES,
        TRANSIENT_RETRIES_ATTR,
        _requeue_frame_on_transient,
    )

    frame = Frame(project_id=1, number=1)
    frame.attrs = {TRANSIENT_RETRIES_ATTR: MAX_TRANSIENT_NET_RETRIES}
    assert _requeue_frame_on_transient(frame, OSError("connection reset"), is_shot2=False) is False


def test_requeue_frame_on_transient_ignores_non_network_error() -> None:
    from app.orchestrator.steps.generate_images import _requeue_frame_on_transient

    frame = Frame(project_id=1, number=1)
    frame.attrs = {}
    assert _requeue_frame_on_transient(frame, ValueError("битый промт"), is_shot2=False) is False


# ── montage_outsee_recover: обход scenes/ с посторонними файлами ──────────


def _project_on_disk(tmp_path: Path, slug: str = "recov") -> Project:
    p = Project(slug=slug, topic="t", hero_mode="full_auto")
    p.id = 11
    return p


def test_hits_from_disk_skips_non_images(tmp_path: Path) -> None:
    from app.services import montage_outsee_recover as mor

    project = _project_on_disk(tmp_path)
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / "frame_001_aabbccdd.png").write_bytes(b"x" * 10)
    (scenes / "notes.txt").write_text("не картинка", encoding="utf-8")
    (scenes / "подпапка").mkdir()

    hits = mor._hits_from_disk(project)
    assert [h.frame_number for h in hits] == [1]
    assert hits[0].short_uuid == "aabbccdd"


def test_hits_from_disk_without_scenes_dir(tmp_path: Path) -> None:
    from app.services import montage_outsee_recover as mor

    project = _project_on_disk(tmp_path, slug="recov-empty")
    assert mor._hits_from_disk(project) == []


def test_collect_stub_prefixes_skips_non_images_and_ready_files(tmp_path: Path) -> None:
    from app.services import montage_outsee_recover as mor

    project = _project_on_disk(tmp_path, slug="recov-stub")
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / "frame_002_11223344.png").write_bytes(b"x" * 10)  # огрызок
    (scenes / "frame_003_55667788.png").write_bytes(b"x" * (mor._READY_BYTES + 1))  # готов
    (scenes / "readme.md").write_text("не картинка", encoding="utf-8")

    stubs = mor.collect_stub_prefixes(project)
    assert [s[0] for s in stubs] == [2]


# ── artifact_recovery: герои с диска ──────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_hero_png_normalizes_extension(session, tmp_path: Path) -> None:
    """Исходник .jpeg кладётся как cNN.jpeg, чужое расширение → .png."""
    from app.services.artifact_recovery import _restore_hero_png_from_path

    project = Project(slug="hero-restore", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()

    src = tmp_path / "исходник.jpeg"
    src.write_bytes(b"JPEG")
    dest = _restore_hero_png_from_path(session, project, "C01", src)
    assert dest is not None
    assert dest.name == "c01.jpeg"
    assert dest.read_bytes() == b"JPEG"

    weird = tmp_path / "исходник.bin"
    weird.write_bytes(b"BIN")
    dest2 = _restore_hero_png_from_path(session, project, "C02", weird)
    assert dest2 is not None and dest2.name == "c02.png"

    await session.flush()
    arts = (await session.execute(__import__("sqlalchemy").select(Artifact))).scalars().all()
    assert {(a.meta or {}).get("excel_id") for a in arts} == {"c01", "c02"}


@pytest.mark.asyncio
async def test_recover_hero_references_from_old_dir_skips_junk(session, tmp_path: Path) -> None:
    """В old/characters/ лежит мусор — восстанавливаем только картинки cNN."""
    from app.services.artifact_recovery import recover_hero_references_from_old_dir

    project = Project(slug="hero-old", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()

    old = project.data_dir / "old" / "characters"
    old.mkdir(parents=True, exist_ok=True)
    (old / "hero_c01.png").write_bytes(b"PNG")
    (old / "hero_c01.txt").write_text("описание", encoding="utf-8")  # не картинка
    (old / "прочее.png").write_bytes(b"PNG")  # без cNN в имени
    (old / "вложенная").mkdir()

    restored = await recover_hero_references_from_old_dir(session, project)
    assert restored == ["c01"]
    assert (project.data_dir / "characters" / "c01.png").is_file()


@pytest.mark.asyncio
async def test_recover_hero_references_from_old_dir_without_dir(session) -> None:
    from app.services.artifact_recovery import recover_hero_references_from_old_dir

    project = Project(slug="hero-nodir", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()
    assert await recover_hero_references_from_old_dir(session, project) == []


# ── project_state: пересчёт по всем проектам ──────────────────────────────


@pytest.mark.asyncio
async def test_recompute_all_skip_assembled(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """skip_assembled не тащит досчитанные проекты в обход артефактов."""
    from app.services import project_state

    done = Project(slug="done", topic="t", hero_mode="full_auto")
    done.status = ProjectStatus.assembled
    fresh = Project(slug="fresh", topic="t", hero_mode="full_auto")
    fresh.status = ProjectStatus.new
    session.add_all([done, fresh])
    await session.flush()

    seen: list[str] = []

    async def _recompute(_s, p, *, dry_run=False):
        seen.append(p.slug)
        return ProjectStatus.new, ProjectStatus.plan_ready, True

    monkeypatch.setattr(project_state, "recompute_status", _recompute)

    changes = await project_state.recompute_all(session, skip_assembled=True)
    assert seen == ["fresh"]
    assert changes == {fresh.id: ("new", "plan_ready")}

    seen.clear()
    await project_state.recompute_all(session)
    assert sorted(seen) == ["done", "fresh"]


# ── run_sync: поиск дефолтного воркфлоу на битой базе ──────────────────────


@pytest.mark.asyncio
async def test_get_default_workflow_id_survives_broken_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute падает (пустая/битая база) — None, а не исключение наружу."""
    from app.services import run_sync

    class _BrokenSession:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("no such table: workflows")

    async def _broken_seed(*_a, **_kw):
        raise RuntimeError("засев тоже не вышел")

    from app.web import settings_default

    monkeypatch.setattr(settings_default, "seed_default_workflow", _broken_seed)
    assert await run_sync._get_default_workflow_id(_BrokenSession()) is None


@pytest.mark.asyncio
async def test_get_default_workflow_id_returns_any_when_heal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пометить дефолтным не вышло — id всё равно отдаём, шаг не блокируем."""
    from app.services import run_sync

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, *_a, **_kw):
            self.calls += 1
            # 1-й запрос — default'а нет, 2-й — есть «любой»
            return _Result(None if self.calls == 1 else SimpleNamespace(id=42))

    class _BrokenScope:
        async def __aenter__(self):
            raise RuntimeError("соединение легло")

        async def __aexit__(self, *_exc):
            return None

    monkeypatch.setattr(run_sync, "session_scope", lambda: _BrokenScope())
    assert await run_sync._get_default_workflow_id(_Session()) == 42


# ── settings_default: засев через переданную сессию + миграция чужих wf ────


@pytest.mark.asyncio
async def test_seed_default_workflow_migrates_non_default(session) -> None:
    """Дефолтный засеян, а чужие воркфлоу прогоняются через миграцию enrich."""
    from app.models import Workflow
    from app.web import settings_default

    custom = Workflow(name="мой", description="", nodes=[], edges=[], is_default=False, version=1)
    session.add(custom)
    await session.flush()

    await settings_default.seed_default_workflow(session)

    import sqlalchemy as sa

    rows = (await session.execute(sa.select(Workflow))).scalars().all()
    assert any(w.is_default for w in rows)
    assert any(not w.is_default for w in rows)


# ── reset_step: дочистка scenes/ при сбросе картинок ──────────────────────


@pytest.mark.asyncio
async def test_wipe_images_removes_only_images(session) -> None:
    """В scenes/ лежат картинка, текстовик и подпапка — сносим только картинку."""
    from app.services.reset_step import _wipe_images

    project = Project(slug="wipe-img", topic="t", hero_mode="full_auto")
    project.status = ProjectStatus.images_ready
    session.add(project)
    await session.flush()

    frame = Frame(project_id=project.id, number=1, voiceover_text="закадр")
    frame.status = FrameStatus.image_generated
    frame.image_prompt = "промт"
    session.add(frame)
    await session.flush()

    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / "frame_001_aabbccdd.png").write_bytes(b"PNG")
    (scenes / "notes.txt").write_text("не картинка", encoding="utf-8")
    (scenes / "подпапка").mkdir()

    stats = await _wipe_images(session, project)
    assert stats["extra_files"] == 1
    assert not (scenes / "frame_001_aabbccdd.png").exists()
    assert (scenes / "notes.txt").exists()
    assert frame.status is FrameStatus.image_prompt_ready


# ── generate_items: предметы с диска и фоллбэк на Entity ──────────────────


@pytest.mark.asyncio
async def test_existing_item_indices_survives_vanished_file(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл исчез между glob и stat — пропускаем, а не роняем шаг."""
    from app.orchestrator.steps.generate_items import _existing_item_indices

    project = Project(slug="items-race", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()

    items = project.data_dir / "items"
    items.mkdir(parents=True, exist_ok=True)
    (items / "predmet1_aabb.png").write_bytes(b"x" * 2000)

    _stat_raiser(monkeypatch, "predmet")
    assert await _existing_item_indices(session, project) == set()


@pytest.mark.asyncio
async def test_existing_item_indices_reads_disk(session) -> None:
    from app.orchestrator.steps.generate_items import _existing_item_indices

    project = Project(slug="items-disk", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()

    items = project.data_dir / "items"
    items.mkdir(parents=True, exist_ok=True)
    (items / "predmet2_aabb.png").write_bytes(b"x" * 2000)
    (items / "predmet3_aabb.png").write_bytes(b"x")  # огрызок — не считается

    assert await _existing_item_indices(session, project) == {2}


@pytest.mark.asyncio
async def test_resolve_item_descriptions_survives_entity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Таблица entities недоступна — отдаём что есть, а не валим шаг."""
    from app.orchestrator.steps.generate_items import _resolve_item_descriptions

    class _BrokenSession:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("no such table: entities")

    project = Project(slug="items-broken", topic="t", hero_mode="full_auto")
    project.id = 5
    project.item_descriptions = []
    assert await _resolve_item_descriptions(_BrokenSession(), project) == []


# ── generate_hero: обход characters/ ──────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_hero_ids_skips_foreign_and_vanished(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """В characters/ чужой png и файл, исчезнувший между glob и stat."""
    from app.orchestrator.steps.generate_hero import _excel_ids_with_artifact

    project = Project(slug="hero-scan", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()

    chars = project.data_dir / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    (chars / "предмет.png").write_bytes(b"x" * 2000)  # не cNN — мимо
    (chars / "c01.png").write_bytes(b"x" * 2000)

    _stat_raiser(monkeypatch, "c01")
    assert await _excel_ids_with_artifact(session, project) == set()


# ── agent_harness: http-проверки без сети ─────────────────────────────────


def test_verify_project_http_counts_scene_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Все ручки зелёные — последним идёт счётчик картинок на диске."""
    from app.services import agent_harness

    scenes = tmp_path / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "frame_001_aabbccdd.png").write_bytes(b"PNG")
    (scenes / "frame_002_bbccddee.png").write_bytes(b"PNG")

    def _fake_http_get(_url: str, **_kw):
        return 200, 1024, b"binary"

    def _fake_http_json(url: str, **_kw):
        if url.endswith("/studio-version"):
            return 200, {"backend_ok": True, "pipeline_ok": True, "build": "x", "backend_git": "y"}
        if url.endswith("/frames"):
            return 200, [{"image_prompt": "p", "animation_prompt": "a", "voiceover_text": "v"}]
        if url.endswith("/assets"):
            return 200, [{"preview_url": "/api/x/1"}, {"preview_url": ""}]
        return 200, {"status": "assembled", "slug": "s"}

    monkeypatch.setattr(agent_harness, "_http_get", _fake_http_get)
    monkeypatch.setattr(agent_harness, "_http_json", _fake_http_json)
    monkeypatch.setattr(agent_harness, "_db_path", lambda: tmp_path / "state.db")

    checks = {c.name: c for c in agent_harness.verify_project_http(1, tmp_path)}
    assert checks["http_scene_parity"].ok is True
    assert "scenes_disk=2" in checks["http_scene_parity"].detail
    assert checks["xlsx_http"].ok is True
    assert checks["assets_http"].ok is True


# ── monitor/report: CLI с --html ──────────────────────────────────────────


def test_monitor_report_cli_writes_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from app.monitor import report

    monitor_dir = tmp_path / "monitor"
    events = monitor_dir / "events"
    events.mkdir(parents=True)
    (events / "events_2026-01-02.jsonl").write_text(
        json.dumps({"event": "step_end", "step": "plan", "duration": 1.5, "ok": True}) + "\n",
        encoding="utf-8",
    )

    out_html = tmp_path / "отчёт" / "r.html"
    monkeypatch.setattr(
        "sys.argv",
        [
            "report",
            "--dir",
            str(monitor_dir),
            "--date",
            "2026-01-02",
            "--html",
            str(out_html),
        ],
    )
    report.main()
    assert out_html.is_file()
    assert "<html" in out_html.read_text(encoding="utf-8").lower()
    assert "HTML-отчёт сохранён" in capsys.readouterr().out


def test_monitor_report_cli_html_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """`--html` без значения — файл ложится в папку мониторинга."""
    from app.monitor import report

    monitor_dir = tmp_path / "monitor"
    events = monitor_dir / "events"
    events.mkdir(parents=True)
    (events / "events_2026-01-03.jsonl").write_text(
        json.dumps({"event": "step_end", "step": "plan", "duration": 2.0, "ok": False}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["report", "--dir", str(monitor_dir), "--date", "2026-01-03", "--html"],
    )
    report.main()
    assert (monitor_dir / "report_2026-01-03.html").is_file()
    capsys.readouterr()


# ── montage/variant2: ffmpeg завис и процесс уже мёртв ────────────────────


@pytest.mark.asyncio
async def test_variant2_run_survives_dead_process_on_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """kill() бьётся об OSError (процесс уже умер) — таймаут всё равно наш."""
    from app.services.montage import variant2

    class _DeadProc:
        returncode: int | None = None

        async def communicate(self):
            await asyncio.sleep(10.0)
            return b"", b""

        def kill(self) -> None:
            raise OSError("No such process")

        async def wait(self) -> int:
            return -9

    async def _spawn(*_a, **_kw):
        return _DeadProc()

    monkeypatch.setattr(variant2.asyncio, "create_subprocess_exec", _spawn)
    with pytest.raises(TimeoutError, match="висел дольше"):
        await variant2._run(["ffmpeg", "-i", "x.mp4"], context="concat", timeout=0.05)


@pytest.mark.asyncio
async def test_variant2_mux_mixes_bgm_and_sfx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """BGM + SFX в одном миксе: обе дорожки попадают в filter_complex."""
    from app.services import sfx_mix
    from app.services.montage import variant2

    bgm_file = tmp_path / "bgm.mp3"
    bgm_file.write_bytes(b"dummy")
    sfx_file = tmp_path / "hit.wav"
    sfx_file.write_bytes(b"dummy")

    captured: list[list[str]] = []

    async def _fake_run(cmd, **_kw):
        captured.append(cmd)

    monkeypatch.setattr(variant2, "_run", _fake_run)
    await variant2._mux(
        tmp_path / "v.mp4",
        tmp_path / "voice.mp3",
        tmp_path / "out.mp4",
        voice_s=10.0,
        bgm=SimpleNamespace(path=bgm_file, level=0.2),
        sfx=[sfx_mix.SfxInput(path=sfx_file, t_start=1.0, gain=0.5, kind="hit")],
    )
    cmd = captured[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[bgm]" in fc
    assert "amix=inputs=3" in fc
    assert "-stream_loop" in cmd


# ── outsee: httpx-фоллбэк скачивания мимо Playwright ──────────────────────


class _FakeHttpxResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _FakeHttpxClient:
    responses: list[Any] = []
    calls: list[str] = []

    def __init__(self, **_kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def get(self, url: str, **_kw):
        type(self).calls.append(url)
        item = type(self).responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _page_with_broken_request():
    class _Api:
        async def get(self, *_a, **_kw):
            raise RuntimeError("playwright: net::ERR_NAME_NOT_RESOLVED")

    return SimpleNamespace(context=SimpleNamespace(request=_Api()))


@pytest.mark.asyncio
async def test_download_via_context_falls_back_to_httpx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Playwright-запрос лёг — тот же URL берём через httpx и сохраняем."""
    import httpx

    from app.bots import outsee

    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = [_FakeHttpxResponse(200, b"P" * 500)]
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)

    out = tmp_path / "img" / "a.png"
    await outsee._download_via_context(
        _page_with_broken_request(),
        "https://cdn.example/a.png",
        out,
        timeout_ms=1000,
        attempts=2,
    )
    assert out.read_bytes() == b"P" * 500
    assert _FakeHttpxClient.calls == ["https://cdn.example/a.png"]


@pytest.mark.asyncio
async def test_download_via_context_reraises_when_httpx_also_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """httpx тоже лёг — после всех попыток наружу уходит ошибка Playwright."""
    import httpx

    from app.bots import outsee
    from app.services import step_cancel

    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = [
        ConnectionError("dns"),
        _FakeHttpxResponse(500, b""),
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)

    async def _no_sleep(_s, _pid=None):
        return None

    monkeypatch.setattr(step_cancel, "sleep_cancellable", _no_sleep)

    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        await outsee._download_via_context(
            _page_with_broken_request(),
            "https://cdn.example/a.png",
            tmp_path / "b.png",
            timeout_ms=1000,
            attempts=2,
        )
    assert len(_FakeHttpxClient.calls) == 2


@pytest.mark.asyncio
async def test_download_via_context_skips_httpx_for_non_http_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """blob:-ссылка — httpx не зовём вовсе."""
    import httpx

    from app.bots import outsee
    from app.services import step_cancel

    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)

    async def _no_sleep(_s, _pid=None):
        return None

    monkeypatch.setattr(step_cancel, "sleep_cancellable", _no_sleep)

    with pytest.raises(RuntimeError):
        await outsee._download_via_context(
            _page_with_broken_request(),
            "blob:outsee/abc",
            tmp_path / "c.png",
            timeout_ms=1000,
            attempts=1,
        )
    assert _FakeHttpxClient.calls == []


# ── artifact_recovery: stale Artifact уступает диску ──────────────────────


def _touch(path: Path, mtime: float) -> None:
    import os

    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_recover_videos_drops_artifact_with_missing_file(session) -> None:
    """Artifact указывает в никуда — запись сносим и привязываем файл с диска."""
    import sqlalchemy as sa

    from app.services.artifact_recovery import recover_scene_videos_from_disk

    project = Project(slug="vid-ghost", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()
    frame = Frame(project_id=project.id, number=1, voiceover_text="закадр")
    session.add(frame)
    await session.flush()

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    clip = videos / "clip_001_aabbccdd.mp4"
    clip.write_bytes(b"0" * 90_000)

    session.add(
        Artifact(
            project_id=project.id,
            frame_id=frame.id,
            kind=ArtifactKind.scene_video,
            uuid="deadbeef",
            path=str(videos / "clip_001_сгинул.mp4"),
            meta={"shot": 1},
        )
    )
    await session.flush()

    recovered = await recover_scene_videos_from_disk(session, project)
    assert recovered == [1]
    rows = (await session.execute(sa.select(Artifact))).scalars().all()
    assert [Path(a.path).name for a in rows] == [clip.name]


@pytest.mark.asyncio
async def test_recover_images_replaces_stale_and_ghost_artifacts(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Картинка на диске новее — старый Artifact уходит; ссылка в никуда тоже."""
    import time

    import sqlalchemy as sa

    from app.services import scan_frames
    from app.services.artifact_recovery import recover_scene_images_from_disk

    monkeypatch.setattr(scan_frames, "is_valid_scene_image", lambda _p: True)

    project = Project(slug="img-stale", topic="t", hero_mode="full_auto")
    session.add(project)
    await session.flush()
    stale_frame = Frame(project_id=project.id, number=1, voiceover_text="закадр")
    ghost_frame = Frame(project_id=project.id, number=2, voiceover_text="закадр")
    session.add_all([stale_frame, ghost_frame])
    await session.flush()

    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    now = time.time()
    old_img = scenes / "frame_001_00000000.png"
    new_img = scenes / "frame_001_11111111.png"
    old_img.write_bytes(b"P" * 90_000)
    new_img.write_bytes(b"P" * 90_000)
    _touch(old_img, now - 60)
    _touch(new_img, now)
    ghost_img = scenes / "frame_002_22222222.png"
    ghost_img.write_bytes(b"P" * 90_000)

    session.add_all(
        [
            Artifact(
                project_id=project.id,
                frame_id=stale_frame.id,
                kind=ArtifactKind.scene_image,
                uuid="stale001",
                path=str(old_img),
                meta={"shot": 1},
            ),
            Artifact(
                project_id=project.id,
                frame_id=ghost_frame.id,
                kind=ArtifactKind.scene_image,
                uuid="ghost002",
                path=str(scenes / "frame_002_сгинул.png"),
                meta={"shot": 1},
            ),
        ]
    )
    await session.flush()

    recovered = await recover_scene_images_from_disk(session, project)
    assert sorted(recovered) == [1, 2]
    rows = (await session.execute(sa.select(Artifact))).scalars().all()
    names = sorted(Path(a.path).name for a in rows)
    assert names == [new_img.name, ghost_img.name] or names == sorted([new_img.name, ghost_img.name])
    assert stale_frame.status is FrameStatus.image_generated


# ── generate_music: ответ GPT чистится до проверки длины ──────────────────


@pytest.mark.asyncio
async def test_generate_music_rejects_short_prompt_after_cleanup(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обёртки снимаются, и только потом меряется длина — иначе брак проходил."""
    from app.orchestrator.steps import generate_music
    from app.settings import settings

    monkeypatch.setattr(settings, "music_enabled", True)

    project = Project(slug="music-short", topic="Тема ролика", hero_mode="full_auto")
    project.status = ProjectStatus.generating_music
    session.add(project)
    await session.flush()
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "voiceover.txt").write_text("закадровый текст" * 5, encoding="utf-8")

    class _Gpt:
        async def new_conversation(self) -> None:
            return None

        async def ask_with_files(self, *_a, **_kw) -> str:
            return '```\n**Промпт**: "dark pads"\n```'

    monkeypatch.setattr("app.services.gpt_client.get_gpt_client", lambda: _Gpt())

    with pytest.raises(RuntimeError, match="слишком короткий промт"):
        await generate_music.run(session, project, bot=None)


# ── generate_items: отмена шага и happy-path одного предмета ──────────────


@pytest.mark.asyncio
async def test_items_run_honours_cancel_at_loop_start(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Шаг остановили — предмет даже не начинаем рисовать."""
    from app.orchestrator.steps import generate_items
    from app.services.step_cancel import StepCancelledError

    project = Project(slug="items-cancel", topic="t", hero_mode="full_auto")
    project.status = ProjectStatus.generating_items
    project.item_descriptions = ["красный чайник"]
    session.add(project)
    await session.flush()

    def _cancelled(_pid: int) -> None:
        raise StepCancelledError("остановлено оператором")

    monkeypatch.setattr(generate_items, "raise_if_cancelled", _cancelled)
    monkeypatch.setattr(generate_items, "_items_style_prompt", lambda _p: "")

    with pytest.raises(StepCancelledError):
        await generate_items.run(session, project, bot=None)


@pytest.mark.asyncio
async def test_items_run_generates_through_shared_image_slot(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Предмет рисуется под общим слотом провайдера и ложится в Artifact."""
    import contextlib

    import sqlalchemy as sa

    from app.orchestrator.steps import generate_items

    project = Project(slug="items-gen", topic="t", hero_mode="full_auto")
    project.status = ProjectStatus.generating_items
    project.item_descriptions = ["красный чайник"]
    session.add(project)
    await session.flush()

    slot_used: list[bool] = []

    @contextlib.asynccontextmanager
    async def _slot():
        slot_used.append(True)
        yield

    @contextlib.asynccontextmanager
    async def _no_browser(**_kw):
        yield None

    out_file = tmp_path / "predmet1.png"
    out_file.write_bytes(b"PNG")

    async def _fake_generate(*_a, **kwargs):
        path = kwargs.get("out_path") or out_file
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"PNG")
        return SimpleNamespace(file_path=Path(path))

    monkeypatch.setattr(generate_items, "_items_style_prompt", lambda _p: "стиль")
    monkeypatch.setattr(generate_items, "acquire_image_slot", _slot)
    monkeypatch.setattr(generate_items, "_optional_browser", _no_browser)
    monkeypatch.setattr(generate_items, "get_gpt_client", lambda: SimpleNamespace())
    monkeypatch.setattr(generate_items, "generate_image_with_retries", _fake_generate)
    monkeypatch.setattr(generate_items, "http_image_primary", lambda: True)

    await generate_items.run(session, project, bot=None)

    assert slot_used == [True]
    assert project.status is ProjectStatus.items_ready
    rows = (await session.execute(sa.select(Artifact))).scalars().all()
    assert [(r.meta or {}).get("item_id") for r in rows] == ["predmet1"]


# ── xlsx_step_runners: закадр из голого JSON ──────────────────────────────


@pytest.mark.asyncio
async def test_run_script_xlsx_reads_bare_json_voiceover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель вернула не apply-ops, а голый JSON — закадр всё равно достаём."""
    from app.services import xlsx_step_runners as xsr
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    project = Project(slug="script-json", topic="t", hero_mode="full_auto")
    project.id = 21
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    (project.data_dir / "voiceover.txt").write_text("старый закадр", encoding="utf-8")

    vo = "Закадровый текст ролика. " * 20
    reply = (
        "```json\n{не json вовсе}\n```\n\n"
        "```json\n" + json.dumps({"voiceover": vo}, ensure_ascii=False) + "\n```"
    )

    prompt_file = project.data_dir / "tmp_gpt" / "prompt_script.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("prompt", encoding="utf-8")

    async def _ask(*_a, **_kw) -> str:
        return reply

    async def _lock(_pid, _step, fn):
        return await fn()

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)
    monkeypatch.setattr(xsr.cx, "write_script_prompt_file", lambda *_a, **_kw: prompt_file)
    monkeypatch.setattr(xsr.cx, "chat_message", lambda *_a, **_kw: "go")

    _result, text = await xsr.run_script_xlsx(project)
    assert text.startswith("Закадровый текст ролика.")


# ── assemble: SFX доезжают до микса обеими ветками сборки ─────────────────


async def _assemble_project(session, slug: str) -> tuple[Project, list[Frame]]:
    project = Project(slug=slug, topic="t", hero_mode="full_auto")
    project.status = ProjectStatus.assembling
    session.add(project)
    await session.flush()
    frames = [
        Frame(project_id=project.id, number=1, voiceover_text="раз"),
        Frame(project_id=project.id, number=2, voiceover_text="два"),
    ]
    frames[0].start_ts, frames[0].end_ts, frames[0].duration_seconds = 0.0, 3.0, 3.0
    frames[1].start_ts, frames[1].end_ts, frames[1].duration_seconds = 3.0, 7.0, 4.0
    session.add_all(frames)
    await session.flush()
    return project, frames


def _write_sfx_plan(project: Project) -> Path:
    """Готовый sfx_gen-чекпоинт: файл на диске + запись в meta.ai_jobs."""
    sfx_dir = project.data_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    sfx_file = sfx_dir / "sfx_00_hit.wav"
    sfx_file.write_bytes(b"RIFF" + b"0" * 100)

    from app.services.ai_result_io import save_ai_job_checkpoint

    save_ai_job_checkpoint(
        project,
        "sfx_files",
        {
            "files": [
                {
                    "idx": 0,
                    "path": str(sfx_file.resolve()),
                    "frame_number": 2,
                    "t_start": 3.5,
                    "duration": 0.8,
                    "kind": "hit",
                    "gain": 0.6,
                    "duck": False,
                    "provider": "local_synth",
                    "offset_in_frame": 0.5,
                }
            ]
        },
    )
    return sfx_file


@pytest.mark.asyncio
async def test_assemble_body_variant2_passes_sfx(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """montage-v3: SFX из sfx_gen уходят в run_variant2, метки — по клипам."""
    from app.orchestrator.steps import assemble as step

    project, frames = await _assemble_project(session, "asm-v2")
    sfx_file = _write_sfx_plan(project)

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "voice_full.mp3"
    audio_path.write_bytes(b"MP3")

    captured: dict[str, Any] = {}

    async def _probe_duration(_p) -> float:
        return 7.0

    async def _run_variant2(_project, frame_numbers, _audio, out, *, bgm=None, sfx=None):
        captured["frame_numbers"] = list(frame_numbers)
        captured["sfx"] = list(sfx or [])
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"MP4")

    async def _send_hitl(*_a, **_kw):
        return None

    async def _on_child(*_a, **_kw):
        return None

    monkeypatch.setattr(step, "probe_duration", _probe_duration)
    monkeypatch.setattr(step, "run_variant2", _run_variant2)
    monkeypatch.setattr(step, "send_hitl_video", _send_hitl)
    monkeypatch.setattr("app.services.mass_factory.on_child_montage_complete", _on_child)

    await step._assemble_body(
        session,
        project,
        bot=None,
        frames=frames,
        frames_all=frames,
        skipped_no_video=[],
        audio=None,
        audio_path=audio_path,
        audio_dir=audio_dir,
        frame_numbers=[1, 2],
        per_frame_tts=False,
        subs_enabled=False,
        words=[],
        whisper_art=None,
        cells=[(1, "раз"), (2, "два")],
    )

    assert captured["frame_numbers"] == [1, 2]
    assert [s.path for s in captured["sfx"]] == [sfx_file.resolve()]
    # старт пересчитан по фактическим длительностям клипов: кадр 2 c 3.0 + 0.5
    assert captured["sfx"][0].t_start == pytest.approx(3.5)
    assert project.status is ProjectStatus.assembled


@pytest.mark.asyncio
async def test_assemble_body_per_frame_passes_sfx(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-frame TTS: тот же SFX-набор уезжает в assemble()."""
    from app.orchestrator.steps import assemble as step
    from app.services.frame_audio import FrameAudioClip

    project, frames = await _assemble_project(session, "asm-pf")
    sfx_file = _write_sfx_plan(project)

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "voice_full.mp3"
    audio_path.write_bytes(b"MP3")

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    clips = {}
    for fr in frames:
        p = videos / f"clip_{fr.number:03d}_aabbccdd.mp4"
        p.write_bytes(b"0" * 90_000)
        clips[fr.number] = p

    async def _timeline(*_a, **_kw):
        return (
            [
                FrameAudioClip(1, audio_path, "", 0.0, 3.0, 3.0),
                FrameAudioClip(2, audio_path, "", 3.0, 7.0, 4.0),
            ],
            7.0,
            1.0,
            True,
        )

    async def _scene_video_path(_session, _project, fr, *, shot=1):
        return clips[fr.number] if shot == 1 else None

    captured: dict[str, Any] = {}

    async def _assemble(clip_specs, _audio, out, **kwargs):
        captured["sfx"] = list(kwargs.get("sfx") or [])
        captured["clips"] = len(clip_specs)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"MP4")

    async def _send_hitl(*_a, **_kw):
        return None

    async def _on_child(*_a, **_kw):
        return None

    monkeypatch.setattr(step, "build_assembly_timeline", _timeline)
    monkeypatch.setattr(step, "_scene_video_path", _scene_video_path)
    monkeypatch.setattr(step, "assemble", _assemble)
    monkeypatch.setattr(step, "send_hitl_video", _send_hitl)
    monkeypatch.setattr("app.services.mass_factory.on_child_montage_complete", _on_child)

    await step._assemble_body(
        session,
        project,
        bot=None,
        frames=frames,
        frames_all=frames,
        skipped_no_video=[],
        audio=None,
        audio_path=audio_path,
        audio_dir=audio_dir,
        frame_numbers=[1, 2],
        per_frame_tts=True,
        subs_enabled=False,
        words=[],
        whisper_art=None,
        cells=[(1, "раз"), (2, "два")],
    )

    assert captured["clips"] == 2
    assert [s.path for s in captured["sfx"]] == [sfx_file.resolve()]
    assert project.status is ProjectStatus.assembled


@pytest.mark.asyncio
async def test_run_variant2_forwards_sfx_to_mux(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """montage-v3 целиком: SFX-вход доезжает до mux, а не теряется по дороге."""
    from app.services import sfx_mix
    from app.services.montage import variant2
    from app.services.montage.r15 import R15Marker

    project, frames = await _assemble_project(session, "v3-sfx")

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    for fr in frames:
        (videos / f"clip_{fr.number:03d}_aabbccdd.mp4").write_bytes(b"0" * 90_000)

    voice = project.data_dir / "audio" / "voice_full.mp3"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"MP3")
    sfx_file = tmp_path / "hit.wav"
    sfx_file.write_bytes(b"RIFF" + b"0" * 100)

    markers = [R15Marker(1, "раз", 0.0, 3.0), R15Marker(2, "два", 3.0, 7.0)]

    async def _load_markers(*_a, **_kw):
        return markers, None

    async def _probe_duration(_p) -> float:
        return 7.0

    async def _probe_size(_p) -> tuple[int, int]:
        return 1080, 1920

    async def _build_timeline(_project, _segments, *, w, h, voice_s, tmp):
        out = Path(tmp) / "timeline.mp4"
        out.write_bytes(b"MP4")
        return out

    captured: dict[str, Any] = {}

    async def _mux(_video, _voice, out, *, voice_s, bgm=None, sfx=None):
        captured["sfx"] = list(sfx or [])
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"MP4")

    monkeypatch.setattr(variant2, "_load_markers_db_first", _load_markers)
    monkeypatch.setattr(variant2, "probe_duration", _probe_duration)
    monkeypatch.setattr(variant2, "probe_video_size", _probe_size)
    monkeypatch.setattr(variant2, "_build_slot_timeline", _build_timeline)
    monkeypatch.setattr(variant2, "_mux", _mux)

    out_path = project.data_dir / "final" / "out.mp4"
    await variant2.run_variant2(
        project,
        [1, 2],
        voice,
        out_path,
        bgm=None,
        sfx=[sfx_mix.SfxInput(path=sfx_file, t_start=3.5, gain=0.6, kind="hit")],
    )

    assert [s.path for s in captured["sfx"]] == [sfx_file]
    assert out_path.is_file()
    # промежуточный pre-mux подчищен
    assert not (project.data_dir / "final" / "_variant2_pre_mux.mp4").exists()


# ── generate_hero: волна excel-персонажей закрывает свою транзакцию ───────


@pytest.mark.asyncio
async def test_run_excel_commits_between_waves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Волна пишет своими сессиями — своя транзакция обязана коммититься,
    иначе refresh читает снимок ДО волны и затирает её статусы."""
    import app.db as app_db
    from app.orchestrator.steps import generate_hero

    async with app_db.SessionLocal() as session:
        project = Project(slug="hero-wave", topic="t", hero_mode="excel")
        project.status = ProjectStatus.generating_hero
        project.meta = {
            "img_streams": 1,
            "excel_hero": {"characters": [{"id": "c01", "name": "Ёж", "look": "колючий"}]},
        }
        session.add(project)
        await session.commit()

        chars_dir = project.data_dir / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        png = chars_dir / "c01.png"

        calls: list[str] = []

        async def _fake_generate(s, p, _bot, ch, **_kw):
            calls.append(ch.id)
            png.write_bytes(b"PNG" * 400)
            s.add(
                Artifact(
                    project_id=p.id,
                    kind=ArtifactKind.hero_reference,
                    uuid=ch.id + "0" * 8,
                    path=str(png.resolve()),
                    meta={"excel_id": ch.id},
                )
            )
            await s.flush()

        monkeypatch.setattr(generate_hero, "_generate_one_excel_character", _fake_generate)

        await generate_hero._run_excel(session, project, bot=None, cfg=project.meta["excel_hero"])

        assert calls == ["c01"]
        assert project.status is ProjectStatus.hero_ready


# ── generate_images: сеть vs. успех в _generate_and_send ──────────────────


class _SheetStub:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def write_frame(self, number: int, **fields) -> None:
        self.rows.append({"number": number, **fields})


async def _image_project(session, slug: str) -> tuple[Project, Frame]:
    project = Project(slug=slug, topic="t", hero_mode="full_auto")
    project.status = ProjectStatus.generating_images
    session.add(project)
    await session.flush()
    frame = Frame(project_id=project.id, number=1, voiceover_text="закадр")
    frame.status = FrameStatus.image_prompt_ready
    frame.image_prompt = "тёплый вечер, чайник на подоконнике"
    frame.attrs = {}
    session.add(frame)
    await session.flush()
    return project, frame


@pytest.mark.asyncio
async def test_generate_and_send_requeues_frame_on_network_drop(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Провайдер отвалился по сети — кадр обратно в очередь, а не failed."""
    from app.orchestrator.steps import generate_images as gi
    from app.services.outsee_retry import OutseeImageError

    project, frame = await _image_project(session, "img-net")

    async def _boom(*_a, **_kw):
        raise OutseeImageError("all connection attempts failed")

    monkeypatch.setattr(gi, "generate_image_with_retries", _boom)
    monkeypatch.setattr(gi, "_sheet_for_project", lambda _p: _SheetStub())

    await gi._generate_and_send(
        session,
        None,
        None,
        SimpleNamespace(),
        project,
        frame,
        tmp_path,
    )

    assert frame.status is FrameStatus.image_prompt_ready
    assert frame.attrs[gi.TRANSIENT_RETRIES_ATTR] == 1


@pytest.mark.asyncio
async def test_generate_and_send_publishes_event_and_survives_bus_failure(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Картинка принята: Artifact + HITL, а упавшая шина не роняет шаг."""
    import sqlalchemy as sa

    from app.orchestrator.steps import generate_images as gi
    from app.services import event_bus, media_probe

    project, frame = await _image_project(session, "img-ok")

    png = tmp_path / "frame_001.png"
    png.write_bytes(b"PNG" * 400)

    async def _ok(*_a, **kwargs):
        out = Path(kwargs.get("out_path") or png)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PNG" * 400)
        return SimpleNamespace(file_path=out, raw_url="https://cdn/x.png")

    async def _probe_ok(*_a, **_kw):
        return None

    hitl_calls: list[dict] = []

    async def _send_hitl(_bot, _session, _project, **kwargs):
        hitl_calls.append(kwargs)

    async def _broken_bus(*_a, **_kw):
        raise RuntimeError("шина недоступна")

    monkeypatch.setattr(gi, "generate_image_with_retries", _ok)
    monkeypatch.setattr(gi, "_sheet_for_project", lambda _p: _SheetStub())
    monkeypatch.setattr(gi, "send_hitl_photo", _send_hitl)
    monkeypatch.setattr(media_probe, "probe_image", _probe_ok)
    monkeypatch.setattr(event_bus, "publish_project_event", _broken_bus)

    await gi._generate_and_send(
        session,
        None,
        None,
        SimpleNamespace(),
        project,
        frame,
        tmp_path,
    )

    assert frame.status is FrameStatus.image_generated
    assert len(hitl_calls) == 1
    rows = (await session.execute(sa.select(Artifact))).scalars().all()
    assert [r.kind for r in rows] == [ArtifactKind.scene_image]


# ── xlsx_step_runners: деградация разбивки всё равно проходит крышку ──────


@pytest.mark.asyncio
async def test_run_split_xlsx_clamps_degraded_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM не прошла контракт → локальная разбивка, но крышка в 30 кадров
    ставится и на деградации: иначе из минутного ролика выходит полсотни."""
    from app.services import xlsx_step_runners as xsr
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    project = Project(slug="split-degraded", topic="t", hero_mode="full_auto")
    project.id = 31
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    vo = "\n\n".join(f"Фраза номер {i} про тёплый вечер и чайник." for i in range(60))
    (project.data_dir / "voiceover.txt").write_text(vo, encoding="utf-8")

    prompt_file = project.data_dir / "tmp_gpt" / "prompt_split.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("prompt", encoding="utf-8")

    async def _ask(*_a, **_kw) -> str:
        return "Извините, не могу выполнить."

    async def _lock(_pid, _step, fn):
        return await fn()

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)
    monkeypatch.setattr(xsr.cx, "write_split_prompt_file", lambda *_a, **_kw: prompt_file)
    monkeypatch.setattr(xsr.cx, "chat_message", lambda *_a, **_kw: "go")

    result = await xsr.run_split_xlsx(project)
    assert result.degraded_no_llm is True
    assert 2 <= len(result.frames_spec) <= xsr.MAX_PARENT_FRAMES


@pytest.mark.asyncio
async def test_generate_one_excel_character_heals_polluted_name(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В ячейку имени уехала инструкция агенту — чиним до сборки промта,
    иначе служебный текст рисуется прямо на листе персонажа."""
    import contextlib

    from app.orchestrator.steps import generate_hero
    from app.services.excel_characters import ExcelCharacter

    project = Project(slug="hero-polluted", topic="t", hero_mode="excel")
    project.status = ProjectStatus.generating_hero
    project.meta = {}
    session.add(project)
    await session.flush()

    dirty = ExcelCharacter(id="c02", name="оставь формат неизменным", look="усатый")
    clean = ExcelCharacter(id="c01", name="Пётр", look="лысый")

    @contextlib.asynccontextmanager
    async def _no_browser(**_kw):
        yield None

    class _Gpt:
        async def ask_fresh(self, *_a, **_kw) -> str:
            return ""

    monkeypatch.setattr(generate_hero, "_optional_browser_session", _no_browser)
    monkeypatch.setattr(generate_hero, "_excel_hero_http_primary", lambda: True)
    monkeypatch.setattr(generate_hero, "get_gpt_client", lambda: _Gpt())
    monkeypatch.setattr(generate_hero, "_read_hero_style", lambda _p: "стиль")

    with pytest.raises(RuntimeError, match="не вернул заполненный промт"):
        await generate_hero._generate_one_excel_character(
            session,
            project,
            None,
            dirty,
            chars=[clean, dirty],
            approved=set(),
            batch_auto=True,
        )

    assert not dirty.name.lower().startswith("оставь формат")


@pytest.mark.asyncio
async def test_run_img_pr_xlsx_runs_wave_under_live_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Волна батчей ограничена img_pr_live_streams — семафор на волну."""
    import uuid as _uuid

    import app.db as app_db
    from app.services import gpt_client
    from app.services import xlsx_step_runners as xsr

    async with app_db.SessionLocal() as s:
        project = Project(slug="imgpr-wave", topic="t", hero_mode="full_auto")
        project.general_plan = "общий план ролика"
        project.meta = {"img_pr_live_streams": 1}
        s.add(project)
        await s.flush()
        frames = []
        for n in (1, 2):
            fr = Frame(project_id=project.id, number=n, voiceover_text=f"кадр {n}")
            fr.uuid = _uuid.uuid4().hex
            frames.append(fr)
        s.add_all(frames)
        await s.commit()
        uuids = [fr.uuid for fr in frames]

    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    (project.data_dir / "voiceover.txt").write_text("закадровый текст" * 10, encoding="utf-8")

    reply = json.dumps(
        {
            "ops": [
                {
                    "frame_uuid": u,
                    "fields": {
                        "промт_картинки": f"Тёплый вечер, план {i}. "
                        + "Мягкий свет, чайник на подоконнике, пар над чашкой, деревянный стол. " * 8
                    },
                }
                for i, u in enumerate(uuids, start=1)
            ]
        },
        ensure_ascii=False,
    )

    asked: list[str] = []

    class _Gpt:
        async def new_conversation(self) -> None:
            return None

        async def ask_with_files(self, chat_msg: str, _files, **_kw) -> str:
            asked.append(chat_msg)
            return reply

    monkeypatch.setattr(gpt_client, "ApiGptClient", _Gpt)

    result = await xsr.run_img_pr_xlsx(project, n_batches=2)
    assert len(asked) == 2
    got = {op["frame_uuid"] for op in result.apply_ops}
    assert got == set(uuids)


@pytest.mark.asyncio
async def test_assemble_body_subtitle_burn_timeout(session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Вшивание субтитров зависло — ffmpeg добиваем и падаем по таймауту,
    а не держим шаг бесконечно."""
    from app.orchestrator.steps import assemble as step

    project, frames = await _assemble_project(session, "asm-subs")
    _write_sfx_plan(project)

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "voice_full.mp3"
    audio_path.write_bytes(b"MP3")

    class _HangingFfmpeg:
        returncode: int | None = None

        def __init__(self) -> None:
            self.killed = False

        async def communicate(self):
            if not self.killed:
                await asyncio.sleep(10.0)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    proc = _HangingFfmpeg()

    async def _probe_duration(_p) -> float:
        return 7.0

    async def _probe_size(_p) -> tuple[int, int]:
        return 1080, 1920

    async def _run_variant2(_project, _nums, _audio, out, **_kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"MP4")

    async def _spawn(*_a, **_kw):
        return proc

    def _make_ass(_entries, path, **_kw):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("[Script Info]\n", encoding="utf-8")

    monkeypatch.setattr(step, "probe_duration", _probe_duration)
    monkeypatch.setattr(step, "probe_video_size", _probe_size)
    monkeypatch.setattr(step, "run_variant2", _run_variant2)
    monkeypatch.setattr(step, "build_subtitle_cues_from_cells", lambda *_a, **_kw: [(0.0, 3.0, "раз")])
    monkeypatch.setattr(step, "make_simple_ass", _make_ass)
    monkeypatch.setattr(step, "FFMPEG_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(step.asyncio, "create_subprocess_exec", _spawn)

    with pytest.raises(TimeoutError, match="subtitle burn timed out"):
        await step._assemble_body(
            session,
            project,
            bot=None,
            frames=frames,
            frames_all=frames,
            skipped_no_video=[],
            audio=None,
            audio_path=audio_path,
            audio_dir=audio_dir,
            frame_numbers=[1, 2],
            per_frame_tts=False,
            subs_enabled=True,
            words=[],
            whisper_art=None,
            cells=[(1, "раз"), (2, "два")],
        )
    assert proc.killed is True
