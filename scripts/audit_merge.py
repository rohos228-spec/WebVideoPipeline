"""Аудит перед слиянием БД main + second-mechanic (фаза 5, read-only).

Сверяет две базы (Postgres на VPS или SQLite-файлы для проверки) и печатает
отчёт о коллизиях, которые убьют прямое слияние:

* разные head alembic (слияние запрещено до выравнивания);
* пересечения UNIQUE-ключей: slugs проектов/batch/test-промптов, emails
  пользователей, codes купонов, имена fleet-узлов;
* перекрытие integer-PK диапазонов (решается ремаппингом в merge-скрипте);
* два дефолтных workflow (is_default);
* объёмы строк по таблицам и tenants без пары по email.

Ничего не пишет. Выход 0 — можно сливать (с ремаппингом), 2 — блокеры.

Использование (на VPS, где стоят postgres-драйверы):
    python scripts/audit_merge.py --main-url "postgresql+asyncpg://app@/vp?host=/var/run/postgresql" \\
        --v2-url "postgresql+asyncpg://app@/vp2?host=/var/run/postgresql"
Локальная проверка на SQLite:
    python scripts/audit_merge.py --main-url "sqlite+aiosqlite:///a.db" \\
        --v2-url "sqlite+aiosqlite:///b.db"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def _fetch(conn, sql: str, **params):
    rows = (await conn.execute(text(sql), params)).fetchall()
    return [tuple(r) for r in rows]


async def _table_names(conn) -> set[str]:
    if conn.dialect.name == "postgresql":
        rows = await _fetch(
            conn,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
        )
    else:
        rows = await _fetch(conn, "SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r[0] for r in rows}


async def snapshot(url: str) -> dict:
    """Плоский снимок базы: ревизия, счётчики, ключи, диапазоны."""
    engine = create_async_engine(url, echo=False)
    try:
        async with engine.connect() as conn:
            tables = await _table_names(conn)
            snap: dict = {"url_host": url.split("@")[-1], "tables": tables}

            def has(t: str) -> bool:
                return t in tables

            snap["alembic"] = (
                (await _fetch(conn, "SELECT version_num FROM alembic_version"))[0][0]
                if has("alembic_version")
                else None
            )
            snap["counts"] = {}
            for t in sorted(tables):
                if t == "alembic_version":
                    continue
                try:
                    snap["counts"][t] = (await _fetch(conn, f"SELECT COUNT(*) FROM {t}"))[0][0]
                except Exception:  # noqa: BLE001
                    snap["counts"][t] = "?"
            if has("projects"):
                snap["slugs"] = {r[0] for r in await _fetch(conn, "SELECT slug FROM projects")}
                snap["project_ids"] = await _fetch(conn, "SELECT MIN(id), MAX(id) FROM projects")
            if has("batch_projects"):
                snap["batch_slugs"] = {r[0] for r in await _fetch(conn, "SELECT slug FROM batch_projects")}
            if has("test_prompt_projects"):
                snap["tp_slugs"] = {r[0] for r in await _fetch(conn, "SELECT slug FROM test_prompt_projects")}
            if has("studio_users"):
                snap["emails"] = {
                    r[0]: r[1] for r in await _fetch(conn, "SELECT email, id FROM studio_users")
                }
            if has("coupons"):
                snap["coupons"] = {
                    r[0]: (r[1], r[2])
                    for r in await _fetch(conn, "SELECT code, amount_micro, used_count FROM coupons")
                }
            if has("fleet_nodes"):
                snap["fleet"] = {r[0] for r in await _fetch(conn, "SELECT name FROM fleet_nodes")}
            if has("workflows"):
                snap["default_wf"] = await _fetch(
                    conn, "SELECT id, name FROM workflows WHERE is_default = true"
                )
                # SQLite хранит bool как 0/1 — тот же запрос ок.
            if has("credit_accounts"):
                snap["tenants_money"] = {
                    r[0] for r in await _fetch(conn, "SELECT tenant_id FROM credit_accounts")
                }
            return snap
    finally:
        await engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only аудит слияния БД.")
    ap.add_argument("--main-url", required=True)
    ap.add_argument("--v2-url", required=True)
    args = ap.parse_args()

    async def _run():
        return await asyncio.gather(snapshot(args.main_url), snapshot(args.v2_url))

    main_snap, v2_snap = asyncio.run(_run())

    blockers: list[str] = []
    notes: list[str] = []
    out: list[str] = ["# Отчёт аудита слияния БД", ""]

    revs = (main_snap["alembic"], v2_snap["alembic"])
    out.append(f"head alembic: main={revs[0]} v2={revs[1]}")
    if revs[0] != revs[1]:
        blockers.append(f"разные head alembic: {revs[0]} vs {revs[1]} — выровнять перед слиянием")

    only_main = sorted(set(main_snap["counts"]) - set(v2_snap["counts"]))
    only_v2 = sorted(set(v2_snap["counts"]) - set(main_snap["counts"]))
    if only_main or only_v2:
        blockers.append(f"разный набор таблиц: только main={only_main}, только v2={only_v2}")

    out.append("")
    out.append("## Строки по таблицам (main | v2)")
    for t in sorted(set(main_snap["counts"]) | set(v2_snap["counts"])):
        out.append(f"  {t}: {main_snap['counts'].get(t)} | {v2_snap['counts'].get(t)}")

    def _overlap(name: str, a: set, b: set) -> None:
        both = sorted(a & b)
        out.append(f"## {name}: пересечение {len(both)}")
        for x in both[:20]:
            out.append(f"  - {x}")
        if both:
            notes.append(
                f"{name}: {len(both)} коллизий — {'суффикс' if 'slug' in name else 'дедуп'} при слиянии"
            )

    out.append("")
    _overlap("slugs проектов", main_snap.get("slugs", set()), v2_snap.get("slugs", set()))
    _overlap(
        "slugs batch",
        main_snap.get("batch_slugs", set()),
        v2_snap.get("batch_slugs", set()),
    )
    _overlap(
        "slugs test_prompt",
        main_snap.get("tp_slugs", set()),
        v2_snap.get("tp_slugs", set()),
    )
    _overlap("fleet_nodes", main_snap.get("fleet", set()), v2_snap.get("fleet", set()))

    out.append("## Emails пользователей (общие — склейка тенантов)")
    common_mail = sorted(set(main_snap.get("emails", {})) & set(v2_snap.get("emails", {})))
    for m in common_mail:
        out.append(f"  - {m}: main={main_snap['emails'][m]} v2={v2_snap['emails'][m]}")
    only_v2_mail = sorted(set(v2_snap.get("emails", {})) - set(main_snap.get("emails", {})))
    if only_v2_mail:
        notes.append(f"учётки только в v2 ({len(only_v2_mail)}): переносятся как есть")

    out.append("## Купоны по кодам")
    for code in sorted(set(main_snap.get("coupons", {})) | set(v2_snap.get("coupons", {}))):
        m = main_snap.get("coupons", {}).get(code)
        v = v2_snap.get("coupons", {}).get(code)
        flag = "  <-- КОЛЛИЗИЯ (победит main, used_count пересчитается)" if m and v else ""
        out.append(f"  {code}: main={m} v2={v}{flag}")

    out.append(
        f"## Дефолтные workflow: main={main_snap.get('default_wf')} "
        f"v2={v2_snap.get('default_wf')} (дефолтом останется main)"
    )
    out.append(
        f"## PK projects: main min/max={main_snap.get('project_ids')} "
        f"v2 min/max={v2_snap.get('project_ids')} (решается ремаппингом)"
    )

    out.append("")
    if blockers:
        out.append("## БЛОКЕРЫ")
        out.extend(f"  [!] {b}" for b in blockers)
    if notes:
        out.append("## Замечания слияния")
        out.extend(f"  [*] {n}" for n in notes)
    if not blockers:
        out.append("Блокеров нет: слияние возможно скриптом с ремаппингом ID.")

    print("\n".join(out))
    return 2 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
