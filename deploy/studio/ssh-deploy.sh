#!/usr/bin/env bash
# Точка входа для ssh-ключа GitHub Actions. Ставится в authorized_keys как
# `command="/opt/studio/ssh-deploy.sh"`.
#
# **Зачем прослойка, а если бы её не было.** Ограничение `command=` в
# authorized_keys НЕ выполняет то, что прислал клиент: присланная строка
# кладётся в `SSH_ORIGINAL_COMMAND`, а запускается только указанная команда.
# Если повесить туда сразу `deploy.sh`, переменная `STUDIO_IMAGE`, которую
# Actions передаёт с дайджестом, теряется молча — и выкладка берёт `:latest`
# из compose. То есть пропадает ровно та гарантия, ради которой дайджест и
# передавался: `latest` к моменту выкладки может указывать уже на следующую
# сборку.
#
# Найдено на боевом сервере при проверке ключа: команда отработала, но с
# другим образом, чем ожидалось.
#
# **Что делает эта обёртка.** Достаёт из `SSH_ORIGINAL_COMMAND` ТОЛЬКО ссылку
# на образ и ТОЛЬКО если она указывает на наш пакет в ghcr. Всё остальное
# игнорируется. Права ключа при этом не расширяются: выполнить произвольную
# команду через него по-прежнему нельзя.
set -euo pipefail

STUDIO_DIR="${STUDIO_DIR:-/opt/studio}"

#: Что вообще может быть образом студии. Проверка не косметическая: строка
#: уезжает в `docker compose pull`, то есть в команду. Разрешаем ровно наш
#: пакет и ровно две формы ссылки — тег и дайджест.
ALLOWED='^ghcr\.io/multikco/video-pipeline(@sha256:[0-9a-f]{64}|:[A-Za-z0-9][A-Za-z0-9._-]{0,127})$'

log() { echo "ssh-deploy: $*"; }

image=""
raw="${SSH_ORIGINAL_COMMAND:-}"

if [ -n "$raw" ]; then
  # Достаём значение STUDIO_IMAGE=… из присланной строки, чем бы она ни была.
  # Не исполняем её и не eval-им: содержимое пришло по сети.
  candidate=$(printf '%s' "$raw" | grep -oE 'STUDIO_IMAGE=[^[:space:]'"'"'"]+' | head -1 | cut -d= -f2- || true)
  if [ -n "$candidate" ]; then
    if printf '%s' "$candidate" | grep -qE "$ALLOWED"; then
      image="$candidate"
      log "образ из запроса: $image"
    else
      log "ОТКАЗ: ссылка на образ не похожа на наш пакет: ${candidate:0:80}" >&2
      exit 2
    fi
  fi
fi

if [ -z "$image" ]; then
  # Не ошибка: так выглядит ручной вызов `ssh <хост>` без аргументов.
  log "образ не передан — берём то, что в compose"
fi

cd "$STUDIO_DIR"
if [ -n "$image" ]; then
  STUDIO_IMAGE="$image" exec ./deploy.sh
fi
exec ./deploy.sh
