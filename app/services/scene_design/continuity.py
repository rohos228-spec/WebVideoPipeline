"""Непрерывность между кадрами: ось действия и реестр предметов.

Каталог наборов и промт CAMERA требуют «не перескакивай ось» и «помни, у кого
предмет». Требование адресовано модели, которая видит кадры пачками и между
вызовами не помнит ничего, — поэтому не выполняется. Здесь то же самое
считается кодом: сторона оси даёт положение и направление взгляда, реестр
предметов — владельца на каждом кадре. В промт картинки уезжает готовая
фраза, а не правило, которое ещё надо применить.

Умолчание безопасное и односторонее:

* сцена целиком снимается с одной стороны оси — пустая ``сторона`` означает
  «та же, что у сцены», а не «на усмотрение модели»;
* предмет остаётся у прежнего владельца, пока кадр явно не объявит передачу,
  и передача обязана быть видна в тексте действия.

То есть промолчав, модель получает непрерывность; сломать её можно только
явным объявлением, а явное объявление проверяется.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

SIDE_A = "A"
SIDE_B = "B"
SIDE_NEUTRAL = "нейтраль"

VERB_INTRO = "вводится"
VERB_HOLD = "держится"
VERB_CHANGE = "меняется"
VERB_EXIT = "уходит"
_PROP_VERBS = (VERB_INTRO, VERB_HOLD, VERB_CHANGE, VERB_EXIT)

_CID = re.compile(r"\bc\s*(\d{1,3})\b", re.IGNORECASE)
_PID = re.compile(r"\bp\s*(\d{1,3})\b", re.IGNORECASE)

# Глаголы, при которых предмет законно меняет владельца. Без такого глагола
# в тексте действия смена владельца — телепорт: зритель видит, что конверт
# оказался в других руках, но не видел момента передачи.
_TRANSFER = re.compile(
    r"переда|протяг|вруч|отда|отдаёт|бер[её]т|забир|принима|подаёт|су[ёе]т|"
    r"кла[дт]|ста[вн]|ловит|выхватыв|роняет|прячет|достаёт|вынима|подхватыв",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    """Нарушение непрерывности на конкретном кадре."""

    kind: str
    frame_uuid: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - только для логов
        return f"{self.kind} @{self.frame_uuid}: {self.detail}"


@dataclass(frozen=True)
class Axis:
    """Ось действия сцены: кто по экрану слева, кто справа на стороне A."""

    left: str
    right: str


@dataclass(frozen=True)
class PropState:
    """Состояние предмета на кадре после применения событий кадра."""

    prop_id: str
    verb: str
    holder: str | None = None
    zone: str | None = None

    @property
    def gone(self) -> bool:
        return self.verb == VERB_EXIT


def parse_ids(raw: Any, pattern: re.Pattern[str] = _CID, prefix: str = "c") -> list[str]:
    """``"c08 врач, c02 мать"`` → ``["c08", "c02"]`` без повторов, в порядке текста."""
    if isinstance(raw, list | tuple):
        text = " ".join(str(x) for x in raw)
    else:
        text = str(raw or "")
    out: list[str] = []
    for m in pattern.finditer(text):
        cid = f"{prefix}{int(m.group(1)):02d}"
        if cid not in out:
            out.append(cid)
    return out


def parse_character_ids(raw: Any) -> list[str]:
    return parse_ids(raw, _CID, "c")


def parse_prop_ids(raw: Any) -> list[str]:
    return parse_ids(raw, _PID, "p")


def normalize_side(raw: Any) -> str | None:
    """``сторона`` шота → ``A`` / ``B`` / ``нейтраль`` / ``None`` (не объявлена)."""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in ("a", "а", "1", "первая"):
        return SIDE_A
    if text in ("b", "б", "2", "вторая"):
        return SIDE_B
    if text.startswith(("нейтр", "neutral", "ось", "on-axis", "on_axis")):
        return SIDE_NEUTRAL
    return None


def resolve_axis(shots: list[dict[str, Any]]) -> Axis | None:
    """Ось сцены из ``кто_в_кадре`` её шотов.

    Двое, кто чаще всех в кадре; левым по экрану становится тот, кто появился
    в сцене раньше. Выбор не спрашивается у модели: он обязан быть одинаковым
    во всех кадрах сцены, а одинаковость — свойство кода, не промта.
    """
    order: list[str] = []
    counts: Counter[str] = Counter()
    for shot in shots:
        for cid in parse_character_ids(shot.get("кто_в_кадре") or shot.get("персонажи")):
            counts[cid] += 1
            if cid not in order:
                order.append(cid)
    if len(counts) < 2:
        return None
    top = sorted(counts, key=lambda cid: (-counts[cid], order.index(cid)))[:2]
    left, right = sorted(top, key=order.index)
    return Axis(left=left, right=right)


def axis_for_scene(scene_shots: list[dict[str, Any]], global_axis: Axis | None) -> Axis | None:
    """Ось сцены, но с оглядкой на ролик целиком.

    Ось, посчитанная внутри сцены, ставит слева того, кто раньше попал в кадр
    **этой** сцены. У пары, которая сидит друг напротив друга весь ролик, это
    даёт переворот на каждой сцене, где первым показали второго, — то есть
    ровно ту ошибку, ради которой всё и затевалось. Пока в сцене та же пара,
    что и в ролике, держим общий порядок; другая пара — считаем заново.
    """
    local = resolve_axis(scene_shots)
    if local is None or global_axis is None:
        return local
    if {local.left, local.right} == {global_axis.left, global_axis.right}:
        return global_axis
    return local


def enforce_axis(shots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Violation]]:
    """Проставить каждому шоту сцены сторону оси; перелёты — только через нейтраль.

    Пустая сторона = сторона сцены. Объявленный перелёт сразу после обычного
    шота — нарушение: возвращаем шот на сторону сцены, чтобы сцена не осталась
    с двумя взаимоисключающими ракурсами на одну ось. Законный перелёт —
    только следом за нейтральным (снятым по оси) шотом, он у зрителя и
    работает переходной ступенькой.
    """
    out: list[dict[str, Any]] = []
    violations: list[Violation] = []
    scene_side: str | None = None
    prev_neutral = False
    for shot in shots:
        uuid = str(shot.get("uuid") or shot.get("frame_uuid") or "")
        declared = normalize_side(shot.get("сторона"))
        if declared == SIDE_NEUTRAL:
            side = SIDE_NEUTRAL
            prev_neutral = True
        else:
            side = declared or scene_side or SIDE_A
            if scene_side is None:
                scene_side = side
            elif side != scene_side:
                if prev_neutral:
                    scene_side = side
                else:
                    violations.append(
                        Violation(
                            kind="axis_jump",
                            frame_uuid=uuid,
                            detail=(
                                f"перелёт через ось: сторона {side} после {scene_side} "
                                "без нейтрального шота между — возвращено на "
                                f"{scene_side}"
                            ),
                        )
                    )
                    side = scene_side
            prev_neutral = False
        out.append({**shot, "сторона": side})
    return out, violations


def screen_directions(axis: Axis, side: str) -> dict[str, str]:
    """cNN → положение в кадре и направление взгляда для этой стороны оси."""
    if side == SIDE_NEUTRAL:
        return {
            axis.left: "по центру кадра, лицом в объектив",
            axis.right: "по центру кадра, спиной к объективу",
        }
    if side == SIDE_B:
        return {
            axis.left: "в правой половине кадра, смотрит влево",
            axis.right: "в левой половине кадра, смотрит вправо",
        }
    return {
        axis.left: "в левой половине кадра, смотрит вправо",
        axis.right: "в правой половине кадра, смотрит влево",
    }


def name_stems(names: dict[str, str] | None) -> dict[str, str]:
    """cNN → основа имени для поиска в падежах: «Игнат» → «игна», «женщина…» → «женщи».

    Русский склоняет: скелет пишет «переходит к женщине», а в реестре имя
    «женщина в сером пальто». Точное сравнение тут не работает, а полноценная
    морфология ради двух-трёх имён на ролик не нужна.
    """
    out: dict[str, str] = {}
    for cid, name in (names or {}).items():
        first = str(name or "").strip().split()[0] if str(name or "").strip() else ""
        if not first:
            continue
        stem = first[:-1] if len(first) > 4 else first
        out[cid] = stem.casefold()
    return out


def _holder_from_text(text: str, stems: dict[str, str]) -> str | None:
    """Кого назвал свободный текст ``как``: сначала cNN, потом имя в любом падеже."""
    ids = parse_character_ids(text)
    if ids:
        return ids[0]
    low = text.casefold()
    for cid, stem in stems.items():
        if stem and stem in low:
            return cid
    return None


def _prop_events(raw: Any, stems: dict[str, str] | None = None) -> list[PropState]:
    """``предметы`` кадра → объявленные события. Мусор молча отбрасывается.

    ``как`` скелет пишет как «глагол» либо «глагол: свободный текст», и в этом
    хвосте прячется всё интересное — «переходит к женщине», «переместился на
    сиденье между ними». Разбираем хвост: узнали человека — это владелец, не
    узнали — это место предмета в кадре.
    """
    if not isinstance(raw, list | tuple):
        return []
    stems = stems or {}
    out: list[PropState] = []
    for item in raw:
        if isinstance(item, str):
            ids = parse_prop_ids(item)
            if ids:
                out.append(PropState(prop_id=ids[0], verb=VERB_HOLD))
            continue
        if not isinstance(item, dict):
            continue
        pid = parse_prop_ids(item.get("id"))
        if not pid:
            continue
        raw_verb = str(item.get("как") or item.get("verb") or "").strip()
        head, _, tail = raw_verb.partition(":")
        verb = head.strip().casefold()
        if verb not in _PROP_VERBS:
            verb = VERB_HOLD
        tail = tail.strip()
        holder_ids = parse_character_ids(item.get("у_кого") or item.get("holder"))
        holder = holder_ids[0] if holder_ids else None
        zone = str(item.get("где") or item.get("zone") or "").strip() or None
        if tail:
            from_text = _holder_from_text(tail, stems)
            if from_text and holder is None:
                holder = from_text
            elif from_text is None and zone is None and verb in (VERB_INTRO, VERB_CHANGE):
                # У «держится» хвост описывает манеру («край теребят пальцы»),
                # а не место: местом его считать нельзя, руки предмет не отпускали.
                zone = tail
        out.append(PropState(prop_id=pid[0], verb=verb, holder=holder, zone=zone))
    return out


def build_prop_ledger(
    frames: list[dict[str, Any]],
    names: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, PropState]], list[Violation]]:
    """Реестр предметов по кадрам в хронологии: uuid → {pNN: состояние}.

    Состояние переносится вперёд само; кадр меняет его только тем, что объявил.
    Проверяем то, что зритель заметил бы глазами:

    * предмет введён дважды — во втором кадре он «появляется заново»;
    * предмет живёт после того, как ушёл из ролика;
    * владелец сменился, а передачи в действии кадра нет — телепорт;
    * держит предмет тот, кого в кадре нет.
    """
    ledger: dict[str, dict[str, PropState]] = {}
    violations: list[Violation] = []
    state: dict[str, PropState] = {}
    introduced: set[str] = set()
    # Один и тот же отклонённый переход повторяется на каждом следующем кадре
    # (состояние-то не изменилось) — жалуемся один раз, а не 6.
    rejected: set[tuple[str, str | None, str | None]] = set()
    stems = name_stems(names)
    for frame in frames:
        uuid = str(frame.get("uuid") or frame.get("frame_uuid") or "")
        action = str(frame.get("действие") or frame.get("action") or "")
        in_frame = set(parse_character_ids(frame.get("персонажи") or frame.get("кто_в_кадре")))
        transfer_seen = bool(_TRANSFER.search(action))
        for event in _prop_events(frame.get("предметы"), stems):
            pid = event.prop_id
            prev = state.get(pid)
            if event.verb == VERB_INTRO and pid in introduced and prev and not prev.gone:
                violations.append(
                    Violation(
                        kind="prop_reintroduced",
                        frame_uuid=uuid,
                        detail=f"{pid} вводится второй раз — в кадре он появится заново",
                    )
                )
                event = replace(event, verb=VERB_HOLD)
            if prev is not None and prev.gone and event.verb != VERB_INTRO:
                violations.append(
                    Violation(
                        kind="prop_after_exit",
                        frame_uuid=uuid,
                        detail=f"{pid} уже ушёл из ролика, но кадр его снова использует",
                    )
                )
                continue
            if event.holder is not None:
                holder = event.holder
            elif event.zone is not None and event.verb in (VERB_INTRO, VERB_CHANGE):
                # Предмет положили в кадр: рук на нём больше нет.
                holder = None
            elif event.verb == VERB_INTRO and prev is None and len(in_frame) == 1:
                # Предмет вводится, в кадре ровно один человек и места ему не
                # назначено — он у этого человека. Скелет так и подразумевает
                # («едет с конвертом на коленях»), просто не пишет владельца.
                holder = next(iter(in_frame))
            else:
                holder = prev.holder if prev else None
            # `меняется` и `вводится` — объявленная смена состояния, это и есть
            # словарь контракта: доказывать её глаголом в действии не требуем.
            # Ловим молчаливую: `держится`, но уже в других руках — зритель
            # передачи не видел, предмет просто оказался у другого.
            silent_move = (
                prev is not None
                and event.holder is not None
                and event.holder != prev.holder
                and event.verb not in (VERB_INTRO, VERB_CHANGE)
                and not transfer_seen
            )
            move_key = (pid, prev.holder if prev else None, event.holder)
            if silent_move:
                if move_key not in rejected:
                    rejected.add(move_key)
                    violations.append(
                        Violation(
                            kind="prop_teleport",
                            frame_uuid=uuid,
                            detail=(
                                f"{pid}: владелец {prev.holder if prev and prev.holder else '—'} → "
                                f"{event.holder} на «{event.verb}», передачи в кадре нет — "
                                "оставлен прежний"
                            ),
                        )
                    )
                holder = prev.holder if prev else None
            if event.verb != VERB_EXIT and holder and in_frame and holder not in in_frame:
                violations.append(
                    Violation(
                        kind="prop_absent_holder",
                        frame_uuid=uuid,
                        detail=f"{pid} держит {holder}, которого в кадре нет",
                    )
                )
            if event.verb == VERB_EXIT:
                holder = None
            state[pid] = PropState(
                prop_id=pid,
                verb=event.verb,
                holder=holder,
                zone=event.zone if event.zone is not None else (prev.zone if prev else None),
            )
            introduced.add(pid)
        ledger[uuid] = dict(state)
    return ledger, violations


def continuity_line(
    *,
    axis: Axis | None,
    side: str,
    names: dict[str, str] | None = None,
    props: dict[str, PropState] | None = None,
    prop_names: dict[str, str] | None = None,
    in_frame: list[str] | None = None,
) -> str:
    """Готовая фраза непрерывности для промта картинки.

    Не правило, а факт: где стоит каждый, куда смотрит, у кого предмет.
    Модель картинки такое исполняет, а «держи ось 180°» — нет.
    """
    names = names or {}
    prop_names = prop_names or {}
    parts: list[str] = []
    if axis is not None:
        dirs = screen_directions(axis, side)
        visible = set(in_frame) if in_frame else set(dirs)
        placed = [f"{names.get(cid, cid)} {text}" for cid, text in dirs.items() if cid in visible]
        if placed:
            parts.append("Расстановка: " + "; ".join(placed) + ".")
    seen_frame = set(in_frame) if in_frame else set()
    for pid, st in (props or {}).items():
        if st.gone:
            continue
        # Владельца в кадре нет — предмет уехал вместе с ним. Называть его
        # значит вернуть в кадр то, что зритель только что видел уходящим.
        if st.holder and seen_frame and st.holder not in seen_frame:
            continue
        label = prop_names.get(pid, pid)
        if st.holder:
            # Именительный падеж: имена в реестре нескланяемы («женщина в сером
            # пальто»), а «в руках у женщина» — брак в промте.
            who = names.get(st.holder, st.holder)
            parts.append(f"{label} держит {who}.")
        elif st.zone:
            parts.append(f"{label} — {st.zone}, ничьих рук на нём нет.")
    if not parts:
        return ""
    return "Непрерывность. " + " ".join(parts)
