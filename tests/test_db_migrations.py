"""Alembic-runner: три сценария состояния базы.

П.11 плана техдолга. До этого схема жила в `create_all` + ad-hoc `ALTER
TABLE` в `app/main.py` и `db_v2.py`; изменение существующей таблицы молча
не доезжало до баз, поднятых раньше. Тесты фиксируют, что runner:

* на пустой базе строит схему с нуля и ставит head;
* на унаследованной (create_all без `alembic_version`) не пытается
  создавать существующие таблицы, а штампует baseline и догоняет 0002+;
* повторный вызов ничего не делает.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db_migrations import BASELINE_REVISION, upgrade_to_head_sync

# Колонки, которых не было в базах «до batch/auto_mode» — их дописывает 0002.
_LEGACY_ADDED = {"batch_id", "batch_position", "batch_slug", "auto_mode", "title"}


def _tables(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _revision(db: Path) -> str | None:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()
    return rows[0][0] if rows else None


def _columns(db: Path, table: str) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_fresh_db_gets_full_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    upgrade_to_head_sync(db)

    tables = _tables(db)
    for need in ("projects", "frames", "node_runs", "llm_calls", "work_leases"):
        assert need in tables, need
    assert "alembic_version" in tables
    assert _revision(db) is not None


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "twice.db"
    upgrade_to_head_sync(db)
    first = _revision(db)
    upgrade_to_head_sync(db)
    assert _revision(db) == first


def test_legacy_db_is_stamped_and_caught_up(tmp_path: Path) -> None:
    """База «до alembic»: таблицы есть, истории нет — 0001 не переигрывается."""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, slug TEXT, topic TEXT, status TEXT)")
    con.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, number INTEGER)")
    con.execute("INSERT INTO projects (slug, topic, status) VALUES ('a', 't', 'failed')")
    con.execute("INSERT INTO projects (slug, topic, status) VALUES ('b', 't', 'images_ready')")
    con.commit()
    con.close()

    upgrade_to_head_sync(db)

    # 0002 дописал недостающие колонки, не тронув существующие данные.
    assert _columns(db, "projects") >= _LEGACY_ADDED
    assert {"uuid", "sort_key", "scene_id"} <= _columns(db, "frames")

    # 0003 сбросил отменённый статус, остальные не трогал.
    con = sqlite3.connect(db)
    try:
        rows = dict(con.execute("SELECT slug, status FROM projects").fetchall())
    finally:
        con.close()
    assert rows == {"a": "new", "b": "images_ready"}

    assert _revision(db) is not None
    assert _revision(db) >= BASELINE_REVISION


def test_baseline_downgrade_refuses(tmp_path: Path) -> None:
    """Откат baseline снёс бы базу заказчика — он намеренно не реализован."""
    import importlib

    mod = importlib.import_module("migrations.versions.0001_baseline")
    with pytest.raises(NotImplementedError):
        mod.downgrade()


def test_managed_db_missing_late_tables_is_healed(tmp_path: Path) -> None:
    """База на head без llm_calls/work_leases (модели добавились после baseline).

    Регрессия 2026-09-17: таблицы появились в models.py после 0001, миграции
    под них не было — stamp+upgrade их не создавал, INSERT падал
    `no such table: llm_calls`, учёт уходил в очередь на дозапись.
    """
    from alembic import command

    from app.db_migrations import alembic_config

    db = tmp_path / "nollm.db"
    upgrade_to_head_sync(db)
    assert _revision(db) == "0014"

    cfg = alembic_config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    from sqlalchemy import create_engine

    # Состояние базы заказчика: ревизия 0013, таблиц llm_calls/work_leases нет
    # (модели добавились после baseline, миграции не было).
    engine = create_engine(f"sqlite+pysqlite:///{db}", future=True)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0013")
    finally:
        engine.dispose()
    assert _revision(db) == "0013"
    assert "llm_calls" not in _tables(db)
    assert "work_leases" not in _tables(db)

    upgrade_to_head_sync(db)

    assert _revision(db) == "0014"
    tables = _tables(db)
    assert "llm_calls" in tables
    assert "work_leases" in tables
    # Запись тут же проходит — исходный симптом закрыт.
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO llm_calls (created_at, node_key, logical_call_id, model,"
            " served_model, relay, endpoint, cost_usd, result, error_kind,"
            " unbilled, contract_rejected, prompt_version_hash, response_id,"
            " duration_ms) VALUES (datetime('now'), 'adhoc', 't', 'm', '', '',"
            " 'chat', 0.0, 'ok', '', 0, 0, '', '', 0)"
        )
        con.commit()
    finally:
        con.close()
