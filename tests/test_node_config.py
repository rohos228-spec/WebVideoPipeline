"""Конфиг узла в одном месте: `node.data.config` вместо трёх хранилищ.

Находка 12 живого прогона 2026-08-31. Проверяются рамки переезда, а не
геттеры: приоритет нового места над старым, полная совместимость со старыми
графами (где нового места нет вовсе), переезд конфига вместе с копией узла и
его смерть вместе с удалённым узлом.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.node_config import (
    CONFIG_PROMPT_SLOTS,
    all_prompt_slots,
    build_node_config,
    excel_gpt_config_for_node,
    migrate_graph_configs,
    model_id_of,
    prompt_slots_for_node,
    prune_configs_for_removed_nodes,
    set_prompt_slot,
    sync_prompt_slots_into_graph,
)
from app.services.project_graph import apply_project_graph, graph_diff, reset_plan
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nc.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    yield factory
    await engine.dispose()


def _graph(nodes, edges=None):
    return {"nodes": nodes, "edges": edges or [], "workflow_id": 0}


def _legacy_meta():
    """Старый проект: модель на узле, привязка промта — рядом с графом."""
    return {
        "canvas_graph": _graph(
            [
                {"id": "n_plan", "type": "plan", "data": {"modelId": "gpt-5.6-sol"}},
                {"id": "n_script", "type": "script", "data": {}},
            ],
            [{"id": "e0", "source": "n_plan", "target": "n_script"}],
        ),
        "prompt_slot_variants": {"n_plan": {"main": "my_plan"}},
        "excel_gpt_nodes": {"n_script": {"workMode": "review"}},
    }


# ── приоритет: новое место важнее старого ────────────────────────────────


def test_config_wins_over_legacy_fields_on_the_same_node():
    node = {
        "id": "n_plan",
        "type": "plan",
        "data": {"modelId": "старая", "config": {"modelId": "новая", CONFIG_PROMPT_SLOTS: {"main": "cfg"}}},
    }
    meta = {"canvas_graph": _graph([node]), "prompt_slot_variants": {"n_plan": {"main": "legacy"}}}
    assert model_id_of(node) == "новая"
    assert prompt_slots_for_node(meta, "n_plan") == {"main": "cfg"}


def test_priority_is_per_key_not_per_container():
    """Контейнер с одним ключом не обнуляет остальные.

    Узел, которому мигрировали только модель, обязан по-прежнему читать
    привязку промта из старого места — иначе первый же частичный переезд
    молча снял бы промт со всех узлов.
    """
    node = {"id": "n_plan", "type": "plan", "data": {"config": {"modelId": "новая"}}}
    meta = {"canvas_graph": _graph([node]), "prompt_slot_variants": {"n_plan": {"main": "legacy"}}}
    assert prompt_slots_for_node(meta, "n_plan") == {"main": "legacy"}


def test_empty_prompt_slots_in_config_means_binding_removed():
    """Снятую привязку нечем выразить, если пустой контейнер откатывается.

    `promptSlots: {}` авторитетен по факту наличия ключа: иначе снятие
    привязки молча возвращало бы старое значение из meta.
    """
    node = {"id": "n_plan", "type": "plan", "data": {"config": {CONFIG_PROMPT_SLOTS: {}}}}
    meta = {"canvas_graph": _graph([node]), "prompt_slot_variants": {"n_plan": {"main": "legacy"}}}
    assert prompt_slots_for_node(meta, "n_plan") == {}


def test_legacy_only_project_reads_exactly_as_before():
    meta = _legacy_meta()
    assert prompt_slots_for_node(meta, "n_plan") == {"main": "my_plan"}
    assert model_id_of(meta["canvas_graph"]["nodes"][0]) == "gpt-5.6-sol"
    assert excel_gpt_config_for_node(meta, "n_script") == {"workMode": "review"}
    assert all_prompt_slots(meta) == {"n_plan": {"main": "my_plan"}}


def test_meta_entries_without_a_node_in_graph_survive():
    """Метаданные времён Node Studio — общепроектный fallback, не мусор."""
    meta = _legacy_meta()
    meta["prompt_slot_variants"]["ghost"] = {"main": "studio_era"}
    assert all_prompt_slots(meta)["ghost"] == {"main": "studio_era"}
    assert prompt_slots_for_node(meta, "ghost") == {"main": "studio_era"}


# ── миграция: чистая функция, зовётся явно ───────────────────────────────


def test_migration_is_pure_and_leaves_input_untouched():
    meta = _legacy_meta()
    nodes = meta["canvas_graph"]["nodes"]
    before = [dict(n, data=dict(n["data"])) for n in nodes]
    out = migrate_graph_configs(nodes, meta)
    assert nodes == before, "миграция не имеет права править вход"
    assert out[0]["data"]["config"] == {"modelId": "gpt-5.6-sol", CONFIG_PROMPT_SLOTS: {"main": "my_plan"}}


def test_migration_does_not_inflate_nodes_without_config():
    """Узел без модели и без промта остаётся как был — пустой контейнер не пишем."""
    meta = _legacy_meta()
    out = migrate_graph_configs(meta["canvas_graph"]["nodes"], meta)
    assert "config" not in out[1]["data"]


def test_migration_does_not_move_excel_gpt_storage():
    """`excelGpt` миграцией не заполняется: там же лежит состояние прогона."""
    meta = _legacy_meta()
    out = migrate_graph_configs(meta["canvas_graph"]["nodes"], meta)
    assert all("excelGpt" not in n["data"].get("config", {}) for n in out)
    assert excel_gpt_config_for_node(meta, "n_script") == {"workMode": "review"}


def test_node_local_model_wins_over_already_migrated_container():
    """Пикер модели во фронте пишет `data.modelId` — свежий выбор обязан победить."""
    node = {"id": "n_plan", "type": "plan", "data": {"modelId": "свежая", "config": {"modelId": "старая"}}}
    assert build_node_config(node, {})["modelId"] == "свежая"


def test_migration_is_idempotent():
    meta = _legacy_meta()
    once = migrate_graph_configs(meta["canvas_graph"]["nodes"], meta)
    assert migrate_graph_configs(once, meta) == once


# ── диф и план сброса не должны сгореть на переезде ──────────────────────


def test_migrating_both_sides_keeps_the_diff_empty():
    """Иначе первое сохранение показало бы «изменены все узлы» на пустом месте."""
    meta = _legacy_meta()
    nodes = meta["canvas_graph"]["nodes"]
    edges = meta["canvas_graph"]["edges"]
    old = migrate_graph_configs(nodes, meta)
    new = migrate_graph_configs([dict(n, data=dict(n["data"])) for n in nodes], meta)
    assert graph_diff(old, edges, new, edges).empty


def test_mirror_container_alone_never_burns_a_done_step():
    """`config` только зеркалит — судят исходные поля, не он."""
    meta = _legacy_meta()
    nodes = meta["canvas_graph"]["nodes"]
    edges = meta["canvas_graph"]["edges"]
    migrated = migrate_graph_configs(nodes, meta)
    p = Project(slug="g", title="g", topic="t", status=ProjectStatus.frames_ready, meta={})
    d = graph_diff(nodes, edges, migrated, edges)
    assert d.changed_nodes, "контейнер действительно добавился"
    assert reset_plan(p, nodes, migrated, d).first_step is None


def test_real_model_change_still_burns_the_step():
    """Защита от переусердствования: настоящая правка модели по-прежнему жжёт."""
    meta = _legacy_meta()
    nodes = migrate_graph_configs(meta["canvas_graph"]["nodes"], meta)
    edges = meta["canvas_graph"]["edges"]
    changed = [dict(n, data=dict(n["data"])) for n in nodes]
    changed[0]["data"]["modelId"] = "другая"
    changed[0]["data"]["config"] = {**changed[0]["data"]["config"], "modelId": "другая"}
    p = Project(slug="g", title="g", topic="t", status=ProjectStatus.frames_ready, meta={})
    d = graph_diff(nodes, edges, changed, edges)
    assert reset_plan(p, nodes, changed, d).first_step == "plan"


# ── запись ───────────────────────────────────────────────────────────────


def test_set_prompt_slot_writes_both_places():
    meta = _legacy_meta()
    set_prompt_slot(meta, "n_plan", "main", "другой")
    node = meta["canvas_graph"]["nodes"][0]
    assert node["data"]["config"][CONFIG_PROMPT_SLOTS] == {"main": "другой"}
    assert meta["prompt_slot_variants"]["n_plan"] == {"main": "другой"}


def test_set_prompt_slot_with_empty_value_clears_both():
    meta = _legacy_meta()
    set_prompt_slot(meta, "n_plan", "main", None)
    assert meta["canvas_graph"]["nodes"][0]["data"]["config"][CONFIG_PROMPT_SLOTS] == {}
    assert "n_plan" not in meta["prompt_slot_variants"]
    assert prompt_slots_for_node(meta, "n_plan") == {}


def test_sync_only_touches_nodes_that_have_a_legacy_entry():
    """Перенос идёт legacy → контейнер; конфиг из ниоткуда не появляется."""
    meta = _legacy_meta()
    changed = sync_prompt_slots_into_graph(meta)
    assert changed == ["n_plan"]
    assert "config" not in meta["canvas_graph"]["nodes"][1]["data"]


def test_sync_does_not_wipe_a_copied_nodes_binding():
    """Копия узла везёт привязку в `data`; записи в бакете у неё нет."""
    meta = _legacy_meta()
    meta["canvas_graph"]["nodes"].append(
        {
            "id": "n_plan_copy",
            "type": "plan",
            "data": {"config": {CONFIG_PROMPT_SLOTS: {"main": "my_plan"}}},
        }
    )
    sync_prompt_slots_into_graph(meta)
    assert prompt_slots_for_node(meta, "n_plan_copy") == {"main": "my_plan"}


def test_patch_of_meta_carries_a_fresh_choice_into_the_container():
    """Инспектор пишет привязку патчем в meta — контейнер не должен её перебить."""
    from app.services.project_meta import merge_project_meta

    meta = _legacy_meta()
    meta = merge_project_meta(meta, {"prompt_slot_variants": {"n_plan": {"main": "свежий"}}}, source="test")
    assert meta["canvas_graph"]["nodes"][0]["data"]["config"][CONFIG_PROMPT_SLOTS] == {"main": "свежий"}
    assert prompt_slots_for_node(meta, "n_plan") == {"main": "свежий"}


# ── удаление узла уносит конфиг ──────────────────────────────────────────


def test_prune_drops_only_the_named_ids():
    meta = _legacy_meta()
    meta["prompt_slot_variants"]["ghost"] = {"main": "studio_era"}
    dropped = prune_configs_for_removed_nodes(meta, ["n_plan", "n_script"])
    assert set(dropped) == {"prompt_slot_variants:n_plan", "excel_gpt_nodes:n_script"}
    assert meta["prompt_slot_variants"] == {"ghost": {"main": "studio_era"}}
    assert meta["excel_gpt_nodes"] == {}


def test_prune_never_touches_step_results():
    meta = _legacy_meta()
    meta["storage_nodes"] = {"n_plan": {"files": ["a"]}}
    meta["gpt_operator_results"] = {"n_script": {"ok": True}}
    prune_configs_for_removed_nodes(meta, ["n_plan", "n_script"])
    assert meta["storage_nodes"] == {"n_plan": {"files": ["a"]}}
    assert meta["gpt_operator_results"] == {"n_script": {"ok": True}}


async def test_removing_a_node_takes_its_binding_with_it(db):
    """Ровно тот симптом находки 12: удалил узел — привязка висела в meta."""
    async with db() as s:
        p = Project(slug="p", title="p", topic="t", status=ProjectStatus.new, meta=_legacy_meta())
        s.add(p)
        await s.flush()
        nodes = [
            {"id": "n_topic", "type": "topic", "data": {}},
            {"id": "n_plan", "type": "plan", "data": {"modelId": "gpt-5.6-sol"}},
            {"id": "n_script", "type": "script", "data": {}},
        ]
        edges = [
            {"id": "e0", "source": "n_topic", "target": "n_plan"},
            {"id": "e1", "source": "n_plan", "target": "n_script"},
        ]
        await apply_project_graph(s, p, nodes, edges, reset=False)
        assert p.meta["prompt_slot_variants"]["n_plan"] == {"main": "my_plan"}
        assert prompt_slots_for_node(p.meta, "n_plan") == {"main": "my_plan"}

        # Узел ушёл — обе привязки обязаны уйти с ним.
        rest = [n for n in nodes if n["id"] == "n_topic"]
        await apply_project_graph(s, p, rest, [], reset=False)
        assert "n_plan" not in p.meta["prompt_slot_variants"]
        assert "n_script" not in p.meta["excel_gpt_nodes"]
        assert prompt_slots_for_node(p.meta, "n_plan") == {}


async def test_saving_the_graph_migrates_config_into_the_node(db):
    async with db() as s:
        p = Project(slug="p2", title="p", topic="t", status=ProjectStatus.new, meta=_legacy_meta())
        s.add(p)
        await s.flush()
        nodes = [
            {"id": "n_topic", "type": "topic", "data": {}},
            {"id": "n_plan", "type": "plan", "data": {"modelId": "gpt-5.6-sol"}},
        ]
        edges = [{"id": "e0", "source": "n_topic", "target": "n_plan"}]
        await apply_project_graph(s, p, nodes, edges, reset=False)
        plan = next(n for n in p.meta["canvas_graph"]["nodes"] if n["id"] == "n_plan")
        assert plan["data"]["config"] == {"modelId": "gpt-5.6-sol", CONFIG_PROMPT_SLOTS: {"main": "my_plan"}}
