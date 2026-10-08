"""pipeline_mode проектов: изоляция механик v1/v2.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08

Каждый проект принадлежит ровно одной механике: ``"v1"`` (классический
линейный пайплайн) или ``"v2"`` (режиссёрский монтаж: дубли shot_01/shot_02,
coverage, референсы). Режим назначается при создании и дальше immutable —
проекты механик строго изолированы фильтром ``WHERE pipeline_mode``, а не
конвертируются друг в друга.

**Предикат backfill (почему именно такой).** Единственный эксклюзивный
след механики на уровне данных — shot02-промпты в ``frames.attrs``
(``image_prompt_shot2`` / ``animation_prompt_shot2``): их пишет только
v2-механика дублей. Проект без shot02-следов в v1-интерфейсе
отображается полностью — терять нечего, прятать нечего.

Сознательно НЕ используется ``meta.montage_board``: монтажная доска пишет
туда и в v1 (trim/очередь/подсветка общие), так что её наличие не
различает механики. Проекты с непустым ``montage_board``, но без shot02
уходят в ``"v1"`` и попадают в review-список лога — их видно целиком,
решать нечего, но след оставлен.

BatchProject (контейнер массовых) режим не несёт: режим живут у
проектов-детей, они и фильтруются.

downgrade служебный (сброс колонки и индекса, режим теряется — как сид
купонов в 0016): нужен тесту «база заказчика», который даунгрейдится
сквозь все ревизии.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from loguru import logger

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "pipeline_mode"
INDEX = "ix_projects_pipeline_mode"

#: Ключи shot02-дублей — единственный эксклюзивный след v2 (см. шапку).
_SHOT2_ATTRS: tuple[str, ...] = ("image_prompt_shot2", "animation_prompt_shot2")


def _as_dict(raw: Any) -> dict:
    """JSON-колонка: dict на Postgres, строка на SQLite, None у пустых."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _uses_v2(attrs_rows: list[Any], meta: dict) -> bool:
    """Проект трогал v2-механику дублей."""
    for attrs in attrs_rows:
        fields = _as_dict(attrs)
        for key in _SHOT2_ATTRS:
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def _board_keys(meta: dict) -> list[str]:
    board = meta.get("montage_board")
    if isinstance(board, dict):
        return sorted(str(k) for k in board)
    return []


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" not in inspector.get_table_names():
        # Свежая база без таблиц: baseline создаст колонку из моделей сам.
        return

    columns = {col["name"] for col in inspector.get_columns("projects")}
    if COLUMN not in columns:
        # NOT NULL с дефолтом: SQLite разрешает ADD COLUMN только так,
        # на Postgres дефолт заодно закроет существующие строки.
        bind.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {COLUMN} VARCHAR(2) NOT NULL DEFAULT 'v1'")
        logger.info("0017: колонка projects.pipeline_mode добавлена")

    indexes = {idx["name"] for idx in inspector.get_indexes("projects")}
    if INDEX not in indexes:
        op.create_index(INDEX, "projects", [COLUMN])
        logger.info("0017: индекс {} создан", INDEX)

    # Backfill по следам механики (см. предикат в шапке). Только если схема
    # полная: на унаследованных базах колонок meta/attrs может не быть
    # (их дописывают другие ревизии), тогда классифицировать не по чему
    # и все остаются на дефолте "v1" — безопасное поведение.
    project_cols = {col["name"] for col in inspector.get_columns("projects")}
    frame_cols = (
        {col["name"] for col in inspector.get_columns("frames")}
        if "frames" in inspector.get_table_names()
        else set()
    )
    if not ({"id", "slug", "meta"} <= project_cols and {"project_id", "attrs"} <= frame_cols):
        logger.info("0017: схема неполная — backfill пропущен, все проекты на дефолте v1")
        return
    rows = bind.exec_driver_sql("SELECT id, slug, meta FROM projects").fetchall()
    frames = bind.exec_driver_sql("SELECT project_id, attrs FROM frames").fetchall()
    attrs_by_project: dict[Any, list[Any]] = {}
    for project_id, attrs in frames:
        attrs_by_project.setdefault(project_id, []).append(attrs)

    v2_ids: list[Any] = []
    review: list[str] = []
    for project_id, slug, meta_raw in rows:
        meta = _as_dict(meta_raw)
        project_attrs = attrs_by_project.get(project_id, [])
        if _uses_v2(project_attrs, meta):
            v2_ids.append(project_id)
        elif _board_keys(meta):
            review.append(f"{slug} (montage_board: {','.join(_board_keys(meta))})")

    slug_by_id = {r[0]: r[1] for r in rows}
    if v2_ids:
        # Одним statement вместо N+1: id только из нашей же таблицы.
        bind.exec_driver_sql(
            f"UPDATE projects SET {COLUMN} = 'v2' WHERE id IN ({','.join(str(int(i)) for i in v2_ids)})"
        )
    logger.info(
        "0017: backfill pipeline_mode — всего {}, v1: {}, v2: {} ({})",
        len(rows),
        len(rows) - len(v2_ids),
        len(v2_ids),
        ",".join(str(slug_by_id[i]) for i in v2_ids),
    )
    if review:
        logger.warning(
            "0017: на ревью (montage_board без shot02, оставлены v1): {}",
            "; ".join(review),
        )


def downgrade() -> None:
    # Dev/test путь (тест «база заказчика на 0013» даунгрейдится сквозь все
    # ревизии). Режим проектов при этом теряется — как и сид купонов в
    # 0016: даунгрейд здесь служебный, не миграция данных.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" not in inspector.get_table_names():
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("projects")}
    if INDEX in indexes:
        bind.exec_driver_sql(f"DROP INDEX {INDEX}")
    columns = {col["name"] for col in inspector.get_columns("projects")}
    if COLUMN in columns:
        bind.exec_driver_sql(f"ALTER TABLE projects DROP COLUMN {COLUMN}")
