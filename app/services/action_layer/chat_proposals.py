"""Правка промта из чата: применить, отменить и откатить словами человека.

Правку готовит ``prompt.edit_text`` (предложение change control), применяет ``change.apply``,
откатывает ``change.rollback`` (прежнее значение — версия цели), отменяет неприменённую
``change.reject``. Здесь:

* какие предложения ждут применения: сначала предложения этой беседы (их создали задачи
  её чата, см. ``chat_undo.session_operators``), иначе все ожидающие у арендатора — на
  Postgres таблицы change control закрыты RLS (``tenant_tables``);
* план «примени правку» / «отмени предложение правки» кодом, без модели: одно ожидающее
  предложение — шаг с ним, несколько — вопрос с нодой и проектом каждого;
* журнал применённых через чат версий для «откати правку промта» (``chat_undo.resolve``);
* человеческие карточки и итоги шагов вместо номеров предложений и контрольных сумм.

Каждый шаг по-прежнему подтверждает человек карточкой; уровень риска здесь не меняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

APPLY_PHRASE = "примени правку промта"
REJECT_PHRASE = "отмени предложение правки"
ROLLBACK_PHRASE = "откати правку промта"
_MAX_LISTED = 5
_MAX_LINES = 3
_LINE_LIMIT = 120

_WORD = re.compile(r"[a-zа-я0-9]+")
_TARGET = re.compile(r"^project:(\d+):([A-Za-z0-9_.\-]+):(prompt|options|step_params)$")
_APPLY_VERB = re.compile(
    r"^(?:примени|примените|применить|применяй|применяйте|внеси|внесите|внести|накати|накатите|накатить)$"
)
_REJECT_VERB = re.compile(
    r"^(?:отмени|отмените|отменить|отменяй|отклони|отклоните|отклонить|выкинь|выбрось|сбрось|удали|удалите)$"
)
_CHANGE_OBJECT = re.compile(r"^(?:правк\w*|предложени\w*|изменени\w*)$")
_PROPOSAL = re.compile(r"^предложени\w*$")
_UNAPPLIED = re.compile(r"^(?:неприменен\w*|ожидающ\w*|подготовлен\w*)$")
# «Примени правку: допиши …» — это новая правка, её планирует модель.
_EDIT_VERB = re.compile(
    r"^(?:допиш\w*|дописать|добав\w*|замени\w*|заменить|перепиш\w*|убер\w*|убрать|исправ\w*|поправ\w*)$"
)
_QUESTION_START = frozenset({"как", "почему", "зачем", "что", "где", "когда", "можно", "можешь", "ты"})

_KIND_OF_GROUP = {
    "prompt": "prompt_version",
    "options": "options_version",
    "step_params": "step_params_version",
}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _tokens(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def _question(text: str, words: list[str]) -> bool:
    return bool(words) and (words[0] in _QUESTION_START or _norm(text).endswith("?"))


def apply_phrase(text: str) -> bool:
    """«Примени правку промта», «примени предложение», «да, применяй»."""
    words = _tokens(text)
    if _question(text, words) or any(_EDIT_VERB.match(w) for w in words):
        return False
    for i, word in enumerate(words):
        if not _APPLY_VERB.match(word):
            continue
        if i > 0 and words[i - 1] == "не":
            return False
        if any(_CHANGE_OBJECT.match(w) for w in words) or len(words) <= 3:
            return True
    return False


def reject_phrase(text: str) -> bool:
    """«Отмени предложение правки», «отклони неприменённую правку», «не применяй правку»."""
    words = _tokens(text)
    if _question(text, words) or any(_EDIT_VERB.match(w) for w in words):
        return False
    for i, word in enumerate(words):
        if _APPLY_VERB.match(word) and i > 0 and words[i - 1] == "не":
            return True
    if not any(_REJECT_VERB.match(w) for w in words):
        return False
    if any(_PROPOSAL.match(w) for w in words):
        return True
    joined = " ".join(words)
    unapplied = any(_UNAPPLIED.match(w) for w in words) or "не примен" in joined
    return unapplied and any(w.startswith("правк") for w in words)


# ── что меняется: нода, проект, строки ──────────────────────────────────────


def parse_target(target: str) -> tuple[int | None, str, str]:
    m = _TARGET.match(target or "")
    if not m:
        return None, "", ""
    return int(m.group(1)), m.group(2), m.group(3)


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= _LINE_LIMIT else line[: _LINE_LIMIT - 1].rstrip() + "…"


def diff_lines(diff: str) -> tuple[list[str], list[str]]:
    """Добавленные и убранные строки из unified diff предложения (без заголовков)."""
    added: list[str] = []
    removed: list[str] = []
    for line in str(diff or "").splitlines():
        if line.startswith(("+++", "---", "@@", "... [diff")):
            continue
        if line.startswith("+") and line[1:].strip():
            added.append(_clip(line[1:]))
        elif line.startswith("-") and line[1:].strip():
            removed.append(_clip(line[1:]))
    return added, removed


def _quoted(lines: list[str]) -> str:
    shown = "; ".join(f"«{line}»" for line in lines[:_MAX_LINES])
    if len(lines) > _MAX_LINES:
        shown += f" и ещё строк: {len(lines) - _MAX_LINES}"
    return shown


async def _meta(project_id: int | None) -> Any:
    from app.services.action_layer.chat_undo import _meta_of

    return await _meta_of(project_id)


def _node(meta: Any, key: str) -> str:
    from app.services.action_layer.chat_undo import _node_name

    return _node_name(meta, key) if key else ""


@dataclass
class Change:
    """Предложение простыми словами."""

    proposal_id: str
    project_id: int | None
    node_key: str
    node: str
    group: str
    kind: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    variant: str = ""

    @property
    def what(self) -> str:
        if self.group == "prompt":
            return "промт"
        if self.group == "options":
            return "настройки"
        return "параметры шага"

    @property
    def where(self) -> str:
        node = f" ноды «{self.node}»" if self.node else ""
        project = f" в проекте #{self.project_id}" if self.project_id else ""
        return node + project

    @property
    def subject(self) -> str:
        """«правку промта ноды «Работа с GPT 1» в проекте #7»"""
        return f"правку {'промта' if self.group == 'prompt' else 'настроек'}{self.where}"

    def change_ru(self, *, undo: bool = False, done: bool = False) -> str:
        """Что добавится и что уберётся; ``undo`` — то же наоборот (для отката), ``done`` — в прошедшем времени."""
        added, removed = (self.removed, self.added) if undo else (self.added, self.removed)
        if self.kind == "prompt_variant" and self.variant:
            from app.orchestrator.agents.approval_steps import variant_title

            return f"нода будет читать промт {variant_title(self.variant)}"
        parts = []
        if added:
            if done:
                parts.append(("добавлено " if not undo else "вернулось ") + _quoted(added))
            else:
                parts.append(("добавится " if not undo else "вернётся ") + _quoted(added))
        if removed:
            parts.append(("убрано " if done else "уберётся ") + _quoted(removed))
        return ", ".join(parts)


async def describe_view(view: dict) -> Change:
    pid, key, group = parse_target(str(view.get("target") or ""))
    pid = view.get("project_id") or pid
    key = str(view.get("node_key") or key)
    meta = await _meta(pid)
    added, removed = diff_lines(str(view.get("diff") or ""))
    cand = view.get("candidate") if isinstance(view.get("candidate"), dict) else {}
    return Change(
        proposal_id=str(view.get("id") or ""),
        project_id=int(pid) if pid else None,
        node_key=key,
        node=_node(meta, key),
        group=group or "prompt",
        kind=str(view.get("kind") or ""),
        added=added,
        removed=removed,
        variant=str(cand.get("variant") or "") if view.get("kind") == "prompt_variant" else "",
    )


async def describe_proposal(proposal_id: str) -> Change | None:
    from app.services.change_control import service

    try:
        return await describe_view(await service.get_proposal(str(proposal_id)))
    except Exception:
        logger.exception("chat proposals: proposal {} not described", str(proposal_id)[:8])
        return None


# ── карточки ────────────────────────────────────────────────────────────────


def _card(action: str, params: dict, title: str, effect: str, *, reversible: bool) -> dict[str, Any]:
    return {
        "title": title,
        "destructive_effects": [effect],
        "effects_reversible": reversible,
        "card_step": {"action": action, "params": dict(params), "title": title},
    }


async def apply_card(params: dict) -> dict[str, Any]:
    """«Применить правку промта ноды «Работа с GPT 1» в проекте #7»: что поменяется и что откат есть."""
    found = await describe_proposal(str(params.get("proposal_id") or ""))
    if found is None:
        return {}
    change = found.change_ru()
    title = f"Применить {found.subject}"
    effect = (
        f"{found.what.capitalize()}{found.where} поменяется"
        + (f": {change}." if change else ".")
        + f" Прежний {found.what if found.group == 'prompt' else 'вариант'} сохранится, правку можно "
        f"откатить: напишите «{ROLLBACK_PHRASE}». Новые запросы к модели пойдут с новым текстом."
    )
    return _card("change.apply", params, title, effect, reversible=True)


async def reject_card(params: dict) -> dict[str, Any]:
    found = await describe_proposal(str(params.get("proposal_id") or ""))
    if found is None:
        return {}
    change = found.change_ru()
    title = f"Отменить неприменённую {found.subject}"
    effect = (
        "Правка не будет применена"
        + (f" ({change})" if change else "")
        + f": {found.what}{found.where} останется как сейчас."
    )
    return _card("change.reject", params, title, effect, reversible=False)


async def _version(target: str, version: int) -> Any:
    from sqlalchemy import select

    from app.services.change_control import store as cs

    async with cs.sessions()() as s:
        return await s.scalar(
            select(cs.ConfigVersion).where(
                cs.ConfigVersion.target == target, cs.ConfigVersion.version == int(version)
            )
        )


async def _rollback_change(target: str, version: int) -> tuple[Change | None, bool]:
    """Правка, которую вернёт откат версии ``version``, и «это откат отката»."""
    row = await _version(target, version)
    if row is None or not row.source_proposal_id:
        return None, False
    from app.services.change_control import store as cs

    return await describe_proposal(row.source_proposal_id), row.kind == cs.VK_ROLLBACK


async def rollback_card(params: dict) -> dict[str, Any]:
    """Карточка change.rollback: какая нода и что вернётся, без target и номера версии."""
    try:
        found, again = await _rollback_change(str(params["target"]), int(params["expected_version"]))
    except Exception:
        logger.exception("chat proposals: rollback card not built")
        return {}
    if found is None:
        return {}
    if again:
        title = f"Снова применить отменённую {found.subject}"
        change = found.change_ru()
        effect = f"{found.what.capitalize()}{found.where} снова поменяется" + (
            f": {change}." if change else "."
        )
    else:
        title = f"Откатить {found.subject}"
        change = found.change_ru(undo=True)
        effect = f"{found.what.capitalize()}{found.where} станет таким, каким был до правки" + (
            f": {change}." if change else "."
        )
    effect += " Текущий вариант сохранится в истории версий, этот откат тоже можно отменить."
    return _card("change.rollback", params, title, effect, reversible=True)


# ── итоги шагов ─────────────────────────────────────────────────────────────


async def applied_text(out: dict) -> str:
    found = await describe_proposal(str(out.get("proposal_id") or ""))
    if found is None:
        return f"Правку применил. Если передумаете, напишите «{ROLLBACK_PHRASE}»."
    lines = [f"Применил {found.subject}."]
    if found.added:
        lines.append("Добавлено:")
        lines.extend(f"+ {line}" for line in found.added[:6])
    if found.removed:
        lines.append("Убрано:")
        lines.extend(f"− {line}" for line in found.removed[:6])
    lines.append(f"Прежний вариант сохранён: если передумаете, напишите «{ROLLBACK_PHRASE}».")
    return "\n".join(lines)


async def rolled_back_text(out: dict) -> str:
    proposal = str(out.get("proposal_id") or "")
    found = await describe_proposal(proposal) if proposal else None
    if found is None:
        return "Откатил правку: вернул прежнее значение. Этот откат тоже можно отменить."
    undone = await _version(str(out.get("target") or ""), int(out.get("previous_current") or 0))
    from app.services.change_control import store as cs

    if undone is not None and undone.kind == cs.VK_ROLLBACK:
        change = found.change_ru(done=True)
        return (
            f"Снова применил {found.subject}"
            + (f": {change}." if change else ".")
            + f" Если передумаете, напишите «{ROLLBACK_PHRASE}»."
        )
    change = found.change_ru(undo=True, done=True)
    return (
        f"Откатил {found.subject}: {found.what} снова такой, как до правки"
        + (f" ({change})." if change else ".")
        + " Этот откат тоже можно отменить: напишите «верни как было»."
    )


async def rejected_text(view: dict) -> str:
    found = await describe_view(view)
    return f"Отменил неприменённую {found.subject}. {found.what.capitalize()} не менялся."


# ── ожидающие предложения ───────────────────────────────────────────────────


def proposers(operators: list[str] | None) -> set[str]:
    """Кем записано предложение задачи чата: ``orchestrator:task-N`` или ``planner:…``."""
    ops = list(operators or [])
    return {*ops, *(f"planner:{op}" for op in ops)}


async def pending(
    operators: list[str] | None,
    project_id: int | None = None,
    *,
    applicable: bool = True,
) -> list[dict]:
    """Предложения, которые ждут решения: сначала этой беседы, иначе все у арендатора."""
    from app.services.change_control import service

    data = await service.list_proposals(project_id=project_id, status="proposed", limit=50)
    items = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("applicable")]
    if applicable:
        items = [i for i in items if i.get("can_apply") and not i.get("stale")]
    own = proposers(operators)
    mine = [i for i in items if i.get("proposer") in own]
    return mine or items


async def _choice_lines(items: list[dict]) -> list[str]:
    lines = []
    for item in items[:_MAX_LISTED]:
        found = await describe_proposal(str(item.get("id")))
        if found is None:
            continue
        change = found.change_ru()
        lines.append(f"{found.what}{found.where}" + (f" ({change})" if change else ""))
    return lines


@dataclass
class ProposalPlan:
    steps: list[dict[str, Any]]
    reason: str = ""


def _step(action: str, params: dict, expected: str) -> dict[str, Any]:
    return {
        "action": action,
        "params": params,
        "expected": expected,
        "paid": False,
        "mutating": True,
        "risk": "high",
    }


async def _pick(items: list[dict], verb: str, nothing: str) -> tuple[dict | None, str]:
    if not items:
        return None, nothing
    if len(items) == 1:
        return items[0], ""
    lines = await _choice_lines(items)
    pids = sorted({int(i["project_id"]) for i in items if i.get("project_id")})
    hint = f"«{verb} в проекте {pids[0]}»" if pids else f"«{verb}»"
    return None, (
        "Ждут решения несколько правок: " + "; ".join(lines) + f". Напишите, какую, например {hint}"
    )


async def resolve(
    text: str, scope_project: int | None = None, *, operators: list[str] | None = None
) -> ProposalPlan | None:
    """План по словам человека. None — это не про ожидающие правки, решает дальше откат/планёр."""
    from app.services.action_layer.chat_undo import named_projects

    want_reject = reject_phrase(text)
    want_apply = not want_reject and apply_phrase(text)
    if not (want_reject or want_apply):
        return None
    named = named_projects(text)
    project_id = named[0] if len(named) == 1 else scope_project
    items = await pending(operators, project_id, applicable=want_apply)
    if want_apply:
        chosen, why = await _pick(
            items,
            APPLY_PHRASE,
            "Ожидающих правок нет: применять нечего. Сначала попросите правку, например «допиши в "
            "конец промта первой ноды «Работа с GPT» в проекте 7 строку: Пиши коротко»",
        )
        if chosen is None:
            return ProposalPlan([], why)
        params = {"proposal_id": str(chosen["id"]), "expected_digest": str(chosen["digest"])}
        return ProposalPlan([_step("change.apply", params, "Правка применена, прежний вариант сохранён.")])
    chosen, why = await _pick(items, REJECT_PHRASE, "Неприменённых правок нет: отменять нечего")
    if chosen is None:
        return ProposalPlan([], why)
    return ProposalPlan(
        [
            _step(
                "change.reject", {"proposal_id": str(chosen["id"])}, "Предложение отменено, промт не менялся."
            )
        ]
    )


async def pending_note(operators: list[str] | None, project_id: int | None = None) -> str:
    """«Откати правку», а правка ещё не применена: так и сказать, с готовыми фразами."""
    try:
        items = await pending(operators, project_id)
    except Exception:
        logger.exception("chat proposals: pending not read")
        return ""
    if not items:
        return ""
    lines = await _choice_lines(items[:1])
    what = f" ({lines[0]})" if lines else ""
    return (
        f"Правка{what} ещё не применена: промт пока прежний, откатывать нечего. Чтобы применить, "
        f"напишите «{APPLY_PHRASE}», чтобы отказаться от неё — «{REJECT_PHRASE}»"
    )


# ── журнал применённых через чат версий ─────────────────────────────────────


@dataclass
class CcChange:
    """Применение или откат change control, сделанные чатом: строка-кандидат для «откати»."""

    id: str
    kind: str
    project_id: int | None
    plan_id: str
    target: dict
    cc_target: str
    version: int
    action_id: str
    proposal_id: str = ""
    created_at: datetime | None = None
    before: Any = None
    after: Any = None
    restorable: bool = True


async def chat_changes(
    project_id: int | None = None, *, operators: list[str] | None = None, limit: int = 20
) -> list[CcChange]:
    """Применения и откаты change control, которые выполнил чат, самые свежие первыми."""
    from sqlalchemy import select

    from app.services.action_layer import store
    from app.services.action_layer.chat_undo import _by_operator

    if operators is not None and not operators:
        return []
    q = (
        select(store.ActionPlanStep)
        .join(store.ActionPlan, store.ActionPlan.id == store.ActionPlanStep.plan_id)
        .where(
            store.ActionPlanStep.action_id.in_(("change.apply", "change.rollback")),
            store.ActionPlanStep.status == store.STEP_SUCCEEDED,
        )
        .order_by(store.ActionPlanStep.finished_at.desc())
        .limit(limit)
    )
    q = _by_operator(q, operators)
    async with store.sessions()() as s:
        rows = list((await s.scalars(q)).all())
    out: list[CcChange] = []
    for step in rows:
        result = step.result if isinstance(step.result, dict) else {}
        target = str(result.get("target") or "")
        pid, key, group = parse_target(target)
        version = result.get("version")
        if pid is None or not isinstance(version, int) or isinstance(version, bool):
            continue
        if project_id is not None and pid != int(project_id):
            continue
        out.append(
            CcChange(
                id=f"cc:{target}@{version}",
                kind=_KIND_OF_GROUP[group],
                project_id=pid,
                plan_id=str(step.plan_id or ""),
                target={"project_id": pid, "node_key": key},
                cc_target=target,
                version=int(version),
                action_id=str(step.action_id),
                proposal_id=str(result.get("proposal_id") or ""),
                created_at=step.finished_at,
            )
        )
    return out


async def still_current(row: CcChange) -> bool:
    """Версия правки — текущая: иначе её уже откатили или поправили заново."""
    from app.services.change_control import store as cs

    async with cs.sessions()() as s:
        ptr = await s.get(cs.ConfigPointer, row.cc_target)
    return ptr is not None and int(ptr.current_version) == int(row.version)


async def describe_change(row: CcChange) -> tuple[str, str, set[str]]:
    """Подпись отката для «верни как было»: заголовок, последствия, слова для сверки с просьбой."""
    found, again = await _rollback_change(row.cc_target, row.version)
    if found is None:
        what = "промт" if row.kind == "prompt_version" else "настройки"
        title = f"Откатить правку ({what} ноды в проекте #{row.project_id})"
        return title, "Вернётся прежнее значение.", {row.target.get("node_key") or ""}
    title = f"Снова применить отменённую {found.subject}" if again else f"Откатить {found.subject}"
    card = await rollback_card({"target": row.cc_target, "expected_version": row.version})
    effect = (card.get("destructive_effects") or [""])[0]
    words = {*_tokens(found.node), *_tokens(found.node_key)}
    return title, effect, words


def rollback_step(row: CcChange, title: str) -> dict[str, Any]:
    return _step("change.rollback", {"target": row.cc_target, "expected_version": int(row.version)}, title)
