#!/bin/sh
# Бэкап боевой базы перед выкладкой с миграцией (политика §10).
#
#   scripts/backup_db.sh [куда-положить-локально]
#
# Дамп снимается НА VPS под ролью postgres: под ролью приложения pg_dump
# падает, потому что FORCE ROW LEVEL SECURITY действует и на владельца
# таблиц. Файл забирается на эту машину — бэкап, лежащий только на той же
# машине, не бэкап.
set -eu
OUT=${1:-$HOME/backups/video-pipeline}
mkdir -p "$OUT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
remote="/tmp/vp-$stamp.sql.gz"

echo "→ дамп на VPS"
ssh studio "sudo -u postgres pg_dump vp | gzip -9 > $remote"
echo "→ забираю сюда"
scp -q "studio:$remote" "$OUT/vp-$stamp.sql.gz"
ssh studio "rm -f $remote"

size=$(du -h "$OUT/vp-$stamp.sql.gz" | cut -f1)
echo "готово: $OUT/vp-$stamp.sql.gz ($size)"
echo "напоминание: бэкап без проверенного восстановления не считается — scripts/restore_test.sh"
