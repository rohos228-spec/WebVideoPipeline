from __future__ import annotations

import pytest

from app.orchestrator.agents.project_nodes import ProjectNodes, named_projects

_ITEMS = [
    {"id": 4, "title": "2 коротких факта о совах", "status": "paused"},
    {"id": 2, "title": "3 факта о котах", "status": "new"},
]


async def _items():
    return _ITEMS


async def test_factory_scope_lists_projects_and_names_the_one_from_title() -> None:
    nodes = ProjectNodes(project_of=lambda _t: None, projects_of=_items)
    text = await nodes("сними проект 2 коротких факта о совах с паузы", 48)
    assert "- 4: «2 коротких факта о совах», paused" in text
    assert "это project_id=4" in text
    assert "цифры в названии" in text


async def test_no_title_in_request_gives_list_without_hint() -> None:
    nodes = ProjectNodes(project_of=lambda _t: None, projects_of=_items)
    text = await nodes("какие проекты сейчас идут", 1)
    assert "- 2: «3 факта о котах», new" in text
    assert "это project_id" not in text


async def test_named_project_brings_its_node_roster() -> None:
    meta = {
        "canvas_graph": {
            "nodes": [{"id": "image_prompts", "type": "img_pr", "data": {"label": "Промты картинок"}}]
        }
    }
    asked = []

    async def meta_of(pid):
        asked.append(pid)
        return meta

    async def frames_of(_pid):
        return 13

    nodes = ProjectNodes(project_of=lambda _t: None, projects_of=_items, meta_of=meta_of, frames_of=frames_of)
    text = await nodes("какая модель на ноде промты картинок в проекте 2 коротких факта о совах", 53)
    assert set(asked) == {4}
    assert "это project_id=4" in text and "Ноды проекта" in text


@pytest.mark.xfail(
    reason="в prompts/05_excel_gpt/.file_meta.json новой линии нет title у вариантов", strict=False
)
async def test_prompt_request_lists_node_variants_with_titles() -> None:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_image_prompts", "type": "image_prompts", "data": {"label": "Промты картинок"}},
                {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
            ]
        }
    }

    async def meta_of(_pid):
        return meta

    async def frames_of(_pid):
        return 13

    nodes = ProjectNodes(project_of=lambda _t: 4, meta_of=meta_of, frames_of=frames_of)
    text = await nodes(
        "на ноду работа с GPT, связанную с промтами картинок, поставь промт Сцены: единая грамматика", 1
    )
    assert "node.set_prompt_variant" in text
    assert "- scene_grammar_unified_agent_v1: Сцены: единая грамматика сцен v1.8" in text


async def test_release_request_gets_blocked_keys() -> None:
    async def meta_of(_pid):
        return {}

    async def frames_of(_pid):
        return 13

    async def blocked_of(_pid):
        return [{"key": "frame_005", "provider": "outsee"}]

    nodes = ProjectNodes(project_of=lambda _t: 4, meta_of=meta_of, frames_of=frames_of, blocked_of=blocked_of)
    text = await nodes("сними блокировку с запросов картинок", 1)
    assert "- key=frame_005, provider=outsee" in text
    assert "frame_005" not in await nodes("запусти картинки", 1)


def test_blocked_answer_names_frames_and_provider() -> None:
    from app.services.action_layer.builtin_media_recovery import unknown_text

    text = unknown_text([{"key": "frame_005", "provider": "outsee", "blocked_until": "2026-10-20T17:52:08"}])
    assert "кадр 5 (Outsee)" in text and "17:52" in text


async def test_undo_request_gets_snapshot_ids() -> None:
    async def meta_of(_pid):
        return {}

    async def frames_of(_pid):
        return 13

    async def snapshots_of(_pid):
        return [
            {
                "snapshot_id": 1,
                "kind": "prompt_slot",
                "target": {"node_key": "n_excel_gpt_2"},
                "before": None,
                "after": "scene_grammar_unified_agent_v1",
            }
        ]

    nodes = ProjectNodes(
        project_of=lambda _t: 4, meta_of=meta_of, frames_of=frames_of, snapshots_of=snapshots_of
    )
    text = await nodes("верни прежний промт на второй ноде работа с GPT", 1)
    assert "- snapshot_id=1: prompt_slot n_excel_gpt_2, было «по умолчанию»" in text
    assert "snapshot_id=1" not in await nodes("покажи промт", 1)


def test_numbered_projects_only_after_the_word_project() -> None:
    from app.orchestrator.agents.project_nodes import numbered_projects

    items = [{"id": i, "title": f"t{i}"} for i in (2, 10, 11, 12)]
    assert numbered_projects(items, "в проектах 10, 11 и 12 поставь промт") == [10, 11, 12]
    assert numbered_projects(items, "запусти 2 коротких факта о совах") == []
    assert numbered_projects(items, "у проекта №11 и проекта 99") == [11]


def test_longest_title_wins() -> None:
    items = [
        {"id": 1, "title": "факты о совах"},
        {"id": 4, "title": "2 коротких факта о совах"},
        {"id": 5, "title": "совы"},
    ]
    assert [i["id"] for i in named_projects(items, "запусти 2 коротких факта о совах")] == [4]


async def test_numbered_projects_get_next_step_from_graph() -> None:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"slotIndex": 1}},
                {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"slotIndex": 2}},
                {"id": "n_image_prompts", "type": "image_prompts"},
            ]
        }
    }

    async def items():
        return [{"id": 10, "title": "t10", "status": "enrich_2_ready"}]

    async def meta_of(_pid):
        return meta

    async def frames_of(_pid):
        return 15

    nodes = ProjectNodes(project_of=lambda _t: None, projects_of=items, meta_of=meta_of, frames_of=frames_of)
    text = await nodes("в проекте 10 запусти следующий шаг", 1)
    assert "step_code=img_pr" in text and "enrich_3" not in text


async def test_project_scope_next_step_request_gets_next_step_from_graph() -> None:
    """Вкладка проекта: «запусти следующий шаг» получает step_code, как во «Всей фабрике»."""
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"slotIndex": 1}},
                {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"slotIndex": 2}},
                {"id": "n_image_prompts", "type": "image_prompts"},
            ]
        }
    }

    async def meta_of(_pid):
        return meta

    async def frames_of(_pid):
        return 15

    async def status_of(_pid):
        return "enrich_2_ready"

    nodes = ProjectNodes(project_of=lambda _t: 6, meta_of=meta_of, frames_of=frames_of, status_of=status_of)
    text = await nodes("запусти следующий шаг", 1)
    assert "Проект 6" in text and "step_code=img_pr" in text
    assert "step_code=" not in await nodes("какая модель на ноде промты картинок", 1)


async def test_project_scope_chain_request_gets_no_next_step_line() -> None:
    """«и дальше до …» - цепочка от ноды: строка «следующий шаг по графу» с ней спорила."""
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"slotIndex": 1}},
                {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"slotIndex": 2}},
                {"id": "n_image_prompts", "type": "image_prompts"},
            ]
        }
    }

    async def meta_of(_pid):
        return meta

    async def frames_of(_pid):
        return 15

    async def status_of(_pid):
        return "enrich_1_ready"

    nodes = ProjectNodes(project_of=lambda _t: 6, meta_of=meta_of, frames_of=frames_of, status_of=status_of)
    for text in (
        "запусти ноду работа с gpt 2 и дальше до промтов картинок",
        "продолжи с ноды работа с gpt 2 только эту ноду",
    ):
        assert "step_code=" not in await nodes(text, 1), text


def test_group_request_gets_catalog_with_kadry_alias() -> None:
    from app.orchestrator.agents.project_nodes import groups_text

    text = groups_text("в проект 6 вставь группу нод Кадры")
    assert "- script_frames_qc:" in text and "«Кадры»" in text
    assert groups_text("запусти картинки") == ""


def test_numbered_projects_take_ranges() -> None:
    from app.orchestrator.agents.project_nodes import numbered_projects

    items = [{"id": i, "title": f"t{i}"} for i in range(8, 16)]
    assert numbered_projects(items, "во всех проектах 10-14 поставь модель") == [10, 11, 12, 13, 14]
    assert numbered_projects(items, "в проектах с 9 по 11 и 15") == [9, 10, 11, 15]
    assert numbered_projects(items, "в проектах 12–10") == [12]
    assert numbered_projects(items, "в проекте 12 до 2 кадров") == [12]
    assert numbered_projects(items, "в проекте 9 до 12 кадров") == [9]
    assert numbered_projects(items, "в проекте 9 по 12 кадров на сцену") == [9]


async def test_several_projects_get_the_batch_edit_rule() -> None:
    meta = {"canvas_graph": {"nodes": [{"id": "n_script", "type": "script", "data": {"label": "Закадр"}}]}}

    async def items():
        return [{"id": i, "title": f"t{i}", "status": "new"} for i in (10, 11, 12)]

    async def meta_of(_pid):
        return meta

    async def frames_of(_pid):
        return 5

    free = ProjectNodes(project_of=lambda _t: None, projects_of=items, meta_of=meta_of, frames_of=frames_of)
    text = await free("во всех проектах 10-12 поставь модель GPT 5.6 Luna на ноду закадр", 1)
    assert "node.set_model_many" in text and "у проектов 10, 11, 12" in text
    bound = ProjectNodes(project_of=lambda _t: 10, projects_of=items, meta_of=meta_of, frames_of=frames_of)
    text = await bound("в проектах 10-12 поставь модель GPT 5.6 Luna на ноду закадр", 1)
    assert "В запросе проекты 10, 11, 12" in text and "node.set_prompt_variant_many" in text
    assert "node.set_model_many" not in await bound("поставь модель GPT 5.6 Luna на ноду закадр", 1)
