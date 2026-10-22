"""Таблицы движка на живом Postgres в тестах.

``TEST_DATABASE_URL`` (как у ``tests/test_rls_postgres.py``) переводит базу
движка на этот сервер: схема — миграциями alembic до head, один раз на
прогон; перед каждым тестом таблицы движка очищаются, кэши соединений и
схемы сбрасываются. Роль в URL — обычная (не суперпользователь).
"""

from __future__ import annotations

import os

from sqlalchemy import text

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
#: Тесты ядра (``tests/agents``) гонят таблицы движка на Postgres только по
#: явному флагу: в CI ``TEST_DATABASE_URL`` задан ради RLS-тестов.
ENGINE_TESTS_ON_POSTGRES = bool(TEST_DB_URL) and os.environ.get("ORCH_TEST_ON_POSTGRES", "").strip() == "1"
SQLITE_ONLY_REASON = "проверка блокировок и PRAGMA файла SQLite: на Postgres их нет"

ENGINE_TABLES = (
    "chat_notices",
    "notification_log",
    "orch_llm_calls",
    "orch_messages",
    "orch_event_seq",
    "orch_events",
    "orch_checkpoints",
    "orch_steps",
    "orch_tasks",
    "orch_report_versions",
    "orch_reports",
    "node_prompt_versions",
    "node_defs",
)
_migrated = False


def reset_engine_state() -> None:
    """Закрыть соединения шины, забыть «схема готова» и пулы движков."""
    from app.orchestrator import events
    from app.orchestrator.db import migrations
    from app.orchestrator.db.engine import dispose_engines
    from app.services import chat_notice, notification_log

    with events._LOCK:
        for conn in list(events._CONNS.values()):
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        events._CONNS.clear()
        events._BATCHES.clear()
        events._RESERVES.clear()
    migrations._MIGRATED.clear()
    chat_notice.reset()
    notification_log.reset_stats()
    dispose_engines()


def prepare_postgres() -> None:
    """alembic до head (один раз) и пустые таблицы движка."""
    global _migrated
    from app.db_migrations import upgrade_to_head_sync
    from app.orchestrator.db.engine import get_engine

    if not _migrated:
        upgrade_to_head_sync()
        _migrated = True
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(ENGINE_TABLES)} RESTART IDENTITY CASCADE"))
