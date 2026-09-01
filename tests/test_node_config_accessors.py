"""Слой доступа к конфигу узла: мусор на входе не должен ронять чтение.

Находка 12: конфиг узла жил в трёх местах, и каждый читатель разбирал его
сам. Теперь путь один — `node_config`, и он обязан переживать любую форму
`meta`: старый граф, кривой JSON, отсутствующий узел.
"""

from __future__ import annotations

import pytest

from app.services.node_config import (
    all_prompt_slots,
    excel_gpt_config_for_node,
    find_node,
    graph_nodes,
    legacy_excel_gpt_config,
    prompt_slots_for_node,
)


def _canvas(nodes: list) -> dict:
    return {"canvas_graph": {"workflow_id": 2, "nodes": nodes, "edges": []}}


@pytest.mark.parametrize("meta", [None, {}, "строка", {"canvas_graph": "не словарь"}])
def test_graph_nodes_survives_garbage(meta) -> None:
    assert graph_nodes(meta) == []


def test_graph_nodes_skips_non_dict_entries() -> None:
    meta = _canvas([{"id": "n1"}, "мусор", None, 42])
    assert [n["id"] for n in graph_nodes(meta)] == ["n1"]


def test_graph_nodes_empty_when_nodes_not_a_list() -> None:
    assert graph_nodes({"canvas_graph": {"nodes": "не список"}}) == []


@pytest.mark.parametrize("node_id", [None, "", "   "])
def test_find_node_needs_an_id(node_id) -> None:
    assert find_node(_canvas([{"id": "n1"}]), node_id) is None


def test_find_node_returns_none_for_unknown() -> None:
    assert find_node(_canvas([{"id": "n1"}]), "n2") is None


@pytest.mark.parametrize("meta", [None, {}, {"prompt_slot_variants": "не словарь"}])
def test_prompt_slots_for_unknown_node_is_empty(meta) -> None:
    assert prompt_slots_for_node(meta, "n1") == {}


def test_prompt_slots_needs_a_node_id() -> None:
    meta = {"prompt_slot_variants": {"n1": {"main": "v"}}}
    assert prompt_slots_for_node(meta, "") == {}


def test_prompt_slots_from_legacy_bucket() -> None:
    meta = {"prompt_slot_variants": {"n1": {"main": "sd_action"}}}
    assert prompt_slots_for_node(meta, "n1") == {"main": "sd_action"}


def test_prompt_slots_container_wins() -> None:
    meta = _canvas([{"id": "n1", "data": {"config": {"promptSlots": {"main": "sd_camera"}}}}])
    meta["prompt_slot_variants"] = {"n1": {"main": "sd_action"}}
    assert prompt_slots_for_node(meta, "n1") == {"main": "sd_camera"}


def test_all_prompt_slots_merges_both_sides() -> None:
    meta = _canvas([{"id": "n1", "data": {"config": {"promptSlots": {"main": "sd_camera"}}}}])
    meta["prompt_slot_variants"] = {"n2": {"main": "sd_action"}}
    got = all_prompt_slots(meta)
    assert got["n1"] == {"main": "sd_camera"}
    assert got["n2"] == {"main": "sd_action"}


def test_all_prompt_slots_skips_nodes_without_id() -> None:
    meta = _canvas([{"data": {"config": {"promptSlots": {"main": "x"}}}}])
    assert all_prompt_slots(meta) == {}


@pytest.mark.parametrize("meta", [None, {}, {"excel_gpt_nodes": "не словарь"}])
def test_legacy_excel_gpt_config_survives_garbage(meta) -> None:
    assert legacy_excel_gpt_config(meta, "n1") == {}


def test_excel_gpt_config_from_legacy_bucket() -> None:
    meta = {"excel_gpt_nodes": {"n1": {"checkMode": True}}}
    assert excel_gpt_config_for_node(meta, "n1") == {"checkMode": True}


def test_excel_gpt_config_container_wins() -> None:
    meta = _canvas([{"id": "n1", "data": {"config": {"excelGpt": {"checkMode": False}}}}])
    meta["excel_gpt_nodes"] = {"n1": {"checkMode": True}}
    assert excel_gpt_config_for_node(meta, "n1") == {"checkMode": False}


def test_excel_gpt_config_needs_a_node_id() -> None:
    assert excel_gpt_config_for_node({"excel_gpt_nodes": {"n1": {"a": 1}}}, None) == {}
