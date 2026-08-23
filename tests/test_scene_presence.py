"""Состав кадра считается кодом, а не спрашивается у модели.

Поле `в_кадре` сначала попросили у агента `action`. Контракт, у которого уже
шесть жёстких проверок, этого не выдержал: на живом прогоне 2026-08-23 модель
пять попыток подряд торговала требованиями — добывала второго героя пассивом
(«смотрит на неё»), склеивала два глагола в одну фазу, потом роняла `payoff`.
Присутствие — это учёт, и он ложится в код так же, как реестр предметов
в ``continuity``: состояние переносится вперёд, событие ловится глаголом.
"""

from __future__ import annotations

from app.services.scene_design.presence import fill_in_frame

_NAMES = {"c01": "Игнат", "c02": "женщина в сером пальто"}


def _scene(*actions: tuple[str, str]) -> dict:
    return {
        "id_scene": "scene_05",
        "цепь_действия": [
            {"phase_index": i, "action": a, "subject": s} for i, (a, s) in enumerate(actions, start=1)
        ],
    }


def _frames(scene: dict) -> list[str]:
    return [ph.get("в_кадре", "") for ph in scene["цепь_действия"]]


def test_second_person_appears_only_after_entering() -> None:
    """До входа героя в кадре нет — иначе расстановка врёт с первого шота."""
    sc = _scene(
        ("Игнат прижимает конверт к колену", "c01"),
        ("женщина входит в вагон через проём", "c02"),
        ("Игнат толкает конверт к её руке", "c01"),
    )
    fill_in_frame([sc], names=_NAMES)
    assert _frames(sc) == ["c01", "c01, c02", "c01, c02"]


def test_presence_carries_forward_without_being_repeated() -> None:
    """Молчание работает на непрерывность: не сказано «ушёл» — значит рядом."""
    sc = _scene(
        ("Игнат и женщина уже сидят друг напротив друга", "c01"),
        ("Игнат разжимает пальцы", "c01"),
        ("конверт скользит по колену", "c01"),
    )
    fill_in_frame([sc], scene_cast={"scene_05": ["c01", "c02"]}, names=_NAMES)
    assert _frames(sc) == ["c01, c02"] * 3


def test_exit_is_visible_in_its_own_phase_and_gone_after() -> None:
    """Кадр ухода показывает уходящего — исчезает он со следующего."""
    sc = _scene(
        ("Игнат толкает конверт к её руке", "c01"),
        ("женщина уходит за створки дверей", "c02"),
        ("Игнат сжимает пустую ладонь", "c01"),
    )
    fill_in_frame([sc], scene_cast={"scene_05": ["c01", "c02"]}, names=_NAMES)
    assert _frames(sc) == ["c01, c02", "c01, c02", "c01"]


def test_subject_is_always_in_frame() -> None:
    """Кто действует — тот виден, даже если каст сцены о нём молчит."""
    sc = _scene(("руки в перчатках прячут конверт", "c02"))
    fill_in_frame([sc], names=_NAMES)
    assert _frames(sc) == ["c02"]


def test_field_from_the_model_is_respected_unless_overwritten() -> None:
    sc = _scene(("Игнат разжимает пальцы", "c01"))
    sc["цепь_действия"][0]["в_кадре"] = "c01, c02"
    fill_in_frame([sc], names=_NAMES)
    assert _frames(sc) == ["c01, c02"]
    fill_in_frame([sc], names=_NAMES, overwrite=True)
    assert _frames(sc) == ["c01"]


def test_scene_without_people_is_left_alone() -> None:
    sc = _scene(("пустой вагон качается на стыках", ""))
    assert fill_in_frame([sc], names=_NAMES) == []
    assert _frames(sc) == [""]


def test_action_contract_no_longer_asks_for_the_field() -> None:
    """Нагрузка снята с промта — иначе смысл расчёта теряется."""
    from pathlib import Path

    text = Path("prompts/05_excel_gpt/sd_action_chrono_dyn.md").read_text(encoding="utf-8")
    assert '"в_кадре"' not in text
    assert "НЕ пиши" in text


def test_hero_is_absent_until_the_film_introduces_them() -> None:
    """Каст ячейки говорит, кто участвует, но не отменяет момент выхода.

    Живой прогон: карточка скелета называла женщину уже в третьей сцене, и
    расчёт ставил её в кадр за две сцены до того, как она вошла в вагон.
    Появление считается по всему ролику, а не внутри сцены.
    """
    early = {
        "id_scene": "scene_03",
        "цепь_действия": [{"phase_index": 1, "action": "Игнат теребит край конверта", "subject": "c01"}],
    }
    entry = {
        "id_scene": "scene_05",
        "цепь_действия": [
            {"phase_index": 1, "action": "женщина входит в вагон через проём", "subject": "c02"},
            {"phase_index": 2, "action": "Игнат толкает конверт к её руке", "subject": "c01"},
        ],
    }
    later = {
        "id_scene": "scene_06",
        "цепь_действия": [{"phase_index": 1, "action": "Игнат вжимается в спинку", "subject": "c01"}],
    }
    cast = {"scene_03": ["c01", "c02"], "scene_05": ["c01", "c02"], "scene_06": ["c01", "c02"]}
    fill_in_frame([early, entry, later], scene_cast=cast, names=_NAMES)
    assert _frames(early) == ["c01"]
    assert _frames(entry) == ["c01, c02", "c01, c02"]
    # Появилась — дальше держится, даже когда текст о ней молчит.
    assert _frames(later) == ["c01, c02"]


def test_departure_holds_across_scene_boundaries() -> None:
    """Вышедшая из вагона не возвращается в кадр сама на границе сцены.

    Живой прогон: женщина уходила на станции в шестой сцене и снова стояла
    в кадре в седьмой — «ушёл» сбрасывался вместе со сценой.
    """
    leave = {
        "id_scene": "scene_06",
        "цепь_действия": [
            {"phase_index": 1, "action": "женщина прячет конверт под полу пальто", "subject": "c02"},
            {"phase_index": 2, "action": "женщина уходит за створки дверей", "subject": "c02"},
        ],
    }
    after = {
        "id_scene": "scene_07",
        "цепь_действия": [{"phase_index": 1, "action": "Игнат сжимает пустую ладонь", "subject": "c01"}],
    }
    cast = {"scene_06": ["c01", "c02"], "scene_07": ["c01", "c02"]}
    fill_in_frame([leave, after], scene_cast=cast, names=_NAMES)
    # В кадре собственного ухода её ещё видно.
    assert _frames(leave) == ["c01, c02", "c01, c02"]
    assert _frames(after) == ["c01"]


def test_skipped_agent_still_feeds_context_in_pointwise_run() -> None:
    """Точечный ▶ не пересчитывает срез, но целевой агент обязан его видеть.

    Живой прогон: `sd_cam` в режиме only_agent не получал фаз action —
    пропущенный агент выходил, не положив срез в results. Камера снимала
    вслепую: девять сцен схлопывались в две, id выдумывались (`scene_p1`),
    и сборщик терял связь шота со сценой.
    """
    import inspect

    from app.services.scene_design import runner

    src = inspect.getsource(runner.run_category_agents)
    skip = src[src.index("if target and name != target:") :][:1200]
    assert "load_checkpoint(project, name)" in skip
    assert "results[name] = stale" in skip
