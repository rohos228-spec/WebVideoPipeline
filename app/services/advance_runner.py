"""Запуск advance_project в отдельной asyncio-task — снимается через ⏹.

Здесь же врезана касса. Место выбрано не случайно: это единственная точка,
через которую проходит ЛЮБОЕ исполнение шага — и автоматический такт воркера,
и ручной запуск из канваса, и повтор после HITL. Обернуть кассой сам шаг
значило бы обернуть двадцать с лишним обработчиков и не забыть ни одного;
обернуть вызывающих — значило бы разложить одно и то же условие по трём
файлам. Резерв ставится до такта, списание идёт после, падение снимает
резерв целиком (`docs/SAAS-PIVOT.md` §5.4).

В режиме владельца касса не делает ничего: арендатора нет, кредитов нет,
владелец платит провайдерам напрямую. Условие проверяется внутри
`step_billing`, то есть такт выглядит ровно так же, как выглядел.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.db import session_scope
from app.models import Project
from app.orchestrator.node_registry import step_code_of_running_status
from app.orchestrator.pipeline import advance_project
from app.services.credit_ledger import InsufficientCredits
from app.services.free_tier import FreeTierExhausted
from app.services.run_sync import complete_active_node_for_step
from app.services.step_billing import step_billing


@dataclass(frozen=True)
class AdvanceJobResult:
    project_id: int
    prev_status: str
    new_status: str | None  # None если статус не изменился


async def advance_project_job(project_id: int, bot: Any = None) -> AdvanceJobResult:
    """Один такт advance_project в своей сессии (for asyncio.create_task)."""
    try:
        # Проект читается ДО транзакции такта: смету надо посчитать и резерв
        # поставить раньше, чем шаг начнёт тратить. Атрибуты остаются
        # доступными после выхода — `expire_on_commit=False`.
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                logger.warning("advance_project_job: проект #{} не найден", project_id)
                return AdvanceJobResult(project_id, "", None)
            prev = project.status.value
            prev_status = project.status
        step_code = step_code_of_running_status(prev_status)

        try:
            # Резерв ставится на входе в блок, списание — на выходе, падение
            # внутри снимает резерв целиком (§5.4 п.5). Отсюда обычный
            # `async with`, а не ручные `__aenter__`/`__aexit__`: любой
            # ранний `return` из тела иначе оставил бы резерв висеть до
            # истечения срока.
            async with step_billing(project, step_code or "", node_key=step_code or ""):
                async with session_scope() as session:
                    project = await session.get(Project, project_id)
                    if project is None:
                        logger.warning("advance_project_job: проект #{} исчез", project_id)
                        return AdvanceJobResult(project_id, prev, None)
                    from app.services import request_context, spend_guard

                    # Цепочка из чата с потолком: такт пишет траты в её run_id и упирается в её
                    # потолок. Управляемый запуск считает траты по своему run_id — его не трогаем.
                    run_id = request_context.get_run_id()
                    chain_meta = project.meta if not run_id or run_id.startswith("native:") else None
                    from app.services.tenant import tenant_scope

                    project_tenant = str(project.tenant_id) if getattr(project, "tenant_id", None) else None
                    with tenant_scope(project_tenant):
                        with spend_guard.chain_budget_scope(chain_meta):
                            await advance_project(session, project, bot)
                    new = project.status.value
                    if new != prev:
                        _NO_CREDITS_FIRST_SEEN.pop(project_id, None)
                        _NO_CREDITS_LOGGED.pop(project_id, None)
                        await complete_active_node_for_step(
                            session,
                            project,
                            prev_status=prev_status,
                            new_status=project.status,
                        )
                        await session.commit()
                        logger.debug("advance_project_job: #{} {} -> {}", project_id, prev, new)
                        await _publish_artifacts(session, project_id)
                        return AdvanceJobResult(project_id, prev, new)
                    return AdvanceJobResult(project_id, prev, None)
        except (InsufficientCredits, FreeTierExhausted) as exc:
            # Не ошибка шага, а отсутствие денег: проект ждёт пополнения.
            # Если пополнения нет слишком долго (>90с), проект останавливается,
            # чтобы нода на холсте не висела в бесконечном ожидании.
            now = time.monotonic()
            first_seen = _NO_CREDITS_FIRST_SEEN.setdefault(project_id, now)
            await _report_no_credits(project_id, step_code or "", exc)

            if now - first_seen >= NO_CREDITS_AUTO_STOP_SEC:
                _NO_CREDITS_FIRST_SEEN.pop(project_id, None)
                _NO_CREDITS_LOGGED.pop(project_id, None)
                logger.warning(
                    "касса: #{} шаг {} ждёт средств более {}с — автоостановка",
                    project_id,
                    step_code,
                    int(NO_CREDITS_AUTO_STOP_SEC),
                )
                from app.services.project_control import is_running_status, stop_project_running

                async with session_scope() as stop_session:
                    p_stop = await stop_session.get(Project, project_id)
                    if p_stop is not None and is_running_status(p_stop.status):
                        meta = dict(p_stop.meta or {})
                        meta["credits_wait_timeout"] = True
                        meta["credits_stop_reason"] = str(exc)
                        p_stop.meta = meta
                        await stop_project_running(stop_session, p_stop)
                        await stop_session.commit()
                        logger.info(
                            "касса: #{} автоостановлен из-за нехватки средств ({} -> {})",
                            project_id,
                            prev,
                            p_stop.status.value,
                        )
                        return AdvanceJobResult(project_id, prev, p_stop.status.value)
            return AdvanceJobResult(project_id, prev, None)
    except asyncio.CancelledError:
        _NO_CREDITS_FIRST_SEEN.pop(project_id, None)
        logger.info("advance_project_job: #{} hard-cancelled (⏹)", project_id)
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is not None:
                    await session.refresh(project)
        except Exception:  # noqa: BLE001
            logger.warning("advance_project_job: refresh #{} after cancel failed", project_id)
        raise
    finally:
        # Транзакция шага закрыта — только теперь SQLite пускает чужой writer.
        # Учёт платных вызовов, отбитый на `database is locked` во время шага,
        # дозаписывается здесь: деньги ушли, строка потеряться не должна.
        await _flush_ledgers()


#: Сколько секунд ждать пополнения перед автоматической остановкой шага.
#: Если за 90 секунд баланс не пополнен, статус running откатывается назад,
#: чтобы нода на холсте не крутилась бесконечно.
NO_CREDITS_AUTO_STOP_SEC = 90.0
_NO_CREDITS_FIRST_SEEN: dict[int, float] = {}

#: Когда по проекту последний раз жаловались на нехватку кредитов. Воркер
#: тикает каждые пять секунд, и без глушилки один непополненный аккаунт даёт
#: семьсот строк в час — в таком журнале не видно ничего другого.
_NO_CREDITS_LOGGED: dict[int, float] = {}
_NO_CREDITS_QUIET_SEC = 300.0


async def _report_no_credits(project_id: int, step_code: str, exc: Exception) -> None:
    """Сказать наружу, что проект ждёт денег, а не сломался.

    Без этого ожидание пополнения неотличимо от зависшего проекта: воркер
    тикает, статус не меняется, интерфейс молчит. Клиент видит остановившуюся
    работу и идёт жаловаться, хотя нужно было нажать «пополнить».

    Глушилка на том же счётчике, что и журнал: воркер тикает каждые пять
    секунд, и событие на каждый тик — это не сигнал, а шум, который перестают
    замечать.
    """
    now = time.monotonic()
    last = _NO_CREDITS_LOGGED.get(project_id, 0.0)
    if now - last < _NO_CREDITS_QUIET_SEC:
        return
    _NO_CREDITS_LOGGED[project_id] = now
    logger.info("касса: #{} шаг {} ждёт пополнения — {}", project_id, step_code, exc)
    try:
        from app.services.event_bus import publish_project_event

        await publish_project_event(
            project_id,
            event_type="credits_required",
            payload={"step_code": step_code, "reason": str(exc)},
        )
    except Exception:  # noqa: BLE001 — молчание шины не должно ронять такт
        logger.debug("касса: событие credits_required не отправлено", exc_info=True)


async def _publish_artifacts(session, project_id: int) -> None:
    """Результат шага — в объектное хранилище. Одно место на все шаги.

    Публикация не должна ронять такт: ролик уже сгенерирован и уже оплачен,
    и уронить шаг из-за недоступного бакета значит списать деньги и не отдать
    результат. Следующий шаг подхватит неопубликованное.
    """
    from app.services.artifact_storage import publish_project_artifacts

    try:
        await publish_project_artifacts(session, project_id)
    except Exception:  # noqa: BLE001
        logger.warning("хранилище: публикация артефактов #{} не удалась", project_id, exc_info=True)


async def _flush_ledgers() -> None:
    from app.services import llm_ledger, media_ledger

    for ledger in (llm_ledger, media_ledger):
        try:
            await ledger.flush_pending()
        except Exception:  # noqa: BLE001 — дозапись учёта не валит такт воркера
            logger.warning("advance_project_job: дозапись учёта не удалась", exc_info=True)
