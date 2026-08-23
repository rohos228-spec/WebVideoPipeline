"""Проверка, что изоляция арендаторов действительно работает.

Политика row-level security умеет отказывать бесшумно, и способов ровно три:

1. **Таблица не под политикой.** Новая таблица появилась после 0006 — она
   просто видна всем. Ничего не падает.
2. **Политика есть, но не форсирована.** `ENABLE ROW LEVEL SECURITY` не
   действует на владельца таблиц, а приложение обычно подключается именно
   им. Политика видна в `\\d+` и не применяется ни к одному запросу.
3. **Роль обходит RLS.** Суперпользователь и любая роль с `BYPASSRLS`
   игнорируют политики по определению. Приложение, поднятое под `postgres`,
   отработает без единой ошибки и покажет всем всё.

Ни один из трёх случаев не даёт исключения, ошибки в логе или падения
теста — он даёт утечку чужого ролика. Поэтому проверка вынесена отдельно и
вызывается на старте: лучше не подняться, чем подняться без изоляции.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.services.tenant_tables import all_isolated, policy_for


@dataclass
class RlsReport:
    """Что именно не так с изоляцией. Пусто — всё в порядке."""

    dialect: str
    role: str = ""
    superuser: bool = False
    bypassrls: bool = False
    unprotected: list[str] = field(default_factory=list)
    unforced: list[str] = field(default_factory=list)
    missing_policy: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.dialect != "postgresql":
            # На SQLite изоляции нет вовсе, и это законно ровно в режиме
            # одного владельца — стережёт `tenant.require_isolation`.
            return True
        return not (
            self.superuser or self.bypassrls or self.unprotected or self.unforced or self.missing_policy
        )

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.superuser:
            out.append(f"роль {self.role} — суперпользователь: RLS её не касается")
        if self.bypassrls:
            out.append(f"роль {self.role} имеет BYPASSRLS: политики игнорируются")
        if self.unprotected:
            out.append(f"RLS не включён: {', '.join(self.unprotected)}")
        if self.unforced:
            out.append(f"RLS не форсирован (владелец обойдёт): {', '.join(self.unforced)}")
        if self.missing_policy:
            out.append(f"нет политики tenant_isolation: {', '.join(self.missing_policy)}")
        return out


async def check_rls(session) -> RlsReport:
    """Снять состояние изоляции с живой базы."""
    from app.settings import settings

    if not settings.is_postgres:
        return RlsReport(dialect=settings.db_dialect)

    report = RlsReport(dialect="postgresql")

    role_row = (
        await session.execute(
            text("select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user")
        )
    ).first()
    if role_row is not None:
        report.role = str(role_row[0])
        report.superuser = bool(role_row[1])
        report.bypassrls = bool(role_row[2])

    wanted = set(all_isolated())
    rows = (
        await session.execute(
            text(
                "select c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = current_schema() and c.relkind = 'r'"
            )
        )
    ).all()
    state = {str(r[0]): (bool(r[1]), bool(r[2])) for r in rows if str(r[0]) in wanted}

    # Политика сверяется поимённо: у маршрутных таблиц она называется иначе,
    # потому что и предикат у неё обратный. Ищи мы одно имя на всех —
    # таблица с чужой политикой считалась бы незакрытой, а таблица с
    # правильным именем и неправильным предикатом прошла бы молча.
    policies = {
        (str(r[0]), str(r[1]))
        for r in (await session.execute(text("select tablename, policyname from pg_policies"))).all()
    }

    for table in sorted(wanted):
        if table not in state:
            continue  # таблицы нет в этой базе — не наша забота
        enabled, forced = state[table]
        if not enabled:
            report.unprotected.append(table)
        elif not forced:
            report.unforced.append(table)
        if (table, policy_for(table)) not in policies:
            report.missing_policy.append(f"{table} ({policy_for(table)})")
    return report


async def assert_rls_or_die(session) -> None:
    """Упасть на старте, если изоляция не в силе. Зовётся из lifespan.

    Падение при запуске чинится за минуту. Утечка чужого ролика не чинится
    вовсе: клиент уже увидел то, что не должен был.
    """
    from app.services.tenant_tables import check_coverage

    forgotten = check_coverage()
    report = await check_rls(session)
    problems = report.problems()
    if forgotten:
        problems.append(f"таблицы вне списков арендатора: {', '.join(forgotten)}")
    if problems:
        raise RuntimeError("изоляция арендаторов не обеспечена: " + "; ".join(problems))
