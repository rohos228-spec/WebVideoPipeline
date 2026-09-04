#!/bin/sh
# Откат прода на предыдущий дайджест из docs/RELEASES.md.
#
#   scripts/rollback.sh [<дайджест или строка образа>]
#
# Дайджест неизменяем, поэтому откат детерминирован; тег `latest` для
# этого непригоден — он к моменту отката указывает уже на новую сборку.
# Критерий и порядок — docs/ENGINEERING-POLICY.md §7.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
cd "$ROOT"

target=${1:-}
if [ -z "$target" ]; then
  target=$(grep -oE 'ghcr\.io/[^`| ]+@sha256:[0-9a-f]+' docs/RELEASES.md | tail -2 | head -1)
  [ -n "$target" ] || { echo "в docs/RELEASES.md нет предыдущего дайджеста — укажи образ явно" >&2; exit 1; }
fi

echo "откат на: $target"
printf 'подтверди (yes): '; read -r ans
[ "$ans" = "yes" ] || { echo "отменено"; exit 1; }

ssh studio "cd /opt/studio && STUDIO_IMAGE='$target' ./deploy.sh"
sleep 5
ssh studio 'docker ps --format "{{.Names}} {{.Status}}" | grep studio-app-1'
printf '| `%s` | ОТКАТ | — | `%s` |\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$target" >> docs/RELEASES.md
echo "откат записан в docs/RELEASES.md; дальше — разбор и фикс обычным путём"
