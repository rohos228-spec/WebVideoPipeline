"""Массовый запуск: какие проекты из списка упрутся в жёсткое условие запуска.

«Запусти проекты 3..12» — один проект без данных сцены не должен держать девять готовых.
Здесь условия действия (те же ``preconditions`` реестра, что проверит карточка и executor)
прогоняются по каждому проекту отдельно; супервизор убирает отказавшие из шага до
карточки и пишет человеку, кого и почему не поставили. Сами условия не ослабляются:
оставшиеся проекты карточка и executor проверят ещё раз, как обычно.

Многоарендность: проверка идёт от имени личности задачи (``ActionContext`` берёт её из
контекста, роль не повышается) и только по своим проектам арендатора задачи
(``orch_tenancy.own_projects``: RLS на Postgres и сверка арендатора строки). Номер чужого
или несуществующего проекта не проверяется и не попадает в список отказов: он остаётся
в шаге, и карточка откажет по нему так же, как по любому неизвестному номеру, — без
названия, статуса и причин чужого проекта. В режиме учётных записей без личности
проверять нечем — ``None``.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

# Действия массового запуска: список проектов в одном шаге или шаг на проект.
QUEUE_ACTION = "queue.enqueue_many"
STEP_ACTION = "step.run_step"
SPLIT_ACTIONS = frozenset({QUEUE_ACTION, STEP_ACTION})
# Проверка повторов в списке — не про отдельный проект.
_LIST_ONLY = frozenset({"project_ids_unique"})
_SCENE_PRE = "img_pr_scene_input"
_LEAD_PID = re.compile(r"^\s*(?:у |в )?(?:проект[ае]?|проекту)\s*#\d+\s*(?:[-—:,]\s*)?", re.IGNORECASE)


def short_reason(reason: str) -> str:
    """«у проекта #10 выключено …» → «выключено …»: номер уже стоит перед причиной."""
    text = _LEAD_PID.sub("", str(reason or "").strip()).strip()
    return (text or str(reason or "").strip()).rstrip(".")


# Технические условия запуска → коротко по-человечески (без имён параметров).
_KNOWN: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), text)
    for pat, text in (
        (
            r"enable_auto_mode|выключено автопродвижение",
            "выключено автопродвижение, а очередь ведёт только проекты с ним",
        ),
        (
            r"allow_auto_chain|включено автопродвижение",
            "включено автопродвижение: после этого шага Студия сама запустит следующие, платные тоже",
        ),
        (
            r"actions-v1|управляем\w* исполнени",
            "проект ведёт управляемое исполнение, вручную его сейчас не запустить",
        ),
        (r"без данных сцены", "кадры без данных сцены"),
        (r"дочерний проект фабрики", "дочерний проект фабрики, очередь его пропускает"),
        (r"preempt_running|выполняется шаг", "в проекте сейчас идёт шаг, дождитесь его окончания"),
        (r"нет ноды", "в схеме проекта нет нужной ноды"),
        (r"until_node|target_node", "не указано, до какой ноды вести проект"),
        (r"node_key", "не указано, какую ноду запускать"),
        (r"ceiling_credits|потолок", "не хватает лимита кредитов на запуск"),
    )
)
# Имя параметра или служебное слово латиницей: «enable_auto_mode=true», «status running».
_TECH = re.compile(r"[A-Za-z]+_[A-Za-z0-9_]*|\w+=\S*|[a-z]{3,}")
_PARENS = re.compile(r"\s*\([^)]*[A-Za-z_=][^)]*\)")
GENERIC_REASON = "не выполнено условие запуска"


def _plain(text: str) -> str:
    """Убрать из причины всё техническое: скобки с латиницей и части фразы с именами параметров."""
    text = _PARENS.sub("", text)
    parts = [part.strip() for part in re.split(r"[:;.]\s+|,\s+(?:или|либо|а)\s+", text)]
    keep: list[str] = []
    for part in parts:
        if not part or _TECH.search(part):
            break
        keep.append(part)
    return ": ".join(keep).strip(" ,:;.")


def human_reason(reason: str, *, queue: bool) -> str:
    """Причина отказа для заметки «не поставлены в очередь / не запущены» — по-русски, без кода."""
    short = short_reason(reason)
    for pattern, text in _KNOWN:
        if pattern.search(short):
            return text
    head = "проект сейчас нельзя поставить в очередь" if queue else "проект сейчас нельзя запустить"
    return f"{head}: {_plain(short) or GENERIC_REASON}"


async def _scene_short(ctx: Any, pid: int) -> str:
    from app.services.action_layer import builtin_launch as bl
    from app.services.action_layer import img_pr_guard as ipg

    meta = (await bl._get_project(ctx, pid)).get("meta")
    return ipg.no_scene_short(meta, await bl._node_runs(ctx, pid))


async def _first_failure(spec: Any, ctx: Any, params: dict[str, Any]) -> tuple[str, str] | None:
    for pre in spec.preconditions:
        if pre.id in _LIST_ONLY:
            continue
        try:
            reason = await pre.check(ctx, params)
        except Exception:  # noqa: BLE001
            # Сбой проверки (например, база на миг недоступна) — не отказ: проект остаётся в
            # шаге, карточка и executor проверят его сами и заблокируют, если нужно.
            logger.exception("launch split: precondition {} not checked", pre.id)
            continue
        if reason:
            return pre.id, str(reason)
    return None


async def project_blockers(
    ctx: Any, spec: Any, params: dict[str, Any], visible: set[int] | None = None
) -> list[dict[str, Any]]:
    """Отказы по проектам шага: [{"project_id", "check", "reason", "reason_ru"}].

    ``visible`` — номера своих проектов арендатора: остальные не проверяются вовсе.
    """
    if spec.id == QUEUE_ACTION:
        ids = [pid for pid in params.get("project_ids") or [] if isinstance(pid, int)]
        items = [(pid, {**params, "project_ids": [pid]}) for pid in ids]
    elif spec.id == STEP_ACTION and isinstance(params.get("project_id"), int):
        items = [(params["project_id"], dict(params))]
    else:
        return []
    if visible is not None:
        items = [(pid, one) for pid, one in items if pid in visible]
    out: list[dict[str, Any]] = []
    for pid, one in items:
        failed = await _first_failure(spec, ctx, one)
        if failed is None:
            continue
        check, reason = failed
        short = human_reason(reason, queue=spec.id == QUEUE_ACTION)
        if check == _SCENE_PRE:
            short = short_reason(reason)
            try:
                short = await _scene_short(ctx, pid)
            except Exception:  # noqa: BLE001
                logger.exception("launch split: scene reason for #{} not built", pid)
        out.append({"project_id": pid, "check": check, "reason": reason, "reason_ru": short})
    return out


def _step_ids(action: str, params: dict[str, Any]) -> list[int]:
    if action == QUEUE_ACTION:
        raw = params.get("project_ids") or []
    else:
        raw = [params.get("project_id")]
    return [pid for pid in raw if isinstance(pid, int) and not isinstance(pid, bool)]


async def _own_ids(ids: list[int]) -> set[int]:
    """Номера из ``ids``, которые арендатор задачи видит как свои (чужие и несуществующие — нет)."""
    import asyncio

    from app.services.orch_tenancy import own_projects
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    found = await asyncio.to_thread(own_projects, tenant, list(ids))
    return {int(pid) for pid, _title in found}


async def launch_blockers(name: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """{"blocked": [...]} для шага массового запуска; None — не тот шаг или проверить нечем."""
    if str(name) not in SPLIT_ACTIONS:
        return None
    from app.orchestrator import app_ref
    from app.services.action_layer.context import ActionContext
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    spec = registry.find(str(name))
    if spec is None:
        return None
    if registry.validate_params(spec.id, dict(params or {})):
        return None  # неверные параметры: пусть карточка скажет о них сама
    from app.services.studio_auth import current_identity
    from app.settings import settings

    ident = current_identity()
    if settings.accounts_enabled and ident is None:
        return None  # личности задачи нет: проверка от чужого имени не делается
    try:
        visible = await _own_ids(_step_ids(spec.id, dict(params or {})))
        # Оператор — личность задачи: внутренний вызов несёт её токен и её арендатора.
        operator = f"studio_user:{ident.user_id}" if ident is not None else "system"
        ctx = ActionContext(app=app_ref.current(), operator=operator, read_only=True)
        blocked = await project_blockers(ctx, spec, dict(params or {}), visible)
    except Exception:  # noqa: BLE001
        logger.exception("launch split: blockers for {} not checked", name)
        return None
    return {"blocked": blocked}
