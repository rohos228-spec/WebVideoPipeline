"""Промт одного кадра из чата: «покажи промт второго кадра», «поменяй промт кадра 3 на сову в снегу».

Планировщик на «покажи промт» звал отчёт работы с GPT (там промтов нет), а на «поменяй промт
кадра 3 на …» отвечал «промт пока не изменился» и советовал ту же фразу другими словами.
Здесь план без модели: показать — чтение доски монтажа, сменить — правка в очередь кадра
(``image_regen_prompt``): новая картинка по новому промту появится после применения очереди.
"""

from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_ORDINALS = {
    "перв": 1,
    "втор": 2,
    "трет": 3,
    "четверт": 4,
    "пят": 5,
    "шест": 6,
    "седьм": 7,
    "восьм": 8,
    "девят": 9,
    "десят": 10,
}
_NUM = r"(\d{1,4}|перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|седьм\w*|восьм\w*|девят\w*|десят\w*)"
_FRAME = re.compile(rf"кадр\w*\s+(?:№\s*)?{_NUM}|{_NUM}(?:-?(?:й|го|ом|м))?\s+кадр\w*")
_SHOW = re.compile(r"(?<![а-я])(?:покажи|покажите|выведи|какой|какая|дай|скинь|напиши)(?![а-я])")
_SET = re.compile(r"(?<![а-я])(?:поменяй|смени|замени|измени|поставь|сделай|перепиши)(?![а-я])")
_PROMPT = re.compile(r"промт|промпт|prompt")
_VALUE = re.compile(r"(?:\s+на\s+|:\s*|\s+[—-]\s+)(.+)$")
_PROJECT = re.compile(r"(?:проект\w*|#)\s*(\d{1,5})")


def _norm(goal: str) -> str:
    return " ".join(str(goal or "").lower().replace("ё", "е").split())


def frame_number(text: str) -> int | None:
    found = _FRAME.search(text)
    if not found:
        return None
    raw = found.group(1) or found.group(2)
    if raw.isdigit():
        return int(raw)
    return next((n for stem, n in _ORDINALS.items() if raw.startswith(stem)), None)


_REPORT_WORDS = re.compile(r"отчет|выгруз|экспорт|таблиц|сводк|xlsx|excel|эксел|html|скачать|файл")
_GPT_DATA = re.compile(r"сцен|персонаж|геро[йеия]|гпт|gpt")


def board_not_report(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    """«Сколько длится каждый кадр», «сколько кадров и можно ли больше»: доска, а не отчёт GPT.

    Планировщик звал ``report.gpt_data`` и чат отвечал ссылкой на xlsx «сцен 0, персонажей 0».
    """
    text = _norm(goal)
    if not plan or _REPORT_WORDS.search(text) or _GPT_DATA.search(text) or "кадр" not in text:
        return plan
    out: list[PlanStep] = []
    for step in plan:
        if step.action == "report.gpt_data" and isinstance(step.params.get("project_id"), int):
            out.append(
                PlanStep(
                    action="montage.get_board",
                    params={"project_id": int(step.params["project_id"])},
                    mutating=False,
                    risk="none",
                    expected="Кадры проекта с доски монтажа: число, длительность, картинки и видео.",
                )
            )
        else:
            out.append(step)
    return out


def frame_prompt_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    text = _norm(goal)
    if not _PROMPT.search(text) or "видео" in text or "анимац" in text:
        return None
    number = frame_number(text)
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1))
    if number is None or project_id is None:
        return None
    if _SET.search(text):
        tail = _FRAME.split(text, maxsplit=1)[-1] if _FRAME.search(text) else ""
        value = _VALUE.search(tail or "")
        prompt = value.group(1).strip(" .«»\"") if value else ""
        prompt = _PROJECT.sub("", prompt).strip(" ,.")
        if len(prompt) < 3:
            return None
        original = re.search(re.escape(prompt), " ".join(str(goal).split()), re.IGNORECASE)
        prompt = original.group(0) if original else prompt
        return [
            PlanStep(
                action="montage.queue_ops",
                params={
                    "project_id": int(project_id),
                    "ops": [{"type": "image_regen_prompt", "frame_number": number, "prompt": prompt}],
                },
                mutating=True,
                risk="medium",
                expected=f"Новый промт картинки кадра {number} поставлен в очередь правок: «{prompt}». "
                "Картинка перерисуется после применения очереди.",
            )
        ]
    if _SHOW.search(text):
        return [
            PlanStep(
                action="montage.get_board",
                params={"project_id": int(project_id), "prompt_frames": [number]},
                mutating=False,
                risk="none",
                expected=f"Промт картинки кадра {number} с доски монтажа.",
            )
        ]
    return None


# «Перезапусти видео кадра 2», «перегенери видео 3 кадра», «видео кадра 5 кривое, переделай».
_VIDEO_REDO = re.compile(
    r"(?<![а-я])(?:перезапуст\w*|перегенер\w*|переделай|переделать|сгенер\w*\s+заново|сделай\s+заново|"
    r"запусти\s+заново|запусти\s+снова|перерисуй|переанимир\w*|кривое|кривой)(?![а-я])"
)
_VIDEO_WORD = re.compile(r"(?<![а-я])(?:видео|видос\w*|анимаци\w*|ролик\w* кадра)(?![а-я])")


def video_frame_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    """Видео одного кадра заново — правка ``video_regen`` в очередь кадра (планировщик: «не умеет»)."""
    text = _norm(goal)
    if not _VIDEO_WORD.search(text) or not _VIDEO_REDO.search(text) or _PROMPT.search(text):
        return None
    number = frame_number(text)
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1))
    if number is None or project_id is None:
        return None
    return [
        PlanStep(
            action="montage.queue_ops",
            params={"project_id": int(project_id), "ops": [{"type": "video_regen", "frame_number": number}]},
            mutating=True,
            risk="medium",
            expected=f"Видео кадра {number} поставлено в очередь на перегенерацию. "
            "Новое видео появится после применения очереди.",
        )
    ]


_IMAGE_REDO = re.compile(
    r"(?<![а-я])(?:переделай|переделать|перегенер\w*|перерисуй|сгенер\w*\s+заново|сделай\s+заново|"
    r"нарисуй\s+заново|кривой|кривая|кривое|некрасив\w*|плох\w*)(?![а-я])"
)
# Что именно поменять («сделай сову белой», «убери кровь») — это правка промта, её ведёт планировщик.
_IMAGE_WISH = re.compile(r"(?<![а-я])(?:сделай\s+(?!заново)\w+|убери|добавь|замени|поменяй|чтобы|на\s+[а-я]+)(?![а-я])")


_WISH_START = re.compile(
    r"(?<![а-я])(?:сделай|сделать|убери|убрать|добавь|добавить|замени|заменить|поменяй|перекрась|"
    r"нарисуй|пусть|чтобы)(?![а-я])"
)
_FRAME_PHRASE = re.compile(
    r"(?:(?:в|на|у)\s+)?(?:кадр\w*\s+(?:№\s*)?(?:\d{1,4}|[а-я]+(?:ой|ом|ый|ий|ем|ого))|"
    r"(?:\d{1,4}|[а-я]+(?:ом|ем|ой|ый|ий))(?:-?(?:й|го|ом|м))?\s+кадр\w*)"
)


def frame_fix_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    """«В кадре 2 сделай сову белой», «на 3 кадре убери кровь»: исправление картинки одного кадра.

    Правка в очередь кадра (``image_regen_correction``) с текстом просьбы; планировщик то ставил
    её, то отвечал готовой командой вместо карточки.
    """
    text = _norm(goal)
    if _VIDEO_WORD.search(text) or _PROMPT.search(text) or "?" in text:
        return None
    number = frame_number(text)
    if number is None or not _WISH_START.search(text):
        return None
    if re.search(r"(?<![а-я])(?:кадр\w*\s+(?:по|на)\s+\d|секунд|длительн|местами|удали|склей|раздел|объедин)", text):
        return None
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1))
    if project_id is None:
        return None
    original = " ".join(str(goal).split())
    wish = _FRAME_PHRASE.sub(" ", original, count=1)
    wish = re.sub(r"(?:(?:в|для|у)\s+)?(?:проект\w*|#)\s*#?\d{1,5}", " ", wish, flags=re.IGNORECASE)
    wish = re.sub(r"\s+([,.;:])", r"\1", " ".join(wish.split())).strip(" ,.:;—-")
    wish = re.sub(r"^(?:переделай|перегенери\w*|перерисуй)[,\s]+", "", wish, flags=re.IGNORECASE)
    if len(wish) < 6:
        return None
    return [
        PlanStep(
            action="montage.queue_ops",
            params={
                "project_id": int(project_id),
                "ops": [{"type": "image_regen_correction", "frame_number": number, "prompt": wish}],
            },
            mutating=True,
            risk="medium",
            expected=f"Исправление картинки кадра {number} поставлено в очередь: «{wish}». "
            "Новая картинка появится после применения очереди.",
        )
    ]


def image_frame_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    """«Кадр 4 переделай», «кит на 2 кадре кривой, перегенери»: картинка кадра заново (``image_regen``)."""
    text = _norm(goal)
    if _VIDEO_WORD.search(text) or _PROMPT.search(text) or not _IMAGE_REDO.search(text):
        return frame_fix_plan(goal, project_id)
    if "?" in text or re.search(r"(?<![а-я])(?:почему|какой|какая|какие|зачем|как)(?![а-я])(?!-то)", text):
        return None
    rest = _IMAGE_REDO.sub(" ", text)
    if _IMAGE_WISH.search(rest):
        return frame_fix_plan(goal, project_id)
    number = frame_number(text)
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1))
    if number is None or project_id is None:
        return None
    return [
        PlanStep(
            action="montage.queue_ops",
            params={"project_id": int(project_id), "ops": [{"type": "image_regen", "frame_number": number}]},
            mutating=True,
            risk="medium",
            expected=f"Картинка кадра {number} поставлена в очередь на перегенерацию. "
            "Новая картинка появится после применения очереди.",
        )
    ]


_SWAP = re.compile(
    r"(?:поменяй|смени|переставь)\s+местами\s+(?:(картинк\w*|видео)\s+)?кадр\w*\s+(\d{1,4})\s+(?:и|с)\s+(\d{1,4})"
    r"|(?:поменяй|смени|переставь)\s+(?:(картинк\w*|видео)\s+)?кадр\w*\s+(\d{1,4})\s+(?:и|с)\s+(\d{1,4})\s+местами"
)


def swap_frames_plan(goal: str, project_id: int | None) -> list[PlanStep] | None:
    """«Поменяй местами кадры 1 и 2»: обмен картинками (и видео, если просят) — ``montage.swap_slots``.

    Планировщик то ставил обмен картинками, то переспрашивал «картинки или видео?».
    """
    text = _norm(goal)
    found = _SWAP.search(text)
    if not found:
        return None
    what = found.group(1) or found.group(4) or ""
    a = int(found.group(2) or found.group(5))
    b = int(found.group(3) or found.group(6))
    named = _PROJECT.search(text)
    if named:
        project_id = int(named.group(1))
    if project_id is None or a == b:
        return None
    kinds = ["video"] if what.startswith("видео") else ["image"]
    if not what and ("видео" in text.replace(found.group(0), "") or "целиком" in text):
        kinds = ["image", "video"]
    names = {"image": "картинки", "video": "видео"}
    steps = [
        PlanStep(
            action="montage.swap_slots",
            params={"project_id": int(project_id), "kind": kind, "a_frame": a, "a_shot": 1, "b_frame": b, "b_shot": 1},
            mutating=True,
            risk="medium",
            expected=f"{names[kind].capitalize()} кадров {a} и {b} поменялись местами.",
        )
        for kind in kinds
    ]
    if kinds == ["image"]:
        steps[0] = PlanStep(
            action=steps[0].action,
            params=steps[0].params,
            mutating=True,
            risk="medium",
            expected=f"Картинки кадров {a} и {b} поменялись местами. Видео тоже можно: «поменяй местами видео "
            f"кадров {a} и {b}».",
        )
    return steps
