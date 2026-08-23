"""Приёмка среза: инвариант объявляется один раз и работает на обе стороны.

Разбор дефектов 2026-08-23. Пять поломок подряд имели одну форму — требование
адресовали модели и не проверили, что оно доехало:

* расстановка считалась кодом, но в контракт промта её не внесли — 0 из 23;
* гейт доли двухсоставных шотов повесили в ``parse_agent_slice``, а прод идёт
  чанками: проверка была мертва на единственном живом пути;
* знаменатель того же гейта выбирал сам проверяемый — камера удовлетворяла
  правило, просто не называя второго героя;
* счётчик людей понимал только ``cNN``, а контракт разрешает «имя/роль»: мерил
  ноль и зеленел;
* точечный ▶ молча не клал срез ``action`` в контекст, и камера снимала вслепую.

Ни один из них не про «модель плохая». Все про то, что **контракт и проверка
жили порознь**, а проверки прибивались к удобному месту кода, а не к артефакту.

Здесь это чинится конструкцией, а не внимательностью:

1. Инвариант — одна запись: ключ, ТЕКСТ требования и функция проверки.
   Текст уезжает в промт, функция — в приёмку. Разойтись им негде.
2. Приёмка зовётся в ОДНОЙ точке — на собранном срезе (после склейки чанков).
   Проверять кусок бессмысленно: доля по восьми шотам ничего не значит.
3. Зависимости среза объявлены (``REQUIRES``). Нет апстрима — это ошибка
   приёмки, а не тишина: ровно так камера и осталась без фаз action.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.scene_design.agents import SceneDesignAgentError

# Срез → срезы, без которых его нельзя принимать.
REQUIRES: dict[str, tuple[str, ...]] = {
    "camera": ("action",),
}


@dataclass(frozen=True)
class SliceContext:
    """Что видит инвариант: собранный артефакт и апстрим-срезы."""

    agent: str
    items: list[Any]
    upstream: dict[str, Any] = field(default_factory=dict)

    def upstream_items(self, name: str, key: str) -> list[Any]:
        data = self.upstream.get(name)
        if isinstance(data, dict):
            raw = data.get(key)
            if isinstance(raw, list):
                return raw
        return []


@dataclass(frozen=True)
class Invariant:
    """Одно требование к срезу: текст для модели и проверка для кода."""

    key: str
    requirement: str
    check: Callable[[SliceContext], list[str]]
    blocking: bool = True


def _people_in(raw: Any) -> list[str]:
    """Люди в поле «кто в кадре». Контракт разрешает и ``cNN``, и имя/роль."""
    from app.services.scene_design.agents import _shot_cast

    return _shot_cast({"кто_в_кадре": raw})


def _scene_ids(items: list[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        if isinstance(it, dict):
            sid = str(it.get("id_scene") or "").strip()
            if sid and sid not in out:
                out.append(sid)
    return out


# ── инварианты камеры ────────────────────────────────────────────────────


def _camera_names_people(ctx: SliceContext) -> list[str]:
    """Шот обязан назвать, кто в кадре, — иначе состав кадра нечем измерить."""
    empty = [
        str(sh.get("id_shot") or sh.get("phase_index") or i)
        for i, sh in enumerate(ctx.items, start=1)
        if isinstance(sh, dict) and not _people_in(sh.get("кто_в_кадре") or sh.get("персонажи"))
    ]
    # Кадр без людей законен (деталь предмета), но не половина плана.
    if len(empty) > max(2, len(ctx.items) // 3):
        return [f"{len(empty)} из {len(ctx.items)} шотов без «кто_в_кадре»: {', '.join(empty[:8])}"]
    return []


def _camera_scene_ids_match_action(ctx: SliceContext) -> list[str]:
    """`id_scene` камеры обязаны быть теми же, что у сцен action."""
    known = {
        str(sc.get("id_scene") or "").strip()
        for sc in ctx.upstream_items("action", "scenes")
        if isinstance(sc, dict)
    }
    if not known:
        return []
    alien = [sid for sid in _scene_ids(ctx.items) if sid not in known]
    if alien:
        return [f"сцены, которых нет у action: {', '.join(alien[:8])}"]
    return []


def _camera_two_shot_share(ctx: SliceContext) -> list[str]:
    """В сценах, где по данным action двое, доля шотов с обоими ≥ порога."""
    from app.services.scene_design.agents import (
        _TWO_SHOT_MIN_SHARE,
        _TWO_SHOT_MIN_SHOTS,
        scenes_with_two_people,
    )

    expect = scenes_with_two_people(ctx.upstream_items("action", "scenes"))
    if not expect:
        return []
    total = together = 0
    worst: list[str] = []
    by_scene: dict[str, list[list[str]]] = {}
    for sh in ctx.items:
        if isinstance(sh, dict):
            sid = str(sh.get("id_scene") or "").strip()
            by_scene.setdefault(sid, []).append(_people_in(sh.get("кто_в_кадре") or sh.get("персонажи")))
    for sid in expect:
        casts = by_scene.get(sid) or []
        if not casts:
            continue
        pairs = sum(1 for cast in casts if len(cast) >= 2)
        total += len(casts)
        together += pairs
        if pairs < max(1, int(0.4 * len(casts))):
            worst.append(f"{sid}: {pairs}/{len(casts)}")
    if total < _TWO_SHOT_MIN_SHOTS or not total:
        return []
    if together / total >= _TWO_SHOT_MIN_SHARE:
        return []
    return [
        f"в сценах с двумя людьми только {together}/{total} шотов показывают обоих "
        f"(хуже всего {'; '.join(worst[:6])})"
    ]


CAMERA_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        key="camera.names_people",
        requirement=(
            "`кто_в_кадре` заполнен у каждого шота с людьми: перечисли всех видимых "
            "через запятую, лучше как `c01 имя`. Пустое поле допустимо только для "
            "кадра без людей (деталь предмета)."
        ),
        check=_camera_names_people,
    ),
    Invariant(
        key="camera.scene_ids",
        requirement=(
            "`id_scene` бери ТОЛЬКО из фаз action — свои id не выдумывай. "
            "Шот обслуживает конкретную фазу конкретной сцены."
        ),
        check=_camera_scene_ids_match_action,
    ),
    Invariant(
        key="camera.two_shot_share",
        requirement=(
            "В сцене, где `в_кадре` фазы называет двоих, не меньше 40% шотов держат "
            "обоих. Первый такой шот — устанавливающий (общий/средний, оба целиком), "
            "дальше лестница общий → средний → крупный."
        ),
        check=_camera_two_shot_share,
    ),
)

INVARIANTS: dict[str, tuple[Invariant, ...]] = {
    "camera": CAMERA_INVARIANTS,
}


def requirements_text(agent: str) -> str:
    """Требования среза для промта — из тех же записей, что и проверки."""
    items = INVARIANTS.get(agent) or ()
    if not items:
        return ""
    lines = [f"- {inv.requirement}" for inv in items]
    return (
        f"# ПРИЁМКА СРЕЗА {agent.upper()} (проверяется кодом, не на глаз)\n"
        + "\n".join(lines)
        + "\nНе выполнено — срез не принят и уедет на переснятие с этим же списком.\n"
    )


def accept_slice(agent: str, items: list[Any], upstream: dict[str, Any] | None = None) -> list[str]:
    """Принять собранный срез. Возвращает предупреждения; на брак — исключение.

    Единственная точка проверки. Зовётся после склейки чанков: доля по одному
    чанку ничего не значит, а половина проверок, прибитых к нечанкованному
    пути, на живом прогоне просто не выполнялась.
    """
    from loguru import logger

    up = upstream or {}
    missing = [name for name in REQUIRES.get(agent, ()) if not up.get(name)]
    if missing:
        # Молчать нельзя: без фаз action камера снимала вслепую, схлопывала
        # девять сцен в две и выдумывала им id — и всё это выглядело успехом.
        raise SceneDesignAgentError(
            f"scene_design/{agent}: нет апстрим-срезов {missing} — принимать нечем. "
            f"Точечный ▶ обязан подложить их из чекпоинта."
        )

    ctx = SliceContext(agent=agent, items=[it for it in items if isinstance(it, dict)], upstream=up)
    problems: list[str] = []
    warnings: list[str] = []
    for inv in INVARIANTS.get(agent) or ():
        try:
            found = inv.check(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning("scene_design/{}: инвариант {} упал: {}", agent, inv.key, e)
            continue
        if not found:
            continue
        text = "; ".join(found)
        (problems if inv.blocking else warnings).append(f"[{inv.key}] {text}")
    for w in warnings:
        logger.warning("scene_design/{}: {}", agent, w)
    if problems:
        raise SceneDesignAgentError(
            f"scene_design/{agent}: срез не принят — "
            + " | ".join(problems)
            + ". Требования: "
            + requirements_text(agent).split("\n", 1)[1].replace("\n", " ")
        )
    return warnings


_CNN = re.compile(r"\bc\d{2}\b", re.IGNORECASE)
