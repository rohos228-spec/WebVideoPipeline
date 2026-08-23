"""Мост «ключ ноды → шаг»: расход не должен теряться по дороге.

Пока деньги считались суммой по проекту, разница между `node_key` и
`step_code` ничего не стоила. Как только по шагу выставляется цена,
нераспознанный ключ — это расход, не попавший ни в одну котировку. Поэтому
здесь проверяется не таблица, а свойство: незнакомое имя видно, а не
растворяется в нуле.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.orchestrator.step_dependencies import known_step_codes
from app.services.cost_attribution import (
    NODE_KEY_STEP,
    attribute,
    is_known_non_step,
    step_of_node_key,
    unmapped_keys,
)


@dataclass
class _Row:
    node_key: str
    cost_usd: float


def test_every_declared_target_is_a_real_step() -> None:
    """Опечатка в таблице не должна создавать шаг, которого нет в графе."""
    known = known_step_codes()
    unknown = {v for v in NODE_KEY_STEP.values() if v not in known}
    assert not unknown, f"в таблице шаги вне DAG: {sorted(unknown)}"


def test_live_node_keys_all_map() -> None:
    """Ключи, реально встречавшиеся в журналах прогонов."""
    live = [
        "plan",
        "script",
        "split",
        "hero",
        "items",
        "excel_gpt",
        "image_prompts",
        "images",
        "videos",
        "audio",
        "sfx_plan",
        "sd_agent",
        "n_excel_gpt_1",
        "n_excel_gpt_2",
        "n_excel_gpt_sd_cd_action",
        "n_excel_gpt_sd_cd_camera",
    ]
    assert unmapped_keys(live) == []


def test_scene_fan_collapses_to_one_step() -> None:
    """Веер агентов раскадровки — один шаг DAG, а не пять разных."""
    for key in ("sd_agent", "n_excel_gpt_sd_skel", "n_excel_gpt_sd_cd_camera"):
        assert step_of_node_key(key) == "scene_d"


def test_enrich_slot_number_survives() -> None:
    assert step_of_node_key("n_excel_gpt_3") == "enrich_3"
    # Слота 9 в графе нет — выдумывать его нельзя.
    assert step_of_node_key("n_excel_gpt_9") is None


def test_unknown_key_is_not_silently_zero() -> None:
    """Незнакомый ключ уходит в отдельную корзину, а не в шаг."""
    rows = [_Row("images", 1.0), _Row("n_custom_node", 0.42), _Row("adhoc", 0.1)]
    by_step, unattributed = attribute(rows)
    assert by_step == {"img": 1.0}
    assert unattributed == {"n_custom_node": 0.42, "adhoc": 0.1}


def test_adhoc_is_known_non_step() -> None:
    """Ручной вызов вне конвейера — не повод для тревоги, но и не шаг."""
    assert is_known_non_step("adhoc")
    assert step_of_node_key("adhoc") is None
    assert unmapped_keys(["adhoc"]) == []


def test_llm_and_media_of_one_step_land_together() -> None:
    """`images` в текстовом журнале — проверка зрением, в медийном —
    генерация PNG. Обе строки принадлежат шагу «Картинки»."""
    by_step, _ = attribute([_Row("images", 0.046), _Row("images", 0.084)])
    assert by_step == {"img": 0.13}
