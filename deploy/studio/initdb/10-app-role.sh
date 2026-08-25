#!/bin/sh
# Роль приложения. Выполняется ОДИН раз — при создании тома базы.
#
# Роль намеренно обычная: не суперпользователь и без BYPASSRLS. И то и другое
# обходит политики row-level security молча — тесты изоляции прошли бы
# «зелёными», не проверив ничего, а в проде это утечка чужого ролика без
# единой строки в журнале. Приложение проверяет это на старте
# (`app/services/rls_check.py`) и отказывается подниматься.
#
# Владельцем таблиц роль становится сама: схему создают миграции, которые она
# же и запускает. Владелец важен — `FORCE ROW LEVEL SECURITY` достаёт как раз
# его, обычные политики на владельца не действуют.
set -e

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${POSTGRES_PASSWORD_APP:?нужен POSTGRES_PASSWORD_APP}" "${POSTGRES_USER_APP:?нужен POSTGRES_USER_APP}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
  create role "$POSTGRES_USER_APP" login password '$POSTGRES_PASSWORD_APP';
  grant all on schema public to "$POSTGRES_USER_APP";
  alter schema public owner to "$POSTGRES_USER_APP";
EOSQL

echo "роль $POSTGRES_USER_APP заведена (обычная, без BYPASSRLS)"
