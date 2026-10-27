"""node.get_llm_route называет провайдера: «через какого провайдера идёт нода» без него — «в данных нет»."""

import pytest

from app.services.action_layer import builtin_group as bg


class _Ctx:
    def __init__(self, model: dict) -> None:
        self.model = model

    async def call(self, method: str, path: str) -> dict:
        return {"model": self.model}


@pytest.mark.asyncio
async def test_route_names_provider(monkeypatch) -> None:
    monkeypatch.setattr(bg, "_forced_vibecode", lambda key: False)
    monkeypatch.setattr(bg, "_text_provider", lambda model_id: "kie")
    out = await bg._node_get_llm_route(_Ctx({"model_id": "gpt-5.6-sol", "model_label": "GPT 5.6 Sol"}),
                                       {"project_id": 12, "node_key": "n_script"})
    assert out["provider"] == "kie"
    assert out["text_ru"] == "Модель GPT 5.6 Sol, запросы идут через провайдера Kie."


@pytest.mark.asyncio
async def test_forced_group_goes_through_vibecode(monkeypatch) -> None:
    monkeypatch.setattr(bg, "_forced_vibecode", lambda key: True)
    out = await bg._node_get_llm_route(_Ctx({"model_id": "gpt-5.6-sol"}), {"project_id": 12, "node_key": "n_x"})
    assert out["provider"] == "vibecode" and "Vibecode" in out["text_ru"]


def test_claude_model_is_vibecode() -> None:
    assert bg._text_provider("claude-haiku-4-5") == "vibecode"
