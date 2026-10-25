"""«Отчёт по данным работы с GPT»: просьба узнаётся кодом, план из одного чтения без модели.

Модель на такие просьбы ставила ``node.output`` на каждую ноду «Работа с GPT» (статус, модель и
промт ноды, а не её данные) или пересказывала просьбу. Здесь — слова просьбы → один шаг
``report.gpt_data`` с проектом, нодой и колонками из этих же слов. Не похоже на такую просьбу
(правка, запуск, отчёт по нодам или по нескольким проектам) — None, план строит модель как раньше.
"""

from __future__ import annotations

import re
from typing import Any

ACTION = "report.gpt_data"

_REPORT = re.compile(r"отчет|выгруз|экспорт|таблиц|сводк|xlsx|excel|эксел|html|скачать|файл")
_DATA = re.compile(
    r"сцен|персонаж|(?<![а-я])геро[йеия]|пром(?:п)?т\w*\s+(?:для\s+)?(?:картин|изображ|видео|анимац)"
    r"|данн\w*.{0,40}(?:гпт|gpt)|(?:гпт|gpt).{0,40}данн|работ\w*\s+с\s+(?:гпт|gpt)"
)
_ASK = re.compile(
    r"(?:какие|какой|какая|кто|список|перечисли|покажи|назови)\s+(?:\w+\s+){0,2}"
    r"(?:сцен|персонаж|геро[йеи])"
)
_EDIT = re.compile(
    r"(?<![а-я])(?:запуст|прогон|прогна|перезапуст|сгенерир|генерир|поменя|смени|измени|замени|допиш|удали"
    r"|верни|откат|примени|поставь|добав|подтверд|одобр|отклон|убери|переимен|нарисуй|создай|пересобер"
    r"|перепиш|исправ|почини|останов|стоп)"
)
_OTHER_REPORT = re.compile(
    r"по\s+(?:всем\s+)?нод|все\s+ноды|по\s+проектам|нескольк\w*\s+проект|всем\s+проект|всех\s+проект"
    r"|перезапис|затер|какие\s+поля|трат|потрач|стоимост|сколько\s+стоил|(?<![а-я])лог(?:и|ов|ах)?(?![а-я])"
)
# «проекта 7», «проектов 5 и 7», «проекты 5, 6», «#12».
_PROJECT = re.compile(
    r"проект\w*\s*(?:№|#)?\s*(\d{1,6}(?:\s*(?:,|и|-|–)\s*\d{1,6})*)(?!\d)|(?<![\w#])#(\d{1,6})(?!\d)"
)
_NODE_NUM = re.compile(r"(?:гпт|gpt)\s*[-№#]?\s*(\d{1,2})(?!\d)(?!\s*(?:кадр|сцен))")
_ORDINALS = (("перв", 1), ("втор", 2), ("трет", 3), ("четверт", 4), ("пят", 5))
_ORDINAL_NODE = re.compile(r"(перв|втор|трет|четверт|пят)\w*\s+(?:нод\w*\s+)?(?:работ\w*\s+с\s+)?(?:гпт|gpt)")


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def wants_data_report(text: str) -> bool:
    low = _norm(text)
    if not low or _EDIT.search(low) or _OTHER_REPORT.search(low):
        return False
    return bool((_REPORT.search(low) and _DATA.search(low)) or _ASK.search(low))


def named_projects(text: str) -> list[int]:
    found = [int(n) for a, b in _PROJECT.findall(_norm(text)) for n in re.findall(r"\d+", a or b)]
    return list(dict.fromkeys(found))


def named_node(text: str) -> str | None:
    """«работы с гпт 1», «гпт-2», «второй работы с гпт» → n_excel_gpt_N."""
    low = _norm(text)
    m = _NODE_NUM.search(low)
    if m:
        return f"n_excel_gpt_{int(m.group(1))}"
    m = _ORDINAL_NODE.search(low)
    if m:
        for stem, num in _ORDINALS:
            if m.group(1).startswith(stem):
                return f"n_excel_gpt_{num}"
    return None


def _kind(low: str) -> str:
    if re.search(r"xlsx|excel|эксел", low):
        return "xlsx"
    if "html" in low:
        return "html"
    return "chat"


def report_params(text: str, project_id: int | None) -> dict[str, Any] | None:
    """Параметры шага по словам просьбы; None — не такая просьба или проект не ясен."""
    from app.services.action_layer import report_data_format as fmt

    if not wants_data_report(text):
        return None
    named = named_projects(text)
    if len(named) > 1:
        return None
    pid = named[0] if named else project_id
    if not pid:
        return None
    low = _norm(text)
    params: dict[str, Any] = {"project_id": int(pid), "kind": _kind(low)}
    node = named_node(text)
    if node:
        params["node_key"] = node
    preset, extra = fmt.columns_from_text(text)
    if preset != fmt.DEFAULT_PRESET:
        params["preset"] = preset
    if extra:
        params["columns"] = [*fmt.PRESETS[preset], *extra]
    return params


def report_step(text: str, project_id: int | None) -> dict[str, Any] | None:
    params = report_params(text, project_id)
    if params is None:
        return None
    return {
        "action": ACTION,
        "params": params,
        "expected": "таблица данных работы с GPT и файлы xlsx и html",
        "paid": False,
        "mutating": False,
        "risk": "none",
    }
