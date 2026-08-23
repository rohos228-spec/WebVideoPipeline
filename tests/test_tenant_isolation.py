"""Изоляция арендаторов: то, что обязано падать громко.

Все три способа потерять изоляцию бесшумны — таблица без политики видна
всем, политика без FORCE не действует на владельца, роль с BYPASSRLS
игнорирует политики. Ни один не даёт исключения сам по себе, поэтому здесь
проверяется не поведение Postgres (его без сервера не проверить), а то, что
система замечает свою незакрытость и отказывается делать вид.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.tenant import (
    TenantIsolationError,
    current_tenant,
    require_isolation,
    set_tenant,
    tenant_scope,
)
from app.services.tenant_tables import CREDIT_TABLES, INFRA_TABLES, TENANT_TABLES, check_coverage


def test_every_model_table_is_classified() -> None:
    """Новая таблица не должна тихо оказаться вне изоляции.

    Таблица без арендатора не падает — она просто видна всем сразу. Отказ
    бесшумный, значит замечать его должен тест, а не клиент.
    """
    assert check_coverage() == []


def test_infra_tables_are_deliberately_excluded() -> None:
    """Парк машин и лизинги шагов — не данные клиента.

    Арендатор у лизинга был бы бессмыслицей: лизинг принадлежит воркеру.
    """
    assert "work_leases" in INFRA_TABLES
    assert "fleet_nodes" in INFRA_TABLES
    assert not set(TENANT_TABLES) & INFRA_TABLES


def test_tenant_tables_carry_the_column() -> None:
    """Список и модели не должны разойтись: политике нужна колонка."""
    from app.models import Base

    missing = [
        name
        for name in TENANT_TABLES
        if name in Base.metadata.tables and "tenant_id" not in Base.metadata.tables[name].c
    ]
    assert missing == []


def test_credit_tables_carry_the_column() -> None:
    from app.models import Base

    for name in CREDIT_TABLES:
        assert "tenant_id" in Base.metadata.tables[name].c


def test_multitenant_on_sqlite_refuses_to_work() -> None:
    """RLS в SQLite не существует — работать «пока без изоляции» нельзя.

    Падать здесь громко правильнее, чем отдать чужой ролик: неудобство
    против утечки.
    """
    with tenant_scope(str(uuid.uuid4())):
        with pytest.raises(TenantIsolationError):
            require_isolation()


def test_single_owner_mode_is_legal() -> None:
    """Без арендатора проверка молчит: это режим владельца, а не дыра."""
    assert current_tenant() is None
    require_isolation()


def test_tenant_scope_restores_previous() -> None:
    """Воркер берёт заказ чужого арендатора и обязан вернуть контекст."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    set_tenant(a)
    try:
        with tenant_scope(b):
            assert current_tenant() == b
        assert current_tenant() == a
    finally:
        set_tenant(None)


def test_tenant_must_be_uuid() -> None:
    """Значение уезжает в SQL-настройку: всё, что прошло, — это UUID.

    Проверка формата превращает «нужно не забыть экранировать» в «нечего
    экранировать».
    """
    with pytest.raises(ValueError):
        set_tenant("'; drop table projects; --")
    with pytest.raises(ValueError):
        set_tenant("не-uuid")


def test_tenant_normalizes_to_canonical_form() -> None:
    raw = "0FC0FFEE-0000-4000-8000-000000000001"
    set_tenant(raw)
    try:
        assert current_tenant() == raw.lower()
    finally:
        set_tenant(None)


async def test_rls_report_is_honest_on_sqlite() -> None:
    """На SQLite отчёт не притворяется, что политики есть."""
    from app.db import session_scope
    from app.services.rls_check import check_rls

    async with session_scope() as s:
        report = await check_rls(s)
    assert report.dialect == "sqlite"
    # `ok` на SQLite истинно только потому, что многоарендный режим на нём
    # запрещён отдельной проверкой — см. тест выше.
    assert report.ok
    assert report.problems() == []
