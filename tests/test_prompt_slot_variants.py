"""resolve_project_prompt_name учитывает meta.prompt_slot_variants (Node Studio)."""

from __future__ import annotations

from unittest.mock import patch

# Оба модуля резолвер грузит лениво, а `prompt_active_global` берёт
# `prompt_path` на уровне модуля. Первый его импорт под `patch(prompt_path)`
# впаял бы мок навсегда — и падал бы уже соседний файл суиты, а не этот.
import app.orchestrator.node_registry  # noqa: F401
import app.services.prompt_active_global  # noqa: F401
from app.services.prompt_library import resolve_project_prompt_name

# Резолвер hero/hero_style проверяет содержимое варианта (лист персонажа /
# лок персонажа, а не агент реестра или шаблон кадров), поэтому мок файла
# отдаёт и текст, не только exists(). Текст годится и листу, и локу.
_FAKE_PROMPT_TEXT = (
    "Character sheet, turnaround, 16:9, white background. "
    "Лок персонажа: лицо, причёска, костюм, пропорции, одни и те же во всех ракурсах."
)


class _FakePromptFile:
    def exists(self) -> bool:
        return True

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        return _FAKE_PROMPT_TEXT


def test_resolve_prefers_prompt_overrides_over_stale_meta_slot() -> None:
    """Активный override проекта важнее чужой ноды в meta."""
    meta = {
        "prompt_slot_variants": {
            "n_old": {"main": "default"},
            "n_enrich_1": {"main": "От клода"},
        }
    }
    overrides = {"enrich_1": "От клода"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "От клода"


def test_resolve_uses_meta_when_no_override() -> None:
    meta = {"prompt_slot_variants": {"n1": {"main": "custom_slot"}}}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        name = resolve_project_prompt_name({}, "enrich_1", meta=meta)
    assert name == "custom_slot"


def test_resolve_falls_back_to_prompt_overrides() -> None:
    meta: dict = {}
    overrides = {"enrich_1": "custom_slot"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "custom_slot"


def test_slot_of_another_step_node_does_not_leak() -> None:
    """`default` лежит в папке каждого шага — привязка одного узла не должна
    молча становиться промтом всех остальных."""
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_hero", "type": "hero"},
                {"id": "n_plan", "type": "plan"},
            ]
        },
        "prompt_slot_variants": {"n_hero": {"main": "character_sheet"}},
    }
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        assert resolve_project_prompt_name({}, "hero", meta=meta) == "character_sheet"
        # У «Сценария» своей привязки нет — чужую он не берёт.
        assert resolve_project_prompt_name({}, "plan", meta=meta) == "default"


def test_hero_style_still_reads_slot_of_hero_node() -> None:
    """У `hero_style` нет своей ноды: промт живёт на узле `hero`, и отсечь
    такой слот по несовпадению кодов значило бы выключить его вовсе."""
    meta = {
        "canvas_graph": {"nodes": [{"id": "n_hero", "type": "hero"}]},
        "prompt_slot_variants": {"n_hero": {"style": "my_style"}},
    }
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        assert resolve_project_prompt_name({}, "hero_style", meta=meta) == "my_style"


def test_hero_style_slot_variant_saved_only_in_db_is_kept() -> None:
    """Вариант hero_style из редактора живёт только в базе (диск :ro) —
    резолвер не должен подменять его фоллбэком с диска."""
    meta = {
        "canvas_graph": {"nodes": [{"id": "n_hero", "type": "hero"}]},
        "prompt_slot_variants": {"n_hero": {"style": "db_only_style"}},
    }
    no_file = type("P", (), {"exists": lambda self: False, "is_file": lambda self: False})
    with (
        patch(
            "app.services.prompt_store.resolve",
            side_effect=lambda step, name, scope=None: _FAKE_PROMPT_TEXT if name == "db_only_style" else None,
        ),
        patch(
            "app.services.prompt_library.prompt_path",
            side_effect=lambda step, name: no_file(),
        ),
    ):
        assert resolve_project_prompt_name({}, "hero_style", meta=meta) == "db_only_style"


def test_node_prompt_variants_reads_main_then_any_slot() -> None:
    """Что показывает инспектор: слот `main`, иначе любой непустой."""
    from app.services.prompt_library import node_prompt_variants

    meta = {
        "prompt_slot_variants": {
            "n1": {"main": "sd_action"},
            "n2": {"style": "my_style"},
            "n3": {"main": ""},
            "n4": "мусор",
        }
    }
    assert node_prompt_variants(meta) == {"n1": "sd_action", "n2": "my_style"}
    assert node_prompt_variants(None) == {}


def test_garbage_entry_in_canvas_nodes_is_skipped() -> None:
    """`canvas_graph.nodes` приходит из браузера: строка или null в списке
    не должны ронять резолвер и не отменяют отсечку чужих слотов."""
    meta = {
        "canvas_graph": {
            "nodes": [
                "мусор",
                None,
                {"id": "n_hero", "type": "hero"},
                {"id": "n_plan", "type": "plan"},
            ]
        },
        "prompt_slot_variants": {"n_hero": {"main": "character_sheet"}},
    }
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: _FakePromptFile(),
    ):
        assert resolve_project_prompt_name({}, "hero", meta=meta) == "character_sheet"
        assert resolve_project_prompt_name({}, "plan", meta=meta) == "default"
