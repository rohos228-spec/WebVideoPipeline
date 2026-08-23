"""Портируемость слоя данных: что ломается при смене движка молча.

Три вещи в этой кодовой базе привязаны к SQLite так, что на Postgres дают
не деградацию, а отказ или — хуже — тишину:

* распознавание «база занята» разбором текста ошибки: на Postgres такой
  строки нет никогда, обработчик «подожди и повтори» превратился бы в
  «упади непонятно почему», причём только под нагрузкой;
* `json_extract` — функция SQLite, в Postgres её нет вовсе;
* `PRAGMA` и `sqlite_master` в миграциях — ревизии не прошли бы совсем.

Проверяется поведение, а не наличие функций.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app.services.db_busy import RETRYABLE_SQLSTATES, is_db_busy


class _PgError(Exception):
    """Похоже на то, что отдаёт asyncpg: код в атрибуте, не в тексте."""

    def __init__(self, sqlstate: str):
        self.sqlstate = sqlstate
        super().__init__("could not serialize access due to concurrent update")


def test_sqlite_busy_is_recognized() -> None:
    assert is_db_busy(OperationalError("stmt", {}, Exception("database is locked")))
    assert is_db_busy("database is busy")


def test_postgres_busy_is_recognized_by_code() -> None:
    """На Postgres признак — SQLSTATE, а текста «database is locked» нет.

    Без этого повтор транзакции перестал бы срабатывать бесшумно.
    """
    for code in RETRYABLE_SQLSTATES:
        assert is_db_busy(_PgError(code)), code


def test_unrelated_error_is_not_busy() -> None:
    assert not is_db_busy(ValueError("нет такого кадра"))
    assert not is_db_busy(_PgError("23505"))  # нарушение уникальности
    assert not is_db_busy(None)


def test_busy_is_found_through_the_cause_chain() -> None:
    """SQLAlchemy заворачивает драйверную ошибку — признак внутри."""
    inner = _PgError("40001")
    outer = RuntimeError("commit failed")
    outer.__cause__ = inner
    assert is_db_busy(outer)


def test_json_field_uses_dialect_operator(monkeypatch) -> None:
    """`json_extract` — синтаксис SQLite; на Postgres нужен `->>`."""
    from sqlalchemy.dialects import postgresql, sqlite

    from app.models import Project
    from app.services.db_json import json_field
    from app.settings import settings

    monkeypatch.setattr(settings, "database_url", "")
    sqlite_sql = str(json_field(Project.meta, "mass_parent_id").compile(dialect=sqlite.dialect()))
    assert "json_extract" in sqlite_sql

    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u@h/db")
    pg_sql = str(json_field(Project.meta, "mass_parent_id").compile(dialect=postgresql.dialect()))
    assert "->>" in pg_sql
    assert "json_extract" not in pg_sql


def test_migrations_are_free_of_sqlite_only_sql() -> None:
    """Ревизии не должны содержать PRAGMA и sqlite_master.

    На них ветка Postgres упала бы на первом же прогоне — а прогон миграций
    это первое, что делает новая инсталляция.
    """
    offenders: list[str] = []
    for path in sorted(Path("migrations/versions").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.split("\n") if not line.strip().startswith("#"))
        # Строки документации упоминают PRAGMA намеренно — ищем выполняемое.
        for marker in ('exec_driver_sql("PRAGMA', 'exec_driver_sql(f"PRAGMA', "sqlite_master"):
            if marker in code:
                offenders.append(f"{path.name}: {marker}")
    assert offenders == []


def test_dialect_is_derived_from_url(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "database_url", "")
    assert settings.db_dialect == "sqlite"
    assert not settings.is_postgres

    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@h:5432/vp")
    assert settings.db_dialect == "postgresql"
    assert settings.is_postgres
    assert settings.db_url.endswith("/vp")


@pytest.mark.parametrize("table", ["projects", "llm_calls", "media_calls", "frames"])
def test_tenant_column_type_is_portable(table: str) -> None:
    """Uuid: нативный тип в Postgres, CHAR(32) в SQLite — одна модель."""
    from sqlalchemy.dialects import postgresql, sqlite

    from app.models import Base

    col = Base.metadata.tables[table].c["tenant_id"]
    assert col.type.compile(postgresql.dialect()) == "UUID"
    assert col.type.compile(sqlite.dialect()) == "CHAR(32)"
