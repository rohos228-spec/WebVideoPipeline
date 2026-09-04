#!/bin/sh
# Проверка восстановлением (политика §10). Раз в месяц: последний дамп
# разворачивается в отдельную базу и на нём прогоняются миграции.
#
#   scripts/restore_test.sh [дамп.sql.gz]
#
# Провал — инцидент: дамп, который не разворачивается, хуже отсутствия
# бэкапа, потому что создаёт ложное спокойствие.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
cd "$ROOT"
DUMP=${1:-$(ls -1t "$HOME"/backups/video-pipeline/*.sql.gz 2>/dev/null | head -1)}
[ -n "${DUMP:-}" ] && [ -f "$DUMP" ] || { echo "нет дампа: scripts/backup_db.sh" >&2; exit 1; }

DB="vp_restore_test_$(date -u +%H%M%S)"
echo "→ разворачиваю $DUMP в $DB"
podman start vp-pg >/dev/null 2>&1 || true
podman exec vp-pg psql -U app -c "DROP DATABASE IF EXISTS $DB" postgres
podman exec vp-pg psql -U app -c "CREATE DATABASE $DB" postgres
gunzip -c "$DUMP" | podman exec -i vp-pg psql -U app -q "$DB" >/dev/null

echo "→ миграции поверх восстановленной базы"
DATABASE_URL="postgresql+asyncpg://app:app@127.0.0.1/$DB" .venv/bin/alembic upgrade head

rows=$(podman exec vp-pg psql -U app -tAc "SELECT count(*) FROM projects" "$DB")
echo "проектов в восстановленной базе: $rows"
podman exec vp-pg psql -U app -c "DROP DATABASE $DB" postgres >/dev/null
echo "восстановление проверено"
