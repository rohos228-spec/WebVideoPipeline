"""Ручки /api/projects/{id}/nii67* (роутер перенесён из video-pipeline).

Проверяем то, что зовёт студия (web/src/lib/api.ts, nii67-card-panel.tsx):
форма ответов, слоты загрузки студии, раздача файла для превью и то, что
поля матричной карточки студии переживают сохранение. Модель не зовётся:
генерация плана и раскадровки подменена.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.models import Project, ProjectStatus
from app.web.deps import get_session
from app.web.routers import nii67 as nii67_router


def _app() -> FastAPI:
    import app.db as app_db

    async def _session():
        async with app_db.SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(nii67_router.router, prefix="/api")
    app.dependency_overrides[get_session] = _session
    return app


async def _project() -> int:
    import app.db as app_db

    async with app_db.SessionLocal() as s:
        p = Project(slug="nii", topic="t", status=ProjectStatus.new)
        s.add(p)
        await s.commit()
        return int(p.id)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t")


def _studio_card() -> dict:
    """Карточка в форме студии: матричные типы блоков и новые поля."""
    block = {
        "id": "b_death1",
        "kind": "death",
        "collapsed": False,
        "text": "герой падает",
        "text2": "второй кадр",
        "image_prompt": "",
        "image_name": "block_b_death1_1__a.png",
        "image_name2": "block_b_death1_2__b.png",
        "outcome": "",
        "custom_title": "",
        "exit_action": "",
        "exit_animation": "",
        "room_after": "",
        "character": {"enabled": False},
    }
    creature = {**block, "id": "b_cr1", "kind": "creature", "outcome": "run", "text2": ""}
    state = {"death_cause": "холод", "place": "зал"}
    return {
        "version": 1,
        "intro": {"text": "", "video_name": "intro_video__src.mp4"},
        "portal": {
            "left_image_name": "portal_left_image__l.png",
            "right_image_name": "portal_right_image__r.png",
        },
        "gg": {"name": "Ник", "note": ""},
        "hands_image_name": "hands_image__h.png",
        "doors": [
            {
                "id": "door1",
                "blocks": [block, creature],
                "move": "death_world",
                "creature_action": "",
                "state": state,
            },
            {"id": "door2", "blocks": [], "move": "creature", "creature_action": "kill", "state": {}},
        ],
        "finale": {"standard": "", "notes": ""},
        "plan": {"door1": {"world": "", "happens": ""}, "door2": {"world": "", "happens": ""}, "finale": ""},
        "generation": {"version": 2, "models": {"llm_model_id": "gpt-x", "junk": "y"}},
    }


async def test_get_default_card_and_404() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.get(f"/api/projects/{pid}/nii67")
        assert r.status_code == 200
        body = r.json()
        assert [d["id"] for d in body["card"]["doors"]] == ["door1", "door2"]
        assert body["frames"] == []
        assert (await c.get("/api/projects/9999/nii67")).status_code == 404


async def test_studio_card_fields_survive_save_and_reload() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.put(f"/api/projects/{pid}/nii67?mode=draft", json={"card": _studio_card()})
        assert r.status_code == 200, r.text
        card = r.json()["card"]
        assert card["last_save"] == "draft"
        got = (await c.get(f"/api/projects/{pid}/nii67")).json()["card"]
    for out in (card, got):
        d1, d2 = out["doors"]
        assert [b["kind"] for b in d1["blocks"]] == ["death", "creature"]
        death, creature = d1["blocks"]
        assert death["text2"] == "второй кадр"
        assert death["image_name2"] == "block_b_death1_2__b.png"
        assert death["outcome"] == ""
        assert creature["outcome"] == "run"
        assert d1["move"] == "death_world"
        assert d1["state"]["death_cause"] == "холод"
        assert d1["state"]["place"] == "зал"
        assert d2["move"] == "creature" and d2["creature_action"] == "kill"
        assert out["portal"]["left_image_name"] == "portal_left_image__l.png"
        assert out["portal"]["right_image_name"] == "portal_right_image__r.png"
        assert out["hands_image_name"] == "hands_image__h.png"
        assert out["generation"] == {"version": 2, "models": {"llm_model_id": "gpt-x"}}


async def test_put_rejects_bad_mode_and_non_card() -> None:
    pid = await _project()
    async with _client() as c:
        assert (
            await c.put(f"/api/projects/{pid}/nii67?mode=x", json={"card": _studio_card()})
        ).status_code == 400
        assert (await c.put(f"/api/projects/{pid}/nii67", json={"card": {"foo": 1}})).status_code == 400


async def test_preview_and_reset() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/preview", json={"card": _studio_card()})
        assert r.status_code == 200
        assert "ИНТРО" in r.json()["text"]
        assert "Смерть" in r.json()["text"]
        r = await c.post(f"/api/projects/{pid}/nii67/reset")
        assert r.status_code == 200
        assert r.json()["card"]["last_save"] == "save"


@pytest.mark.parametrize(
    "slot",
    [
        "intro_video",
        "portal_left_image",
        "portal_right_image",
        "hands_image",
        "block_b_abc123_1",
        "door_door1_image",
    ],
)
async def test_upload_studio_slots_and_serve_asset(slot: str) -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.post(
            f"/api/projects/{pid}/nii67/upload?slot={slot}",
            files={"file": ("my photo.png", b"PNGDATA", "image/png")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slot"] == slot
        assert body["name"] == f"{slot}__my_photo.png"
        assert body["size"] == 7
        assert body["path"] == f"nii67_assets/{slot}__my_photo.png"
        r = await c.get(f"/api/projects/{pid}/nii67/asset/{body['name']}")
        assert r.status_code == 200
        assert r.content == b"PNGDATA"


async def test_upload_rejects_unknown_slot_and_asset_is_confined() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.post(
            f"/api/projects/{pid}/nii67/upload?slot=../../etc",
            files={"file": ("a.png", b"x", "image/png")},
        )
        assert r.status_code == 400
        assert (await c.get(f"/api/projects/{pid}/nii67/asset/nope.png")).status_code == 404
        assert (await c.get(f"/api/projects/{pid}/nii67/asset/..%2Fstate.db")).status_code == 404


async def test_put_frames_validates_contract() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.put(f"/api/projects/{pid}/nii67/frames", json={"frames": "x"})
        assert r.status_code == 400
        r = await c.put(f"/api/projects/{pid}/nii67/frames", json={"frames": [{"slot_id": "s1"}]})
        assert r.status_code == 400
        assert "invalid" in r.json()["detail"]

        def frame(slot: str) -> dict:
            return {
                "slot_id": slot,
                "door": "door1",
                "beat": "entry",
                "start_frame": "a",
                "end_frame": "b",
                "camera": "close",
                "camera_feel": "handheld_soft",
                "characters_on": [],
                "action": "идёт",
                "refs": [{"type": "style", "name": "s", "attached": True}],
                "duration_hint": 3,
            }

        r = await c.put(f"/api/projects/{pid}/nii67/frames", json={"frames": [frame("f1"), frame("f2")]})
        assert r.status_code == 200, r.text
        cams = [f["camera"] for f in r.json()["frames"]]
        assert cams == ["close", "side"]  # два close подряд в одной двери разводятся
        got = (await c.get(f"/api/projects/{pid}/nii67")).json()
        assert [f["slot_id"] for f in got["frames"]] == ["f1", "f2"]


async def test_plan_uses_generator_and_saves(monkeypatch) -> None:
    pid = await _project()
    plan = {
        "door1": {"world": "лёд", "happens": "падает", "outcome": "death", "category": ""},
        "door2": {"world": "лес", "happens": "бежит", "outcome": "escape", "category": ""},
        "finale": "возврат",
    }

    async def _gen_plan(card, *, project):
        assert project.id == pid
        return plan

    monkeypatch.setattr(nii67_router, "generate_plan", _gen_plan)
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/plan", json={"card": _studio_card()})
        assert r.status_code == 200, r.text
        assert r.json()["card"]["plan"]["door1"]["world"] == "лёд"
        got = (await c.get(f"/api/projects/{pid}/nii67")).json()["card"]
        assert got["plan"]["door2"]["happens"] == "бежит"


async def test_plan_provider_unavailable_is_422(monkeypatch) -> None:
    from app.services.gpt_client import GptApiUnavailable

    pid = await _project()

    async def _gen_plan(card, *, project):
        raise GptApiUnavailable("нет ключа")

    monkeypatch.setattr(nii67_router, "generate_plan", _gen_plan)
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/plan", json={"card": _studio_card()})
        assert r.status_code == 422


async def test_storyboard_requires_plan_then_writes_general_plan(monkeypatch) -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/storyboard", json={"card": _studio_card()})
        assert r.status_code == 400

        card = _studio_card()
        card["plan"] = {
            "door1": {"world": "лёд", "happens": "падает"},
            "door2": {"world": "лес", "happens": "бежит"},
            "finale": "",
        }
        frames = [{"slot_id": "door1.0.death", "beat": "entry"}]

        async def _gen(card_in, *, project, plan):
            assert plan["door1"]["world"] == "лёд"
            return card_in, {"frames": frames}

        monkeypatch.setattr(nii67_router, "generate_scenarios", _gen)
        monkeypatch.setattr(nii67_router, "render_plan", lambda agent: "ПЛАН КАДРОВ")
        r = await c.post(f"/api/projects/{pid}/nii67/storyboard", json={"card": card})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["frames"] == frames
        assert body["general_plan"].endswith("ПЛАН КАДРОВ")
        assert body["card"]["generated_text"] == body["general_plan"]
        got = (await c.get(f"/api/projects/{pid}/nii67")).json()
        assert got["frames"] == frames


def _matrix_card(move1: str, move2: str, **state) -> dict:
    from app.services.nii67_card import default_card

    card = default_card()
    for door, move in zip(card["doors"], (move1, move2), strict=False):
        door["move"] = move
        door["state"] = {**door["state"], "world_description": "затопленный архив", **state}
        if move == "creature":
            door["creature_action"] = "interact"
    return card


async def test_matrix_plan_without_move_is_a_draft_and_not_saved() -> None:
    pid = await _project()
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/matrix-plan", json={"card": _matrix_card("", "")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preview"] is True
        assert any("не выбран ход" in i for i in body["issues"])
        assert body["frames"]
        got = await c.get(f"/api/projects/{pid}/nii67")
    # Кадры черновика не пишутся, а карточка пишется: студия перечитывает её после ответа.
    assert got.json()["frames"] == []
    assert got.json()["card"]["doors"][0]["state"]["world_description"] == "затопленный архив"


async def test_matrix_plan_builds_and_saves_frames_without_gpt() -> None:
    pid = await _project()
    card = _matrix_card(
        "death_world",
        "creature",
        death_action="тянется к рубильнику",
        death_cause="удар током",
        interaction_result="существо отдаёт ключ",
    )
    async with _client() as c:
        r = await c.post(f"/api/projects/{pid}/nii67/matrix-plan", json={"card": card})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preview"] is False, body["issues"]
        frames = body["frames"]
        assert any(f["slot_id"].startswith("door1.") for f in frames)
        assert any(f["slot_id"].startswith("door2.") for f in frames)
        text = " ".join(str(f) for f in frames)
        assert "рубильник" in text and "ключ" in text
        got = await c.get(f"/api/projects/{pid}/nii67")
    assert len(got.json()["frames"]) == len(frames)
    assert got.json()["card"]["doors"][0]["move"] == "death_world"
