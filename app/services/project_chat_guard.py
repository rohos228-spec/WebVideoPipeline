"""Правила чата проекта («ИИ-агент» в панели проекта): что выполнять сразу, что по кнопке.

Раньше режим «С подтверждением» был только фразой в подсказке модели: сервер
выполнял любое действие из её ответа сразу — запуск платного шага, одобрение
проверки, смену настроек. Здесь:

- какие действия в режиме «С подтверждением» превращаются в карточку с кнопкой;
- хранилище таких карточек на сервере (кнопка подтверждает ровно то, что показано);
- подписи карточек и плашек по-русски, названиями нод с канваса;
- человеческий текст ошибок вместо сообщений исключений;
- правка кода программы из чата: только админ и только с флагом окружения.
"""

from __future__ import annotations

import os
import re
import secrets
import time

from app.services.chat_humanize import _TYPE_DEFAULT, humanize_reply, step_title

#: Действия, которые в режиме «С подтверждением» ждут кнопку.
#: Остаются сразу: открыть окно, показать, автопроверка, остановить генерацию
#: (остановка — защитное действие, человек ждёт её немедленно).
CONFIRM_KEYS: tuple[str, ...] = (
    "run_step",
    "hitl_decision",
    "set_option",
    "set_prompt",
    "set_text_llm",
    "set_topic",
    "create_project",
    "create_child",
    "add_node",
    "connect_edges",
    "repair_graph",
    "rename_node",
)
CODE_KEYS: tuple[str, ...] = ("read_file", "edit_files", "run_tests", "git_commit_push")

#: Шаги, которые тратят деньги на медиа-провайдерах (картинки, видео, звук).
PAID_MEDIA_STEPS = frozenset({"img", "video", "audio", "music", "hero", "items", "sfx_gen"})

_PENDING: dict[str, dict] = {}
PENDING_TTL_S = 30 * 60

_HITL_RU = {
    "approve": "одобрить",
    "regenerate": "отправить на перегенерацию",
    "reject": "отклонить",
    "edit_prompt": "поправить промт",
}
_HITL_DONE_RU = {
    "approved": "одобрено",
    "regenerate": "отправлено на перегенерацию",
    "rejected": "отклонено",
    "edit_prompt": "промт отправлен на правку",
}


def code_edits_allowed() -> bool:
    """Правка кода программы из чата: флаг ORCHESTRATOR_CHAT_CODE_EDITS=1 и админ (или режим владельца)."""
    flag = os.environ.get("ORCHESTRATOR_CHAT_CODE_EDITS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not flag:
        return False
    from app.services.studio_auth import current_identity

    ident = current_identity()
    return ident is None or ident.is_admin


def action_key(act: dict) -> str | None:
    for k in (
        *CONFIRM_KEYS,
        *CODE_KEYS,
        "stop_step",
        "open_ui",
        "run_harness",
        "delete_projects",
        "remove_node",
    ):
        if k in act:
            return k
    return None


def is_publish(act: dict) -> bool:
    return action_key(act) == "run_step" and str(act.get("run_step") or "").strip() == "publish"


def needs_confirm(act: dict, *, auto: bool) -> bool:
    # Публикация выкладывает ролик наружу и не отзывается: кнопка человека даже в автономном режиме.
    if is_publish(act):
        return True
    return not auto and action_key(act) in CONFIRM_KEYS


async def publish_refusal(session, project) -> str | None:
    """Почему шаг «Публикация» сейчас нельзя запускать (None — можно).

    Шаг заказчика при выключенной автопубликации молча ставит «опубликовано», ничего
    не выложив, — такой запуск из чата обманул бы человека, поэтому отказ.
    """
    from sqlalchemy import select

    from app.models import Artifact, ArtifactKind
    from app.settings import settings

    if not settings.social_publish_enabled:
        return (
            "автопубликация выключена в настройках сервера (SOCIAL_PUBLISH_ENABLED): "
            "шаг «Публикация» ничего не выложит, поэтому не запускаю"
        )
    status = getattr(project.status, "value", str(project.status))
    if status not in {"assembled", "publishing"}:
        return f"публиковать можно собранный ролик; сейчас проект на этапе «{status}»"
    final = (
        await session.execute(
            select(Artifact.id)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video)
            .limit(1)
        )
    ).scalar_one_or_none()
    if final is None:
        return "нет финального видео для публикации"
    return None


def _node_label(keymap: dict, key: str | None) -> str:
    if not key:
        return ""
    lab = humanize_reply(str(key), keymap)
    return lab.strip("«»")


def action_title(act: dict, keymap: dict | None = None) -> str:
    """Что сделает действие — одной фразой для карточки подтверждения."""
    km = keymap or {}
    key = action_key(act)
    v = act.get(key) if key else None
    spec = v if isinstance(v, dict) else {}
    if key == "run_step" and str(v or "").strip() == "publish":
        return (
            "Опубликовать готовый ролик на все площадки, где его ещё нет (TikTok, YouTube Shorts, "
            "Instagram Reels, VK Клипы, Likee), с подписью из темы проекта. Отозвать из студии нельзя."
        )
    if key == "run_step":
        step = str(v or "")
        paid = " Это платный шаг." if step in PAID_MEDIA_STEPS else ""
        return f"Запустить шаг «{step_title(step, km)}».{paid}"
    if key == "hitl_decision":
        return f"Проверка человеком: {_HITL_RU.get(str(v), str(v))}."
    if key == "set_option":
        name = humanize_reply(str(spec.get("key") or ""), km)
        return f"Поменять настройку «{name}» на «{spec.get('value')}»."
    if key == "set_prompt":
        return f"Выбрать вариант промта «{spec.get('variant')}» для шага «{step_title(str(spec.get('step') or ''), km)}»."
    if key == "set_text_llm":
        return f"Сменить текстовую модель на «{spec.get('provider')}» — для всех проектов студии."
    if key == "set_topic":
        return f"Сменить тему проекта на «{str(v)[:120]}»."
    if key == "create_project":
        return f"Создать новый проект «{spec.get('title') or 'без названия'}»."
    if key == "create_child":
        return "Создать дочерний проект с теми же настройками."
    if key == "add_node":
        typ = str(spec.get("node_type") or "")
        name = _TYPE_DEFAULT.get(typ) or ("Проверка человеком" if typ.startswith("hitl") else typ)
        after = spec.get("after")
        where = (
            " после каждой рабочей ноды"
            if after == "each"
            else (f" после «{_node_label(km, after)}»" if after else "")
        )
        return f"Добавить ноду «{name}»{where}."
    if key == "connect_edges":
        return "Провести связи от нод к хранилищу."
    if key == "repair_graph":
        return "Пересобрать цепочку нод по порядку."
    if key == "rename_node":
        return f"Переименовать «{_node_label(km, spec.get('node_key'))}» в «{spec.get('label')}»."
    return "Выполнить действие."


def put_pending(project_id: int, act: dict, title: str) -> str:
    now = time.time()
    for tok in [t for t, p in _PENDING.items() if now - p["created"] > PENDING_TTL_S]:
        _PENDING.pop(tok, None)
    token = secrets.token_urlsafe(12)
    _PENDING[token] = {"project_id": project_id, "action": act, "title": title, "created": now}
    return token


def take_pending(project_id: int, token: str) -> dict | None:
    """Карточка по токену (одноразово). None — нет, чужой проект или устарела."""
    p = _PENDING.pop(str(token or ""), None)
    if p is None or p["project_id"] != project_id or time.time() - p["created"] > PENDING_TTL_S:
        return None
    return p


def action_note(entry: dict, keymap: dict | None = None) -> str:
    """Плашка под ответом: что реально выполнено — по-русски, названиями с канваса."""
    km = keymap or {}
    if "run_step" in entry:
        return f"запущен шаг «{entry.get('step_label') or step_title(str(entry['run_step']), km)}»"
    if "stop_step" in entry:
        return "генерация остановлена"
    if "set_option" in entry:
        name, _, value = humanize_reply(str(entry["set_option"]), km).partition("=")
        return f"настройка «{name}»: теперь {value}" if value else "настройка изменена: " + name
    if "set_prompt" in entry:
        raw = str(entry["set_prompt"])
        step, arrow, variant = raw.partition("→")
        if arrow and step.strip():
            return f"для шага «{step_title(step.strip(), km)}» выбран вариант промта «{variant.strip()}»"
        return "выбран вариант промта: " + humanize_reply(raw, km)
    if "set_text_llm" in entry:
        return "текстовая модель: " + str(entry["set_text_llm"])
    if "hitl_decision" in entry:
        head = str(entry["hitl_decision"]).split("→")[0].strip()
        return "проверка человеком: " + _HITL_DONE_RU.get(head, head)
    if "set_topic" in entry:
        return "тема проекта: " + str(entry["set_topic"])
    if "create_project" in entry:
        return "создан проект " + str(entry["create_project"])
    if "create_child" in entry:
        return "создан дочерний проект " + str(entry["create_child"]).split("←")[0].strip()
    if "run_harness" in entry:
        v = str(entry["run_harness"])
        return "автопроверка: всё в порядке" if v == "ок" else "автопроверка: есть замечания"
    for k, prefix in (
        ("add_node", "нода: "),
        ("connect_edges", "связи: "),
        ("repair_graph", "граф: "),
        ("rename_node", "переименовано: "),
    ):
        if k in entry:
            text = humanize_reply(str(entry[k]), km)
            text = re.sub(r"\bstorage\b", "хранилище", text)
            return prefix + text
    for k, prefix in (
        ("read_file", "код: "),
        ("edit_files", "код изменён: "),
        ("run_tests", "тесты: "),
        ("git_commit_push", "изменения отправлены: "),
    ):
        if k in entry:
            return prefix + str(entry[k]).split("\n")[0][:200]
    return ""


_ERR_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"неизвестный шаг|unknown step code", re.I), "такого шага в проекте нет"),
    (re.compile(r"нет ожидающих HITL", re.I), "сейчас ничего не ждёт проверки человеком"),
    (re.compile(r"неизвестное значение|unknown value", re.I), "у этой настройки нет такого значения"),
    (re.compile(r"неизвестный ключ|unknown key", re.I), "такой настройки нет"),
    (re.compile(r"неизвестный node_key|неизвестный тип ноды", re.I), "такой ноды на канвасе нет"),
    (re.compile(r"нет промтов/студии|это настройка", re.I), "у этой ноды нет окна промтов — это настройка"),
    (re.compile(r"неизвестное действие", re.I), "такого действия у агента нет"),
)


def human_error(raw: object, keymap: dict | None = None) -> str:
    """Ошибка действия для человека: без списков допустимых значений, ключей и английского."""
    s = str(raw or "").strip()
    for rx, text in _ERR_RULES:
        if rx.search(s):
            return text
    if not re.search(r"[А-Яа-яЁё]", s):
        return "действие не выполнилось (подробности в журнале проекта)"
    s = re.sub(r";\s*(?:разрешены|допустимые|есть|ключи|kinds?)\b[^;]*", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"^[a-z_]+(?:\s+[a-z_]+)?:\s*", "", s)
    return humanize_reply(s, keymap or {})[:300] or "действие не выполнилось"
