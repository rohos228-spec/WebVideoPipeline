"""«Верни как было» из чата: откат последней обратимой правки, сделанной через чат.

Правку чата узнаём по снимку слоя, чей план создала задача этой беседы (оператор
``orchestrator:task-N``, задачи беседы — по её session_id). Откат идёт обычным history.rollback со всеми его проверками и
карточкой подтверждения; уровень риска отката здесь не меняется.
Фразы-подсказки после правки берутся отсюда же, чтобы чат советовал только то,
что сам понимает.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from app.services.action_layer import store

CHAT_OPERATOR = "orchestrator%"
UNDO_PHRASE = "верни как было"
_MAX_ROWS = 50
_MAX_TASKS = 500
_MAX_CHECKED = 10
_MAX_LISTED = 6

_WORD = re.compile(r"[a-zа-я0-9]+")
_VERB = re.compile(
    r"^(?:откат\w*|откач\w*|верни|верните|вернуть|вернем|верну|отмени|отмените|отменить|"
    r"undo)$"
)
# «Поставь обратно», «восстанови прежнюю»: откат, только если сказано, что вернуть прежнее.
_SOFT_VERB = re.compile(r"^(?:постав\w*|сделай|восстанов\w*)$")
_BACK = re.compile(r"^(?:обратно|назад|прежн\w*|раньше|было)$")
# «отмени» без объекта — это «останови задачу», а не откат правки.
_BARE_UNDO = re.compile(r"^(?:откат\w*|undo)$")
# «Откатил», «вернул»: рассказ о сделанном, а не просьба.
_PAST = re.compile(r"^\w+(?:л|ла|ло|ли|лся|лась|лись)$")
# «Отмени задачу / генерацию / это действие» — остановить работу, а не вернуть правку.
_CANCEL_VERB = re.compile(r"^отмен\w*$")
_TASK_OBJECT = re.compile(
    r"^(?:задач\w*|команд\w*|операци\w*|действи\w*|генерац\w*|запуск\w*|"
    r"рендер\w*|карточк\w*|очеред\w*|проверк\w*)$"
)
_AGENT = re.compile(r"^агент\w*$")
# Вопрос («как вернуть прежнюю модель?», «ты откатил?») — ответить, а не откатывать.
_QUESTION_START = frozenset(
    {"как", "что", "почему", "зачем", "когда", "где", "кто", "какой", "какая", "какие", "какое", "сколько"}
)
_ASK_VERB = re.compile(
    r"^(?:верни|верните|вернем|откати|откатите|откатим|отмени|отмените|поставь|"
    r"поставьте|сделай|сделайте|восстанови|восстановите)$"
)
_OBJECT = re.compile(
    r"^(?:как|было|раньше|обратно|назад|прежн\w*|последн\w*|предыдущ\w*|прошл\w*|"
    r"стар\w*|правк\w*|изменени\w*|действи\w*|операци\w*|команд\w*|"
    r"снимок|снимк\w*|значени\w*)$"
)
# «Восстанови из снимка»: снимок — это откат, только если его восстанавливают («сделай снимок» — нет).
_SNAPSHOT = re.compile(r"^(?:снимок|снимк\w*)$")
_RESTORE = re.compile(r"^восстанов\w*$")
_FILLER = re.compile(
    r"^(?:в|во|на|у|для|с|со|из|к|по|и|а|же|что|только|это|эту|этот|его|ее|все|"
    r"пожалуйста|плиз|давай|мне|мою|мое|мой|мои|свою|свое|чат\w*|журнал\w*|"
    r"нод\w*|проект\w*|n|ctrl|z|"
    # «не, верни обратно», «блин, откати»: междометие перед просьбой, а не что вернуть.
    r"не|нет|неа|ну|ой|блин|слушай|короче|ладно|лучше|таки|все-таки|всетаки|тогда|опять|снова)$"
)
_KINDS = (
    (re.compile(r"^модел\w*$"), ("node_model",)),
    # prompt_version — правка текста промта, применённая через change control (chat_proposals).
    (re.compile(r"^(?:промт\w*|промпт\w*|prompt\w*)$"), ("prompt_slot", "master_prompt", "prompt_version")),
    (
        re.compile(r"^(?:настро\w*|параметр\w*)$"),
        (
            "node_params",
            "project_settings",
            "node_config",
            "assemble_params",
            "options_version",
            "step_params_version",
        ),
    ),
    # «Проверка» одна — это и «останови проверку»: агента узнаём только по слову «агент».
    (_AGENT, ("check_agent",)),
)
_PROJECTS = re.compile(r"проект\w*\s+((?:№?#?\d{1,5}(?:\s*(?:,|и)\s*)?)+)|#(\d{1,5})")
_KIND_RU = {
    "voiceover": "закадровый текст кадра",
    "coverage": "покрытие кадра",
    "cell_voiceover": "закадр сцены",
    "shot_field": "поле шота",
    "file_voice": "файл озвучки",
    "file_music": "файл музыки",
    "master_prompt": "общий промт шага",
    "prompt_slot": "промт ноды",
    "node_config": "настройки ноды",
    "node_disabled": "включение ноды",
    "assemble_params": "настройки сборки",
    "node_model": "модель ноды",
    "node_params": "настройки ноды",
    "project_settings": "настройки проекта",
    "check_agent": "агент проверки ноды",
    "prompt_version": "текст промта ноды",
    "options_version": "настройки ноды",
    "step_params_version": "параметры шага",
}
# «Первой работы с гпт»: так называют ноду «Работа с GPT 1».
_ALIASES = {"гпт": "gpt", "джипити": "gpt", "чатгпт": "gpt"}
_ORDINALS = (
    (re.compile(r"^перв\w*$"), "1"),
    (re.compile(r"^втор\w*$"), "2"),
    (re.compile(r"^трет\w*$"), "3"),
    (re.compile(r"^четверт\w*$"), "4"),
)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _tokens(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def named_projects(text: str) -> list[int]:
    """Номера проектов из «в проекте 6», «в проектах 5, 6 и 7», «#6»."""
    out: list[int] = []
    for group, single in _PROJECTS.findall(_norm(text)):
        for raw in re.findall(r"\d{1,5}", group or single):
            if int(raw) not in out:
                out.append(int(raw))
    return out


def _question(text: str, words: list[str]) -> bool:
    if words and words[0] in _QUESTION_START:
        return True
    return _norm(text).endswith("?") and not any(_ASK_VERB.match(word) for word in words)


_RECENT = re.compile(r"^(?:последн\w*|предыдущ\w*|прошл\w*)$")
_ACT = re.compile(r"^действи\w*$")


def _task_object(word: str, words: list[str]) -> bool:
    if not _TASK_OBJECT.match(word):
        return False
    # «Отмени последнее действие» — откат правки; при живой задаче очередь отдаёт это классификатору.
    if _ACT.match(word) and any(
        _RECENT.match(other) for other in words
    ):  # «команду», «операцию» — про задачу
        return False
    return not (word.startswith("проверк") and any(_AGENT.match(other) for other in words))


def undo_phrase(text: str) -> bool:
    """Просьба вернуть свою правку: «верни как было», «отмени последнее», «откати»."""
    words = _tokens(text)
    if _question(text, words):
        return False
    if "ctrl" in words and "z" in words:
        return True
    verbs = [word for word in words if _VERB.match(word) and not _PAST.match(word)]
    if not verbs:
        if any(_RESTORE.match(word) and not _PAST.match(word) for word in words) and any(
            _SNAPSHOT.match(word) for word in words
        ):
            return True
        return any(_SOFT_VERB.match(word) and not _PAST.match(word) for word in words) and any(
            _BACK.match(word) for word in words
        )
    if all(_CANCEL_VERB.match(word) for word in verbs) and any(_task_object(word, words) for word in words):
        return False
    if any(_OBJECT.match(word) or any(rx.match(word) for rx, _kinds in _KINDS) for word in words):
        return True
    return len(words) <= 3 and any(_BARE_UNDO.match(word) for word in verbs)


def kinds_of(text: str) -> set[str]:
    found: set[str] = set()
    for word in _tokens(text):
        for rx, kinds in _KINDS:
            if rx.match(word):
                found.update(kinds)
    return found


def _free_words(text: str) -> list[str]:
    """Слова, которые не объясняются самой просьбой об откате: вид правки, нода, название модели."""
    numbers = {str(pid) for pid in named_projects(text)}
    words = _tokens(text)
    agent = any(_AGENT.match(word) for word in words)
    out = []
    for word in words:
        if (
            _VERB.match(word)
            or _SOFT_VERB.match(word)
            or _OBJECT.match(word)
            or _FILLER.match(word)
            or word in numbers
            or (agent and word.startswith("проверк"))
        ):
            continue
        out.append(word)
    return out


# Подсказки после правки. Каждая фраза проходит undo_phrase и разбирается resolve.
def undo_hint() -> str:
    return f"Передумали — напишите «{UNDO_PHRASE}»."


def project_undo_phrase(project_id: int, what: str = "") -> str:
    return f"верни {what + ' ' if what else ''}как было в проекте {project_id}"


def many_model_phrase(project_ids: list[int], node_label: str) -> str:
    ids = ", ".join(str(pid) for pid in project_ids)
    return f"в проектах {ids} верни прежнюю модель на ноде {node_label}"


AGENT_PHRASE = "верни прежнего агента проверки"


# Подписи правок для карточки, вопроса и списка «что сделал».
@dataclass
class Described:
    title: str
    effect: str
    words: set[str] = field(default_factory=set)
    example: str = ""


# Откат этих видов сам оставляет обратимый снимок: «верни как было» вернёт правку обратно.
_BACK_AGAIN = frozenset(
    {
        "voiceover",
        "cell_voiceover",
        "master_prompt",
        "prompt_slot",
        "node_config",
        "node_disabled",
        "assemble_params",
        "node_model",
        "node_params",
        "project_settings",
    }
)


def _model_name(model_id: Any) -> str:
    from app.services import node_edit as ne

    if not model_id:
        return "по умолчанию"
    return ne.model_title(ne.find_catalog_model(str(model_id)), str(model_id))


def _node_name(meta: Any, key: str) -> str:
    from app.orchestrator.agents.approval_steps import node_labels
    from app.services.action_layer.builtin_group import node_label_ru

    label = node_labels(meta, [key]).get(key) if isinstance(meta, dict) else ""
    if label:
        return label
    try:
        return node_label_ru(meta if isinstance(meta, dict) else None, key)
    except Exception:
        return key


def _value_words(*texts: str) -> set[str]:
    return {word for text in texts for word in _tokens(text)}


def describe(row: Any, meta: Any = None) -> Described:
    """Что вернёт откат этого снимка, простыми словами, без номеров снимков."""
    kind = str(getattr(row, "kind", "") or "")
    target = row.target if isinstance(getattr(row, "target", None), dict) else {}
    pid = target.get("project_id") or getattr(row, "project_id", None)
    where = f" в проекте #{pid}" if pid else ""
    key = str(target.get("node_key") or "")
    node = _node_name(meta, key) if key else ""
    keep = (
        " Текущее значение сохранится, этот возврат тоже можно отменить."
        if kind in _BACK_AGAIN
        else " Текущее значение сохранится в журнале правок."
    )
    if kind == "node_model" and node:
        from app.services import node_edit as ne

        before, after = _model_name(row.before), _model_name(row.after)
        # Без своей модели нода идёт на модели по умолчанию своего типа: называем и её.
        default = ne.default_model_title(meta if isinstance(meta, dict) else None, key)
        before_ru = ne.model_ru(row.before, meta if isinstance(meta, dict) else None, key)
        after_ru = ne.model_ru(row.after, meta if isinstance(meta, dict) else None, key)
        title = (
            f"Вернуть прежнюю модель {before_ru} на ноде «{node}»{where}"
            if row.before
            else f"Вернуть модель {before_ru} на ноде «{node}»{where}"
        )
        instead = after_ru if row.after else f"модели {after_ru}"
        effect = f"На ноде «{node}»{where} снова будет модель {before_ru} вместо {instead}." + keep
        return Described(
            title,
            effect,
            _value_words(
                node,
                key,
                before,
                after,
                str(row.before or ""),
                str(row.after or ""),
                default if not (row.before and row.after) else "",
            ),
            f"верни прежнюю модель на ноде {node}{where}",
        )
    if kind == "prompt_slot" and node:
        from app.orchestrator.agents.approval_steps import variant_title

        before = variant_title(str(row.before)) if row.before else "«по умолчанию»"
        after = variant_title(str(row.after)) if row.after else "«по умолчанию»"
        title = f"Вернуть прежний промт {before} на ноде «{node}»{where}"
        effect = f"Нода «{node}»{where} снова будет читать промт {before} вместо {after}." + keep
        return Described(
            title,
            effect,
            _value_words(node, key, before, after),
            f"верни прежний промт на ноде {node}{where}",
        )
    if kind == "check_agent" and node:
        from app.services.action_layer.builtin_wave5_history import agent_state_ru

        before = agent_state_ru(row.before)
        title = f"Вернуть прежнего агента проверки на ноде «{node}»{where}"
        effect = f"На ноде «{node}»{where} снова будет {before} вместо {agent_state_ru(row.after)}." + keep
        return Described(title, effect, _value_words(node, key))
    if kind == "node_params" and node:
        from app.services import node_edit as ne

        names = ", ".join(ne.PARAM_TITLE_RU.get(k, k) for k in target.get("params") or [])
        title = f"Вернуть прежние настройки ноды «{node}»{where}" + (f": {names}" if names else "")
        effect = f"Настройки ноды «{node}»{where} станут такими, какими были до правки." + keep
        return Described(title, effect, _value_words(node, key, names))
    if kind == "project_settings":
        from app.services.action_layer.builtin_wave5_history import _SETTING_RU, _setting_value_ru

        values = row.before if isinstance(row.before, dict) else {}
        parts = [f"{_SETTING_RU.get(k, k)} {_setting_value_ru(k, v)}" for k, v in values.items()]
        title = f"Вернуть прежние настройки проекта{where.replace(' в проекте', '')}" + (
            ": " + ", ".join(parts) if parts else ""
        )
        effect = "Настройки проекта станут такими, какими были до правки." + keep
        return Described(title, effect, _value_words(*parts))
    what = _KIND_RU.get(kind, "прежнее значение")
    title = f"Вернуть прежнее значение: {what}{where}" + (f", нода «{node}»" if node else "")
    effect = f"Вернётся значение, которое было до правки ({what}{where})." + keep
    return Described(title, effect, _value_words(node, key, what))


# Чья правка: задачи этой беседы.
def task_operator(task_id: Any) -> str:
    """Создатель плана задачи чата, как его пишет orch_bridge."""
    return f"orchestrator:task-{task_id}"


def session_operators(session_id: str | None) -> list[str]:
    """Создатели планов одной беседы: её задачи, самые свежие первыми.

    ``session_id`` — внутреннее имя сессии движка (``<сессия>.<tenant>``,
    `app/services/orch_tenancy.py`), как его пишет супервизор в orch_tasks:
    одноимённый чат другого арендатора сюда не попадёт. Соединение движка на
    Postgres ещё и привязано к арендатору (RLS orch_tasks, ревизия 0020).
    """
    if not session_id:
        return []
    from sqlalchemy import text

    from app.orchestrator.db.migrations import connect

    conn = connect()
    try:
        rows = (
            conn.execute(
                text("SELECT id FROM orch_tasks WHERE session_id = :sid ORDER BY id DESC LIMIT :lim"),
                {"sid": str(session_id), "lim": int(_MAX_TASKS)},
            )
            .scalars()
            .all()
        )
    finally:
        conn.close()
    return [task_operator(row) for row in rows]


def task_operators(task_id: Any) -> list[str]:
    """Создатели планов беседы, к которой относится задача."""
    if task_id is None:
        return []
    from app.orchestrator.agents.task_store import TaskStore

    session_id = (TaskStore().get(int(task_id)) or {}).get("session_id")
    return session_operators(session_id) if session_id else [task_operator(task_id)]


def _by_operator(q: Any, operators: list[str] | None) -> Any:
    """None — правки любой беседы чата; список — только этих задач."""
    if operators is None:
        return q.where(store.ActionPlan.operator.like(CHAT_OPERATOR))
    return q.where(store.ActionPlan.operator.in_(list(operators)))


# Поиск правки и план отката.
async def chat_snapshots(
    project_id: int | None = None, *, operators: list[str] | None = None, limit: int = _MAX_ROWS
) -> list[Any]:
    """Снимки правок чата, которые ещё можно откатить, самые свежие первыми."""
    from sqlalchemy import select

    if operators is not None and not operators:
        return []
    q = (
        select(store.ActionSnapshot)
        .join(store.ActionPlan, store.ActionPlan.id == store.ActionSnapshot.plan_id)
        .where(store.ActionSnapshot.restored_at.is_(None), store.ActionSnapshot.restorable.is_(True))
        .order_by(store.ActionSnapshot.id.desc())
        .limit(limit)
    )
    q = _by_operator(q, operators)
    if project_id is not None:
        q = q.where(store.ActionSnapshot.project_id == int(project_id))
    async with store.sessions()() as s:
        return list((await s.scalars(q)).all())


async def still_current(row: Any) -> bool:
    """Значение после правки ещё на месте: иначе правку уже вернули или поправили заново."""
    from app.orchestrator import app_ref
    from app.services.action_layer.builtin_wave5_history import (
        _compare_value,
        _handler_for_kind,
        _public_file_state,
    )
    from app.services.action_layer.context import ActionContext

    app = app_ref.current()
    handler = _handler_for_kind(str(row.kind or ""))
    if app is None or handler is None:
        return True
    try:
        current = await handler.read(
            ActionContext(app=app, operator="system", read_only=True), dict(row.target or {})
        )
    except Exception:
        return True
    if isinstance(current, dict):
        current = _public_file_state(current)
    return _compare_value(row.kind, current) == _compare_value(row.kind, row.after)


async def _meta_of(project_id: int | None) -> Any:
    if not project_id:
        return None
    from app.orchestrator.agents.project_nodes import load_meta

    try:
        return await load_meta(int(project_id))
    except Exception:
        logger.exception("chat undo: project {} meta not read", project_id)
        return None


@dataclass
class UndoPlan:
    steps: list[dict[str, Any]]
    reason: str = ""


SnapshotsOf = Callable[[int | None], Awaitable[list[Any]]]
ChangesOf = Callable[[int | None], Awaitable[list[Any]]]
MetaOf = Callable[[int | None], Awaitable[Any]]
CurrentOf = Callable[[Any], Awaitable[bool]]
StepsOf = Callable[[int | None], Awaitable[list[Any]]]

# Сами откаты и чтения — не правка, после которой «верни как было» должно молчать.
_NOT_EDIT_STEPS = frozenset({"history.rollback", "change.rollback"})


async def chat_steps(
    project_id: int | None = None, *, operators: list[str] | None = None, limit: int = _MAX_ROWS
) -> list[Any]:
    """Сделанные шаги правок чата, самые свежие первыми: и те, что снимка «до» не оставили."""
    from sqlalchemy import or_, select

    from app.services.action_layer.spec import RISK_READ, ensure_builtin_actions, registry

    if operators is not None and not operators:
        return []
    q = (
        select(store.ActionPlanStep)
        .join(store.ActionPlan, store.ActionPlan.id == store.ActionPlanStep.plan_id)
        .where(store.ActionPlanStep.status == store.STEP_SUCCEEDED)
        .order_by(store.ActionPlanStep.id.desc())
        .limit(limit)
    )
    q = _by_operator(q, operators)
    if project_id is not None:
        # Шаг на всю студию («одновременно 2 проекта») без проекта: при открытом проекте он тоже свой.
        q = q.where(
            or_(store.ActionPlan.project_id == int(project_id), store.ActionPlan.project_id.is_(None))
        )
    async with store.sessions()() as s:
        rows = list((await s.scalars(q)).all())
        plan_ids = {row.plan_id for row in rows}
        # План с откатываемым снимком (даже уже откатанным) отменяет журнал снимков, это не «без снимка».
        with_snapshot = (
            set(
                (
                    await s.scalars(
                        select(store.ActionSnapshot.plan_id).where(
                            store.ActionSnapshot.plan_id.in_(plan_ids),
                            store.ActionSnapshot.restorable.is_(True),
                        )
                    )
                ).all()
            )
            if plan_ids
            else set()
        )
    ensure_builtin_actions()
    out = []
    for row in rows:
        spec = registry.find(row.action_id)
        if row.plan_id in with_snapshot:
            continue
        if spec is None or spec.risk == RISK_READ or row.action_id in _NOT_EDIT_STEPS:
            continue
        out.append(row)
    return out


def _no_snapshot_text(step: Any, older: Described | None) -> str:
    """Последняя правка чата без снимка «до»: вернуть её нечем, старую правку молча не трогаем."""
    from app.services.action_layer.spec import registry

    spec = registry.find(step.action_id)
    title = (spec.title_ru if spec else step.action_id).rstrip(".")
    frame = (step.params or {}).get("frame_number")
    where = f" (кадр {frame})" if frame not in (None, "") else ""
    text = (
        f"Последнюю правку «{title}»{where} автоматически вернуть нельзя: прежнее значение не "
        "сохранялось. Напишите, что поставить обратно, обычной командой"
    )
    # Голое «верни как было» снова упрётся в эту правку: подсказываем только точную фразу.
    if older is not None and older.example and older.example != UNDO_PHRASE:
        text += (
            f". Более раннюю правку можно вернуть: «{older.example}» — {older.title.lower()}"
        )
    return text


def _parallel_back(step: Any) -> dict[str, Any] | None:
    """«Верни как было» после смены параллели: прежнее число шаг сам записал в результат (before)."""
    if getattr(step, "action_id", None) != "queue.set_parallel":
        return None
    result = step.result if isinstance(getattr(step, "result", None), dict) else {}
    before = result.get("before")
    if not isinstance(before, int) or isinstance(before, bool) or before < 1 or before == result.get("worker_max_parallel"):
        return None
    return {
        "action": "queue.set_parallel",
        "params": {"workers": before},
        "expected": f"Вернуть {before} проектов одновременно в очереди генерации",
        "paid": False,
        "mutating": True,
        "risk": "low",
    }


def _alias(word: str) -> str:
    if word in _ALIASES:
        return _ALIASES[word]
    for rx, digit in _ORDINALS:
        if rx.match(word):
            return digit
    return word


def _covered(free: list[str], words: set[str], kind: str) -> bool:
    """Каждое слово просьбы подходит к правке: её вид («модель»), нода или значения."""
    from app.orchestrator.agents.project_nodes import _same, _stem

    have = {_stem(word) for word in words}
    for word in free:
        if any(rx.match(word) and kind in kinds for rx, kinds in _KINDS):
            continue
        word = _alias(word)
        if word not in words and not any(_same(_stem(word), got) for got in have):
            return False
    return True


def _rollback(row: Any, title: str) -> dict[str, Any]:
    from app.services.action_layer.chat_proposals import CcChange, rollback_step

    if isinstance(row, CcChange):
        return rollback_step(row, title)
    return {
        "action": "history.rollback",
        "params": {"snapshot_id": int(row.id)},
        "expected": title,
        "paid": False,
        "mutating": True,
        "risk": "high",
    }


async def _many_models(text: str, project_ids: list[int], meta_of: MetaOf) -> UndoPlan | None:
    """«В проектах 5, 6 верни прежнюю модель на ноде X»: одна правка на все проекты."""
    from app.orchestrator.agents.project_nodes import match_nodes, roster

    try:
        rows = roster(await meta_of(project_ids[0]))
    except Exception:
        return None
    keys = match_nodes(rows, text)
    if len(keys) != 1:
        return None
    return UndoPlan(
        [
            {
                "action": "node.revert_model_many",
                "params": {"project_ids": list(project_ids), "node_key": keys[0]},
                "paid": False,
                "mutating": True,
                "risk": "medium",
            }
        ]
    )


async def resolve(
    text: str,
    scope_project: int | None = None,
    *,
    operators: list[str] | None = None,
    snapshots_of: SnapshotsOf | None = None,
    meta_of: MetaOf | None = None,
    current_of: CurrentOf | None = None,
    changes_of: ChangesOf | None = None,
    steps_of: StepsOf | None = None,
) -> UndoPlan | None:
    """План отката по словам человека. None — это не просьба об откате, решает планёр.

    Кандидаты — снимки слоя (history.rollback) и версии change control, которые чат
    применил или откатил (change.rollback): правка текста промта живёт там.
    """
    from app.services.action_layer import chat_proposals as cp

    if not undo_phrase(text):
        return None

    async def own_snapshots(project_id: int | None) -> list[Any]:
        return await chat_snapshots(project_id, operators=operators)

    async def own_changes(project_id: int | None) -> list[Any]:
        try:
            return await cp.chat_changes(project_id, operators=operators)
        except Exception:
            logger.exception("chat undo: change control versions not read")
            return []

    async def no_changes(project_id: int | None) -> list[Any]:
        return []

    async def own_steps(project_id: int | None) -> list[Any]:
        try:
            return await chat_steps(project_id, operators=operators)
        except Exception:
            logger.exception("chat undo: chat steps not read")
            return []

    # Свой журнал снимков (тест, особый вызов) — и версии берутся только явно переданные.
    steps_of = steps_of or (own_steps if snapshots_of is None else no_changes)
    changes_of = changes_of or (own_changes if snapshots_of is None else no_changes)
    snapshots_of = snapshots_of or own_snapshots
    meta_of = meta_of or _meta_of
    snapshot_current = current_of or still_current

    async def current_of(row: Any) -> bool:
        if isinstance(row, cp.CcChange):
            return await cp.still_current(row)
        return await snapshot_current(row)

    named = named_projects(text)
    kinds = kinds_of(text)
    if len(named) > 1:
        return await _many_models(text, named, meta_of) if kinds == {"node_model"} else None
    project_id = named[0] if named else scope_project
    free = _free_words(text)
    rows = [*await snapshots_of(project_id), *await changes_of(project_id)]
    rows = [row for row in rows if not kinds or row.kind in kinds]
    # Самая свежая правка первой, из какого бы журнала она ни была.
    rows.sort(key=lambda row: getattr(row, "created_at", None) or datetime.min, reverse=True)
    metas: dict[Any, Any] = {}

    async def told(row: Any) -> Described | None:
        """Подпись правки, если слова просьбы (нода, модель) к ней подходят."""
        if isinstance(row, cp.CcChange):
            title, effect, words = await cp.describe_change(row)
            found = Described(title, effect, words, f"{cp.ROLLBACK_PHRASE} в проекте {row.project_id}")
            return found if _covered(free, found.words, str(row.kind or "")) else None
        if row.project_id not in metas:
            metas[row.project_id] = await meta_of(row.project_id)
        found = describe(row, metas[row.project_id])
        return found if _covered(free, found.words, str(row.kind or "")) else None

    chosen = None
    for row in rows[:_MAX_CHECKED]:
        found = await told(row)
        if found is not None and await current_of(row):
            chosen = (row, found)
            break
    if not free and not kinds:
        # «Верни как было» после правки без снимка (покрытие, перенос картинки): раньше молча
        # откатывалась более старая правка закадра.
        steps = await steps_of(project_id)
        newest = steps[0] if steps else None
        if newest is not None:
            since = getattr(chosen[0], "created_at", None) if chosen else None
            done = newest.finished_at or newest.started_at
            if since is None or (done is not None and done > since):
                back = _parallel_back(newest)
                if back is not None:
                    return UndoPlan([back])
                return UndoPlan([], _no_snapshot_text(newest, chosen[1] if chosen else None))
    if chosen is None:
        if free:
            return None
        where = f" в проекте #{project_id}" if project_id else ""
        return UndoPlan(
            [], f"Через этот чат{where} ещё ничего не меняли или всё уже вернули — возвращать нечего"
        )
    row, found = chosen
    # Одна правка чата сразу в нескольких местах: что вернуть, решает человек.
    group = [(other, await told(other)) for other in rows if row.plan_id and other.plan_id == row.plan_id]
    group = [(other, item) for other, item in group if item is not None]
    if len(group) > 1:
        lines = [item.title.replace("Вернуть ", "", 1) for _other, item in group[:_MAX_LISTED]]
        if len({other.project_id for other, _item in group}) > 1:
            example = project_undo_phrase(group[0][0].project_id)
        else:
            example = found.example or (
                project_undo_phrase(row.project_id) if row.project_id else UNDO_PHRASE
            )
        return UndoPlan(
            [],
            "Последняя правка чата поменяла сразу несколько мест: "
            + "; ".join(lines)
            + f". Напишите, что вернуть, например «{example}»",
        )
    return UndoPlan([_rollback(row, found.title)])


# Карточка отката.
async def rollback_card(params: dict[str, Any], meta_of: MetaOf | None = None) -> dict[str, Any]:
    """Заголовок и последствия отката простыми словами вместо номера снимка."""
    try:
        row = await store.get_snapshot(int(params["snapshot_id"]))
    except Exception:
        return {}
    if row is None:
        return {}
    found = describe(row, await (meta_of or _meta_of)(row.project_id))
    effect = found.effect
    if params.get("force"):
        effect += " Проверка «значение не меняли после правки» отключена."
    # «Что изменится» без «безвозвратно» — только там, где возврат сам оставляет снимок.
    return {
        "title": found.title,
        "destructive_effects": [effect],
        "effects_reversible": str(row.kind or "") in _BACK_AGAIN,
        "rollback": {"snapshot_id": int(row.id), "title": found.title},
    }


# Что чат сделал за сегодня.
def _day_start_utc(now: datetime | None = None) -> datetime:
    local = (now or datetime.now().astimezone()).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).replace(tzinfo=None)


def _local_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone().strftime("%H:%M")


async def today_changes(
    project_id: int | None = None,
    *,
    operators: list[str] | None = None,
    now: datetime | None = None,
    limit: int = 30,
) -> list[str]:
    """Изменяющие шаги, которые чат выполнил сегодня: время, проект, что сделано."""
    from sqlalchemy import select

    from app.services.action_layer.spec import RISK_READ, ensure_builtin_actions, registry
    from app.services.plain_ru import plain_ru
    from app.services.redact import redact_text

    ensure_builtin_actions()
    if operators is not None and not operators:
        return []
    q = (
        select(store.ActionPlanStep, store.ActionPlan.project_id)
        .join(store.ActionPlan, store.ActionPlan.id == store.ActionPlanStep.plan_id)
        .where(
            store.ActionPlanStep.status == store.STEP_SUCCEEDED,
            store.ActionPlanStep.finished_at >= _day_start_utc(now),
        )
        .order_by(store.ActionPlanStep.finished_at.asc())
    )
    q = _by_operator(q, operators)
    async with store.sessions()() as s:
        rows = (await s.execute(q)).all()
    lines: list[str] = []
    for step, plan_project in rows:
        spec = registry.find(step.action_id)
        if spec is None or spec.risk == RISK_READ:
            continue
        params = step.params if isinstance(step.params, dict) else {}
        pid = params.get("project_id") or plan_project
        # Правка сразу в нескольких проектах: каждый из них видит её как свою.
        many = [int(p) for p in params.get("project_ids") or [] if str(p).isdigit()] if not pid else []
        if project_id is not None and pid != project_id and project_id not in many:
            continue
        result = step.result if isinstance(step.result, dict) else {}
        done = " ".join(str(result.get("text_ru") or "").split())
        where = f", проект #{pid}" if pid else ", проекты " + ", ".join(f"#{p}" for p in many) if many else ""
        head = f"- {_local_time(step.finished_at)}{where}"
        lines.append(plain_ru(redact_text(f"{head}: {done or spec.title_ru}"))[:400])
    return lines[-limit:]


_NOTHING = "возвращать нечего"


async def plan_for_task(text: str, task_id: Any = None) -> UndoPlan | None:
    """Планёр чата кодом: правка промта (применить, отменить предложение) и откат правки беседы.

    Проект — задачи, если он известен. «Откати правку», а правка ещё не применена: ответ
    об этом с готовыми фразами вместо «возвращать нечего».
    """
    import asyncio

    from app.orchestrator.agents.action_catalog import _task_project
    from app.services.action_layer import chat_proposals as cp

    try:
        operators = await asyncio.to_thread(task_operators, task_id)
        scope = _task_project(task_id)
        found = await cp.resolve(text, scope, operators=operators)
        if found is not None:
            return UndoPlan(found.steps, found.reason)
        plan = await resolve(text, scope, operators=operators)
        nothing = plan is not None and not plan.steps and _NOTHING in plan.reason
        # Откатывать нечего (или нечего подходящего), а правка промта ждёт применения.
        about_prompt = plan is None and undo_phrase(text) and "prompt_version" in kinds_of(text)
        if nothing or about_prompt:
            named = named_projects(text)
            note = await cp.pending_note(operators, named[0] if len(named) == 1 else scope)
            if note:
                return UndoPlan([], note)
        return plan
    except Exception:
        logger.exception("chat undo: plan not resolved")
        return None
