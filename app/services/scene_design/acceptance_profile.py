"""Пороги приёмки среза `action` — конфигурация узла, а не константы кода.

Рядом живёт `acceptance.py` и решает **другую** задачу: там инвариант
объявляется один раз («ключ + текст требования для промта + функция
проверки») и приёмка зовётся одной точкой на собранном срезе. Здесь —
про числа: те пороги из `agents.validate_chrono_dyn_action_scenes`, которые
описывают жанр и потому обязаны настраиваться.

**Зачем.** Пороги в `agents.validate_chrono_dyn_action_scenes` описывают один
жанр — документальную криминальную драму: «дверной конвейер», «коллаж
больница/милиция/дом», «каждый кадр — столкновение людей: хват, отказ,
улика». На тревел-влоге три из них сработали на **корректном** выводе
(живой прогон 2026-08-31):

* склеенные фазы <20% — разговорная речь даёт «отталкивается лапками и не
  тонет» пачками;
* пассив <15% — «смотрит, как считают шишки» это буквально грамматика b-roll;
* одна локация ≤32% сцен — а маршрут одного утра идёт по одной улице.

Три отказа подряд уводили проект в получасовую паузу. Промт на ноде при этом
настраивался, а судящий его критерий — нет; значит настраивался только жанр,
под который писали валидаторы.

**Контракт.** Дефолты здесь — ровно сегодняшние числа, поэтому поведение
существующих роликов не меняется ни на йоту. Профиль резолвится так:

1. `meta.acceptance.action` — словарь переопределений (частичный, любые поля);
2. `meta.acceptance.preset` или `meta.scene_design_acceptance_preset` — имя
   пресета из `PRESETS`;
3. иначе — `default`, то есть как было.

Пресет и переопределения складываются: пресет задаёт базу, словарь правит
поверх. Так жанр приезжает одной строкой, а точечная настройка остаётся.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ActionAcceptance:
    """Пороги приёмки среза `action`. Значения — как в коде до вынесения."""

    #: Минимальная доля фаз с персонажем cNN (иначе «crowd/prop вместо героев»).
    min_cnn_share: float = 0.25
    #: Минимальная доля фаз с проставленным beat (арка сцены).
    min_beat_share: float = 0.7
    #: Максимальная доля сцен без `связь_с_прошлой`.
    max_missing_links_share: float = 0.4
    #: Максимальная доля сцен без `крючок_в_следующую`.
    max_missing_hooks_share: float = 0.4
    #: Максимальная доля фаз, склеивающих несколько действий («…, … и …»).
    max_compound_share: float = 0.2
    #: Максимальная доля пассивных фаз («смотрит/стоит/наблюдает»).
    max_passive_share: float = 0.15
    #: Максимальная доля фаз про дверь/звонок/засов.
    max_door_share: float = 0.18
    #: Максимальная доля повторов «захлопывает дверь».
    max_door_slam_share: float = 0.12
    #: Максимальная доля метафорических фаз.
    max_metaphor_share: float = 0.12
    #: Максимальная доля сцен в самой частой локации.
    max_top_location_share: float = 0.32
    #: Минимум сцен, начиная с которого вообще считается разнообразие локаций.
    location_variety_min_scenes: int = 28
    #: Считать ли «прыжок по годам» внутри сцены браком.
    forbid_year_jumps: bool = True
    #: Считать ли коллаж разных мест внутри сцены браком.
    forbid_location_collage: bool = True
    #: Минимальное среднее число фаз на сцену.
    min_avg_phases: float = 3.2


#: Жанровые пресеты. `default` обязан повторять дефолты dataclass — на нём
#: стоят все существующие ролики.
PRESETS: dict[str, dict[str, Any]] = {
    "default": {},
    # Влог: разговорная речь, b-roll и маршрут одного места. Ослаблено ровно
    # то, что мешало корректному выводу; арка, связи и доля cNN не тронуты —
    # они про драматургию, а она нужна любому жанру.
    "vlog": {
        "max_compound_share": 0.35,
        "max_passive_share": 0.30,
        "max_top_location_share": 0.55,
        "forbid_location_collage": False,
        "min_avg_phases": 2.5,
    },
}

_FIELD_NAMES = frozenset(f.name for f in fields(ActionAcceptance))


def acceptance_from_dict(raw: Any, *, base: ActionAcceptance | None = None) -> ActionAcceptance:
    """Словарь переопределений → профиль. Чужие ключи игнорируются с предупреждением."""
    profile = base or ActionAcceptance()
    if not isinstance(raw, dict) or not raw:
        return profile
    patch: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if name not in _FIELD_NAMES:
            logger.warning("acceptance: неизвестный порог {!r} — игнорирую", name)
            continue
        patch[name] = value
    if not patch:
        return profile
    try:
        return replace(profile, **patch)
    except (TypeError, ValueError):
        logger.exception("acceptance: не применить переопределения {}", patch)
        return profile


def resolve_action_acceptance(project: Any) -> ActionAcceptance:
    """Профиль приёмки проекта: пресет + точечные переопределения."""
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return ActionAcceptance()
    raw = meta.get("acceptance")
    block: dict[str, Any] = raw if isinstance(raw, dict) else {}
    preset_name = str(block.get("preset") or meta.get("scene_design_acceptance_preset") or "default").strip()
    preset = PRESETS.get(preset_name)
    if preset is None:
        logger.warning(
            "acceptance: неизвестный пресет {!r}; есть: {}",
            preset_name,
            ", ".join(sorted(PRESETS)),
        )
        preset = {}
    profile = acceptance_from_dict(preset)
    return acceptance_from_dict(block.get("action"), base=profile)
