"""Инструменты агента: тонкие обёртки над тем, что уже умеет конвейер.

Набор из `docs/SAAS-PIVOT.md` §8.3. Каждый инструмент — это имя, описание для
модели, схема аргументов и обработчик. Обработчик не содержит логики
конвейера: он зовёт существующий сервис и переводит ответ в то, из чего чат
рисует карточку.

**Почему обёртки, а не своя логика.** §8.2: агент — дирижёр, а не автор
конвейера. Как только у инструмента появляется собственное представление о
том, что за чем идёт, оно начинает расходиться с конечным автоматом
оркестратора — и расходиться молча, потому что обе стороны «работают».

**Три вещи, которые инструмент обязан делать, и все три про деньги.**

1. *Цену показывать до, а не после.* `runStep` возвращает смету вместе с
   подтверждением запуска, а не «запущено, узнаете в конце».
2. *Дорогое спрашивать.* Порог подтверждения — 1 кредит (§7.3). Всё дешевле
   выполняется сразу, дороже — требует `confirm=true`. Порог проверяется
   здесь, а не в интерфейсе: интерфейсов будет несколько, а касса одна.
3. *Радиус показывать вместе с ценой.* «Переделать раскадровку» — это не
   один шаг, а каскад: пользователь должен видеть, что именно сгорит.

**Ошибки инструментов — это `ToolError`, а не исключения наружу.** Модель
получает текст ошибки и может объяснить его человеку или попробовать иначе;
пятисотка в чате не объясняет ничего.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

#: Порог обязательного подтверждения, микрокредиты (§7.3 п.3). Всё дешевле
#: выполняется сразу: спрашивать про 0.01 кредита значит приучить нажимать
#: «да», не читая, — и тогда подтверждение не сработает там, где важно.
CONFIRM_THRESHOLD_MICRO: int = 1_000_000


class ToolError(RuntimeError):
    """Инструмент не смог. Текст уходит модели, а не пользователю напрямую."""


@dataclass(frozen=True)
class Tool:
    """Что модель видит и что выполняется."""

    name: str
    description: str
    #: JSON Schema аргументов — то же описание уходит и в промт, и в проверку.
    args: dict[str, Any] = field(default_factory=dict)
    #: Инструмент меняет состояние или только читает. Читающие можно звать
    #: свободно; меняющие проходят через порог подтверждения.
    mutating: bool = False


TOOLS: dict[str, Tool] = {
    "createProject": Tool(
        name="createProject",
        description=(
            "Завести новый проект ролика. Аргумент title — тема одной строкой, "
            "как её сформулировал человек. Возвращает id проекта."
        ),
        args={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "тема ролика"},
                "hero_mode": {
                    "type": "string",
                    "enum": ["no_hero", "hero"],
                    "description": "есть ли сквозной герой",
                },
            },
            "required": ["title"],
        },
        mutating=True,
    ),
    "estimateStep": Tool(
        name="estimateStep",
        description=(
            "Сколько стоит шаг. cascade=true — сколько стоит переделать шаг "
            "вместе со всем, что от него зависит (радиус поражения). "
            "Зови ПЕРЕД runStep на дорогих шагах."
        ),
        args={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "step_code": {"type": "string", "description": "код шага, например video"},
                "cascade": {"type": "boolean"},
            },
            "required": ["project_id", "step_code"],
        },
    ),
    "runStep": Tool(
        name="runStep",
        description=(
            "Запустить шаг. Порядок шагов задан конвейером — запустить можно "
            "только тот, который разрешён сейчас. Дороже 1 кредита требует "
            "confirm=true: сначала покажи цену человеку и спроси."
        ),
        args={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "step_code": {"type": "string"},
                "confirm": {"type": "boolean", "description": "человек согласился с ценой"},
            },
            "required": ["project_id", "step_code"],
        },
        mutating=True,
    ),
    "showStoryboard": Tool(
        name="showStoryboard",
        description=(
            "Лента кадров проекта: номер, статус, закадровый текст, промт "
            "картинки. То, что человек видит как контактный лист."
        ),
        args={
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"],
        },
    ),
    "editFramePrompt": Tool(
        name="editFramePrompt",
        description=(
            "Поменять промт одного кадра. Сам по себе ничего не перерисовывает "
            "— после правки зови regenerateFrame."
        ),
        args={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "frame_number": {"type": "integer"},
                "image_prompt": {"type": "string"},
            },
            "required": ["project_id", "frame_number", "image_prompt"],
        },
        mutating=True,
    ),
    "regenerateFrame": Tool(
        name="regenerateFrame",
        description=(
            "Перерисовать один кадр (what=image) или переснять один клип "
            "(what=video). Картинка в 54 раза дешевле клипа — итерации веди "
            "на картинках."
        ),
        args={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "frame_number": {"type": "integer"},
                "what": {"type": "string", "enum": ["image", "video"]},
            },
            "required": ["project_id", "frame_number"],
        },
        mutating=True,
    ),
    "approveStage": Tool(
        name="approveStage",
        description="Утвердить карточку HITL — снимает блокировку и пускает конвейер дальше.",
        args={
            "type": "object",
            "properties": {
                "hitl_id": {"type": "integer"},
                "decision": {"type": "string", "enum": ["approve", "reject"]},
            },
            "required": ["hitl_id"],
        },
        mutating=True,
    ),
    "showBalance": Tool(
        name="showBalance",
        description="Остаток кредитов, сумма резервов под идущими шагами и последние проводки.",
        args={"type": "object", "properties": {}},
    ),
}


def tool_manifest() -> list[dict[str, Any]]:
    """Описание инструментов для промта. Один источник — словарь выше."""
    return [{"name": t.name, "description": t.description, "args": t.args} for t in TOOLS.values()]


async def call_tool(session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Выполнить инструмент. Возвращает то, из чего чат рисует карточку."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"инструмента {name!r} не существует; доступны: {', '.join(TOOLS)}")
    handler = _HANDLERS.get(name)
    if handler is None:  # pragma: no cover — реестр и обработчики рядом
        raise ToolError(f"инструмент {name!r} объявлен, но не реализован")
    logger.info("агент: инструмент {} args={}", name, args)
    return await handler(session, args or {})


# ── обработчики ─────────────────────────────────────────────────────────────


async def _create_project(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    from app.web.routers.projects import CreateProjectRequest, create_project

    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("нужна тема ролика")
    body = CreateProjectRequest(title=title, hero_mode=str(args.get("hero_mode") or "no_hero"))
    detail = await create_project(body, session)
    return {"project_id": detail.id, "slug": detail.slug, "title": detail.title}


async def _estimate_step(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    from app.web.routers.billing import quote

    project_id = _int(args, "project_id")
    step_code = _str(args, "step_code")
    result = await quote(project_id, step_code, bool(args.get("cascade")), session)
    return result.model_dump()


async def _run_step(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    from app.models import Project
    from app.orchestrator.node_registry import spec_for_step_code
    from app.services.credits import format_credits
    from app.services.project_steps import start_step
    from app.web.routers.billing import quote

    project_id = _int(args, "project_id")
    step_code = _str(args, "step_code")
    project = await session.get(Project, project_id)
    if project is None:
        raise ToolError(f"проекта #{project_id} нет")

    spec = spec_for_step_code(step_code)
    if spec is None:
        raise ToolError(f"шага {step_code!r} в конвейере нет")
    _assert_step_is_reachable(project, step_code)

    price = await quote(project_id, step_code, False, session)
    if price.price_micro > CONFIRM_THRESHOLD_MICRO and not args.get("confirm"):
        # Не отказ, а требование спросить человека. Модель получит цену и
        # обязана показать её прежде, чем звать инструмент снова.
        return {
            "needs_confirmation": True,
            "step_code": step_code,
            "price_credits": price.price_credits,
            "reason": (
                f"шаг стоит {price.price_credits} кр — дороже порога "
                f"{format_credits(CONFIRM_THRESHOLD_MICRO)} кр. Покажи цену человеку "
                "и позови снова с confirm=true, если он согласен."
            ),
        }

    try:
        # `explicit_ui_start` НЕ передаётся намеренно. Этот флаг — право
        # оператора у своего канваса: он снимает очередь, убивает чужой
        # идущий шаг и стартует что угодно поверх. Агенту такого права не
        # дано (§8.2): порядок задан конечным автоматом, а не разговором.
        status = await start_step(session, project, step_code)
    except Exception as exc:  # noqa: BLE001 — текст уйдёт модели, а не в 500
        raise ToolError(f"шаг {step_code} сейчас запустить нельзя: {exc}") from exc
    await session.commit()
    return {
        "started": True,
        "step_code": step_code,
        "status": status.value if hasattr(status, "value") else str(status),
        "price_credits": price.price_credits,
    }


def _assert_step_is_reachable(project: Any, step_code: str) -> None:
    """Дошёл ли проект до этого шага. Иначе агент строил бы конвейер сам.

    `start_step` этого не проверяет и правильно делает: у оператора за своим
    канвасом есть право запустить любой шаг вручную. Агенту такого права не
    дано — §8.2 прямо запрещает ему решать, что за чем идёт, потому что
    именно так пять дефектов подряд из `SHOTS-FIX` §11 и получились:
    требование адресовали модели и не проверили, что оно доехало.

    Проверка та же, по которой рисуется меню шагов: у шага объявлен
    prerequisite-статус, и проект обязан быть на нём или дальше.
    """
    from app.telegram.menu import status_order, step_by_code

    step = step_by_code(step_code)
    if step is None or step.requires is None:
        return  # у шага нет предусловия — запускается когда угодно
    if status_order(project.status) < status_order(step.requires):
        raise ToolError(
            f"шаг {step_code} ещё недоступен: проект в статусе "
            f"{project.status.value}, а нужен {step.requires.value} или дальше. "
            "Сначала пройди предыдущие шаги."
        )


async def _show_storyboard(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models import Frame

    project_id = _int(args, "project_id")
    rows = (
        (await session.execute(select(Frame).where(Frame.project_id == project_id).order_by(Frame.number)))
        .scalars()
        .all()
    )
    return {
        "project_id": project_id,
        "frames": [
            {
                "number": f.number,
                "status": f.status.value if hasattr(f.status, "value") else str(f.status),
                "voiceover": (f.voiceover_text or "")[:200],
                "image_prompt": (getattr(f, "image_prompt", "") or "")[:400],
            }
            for f in rows
        ],
    }


async def _edit_frame_prompt(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    frame = await _frame(session, args)
    prompt = str(args.get("image_prompt") or "").strip()
    if not prompt:
        raise ToolError("пустой промт — нечего сохранять")
    frame.image_prompt = prompt
    await session.commit()
    return {"frame_number": frame.number, "saved": True}


async def _regenerate_frame(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    from app.models import FrameStatus

    frame = await _frame(session, args)
    what = str(args.get("what") or "image").lower()
    if what not in ("image", "video"):
        raise ToolError("what может быть только image или video")
    # Точечная перегенерация делается откатом статуса кадра — ровно тем же
    # способом, каким её делает HITL-карточка (`hitl_apply.py`). Шаг доделает
    # только откатившиеся кадры, остальные не тронет.
    frame.status = FrameStatus.image_prompt_ready if what == "image" else FrameStatus.animation_prompt_ready
    await session.commit()
    return {"frame_number": frame.number, "reset_to": frame.status.value, "what": what}


async def _approve_stage(session: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Решение по карточке уходит в тот же обработчик, что и кнопка в UI.

    Не «поставить статус и позвать side effects» своими руками: у роутера
    есть проверка «уже решено», публикация события и разбор синонимов
    решения. Продублировать это здесь значит завести вторую полуправду о
    том, что происходит при утверждении.
    """
    from fastapi import HTTPException

    from app.web.routers.hitl import submit_decision
    from app.web.schemas import HITLDecisionRequest

    hitl_id = _int(args, "hitl_id")
    decision = str(args.get("decision") or "approve").lower()
    if decision not in ("approve", "reject"):
        raise ToolError("decision может быть только approve или reject")
    try:
        req = await submit_decision(hitl_id, HITLDecisionRequest(decision=decision), session)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc
    return {
        "hitl_id": hitl_id,
        "decision": req.decision.value,
        "kind": req.kind.value,
        "project_id": req.project_id,
    }


async def _show_balance(session: Any, _args: dict[str, Any]) -> dict[str, Any]:
    from app.web.routers.billing import balance

    return (await balance(20, session)).model_dump()


async def _frame(session: Any, args: dict[str, Any]):
    from sqlalchemy import select

    from app.models import Frame

    project_id = _int(args, "project_id")
    number = _int(args, "frame_number")
    frame = (
        await session.execute(select(Frame).where(Frame.project_id == project_id, Frame.number == number))
    ).scalar_one_or_none()
    if frame is None:
        raise ToolError(f"кадра №{number} в проекте #{project_id} нет")
    return frame


def _int(args: dict[str, Any], key: str) -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"нужен целочисленный аргумент {key}") from exc


def _str(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ToolError(f"нужен аргумент {key}")
    return value


_HANDLERS = {
    "createProject": _create_project,
    "estimateStep": _estimate_step,
    "runStep": _run_step,
    "showStoryboard": _show_storyboard,
    "editFramePrompt": _edit_frame_prompt,
    "regenerateFrame": _regenerate_frame,
    "approveStage": _approve_stage,
    "showBalance": _show_balance,
}
