import asyncio

from app.services.action_layer import builtin_insight as bi


def test_blocking_counted_over_all(monkeypatch):
    every = [
        {"blocking": True, "node": "", "title": f"i{n}", "fact": "", "retry": "нет", "what_to_do": ""}
        for n in range(14)
    ]
    every += [{"blocking": False, "node": "", "title": "j", "fact": "", "retry": "нет", "what_to_do": ""}] * 2

    async def incidents(pid, *, include_closed=False, node_key=None, limit=20, labels=None):
        return every[:limit], len(every)

    async def nothing(*args, **kwargs):
        return None

    async def feed(pid, limit):
        return []

    monkeypatch.setattr(bi, "_incidents", incidents)
    monkeypatch.setattr(bi, "_project_row", nothing)
    monkeypatch.setattr(bi, "_feed", feed)
    out = asyncio.run(bi._diag_incidents(None, {"project_id": 12, "limit": 5}))
    assert "инцидентов 16, блокирующих 14" in out["text_ru"]
