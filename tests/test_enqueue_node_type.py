from __future__ import annotations

from app.services.action_layer import builtin_launch as bl

_META = {"canvas_graph": {"nodes": [{"id": "n_videos", "type": "videos"}]}}


class _Ctx:
    def __init__(self):
        self.posts = []

    async def call(self, method, path, **kw):
        if method == "POST":
            self.posts.append(kw["json"])
            return {"position": 1}
        return {"id": 10, "auto_mode": True, "meta": _META}


async def test_node_key_brings_its_type_into_queue_request() -> None:
    ctx = _Ctx()
    await bl._enqueue_many(ctx, {"project_ids": [10], "mode": "until_node", "target_node_key": "n_videos"})
    assert ctx.posts == [{"project_id": 10, "mode": "until_node", "target_node_key": "n_videos",
                          "target_node_type": "videos"}]


async def test_graph_type_beats_planner_guess() -> None:
    ctx = _Ctx()
    await bl._enqueue_many(ctx, {"project_ids": [10], "mode": "until_node", "target_node_key": "n_videos",
                                 "target_node_type": "video"})
    assert ctx.posts[0]["target_node_type"] == "videos"


def test_unknown_node_key_has_no_type() -> None:
    assert bl._node_type_of({"meta": _META}, "n_video") is None


async def test_remove_many_skips_projects_not_in_queue() -> None:
    from app.services.action_layer.context import ActionCallError

    class Ctx:
        async def call(self, method, path, **kw):
            if kw["json"]["project_id"] == 7:
                raise ActionCallError(400, "use POST /gen-queue/enqueue")
            return {"gen_queue": []}

    assert await bl._dequeue_many(Ctx(), {"project_ids": [6, 7]}) == {"count": 1, "removed": [6]}
