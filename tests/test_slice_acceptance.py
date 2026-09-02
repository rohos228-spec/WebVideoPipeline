"""Приёмка среза: одно объявление на требование в промте и на проверку.

Разбор 2026-08-23: пять дефектов подряд имели одну форму — требование
адресовали модели и не проверили, что оно доехало. Тесты держат саму
конструкцию, а не отдельные правила: текст требования и проверка обязаны
жить в одной записи, апстрим обязан быть объявлен, проверка обязана стоять
на собранном артефакте.
"""

from __future__ import annotations

import pytest

from app.services.scene_design.acceptance import (
    INVARIANTS,
    REQUIRES,
    accept_slice,
    requirements_text,
)
from app.services.scene_design.agents import SceneDesignAgentError


def _action(scenes: list[tuple[str, list[str]]]) -> dict:
    return {
        "scenes": [
            {
                "id_scene": sid,
                "цепь_действия": [{"phase_index": 1, "action": "толкает конверт", "в_кадре": ", ".join(who)}],
            }
            for sid, who in scenes
        ]
    }


def _shot(sid: str, who: str) -> dict:
    return {"id_scene": sid, "кто_в_кадре": who, "крупность": "средний план"}


def test_every_invariant_carries_its_own_prompt_text() -> None:
    """Расходиться промту и чекеру негде — они в одной записи."""
    for agent, invs in INVARIANTS.items():
        assert invs, agent
        text = requirements_text(agent)
        for inv in invs:
            assert inv.requirement.strip()
            assert inv.requirement in text
            assert callable(inv.check)


def test_missing_upstream_is_an_error_not_silence() -> None:
    """Точечный ▶ оставлял камеру без фаз action, и это выглядело успехом."""
    with pytest.raises(SceneDesignAgentError) as err:
        accept_slice("camera", [_shot("scene_01", "c01")], upstream={})
    assert "action" in str(err.value)
    assert "camera" in REQUIRES


def test_invented_scene_ids_are_rejected() -> None:
    up = {"action": _action([("scene_01", ["c01"]), ("scene_02", ["c01"])])}
    with pytest.raises(SceneDesignAgentError) as err:
        accept_slice("camera", [_shot("scene_p1", "c01")], upstream=up)
    assert "scene_p1" in str(err.value)


def test_shots_without_people_are_rejected_in_bulk_but_not_singly() -> None:
    """Кадр-деталь без людей законен; план, где их нет у трети, — нет."""
    up = {"action": _action([("scene_01", ["c01"])])}
    ok = [_shot("scene_01", "c01") for _ in range(9)] + [_shot("scene_01", "")]
    accept_slice("camera", ok, upstream=up)
    bad = [_shot("scene_01", "") for _ in range(6)] + [_shot("scene_01", "c01") for _ in range(4)]
    with pytest.raises(SceneDesignAgentError):
        accept_slice("camera", bad, upstream=up)


def test_two_shot_share_is_measured_against_action_not_camera() -> None:
    """Знаменатель не выбирает проверяемый: камера не может «не заметить» второго."""
    up = {"action": _action([("scene_01", ["c01", "c02"])])}
    monologues = [_shot("scene_01", "c01 Игнат") for _ in range(9)] + [_shot("scene_01", "c02 женщина")]
    with pytest.raises(SceneDesignAgentError) as err:
        accept_slice("camera", monologues, upstream=up)
    assert "показывают обоих" in str(err.value)

    dialogue = [_shot("scene_01", "c01 Игнат, c02 женщина") for _ in range(5)] + [
        _shot("scene_01", "c01 Игнат") for _ in range(5)
    ]
    accept_slice("camera", dialogue, upstream=up)


def test_people_named_without_ids_still_count() -> None:
    """Контракт разрешает «имя/роль» — счётчик обязан это понимать."""
    up = {"action": _action([("scene_01", ["c01", "c02"])])}
    dialogue = [_shot("scene_01", "Игнат, женщина в сером пальто") for _ in range(5)] + [
        _shot("scene_01", "Игнат") for _ in range(5)
    ]
    accept_slice("camera", dialogue, upstream=up)


def test_solo_scenes_are_not_judged_by_the_two_shot_rule() -> None:
    up = {"action": _action([("scene_01", ["c01"])])}
    accept_slice("camera", [_shot("scene_01", "c01 Игнат") for _ in range(20)], upstream=up)


def test_requirements_reach_the_prompt_from_the_same_declaration() -> None:
    """Контур замкнут: промт собирается из ``requirements_text``, не из копии.

    Ровно этого не хватало расстановке: поле приехало в контекст, а в список
    требований промта его никто не внёс — 0 промтов из 23.
    """
    import inspect

    from app.services.scene_design import runner

    src = inspect.getsource(runner._run_one_agent)
    assert "requirements_text(name)" in src
    assert "acceptance" in src


def test_broken_invariant_warns_but_does_not_block() -> None:
    """Упавший чекер — предупреждение в лог, а не брак среза.

    Инвариант защищает контент; его собственное исключение — дефект чекера.
    Ронять на нём прогон значило бы блокировать конвейер багом проверки.
    """
    from unittest.mock import patch

    from app.services.scene_design import acceptance as acc

    def _boom(ctx: acc.SliceContext) -> list[str]:
        raise RuntimeError("чекер сломан")

    def _warn_only(ctx: acc.SliceContext) -> list[str]:
        return ["мягкое замечание"]

    fake = (
        acc.Invariant(key="boom", requirement="не падать", check=_boom),
        acc.Invariant(key="soft", requirement="мягко", check=_warn_only, blocking=False),
    )
    with (
        patch.dict(acc.INVARIANTS, {"camera": fake}),
        patch.dict(acc.REQUIRES, {"camera": ()}),
    ):
        warnings = acc.accept_slice("camera", [_shot("scene_01", "c01")], upstream={})
    assert warnings == ["[soft] мягкое замечание"]


def test_upstream_items_ignores_wrong_shapes() -> None:
    """Апстрим не dict или ключ не list — пустой список, а не исключение."""
    from app.services.scene_design.acceptance import SliceContext

    ctx = SliceContext(agent="camera", items=[], upstream={"action": ["не dict"], "b": {"scenes": "не list"}})
    assert ctx.upstream_items("action", "scenes") == []
    assert ctx.upstream_items("b", "scenes") == []
    assert ctx.upstream_items("нет", "scenes") == []
