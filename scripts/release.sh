#!/bin/sh
# Релиз: тонкая обёртка над тем, что уже делает release.yml. Своей сборки
# и своего деплоя здесь нет намеренно — вторая реализация разъезжается с
# первой (docs/ENGINEERING-POLICY.md §6).
#
#   scripts/release.sh [--tag v0.2.0]
#
# Делает: предполётную проверку → push с keepalive → ожидание Release →
# проверку прода → запись в docs/releases.log.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
cd "$ROOT"

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "main" ] || { echo "релиз идёт с main, сейчас $branch" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "рабочее дерево грязное" >&2; exit 1; }

tag=""
[ "${1:-}" = "--tag" ] && { tag=${2:?нужен тег вида v0.2.0}; }

scripts/preflight.sh

sha=$(git rev-parse HEAD)
echo "→ push (keepalive: ssh рвётся, если гейт идёт дольше простоя)"
git -c core.sshCommand="ssh -o ServerAliveInterval=20 -o ServerAliveCountMax=90" push origin main

if [ -n "$tag" ]; then
  git tag -a "$tag" -m "release $tag"
  git -c core.sshCommand="ssh -o ServerAliveInterval=20 -o ServerAliveCountMax=90" push origin "$tag"
fi

echo "→ ожидание Release"
python3 scripts/watch_release.py "$sha"

echo "→ проверка прода"
digest=$(ssh studio 'docker inspect studio-app-1 --format "{{.Config.Image}}"' 2>/dev/null || echo "?")
health=$(ssh studio 'docker ps --format "{{.Names}} {{.Status}}" | grep studio-app-1' 2>/dev/null || echo "?")
echo "  $health"
echo "  $digest"

printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "${tag:-—}" "$sha" "$digest" >> docs/releases.log
echo "записано в docs/releases.log; откат — scripts/rollback.sh"
