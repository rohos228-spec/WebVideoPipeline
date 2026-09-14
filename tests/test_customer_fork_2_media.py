"""Перенос медийного блока из форка заказчика (theirs/main c53a238b).

Здесь собраны проверки того, что переносилось руками и потому легко
разъезжается с оригиналом: ручка перегенерации клипа, крышка на число
кадров разбивки, исцеление служебных имён персонажей, предметы из
сущностей и живучесть скачивания.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots.outsee import OutseeContentRejectedError, OutseeImageError
from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Entity,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
)
from app.monitor.report import render_html_report
from app.orchestrator.steps import generate_hero, generate_items
from app.services.gpt_text_builder import (
    HERO_PLACEHOLDER_BRIEF,
    HERO_PLACEHOLDER_STYLE,
    render_hero_text,
)
from app.services.outsee_retry import _is_transient_network_error
from app.services.plan_shot2 import SHOT2_STATUS_ATTR
from app.services.xlsx_step_runners import (
    _IMG_PR_LIVE_STREAMS,
    clamp_parent_frames,
    img_pr_live_streams,
)
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session

# ───────────────────────── ручка перегенерации клипа ─────────────────────────


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'frames-api.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.factory = factory  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def _seed_frame_with_clip(client, *, with_artifact: bool = True) -> tuple[Project, Frame, Path]:
    async with client.factory() as s:
        p = Project(slug="regen-clip", topic="тема", status=ProjectStatus.generating_videos, meta={})
        s.add(p)
        await s.flush()
        fr = Frame(
            project_id=p.id,
            number=3,
            voiceover_text="реплика",
            animation_prompt="camera slowly pushes in",
            status=FrameStatus.video_generated,
            attrs={
                "video_gen_skip": "OutseeImageError",
                "video_gen_inflight": True,
                "video_inflight": True,  # имя из форка
                "video_gen_fail_count": 5,
                SHOT2_STATUS_ATTR: "image_generated",
                "keep_me": 1,
            },
        )
        s.add(fr)
        await s.flush()
        videos = p.data_dir / "videos"
        videos.mkdir(parents=True, exist_ok=True)
        clip = videos / "clip_003_abcdef12.mp4"
        clip.write_bytes(b"x" * 2048)
        (videos / "clip_003_s2_abcdef12.mp4").write_bytes(b"x" * 2048)
        if with_artifact:
            s.add(
                Artifact(
                    project_id=p.id,
                    frame_id=fr.id,
                    kind=ArtifactKind.scene_video,
                    uuid="a" * 32,
                    path=str(clip),
                    meta={},
                )
            )
        await s.commit()
        return p, fr, clip


async def test_regenerate_video_clears_clip_artifact_and_flags(client) -> None:
    p, fr, clip = await _seed_frame_with_clip(client)

    res = await client.post(f"/api/projects/{p.id}/frames/{fr.id}/regenerate-video")
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "frame_id": fr.id, "frame_number": 3}

    # Клипы обоих shot уехали в архив, рядом с новым их больше нет.
    videos = p.data_dir / "videos"
    assert not clip.exists()
    assert list(videos.glob("clip_003_*.mp4")) == []
    assert [q.name for q in (p.data_dir / "old" / "videos").glob("*clip_003_*.mp4")]

    async with client.factory() as s:
        arts = (
            (
                await s.execute(
                    select(Artifact).where(
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_video,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert arts == []
        fresh = await s.get(Frame, fr.id)
        assert fresh is not None
        # Снимаем ровно флаги генерации, остальные attrs кадра не трогаем.
        assert fresh.attrs == {"keep_me": 1}
        assert fresh.status is FrameStatus.animation_prompt_ready


async def test_regenerate_video_checks_project_of_frame(client) -> None:
    p, fr, _clip = await _seed_frame_with_clip(client, with_artifact=False)
    res = await client.post(f"/api/projects/{p.id + 777}/frames/{fr.id}/regenerate-video")
    assert res.status_code == 404
    res = await client.post(f"/api/projects/{p.id}/frames/{fr.id + 777}/regenerate-video")
    assert res.status_code == 404


# ───────────────────────── крышка кадров разбивки ─────────────────────────


def test_clamp_parent_frames_under_limit() -> None:
    frames = [{"закадр": f"Фраза {i}", "длительность": 2.5} for i in range(1, 21)]
    assert clamp_parent_frames(frames, max_frames=30) == frames

    frames_30 = [{"закадр": f"Фраза {i}", "длительность": 2.0} for i in range(1, 31)]
    assert clamp_parent_frames(frames_30, max_frames=30) == frames_30


def test_clamp_parent_frames_over_limit_preserves_text_and_durations() -> None:
    original = [{"закадр": f"Слово{i}", "длительность": 1.0} for i in range(1, 51)]
    clamped = clamp_parent_frames(original, max_frames=30)
    assert len(clamped) == 30
    # Текст закадра сохраняется слово в слово и в том же порядке.
    assert " ".join(f["закадр"] for f in original) == " ".join(f["закадр"] for f in clamped)
    assert pytest.approx(sum(f["длительность"] for f in original), 0.01) == sum(
        f["длительность"] for f in clamped
    )


def test_clamp_parent_frames_canonical_contract_keys() -> None:
    """После контракта ключи канонические (`voiceover_text`), не «закадр»."""
    original = [{"voiceover_text": f"Слово{i}", "duration_seconds": 1.0} for i in range(1, 41)]
    clamped = clamp_parent_frames(original, max_frames=30)
    assert len(clamped) == 30
    assert all("voiceover_text" in f for f in clamped)
    assert " ".join(f["voiceover_text"] for f in original) == " ".join(f["voiceover_text"] for f in clamped)
    assert pytest.approx(40.0, 0.01) == sum(f["duration_seconds"] for f in clamped)


def test_clamp_parent_frames_edge_cases() -> None:
    assert clamp_parent_frames([]) == []
    assert clamp_parent_frames([{"закадр": "Один"}]) == [{"закадр": "Один"}]


def test_img_pr_live_streams_meta_override() -> None:
    assert img_pr_live_streams(None) == _IMG_PR_LIVE_STREAMS
    p = Project(slug="streams", topic="t", meta={})
    assert img_pr_live_streams(p) == _IMG_PR_LIVE_STREAMS
    p.meta = {"img_pr_streams": 6}
    assert img_pr_live_streams(p) == 6
    # Верхняя граница — 8, мусор в meta игнорируется.
    p.meta = {"img_pr_streams": 20}
    assert img_pr_live_streams(p) == 8
    p.meta = {"img_pr_streams": "не число"}
    assert img_pr_live_streams(p) == _IMG_PR_LIVE_STREAMS


def test_split_db_hint_carries_shorts_limit() -> None:
    from app.services.xlsx_step_runners import _SPLIT_DB_HINT

    assert "15–25" in _SPLIT_DB_HINT
    assert "≤30" in _SPLIT_DB_HINT


# ───────────────────────── имена персонажей ─────────────────────────


def _entity(code: str, name: str, **attrs) -> Entity:
    return Entity(
        id=int(code[1:]),
        project_id=992,
        type="character",
        code=code,
        name=name,
        attrs=attrs or {"look": "внешность"},
        sort_key=float(code[1:]),
    )


def _session_returning(rows: list) -> AsyncMock:
    scalars = MagicMock()
    scalars.all.return_value = rows
    result = MagicMock()
    result.scalars.return_value = scalars
    session = AsyncMock()
    session.execute.return_value = result
    session.flush = AsyncMock()
    return session


async def test_load_excel_hero_auto_heals_polluted_names(tmp_path, monkeypatch) -> None:
    """Служебный текст агента в имени c02 больше не роняет шаг."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = Project(
        id=992,
        slug="healed",
        topic="т",
        status=ProjectStatus.generating_hero,
        hero_mode="auto",
        # Ручной hero есть, но excel-режим включён явно — приоритет у сущностей.
        hero_description="Ткач",
        hero_count=1,
        meta={"excel_hero_enabled": True},
    )
    ents = [
        _entity("c01", "Ткач", look="ткач"),
        _entity("c02", "оставь формат неизменным", look="ткач в грязной одежде", rules="c01"),
    ]

    cfg = await generate_hero._load_excel_hero_from_xlsx(_session_returning(ents), project)

    assert cfg is not None
    assert cfg["source"] == "entity"
    chars = cfg["characters"]
    assert [c["id"] for c in chars] == ["c01", "c02"]
    assert chars[1]["name"] == "Ткач (вариация c02)"
    assert project.meta["excel_hero"]["characters"][1]["name"] == "Ткач (вариация c02)"


def test_healed_character_name_without_parent() -> None:
    """Родителя в ref_ids нет — имя всё равно осмысленное, не текст агента."""
    from app.services.excel_characters import ExcelCharacter

    ch = ExcelCharacter(id="c03", name="не выдумывай", look="x", ref_ids=[])
    assert generate_hero._healed_character_name(ch, {}) == "Персонаж c03"
    ch2 = ExcelCharacter(id="c04", name="не выдумывай", look="x", ref_ids=["c01"])
    assert generate_hero._healed_character_name(ch2, {"c01": "Ткач"}) == "Ткач (вариация c04)"


def test_render_hero_text_preserves_style_without_placeholder() -> None:
    rendered = render_hero_text(
        "Создай промт для персонажа на белом фоне.",
        brief="Мальчик 12 лет",
        hero_style="Cinematic 8k photorealism, soft lighting",
    )
    assert "Visual style (применять обязательно):" in rendered
    assert "Cinematic 8k photorealism, soft lighting" in rendered
    assert "Описание персонажа:" in rendered
    assert "Мальчик 12 лет" in rendered


def test_render_hero_text_with_placeholders() -> None:
    template = f"Шаблон:\nStyle: {HERO_PLACEHOLDER_STYLE}\nBrief: {HERO_PLACEHOLDER_BRIEF}"
    rendered = render_hero_text(template, brief="Художник", hero_style="Anime style")
    assert "Style: Anime style" in rendered
    assert "Brief: Художник" in rendered
    # Плейсхолдеры были — хвост не дописывается.
    assert "Visual style (применять обязательно):" not in rendered


def test_render_html_report_validity() -> None:
    analysis = {
        "total_events": 42,
        "screenshots_count": 5,
        "projects_seen": [101, 102],
        "timing": {
            "img_pr": {"count": 4, "total_s": 45.2, "avg_s": 11.3, "min_s": 8.0, "max_s": 15.0},
        },
        "errors": [
            {
                "ts": "2026-09-07T12:34:56",
                "project_id": 101,
                "error_type": "DNSLookupError",
                "error_msg": "Yandex Cloud DNS timeout",
                "screenshot": "screen_101.png",
            }
        ],
        "event_counts": {"step_start": 14, "step_end": 14, "error": 1},
    }
    html = render_html_report(analysis, title="Тестовый отчёт")
    assert "<!DOCTYPE html>" in html
    assert "Тестовый отчёт" in html
    assert "#101" in html
    assert "DNSLookupError" in html
    assert "Yandex Cloud DNS timeout" in html
    assert "45.2s" in html


# ───────────────────────── предметы ─────────────────────────


async def test_resolve_item_descriptions_from_project() -> None:
    session = AsyncMock()
    project = Project(id=1, slug="i", topic="t", item_descriptions=["  Меч  ", "", "Болтер"])
    assert await generate_items._resolve_item_descriptions(session, project) == ["Меч", "Болтер"]
    session.execute.assert_not_called()


async def test_resolve_item_descriptions_from_entities() -> None:
    project = Project(id=1, slug="i", topic="t", item_descriptions=[])
    ents = [
        Entity(
            id=10,
            project_id=1,
            type="prop",
            name="Реликварий",
            attrs={"description": "Древний золотой ларец"},
            sort_key=1.0,
        ),
        Entity(id=11, project_id=1, type="item", name="Штандарт", attrs={}, sort_key=2.0),
    ]
    session = _session_returning(ents)

    descs = await generate_items._resolve_item_descriptions(session, project)

    assert descs == ["Реликварий: Древний золотой ларец", "Штандарт"]
    # Поле проекта заполняется, чтобы дальше по шагу был один источник.
    assert project.item_descriptions == descs
    session.flush.assert_awaited()


async def test_existing_item_indices_artifact_and_disk(tmp_path: Path) -> None:
    project = Project(id=1, slug="items-disk", topic="t")
    art = Artifact(
        id=1,
        project_id=1,
        kind=ArtifactKind.item_reference,
        uuid="b" * 32,
        meta={"item_index": 1},
    )
    session = _session_returning([art])

    items_dir = tmp_path / "items"
    items_dir.mkdir()
    (items_dir / "predmet2_abcdef12.png").write_bytes(b"x" * 2000)
    (items_dir / "predmet7_ffffffff.png").write_bytes(b"x" * 10)  # огрызок — не в счёт

    with patch.object(Project, "data_dir", new_callable=PropertyMock, return_value=tmp_path):
        indices = await generate_items._existing_item_indices(session, project)

    assert indices == {1, 2}


async def test_run_items_without_descriptions_marks_skipped(tmp_path: Path) -> None:
    project = Project(
        id=1,
        slug="items-empty",
        topic="t",
        status=ProjectStatus.generating_items,
        item_descriptions=[],
        meta={},
    )
    session = _session_returning([])
    await generate_items.run(session, project, AsyncMock())
    assert project.status is ProjectStatus.items_ready
    assert project.meta.get("items_skipped_empty") is True


# ───────────────────────── живучесть сети ─────────────────────────


def test_is_transient_network_error() -> None:
    assert _is_transient_network_error(socket.gaierror("getaddrinfo failed")) is True
    assert _is_transient_network_error(OutseeImageError("image fetch: getaddrinfo failed")) is True
    assert _is_transient_network_error(ConnectionResetError("connection reset by peer")) is True
    assert _is_transient_network_error(OutseeImageError("504 Gateway Time-out")) is False
    assert _is_transient_network_error(OutseeImageError("read timed out")) is True
    assert _is_transient_network_error(OutseeContentRejectedError("moderation policy")) is False
    assert _is_transient_network_error(ValueError("Invalid prompt format")) is False


def test_requeue_frame_on_transient_network_error() -> None:
    """Сеть оборвалась — кадр возвращается в очередь, но не бесконечно."""
    from app.orchestrator.steps.generate_images import (
        MAX_TRANSIENT_NET_RETRIES,
        TRANSIENT_RETRIES_ATTR,
        _requeue_frame_on_transient,
    )

    fr = Frame(id=1, number=1, voiceover_text="v", status=FrameStatus.failed, attrs={})
    err = OutseeImageError("image fetch: getaddrinfo failed")

    for n in range(1, MAX_TRANSIENT_NET_RETRIES + 1):
        assert _requeue_frame_on_transient(fr, err, is_shot2=False) is True
        assert fr.attrs[TRANSIENT_RETRIES_ATTR] == n
        assert fr.status is FrameStatus.image_prompt_ready
    # Попытки исчерпаны — дальше обычная ветка отказа.
    assert _requeue_frame_on_transient(fr, err, is_shot2=False) is False

    # Не сетевая ошибка — никакого реквея.
    fresh = Frame(id=2, number=2, voiceover_text="v", status=FrameStatus.failed, attrs={})
    assert _requeue_frame_on_transient(fresh, ValueError("bad prompt"), is_shot2=False) is False
    assert fresh.attrs == {}

    # Второй кадр shot2 живёт своим attr, статус кадра не трогаем.
    s2 = Frame(id=3, number=3, voiceover_text="v", status=FrameStatus.image_generated, attrs={})
    assert _requeue_frame_on_transient(s2, err, is_shot2=True) is True
    assert s2.attrs[SHOT2_STATUS_ATTR] == "image_prompt_ready"
    assert s2.status is FrameStatus.image_generated


def test_download_via_context_defaults_to_five_attempts() -> None:
    import inspect

    from app.bots import outsee

    sig = inspect.signature(outsee._download_via_context)
    assert sig.parameters["attempts"].default == 5
