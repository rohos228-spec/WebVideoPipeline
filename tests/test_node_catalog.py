"""Каталог узлов для конструктора не расходится с реестром.

Палитра конструктора берёт типы узлов с сервера, а не держит свой список.
Причина простая: у типа есть код шага, running- и ready-статус, и восстановить
это на фронте нечем. Но раз список отдаётся, он обязан оставаться полным —
иначе добавленный в `node_registry` узел просто не появится в палитре, и
никто этого не заметит: ошибки нет, узла нет.

Вторая половина — подписи. Тип `sfx_gen` в палитре читается как опечатка;
человеческое имя есть, но живёт отдельным словарём, и забыть его при
добавлении типа проще всего.
"""

from __future__ import annotations

import asyncio

from app.orchestrator.node_registry import (
    CONFIG_NODE_TYPES,
    HITL_NODE_TYPES,
    NODE_TYPE_TO_STEP_CODE,
    WORK_NODES,
)
from app.services.prompt_library import STEP_FOLDERS
from app.web.routers.workflows import node_catalog


def _catalog() -> dict:
    return asyncio.run(node_catalog())


def test_every_registry_node_is_offered() -> None:
    """Ни один тип из реестра не потерян."""
    cat = _catalog()
    offered = {n["type"] for n in cat["nodes"]}
    expected = set(WORK_NODES) | set(HITL_NODE_TYPES) | set(CONFIG_NODE_TYPES)

    missing = expected - offered
    assert not missing, (
        f"эти типы есть в реестре, но не попадут в палитру: {sorted(missing)} — "
        "узел нельзя будет поставить на схему"
    )


def test_no_node_is_invented() -> None:
    """И наоборот: в палитре нет того, чего реестр не знает."""
    cat = _catalog()
    offered = {n["type"] for n in cat["nodes"]}
    known = set(WORK_NODES) | set(HITL_NODE_TYPES) | set(CONFIG_NODE_TYPES)

    extra = offered - known
    assert not extra, f"палитра предлагает неизвестные реестру типы: {sorted(extra)}"


def test_every_node_has_a_human_label() -> None:
    """Подпись отличается от кода — иначе в палитре будет «sfx_gen»."""
    cat = _catalog()
    raw = [n["type"] for n in cat["nodes"] if n["label"] == n["type"]]
    assert not raw, (
        f"у этих узлов нет человеческого имени: {sorted(raw)} — "
        "добавьте в NODE_LABELS (app/web/routers/workflows.py)"
    )


def test_prompt_flag_matches_the_library() -> None:
    """`has_prompt` совпадает с тем, есть ли у шага папка промтов.

    От флага зависит, покажет ли инспектор правку промта. Соврать здесь значит
    либо спрятать существующий промт, либо предложить править несуществующий.
    """
    cat = _catalog()
    for node in cat["nodes"]:
        step = NODE_TYPE_TO_STEP_CODE.get(node["type"])
        expected = bool(step and step in STEP_FOLDERS)
        assert node["has_prompt"] is expected, (
            f"{node['type']}: has_prompt={node['has_prompt']}, "
            f"а шаг {step!r} {'есть' if expected else 'отсутствует'} в STEP_FOLDERS"
        )


def test_kinds_cover_every_node() -> None:
    """У каждого узла раздел палитры, и раздел объявлен."""
    cat = _catalog()
    kinds = set(cat["kinds"])
    for node in cat["nodes"]:
        assert node["kind"] in kinds, f"{node['type']}: раздел {node['kind']!r} не объявлен"
