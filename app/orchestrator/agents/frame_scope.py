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
