"""Запуск нескольких проектов — всегда очередь генерации, а не шаги по проектам.

«Запусти 5 проектов параллельно до картинок: 5 6 7 8 9» планировщик собирал как
queue.set_parallel + queue.enqueue_many, а «запусти проекты 3,4,5,…,12 одновременно» — как
десять step.run_step со своими шагами. Карточка таких шагов требовала потолок трат на каждый
проект и не открывалась. Здесь просьба запустить 2+ названных проекта без названного шага
переводится в очередь: queue.enqueue_many с этими проектами (и queue.set_parallel, если
проектов больше, чем окно параллели, или человек сказал «одновременно» / «параллельно»).
«Запусти шаг озвучки в проектах 5 и 6» не трогаем: там назван шаг.

Только повелительная просьба («запусти», «прогони», «стартуй»): «покажи статус запуска
проектов 3 и 4» — чтение, а не запуск. Деньги («с потолком 100 рублей»), «2 из проектов
5,6,7», «до 3 сцены» — не очередь: там условие, которого у очереди нет. Числа потоков
(«в 2 потока») и формата («9:16») — не номера проектов. «По очереди» — без окна параллели.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.orchestrator.contracts import PlanStep
from app.services.runtime_streams import WORKER_MAX

MAX_PROJECTS = 20
# Потолок окна параллели — тот же, что у схемы queue.set_parallel (builtin_launch.MAX_PARALLEL):
# больше — схема отказала бы «above N», и карточка всего массового запуска не открылась бы.
MAX_PARALLEL = WORKER_MAX

# Только повелительные формы: «запуск», «прогон», «старт» — существительные, это чтение.
_LAUNCH = re.compile(
    r"(?<![а-яa-z])(?:запусти(?:те)?|запускай(?:те)?|запустить|прогони(?:те)?|прогнать|"
    r"погони(?:те)?|стартуй(?:те)?|стартани(?:те)?|run|start)(?![а-яa-z])"
)
# Один за другим: окно параллели не трогаем.
_SEQUENTIAL = re.compile(
    r"по очереди|по одному|друг за другом|один за другим|последовательн|"
    r"(?<![а-яa-z])(?:потом|затем|после)(?![а-яa-z])"
)
# Потолок трат: у очереди его нет, такую просьбу не переводим в очередь.
_MONEY = re.compile(
    r"(?<![а-яa-z])(?:потол\w*|лимит\w*|бюджет\w*|руб\w*|доллар\w*|бакс\w*|евро|цент\w*|"
    r"usd|rub|не дороже|не больше)(?![а-яa-z])|[$₽€]|\d\s*р(?![а-яa-z])"
)
# «2 из проектов 5,6,7» — часть списка, а не весь список.
_SOME_OF = re.compile(r"(?<!\d)\d{1,3}\s+из(?![а-яa-z])")
# «в 2 потока», «по 3 за раз», «3 воркера»: число потоков.
_WORKERS = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:поток\w*|воркер\w*|worker\w*)|"
    r"(?<![а-яa-z])по\s+(\d{1,2})\s+(?:за раз|одновременно|параллельно)"
)
# «9:16», «16x9», «16/9» — формат кадра.
_FORMAT = re.compile(r"(?<!\d)\d{1,2}\s*[:x×х/]\s*\d{1,2}(?!\d)")
_PARALLEL = re.compile(r"одновремен|параллел|разом|все сразу|сразу все")
# Шаг, нода или «следующий шаг»: это запуск шага, а не всего конвейера.
_STEP_WORDS = re.compile(
    r"(?<![а-яa-z])(?:шаг\w*|нод\w*|сценари\w*|закадр\w*|текст\w*|разбивк\w*|объект\w*|"
    r"предмет\w*|персонаж\w*|геро\w*|озвучк\w*|голос\w*|музык\w*|звук\w*|сборк\w*|"
    r"монтаж\w*|картин\w*|изображен\w*|видео\w*|анимац\w*|промт\w*|промпт\w*|"
    r"следующ\w*|сцен\w*|gpt|гпт)(?![а-яa-z])"
)
_QUESTION = re.compile(r"^(?:как|можно|могу|почему|зачем|сколько|что|когда|где)\b|\?\s*$")
_NOT = re.compile(r"(?<![а-яa-z])(?:не|нельзя|останови\w*|стоп|отмени\w*|убери\w*)(?![а-яa-z])")
# «до картинок», «до промтов картинок», «до конца»: цель очереди, а не шаг.
_UNTIL = re.compile(r"(?<![а-яa-z])до\s+([а-яa-z]+(?:\s+[а-яa-z]+)?)")
_FULL_WORDS = ("конц", "финал", "готов", "результат")
_TARGETS: tuple[tuple[str, str], ...] = (
    ("промт\\w*\\s+(?:картин|изображ)", "image_prompts"),
    ("промпт\\w*\\s+(?:картин|изображ)", "image_prompts"),
    ("промт\\w*\\s+(?:видео|анимац)", "animation_prompts"),
    ("промпт\\w*\\s+(?:видео|анимац)", "animation_prompts"),
    ("картин|изображ|фото|кадр", "images"),
    ("анимац", "animation_prompts"),
    ("видео|ролик", "videos"),
    ("озвучк|голос", "audio"),
    ("музык", "music"),
    ("звук", "sfx_gen"),
    ("сборк|монтаж|склейк", "assemble"),
    ("персонаж|геро", "hero"),
    ("предмет", "items"),
    ("разбивк", "split"),
    ("закадр|текст", "script"),
    ("сценари|план", "plan"),
)
# Тип ноды цели очереди: планировщик пишет «img», «video», граф знает «images», «videos».
_TYPE_FIX = {
    "img": "images",
    "image": "images",
    "picture": "images",
    "pictures": "images",
    "video": "videos",
    "vid": "videos",
    "voice": "audio",
    "sound": "sfx_gen",
    "sfx": "sfx_gen",
    "assembly": "assemble",
    "montage": "assemble",
    "heroes": "hero",
}
LAUNCH_STEPS = frozenset({"step.run_step", "node.run_step"})
QUEUE_STEPS = frozenset({"queue.enqueue_many", "queue.set_parallel"})
# Чтения перед запуском: в очереди воркер сам проверяет каждый проект.
_READS = frozenset(
    {
        "project.status",
        "node.describe",
        "montage.get_board",
        "step.get_run_cost_basis",
        "project.get",
        "projects.overview",
    }
)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


@dataclass(frozen=True)
class LaunchAsk:
    project_ids: list[int]
    parallel: bool
    target: str | None  # тип ноды цели; None — весь конвейер
    sequential: bool = False  # «по очереди», «потом»: окно параллели не трогать
    workers: int | None = None  # «в 2 потока»


def _ids(text: str) -> list[int]:
    """Номера проектов: «3,4,5», «3 4 5», «3-12», «с 3 по 12», «#5». «5 проектов» — это счёт."""
    body = re.sub(r"(?<!\d)\d{1,3}\s+проект\w*", " ", text)  # «запусти 5 проектов» — сколько, а не какой
    body = _FORMAT.sub(" ", body)
    body = _WORKERS.sub(" ", body)
    body = re.sub(r"(?<![а-яa-z])до\s+\d+\S*", " ", body)
    out: list[int] = []

    def add(value: int) -> None:
        if 0 < value <= 99999 and value not in out:
            out.append(value)

    ranges = re.compile(r"(?:с\s+)?#?(\d{1,5})\s*(?:-|–|—|\.\.\.?|…|по)\s*#?(\d{1,5})(?!\s*[kкpр%$])")
    spans: list[tuple[int, int]] = []
    for match in ranges.finditer(body):
        low, high = int(match.group(1)), int(match.group(2))
        if low < high and high - low < MAX_PROJECTS:
            for value in range(low, high + 1):
                add(value)
            spans.append(match.span())
    rest = body
    for start, end in reversed(spans):
        rest = rest[:start] + " " + rest[end:]
    for match in re.finditer(r"(?<![\w.$])#?(\d{1,5})(?![\w.%$:])", rest):
        add(int(match.group(1)))
    return out


def _target(text: str) -> str | None:
    for match in _UNTIL.finditer(text):
        phrase = match.group(1)
        if any(phrase.startswith(word) for word in _FULL_WORDS):
            return None
        for pattern, node_type in _TARGETS:
            if re.match(pattern, phrase):
                return node_type
    return None


def launch_ask(goal: str) -> LaunchAsk | None:
    """Просьба запустить 2+ названных проекта целиком или до цели, без названного шага."""
    norm = _norm(goal)
    if not norm or _QUESTION.search(norm) or not _LAUNCH.search(norm) or _NOT.search(norm):
        return None
    if _MONEY.search(norm) or _SOME_OF.search(norm):
        return None
    ids = _ids(norm)
    if len(ids) < 2 or len(ids) > MAX_PROJECTS:
        return None
    without_until = _UNTIL.sub(" ", norm)
    if _STEP_WORDS.search(without_until):
        return None
    sequential = bool(_SEQUENTIAL.search(norm))
    found = _WORKERS.search(norm)
    workers = min(max(int(found.group(1) or found.group(2)), 1), MAX_PARALLEL) if found else None
    return LaunchAsk(
        project_ids=ids,
        parallel=bool(_PARALLEL.search(norm)) and not sequential,
        target=_target(norm),
        sequential=sequential,
        workers=workers,
    )


def _fixed_type(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    return _TYPE_FIX.get(raw, raw)


def fix_queue_target(plan: list[PlanStep]) -> list[PlanStep]:
    """«img» в target_node_type очереди — «images»: иначе очередь откажет «invalid target node type»."""
    out = []
    for step in plan:
        typ = step.params.get("target_node_type") if step.action == "queue.enqueue_many" else None
        fixed = _fixed_type(typ)
        if typ and fixed != typ:
            step = step.model_copy(update={"params": {**step.params, "target_node_type": fixed}})
        out.append(step)
    return out


def _queue_step(ask: LaunchAsk, drafted: PlanStep | None) -> PlanStep:
    params: dict[str, Any] = {"project_ids": list(ask.project_ids), "enable_auto_mode": True}
    if ask.target:
        params.update(mode="until_node", target_node_type=ask.target)
    elif drafted is not None and drafted.params.get("mode") == "until_node":
        for key in ("mode", "target_node_key", "target_node_type"):
            if drafted.params.get(key):
                params[key] = drafted.params[key]
        if params.get("target_node_type"):
            params["target_node_type"] = _fixed_type(params["target_node_type"])
    else:
        params["mode"] = "full"
    count = len(ask.project_ids)
    return PlanStep(
        action="queue.enqueue_many",
        params=params,
        mutating=True,
        risk="high",
        expected=f"Проекты {', '.join(f'#{p}' for p in ask.project_ids)} стоят в очереди "
        f"генерации ({count}).",
    )


def parallel_refused_note(limit: int | None = None) -> str:
    """Участнику окно параллели не меняем: оно одно на всю студию (``plan.ADMIN_ONLY_ACTIONS``).

    Само число окна участнику не называем — это настройка всей студии, как и в
    ``plan.PARALLEL_ADMIN_ONLY_MESSAGE``; ``limit`` оставлен для совместимости вызовов."""
    del limit
    return (
        "Сколько проектов идут одновременно — общая настройка студии, её меняет только админ. "
        "Ваши проекты встанут в очередь и пойдут в пределах текущего числа."
    )


def route_multi_launch(
    plan: list[PlanStep],
    goal: str,
    limit: int | None,
    allowed: Callable[[str], bool] | None = None,
    notes: list[str] | None = None,
) -> list[PlanStep]:
    """План запуска нескольких проектов — очередь (и окно параллели); другие планы как есть.

    ``limit`` — сколько проектов очереди сейчас идут одновременно (None — неизвестно).
    ``allowed`` — есть ли действие в каталоге чата: без очереди в каталоге план не трогаем.
    Без ``queue.set_parallel`` (участник студии: окно параллели одно на всю студию) шага
    параллели нет, а в ``notes`` — понятная строка для карточки вместо него.
    План с чем-то кроме запусков, очереди и чтений (правка настроек, создание) не трогаем.
    Пустой план и план из одних чтений — тоже: это готовый ответ или вопрос, не запуск.
    """
    ask = launch_ask(goal)
    if ask is None or (allowed is not None and not allowed("queue.enqueue_many")):
        return fix_queue_target(plan)
    if any(step.action not in LAUNCH_STEPS | QUEUE_STEPS | _READS for step in plan):
        return fix_queue_target(plan)
    if not any(step.action in LAUNCH_STEPS | QUEUE_STEPS for step in plan):
        return fix_queue_target(plan)
    drafted = next((step for step in plan if step.action == "queue.enqueue_many"), None)
    out: list[PlanStep] = []
    need_parallel, workers = _parallel_need(ask, len(ask.project_ids), limit)
    if need_parallel and allowed is not None and not allowed("queue.set_parallel"):
        need_parallel = False
        if notes is not None:
            notes.append(parallel_refused_note(limit))
    if need_parallel:
        out.append(_parallel_step(workers))
    out.append(_queue_step(ask, drafted))
    return out


def _parallel_need(ask: LaunchAsk, count: int, limit: int | None) -> tuple[bool, int]:
    """Нужно ли трогать окно параллели очереди и до скольких: одно правило для маршрута и подрезки."""
    if ask.sequential:
        return False, 0
    if ask.workers is not None:
        return limit is None or ask.workers != limit, ask.workers
    workers = min(count, MAX_PARALLEL)
    # Окно уже на потолке (или шире, чем нужно): поднять его нельзя — и шага нет, и строки
    # участнику «меняет только админ» тоже: админ шире потолка не поставит.
    return (limit is not None and workers > limit) or (limit is None and ask.parallel), workers


def _parallel_step(workers: int) -> PlanStep:
    return PlanStep(
        action="queue.set_parallel",
        params={"workers": workers},
        mutating=True,
        risk="medium",
        expected=f"Одновременно идут до {workers} проектов очереди.",
    )


def parallel_needed(goal: str, limit: int | None, count: int) -> bool:
    """Нужна ли смена окна параллели для ``count`` проектов (то же правило, что у маршрута)."""
    ask = launch_ask(goal)
    return ask is not None and _parallel_need(ask, count, limit)[0]


def refit_parallel(plan: list[PlanStep], goal: str, limit: int | None, count: int) -> list[PlanStep]:
    """Очередь стала короче (часть проектов не прошла условие запуска): окно параллели — по
    оставшимся ``count`` проектам тем же правилом, что в ``route_multi_launch``. Не нужно больше —
    шаг ``queue.set_parallel`` уходит из плана; просьба «в N потоков» остаётся как есть."""
    ask = launch_ask(goal)
    if ask is None or not any(step.action == "queue.set_parallel" for step in plan):
        return plan
    need, workers = _parallel_need(ask, count, limit)
    out: list[PlanStep] = []
    for step in plan:
        if step.action != "queue.set_parallel":
            out.append(step)
        elif need:
            fresh = _parallel_step(workers)
            out.append(
                step.model_copy(
                    update={"params": {**step.params, "workers": workers}, "expected": fresh.expected}
                )
            )
    return out
