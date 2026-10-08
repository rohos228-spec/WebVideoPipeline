"""Слияние БД second-mechanic → main (фаза 5, операция по runbook).

Переносит ВСЕ данные v2 в main-БД с ремаппингом integer-PK, склейкой
тенантов по email и дедупом справочников. Проекты из v2 получают
``pipeline_mode='v2'`` по происхождению (детерминировано, без эвристик).

Политики по таблицам (выведены из app.models + tenant_tables):

* SKIP (эфемерное, не переносим): work_leases, email_verifications,
  alembic_version. project_routes НЕ копируем — её пересоздаёт триггер
  0007 при INSERT в projects (проверено: триггер есть на обеих головах).
* Тенанты: пользователи склеиваются по email (строка main побеждает);
  недостающие учётки импортируются как есть. Все колонки tenant_id +
  credit_* переключиваются на main-tenant (см. TENANT_TABLES/CREDIT_TABLES).
* Дедуп по UNIQUE, main побеждает, конфликты в отчёт: coupons (по code,
  used_count пересчитывается по проводкам), fleet_nodes (по name),
  library_*/prompt_library/master_prompts (по их uq), workflows
  (дефолтом остаётся main; дефолт v2 импортируется копией is_default=False).
* Ремаппинг: все остальные таблицы, новые PK = max(main)+1.., FK
  переписываются по картам. llm/media_calls: FK нет, маппится project_id.
* Слаг-коллизии (projects/batch/test_prompt): суффикс (по умолчанию "-v2")
  + карта переименований для томов (файлы — по runbook, НЕ здесь).
* Сиквенсы Postgres: setval после вставки. SQLite: noop.

Транзакция одна: без --apply в конце ROLLBACK (dry-run гоняет всё).

Предусловие: одинаковый head alembic в обеих базах, иначе стоп.

    python scripts/merge_v2_database.py --main-url ... --v2-url ... [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict, deque

from sqlalchemy import Integer, MetaData, Table, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, ".")

SKIP_TABLES = frozenset({"work_leases", "email_verifications", "alembic_version", "project_routes"})

SLUG_TABLES = ("projects", "batch_projects", "test_prompt_projects")
SLUG_COL = {"projects": "slug", "batch_projects": "slug", "test_prompt_projects": "slug"}


def _ordered_tables(meta: MetaData) -> list[str]:
    """Топологический порядок по FK (родители раньше детей). Цикл = авария."""
    deps: dict[str, set[str]] = {}
    for name, tbl in meta.tables.items():
        if name in SKIP_TABLES:
            continue
        refs = set()
        for fk in tbl.foreign_keys:
            ref = fk.column.table.name
            if ref != name and ref not in SKIP_TABLES:
                refs.add(ref)
        deps[name] = refs
    order: list[str] = []
    ready = deque(sorted(n for n, d in deps.items() if not d))
    while ready:
        node = ready.popleft()
        order.append(node)
        for n, d in deps.items():
            if node in d:
                d.discard(node)
                if not d and n not in order and n not in ready:
                    ready.append(n)
    if len(order) != len(deps):
        missing = sorted(set(deps) - set(order))
        raise RuntimeError(f"цикл FK в схеме, не топологируется: {missing}")
    return order


async def _rows(conn, table: Table) -> list[dict]:
    out = []
    for row in (await conn.execute(select(table))).mappings().all():
        out.append(dict(row))
    return out


async def _max_id(conn, table: Table, pk: str) -> int:
    val = (await conn.execute(select(func.max(table.c[pk])))).scalar()
    return int(val or 0)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Слияние v2-БД в main-БД.")
    ap.add_argument("--main-url", required=True)
    ap.add_argument("--v2-url", required=True)
    ap.add_argument("--apply", action="store_true", help="писать (иначе dry-run)")
    ap.add_argument("--slug-suffix", default="-v2")
    args = ap.parse_args()

    from app.models import Base
    from app.services.tenant_tables import CREDIT_TABLES, TENANT_TABLES

    meta = Base.metadata
    order = _ordered_tables(meta)
    tenant_tables = set(TENANT_TABLES) | set(CREDIT_TABLES)

    eng_main = create_async_engine(args.main_url, echo=False)
    eng_v2 = create_async_engine(args.v2_url, echo=False)
    report: list[str] = []
    try:
        async with eng_main.connect() as main, eng_v2.connect() as v2:
            rev_main = (await main.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            rev_v2 = (await v2.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            if rev_main != rev_v2:
                print(f"СТОП: head различаются main={rev_main} v2={rev_v2}")
                return 2

            # --- тенанты: склейка по email ---
            main_users = {r["email"]: r["id"] for r in await _rows(main, meta.tables["studio_users"])}
            v2_users = await _rows(v2, meta.tables["studio_users"])
            tenant_map: dict[str, str] = {}
            new_users = 0
            for u in v2_users:
                if u["email"] in main_users:
                    tenant_map[str(u["id"])] = str(main_users[u["email"]])
                else:
                    tenant_map[str(u["id"])] = str(u["id"])
                    new_users += 1
            report.append(f"тенанты: склеено {len(v2_users) - new_users}, новых учёток {new_users}")

            # Пишущая фаза — вложенной транзакцией (savepoint): выше на этом же
            # соединении уже висит autobegin от чтений. Dry-run откатывает
            # savepoint, --apply коммитит его во внешнюю транзакцию чтения
            # (она read-only по факту и гасится при dispose).
            tx = main.begin_nested()
            await tx.__aenter__()
            try:
                id_map: dict[str, dict] = defaultdict(dict)  # table -> {old: new}
                renamed_slugs: list[tuple[str, str, str]] = []
                conflicts: list[str] = []
                _slug_cache: dict[str, set[str]] = {}
                _coupon_codes: set[str] = {
                    r[0] for r in (await main.execute(select(meta.tables["coupons"].c.code))).all()
                }

                # недостающие учётки — как есть
                if new_users:
                    tbl = meta.tables["studio_users"]
                    for u in v2_users:
                        if str(u["id"]) not in main_users.values() and tenant_map[str(u["id"])] == str(
                            u["id"]
                        ):
                            await main.execute(tbl.insert().values(**u))

                max_ids: dict[str, int] = {}
                for name in order:
                    tbl = meta.tables[name]
                    pk_cols = [c.name for c in tbl.primary_key.columns]
                    v2_rows = await _rows(v2, tbl)
                    if not v2_rows:
                        continue
                    if len(pk_cols) == 1 and pk_cols[0] == "id" and isinstance(tbl.c["id"].type, Integer):
                        base = await _max_id(main, tbl, "id")
                        max_ids[name] = base
                        for i, row in enumerate(v2_rows, start=1):
                            id_map[name][row["id"]] = base + i
                    else:
                        for row in v2_rows:
                            id_map[name][row[pk_cols[0]]] = row[pk_cols[0]]

                for name in order:
                    tbl: Table = meta.tables[name]
                    v2_rows = await _rows(v2, tbl)
                    if not v2_rows:
                        continue
                    pk = [c.name for c in tbl.primary_key.columns]
                    single_int_pk = len(pk) == 1 and pk[0] == "id"
                    fks = {
                        c.name: (list(c.foreign_keys)[0].column.table.name)
                        for c in tbl.columns
                        if c.foreign_keys
                    }

                    # существующие ключи main для дедупа
                    existing: set = set()
                    uq_cols = [c.name for c in tbl.columns if c.unique]
                    if uq_cols and name not in SLUG_TABLES and name != "coupons":
                        cols = [tbl.c[c] for c in uq_cols]
                        rows = (await main.execute(select(*cols))).all()
                        existing = {tuple(r) for r in rows}

                    inserted = skipped = 0
                    for row in v2_rows:
                        new = dict(row)
                        if single_int_pk:
                            new["id"] = id_map[name][row["id"]]
                        for col, ref_table in fks.items():
                            if new.get(col) is None:
                                continue
                            target_map = id_map.get(ref_table, {})
                            new[col] = target_map.get(new[col], new[col])
                        if name in tenant_tables and new.get("tenant_id") is not None:
                            new["tenant_id"] = tenant_map.get(str(new["tenant_id"]), new["tenant_id"])
                        # pipeline_mode: всё из v2 — v2 по происхождению
                        if "pipeline_mode" in tbl.columns:
                            new["pipeline_mode"] = "v2"
                        # слаги: коллизия -> суффикс (кэш + счётчик на цепочки)
                        if name in SLUG_TABLES:
                            col = SLUG_COL[name]
                            if name not in _slug_cache:
                                _slug_cache[name] = await _main_slugs(main, tbl, col)
                            taken = _slug_cache[name]
                            base_slug = str(new[col])
                            candidate, n = base_slug, 1
                            while candidate in taken:
                                n += 1
                                candidate = (
                                    base_slug + args.slug_suffix
                                    if n == 2
                                    else base_slug + args.slug_suffix + str(n - 1)
                                )
                            if candidate != base_slug:
                                renamed_slugs.append((name, base_slug, candidate))
                            new[col] = candidate
                            taken.add(candidate)
                        # дедуп по UNIQUE (main побеждает)
                        if uq_cols and name not in SLUG_TABLES and name != "coupons":
                            key = tuple(new[c] for c in uq_cols)
                            if key in existing:
                                old_row = {k: row[k] for k in row if k != "id"}
                                new_row = {k: new[k] for k in new if k != "id"}
                                if old_row != new_row:
                                    conflicts.append(f"{name} {key}: контент различается, оставлен main")
                                skipped += 1
                                continue
                            existing.add(key)
                        # купоны: дедуп по code (main побеждает, сиды совпадают)
                        if name == "coupons":
                            if new["code"] in _coupon_codes:
                                skipped += 1
                                continue
                            _coupon_codes.add(new["code"])
                        # workflows: дефолт остаётся main
                        if name == "workflows" and new.get("is_default"):
                            new["is_default"] = False
                            conflicts.append(f"workflows id={new['id']}: дефолт v2 импортирован как обычный")
                        await main.execute(tbl.insert().values(**new))
                        inserted += 1
                    report.append(
                        f"{name}: v2-строк {len(v2_rows)}, вставлено {inserted}, пропущено {skipped}"
                    )

                # купоны отдельно: склейка по code
                await _merge_coupons(main, v2, meta, tenant_map, report)

                # сиквенсы Postgres
                if main.dialect.name == "postgresql":
                    for name in order:
                        tbl = meta.tables[name]
                        pk = [c.name for c in tbl.primary_key.columns]
                        if len(pk) == 1 and pk[0] == "id":
                            await main.execute(
                                text(
                                    "SELECT setval(pg_get_serial_sequence(:t, :c),"
                                    " (SELECT MAX(id) FROM " + name + "))"
                                ).bindparams(t=name, c="id")
                            )
                    report.append("сиквенсы Postgres выровнены")

                if renamed_slugs:
                    report.append("переименованные слаги (тома — по runbook!):")
                    report.extend(f"  {t}: {a} -> {b}" for t, a, b in renamed_slugs)
                if conflicts:
                    report.append("конфликты (оставлен main):")
                    report.extend(f"  {c}" for c in conflicts[:30])

                if args.apply:
                    await tx.commit()
                    report.append("APPLY: закоммичено")
                else:
                    await tx.rollback()
                    report.append("DRY-RUN: откачено, записи не было")
            except Exception:
                await tx.rollback()
                raise
            print("\n".join(report))
            return 0
    finally:
        await eng_main.dispose()
        await eng_v2.dispose()


async def _main_slugs(conn, tbl: Table, col: str) -> set[str]:
    rows = (await conn.execute(select(tbl.c[col]))).all()
    return {str(r[0]) for r in rows}


async def _merge_coupons(main, v2, meta, tenant_map: dict, report: list[str]) -> None:
    from sqlalchemy import func as _func
    from sqlalchemy import select as _select

    coupons, reds = meta.tables["coupons"], meta.tables["coupon_redemptions"]
    main_codes = {r[0]: r[1] for r in (await main.execute(_select(coupons.c.code, coupons.c.id))).all()}
    for row in await _rows(v2, coupons):
        if row["code"] in main_codes:
            continue
        await main.execute(coupons.insert().values(**row))
        main_codes[row["code"]] = row["id"]
    moved = 0
    # редемпшены: coupon_id -> main-id по коду, tenant -> склейка
    v2_coupon_by_id = {str(r["id"]): r["code"] for r in await _rows(v2, coupons)}
    for r in await _rows(v2, reds):
        code = v2_coupon_by_id.get(str(r["coupon_id"]))
        if code not in main_codes:
            continue
        nr = dict(r)
        nr["coupon_id"] = main_codes[code]
        nr["tenant_id"] = tenant_map.get(str(nr["tenant_id"]), nr["tenant_id"])
        try:
            await main.execute(reds.insert().values(**nr))
            moved += 1
        except Exception:  # noqa: BLE001 — дубль (coupon, tenant) уже есть
            pass
    # used_count = факт по проводкам погашений
    for _code, cid in main_codes.items():
        cnt = (
            await main.execute(_select(_func.count()).select_from(reds).where(reds.c.coupon_id == cid))
        ).scalar()
        await main.execute(coupons.update().where(coupons.c.id == cid).values(used_count=int(cnt or 0)))
    report.append(f"купоны: кодов {len(main_codes)}, погашений перенесено {moved}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
