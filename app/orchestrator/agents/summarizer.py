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
    return f"Теперь одновременно идут до {got} {_plural(got, 'проекта', 'проектов', 'проектов')}."


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


def _change_line(note: StepNote) -> str:
    if note.known and note.text:
        return note.text
    return f"{note.title}" + (f": {note.text}" if note.text else ".")


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


def deterministic_text(facts: TaskFacts) -> str:
    done = [note for note in facts.steps if note.ok and not note.skipped]
    failed = [note for note in facts.steps if not note.ok and not note.skipped]
    lines: list[str] = []
    if facts.status == "done":
        changes = [note for note in done if not note.readonly]
        if changes:
            # Чтения перед правкой — проверки состояния «до»: после правки они уже устарели.
            if len(changes) == 1 and changes[0].known and changes[0].text:
                lines.append(changes[0].text)
            else:
                lines.append("Готово. Что сделано:")
                lines.extend(f"• {_change_line(note)}" for note in changes)
            undo = [note for note in changes if note.undo]
            lines.extend(dict.fromkeys(line for line in map(_undo_line, undo) if line))
            if not undo and not all(note.known for note in changes):
                lines.append(
                    "Снимка для автоматического отката у этих шагов нет: вернуть можно только "
                    "повторной правкой."
                )
        else:
            found = [note for note in done if note.text]
            if found:
                lines.append("Вот что нашёл:")
                lines.extend(f"• {note.title}: {note.text}" for note in found)
            else:
                lines.append("Готово, но данных для ответа шаги не вернули.")
        for note in failed:
            lines.append(f"Не получилось «{note.title}»: {note.error or 'без пояснения'}.")
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
        lines.append(f"Шаг «{note.title}»: {note.error or 'ошибка без пояснения'}.")
    # Чтения состояния перед шагом ничего не меняли: в «успели» только правки.
    changed = [note for note in done if not note.readonly]
    if changed:
        lines.append("Успели выполнить: " + ", ".join(f"«{note.title}»" for note in changed) + ".")
    lines.append("Что можно сделать: " + todo)
    return "\n".join(lines)


def _facts_prompt(facts: TaskFacts) -> list[dict[str, str]]:
    rules = list(ANSWER_RULES)
    rules.append("Это итог задачи из чата: перескажи найденное по фактам и ответь на просьбу пользователя.")
    used = 0
    lines = ["Факты, прочитанные шагами задачи:"]
    for note in facts.steps[:_STEP_LIMIT]:
        body = (
            note.text
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
        if not text:
            return fallback
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
