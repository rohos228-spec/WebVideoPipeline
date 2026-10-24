from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.orchestrator.agents.question_context import ANSWER_RULES, fact_limit, fact_text

_TIMEOUT_S = 90.0
_FACT_LIMIT = 1200
_TOTAL_LIMIT = 9000
_STEP_LIMIT = 8
_STEP_SUMMARY = 300
APPROVAL_EXPIRED = "approval_expired"
APPROVAL_EXPIRED_TEXT = "Подтверждение не дали вовремя — задача отменена, ничего не изменено."
APPROVAL_STALE = "approval_stale"
APPROVAL_STALE_TEXT = "Сервер перезапускался, карточка устарела — повторите просьбу. Ничего не изменено."
NOT_UNDERSTOOD = "Не понял, что сделать."
NOT_UNDERSTOOD_HINT = (
    "Напишите номер проекта и что нужно, например «запусти картинки в проекте 6» "
    "или «что с проектом 4». Если не знаете, с чего начать, напишите «помоги»."
)
# Заметка о чужой карточке дописывается к причине; фильтр сырых слов её не касается.
WAITING_OTHER = "В этом чате ждёт подтверждения другая задача: «{goal}» — карточка выше."
_WAITING_HEAD = WAITING_OTHER.split("«", 1)[0]
# Сырой ответ планировщика: служебные имена (project_id, node.set_model), «каталог», «action».
_RAW_REASON = re.compile(
    r"[a-z0-9]+[._][a-z_]|каталог|(?<![a-zа-я])(?:action|project|node|step|snapshot|catalog|json|id)(?![a-zа-я])",
    re.IGNORECASE,
)


def raw_reason(text: str) -> bool:
    """True — причину планировщика показывать человеку нельзя: в ней внутренние слова."""
    return bool(_RAW_REASON.search(text or ""))


# Уточняющий вопрос планировщика: «Уточните, нужно ли отменить…», «Какую правку вернуть?».
_CLARIFY = re.compile(
    r"уточни|нужно\s+ли|\?|(?<![а-я])(?:какую|какой|какое|какие|какая|напишите|выберите|скажите)(?![а-я])",
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"(?<=[.!?…])\s+")
# Служебное в причине: id и суммы, ключи нод, поля плана, «в каталоге / журнале / контексте».
_JARGON = (
    (re.compile(r"\s*\((?:[^()]*[a-z_][^()]*)\)"), ""),
    (re.compile(r"(?:№|#)?\b[0-9a-f]{8,}\b(?:\.\.\.)?", re.IGNORECASE), ""),
    (re.compile(r"\b(?:вида\s+)?[a-z][a-z0-9]*(?:[._][a-z0-9]+)+\b", re.IGNORECASE), ""),
    (re.compile(r"\b(?:proposal|digest|snapshot|target|version|id)\b", re.IGNORECASE), ""),
    (re.compile(r"\b(?:в|из)\s+(?:доступном\s+)?(?:контексте|журнале|каталоге|списке)\b", re.IGNORECASE), ""),
)


# Часть фразы, которая просит у человека служебное поле («передайте proposal_id», «предоставьте
# snapshot_id»), выкидывается целиком: человек его не знает и знать не должен.
_TECH_ASK = re.compile(r"(?:proposal|digest|snapshot|target|version)\w*|(?<![a-z])id(?![a-z])", re.IGNORECASE)
_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=или\s)")


def _scrub(sentence: str) -> str:
    end = sentence[-1] if sentence[-1:] in ".!?…" else ""
    clauses = [part for part in _CLAUSE.split(sentence.rstrip(".!?…")) if not _TECH_ASK.search(part)]
    out = " ".join(clauses).rstrip(",;: ") + end if clauses else ""
    out = re.sub(r"\s*(?:№|#)\s?\d+\b", "", out)
    for pattern, repl in _JARGON:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+([,.:;!?])", r"\1", " ".join(out.split()))
    out = re.sub(r"([,:;])(?:\s*[,:;])+", r"\1", out)
    return re.sub(r"^[,:;\s]+", "", out).strip()


def clarify_text(reason: str) -> str:
    """Уточнение планировщика человеческими словами; пусто — показать его нельзя.

    Пустой план с понятной причиной («правка ещё ожидает применения. Уточните, нужно ли
    отменить…») — не «Не понял»: человеку нужен сам вопрос, без id и служебных имён.
    """
    from app.services.plain_ru import plain_ru
    from app.services.redact import redact_text

    text = " ".join(plain_ru(redact_text(str(reason or ""))).split())
    sentences = [_scrub(part) for part in _SENTENCES.split(text) if part.strip()]
    sentences = [part for part in sentences if len(part) >= 8 and not raw_reason(part)]
    asks = [part for part in sentences if _CLARIFY.search(part)]
    if not asks:
        return ""
    picked = [part for part in sentences if part in asks or part == sentences[0]][:3]
    out = " ".join(part if part.endswith((".", "?", "!", "…")) else part + "." for part in picked)
    return out[:400]


# Планировщик понял просьбу, но такого действия у чата нет: «в каталоге нет действия для удаления проекта».
_MISSING_ACTION = re.compile(
    r"в\s+каталоге\s+нет|нет\s+в\s+каталоге|нет\s+(?:такого\s+|подходящего\s+|нужного\s+|отдельного\s+)?действи"
    r"|отсутству\w*[^.]{0,40}(?:действи|каталог)|действи\w*[^.]{0,30}отсутству",
    re.IGNORECASE,
)
_DELETE_PROJECT = re.compile(r"удал\w*\s+(?:\w+\s+)?проект|проект\w*\s+(?:№?#?\d+\s+)?удал", re.IGNORECASE)
NOT_SUPPORTED = "Этого чат пока не умеет."
# В списке проектов слева у каждого проекта меню «Действия с проектом» → «Удалить».
DELETE_PROJECT_HINT = "Удалить проект можно в списке проектов слева: меню у проекта → «Удалить»."


def missing_action_text(reason: str, goal: str = "") -> str:
    """Честный ответ, когда нужного действия у чата нет; пусто — причина о другом."""
    if not _MISSING_ACTION.search(reason or ""):
        return ""
    if _DELETE_PROJECT.search(reason or "") or _DELETE_PROJECT.search(goal or ""):
        return f"{NOT_SUPPORTED} {DELETE_PROJECT_HINT}"
    return NOT_SUPPORTED


# «Из файла / по файлу / в файле»: прикрепить файл к сообщению чата оркестратора нельзя.
_FROM_FILE = re.compile(
    r"(?:из|по|в|с)\s+(?:\w+\s+)?(?:файл|таблиц|excel|экселя?|xlsx|списка\s+в\s+файле)", re.IGNORECASE
)
_MANY_PROJECTS = re.compile(r"проект", re.IGNORECASE)
FILE_CLARIFY = (
    "Из какого файла взять темы? Прикрепить файл к сообщению в этом чате пока нельзя. "
    "Напишите темы списком, например «создай проекты: лисы, совы, киты, пчёлы, волки и "
    "запусти» — создам проекты и поставлю их в очередь."
)


# «Смени модель на gpt»: у студии GPT — и модель картинок проекта (GPT Image), и текстовая модель
# нод «Работа с GPT». Без слов о картинках, видео или нодах не угадываем, а спрашиваем.
_GPT_WORD = re.compile(r"(?<![a-zа-я])(?:gpt|гпт|джипити|чатгпт|chatgpt)", re.IGNORECASE)
_MODEL_WORD = re.compile(r"модел|нейросет|генератор", re.IGNORECASE)
_MODEL_TARGET = re.compile(
    r"картин|изображ|image|img|кадр|фото|видео|ролик|анимац|video|нод|текст|сценар|работ|агент|"
    r"промт|промпт|озвуч|голос|имидж|"
    # Текстовые модели GPT названы явно: «gpt 5.6 sol», «gpt-5.6-luna».
    r"(?<![a-zа-я])(?:sol|terra|luna|astra|сол|терра|луна|астра)(?![a-zа-я])|\d\.\d",
    re.IGNORECASE,
)
_PROJECT_NUMBER = re.compile(r"проект\w*\s+(?:№\s*)?#?(\d{1,5})", re.IGNORECASE)


def gpt_model_clarify_text(goal: str) -> str:
    """«Смени модель на gpt в проекте 5»: что именно менять — картинки или ноды GPT; пусто — понятно."""
    text = " ".join((goal or "").lower().replace("ё", "е").split())
    if not _GPT_WORD.search(text) or not _MODEL_WORD.search(text) or _MODEL_TARGET.search(text):
        return ""
    number = _PROJECT_NUMBER.search(text)
    where = f" в проекте {number.group(1)}" if number else ""
    return (
        "Какую модель поменять на GPT? Это может быть модель картинок проекта (GPT Image) или "
        "текстовая модель нод «Работа с GPT». Напишите точнее, например "
        f"«смени модель картинок на gpt{where}» или «какие модели можно поставить на работу с "
        f"GPT{where}». Ничего не меняю и не запускаю, пока не уточните."
    )


# «Модели `gpt-5` нет в списке доступных для ноды …»: планировщик понял просьбу, но такой модели
# у ноды нет. Не «Не понял», а вопрос с ближайшими моделями из каталога этой ноды.
_UNKNOWN_MODEL = re.compile(
    r"модел\w*[^.;]{0,80}?(?:нет\b|отсутству|недоступ|не\s+найден|не\s+существу|не\s+поддерж)",
    re.IGNORECASE,
)
_QUOTED_MODEL = re.compile(r"[«`\"']\s*([A-Za-z][\w.\-]{1,40})\s*[»`\"']")
_GOAL_MODEL = re.compile(r"(?:на|в|модель)\s+[«`\"']?([A-Za-z][\w.\-]{1,40})[»`\"']?\s*$", re.IGNORECASE)
# Ключ ноды («n_12», «n_excel_gpt_1»), а не модель: у моделей каталога подчёркиваний нет.
_NODE_KEY = re.compile(r"^n_|_")
# Догадка о виде ноды по словам, только когда настоящий вид ноды прочитать не удалось.
# «Работа с GPT» — текстовая нода, даже если описывает картинки: её проверяем первой.
_KIND_WORDS = (
    (
        "text",
        re.compile(r"работ\w*\s+с\s+(?:gpt|гпт)|excel_gpt|текст|сценари|нод\w*\s+«?работа", re.IGNORECASE),
    ),
    ("image", re.compile(r"картин|изображен|image|фото", re.IGNORECASE)),
    ("video", re.compile(r"видео|ролик|анимац|video", re.IGNORECASE)),
)
MODEL_KINDS = ("text", "image", "video")
UNKNOWN_MODEL_SHOWN = 5


def _model_family(name: str) -> str:
    return re.split(r"[\d\-_. ]", name.lower(), maxsplit=1)[0]


def _model_score(want: str, model: dict) -> tuple:
    import difflib
    import os

    ids = [str(model.get("id") or "").lower(), str(model.get("label") or "").lower()]
    want = want.lower()
    family = max(int(_model_family(want) == _model_family(x)) for x in ids)
    prefix = max(len(os.path.commonprefix([want, x])) for x in ids)
    ratio = max(difflib.SequenceMatcher(None, want, x).ratio() for x in ids)
    return (family, prefix, ratio)


def _model_like(name: str) -> bool:
    return bool(name) and not _NODE_KEY.search(name)


def unknown_model_name(reason: str, goal: str = "") -> str:
    """Имя модели, которой нет у ноды; пусто — не о том.

    Имя из причины планировщика берём, только если оно похоже на модель (не ключ ноды) и есть
    в просьбе человека: иначе первое слово в кавычках («n_12») выдавалось за модель.
    """
    text = reason or ""
    if not _UNKNOWN_MODEL.search(text):
        return ""
    said = " ".join((goal or "").split()).lower()
    for found in _QUOTED_MODEL.finditer(text):
        name = found.group(1).strip()
        if _model_like(name) and (not said or name.lower() in said):
            return name
    tail = _GOAL_MODEL.search(" ".join((goal or "").split()))
    name = tail.group(1).strip() if tail else ""
    return name if _model_like(name) else ""


def guess_model_kind(reason: str, goal: str = "") -> str:
    both = f"{reason} {goal}"
    return next((k for k, rx in _KIND_WORDS if rx.search(both)), "")


def _catalog(models_of, kind: str) -> list[dict]:
    return [m for m in models_of(kind) or [] if isinstance(m, dict) and m.get("id")]


def _has_model(pool: list[dict], want: str) -> bool:
    return any(want in {str(m.get("id") or "").lower(), str(m.get("label") or "").lower()} for m in pool)


def unknown_model_text(reason: str, goal: str = "", *, kind: str | None = None, models_of=None) -> str:
    """«Модели gpt-5 для этой ноды нет. Есть: gpt-5.6-sol, … Какую поставить?»; пусто — причина о другом.

    ``kind`` — настоящий вид ноды из её состояния (text/image/video). Без него вид угадывается
    по словам; не угадан — список не показываем (модели разных видов не смешиваем). При догадке
    модель, которая есть в каталоге любого вида, не называем отсутствующей.
    ``models_of(kind)`` — каталог моделей вида (по умолчанию ``node_edit.allowed_models``).
    Причина и ответ проходят ``redact_text``.
    """
    from app.services.redact import redact_text

    reason = redact_text(str(reason or ""))
    name = unknown_model_name(reason, goal)
    if not name or redact_text(name) != name:
        return ""
    if models_of is None:
        from app.services.node_edit import allowed_models as models_of
    real = kind in MODEL_KINDS
    use = kind if real else guess_model_kind(reason, goal)
    want = name.lower()
    try:
        pool = _catalog(models_of, use) if use else []
        if not real and any(_has_model(_catalog(models_of, k), want) for k in MODEL_KINDS):
            return ""  # модель в каталоге есть, вид ноды лишь угадан: не утверждаем, что её нет
    except Exception:  # noqa: BLE001 — без каталога ответ остаётся обычным
        return ""
    if _has_model(pool, want):
        return ""  # такая модель есть: причина о чём-то другом
    if not pool:
        if real or use:
            return ""
        return redact_text(f"Модели {name} для этой ноды нет. Какую модель поставить?")
    ranked = sorted(pool, key=lambda m: _model_score(want, m), reverse=True)
    shown = [str(m["id"]) for m in ranked[:UNKNOWN_MODEL_SHOWN]]
    return redact_text(f"Модели {name} для этой ноды нет. Есть: {', '.join(shown)}. Какую поставить?")


# «[Скачать отчёт]( /api/projects/…)»: модель ставит пробел после скобки, ссылка показывалась текстом.
_LINK_SPACES = re.compile(r"\]\(\s*([^\s()]+)\s*\)")


def normalize_links(text: str) -> str:
    """Markdown-ссылки без пробелов вокруг адреса в круглых скобках."""
    return _LINK_SPACES.sub(r"](\1)", text or "")


def file_clarify_text(goal: str) -> str:
    """Просьба создать проекты «из файла»: конкретный вопрос вместо «не понял»; пусто — не о том."""
    text = goal or ""
    if not _FROM_FILE.search(text) or not _MANY_PROJECTS.search(text):
        return ""
    if not re.search(r"созда|сдела|завед|нов", text, re.IGNORECASE):
        return ""
    return FILE_CLARIFY


_REASON_RU: dict[str, tuple[str, str]] = {
    "validator_blocked": (
        "проверка плана отклонила шаги, план не исполнялся",
        "Уточните просьбу: какой проект и какой шаг (например, «запусти шаг Картинки для проекта 12»).",
    ),
    "not_in_whitelist": (
        "в плане было действие, которого нет среди разрешённых",
        "Переформулируйте просьбу простыми словами, без названий функций.",
    ),
    "plan_sanity": (
        "не удалось составить подходящий план: нужного действия нет или перед запуском шага не было "
        "проверки состояния",
        "Уточните, что именно сделать и с каким проектом.",
    ),
    "empty_plan": (
        "для этой просьбы не нашлось подходящих действий, поэтому ничего не делалось и подтверждать нечего",
        "Уточните, что именно сделать и с каким проектом.",
    ),
    "unknown_cost": (
        "стоимость плана оценить не удалось, поэтому платные шаги не запускались",
        "Укажите потолок трат или выберите шаг с известной ценой.",
    ),
    "deletion_forbidden": (
        "план удалял кадры или сцены — это запрещено правилами",
        "Кадры и сцены можно только скрыть мягко, без удаления.",
    ),
    "deletion_blocked": (
        "план удалял данные — такой шаг заблокирован",
        "Кадры и сцены можно только скрыть мягко, без удаления.",
    ),
    "critic_reject": (
        "проверяющий агент отклонил план как нарушающий правила",
        "Переформулируйте просьбу или сделайте это вручную в Студии.",
    ),
    "critic_escalation": (
        "план не прошёл проверку после нескольких доработок",
        "Переформулируйте просьбу точнее.",
    ),
    "steer_not_approved": ("исправленный план не прошёл проверку", "Переформулируйте поправку."),
    "no_input_frames": (
        "запускать нечего: в проекте нет кадров",
        "Сначала сделайте разбивку на кадры (или импортируйте Excel с кадрами), потом запускайте этот шаг.",
    ),
    "plan_changed": (
        "план изменился после подтверждения и не выполнялся",
        "Повторите просьбу: я составлю план заново и покажу его.",
    ),
    "drive_failed": (
        "внутренняя ошибка при составлении или выполнении плана",
        "Повторите просьбу чуть позже.",
    ),
    "step_failed": ("шаг упал с ошибкой", "Устраните ошибку, указанную выше, и повторите."),
    "action_rejected": ("действие отказалось выполняться", "Устраните причину, указанную выше, и повторите."),
    "missing_run_id": ("внутренняя ошибка учёта запуска", "Повторите просьбу."),
    "parallel_risk": ("в параллельной группе оказался изменяющий шаг", "Повторите просьбу."),
    "plan_stopped": ("план остановлен", "Повторите просьбу, если нужно продолжить."),
    "plan_unreadable": (
        "планировщик не ответил или ответ оборвался, поэтому ничего не делалось",
        "Повторите просьбу чуть позже.",
    ),
    "plan_schema": (
        "планировщик несколько раз вернул план в неверном виде",
        "Повторите просьбу чуть проще или другими словами.",
    ),
    "project_not_selected": (
        "шагам плана нужен проект, а в чате он не выбран, поэтому ничего не выполнялось",
        "Выберите проект в чате (переключатель «Проект») и повторите просьбу.",
    ),
    "interrupted_by_restart": (
        "задача прервалась из-за перезапуска сервера",
        "Повторите просьбу — она выполнится заново.",
    ),
}


@dataclass
class StepNote:
    action: str
    title: str
    ok: bool
    text: str = ""
    error: str = ""
    readonly: bool = True
    undo: dict[str, Any] | None = None
    skipped: bool = False
    known: bool = False
    links: list[str] = field(default_factory=list)
    # Проект шага: в задаче на несколько проектов итог называет каждый.
    project_id: int | None = None


@dataclass
class TaskFacts:
    goal: str
    status: str
    error: str | None = None
    detail: str = ""
    steps: list[StepNote] = field(default_factory=list)
    # Пустой план с готовым ответом кода («возвращать нечего»): просьбу поняли, «не понял» не пишем.
    answered: bool = False


def explain_failure(exc: BaseException) -> str:
    message = getattr(exc, "message_ru", None)
    if isinstance(message, str) and message.strip():
        return "Модель не вызвана: " + message.strip()
    name = type(exc).__name__
    text = str(exc).strip()
    if name == "RouterExhausted":
        return "Модель сейчас не отвечает: все провайдеры недоступны" + (f" ({text[:200]})." if text else ".")
    if name in {"FixtureMiss", "RouterError"}:
        return "Модель не вызвана: " + (text[:200] or name) + "."
    if name == "OperationalError" and ("locked" in text.lower() or "busy" in text.lower()):
        return "База данных Студии была занята слишком долго, и запись не прошла."
    return f"Внутренняя ошибка: {name}."


def step_title(action: str) -> str:
    try:
        from app.services.action_layer.spec import ensure_builtin_actions, registry

        ensure_builtin_actions()
        spec = registry.find(action)
        if spec is not None and getattr(spec, "title_ru", ""):
            return str(spec.title_ru)
    except Exception:
        pass
    return action


def _field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def note_from_result(action: str, result: Any, *, readonly: bool) -> StepNote:
    from app.orchestrator.contracts import envelope_body, envelope_status

    title = step_title(action)
    status = envelope_status(result)
    body = envelope_body(result)
    if body is result and not isinstance(result, dict):
        body = _field(result, "data")
    if hasattr(body, "model_dump"):
        body = body.model_dump(mode="json")
    error = str(
        _field(result, "error") or (body.get("error") if isinstance(body, dict) else "") or ""
    ).strip()
    undo = body.get("undo") if isinstance(body, dict) and isinstance(body.get("undo"), dict) else None
    known = known_text(action, body) if isinstance(body, dict) and status == "ok" else ""
    text = known or (fact_text(action, body) if isinstance(body, dict) else "")
    if not text and isinstance(body, dict):
        text = _plain(body) or fields_ru(body)
    from app.services.plain_ru import plain_ru

    text, error = plain_ru(text), plain_ru(error)
    return StepNote(
        action,
        title,
        ok=status == "ok",
        text=text[: max(_FACT_LIMIT, fact_limit(action))],
        error=error[:400],
        readonly=readonly,
        undo=undo,
        known=bool(known),
        links=download_links(body) if isinstance(body, dict) else [],
    )


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _name(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    pid = item.get("project_id") or item.get("id")
    title = " ".join(str(item.get("title") or "").split())
    if title and pid:
        return f"«{title}» (#{pid})"
    return f"«{title}»" if title else (f"#{pid}" if pid else "")


def _batch_text(body: dict[str, Any]) -> str:
    names = [name for name in (_name(item) for item in body.get("projects") or []) if name]
    if not names:
        return ""
    n = len(names)
    return f"Создал {n} {_plural(n, 'проект', 'проекта', 'проектов')}: " + ", ".join(names) + "."


def _queue_text(body: dict[str, Any]) -> str:
    ids = [
        f"#{item.get('project_id')}"
        for item in body.get("enqueued") or []
        if isinstance(item, dict) and item.get("project_id")
    ]
    if not ids:
        return ""
    n = len(ids)
    mode = " до выбранной ноды" if body.get("mode") == "until_node" else ""
    return (
        f"Поставил в очередь генерации {n} {_plural(n, 'проект', 'проекта', 'проектов')}{mode}: "
        + ", ".join(ids)
        + "."
    )


def _dequeue_text(body: dict[str, Any]) -> str:
    ids = [f"#{pid}" for pid in body.get("removed") or []]
    if not ids:
        return "В очереди генерации этих проектов не было, ничего не менял."
    n = len(ids)
    return (
        f"Снял с очереди генерации {n} {_plural(n, 'проект', 'проекта', 'проектов')}: "
        + ", ".join(ids)
        + ". Сами проекты и их результаты не трогал."
    )


def _parallel_text(body: dict[str, Any]) -> str:
    got = body.get("worker_max_parallel")
    if not isinstance(got, int) or isinstance(got, bool):
        return ""
    text = f"Теперь одновременно идут до {got} {_plural(got, 'проекта', 'проектов', 'проектов')}."
    if body.get("lowered"):
        # Уменьшение не прерывает идущие шаги: человек должен знать, что они доиграют.
        text += " Уже идущие доиграют, новые будут ждать."
    return text


def _model_text(body: dict[str, Any]) -> str:
    node = body.get("node_label") or body.get("node_key")
    after = body.get("after_label") or body.get("after_model_id")
    if not node or not after:
        return str(body.get("text_ru") or "")
    before = body.get("before_label") or body.get("before_model_id")
    tail = f" (была {before})" if before and before != after else ""
    return f"На ноде «{node}» теперь модель {after}{tail}."


def _own_text(body: dict[str, Any]) -> str:
    return str(body.get("text_ru") or "").strip()


def _overview_text(body: dict[str, Any]) -> str:
    """Список проектов строками: в одну строку 15 проектов не прочитать."""
    items = [item for item in body.get("projects") or [] if isinstance(item, dict)]
    text = str(body.get("text_ru") or "").strip()
    if not items:
        return text
    head = text.split(". ", 1)[0].rstrip(".") + "."
    lines = [head]
    if body.get("queue"):
        queue = body["queue"]
        lines.append(
            f"В очереди генерации {queue.get('size', 0)}, одновременно идут не больше {queue.get('max_parallel', '?')}."
        )
    for item in items:
        extra = item.get("pause_reason") or item.get("error") or ""
        place = (
            f"{item['queue_position']}. "
            if body.get("filter") == "queue" and item.get("queue_position")
            else ""
        )
        lines.append(
            f"• {place}#{item.get('id')} «{item.get('title') or ''}»: {item.get('status_ru') or item.get('status') or ''}"
            + (f" — {extra}" if extra else "")
        )
    return "\n".join(lines)


def _resume_text(body: dict[str, Any]) -> str:
    from app.services.action_layer.builtin_insight import _status_ru

    status = body.get("final_status") or body.get("status")
    return f"Пауза снята, статус проекта: {_status_ru(str(status))}." if status else "Пауза снята."


_KNOWN_TEXT = {
    "project.create_batch": _batch_text,
    "queue.enqueue_many": _queue_text,
    "queue.set_parallel": _parallel_text,
    "queue.dequeue_many": _dequeue_text,
    "node.set_model": _model_text,
    "node.set_model_costly": _model_text,
    "node.set_params": _own_text,
    "node.set_params_costly": _own_text,
    "project.patch_settings": _own_text,
    "project.patch_settings_costly": _own_text,
    "prompt.edit_text": _own_text,
    "change.apply": _own_text,
    "change.rollback": _own_text,
    "change.reject": _own_text,
    "node.set_prompt_variant": _own_text,
    "node.set_model_many": _own_text,
    "node.set_prompt_variant_many": _own_text,
    "node.revert_model_many": _own_text,
    "project.resume": _resume_text,
    "node.revert_model": _own_text,
    "node.revert_params": _own_text,
    "node.upload_check_agent": _own_text,
    "node.clear_check_agent": _own_text,
    "node.revert_check_agent": _own_text,
    "media.list_unknown_outcomes": _own_text,
    "media.release_unknown": _own_text,
    "history.rollback": _own_text,
    "project.stop": _own_text,
    "report.nodes": _own_text,
    "report.projects": _own_text,
    "report.gpt_data": _own_text,
    "projects.overview": _overview_text,
    "project.status": _own_text,
    "group.shots_report": _own_text,
    "step.run_step": _own_text,
    "publish.send": _own_text,
    "project.create": _own_text,
    "hitl.approve": _own_text,
    "hitl.reject": _own_text,
    "hitl.regenerate": _own_text,
}


def known_text(action: str, body: dict[str, Any]) -> str:
    fn = _KNOWN_TEXT.get(action)
    if fn is None:
        return ""
    try:
        return fn(body).strip()
    except Exception:
        return ""


# Ответ — текст шага как есть, без пересказа моделью (таблица, ссылки на файлы).
VERBATIM_ACTIONS = frozenset({"report.gpt_data"})


def _simple_read(goal: str, action: str) -> bool:
    from app.orchestrator.agents.read_plan import READ_ACTIONS, read_step

    if action not in READ_ACTIONS:
        return False
    try:
        step = read_step(goal or "")
    except Exception:
        return False
    return step is not None and step["action"] == action


_REPORT_URL = re.compile(r"^/api/projects/(?:\d+/)?reports/[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}$")


def _download_link(url: Any, kind: str) -> str:
    if not isinstance(url, str) or not url.startswith("/api/projects/"):
        return ""
    if "/reports/" in url and (not _REPORT_URL.fullmatch(url) or ".." in url):
        return ""
    return f"[Скачать отчёт ({kind})]({url})"


def download_links(body: dict[str, Any]) -> list[str]:
    """Ссылки на файлы отчёта: все из downloads (html и xlsx), иначе одна download_url."""
    files = body.get("downloads")
    if isinstance(files, list) and files:
        links = [
            _download_link(f.get("url"), str(f.get("file_format") or "файл"))
            for f in files
            if isinstance(f, dict)
        ]
        return list(dict.fromkeys(link for link in links if link))
    link = _download_link(
        body.get("download_url"), str(body.get("file_format") or body.get("format") or "файл")
    )
    return [link] if link else []


def _change_line(note: StepNote, many: bool = False) -> str:
    if note.known and note.text:
        return note.text
    return f"{note.title}{_in_project(note, many)}" + (f": {note.text}" if note.text else ".")


def step_summary(note: StepNote | None) -> str:
    if note is None:
        return ""
    if not note.ok:
        return f"Не получилось «{note.title}»: {note.error or 'ошибка без пояснения'}."
    text = " ".join(note.text.split())
    if text:
        return text if len(text) <= _STEP_SUMMARY else text[: _STEP_SUMMARY - 1].rstrip() + "…"
    return f"Готово: {note.title}."


def _plain(body: dict[str, Any]) -> str:
    for key in ("text_ru", "summary_ru", "message", "summary"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_FIELD_RU = {
    "status": "статус",
    "state": "состояние",
    "ok": "успешно",
    "found": "найдено",
    "started": "запущено",
    "done": "готово",
    "count": "количество",
    "total": "всего",
    "items": "элементы",
    "rows": "строки",
    "nodes": "ноды",
    "frames": "кадры",
    "scenes": "сцены",
    "project_id": "проект",
    "node_key": "нода",
    "step_code": "шаг",
    "label": "название",
    "title": "название",
    "name": "название",
    "model": "модель",
    "model_id": "модель",
    "model_label": "модель",
    "price_ru": "цена",
    "cost_usd": "стоимость, $",
    "spent_usd": "потрачено, $",
    "estimate_usd": "оценка, $",
    "credits": "кредиты",
    "error": "ошибка",
    "reason": "причина",
    "path": "файл",
    "file": "файл",
    "url": "ссылка",
    "enabled": "включено",
    "disabled": "выключено",
    "progress": "прогресс",
    "updated_at": "обновлено",
    "created_at": "создано",
    "duration_s": "длительность, с",
    "kind": "вид",
    "mode": "режим",
    "current": "сейчас",
    "options": "варианты",
    "applies": "действует",
}
_SKIP_FIELDS = frozenset(
    {
        "undo",
        "text_ru",
        "summary_ru",
        "message",
        "summary",
        "apply_step",
        "diff",
        "markdown",
        "raw",
        "payload",
        "headers",
    }
)
_SECRET_PARTS = ("sha", "digest", "hash", "token", "secret", "password", "api_key", "fingerprint")
_KEEP_IDS = frozenset({"project_id", "model_id"})
_HEXISH = re.compile(r"^[0-9a-f-]{16,}$", re.IGNORECASE)
_FIELDS_LIMIT = 12
_VALUE_LIMIT = 160


def _field_name(key: str) -> str:
    return _FIELD_RU.get(key) or key.replace("_", " ").strip()


def _scalar_ru(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        text = " ".join(value.split())
        if not text or _HEXISH.match(text):
            return ""
        return text if len(text) <= _VALUE_LIMIT else text[: _VALUE_LIMIT - 1].rstrip() + "…"
    return ""


def _value_ru(value: Any, depth: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return "нет"
        flat = [_scalar_ru(item) for item in value if not isinstance(item, (dict, list))]
        flat = [item for item in flat if item]
        if len(flat) == len(value) and len(value) <= 6:
            return ", ".join(flat)
        return f"{len(value)} шт."
    if isinstance(value, dict):
        if depth > 0:
            return ""
        inner = _pairs(value, depth + 1, 4)
        return "(" + ", ".join(inner) + ")" if inner else ""
    return _scalar_ru(value)


def _pairs(body: dict[str, Any], depth: int, limit: int) -> list[str]:
    out: list[str] = []
    for key, value in body.items():
        name = str(key)
        low = name.lower()
        if name in _SKIP_FIELDS or any(part in low for part in _SECRET_PARTS):
            continue
        if (low == "id" or low.endswith(("_id", "_ids"))) and name not in _KEEP_IDS:
            continue
        text = _value_ru(value, depth)
        if text:
            out.append(f"{_field_name(name)}: {text}")
        if len(out) >= limit:
            break
    return out


def fields_ru(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    from app.services.redact import redact_text

    return redact_text("; ".join(_pairs(body, 0, _FIELDS_LIMIT)))


# Сам возврат тоже оставляет снимок, но «откатить откат» не предлагаем.
_REVERT_ACTIONS = frozenset(
    {
        "history.rollback",
        "node.revert_model",
        "node.revert_model_many",
        "node.revert_params",
        "node.revert_check_agent",
    }
)


def _undo_line(note: StepNote) -> str:
    from app.services.action_layer.chat_undo import undo_hint

    return "" if note.action in _REVERT_ACTIONS else undo_hint()


def _counted(note: StepNote) -> bool:
    return bool(note.project_id) and not note.skipped and not (note.ok and note.readonly)


def _many_projects(facts: TaskFacts) -> bool:
    """Задача правила или запускала два и больше проектов: итог нужен по каждому."""
    return len({note.project_id for note in facts.steps if _counted(note)}) >= 2


def _in_project(note: StepNote, many: bool) -> str:
    pid = note.project_id
    if not many or not pid or f"#{pid}" in f"{note.text} {note.error}":
        return ""
    return f" в проекте #{pid}"


def _project_tally(facts: TaskFacts) -> list[str]:
    """Итог по проектам: сколько готово, где не вышло, где исход неизвестен."""
    outcome: dict[int, str] = {}
    for note in facts.steps:
        if not _counted(note):
            continue
        pid = int(note.project_id or 0)
        if note.ok:
            outcome.setdefault(pid, "ok")
        elif "результат неизвестен" in note.error:
            outcome[pid] = "unknown"
        elif outcome.get(pid) != "unknown":
            outcome[pid] = "failed"
    if len(outcome) < 2:
        return []

    def ids(kind: str) -> str:
        return ", ".join(f"#{pid}" for pid in sorted(outcome) if outcome[pid] == kind)

    ok = sum(1 for kind in outcome.values() if kind == "ok")
    line = f"Итог по проектам: готово {ok} из {len(outcome)}"
    if ids("failed"):
        line += f"; не получилось: {ids('failed')}"
    if ids("unknown"):
        line += f"; результат неизвестен: {ids('unknown')}"
    return [line + "."]


def _superseded(notes: list[StepNote]) -> list[StepNote]:
    """Правку промта подготовили и тут же применили: итог — применение, без «промт пока не изменён»."""
    if not any(note.action == "change.apply" and note.ok for note in notes):
        return notes
    return [note for note in notes if not (note.action == "prompt.edit_text" and note.ok)]


def deterministic_text(facts: TaskFacts) -> str:
    done = _superseded([note for note in facts.steps if note.ok and not note.skipped])
    failed = [note for note in facts.steps if not note.ok and not note.skipped]
    many = _many_projects(facts)
    lines: list[str] = []
    if facts.status == "done":
        changes = [note for note in done if not note.readonly]
        if changes:
            # Чтения перед правкой — проверки состояния «до»: после правки они уже устарели.
            if len(changes) == 1 and changes[0].known and changes[0].text:
                lines.append(changes[0].text)
            else:
                lines.append("Готово. Что сделано:")
                lines.extend(f"• {_change_line(note, many)}" for note in changes)
            undo = [note for note in changes if note.undo]
            lines.extend(dict.fromkeys(line for line in map(_undo_line, undo) if line))
            if not undo and not all(note.known for note in changes):
                lines.append(
                    "Снимка для автоматического отката у этих шагов нет: вернуть можно только "
                    "повторной правкой."
                )
        else:
            found = [note for note in done if note.text]
            if len(found) == 1 and found[0].known:
                # Отчёт и подобные шаги сами пишут итог с числами и ссылками: его и показываем.
                lines.append(found[0].text)
            elif found:
                lines.append("Вот что нашёл:")
                lines.extend(f"• {note.title}: {note.text}" for note in found)
            else:
                lines.append("Готово, но данных для ответа шаги не вернули.")
        for note in failed:
            lines.append(
                f"Не получилось «{note.title}»{_in_project(note, many)}: {note.error or 'без пояснения'}."
            )
        lines.extend(_project_tally(facts))
        return "\n".join(lines)
    if facts.error == APPROVAL_EXPIRED:
        if not facts.detail:
            return APPROVAL_EXPIRED_TEXT
        return (
            "Подтверждение не дали вовремя — задача отменена, шаг, который ждал "
            "подтверждения, не выполнялся. " + facts.detail
        )
    if facts.error == APPROVAL_STALE:
        return APPROVAL_STALE_TEXT
    if facts.error == "rejected" and facts.status in {"failed", "cancelled"}:
        changed = [note for note in done if not note.readonly]
        if not changed:
            return "Отменено по вашему решению. Ничего не изменено."
        return (
            "Отменено по вашему решению. До отмены успели выполнить: "
            + ", ".join(f"«{note.title}»" for note in changed)
            + "."
        )
    if facts.error == "empty_plan" and facts.status == "cancelled":
        reason, head, note = (facts.detail or "").partition(_WAITING_HEAD)
        reason = reason.strip()
        reason = reason if reason and not raw_reason(reason) else ""
        if facts.answered and reason:
            return "\n".join(line for line in (reason, (head + note).strip()) if line)
        # Причина с готовым примером уже говорит, что написать: вторая подсказка лишняя.
        hint = "" if "например" in reason else NOT_UNDERSTOOD_HINT
        return "\n".join(line for line in (NOT_UNDERSTOOD, reason, hint, (head + note).strip()) if line)
    if facts.status == "cancelled":
        lines.append("Задача остановлена.")
        if done:
            lines.append("До остановки выполнено: " + ", ".join(f"«{note.title}»" for note in done) + ".")
        return "\n".join(lines)
    what, todo = _REASON_RU.get(
        str(facts.error or ""), ("задача не выполнена", "Повторите просьбу или уточните её.")
    )
    if "потолок трат" in (facts.detail or ""):
        # Повтор «чуть позже» не поможет: лимит считается за сутки.
        what = "упёрлись в лимит трат"
        todo = "Поднимите лимит трат в настройках или повторите завтра, когда начнётся новый день."
    lines.append(f"Не получилось: {what}.")
    if facts.detail:
        lines.append(facts.detail)
    for note in failed:
        lines.append(f"Шаг «{note.title}»{_in_project(note, many)}: {note.error or 'ошибка без пояснения'}.")
    # Чтения состояния перед шагом ничего не меняли: в «успели» только правки.
    changed = [note for note in done if not note.readonly]
    if changed:
        lines.append(
            "Успели выполнить: "
            + ", ".join(f"«{note.title}»{_in_project(note, many)}" for note in changed)
            + "."
        )
    lines.extend(_project_tally(facts))
    lines.append("Что можно сделать: " + todo)
    return "\n".join(lines)


# Повтор просьбы повелительным глаголом («Скачай Excel-отчёт…», «Откати промт в проекте 7.»)
# вместо итога: такие предложения из ответа модели убираем.
_IMPERATIVE = re.compile(
    r"^[\s*_>#•\-–—]*(?:скачай|покажи|откати|сформируй|выгрузи|сделай|создай|запусти|сравни|верни|поменяй|смени"
    r"|поставь|дай|пришли|выведи|собери|подготовь|проверь|найди|посчитай|составь|построй|отчитайся"
    r"|расскажи|напиши|открой|экспортируй|выдай|скинь|отправь|обнови|останови|продолжи|откатись"
    r"|сгенерируй|прогони|перезапусти)(?![а-яё])",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)")
_LINK_LINE = re.compile(r"^\s*\[[^\]]+\]\([^)]+\)\s*$")


def _first_word(text: str) -> str:
    # Начало в кавычках («запусти …») — готовая команда для чата, а не пересказ просьбы.
    match = re.match(r"[\s*_>#•\-–—]*([а-яёa-z-]+)", (text or "").lower().replace("ё", "е"))
    return match.group(1) if match else ""


# Совет о следующем шаге («Запусти его, когда будешь готов: напиши «запусти проект 15».»,
# «Открой отчёт по ссылке выше.») — не пересказ просьбы, его оставляем.
_ADVICE = re.compile(
    r"«[^»]{3,}»|(?<![а-яё])(?:когда|если|чтобы|потом|затем|после\s+этого)(?![а-яё])"
    r"|по\s+ссылк|ссылк\w*\s+(?:выше|ниже)|(?<![а-яё])(?:выше|ниже)(?![а-яё])",
    re.IGNORECASE,
)


def _echo_sentence(sentence: str, goal: str) -> bool:
    if _ADVICE.search(sentence):
        return False
    if _IMPERATIVE.match(sentence):
        return True
    word, asked = _first_word(sentence), _first_word(goal)
    # «Выгрузи HTML-отчёт…» на просьбу «выгрузи в html…»: тот же повелительный глагол, что в просьбе.
    return bool(word) and word == asked and asked.endswith(("й", "и", "ь"))


def drop_echo(text: str, goal: str) -> str:
    """Убирает предложения, которые пересказывают просьбу как приказ; пусто — итога не осталось."""
    out: list[str] = []
    for line in (text or "").splitlines():
        if _LINK_LINE.match(line) or line.lstrip().startswith("|") or "](" in line:
            out.append(line)
            continue
        kept = [part for part in _SENTENCE.split(line) if not _echo_sentence(part, goal)]
        if kept or not line.strip():
            out.append(" ".join(kept))
    body = "\n".join(out).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Строка только со ссылками («Скачать отчёт: [HTML](…)») — не итог: без итога берём текст кода.
    meaningful = [line for line in body.splitlines() if line.strip() and "](" not in line]
    return body if meaningful else ""


# «Текст промта «X» (системный):\n…\n(показаны первые 1500 из 5647 знаков)» из node.describe.
_PROMPT_HEAD = re.compile(r"^(Текст промта[^\n]*):[ \t]*\n", re.MULTILINE)
_PROMPT_TAIL = re.compile(r"\n\(показаны (?:первые|последние) (\d+) из (\d+) знаков\)\s*$")
_PROMPT_PROBE = 60
# Сколько разных промтов ответ цитирует и сколько знаков каждого: остальные - «спросите отдельно».
_PROMPT_QUOTES_MAX = 2
_PROMPT_QUOTE_CHARS = 1500
PROMPT_QUOTE_RULE = (
    "Пользователь просил показать промт, и в фактах есть его текст («Текст промта …»). Покажи его "
    "цитатой дословно, каждую строку с «> », не пересказывай и не сокращай показанный текст. "
    f"Цитируй не больше {_PROMPT_QUOTES_MAX} разных промтов, не длиннее {_PROMPT_QUOTE_CHARS} знаков "
    "каждый, одинаковый текст - один раз. Если показана только часть, скажи, сколько знаков показано "
    "из скольких. Если промтов больше, про остальные напиши «ещё N промтов — спросите отдельно»."
)
# Просьба показать промт, а не поменять что-то в ноде.
_PROMPT_WORD = re.compile(r"промп?т|prompt", re.IGNORECASE)
_SHOW_WORDS = re.compile(
    r"покаж|показа|выведи|вывест|прочитай|прочти|процитир|цитат|скинь|пришли|открой|посмотр|глян"
    r"|каки[ей]|каков|что\s+(?:за|в|у|написан|стоит)|текст|\bshow\b",
    re.IGNORECASE,
)
_CHANGE_WORDS = re.compile(
    r"смени|поменя|замени|измени|постав|установ|переключ|исправ|перепиш|отредактир|правь|убери|удали"
    r"|добав|запуст",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptExcerpt:
    head: str
    body: str
    tail: str


def wants_prompt_text(facts: TaskFacts) -> bool:
    """Ответ цитирует промт, только если просили его показать.

    Смена модели или параметров ноды тоже читает ноду через node.describe, и текст промта
    попадает в факты; задача, которая что-то меняла, промт не цитирует. Без слова «промт» в
    просьбе - тоже нет. Просьба со словом «покажи/какой/текст» - да; без такого слова - только
    если вся задача состояла из node.describe и в просьбе нет слов правки.
    """
    if any(note.ok and not note.readonly and not note.skipped for note in facts.steps):
        return False
    goal = str(facts.goal or "")
    if not _PROMPT_WORD.search(goal):
        return False
    if _SHOW_WORDS.search(goal):
        return True
    if _CHANGE_WORDS.search(goal):
        return False
    done = [note for note in facts.steps if note.ok and not note.skipped]
    return bool(done) and all(note.action == "node.describe" for note in done)


def _parse_prompt(text: str) -> tuple[str, str, str, int] | None:
    """(заголовок, текст, хвост «показаны первые …», всего знаков) из ответа node.describe."""
    found = _PROMPT_HEAD.search(text or "")
    if found is None:
        return None
    rest = text[found.end() :]
    tail = _PROMPT_TAIL.search(rest)
    body = (rest[: tail.start()] if tail else rest).strip()
    if not body:
        return None
    total = int(tail.group(2)) if tail else len(body)
    return found.group(1), body, tail.group(0).strip() if tail else "", total


def _clip_excerpt(head: str, body: str, tail: str, total: int) -> PromptExcerpt:
    if len(body) <= _PROMPT_QUOTE_CHARS:
        return PromptExcerpt(head, body, tail)
    if "последние" in tail:
        # «Покажи конец промта»: режем с начала, конец остаётся.
        cut = body[-_PROMPT_QUOTE_CHARS:]
        line_start = cut.find("\n")
        if 0 <= line_start < _PROMPT_QUOTE_CHARS // 2:
            cut = cut[line_start + 1 :]
        cut = cut.strip()
        return PromptExcerpt(head, cut, f"(показаны последние {len(cut)} из {max(total, len(body))} знаков)")
    cut = body[:_PROMPT_QUOTE_CHARS]
    line_end = cut.rfind("\n")
    if line_end >= _PROMPT_QUOTE_CHARS // 2:
        cut = cut[:line_end]
    cut = cut.rstrip()
    return PromptExcerpt(head, cut, f"(показаны первые {len(cut)} из {max(total, len(body))} знаков)")


def prompt_quotes(facts: TaskFacts) -> tuple[list[PromptExcerpt], int]:
    """Промты, которые ответ покажет цитатой, и сколько разных промтов осталось за кадром.

    Одинаковые тексты идут один раз, не больше ``_PROMPT_QUOTES_MAX`` промтов и не больше
    ``_PROMPT_QUOTE_CHARS`` знаков каждого.
    """
    if not wants_prompt_text(facts):
        return [], 0
    distinct: list[tuple[str, str, str, int]] = []
    seen: set[str] = set()
    for note in facts.steps:
        if note.action != "node.describe" or not note.ok or not note.text:
            continue
        parsed = _parse_prompt(note.text)
        if parsed is None or parsed[1] in seen:
            continue
        seen.add(parsed[1])
        distinct.append(parsed)
    shown = [_clip_excerpt(*item) for item in distinct[:_PROMPT_QUOTES_MAX]]
    return shown, max(0, len(distinct) - _PROMPT_QUOTES_MAX)


def prompt_excerpts(facts: TaskFacts) -> list[PromptExcerpt]:
    """Тексты промтов, которые ответ показывает цитатой, а не пересказывает (см. ``prompt_quotes``)."""
    return prompt_quotes(facts)[0]


def more_prompts_note(more: int) -> str:
    return f"Ещё {more} {_plural(more, 'промт', 'промта', 'промтов')} — спросите отдельно."


def _squash(text: str) -> str:
    return re.sub(r"[\s>*_`#«»\"]+", "", text).lower()


def ensure_prompt_quoted(text: str, facts: TaskFacts) -> str:
    """Модель пересказала промт вместо цитаты: дописываем показанный текст цитатой сами."""
    items, more = prompt_quotes(facts)
    flat = _squash(text)
    extra: list[str] = []
    for item in items:
        probe = _squash(item.body)[:_PROMPT_PROBE]
        if probe and probe in flat:
            continue
        quoted = "\n".join("> " + line if line.strip() else ">" for line in item.body.splitlines())
        extra.append("\n".join(part for part in (f"{item.head}:", quoted, item.tail) if part))
    if more and "спросите отдельно" not in text.lower():
        extra.append(more_prompts_note(more))
    return "\n\n".join([text, *extra]) if extra else text


def _fact_text(note: StepNote, quotes: list[PromptExcerpt], shown: set[str]) -> str:
    """Текст шага для модели: промт - только тот, что ответ цитирует, и один раз."""
    if note.action != "node.describe" or not note.ok or not note.text:
        return note.text
    found = _PROMPT_HEAD.search(note.text)
    parsed = _parse_prompt(note.text)
    if found is None or parsed is None:
        return note.text
    head, body = parsed[0], parsed[1]
    item = next((q for q in quotes if body.startswith(q.body) or body.endswith(q.body)), None)
    before = note.text[: found.start()]
    if item is None:
        return f"{before}{head}: текст не показан{'' if quotes else ' (о нём не спрашивали)'}"
    if item.body in shown:
        return f"{before}{head}: тот же текст, что выше"
    shown.add(item.body)
    return "\n".join(part for part in (f"{before}{head}:", item.body, item.tail) if part)


def _facts_prompt(facts: TaskFacts) -> list[dict[str, str]]:
    rules = list(ANSWER_RULES)
    quotes, more = prompt_quotes(facts)
    if quotes:
        rules.append(PROMPT_QUOTE_RULE)
    if more:
        rules.append(f"Не все промты вошли в цитату: в конце ответа напиши «{more_prompts_note(more)}».")
    shown: set[str] = set()
    rules.append("Это итог задачи из чата: перескажи найденное по фактам и ответь на просьбу пользователя.")
    rules.append(
        "Пиши итог тем, что сделано и что получилось, в прошедшем времени и с числами, например "
        "«Отчёт по проекту #5 готов: 18 нод, 3 готовы». Просьбу пользователя не повторяй и не "
        "начинай с повелительного глагола («скачай», «покажи», «сформируй»)."
    )
    used = 0
    lines = ["Факты, прочитанные шагами задачи:"]
    for note in facts.steps[:_STEP_LIMIT]:
        body = (
            _fact_text(note, quotes, shown)
            if note.ok and note.text
            else ("прочитать не удалось: " + note.error if note.error else "пусто")
        )
        chunk = f"[{note.title}] {body}"
        if used + len(chunk) > _TOTAL_LIMIT:
            break
        used += len(chunk)
        lines.append(chunk)
    return [
        {"role": "system", "content": "\n".join(rules + lines)},
        {"role": "user", "content": facts.goal or "что с проектом?"},
    ]


class TaskSummarizer:
    def __init__(self, router: Any = None, *, timeout_s: float = _TIMEOUT_S) -> None:
        self.router = router
        self.timeout_s = timeout_s
        self.last_response: Any = None

    def _wants_model(self, facts: TaskFacts) -> bool:
        if self.router is None or facts.status != "done":
            return False
        done = [note for note in facts.steps if note.ok and not note.skipped]
        if done and all(note.action in VERBATIM_ACTIONS and note.known for note in done):
            # Таблицу отчёта код уже собрал: модель её пересказывала или повторяла просьбу.
            return False
        if len(done) == 1 and done[0].known and _simple_read(facts.goal, done[0].action):
            # «Покажи все проекты»: текст шага и есть ответ, пересказ моделью стоил ~20 с.
            return False
        if any(not note.readonly for note in facts.steps if note.ok):
            return False
        return any(note.ok and note.text for note in facts.steps)

    async def summarize(self, facts: TaskFacts, *, run_id: str, task_id: Any = None) -> str:
        fallback = deterministic_text(facts)
        if not self._wants_model(facts):
            return fallback
        from app.orchestrator.agents.llm_call import router_call
        from app.orchestrator.contracts import ORCH_REASONING_EFFORT, AgentRole, BudgetScope

        try:
            response = await asyncio.wait_for(
                router_call(
                    self.router,
                    "strong",
                    _facts_prompt(facts),
                    stream=False,
                    run_id=run_id,
                    task_id=task_id,
                    agent_role=AgentRole.summarizer.value,
                    budget_scope=BudgetScope.internal.value,
                    reasoning_effort=ORCH_REASONING_EFFORT,
                ),
                self.timeout_s,
            )
        except Exception as exc:
            logger.warning("orch summary model failed: {}", type(exc).__name__)
            note = explain_failure(exc)
            return fallback + ("\n" + note if "Модель не вызвана" in note else "")
        self.last_response = response
        if getattr(response, "approval", None) is not None:
            return fallback
        text = str(getattr(response, "text", "") or "").strip()
        text = normalize_links(text)
        cleaned = drop_echo(text, facts.goal)
        if cleaned != text:
            logger.info("orch summary echoed the request, kept {} of {} chars", len(cleaned), len(text))
        text = cleaned
        if not text:
            return fallback
        text = ensure_prompt_quoted(text, facts)
        # Модель могла уже вставить ссылку со своей подписью: сравниваем по адресу, не по строке.
        links = [
            link
            for note in facts.steps
            for link in note.links
            if link.rsplit("](", 1)[-1].rstrip(")") not in text
        ]
        return "\n".join([text, *dict.fromkeys(links)])


def has_prior_text(session_id: str, task_id: int) -> bool:
    from sqlalchemy import text

    from app.orchestrator.db.migrations import connect

    conn = connect()
    try:
        row = conn.execute(
            text(
                "SELECT 1 FROM orch_events WHERE session_id = :sid AND task_id = :task_id"
                " AND type IN ('message', 'token') LIMIT 1"
            ),
            {"sid": session_id, "task_id": int(task_id)},
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def store_message(session_id: str, task_id: int, text: str) -> None:
    from app.orchestrator.queue import _insert_message

    _insert_message(session_id, "assistant", text, task_id=task_id, mode="task")


def payload(task_id: int, text: str, status: str) -> dict[str, Any]:
    return {"role": "assistant", "text": text, "kind": "task_summary", "task_id": task_id, "status": status}
