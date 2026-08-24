"""учётные записи студии: своя личность вместо токена биллинга

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-24

До этой ревизии личность приходила снаружи: `llm-gateway/billing` подписывал
JWT, `sub` из него становился `tenant_id`, и никакой таблицы пользователей у
студии не было. Решение владельца от 2026-08-24 сняло связь — платежей студия
не принимает, продукт внутренний, — и личность переехала сюда.

**Почему `id` совпадает с `tenant_id` остальных таблиц.** Изоляция уже
построена на `tenant_id UUID` и политиках RLS (ревизия 0006). Отдельный ключ
пользователя плюс таблица соответствия добавили бы джойн ради ничего: один
человек — один арендатор. Поэтому первичный ключ здесь `Uuid`, и он же уезжает
в `SET LOCAL app.tenant_id`.

**Почему на таблице НЕТ политики RLS.** `studio_users` — не данные арендатора,
а список арендаторов. Политика «видно только своё» на ней означала бы, что
форма входа не может найти пользователя до того, как узнает, кто он, то есть
войти нельзя вовсе. Читает таблицу слой личности до назначения арендатора
(`app/web/identity.py::_assert_live`), и это единственный правильный порядок.
Роль базы при этом обычная, не `BYPASSRLS`: политик на этой таблице просто
нет, а на остальных они остаются в силе.

**`token_epoch` — механизм отзыва.** Сессионный токен подписан и проверяется
без базы; погасить подписанный токен нечем. Счётчик едет в токен и сверяется
со строкой: смена пароля или отключение учётки поднимают его, и все выданные
токены становятся недействительны в тот же миг.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "studio_users"


def upgrade() -> None:
    # Как в 0007–0010: baseline (0001) создаёт схему из моделей, поэтому на
    # пустой базе таблица уже есть — ревизия должна быть к этому безразлична.
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        # argon2id: соль и параметры внутри строки, отдельных колонок не нужно.
        sa.Column("password_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    # Уникальность по адресу — индексом, а не только проверкой в коде: код
    # проверяет «не занят ли» и вставляет двумя действиями, между которыми
    # помещается второй такой же запрос.
    op.create_index(f"ix_{TABLE}_email", TABLE, ["email"], unique=True)
    op.create_index(f"ix_{TABLE}_role", TABLE, ["role"])
    op.create_index(f"ix_{TABLE}_is_active", TABLE, ["is_active"])


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
