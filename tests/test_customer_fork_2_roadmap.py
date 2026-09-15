"""Перенос из форка заказчика (Roadmap V2, theirs/main c53a238b) — блок «ядро».

Покрывает:
1. курсор спанов сцен в ``db_apply`` (одинаковые ``start_words`` в разных сценах);
2. реестр running-статусов как SoT для ``gen_queue`` / ``project_state``;
3. ``_IMG_EXTENSIONS`` (.png/.jpg/.jpeg/.webp) в recovery / reset / montage;
4. таймауты ffmpeg в ``assembly`` и ``frame_audio`` (+ kill процесса);
5. ``commit_with_retry`` вместо голых ``session.commit()`` в роутерах;
6. ``asyncio.get_running_loop()`` вместо ``get_event_loop()``;
7. вычищенный tokenrouter/Kimi из настроек и каталога ошибок.

Тесты по sfx / variant2 / LINEAR_NODE_TYPES / enhance_prompt — в другом блоке.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import ProjectStatus
from app.services import gen_queue, project_state, step_registry
from app.services.db_apply import _scene_span_in_text, expand_scene_registry_onto_frames

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1. Спаны сцен
# --------------------------------------------------------------------------


def test_scene_span_cursor_with_repeating_words() -> None:
    full = (
        "В темной комнате сидел детектив. Часы тикали. Дверь открылась. "
        "В темной комнате сидел свидетель. Он молчал. Дверь закрылась."
    )
    s1 = _scene_span_in_text(full, "В темной комнате", "Дверь открылась.", start_offset=0)
    assert s1 is not None
    assert s1[0] == 0
    assert full[s1[0] : s1[1]] == "В темной комнате сидел детектив. Часы тикали. Дверь открылась."

    s2 = _scene_span_in_text(full, "В темной комнате", "Дверь закрылась.", start_offset=s1[1])
    assert s2 is not None
    assert s2[0] > s1[0]
    assert full[s2[0] : s2[1]] == "В темной комнате сидел свидетель. Он молчал. Дверь закрылась."


def test_scene_span_falls_back_to_start_when_offset_missed() -> None:
    full = "Первая сцена. Вторая сцена."
    span = _scene_span_in_text(full, "Первая", "сцена.", start_offset=100)
    assert span is not None
    assert span[0] == 0


def test_expand_scene_registry_onto_frames_sequential() -> None:
    f1 = SimpleNamespace(attrs={}, voiceover_text="В темной комнате сидел детектив.")
    f2 = SimpleNamespace(attrs={}, voiceover_text="Часы тикали. Дверь открылась.")
    f3 = SimpleNamespace(attrs={}, voiceover_text="В темной комнате сидел свидетель.")
    f4 = SimpleNamespace(attrs={}, voiceover_text="Он молчал. Дверь закрылась.")
    frames = [f1, f2, f3, f4]
    registry = [
        {
            "id_scene": "sc_01",
            "start_words": "В темной комнате",
            "end_words": "Дверь открылась.",
            "место": "комната 1",
        },
        {
            "id_scene": "sc_02",
            "start_words": "В темной комнате",
            "end_words": "Дверь закрылась.",
            "место": "комната 2",
        },
    ]

    applied = expand_scene_registry_onto_frames(frames, registry)  # type: ignore[arg-type]
    assert applied == 4
    assert f1.attrs["shot01_id_scene"] == "sc_01"
    assert f2.attrs["shot01_id_scene"] == "sc_01"
    assert f3.attrs["shot01_id_scene"] == "sc_02"
    assert f4.attrs["shot01_id_scene"] == "sc_02"
    assert f1.attrs["place"] == "комната 1"
    assert f3.attrs["place"] == "комната 2"


# --------------------------------------------------------------------------
# 2. Реестр running-статусов
# --------------------------------------------------------------------------


def test_running_statuses_are_single_source_of_truth() -> None:
    running = step_registry.running_statuses()
    running_list = step_registry.running_statuses_list()
    assert set(running_list) == running

    assert running_list == gen_queue.GEN_QUEUE_BUSY_STATUSES
    # Их не было в литеральном списке gen_queue — из-за этого очередь считала
    # проект свободным во время веера сцен.
    assert ProjectStatus.scene_designing in gen_queue.GEN_QUEUE_BUSY_STATUSES
    assert ProjectStatus.scene_assembling in gen_queue.GEN_QUEUE_BUSY_STATUSES
    # publish-нода есть в реестре нод, пункта меню нет — статус не терять.
    assert ProjectStatus.publishing in gen_queue.GEN_QUEUE_BUSY_STATUSES

    assert running == project_state._RUNNING_STATUSES
    for st in running:
        assert project_state.is_running_status(st) is True
    assert project_state.is_running_status(ProjectStatus.plan_ready) is False


def test_running_statuses_have_no_literal_lists_left() -> None:
    src = (REPO_ROOT / "app" / "services" / "gen_queue.py").read_text(encoding="utf-8")
    assert "GEN_QUEUE_BUSY_STATUSES = running_statuses_list()" in src
    assert "ProjectStatus.enriching_5," not in src


# --------------------------------------------------------------------------
# 3. Расширения картинок
# --------------------------------------------------------------------------


def test_img_extensions_single_definition() -> None:
    from app.services.plan_shot2 import _IMG_EXTENSIONS

    assert frozenset({".png", ".jpg", ".jpeg", ".webp"}) == _IMG_EXTENSIONS

    for rel in (
        "app/services/artifact_recovery.py",
        "app/services/reset_step.py",
        "app/services/montage_board_assets.py",
        "app/services/montage_outsee_recover.py",
        "app/services/agent_harness.py",
        "app/services/animation_prompt_gpt.py",
        "app/services/vision_check_loop.py",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "_IMG_EXTENSIONS" in src, rel
        assert "_IMG_EXTENSIONS: frozenset" not in src, f"{rel}: дубль определения"


def test_restore_and_backup_handle_jpg_webp(tmp_path: Path) -> None:
    from app.services import artifact_recovery, reset_step

    project_dir = tmp_path / "proj"
    scenes_dir = project_dir / "scenes"
    old_scenes = project_dir / "old" / "scenes" / "20260910_120000"
    old_scenes.mkdir(parents=True)
    (old_scenes / "frame_001_abc12345.webp").write_bytes(b"webp data" * 20)
    (old_scenes / "frame_002_def67890.jpg").write_bytes(b"jpeg data" * 20)
    (old_scenes / "frame_003_ghi11223.png").write_bytes(b"png data" * 20)
    (old_scenes / "frame_004_note.txt").write_text("не картинка", encoding="utf-8")

    proj = SimpleNamespace(id=42, data_dir=project_dir)

    stats = artifact_recovery.restore_scene_images_from_old(proj)  # type: ignore[arg-type]
    assert stats["restored"] == 3
    assert (scenes_dir / "frame_001_abc12345.webp").is_file()
    assert (scenes_dir / "frame_002_def67890.jpg").is_file()
    assert (scenes_dir / "frame_003_ghi11223.png").is_file()
    assert not (scenes_dir / "frame_004_note.txt").exists()

    backed_up = reset_step._backup_scenes_before_wipe(proj, scenes_dir)  # type: ignore[arg-type]
    assert backed_up == 3


def test_hero_char_regex_accepts_jpg_and_webp() -> None:
    from app.services.artifact_recovery import _CHAR_ID_IN_NAME_RE, _CHAR_ID_RE

    for name in ("c01.png", "c01.jpg", "c01.jpeg", "c01.webp"):
        assert _CHAR_ID_RE.match(name), name
        assert _CHAR_ID_IN_NAME_RE.search(f"hero_{name}"), name
    assert _CHAR_ID_RE.match("c01.mp4") is None


def test_delete_scene_image_patterns_cover_all_extensions() -> None:
    from app.services.montage_board_assets import _shot1_image_patterns

    pats = _shot1_image_patterns(7)
    assert sorted(pats) == sorted(
        [
            "frame_007_*.jpeg",
            "frame_007_*.jpg",
            "frame_007_*.png",
            "frame_007_*.webp",
        ]
    )
    src = (REPO_ROOT / "app" / "services" / "montage_board_assets.py").read_text(encoding="utf-8")
    # Баг форка: delete_scene_image ходил по одному pattern, а не по списку.
    assert "for pattern in patterns:" in src


def test_outsee_recover_prefix_reads_any_extension() -> None:
    from app.services.montage_outsee_recover import rebuild_prefix_from_filename

    for name in ("frame_003_abcd1234.png", "frame_003_abcd1234.webp", "frame_003_s2_abcd1234.jpg"):
        assert rebuild_prefix_from_filename(7, Path(name)) is not None, name
    assert rebuild_prefix_from_filename(7, Path("frame_003_abcd1234.mp4")) is None


def test_index_scene_image_paths_sees_webp(tmp_path: Path) -> None:
    from app.services.animation_prompt_gpt import index_scene_image_paths

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_aaaa1111.webp").write_bytes(b"x")
    (scenes / "frame_002_bbbb2222.jpg").write_bytes(b"x")
    (scenes / "frame_002_s2_cccc3333.png").write_bytes(b"x")
    (scenes / "frame_003_dddd4444.mp4").write_bytes(b"x")

    project = SimpleNamespace(data_dir=tmp_path)
    idx = index_scene_image_paths(project)  # type: ignore[arg-type]
    assert set(idx) == {1, 2}
    assert idx[2].name == "frame_002_bbbb2222.jpg"


# --------------------------------------------------------------------------
# 4. Таймауты ffmpeg
# --------------------------------------------------------------------------


class _HangingProc:
    def __init__(self) -> None:
        self.killed = False
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(10.0)
        return b"", b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_assembly_run_kills_hanging_ffmpeg(monkeypatch) -> None:
    from app.services import assembly

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(assembly.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(assembly, "FFMPEG_TIMEOUT_SEC", 0.01)
    assert assembly.FFMPEG_TIMEOUT_SEC == 0.01
    with pytest.raises(TimeoutError, match="ffmpeg timed out"):
        await assembly._run(["ffmpeg", "-i", "dummy.mp4"])
    assert proc.killed is True


@pytest.mark.asyncio
async def test_frame_audio_run_ffmpeg_kills_hanging_process(monkeypatch) -> None:
    from app.services import frame_audio

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(frame_audio.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(TimeoutError, match="ffmpeg timed out"):
        await frame_audio._run_ffmpeg(["ffmpeg", "-i", "dummy.mp3"], timeout=0.01)
    assert proc.killed is True


def test_ffmpeg_timeout_defaults() -> None:
    from app.services.assembly import FFMPEG_TIMEOUT_SEC as assemble_timeout
    from app.services.frame_audio import FFMPEG_TIMEOUT_SEC as audio_timeout

    assert assemble_timeout == 300.0
    assert audio_timeout == 120.0

    src = (REPO_ROOT / "app" / "orchestrator" / "steps" / "assemble.py").read_text(encoding="utf-8")
    assert "asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT_SEC)" in src
    assert "ffmpeg subtitle burn timed out" in src


# --------------------------------------------------------------------------
# 5. commit_with_retry
# --------------------------------------------------------------------------


def test_routers_use_commit_with_retry() -> None:
    for name in (
        "db_browser",
        "projects",
        "project_ops",
        "prompt_studio",
        "fleet",
        "library",
    ):
        src = (REPO_ROOT / "app" / "web" / "routers" / f"{name}.py").read_text(encoding="utf-8")
        assert "await session.commit()" not in src, name
        assert "from app.db import commit_with_retry" in src, name
        assert "await commit_with_retry(session)" in src, name


@pytest.mark.asyncio
async def test_commit_with_retry_retries_locked_sqlite() -> None:
    from sqlalchemy.exc import OperationalError

    from app.db import commit_with_retry

    calls = {"commit": 0, "rollback": 0}

    class FakeSession:
        async def commit(self) -> None:
            calls["commit"] += 1
            if calls["commit"] < 3:
                raise OperationalError("COMMIT", {}, Exception("database is locked"))

        async def rollback(self) -> None:
            calls["rollback"] += 1

    await commit_with_retry(FakeSession(), base_delay=0.001)  # type: ignore[arg-type]
    assert calls["commit"] == 3
    assert calls["rollback"] == 0


# --------------------------------------------------------------------------
# 6. get_running_loop
# --------------------------------------------------------------------------


def test_no_legacy_get_event_loop_in_ported_modules() -> None:
    """Замена устаревшего вызова взята только там, где она проверяема.

    CDP-боты (`browser`, `chatgpt`, `elevenlabs`, `grsai`, `publishers`) из
    этой проверки исключены намеренно: замена там ничего не меняла (внутри
    корутины старый вызов отдаёт тот же running loop), а 45 изменённых строк
    лежат в коде, который без живого Chrome не проверить — diff-cov завернул
    push именно на них, и правка вынута из переноса.
    """
    for rel in ("app/services/step_cancel.py",):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "asyncio.get_event_loop()" not in src, rel
        assert "asyncio.get_running_loop()" in src, rel


# --------------------------------------------------------------------------
# 7. tokenrouter / Kimi вычищены
# --------------------------------------------------------------------------


def test_settings_have_no_tokenrouter() -> None:
    from app.settings import Settings, settings

    for field in ("tokenrouter_api_key", "tokenrouter_base_url", "tokenrouter_model"):
        assert field not in Settings.model_fields, field
        assert not hasattr(settings, field), field
    assert not hasattr(settings, "text_llm_is_tokenrouter")
    assert settings.resolved_text_llm_provider() in {"kie", "vibecode"}


def test_error_catalog_points_at_vibecode() -> None:
    from app.services.error_catalog import ERROR_CATALOG

    blob = "\n".join(f"{spec.title} {spec.hint}" for spec in ERROR_CATALOG.values())
    assert "TOKENROUTER" not in blob.upper()
    assert "VIBECODE_API_KEY" in blob


def test_tokenrouter_gone_from_core_sources() -> None:
    for rel in (
        "app/settings.py",
        "app/services/gpt_api.py",
        "app/services/gpt_client.py",
        "app/services/error_catalog.py",
        "app/web/routers/db_browser.py",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"tokenrouter", src, re.IGNORECASE), rel
