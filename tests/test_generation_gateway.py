"""Тесты для шлюза и маршрутизатора генераций (generation_gateway)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
    StudioUser,
)
from app.services.generation_gateway import GenerationContext, GenerationGateway, gateway
from app.web.deps import get_session


@pytest.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def test_gateway_route_resolution() -> None:
    # Kie image
    prim, fallbacks = GenerationGateway.resolve_image_route("flux-2-pro")
    assert prim == "kie"
    assert "outsee" in fallbacks

    # Outsee image
    prim, fallbacks = GenerationGateway.resolve_image_route("gpt-image-2")
    assert prim == "outsee"
    assert "kie" in fallbacks

    # Kie video
    prim, fallbacks = GenerationGateway.resolve_video_route("kling-2-6")
    assert prim == "kie"
    assert "outsee" in fallbacks

    # Outsee video
    prim, fallbacks = GenerationGateway.resolve_video_route("veo-3-1-lite")
    assert prim == "outsee"
    assert "kie" in fallbacks


@pytest.mark.asyncio
async def test_record_artifact_and_call_atomic(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    session = async_session
    tenant_id = str(uuid.uuid4())

    user = StudioUser(id=tenant_id, email="artist@test.com", display_name="Artist")
    session.add(user)

    project = Project(
        title="Gateway Project",
        slug="gw-proj",
        topic="AI Film",
        status=ProjectStatus.new,
        tenant_id=tenant_id,
    )
    session.add(project)
    await session.flush()

    frame = Frame(
        project_id=project.id,
        number=1,
        voiceover_text="Intro",
        status=FrameStatus.planned,
    )
    session.add(frame)
    await session.commit()

    # Создаём файл
    test_img = tmp_path / "frame_001_gen.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    ctx = GenerationContext(
        user_id=tenant_id,
        tenant_id=tenant_id,
        project_id=project.id,
        frame_id=frame.id,
        node_key="test_node",
        shot=1,
    )

    art, mc = await gateway.record_artifact_and_call(
        session,
        ctx,
        kind=ArtifactKind.scene_image,
        file_path=test_img,
        provider="kie",
        model="flux-2-pro",
        units=1.0,
        result="ok",
        duration_ms=2350,
    )
    await session.commit()

    assert art is not None
    assert art.tenant_id == tenant_id
    assert art.project_id == project.id
    assert art.frame_id == frame.id
    assert art.kind == ArtifactKind.scene_image
    assert art.path == str(test_img.resolve())

    assert mc.tenant_id == tenant_id
    assert mc.project_id == project.id
    assert mc.node_key == "test_node"
    assert mc.provider == "kie"
    assert mc.result == "ok"
    assert mc.duration_ms == 2350

    # Статус кадра обновился
    await session.refresh(frame)
    assert frame.status == FrameStatus.image_generated


@pytest.mark.asyncio
async def test_generation_api_routes(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    from app.web.api import create_app

    session = async_session
    tenant_id = str(uuid.uuid4())
    user = StudioUser(id=tenant_id, email="user@test.com", display_name="User")
    session.add(user)

    project = Project(
        title="Api Project",
        slug="api-proj",
        topic="API Test",
        status=ProjectStatus.new,
        tenant_id=tenant_id,
    )
    session.add(project)
    await session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Проверяем список маршрутов
        routes_res = await client.get("/api/generate/routes")
        assert routes_res.status_code == 200
        routes_data = routes_res.json()
        assert "images" in routes_data
        assert "videos" in routes_data
        assert any(m["model"] == "flux-2-pro" for m in routes_data["images"])

        # 2. Проверяем регистрацию внешней генерации
        dummy_file = tmp_path / "external_gen.png"
        dummy_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        record_res = await client.post(
            "/api/generate/record",
            json={
                "kind": "image",
                "model": "gpt-image-2",
                "provider": "outsee",
                "file_path": str(dummy_file),
                "project_id": project.id,
                "units": 1.0,
                "duration_ms": 1500,
                "result": "ok",
            },
        )
        assert record_res.status_code == 200
        rec_data = record_res.json()
        assert rec_data["success"] is True
        assert rec_data["artifact_id"] is not None
        assert rec_data["media_call_id"] is not None
