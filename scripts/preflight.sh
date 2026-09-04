#!/bin/sh
# Предполётная проверка перед пушем: всё, что проверит гейт, плюс смоук
# студии — но НА СВОЁМ ЖЕЛЕЗЕ и до того, как git откроет соединение.
#
#   scripts/preflight.sh [--with-smoke <sqlite-копия>]
#
# Зачем отдельно от pre-push. Гейт идёт 10–15 минут, всё это время ssh к
# GitHub простаивает и рвётся (см. docs/RELEASE-PROCESS.md §4). Прогнав
# гейт заранее, пуш получает уже прогретый кэш и проходит быстро.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
cd "$ROOT"

echo "→ Postgres для яруса rls"
podman start vp-pg >/dev/null 2>&1 || echo "  (vp-pg уже поднят или недоступен — ярус rls покраснеет)"

echo "→ гейт push-яруса"
"$HOME/.agents/hooks/verify.sh" --stage push

if [ "${1:-}" = "--with-smoke" ]; then
  db=${2:?нужен путь к sqlite-копии базы}
  echo "→ сборка фронта"
  (cd web && npm run build >/dev/null)
  echo "→ смоук студии"
  scripts/smoke_studio.sh "$db"
fi

echo "предполётная проверка пройдена"
