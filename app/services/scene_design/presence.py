"""Кто в кадре фазы — считается кодом, а не спрашивается у модели.

Камере нужен состав кадра: без него диалог двоих раскладывается на
односубъектные фазы, и сцена выходит галереей портретов. Поле `в_кадре`
сначала попросили у агента `action` — и контракт, у которого уже шесть
жёстких проверок, посыпался: модель начала торговать требованиями, добывая
второго героя пассивом («смотрит на неё»), склейкой двух глаголов в одну
фазу, а потом и вовсе роняя `payoff`. Пять попыток по десять минут.

Но присутствие — это не режиссура, это учёт, и код ведёт его лучше. Ровно
так же ``continuity`` уже ведёт владельца предмета: состояние переносится
вперёд само, событие ловится глаголом в тексте действия.

Правила ровно три:

* каст сцены известен заранее — он в карточке скелета (`персонажи`) и в
  `subject` фаз;
* человек считается в кадре с той фазы, где он появился (назван по имени,
  роли или id — либо он в касте с самого начала сцены), и до фазы, где текст
  показал его уход, — и то и другое считается по всему ролику, а не внутри
  сцены;
* появление считается **на весь ролик**, а не на сцену: пока герой не вышел
  на сцену ни разу, его нет в кадре, даже если каст его ячейки уже называет.
  Иначе женщина, входящая в вагон на пятой сцене, стоит в кадре с первой;
* субъект фазы в кадре всегда — он действует.

Молчание работает на непрерывность: не сказано, что человек вышел, — он
рядом. Сломать это можно только явным уходом, а явный уход виден в тексте.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.scene_design.continuity import parse_character_ids

# Уход из кадра: человек физически покидает пространство сцены.
_EXIT = re.compile(
    r"уход|ушёл|ушла|уезжа|уезжа|выход(?!ны)|вышел|вышла|скрыва|скрылся|скрылась|"
    r"исчеза|исчез|покида|покинул|покинула|двери сход|за створк|растворя",
    re.IGNORECASE,
)
# Появление: человек входит в пространство сцены.
_ENTER = re.compile(
    r"вход|вошёл|вошла|заход|появля|появил|появилась|шагает через порог|"
    r"переступа|садится напротив|подход|подошёл|подошла",
    re.IGNORECASE,
)


def _phase_text(phase: dict[str, Any]) -> str:
    return " ".join(
        str(phase.get(k) or "") for k in ("action", "действие", "продолжает", "orientation") if phase.get(k)
    )


def _named_in(text: str, names: dict[str, str]) -> set[str]:
    """cNN, названные в тексте фазы — по id или по имени персонажа."""
    found = set(parse_character_ids(text))
    low = text.casefold()
    for cid, name in names.items():
        needle = (name or "").strip().casefold()
        if not needle:
            continue
        first = needle.split()[0] if needle.split() else ""
        if len(first) >= 4 and re.search(rf"(?<![0-9a-zа-яё]){re.escape(first)}", low):
            found.add(cid)
    return found


def fill_in_frame(
    scenes: list[Any],
    *,
    scene_cast: dict[str, list[str]] | None = None,
    names: dict[str, str] | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Проставить ``в_кадре`` каждой фазе. Возвращает список изменённых сцен.

    ``scene_cast`` — каст по ``id_scene`` из скелета; чего там нет, собирается
    из ``subject`` и имён в тексте самих фаз. ``overwrite=False`` уважает поле,
    если агент его всё-таки прислал.
    """
    cast_map = scene_cast or {}
    names = names or {}
    touched: list[str] = []
    # Появление — сквозное по ролику: герой не в кадре, пока не вышел на сцену.
    introduced: set[str] = set()
    # Уход тоже сквозной: вышедший из вагона не возвращается в кадр на границе
    # сцены сам по себе — только если текст снова его называет.
    gone: set[str] = set()
    first_scene = True
    # Кого ролик где-либо вводит явным входом — тот не «уже на месте» в первой
    # же сцене, даже если каст его ячейки называет. Женщина входит в вагон на
    # пятой сцене; без этого она стояла бы в кадре с первой.
    enters_later = _entering_anywhere(scenes, names)

    for i, sc in enumerate(scenes or [], start=1):
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id_scene") or f"#{i}")
        chain = [ph for ph in _chain(sc) if isinstance(ph, dict)]
        if not chain:
            continue

        # Каст сцены: скелет + субъекты фаз + имена, названные в тексте.
        cast: list[str] = list(cast_map.get(sid) or [])
        for ph in chain:
            for cid in parse_character_ids(ph.get("subject") or ph.get("субъект")):
                if cid not in cast:
                    cast.append(cid)
            for cid in sorted(_named_in(_phase_text(ph), names)):
                if cid not in cast:
                    cast.append(cid)
        if not cast:
            continue

        # Кто уже вошёл: тот, кого сцена вводит по ходу, до своей фазы не в кадре.
        entering = {
            cid
            for cid in cast
            if any(
                _ENTER.search(_phase_text(ph)) and cid in _named_in(_phase_text(ph), names) for ph in chain
            )
        }
        if first_scene:
            # Начало ролика: кого никто не вводит — тот уже на месте.
            present = [cid for cid in cast if cid not in entering and cid not in enters_later]
            introduced |= set(present)
            first_scene = False
        else:
            # Дальше в кадре только те, кто уже появлялся: каст ячейки говорит,
            # кто участвует, но не отменяет момент выхода на сцену.
            present = [cid for cid in cast if cid not in entering and cid in introduced]
        present = [cid for cid in present if cid not in gone]
        changed = False

        for ph in chain:
            text = _phase_text(ph)
            named = _named_in(text, names) | set(parse_character_ids(ph.get("subject") or ph.get("субъект")))
            for cid in named:
                if cid in gone:
                    # Вернулся в текст после ухода — значит вернулся в кадр.
                    gone.discard(cid)
                if cid not in present:
                    present.append(cid)
                introduced.add(cid)
            visible = [cid for cid in present if cid not in gone]
            if visible and (overwrite or not str(ph.get("в_кадре") or "").strip()):
                line = ", ".join(visible)
                if line != str(ph.get("в_кадре") or ""):
                    ph["в_кадре"] = line
                    changed = True
            # Уход применяем ПОСЛЕ кадра: в самой фазе ухода человека ещё видно.
            if _EXIT.search(text):
                gone |= {cid for cid in named if cid in present}
        if changed:
            touched.append(sid)
    return touched


def _entering_anywhere(scenes: list[Any] | None, names: dict[str, str]) -> set[str]:
    """cNN, чей выход на сцену показан текстом хоть где-то в ролике."""
    out: set[str] = set()
    for sc in scenes or []:
        if not isinstance(sc, dict):
            continue
        for ph in _chain(sc):
            if not isinstance(ph, dict):
                continue
            text = _phase_text(ph)
            if _ENTER.search(text):
                out |= _named_in(text, names)
    return out


def _chain(scene: dict[str, Any]) -> list[Any]:
    for key in ("цепь_действия", "phases", "фазы"):
        raw = scene.get(key)
        if isinstance(raw, list):
            return raw
    return []
