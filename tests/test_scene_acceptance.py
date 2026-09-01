"""Критерий приёмки — конфигурация, а не константа кода.

Живой прогон 2026-08-31: пороги веера описывают докдраму и трижды забраковали
корректный вывод влога (склейки от разговорной речи, «смотрит» из b-roll,
маршрут одного места). Промт настраивался, судящий его критерий — нет.
"""

from __future__ import annotations

import pytest

from app.services.scene_design.acceptance_profile import (
    PRESETS,
    ActionAcceptance,
    acceptance_from_dict,
    resolve_action_acceptance,
)
from app.services.scene_design.agents import (
    SceneDesignAgentError,
    validate_chrono_dyn_action_scenes,
)

#: Заметно разные фразы: соседние фазы не должны попадать под проверку
#: «почти одинаковы» (пересечение слов ≥55%) — она тут ни при чём.
_PLAIN = [
    "шагает по утоптанной тропе",
    "приседает у корзины с шишками",
    "оборачивается на свист позади",
    "вытирает объектив рукавом",
    "поднимает крышку с горшка",
    "перешагивает через жёлоб",
]
#: Склейка нескольких действий — запятая вместе с « и » (см. _is_compound_action).
_COMPOUND = [
    "тянется к прилавку, берёт шишку и роняет",
    "приподнимает полог, заглядывает внутрь и пятится",
    "хватает ремешок, дёргает вверх и упускает",
    "нагибается к луже, черпает ладонью и отряхивает",
]


def _scenes(n_scenes: int, compound_per_scene: int, phases_per_scene: int = 4) -> list[dict]:
    """Сцены с заданной долей «склеенных» фаз («…, … и …»)."""
    out: list[dict] = []
    plain = iter(_PLAIN * 20)
    comp = iter(_COMPOUND * 20)
    for i in range(1, n_scenes + 1):
        chain = []
        for k in range(phases_per_scene):
            is_compound = k < compound_per_scene
            chain.append(
                {
                    "subject": "c01",
                    "beat": "payoff" if k == phases_per_scene - 1 else "setup",
                    "действие": next(comp) if is_compound else next(plain),
                    "переход_к_следующей": "склейка",
                }
            )
        out.append(
            {
                "id_scene": f"scene_{i:02d}",
                "связь_с_прошлой": "оттуда же",
                "крючок_в_следующую": "дальше",
                "location": f"loc{i:02d}",
                "цепь_действия": chain,
            }
        )
    return out


class _Project:
    def __init__(self, meta: dict) -> None:
        self.meta = meta


def test_default_profile_repeats_todays_numbers() -> None:
    """Дефолты обязаны совпадать с кодом до выноса — иначе поедут все ролики."""
    d = ActionAcceptance()
    assert (d.max_compound_share, d.max_passive_share, d.max_top_location_share) == (0.2, 0.15, 0.32)
    assert d.forbid_year_jumps and d.forbid_location_collage


def test_default_preset_is_empty_override() -> None:
    assert PRESETS["default"] == {}
    assert acceptance_from_dict(PRESETS["default"]) == ActionAcceptance()


def test_same_slice_fails_by_default_and_passes_for_vlog() -> None:
    """Один и тот же срез: докдраме брак, влогу — норма. Это и есть смысл."""
    scenes = _scenes(n_scenes=4, compound_per_scene=1)  # 4/16 = 25% склеек

    with pytest.raises(SceneDesignAgentError, match="склеивают"):
        validate_chrono_dyn_action_scenes(scenes, ActionAcceptance())

    vlog = acceptance_from_dict(PRESETS["vlog"])
    validate_chrono_dyn_action_scenes(scenes, vlog)


def test_none_means_default_profile() -> None:
    """Вызов без профиля обязан вести себя как раньше."""
    scenes = _scenes(n_scenes=4, compound_per_scene=1)
    with pytest.raises(SceneDesignAgentError, match="склеивают"):
        validate_chrono_dyn_action_scenes(scenes)


def test_clean_slice_passes_both_profiles() -> None:
    scenes = _scenes(n_scenes=4, compound_per_scene=0)
    validate_chrono_dyn_action_scenes(scenes, ActionAcceptance())
    validate_chrono_dyn_action_scenes(scenes, acceptance_from_dict(PRESETS["vlog"]))


def test_preset_resolved_from_project_meta() -> None:
    p = _Project({"acceptance": {"preset": "vlog"}})
    assert resolve_action_acceptance(p).max_compound_share == PRESETS["vlog"]["max_compound_share"]


def test_point_override_wins_over_preset() -> None:
    p = _Project({"acceptance": {"preset": "vlog", "action": {"max_passive_share": 0.5}}})
    prof = resolve_action_acceptance(p)
    assert prof.max_passive_share == 0.5
    assert prof.max_compound_share == PRESETS["vlog"]["max_compound_share"]


def test_unknown_preset_falls_back_to_defaults() -> None:
    p = _Project({"acceptance": {"preset": "нет-такого"}})
    assert resolve_action_acceptance(p) == ActionAcceptance()


def test_unknown_threshold_key_ignored() -> None:
    assert acceptance_from_dict({"нет_такого_порога": 1}) == ActionAcceptance()


def test_project_without_meta_gets_defaults() -> None:
    assert resolve_action_acceptance(object()) == ActionAcceptance()


def test_location_collage_can_be_allowed() -> None:
    """Влогу коллаж мест не запрещён: маршрут идёт по разным точкам."""
    assert acceptance_from_dict(PRESETS["vlog"]).forbid_location_collage is False
    assert ActionAcceptance().forbid_location_collage is True


def test_broken_override_keeps_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если применить переопределения не вышло — работаем на прежнем профиле."""

    def boom(*_a, **_k):
        raise TypeError("dataclasses.replace сломался")

    monkeypatch.setattr("app.services.scene_design.acceptance_profile.replace", boom)
    assert acceptance_from_dict({"max_compound_share": 0.9}) == ActionAcceptance()


def test_location_variety_threshold_is_configurable() -> None:
    """Маршрут одного места — брак для докдрамы и норма для влога."""
    scenes = _scenes(n_scenes=30, compound_per_scene=0)
    for i, sc in enumerate(scenes):
        # 12 сцен из 30 в одной локации = 40%: докдраме много, влогу нет.
        sc["location"] = "loc01" if i < 12 else f"loc{i:02d}"

    with pytest.raises(SceneDesignAgentError, match="однообразно"):
        validate_chrono_dyn_action_scenes(scenes, ActionAcceptance())

    validate_chrono_dyn_action_scenes(scenes, acceptance_from_dict(PRESETS["vlog"]))


def test_location_variety_needs_three_locations() -> None:
    """Две локации — не «однообразие», а двухчастная сцена: правило молчит."""
    scenes = _scenes(n_scenes=30, compound_per_scene=0)
    for i, sc in enumerate(scenes):
        sc["location"] = "loc01" if i < 20 else "loc02"
    validate_chrono_dyn_action_scenes(scenes, ActionAcceptance())
