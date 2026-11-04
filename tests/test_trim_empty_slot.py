"""Обрезка пустого слота — отказ, а не «Обрезка видео задана»."""

from __future__ import annotations

import asyncio

import pytest

from app.services.action_layer import builtin_montage_more as bm


class _Ctx:
    def __init__(self, video: bool) -> None:
        self.board = {"frames": [{"number": 1, "video_shot1_url": "/v.mp4" if video else None}], "meta": {}}
        self.posts: list = []

    async def call(self, method, path, params=None, json=None):
        if method == "GET":
            return self.board
        self.posts.append(json)
        return {"ok": True, "video_trims": (json or {}).get("video_trims"), "pending_ops": []}


def _trim(ctx):
    return asyncio.run(
        bm._set_video_trim(ctx, {"project_id": 4, "frame_number": 1, "shot": 1, "start": 0, "end": 2})
    )


def test_trim_on_empty_slot_is_refused():
    ctx = _Ctx(video=False)
    with pytest.raises(ValueError, match="нет видео"):
        _trim(ctx)
    assert ctx.posts == []


def test_trim_with_video_is_written():
    ctx = _Ctx(video=True)
    _trim(ctx)
    assert ctx.posts and ctx.posts[0]["video_trims"]["1:1"] == {"start": 0.0, "end": 2.0}
