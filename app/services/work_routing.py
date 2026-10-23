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


#: Финальные статусы: проект дошёл до конца, автопродвижению там делать нечего
#: (`assembled` без публикации на канвасе — нормальный покой). Проход арендатору
#: ради них не нужен — иначе каждый, кто хоть раз собрал ролик, получал бы полный
#: проход воркера каждые 5 секунд.
TERMINAL_STATUSES: frozenset[str] = frozenset({"assembled", "published"})


def waiting_statuses() -> list[str]:
    """Ждущие статусы (`*_ready`), из которых автопродвижение ведёт дальше; без финальных."""
    from app.orchestrator.auto_advance import TRANSITIONS

    out = [getattr(st, "value", str(st)) for st in TRANSITIONS]
    return [st for st in out if st not in TERMINAL_STATUSES]


async def _has_auto_waiting(session_factory, tenant: str | None, ids: list[int]) -> bool:
    """Есть ли у арендатора среди ``ids`` проект с включённым `auto_mode`.

    `auto_mode` в маршрутной таблице нет, а `projects` под RLS: читаем от имени
    самого арендатора (его строки, ничего чужого) и ещё раз сверяем арендатора
    строки. Один короткий запрос на арендатора и только по его ждущим номерам.
    """
    from app.models import Project
    from app.services.tenant import tenant_scope

    with tenant_scope(tenant):
        async with session_factory() as s:
            rows = (
                await s.execute(
                    select(Project.tenant_id)
                    .where(Project.id.in_(ids), Project.auto_mode.is_(True))
                    .limit(len(ids))
                )
            ).all()
    want = None if tenant is None else str(tenant)
    return any((None if owner is None else str(owner)) == want for (owner,) in rows)


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
      арендатора. Без этого арендатор с одной очередью ждущих проектов
      прохода не получал, и очередь отвечала «поставлено» и стояла;
    * ждущий статус (`waiting`, `*_ready` без финальных — `waiting_statuses`)
      у проекта с включённым `auto_mode` — его двигает автопродвижение в
      проходе арендатора. Флага `auto_mode` в маршрутной таблице нет, поэтому
      он проверяется коротким запросом от имени арендатора
      (``session_factory``), и только для тех, кого первые два правила не
      выбрали. Без ``session_factory`` ждущие статусы проходят без проверки.

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
    if waiting_l:
        rows = (
            await session.execute(
                select(ProjectRoute.tenant_id, ProjectRoute.project_id).where(
                    ProjectRoute.status_value.in_(waiting_l)
                )
            )
        ).all()
        by_tenant: dict[str | None, list[int]] = {}
        for tenant, pid in rows:
            if tenant in served:
                continue
            by_tenant.setdefault(tenant, []).append(int(pid))
        for tenant, pids in by_tenant.items():
            if session_factory is None:
                served.append(tenant)
                continue
            try:
                if await _has_auto_waiting(session_factory, tenant, pids):
                    served.append(tenant)
            except Exception:  # noqa: BLE001
                # Проверка не удалась — служим, как служили бы без неё: молча
                # вставший проект хуже лишнего прохода.
                logger.exception("work_routing: auto_mode check failed for tenant {}", tenant)
                served.append(tenant)
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
