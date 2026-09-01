"""Модель узла читается из одного слоя, что бы ни передали.

Часть находки 12: конфиг узла жил в трёх местах. `read_node_model_fields`
принимает и узел целиком, и сразу его `data` — вызывающие в проекте делают и
так, и так, и разойтись эти пути не должны.
"""

from __future__ import annotations

from app.services.vibecode_catalog import read_node_model_fields


def test_none_is_not_a_model() -> None:
    assert read_node_model_fields(None) == (None, "stable")


def test_not_a_dict_is_not_a_model() -> None:
    assert read_node_model_fields("строка") == (None, "stable")  # type: ignore[arg-type]


def test_reads_legacy_model_id_from_node() -> None:
    node = {"id": "n1", "type": "plan", "data": {"modelId": "claude-opus-5"}}
    assert read_node_model_fields(node)[0] == "claude-opus-5"


def test_container_wins_over_legacy() -> None:
    node = {
        "id": "n1",
        "type": "plan",
        "data": {"modelId": "gpt-5.6-sol", "config": {"modelId": "claude-opus-5"}},
    }
    assert read_node_model_fields(node)[0] == "claude-opus-5"


def test_bare_data_dict_is_accepted() -> None:
    """Передали `data`, а не узел — тот же ответ, а не молчаливый None."""
    assert read_node_model_fields({"modelId": "gemini-3.1-pro-preview"})[0] == ("gemini-3.1-pro-preview")


def test_bare_data_dict_with_container() -> None:
    assert read_node_model_fields({"config": {"modelId": "kimi-k3"}})[0] == "kimi-k3"


def test_node_without_model_gives_none() -> None:
    assert read_node_model_fields({"id": "n1", "data": {}}) == (None, "stable")
