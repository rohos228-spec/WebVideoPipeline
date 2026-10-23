"""Очередь генерации проектов по сайдбару (top-N окно).

При WORKER_MAX_PARALLEL=1 — строго по одному (как раньше).
При N>1 — одновременно выполняются до N runnable слотов.
paused/user_stop пропускаются (не занимают окно) — хвост может работать,
если есть свободные слоты параллели.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.auto_advance import TRANSITIONS
from app.orchestrator.graph.planner import load_graph_for_project
from app.services.gen_queue_run import (
    CHAT_OWNER_KEY,
    gen_queue_slot_skipped,
    is_gen_queue_run_complete,
    is_gen_queue_timeline_complete,
    mark_gen_queue_run_complete,
    skip_gen_queue_slot,
)
from app.services.mass_factory import is_mass_factory_child
from app.services.sidebar_layout import get_gen_queue, is_gen_queue_halted
from app.services.step_registry import running_statuses_list

GEN_QUEUE_BUSY_STATUSES = running_statuses_list()


def _slot_blocked(project: Project) -> bool:
    return bool(project.status is ProjectStatus.paused or _user_stop_blocks_queue(project))


def _slot_closed(project: Project) -> bool:
    return is_gen_queue_run_complete(project) or gen_queue_slot_skipped(project) is not None


def worker_max_parallel() -> int:
    """Сколько проектов очереди могут выполняться одновременно (1..4).

    Источник: data/runtime_streams.json → env WORKER_MAX_PARALLEL → 1.
    """
    from app.services.runtime_streams import get_worker_max_parallel

    return get_worker_max_parallel()


async def own_project_ids(session: AsyncSession, ids: list[int] | None = None) -> set[int] | None:
    """Проекты арендатора запроса; ``None`` — режим владельца (видно всё).

    Файл очереди один на студию. Номера и места, которые видит клиент, считаются
    только среди его проектов: место в общей очереди выдавало бы, сколько чужих
    проектов стоит перед ним. Строки видны под RLS (Postgres) и ещё раз
    сверяются с арендатором строки (на SQLite политик нет). ``ids`` — проверить
    только эти номера.
    """
    from app.services.tenant import current_tenant

    tenant = current_tenant()
    if tenant is None:
        return None
    q = select(Project.id, Project.tenant_id)
    if ids is not None:
        if not ids:
            return set()
        q = q.where(Project.id.in_(sorted({int(i) for i in ids})))
    rows = (await session.execute(q)).all()
    return {int(pid) for pid, owner in rows if owner is not None and str(owner) == str(tenant)}


async def own_gen_queue(session: AsyncSession, queue: list[int] | None = None) -> list[int]:
    """Очередь, какой её видит автор запроса: только свои номера, в общем порядке.

    Место проекта для клиента — ``index + 1`` в этом списке. Владелец видит всю очередь.
    """
    full = list(get_gen_queue() if queue is None else queue)
    own = await own_project_ids(session, full)
    if own is None:
        return full
    return [pid for pid in full if pid in own]


def gen_queue_is_active() -> bool:
    return bool(get_gen_queue())


def project_gated_by_gen_queue(project_id: int) -> bool:
    """Очередь непуста, а проект не в ней — пайплайн не трогаем (только ручной ▶)."""
    queue = get_gen_queue()
    return bool(queue) and project_id not in queue


def enrich_ready_bypasses_gen_queue(project: Project) -> bool:
    """enrich_N_ready + на канвасе есть следующий незавершённый excel_gpt.

    Ручной ▶ одной ноды обходит очередь, но auto_advance после ready
    снова упирался в gen_queue — слот 3 не стартовал. Цепочку excel_gpt
    пропускаем через gate.
    """
    from app.services.excel_gpt_node import (
        next_incomplete_excel_gpt_slot,
        slot_from_ready_status,
    )

    slot = slot_from_ready_status(project.status)
    if slot is None:
        return False
    return next_incomplete_excel_gpt_slot(project, slot) is not None


async def on_project_removed_from_gen_queue(
    session: AsyncSession,
    project: Project,
) -> None:
    """Снятие номера очереди: стоп running + user_stop (иначе script_ready → auto_advance)."""
    from app.services.project_control import _set_user_stop_gate, stop_project_running
    from app.services.step_cancel import request_stop

    if project.status in GEN_QUEUE_BUSY_STATUSES:
        request_stop(project.id)
        await stop_project_running(session, project)
        logger.info(
            "[#{}] gen_queue: снят с очереди — остановлен {}",
            project.id,
            project.status.value,
        )
        return

    _set_user_stop_gate(project)
    await session.flush()
    logger.info(
        "[#{}] gen_queue: снят с очереди — user_stop ({}), без auto_advance",
        project.id,
        project.status.value,
    )


def chat_queue_owner(project: Project) -> str | bool | None:
    """Кто из чата ставил номер в очередь: id сессии чата, ``True`` (старая метка без сессии) или None."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    run = meta.get("gen_queue_run")
    raw = run.get(CHAT_OWNER_KEY) if isinstance(run, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if raw is True:
        return True
    return None


def queued_by_chat(project: Project, session_id: str | None = None) -> bool:
    """Номер в очереди ставил чат (а не человек кнопкой очереди).

    С ``session_id`` — именно эта сессия чата. Старая метка ``True`` не принадлежит
    ни одной сессии: аварийный стоп её не трогает.
    """
    owner = chat_queue_owner(project)
    if session_id is None:
        return owner is not None
    return isinstance(owner, str) and owner == str(session_id)


def _live_work(project_id: int) -> bool:
    """В проекте идёт живая работа этого процесса (не просто stop-файл)."""
    from app.services.montage_board_montage_job import is_montage_job_live
    from app.services.step_cancel import is_advance_active
    from app.services.xlsx_flow_locks import is_any_xlsx_flow_active

    try:
        return bool(
            is_advance_active(project_id)
            or is_montage_job_live(project_id)
            or is_any_xlsx_flow_active(project_id)
        )
    except Exception:  # noqa: BLE001
        return False


async def dequeue_chat_projects(
    session: AsyncSession,
    project_ids: list[int],
    *,
    session_id: str,
) -> dict[str, list[int]]:
    """Аварийный стоп чата: убрать из очереди генерации проекты, которые туда ставил чат этой сессии.

    * ``removed`` — все убранные из очереди;
    * ``stopping`` — из них те, у кого шаг уже идёт или статус «running»: им
      вызывающий ставит stop-ключ, воркер откатывает шаг и ставит user_stop,
      дальше проект сам не пойдёт;
    * ``kept_user`` — номер в очереди ставил человек или другая сессия чата: не трогаем.

    Ждущим ставится user_stop (как кнопка очереди): auto_advance не двинет
    их до ручного ▶. Метка сохраняется в БД раньше, чем номер снимается с
    очереди: тик очереди, читающий файл очереди, уже не увидит проект без
    метки. Общий стоп очереди не включаем.
    """
    from app.services.gen_queue_run import clear_gen_queue_run
    from app.services.project_control import _set_user_stop_gate
    from app.services.sidebar_layout import set_gen_queue

    queue = get_gen_queue()
    wanted: list[int] = []
    for raw in project_ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid in queue and pid not in wanted:
            wanted.append(pid)
    out: dict[str, list[int]] = {"removed": [], "stopping": [], "kept_user": []}
    for pid in wanted:
        project = await _load_project(session, pid)
        if project is None:
            continue
        if not queued_by_chat(project, session_id):
            out["kept_user"].append(pid)
            continue
        if project.status in GEN_QUEUE_BUSY_STATUSES or _live_work(pid):
            # user_stop здесь не ставим: воркер пропускает user_stop-проекты
            # и не снял бы шаг; stop-ключ он снимет сам и сам поставит user_stop.
            out["stopping"].append(pid)
        else:
            _set_user_stop_gate(project)
        await clear_gen_queue_run(session, project)
        out["removed"].append(pid)
    if out["removed"]:
        await session.commit()
        current = get_gen_queue()
        set_gen_queue([p for p in current if p not in out["removed"]])
        logger.info(
            "gen_queue: аварийный стоп чата — из очереди убраны {} (шаг идёт у {})",
            out["removed"],
            out["stopping"],
        )
    return out


async def recheck_dequeued(
    session: AsyncSession,
    project_ids: list[int],
    *,
    session_id: str | None = None,
    since: float | None = None,
) -> list[int]:
    """После стопа: проект, который тик очереди всё же успел стартовать, снова под стоп.

    Метка user_stop возвращается, если чужая запись её затёрла. Возвращает
    проекты, у которых шаг идёт, — им нужен stop-ключ.

    Проект, который после стопа взял человек, не трогаем: он снова в очереди и
    номер не этой сессии чата, или стоп снят позже ``since`` (ручной ▶,
    «Продолжить», повторная постановка в очередь — от человека или от чата).
    """
    from app.services.project_control import (
        STOP_GATE_CLEARED_AT_KEY,
        USER_STOP_CLEARED_AT_KEY,
        _set_user_stop_gate,
    )

    queue = get_gen_queue()
    busy: list[int] = []
    changed = False
    for raw in project_ids:
        project = await _load_project(session, int(raw))
        if project is None:
            continue
        await session.refresh(project)
        if project.id in queue and not (session_id and queued_by_chat(project, session_id)):
            continue
        meta = project.meta if isinstance(project.meta, dict) else {}
        cleared_at = 0.0
        for key in (USER_STOP_CLEARED_AT_KEY, STOP_GATE_CLEARED_AT_KEY):
            try:
                cleared_at = max(cleared_at, float(meta.get(key) or 0))
            except (TypeError, ValueError):
                continue
        if since is not None and cleared_at > since:
            continue
        if project.status in GEN_QUEUE_BUSY_STATUSES or _live_work(project.id):
            busy.append(project.id)
            continue
        if not _user_stop_blocks_queue(project):
            _set_user_stop_gate(project)
            changed = True
    if changed:
        await session.commit()
    return busy


def _user_stop_blocks_queue(project: Project) -> bool:
    """user_stop блокирует очередь (ручной ▶ снимет gate через start_step)."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(meta.get("user_stop") or meta.get("mass_lane_user_stop"))


async def is_timeline_complete(session: AsyncSession, project: Project) -> bool:
    """True если последний шаг таймлайна завершён и следующего нет."""
    if is_gen_queue_timeline_complete(project):
        return True
    status = project.status
    if status is ProjectStatus.published:
        return True
    if status in (ProjectStatus.failed, ProjectStatus.paused, ProjectStatus.new):
        return False
    if status in GEN_QUEUE_BUSY_STATUSES:
        return False

    graph = await load_graph_for_project(session, project)
    if status in TRANSITIONS:
        return graph.next_running_after_ready(project, status) is None
    if status is ProjectStatus.assembled:
        return graph.next_running_after_ready(project, ProjectStatus.assembled) is None
    return False


async def _load_project(session: AsyncSession, project_id: int) -> Project | None:
    return (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()


async def _close_slot_if_already_at_target(
    session: AsyncSession, project: Project, *, queue_pos: int
) -> bool:
    """Закрыть слот очереди, если цель прогона уже достигнута."""
    if is_gen_queue_run_complete(project):
        return False
    if not await is_timeline_complete(session, project):
        return False
    await mark_gen_queue_run_complete(session, project)
    await session.flush()
    logger.info(
        "gen_queue: #{} уже на цели — слот {} закрыт",
        project.id,
        queue_pos,
    )
    return True


async def _advance_ready_project(session: AsyncSession, project: Project) -> bool:
    """Продвинуть *_ready проект в очереди на следующий шаг."""
    from app.orchestrator.auto_advance import maybe_auto_advance

    if project.status not in TRANSITIONS:
        return False
    if not project.auto_mode:
        return False
    return await maybe_auto_advance(session, project, bot=None, force=True)


async def _start_or_advance_project(session: AsyncSession, project: Project, *, queue_pos: int) -> int:
    """Продвинуть *_ready. new — только ручной ▶ (не автостарт).

    Возвращает 1 если что-то стартовало/продвинулось.
    """
    from app.services.project_control import auto_awaits_manual_start

    if project.status is ProjectStatus.new:
        # Автопродвижение ≠ автостарт: первый шаг только руками.
        logger.info(
            "gen_queue: #{} new — ждём ручной ▶ (queue position {})",
            project.id,
            queue_pos,
        )
        return 0
    if auto_awaits_manual_start(project):
        logger.info(
            "gen_queue: #{} {} — auto_mode ждёт ручной ▶ (queue position {})",
            project.id,
            project.status.value,
            queue_pos,
        )
        return 0
    if project.status in TRANSITIONS:
        if not project.auto_mode:
            return 0
        advanced = await _advance_ready_project(session, project)
        if advanced:
            logger.info(
                "gen_queue: advanced #{} from {} (queue position {})",
                project.id,
                project.status.value,
                queue_pos,
            )
            return 1
    return 0


def _idle_reason_for_project(project: Project, *, position: int) -> dict[str, Any]:
    if _user_stop_blocks_queue(project):
        return {
            "project_id": project.id,
            "position": position,
            "reason": "user_stop",
            "detail": "Остановлен пользователем (⏹)",
        }
    if project.status is ProjectStatus.paused:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "paused",
            "detail": "Проект на паузе",
        }
    if project.status is ProjectStatus.failed:
        fs = (project.meta or {}).get("step_failure") or {}
        err = str(fs.get("last_error") or "ошибка шага")[:120]
        return {
            "project_id": project.id,
            "position": position,
            "reason": "failed",
            "detail": err,
        }
    if project.status is ProjectStatus.new:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "manual_start",
            "detail": "Ожидает ручного ▶ (автостарт отключён)",
        }
    from app.services.project_control import auto_awaits_manual_start

    if auto_awaits_manual_start(project):
        return {
            "project_id": project.id,
            "position": position,
            "reason": "manual_start",
            "detail": "Автопродвижение включено — ждёт ручного ▶",
        }
    if not project.auto_mode:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "auto_mode",
            "detail": "Выключен режим ИИ (auto_mode)",
        }
    return {
        "project_id": project.id,
        "position": position,
        "reason": "status",
        "detail": f"Статус: {project.status.value}",
    }


async def get_gen_queue_idle_info(session: AsyncSession) -> dict[str, Any] | None:
    """Почему очередь стоит (для API/UI). None — очередь пуста или идёт работа.

    ``position`` — место среди своих проектов арендатора (владелец — по всей очереди).
    """
    queue = await own_gen_queue(session)
    if not queue:
        return None
    if await gen_queue_busy_count(session) > 0:
        return None

    for idx, pid in enumerate(queue):
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue
        if _slot_blocked(project):
            # paused/⏹ не считаем простоем очереди — хвост может работать.
            continue
        if project.status is ProjectStatus.failed:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new and not project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new:
            # auto_mode включён, но первый старт только руками
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status in TRANSITIONS and project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        return _idle_reason_for_project(project, position=idx + 1)
    return None


async def gen_queue_head_project(session: AsyncSession) -> Project | None:
    """Первый незакрытый слот очереди (может быть paused/user_stop)."""
    window = await gen_queue_window_projects(session)
    return window[0] if window else None


async def gen_queue_window_projects(session: AsyncSession) -> list[Project]:
    """Первые N runnable слотов очереди (top-N окно).

    paused/user_stop не входят в окно — освобождают параллель для следующих.
    """
    queue = get_gen_queue()
    n = worker_max_parallel()
    out: list[Project] = []
    for pid in queue:
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            continue
        out.append(project)
        if len(out) >= n:
            break
    return out


async def _rollback_running(
    session: AsyncSession,
    project: Project,
    *,
    reason: str,
) -> bool:
    from app.services.project_control import rollback_running_for_queue

    return await rollback_running_for_queue(session, project, reason=reason)


def _step_really_running(project_id: int) -> bool:
    """Шаг проекта сейчас реально выполняется (идёт платная работа)."""
    from app.services.step_cancel import is_generation_active

    try:
        return bool(is_generation_active(project_id))
    except Exception:  # noqa: BLE001
        return False


async def gen_queue_reconcile(session: AsyncSession) -> int:
    """Сбросить running вне top-N окна; внутри окна — rollback blocked слотов.

    Шаг, который уже реально выполняется, не сбрасываем: после уменьшения
    параллели он доигрывает, а новые старты ждут (см. gen_queue_tick).
    Сбрасывается только статус «running», за которым нет живого шага.
    """
    queue = get_gen_queue()
    if not queue:
        return 0

    window = await gen_queue_window_projects(session)
    window_ids = {p.id for p in window}
    rolled = 0

    for project in window:
        if _slot_blocked(project) and project.status in GEN_QUEUE_BUSY_STATUSES:
            if await _rollback_running(session, project, reason="window slot blocked"):
                rolled += 1

    for pid in queue:
        if pid in window_ids:
            continue
        loaded = await _load_project(session, pid)
        if loaded is None or is_mass_factory_child(loaded):
            continue
        project = loaded
        if project.status not in GEN_QUEUE_BUSY_STATUSES:
            continue
        if _step_really_running(project.id):
            logger.info(
                "gen_queue: #{} вне окна, но шаг уже идёт — доигрывает, не сбрасываем",
                project.id,
            )
            continue
        if await _rollback_running(session, project, reason="out-of-window"):
            rolled += 1

    return rolled


async def gen_queue_busy_project(session: AsyncSession) -> int | None:
    """ID первого running-проекта в top-N окне (или None)."""
    busy_ids = await gen_queue_busy_projects(session)
    return busy_ids[0] if busy_ids else None


async def gen_queue_busy_projects(session: AsyncSession) -> list[int]:
    """ID running-проектов внутри top-N окна (после reconcile)."""
    await gen_queue_reconcile(session)
    out: list[int] = []
    for project in await gen_queue_window_projects(session):
        if _slot_blocked(project):
            continue
        if project.status in GEN_QUEUE_BUSY_STATUSES:
            out.append(project.id)
    return out


async def gen_queue_busy_count(session: AsyncSession) -> int:
    """Сколько проектов очереди сейчас в running внутри окна."""
    return len(await gen_queue_busy_projects(session))


async def gen_queue_incomplete_earlier(session: AsyncSession, project_id: int) -> int | None:
    """Блокирующий более ранний слот, если проект вне top-N окна.

    paused/user_stop пропускаются. Блокирует N-й runnable предшественник
    (окно параллели заполнено).
    """
    queue = get_gen_queue()
    if not queue or project_id not in queue:
        return None
    n = worker_max_parallel()
    pos = queue.index(project_id)
    holding: list[int] = []
    for pid in queue[:pos]:
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            logger.debug(
                "gen_queue: #{} пропуск (paused/user_stop) — не блокирует хвост",
                project.id,
            )
            continue
        holding.append(project.id)
        if len(holding) >= n:
            return holding[n - 1]
    return None


async def gen_queue_blocks_project(session: AsyncSession, project_id: int) -> int | None:
    """Если проект в очереди — ID блокирующего предшественника или None."""
    return await gen_queue_incomplete_earlier(session, project_id)


async def _still_queued_and_free(session: AsyncSession, project: Project) -> bool:
    """Прямо перед стартом: проект ещё в очереди и без user_stop (стоп мог прийти во время тика)."""
    if project.id not in get_gen_queue():
        logger.info("gen_queue: #{} уже снят с очереди — не стартуем", project.id)
        return False
    try:
        await session.refresh(project, ["meta", "status"])
    except Exception:  # noqa: BLE001
        return True
    if _slot_blocked(project) or project.status in GEN_QUEUE_BUSY_STATUSES:
        logger.info("gen_queue: #{} остановлен во время тика — не стартуем", project.id)
        return False
    return True


async def gen_queue_tick(session: AsyncSession) -> int:
    """Заполнить top-N окно: стартовать/продвинуть до N проектов."""
    queue = get_gen_queue()
    if not queue:
        return 0
    if is_gen_queue_halted():
        logger.info("gen_queue tick: halted после ⏹ — следующий не стартует")
        return 0

    rolled = await gen_queue_reconcile(session)
    if rolled:
        await session.flush()

    n = worker_max_parallel()
    logger.debug("gen_queue tick: порядок {} (max_parallel={})", queue, n)

    # Сколько проектов очереди уже идут (в окне и доигрывающие вне окна после
    # уменьшения параллели). Новые старты — только пока идущих меньше N.
    busy_total = 0
    for pid in queue:
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project) or _slot_blocked(project):
            continue
        if project.status in GEN_QUEUE_BUSY_STATUSES:
            busy_total += 1

    started_total = 0
    window_filled = 0

    for idx, pid in enumerate(queue):
        if window_filled >= n:
            break
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            logger.info(
                "gen_queue: #{} {} — пропуск слота, ищем свободный (позиция {})",
                project.id,
                "user_stop" if _user_stop_blocks_queue(project) else "paused",
                idx + 1,
            )
            continue
        if project.status is ProjectStatus.failed:
            fs = (project.meta or {}).get("step_failure") or {}
            err = str(fs.get("last_error") or "ошибка шага")[:120]
            await skip_gen_queue_slot(
                session,
                project,
                reason="failed",
                detail=err,
            )
            logger.warning(
                "gen_queue: #{} пропущен (ошибка, позиция {}): {}",
                project.id,
                idx + 1,
                err,
            )
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue

        if project.status in GEN_QUEUE_BUSY_STATUSES:
            window_filled += 1
            continue

        if busy_total >= n:
            logger.info(
                "gen_queue: #{} ждёт — уже идут {} при лимите {} (доигрывают)",
                project.id,
                busy_total,
                n,
            )
            return started_total

        if not await _still_queued_and_free(session, project):
            continue

        started = await _start_or_advance_project(session, project, queue_pos=idx + 1)
        if started:
            started_total += started
            busy_total += 1
            window_filled += 1
            continue

        # Слот занимает окно (new / ждёт ▶ / без auto_mode), но не running.
        # При N=1 — как раньше: стоп на этом слоте. При N>1 — пробуем
        # следующие слоты окна.
        window_filled += 1
        if n == 1:
            logger.info(
                "gen_queue: ждём #{} (позиция {}, status={})",
                project.id,
                idx + 1,
                project.status.value,
            )
            return started_total
        logger.info(
            "gen_queue: #{} занимает окно (status={}), слот {}/{}",
            project.id,
            project.status.value,
            window_filled,
            n,
        )
    return started_total


async def on_project_timeline_maybe_advance_queue(session: AsyncSession, project: Project) -> int:
    """После завершения шага: закрыть слот и дозаполнить top-N окно."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return 0
    if is_gen_queue_halted():
        logger.info(
            "gen_queue: #{} timeline complete — очередь halted после ⏹",
            project.id,
        )
        return 0
    if is_mass_factory_child(project):
        return 0
    if not await is_timeline_complete(session, project):
        return 0
    if not is_gen_queue_run_complete(project):
        await mark_gen_queue_run_complete(session, project)
        await session.flush()
        logger.info(
            "gen_queue: #{} timeline complete — слот {} закрыт",
            project.id,
            queue.index(project.id) + 1,
        )
    # Дозаполняем окно (при N=1 — следующий слот; при N>1 — несколько).
    return await gen_queue_tick(session)


async def assert_can_start_in_queue(session: AsyncSession, project: Project) -> None:
    """Ручной start_step: проект должен быть внутри top-N окна."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return
    blocker = await gen_queue_blocks_project(session, project.id)
    if blocker is not None:
        raise ValueError(f"Очередь: сначала завершите #{blocker}, затем #{project.id}")
