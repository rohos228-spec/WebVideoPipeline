"""У кого сейчас есть работа. Единственный межарендный запрос в системе.

Воркер конвейера ищет работу сканированием: «дай все проекты в рабочем
статусе». Под row-level security такого запроса не бывает — сканирование
межарендно, а RLS запрещает межарендное чтение, в том и смысл. Воркер, не
назначивший арендатора, видит проекты владельца и ни одного клиентского, и
это не ошибка в коде, а следствие политики.

Развязка — маршрутная таблица `project_routes` (ревизия 0007): номер
проекта, арендатор, статус, и ничего больше. Она под своей политикой,
обратной обычной: сессия без арендатора видит всё, сессия с арендатором —
только своё. Здесь — единственное место, которое этим пользуется.

**Что возвращается.** Список арендаторов, у которых есть проект в одном из
переданных статусов, плюс `None`, если такие проекты есть у владельца.
`None` идёт первым: на машине владельца он единственный, и порядок делает
поведение там ровно прежним.

**Список статусов приходит снаружи.** В SQL его копии нет намеренно: рабочие
статусы объявлены один раз в `node_registry.WORKER_ACTIVE_STATUSES`, и вторая
копия разошлась бы с первой молча — воркер перестал бы видеть целый класс
шагов, ничего при этом не сломав.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from loguru import logger
from sqlalchemy import or_, select

from app.models import ProjectRoute


async def tenants_with_active_work(session, statuses: Iterable[str]) -> list[str | None]:
    """Арендаторы, у которых есть проект в рабочем статусе. Владелец — `None`.

    Сессию обязан открыть вызывающий, и открыть её БЕЗ арендатора: под
    назначенным арендатором политика вернёт только его строки, и воркер
    снова увидит одного клиента вместо всех.
    """
    wanted = [str(s) for s in statuses]
    if not wanted:
        return []
    rows = (
        (
            await session.execute(
                select(ProjectRoute.tenant_id).where(ProjectRoute.status_value.in_(wanted)).distinct()
            )
        )
        .scalars()
        .all()
    )
    return _ordered(rows)


#: Финальные статусы: проект дошёл до конца, автопродвижению там делать нечего.
#: `assembled` сюда НЕ входит: из него автопродвижение ведёт в `publishing`, если на
#: канвасе проекта после сборки есть публикация (канвас по умолчанию кончается
#: нодой `publish`). Есть ли там шаг — проверяется по графу проекта в
#: `_tenant_needs_tick`; без шага `assembled` — нормальный покой, прохода нет.
TERMINAL_STATUSES: frozenset[str] = frozenset({"published"})

#: Ждущий статус, которому проход нужен только при шаге после него на канвасе.
ASSEMBLED = "assembled"

#: `new` — ни шага, ни ожидания, но проект стартует сам в проходе арендатора, если
#: это следующая полоса массовой фабрики (`serial_tick_mass_lanes`, meta
#: `mass_parent_id`) или подпроект запущенного массового (`serial_tick_batches`,
#: `BatchProject.status == running`). Проверяется так же, по проекту.
NEW = "new"

#: Сколько секунд помнить «этому арендатору проход не нужен» при тех же
#: номерах и статусах кандидатов. Смена статуса любого кандидата (▶, шаг,
#: стоп) сбрасывает память сразу; флаги в meta (`auto_mode`, стоп, запуск
#: массового) доходят не позже этого срока.
NEGATIVE_TTL_SECONDS = 30.0

_negative_cache: dict[str | None, tuple[tuple, float]] = {}


def reset_serve_cache() -> None:
    """Забыть отрицательные ответы проверки (тесты)."""
    _negative_cache.clear()


def waiting_statuses() -> list[str]:
    """Ждущие статусы (`*_ready`, `assembled`), из которых автопродвижение может вести дальше."""
    from app.orchestrator.auto_advance import TRANSITIONS

    out = [getattr(st, "value", str(st)) for st in TRANSITIONS]
    return [st for st in out if st not in TERMINAL_STATUSES]


def _same_tenant(owner, tenant: str | None) -> bool:
    return (None if owner is None else str(owner)) == (None if tenant is None else str(tenant))


async def _waiting_needs_tick(s, project) -> bool:
    """Ждущий проект с `auto_mode`: двинет ли его автопродвижение."""
    from app.models import ProjectStatus
    from app.services.gen_queue_run import is_user_stopped
    from app.services.project_control import auto_awaits_manual_start

    if is_user_stopped(project) or auto_awaits_manual_start(project):
        # ⏹ или «ждём ручной ▶»: автопродвижение такой проект не трогает, а ▶
        # меняет статус — и проверка повторится сразу.
        return False
    status = getattr(project.status, "value", str(project.status))
    if status == ASSEMBLED:
        from app.orchestrator.auto_advance import _graph_next_running

        return await _graph_next_running(s, project, ProjectStatus.assembled) is not None
    return True


async def _new_needs_tick(s, project) -> bool:
    """Проект в `new`: следующая полоса массовой фабрики или подпроект запущенного массового."""
    from app.models import BatchProject, BatchStatus, Project
    from app.services.gen_queue_run import is_user_stopped
    from app.services.project_control import is_mass_family_halted

    if is_user_stopped(project):
        return False
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw_parent = meta.get("mass_parent_id")
    if raw_parent is not None:
        try:
            parent = await s.get(Project, int(raw_parent))
        except (TypeError, ValueError):
            parent = None
        if parent is None or not is_mass_family_halted(parent):
            return True
    if project.batch_id is not None:
        batch = await s.get(BatchProject, project.batch_id)
        if batch is not None and batch.status == BatchStatus.running:
            return True
    return False


async def _tenant_needs_tick(
    session_factory, tenant: str | None, waiting_ids: list[int], new_ids: list[int]
) -> bool:
    """Нужен ли арендатору проход ради ждущих (`*_ready`, `assembled`) или новых (`new`) проектов.

    Ни `auto_mode`, ни meta, ни графа в маршрутной таблице нет, а `projects` под
    RLS: читаем от имени самого арендатора (его строки, ничего чужого) и ещё раз
    сверяем арендатора строки. Одна сессия на арендатора и только по его
    номерам-кандидатам.
    """
    from app.models import Project
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with session_factory() as s:
            if waiting_ids:
                rows = (
                    (
                        await s.execute(
                            select(Project).where(Project.id.in_(waiting_ids), Project.auto_mode.is_(True))
                        )
                    )
                    .scalars()
                    .all()
                )
                for p in rows:
                    if _same_tenant(p.tenant_id, tenant) and await _waiting_needs_tick(s, p):
                        return True
            if new_ids:
                rows = (await s.execute(select(Project).where(Project.id.in_(new_ids)))).scalars().all()
                for p in rows:
                    if _same_tenant(p.tenant_id, tenant) and await _new_needs_tick(s, p):
                        return True
    return False


async def tenants_to_serve(
    session,
    statuses: Iterable[str],
    project_ids: Iterable[int] = (),
    *,
    waiting: Iterable[str] = (),
    session_factory=None,
) -> list[str | None]:
    """Кому воркер служит в этом тике.

    * рабочий статус (`statuses`) — шаг идёт, его ведёт воркер;
    * номер в очереди генерации (`project_ids`) — проект, поставленный в
      очередь (чат «queue.enqueue_many», кнопка очереди), стоит в `new` или
      `*_ready`, а стартует его `gen_queue_tick` — только внутри прохода
      арендатора;
    * ждущий статус (`waiting`, `*_ready` и `assembled` — `waiting_statuses`) у
      проекта с включённым `auto_mode`, без ⏹ и без «ждём ручной ▶»; у
      `assembled` — ещё и с шагом после сборки на канвасе (публикация);
    * `new` у следующей полосы массовой фабрики (meta `mass_parent_id`, семья
      не остановлена) или у подпроекта запущенного массового (`BatchProject`
      в `running`) — их стартуют `serial_tick_mass_lanes` / `serial_tick_batches`
      в проходе арендатора.

    Последние два правила проверяются одной короткой сессией от имени
    арендатора (``session_factory``) и только для тех, кого первые два не
    выбрали; отрицательный ответ помнится ``NEGATIVE_TTL_SECONDS`` при тех же
    кандидатах. Без ``session_factory`` ждущие статусы проходят без проверки, а
    `new` не учитывается.

    Номера очереди — из общего файла очереди: чьи они, знает только
    маршрутная таблица, и читается она здесь же, без арендатора.
    """
    wanted = [str(s) for s in statuses]
    ids = sorted({int(pid) for pid in project_ids})
    waiting_l = [str(s) for s in waiting if str(s) not in wanted]
    cond = []
    if wanted:
        cond.append(ProjectRoute.status_value.in_(wanted))
    if ids:
        cond.append(ProjectRoute.project_id.in_(ids))
    served: list[str | None] = []
    if cond:
        served = list(
            (await session.execute(select(ProjectRoute.tenant_id).where(or_(*cond)).distinct()))
            .scalars()
            .all()
        )
    candidates = list(waiting_l)
    if session_factory is not None and NEW not in wanted and NEW not in candidates:
        candidates.append(NEW)
    if not candidates:
        return _ordered(served)
    rows = (
        await session.execute(
            select(ProjectRoute.tenant_id, ProjectRoute.project_id, ProjectRoute.status_value).where(
                ProjectRoute.status_value.in_(candidates)
            )
        )
    ).all()
    by_tenant: dict[str | None, list[tuple[int, str]]] = {}
    for tenant, pid, status in rows:
        if tenant in served:
            continue
        by_tenant.setdefault(tenant, []).append((int(pid), str(status)))
    now = time.monotonic()
    for tenant, items in by_tenant.items():
        if session_factory is None:
            if any(st != NEW for _pid, st in items):
                served.append(tenant)
            continue
        signature = tuple(sorted(items))
        cached = _negative_cache.get(tenant)
        if cached is not None and cached[0] == signature and cached[1] > now:
            continue
        waiting_ids = [pid for pid, st in items if st != NEW]
        new_ids = [pid for pid, st in items if st == NEW]
        try:
            need = await _tenant_needs_tick(session_factory, tenant, waiting_ids, new_ids)
        except Exception:  # noqa: BLE001
            # Проверка не удалась — служим, как служили бы без неё: молча
            # вставший проект хуже лишнего прохода.
            logger.exception("work_routing: serve check failed for tenant {}", tenant)
            served.append(tenant)
            continue
        if need:
            _negative_cache.pop(tenant, None)
            served.append(tenant)
        else:
            _negative_cache[tenant] = (signature, now + NEGATIVE_TTL_SECONDS)
    return _ordered(served)


def _ordered(rows: Sequence[str | None]) -> list[str | None]:
    """Владелец первым, дальше арендаторы в стабильном порядке.

    Стабильность важнее красоты: при нехватке слотов параллели воркер
    обрывает обход, и случайный порядок означал бы, что кому-то не достаётся
    очереди никогда.
    """
    tenants = sorted({r for r in rows if r is not None})
    out: list[str | None] = [None] if any(r is None for r in rows) else []
    out.extend(tenants)
    return out
