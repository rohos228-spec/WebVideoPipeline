"""legacy ad-hoc колонки projects/frames → миграция

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-22

Переносит сюда два блока `ALTER TABLE`, которые жили в коде запуска:

* `app/main.py::_init_db` — 22 колонки `projects` (генераторы, hero/items,
  batch, auto_mode, …), каждая в своём try/except с warning в лог;
* `app/services/db_v2.migrate_db_v2_schema` — 3 колонки `frames`
  (uuid, sort_key, scene_id).

Идемпотентно по PRAGMA table_info: на свежей базе всё уже создано
baseline'ом из моделей, и шаг ничего не делает; на базе заказчика,
поднятой до этих полей, — дописывает недостающее ровно как раньше.

downgrade не реализован: DROP COLUMN на живых данных — потеря, а не откат.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROJECT_COLS: list[tuple[str, str]] = [
    ("image_generator", "VARCHAR(40)"),
    ("aspect_ratio", "VARCHAR(10)"),
    ("image_resolution", "VARCHAR(10)"),
    ("image_quality", "VARCHAR(10)"),
    ("image_relax", "BOOLEAN DEFAULT 0"),
    ("video_generator", "VARCHAR(40)"),
    ("video_resolution", "VARCHAR(10)"),
    ("video_relax", "BOOLEAN DEFAULT 0"),
    ("hero_count", "INTEGER"),
    ("hero_descriptions", "JSON"),
    ("hero_variations", "JSON"),
    ("hero_variation_modifiers", "JSON"),
    ("prompt_overrides", "JSON"),
    ("gpt_text_overrides", "JSON"),
    ("enrich_slots_count", "INTEGER DEFAULT 3"),
    ("item_descriptions", "JSON"),
    ("item_variations", "JSON"),
    ("batch_id", "INTEGER"),
    ("batch_position", "INTEGER"),
    ("batch_slug", "VARCHAR(120)"),
    ("auto_mode", "BOOLEAN DEFAULT 0"),
    ("title", "VARCHAR(240)"),
]

_FRAME_COLS: list[tuple[str, str]] = [
    ("uuid", "VARCHAR(64)"),
    ("sort_key", "FLOAT"),
    ("scene_id", "INTEGER"),
]


def _existing(bind, table: str) -> set[str]:
    """Колонки таблицы. Через инспектор, а не `PRAGMA table_info`: PRAGMA —
    синтаксис SQLite, и на Postgres ревизия падала бы при первом же
    прогоне (docs/SAAS-PIVOT.md §11, этап 1)."""
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _add_missing(bind, table: str, cols: list[tuple[str, str]]) -> None:
    have = _existing(bind, table)
    if not have:
        # Таблицы нет — baseline её создаст/создал; добавлять нечего.
        return
    for col, ctype in cols:
        if col in have:
            continue
        bind.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")


def upgrade() -> None:
    bind = op.get_bind()
    _add_missing(bind, "projects", _PROJECT_COLS)
    _add_missing(bind, "frames", _FRAME_COLS)


def downgrade() -> None:
    raise NotImplementedError("0002 — DROP COLUMN на живых данных не откат, а потеря")
