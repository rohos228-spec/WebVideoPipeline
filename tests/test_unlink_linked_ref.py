"""Отвязка приложенного рефа по короткому имени («predmet2» для ассета «predmet2_b0c165a8»)."""

from __future__ import annotations

import asyncio

import pytest

from app.services.action_layer import builtin_montage_more as bm


class _Ctx:
    def __init__(self, refs: list[dict]) -> None:
        self.refs = refs
        self.posts: list[dict] = []

    async def call(self, method: str, path: str, params: dict | None = None):
        if method == "GET":
            return {"frames": [{"number": 1, "manual_refs": self.refs}]}
        self.posts.append(dict(params or {}))
        return {"ok": True, "refs": []}


def _run(ctx: _Ctx, ref_id: str) -> dict:
    return asyncio.run(bm._unlink_ref(ctx, {"project_id": 4, "frame_number": 1, "ref_id": ref_id, "kind": "linked"}))


def test_short_name_finds_the_linked_asset() -> None:
    ctx = _Ctx([{"id": "a1", "name": "predmet2_b0c165a8", "linked": True}])
    out = _run(ctx, "predmet2")
    assert out["deleted"] and ctx.posts == [{"frame_number": 1, "ref_id": "a1", "kind": "manual"}]


def test_short_name_does_not_match_a_longer_one() -> None:
    ctx = _Ctx([{"id": "a1", "name": "predmet22_b0c165a8", "linked": True}])
    with pytest.raises(ValueError):
        _run(ctx, "predmet2")
    assert ctx.posts == []


def test_uploaded_file_is_not_unlinked() -> None:
    ctx = _Ctx([{"id": "u1", "name": "predmet2_ffff", "linked": False}])
    with pytest.raises(ValueError):
        _run(ctx, "predmet2")


_URL = "/api/files?path=C%3A%5Cd%5Citems%5Cpredmet3_6c6ddac2.png&v=1"


def test_linking_the_same_file_again_adds_nothing() -> None:
    ctx = _Ctx([{"id": "a1", "name": "predmet3_6c6ddac2", "linked": True, "image_url": _URL}])
    out = asyncio.run(bm._link_ref(ctx, {"project_id": 4, "frame_number": 1, "file": "items/predmet3_6c6ddac2.png"}))
    assert out["already_linked"] and ctx.posts == []


def test_two_copies_of_one_file_are_both_unlinked() -> None:
    ctx = _Ctx([{"id": "a1", "name": "predmet3_6c6ddac2", "linked": True, "image_url": _URL},
                {"id": "a2", "name": "predmet3_6c6ddac2", "linked": True, "image_url": _URL}])
    out = _run(ctx, "predmet3")
    assert out["removed"] == 2 and [p["ref_id"] for p in ctx.posts] == ["a1", "a2"]
