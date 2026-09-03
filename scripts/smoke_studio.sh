#!/bin/sh
# Смоук студии на КОПИИ базы, без метки сессии (headless Chromium, не agent-browser).
#
#   scripts/smoke_studio.sh <sqlite-копия> [project_id]
#
# Поднимает web-only uvicorn на 127.0.0.1:8765 поверх временной копии базы,
# гоняет scripts/smoke_studio.mjs (Playwright из web/node_modules), кладёт
# скриншоты в $SMOKE_OUT (по умолчанию /tmp/vp-smoke), гасит сервер.
# Ключей провайдеров не нужно: LLM-вызовы отвечают 5xx, это ожидаемо.
set -eu
SRC=${1:?путь к sqlite-копии базы (снять с прода: docker cp studio-app-1:/app/data/state.db …)}
PROJECT=${2:-3}
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
OUT=${SMOKE_OUT:-/tmp/vp-smoke}
mkdir -p "$OUT"
cp "$SRC" "$OUT/state-smoke.db"
cd "$ROOT"
test -d web/out || { echo "web/out нет — сначала: cd web && npm run build"; exit 2; }
SQLITE_PATH="$OUT/state-smoke.db" DATABASE_URL= TELEGRAM_ENABLED=false WEB_PORT=8765 LOG_LEVEL=WARNING \
  .venv/bin/uvicorn app.web.api:create_app --factory --host 127.0.0.1 --port 8765 --log-level warning \
  > "$OUT/web.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for i in $(seq 1 60); do
  curl -s -o /dev/null http://127.0.0.1:8765/pipeline && break
  sleep 1
done
S="$OUT" PROJECT="$PROJECT" node scripts/smoke_studio.mjs
echo "скриншоты: $OUT"
grep -iE 'traceback|error' "$OUT/web.log" | grep -v 'сессия nope' | head -5 || true
