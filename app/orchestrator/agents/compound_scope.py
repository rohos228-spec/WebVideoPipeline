"""Просьба из нескольких частей: «поменяй местами кадры 3 и 4, а кадр 6 перерисуй».

Каждое точное правило понимает одно действие и молча теряло остальное: кадр 6 пропадал, а голос
вставал после монтажа. Здесь просьба делится на части. Если правило есть для каждой, план
собирается из них по порядку, иначе просьбу разбирает планировщик целиком. Часть плана
порядок задаёт сама работа: монтаж последним, перед ним новая озвучка и применение правок кадров.
"""

from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_VERB = re.compile(
    r"(?<![а-яё])(?:поменяй|смени|замени|сделай|поставь|перерисуй|нарисуй|дорисуй|переделай|перегенер\w*|"
    r"сгенер\w*|перезапусти|запусти|прогони|собери|пересобери|перемонтируй|смонтируй|убери|добавь|верни|"
    r"покажи|удали|создай|подними|опусти|снизь|увеличь|уменьши|отключи|выключи|включи|почини|исправь|"
    r"разберись|проверь|доведи|опубликуй|выложи|примени|озвучь|сократи|перепиши|оживи|найди|выгрузи|"
    r"скачай|перекрась|переставь|отмени)(?![а-яё])",
    re.IGNORECASE,
)
_SEP = re.compile(
    r"\s*[,;]\s*(?:(?:и|а|потом|затем)\s+)*|\s*\.\s+(?=[А-ЯЁа-яё])|"
    r"\s+(?:(?:и|а)\s+)?(?:потом|затем|после\s+этого)\s+|\s+(?:и|а)\s+",
    re.IGNORECASE,
)
_ONE_ASK = re.compile(
    r"^\s*(?:почему|как|зачем|сколько|что|где|когда|какой|какая|какие|можно\s+ли|есть\s+ли|если|стоит\s+ли|"
    r"нужно\s+ли|надо\s+ли)(?![а-яё])|\?\s*$",
    re.IGNORECASE,
)
_LEAD = re.compile(r"^(?:(?:и|а|да|ну|потом|затем|после\s+этого)[\s,]+)+", re.IGNORECASE)
_HAS_FRAME = re.compile(r"кадр", re.IGNORECASE)
# «Перерисуй кадры 2 и 5, сделай их ночными»: вторая часть уточняет ту же правку кадров.
_WISH = re.compile(
    r"(?<![а-яё])(?:сделай|убери|добавь|замени|перекрась|нарисуй|пусть|чтобы)(?![а-яё])", re.IGNORECASE
)
_OWN_OBJECT = re.compile(
    r"кадр|картинки|картинках|картинок|голос|диктор|музык|проект|сценари|ролик|видео|озвучк|субтитр|промт|промпт",
    re.IGNORECASE,
)
_PROJECT = re.compile(r"(?:проект\w*|#)\s*#?(\d{1,5})", re.IGNORECASE)

_NUMBERED = r"(?P<word>кадр\w*|проект\w*)\s+(?:№\s*|#)?(?:\d{1,5}|[а-яё]+(?:ый|ий|ой|ом|ем))"
_MIND = r"(?:а\s+нет|хотя\s+нет|или\s+нет|нет|вернее|точнее|лучше)"
_CHANGED = re.compile(
    rf"(?P<before>{_NUMBERED})\s*[,—–-]?\s*{_MIND}(?:[,\s]+(?:{_MIND}|давай))*[,\s]+"
    r"(?:(?:кадр\w*|проект\w*)\s+)?(?:№\s*|#)?(?P<new>\d{1,5}|[а-яё]+(?:ый|ий|ой|ом|ем))(?![а-яё])",
    re.IGNORECASE,
)
_NOT_THIS = re.compile(
    r"(?<![а-яё])не\s+(?:кадр\w*|проект\w*)\s+\d{1,5}\s*,?\s*а\s+(?=(?:кадр\w*|проект\w*)\s+\d)", re.IGNORECASE
)
_BUT_NOT = re.compile(r"\s*,?\s+а\s+не\s+(?:кадр\w*|проект\w*)\s+\d{1,5}", re.IGNORECASE)
_OPEN_MIND = re.compile(
    r"(?<![а-яё])(?:а\s+нет|хотя\s+нет|или\s+нет|нет,?\s+лучше|передумал\w*)(?![а-яё])", re.IGNORECASE
)


def resolve_changed_mind(goal: str) -> str:
    """«Кадр 2, а нет, лучше кадр 3» → «кадр 3»: действует то, что человек сказал последним."""
    text = _BUT_NOT.sub("", _NOT_THIS.sub("", str(goal or "")))

    def later(found: re.Match[str]) -> str:
        return f"{found.group('word')} {found.group('new')}"

    return _CHANGED.sub(later, text)


def has_open_change_of_mind(goal: str) -> bool:
    """Человек передумал, а заменить номер нельзя: правила взяли бы первый вариант."""
    return bool(_OPEN_MIND.search(goal or ""))


def _inside_quotes(text: str, at: int) -> bool:
    head = text[:at]
    return head.count("«") > head.count("»") or head.count('"') % 2 == 1


def split_clauses(goal: str) -> list[str]:
    """Части просьбы, у каждой свой глагол. Вопрос и «если …» остаются целыми."""
    text = " ".join(str(goal or "").split())
    if not text or _ONE_ASK.search(text):
        return [text] if text else []
    clauses: list[str] = []
    start, last, prev_end = 0, 0, 0
    for found in [*_SEP.finditer(text), None]:
        if found is not None and _inside_quotes(text, found.start()):
            continue
        end = found.start() if found is not None else len(text)
        if last > start and _starts_new(text[start:prev_end], text[last:end]):
            clauses.append(text[start:prev_end])
            start = last
        prev_end = end
        if found is not None:
            last = found.end()
    clauses.append(text[start:prev_end])
    return [_LEAD.sub("", clause).strip(" ,;.") for clause in clauses if clause.strip(" ,;.")]


def _starts_new(current: str, piece: str) -> bool:
    if not _VERB.search(current) or not _VERB.search(piece):
        return False
    return not (_HAS_FRAME.search(current) and _WISH.search(piece) and not _OWN_OBJECT.search(piece))


def with_project(clause: str, goal: str) -> str:
    """Проект назван один раз («в проекте 5 смени голос и собери»): он относится ко всем частям."""
    named = _PROJECT.search(goal or "")
    if not named or _PROJECT.search(clause):
        return clause
    return f"{clause} проект {named.group(1)}"


_STEP_WORDS = (
    ("промт", "img_pr"),
    ("промпт", "img_pr"),
    ("картин", "img"),
    ("видео", "video"),
    ("озвучк", "audio"),
    ("сценари", "script"),
    ("музык", "music"),
)
_STEP_CLAUSE = re.compile(
    r"^(?:давай\s+)?(?:перезапусти|запусти|прогони|сгенер\w*|перегенер\w*)\s+(?:заново\s+|снова\s+|ещ[её]\s+раз\s+)?"
    r"(?:все\s+|всю\s+|весь\s+)?([а-яё]+)(?:\s+(?:заново|снова|ещ[её]\s+раз|картинок))?"
    r"(?:\s+(?:в\s+)?проект\w*\s*#?\d{1,5})?\s*[.!]*$",
    re.IGNORECASE,
)


def step_clause_plan(clause: str, project_id: int | None) -> list[PlanStep] | None:
    """«Перезапусти картинки» как часть составной просьбы: один шаг конвейера с ценой на карточке."""
    found = _STEP_CLAUSE.match(clause.strip().lower())
    named = _PROJECT.search(clause)
    if named:
        project_id = int(named.group(1))
    if not found or project_id is None:
        return None
    word = found.group(1).replace("ё", "е")
    code = next((step for stem, step in _STEP_WORDS if word.startswith(stem)), None)
    if code is None:
        return None
    return [_run_step(int(project_id), code)]


_STEP_NAMES = {
    "img_pr": "Промты картинок",
    "img": "Картинки",
    "video": "Видео",
    "audio": "Озвучка",
    "script": "Сценарий",
    "music": "Музыка",
}


def _run_step(project_id: int, code: str) -> PlanStep:
    return PlanStep(
        action="step.run_step",
        params={"project_id": project_id, "step_code": code},
        mutating=True,
        risk="high",
        expected=f"Шаг «{_STEP_NAMES[code]}» проекта #{project_id} выполнен заново.",
    )


def _apply_step(queued: list[PlanStep], project_id: int) -> PlanStep:
    ops = [op for step in queued for op in step.params.get("ops") or [] if isinstance(op, dict)]
    frames = sorted({int(op["frame_number"]) for op in ops if op.get("frame_number")})
    images = sum(1 for op in ops if str(op.get("type") or "").startswith("image_"))
    videos = sum(1 for op in ops if str(op.get("type") or "").startswith("video_"))
    return PlanStep(
        action="montage.apply_queue",
        params={
            "project_id": project_id,
            "frame_numbers": frames,
            "max_image_ops": images,
            "max_video_ops": videos,
        },
        mutating=True,
        risk="high",
        expected="Правки кадров применены, новые файлы в кадрах.",
    )


def _edits_images(step: PlanStep) -> bool:
    return step.action == "prompt.edit_text" and "КАРТИНК" in str(step.params.get("append") or "")


def _inserted(plan: list[PlanStep], at: int, step: PlanStep) -> list[PlanStep]:
    return [*plan[:at], step, *plan[at:]]


def order_steps(plan: list[PlanStep]) -> list[PlanStep]:
    """Порядок, который требует сама работа, а не порядок слов в просьбе.

    Монтаж озвучку не пишет и новых картинок не рисует: всё, что влияет на ролик, идёт раньше него.
    Новые промты картинок нужны раньше самих картинок.
    """
    out = list(plan)
    montage = [step for step in out if step.action == "montage.run_montage"]
    if montage:
        out = [step for step in out if step.action != "montage.run_montage"]
        target = montage[0]
        pid = int(target.params.get("project_id") or 0)
        runs = {str(s.params.get("step_code")) for s in out if s.action == "step.run_step"}
        if any(s.action == "audio.set_voice" for s in out) and "audio" not in runs:
            out.append(_run_step(pid, "audio"))
        queued = [s for s in out if s.action == "montage.queue_ops"]
        if queued and not any(s.action == "montage.apply_queue" for s in out):
            out.append(_apply_step(queued, pid))
        out.append(target)
    if any(_edits_images(s) for s in out):
        runs = [s for s in out if s.action == "step.run_step"]
        codes = {str(s.params.get("step_code")) for s in runs}
        first_img = next(
            (i for i, s in enumerate(out) if s.action == "step.run_step" and s.params.get("step_code") == "img"),
            None,
        )
        if first_img is not None and "img_pr" not in codes:
            out = _inserted(out, first_img, _run_step(int(out[first_img].params["project_id"]), "img_pr"))
    return out


def compound_note(clauses: list[str]) -> str:
    """Подсказка планировщику: ни одна часть не должна пропасть молча."""
    parts = "; ".join(f"{i}) {text}" for i, text in enumerate(clauses, 1))
    return (
        f"В просьбе несколько частей: {parts}. План должен выполнить все части в этом порядке. "
        "Если для части не хватает данных (например, суммы), верни пустой план и задай один вопрос об этом. "
        "Не ставь платный запуск вместо пропущенной части."
    )
