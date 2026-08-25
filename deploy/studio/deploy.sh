#!/usr/bin/env bash
# Выкладка студии на VPS. Зовётся вручную и из GitHub Actions по ssh.
#
#   ./deploy.sh                  # обновить до образа из STUDIO_IMAGE или latest
#   ./deploy.sh status           # что сейчас запущено
#   ./deploy.sh logs [служба]    # хвост журнала
#   ./deploy.sh backup           # дамп базы в ./backups
#   ./deploy.sh rollback         # вернуться на предыдущий образ
#   ./deploy.sh admin            # завести администратора студии
#
# **Почему дайджест, а не тег.** GitHub Actions передаёт `STUDIO_IMAGE` с
# `@sha256:…`. Тег `latest` к моменту выкладки может уже указывать на следующую
# сборку, и на сервер уедет не то, что прошло гейт в этом прогоне. Дайджест
# неподвижен, и он же делает откат честным: предыдущий записан в ./.last-image.
#
# **Почему бэкап до, а не после.** Миграции применяются автоматически на старте
# приложения. Откат образа схему назад не откатывает — `downgrade` в этом
# проекте намеренно отказывает. Значит единственный способ вернуться из плохой
# миграции — дамп, снятый ДО неё.
#
# **База на ХОСТЕ, не в контейнере** (решение владельца 2026-08-25). Ставится
# один раз `sudo ./setup-postgres.sh`; контейнер ходит к ней через
# смонтированный unix-сокет, TCP у базы выключен. Отсюда две особенности этого
# скрипта: `pg_dump` берётся с хоста, а доступность базы проверяется явно —
# `depends_on: service_healthy` больше некому ждать.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# `--env-file ./env` обязателен, и это не стилистика.
#
# compose берёт значения для подстановки `${…}` из окружения и из файла `.env`
# рядом — но НЕ из `env_file:` внутри службы: тот отдаёт переменные контейнеру
# и в интерполяции не участвует. Файл здесь называется `env`, а не `.env`,
# намеренно (см. env.template), поэтому без явного `--env-file` выкладка
# падает на первой же обязательной переменной:
#
#   required variable POSTGRES_PASSWORD is missing a value
#
# Проверено: без флага `docker compose config` отказывается собирать конфиг.
COMPOSE=(docker compose --env-file ./env)
LAST_IMAGE_FILE=".last-image"
BACKUP_DIR="backups"

die() { echo "деплой: $*" >&2; exit 1; }

require_env_file() {
  [ -f ./env ] || die "нет файла ./env — скопируйте env.template и заполните"
  # 0600: в файле секрет подписи сессий и ключи провайдеров. Файл, который
  # читает кто угодно на машине, — это те же секреты, только с лишним шагом.
  local mode
  mode=$(stat -c '%a' ./env)
  [ "$mode" = "600" ] || die "./env с правами $mode — нужно 600 (chmod 600 env)"
}

require_prompts() {
  # Библиотека мастер-промтов монтируется томом и в образ не входит. Без неё
  # конвейер падает на первом же шаге — и падает данными, а не кодом, то есть
  # разбираться будут не там. Проверка нужна только на ПЕРВОМ запуске: дальше
  # источник — база.
  if [ ! -d ./prompts ] || [ -z "$(ls -A ./prompts 2>/dev/null)" ]; then
    echo "деплой: ВНИМАНИЕ — ./prompts пуст." >&2
    echo "  Библиотека промтов намеренно вне git и вне образа. Если база уже" >&2
    echo "  наполнена (не первый запуск) — это нормально. Если первый —" >&2
    echo "  скопируйте библиотеку: rsync -a <ПК>:video-pipeline/prompts/ ./prompts/" >&2
  fi
}

cmd_deploy() {
  require_env_file
  require_prompts

  local image="${STUDIO_IMAGE:-}"
  if [ -n "$image" ]; then
    export STUDIO_IMAGE="$image"
    echo "деплой: образ $image"
  else
    echo "деплой: STUDIO_IMAGE не задан — берём то, что в compose"
  fi

  # Что работает сейчас — чтобы было куда откатиться.
  local previous
  previous=$(docker inspect --format '{{.Image}}' studio-app-1 2>/dev/null || true)

  # База живёт на ХОСТЕ, и `depends_on: service_healthy` её больше не ждёт.
  # Поднимать приложение против неотвечающей базы можно (его перезапустит
  # restart-политика), но тогда «деплой не удался» скажет healthcheck через две
  # минуты вместо внятной строки здесь.
  db_conn
  if ! PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    die "Postgres на хосте не отвечает ($DB_SOCK). Проверьте: systemctl status postgresql"
  fi
  echo "деплой: база отвечает ($DB_NAME на $DB_SOCK)"

  echo "деплой: бэкап базы"
  cmd_backup

  echo "деплой: тянем образы"
  "${COMPOSE[@]}" pull --quiet

  echo "деплой: поднимаем"
  # --wait: команда возвращается, только когда healthcheck прошёл. Без него
  # «деплой прошёл» означает «docker принял команду», а упало оно или нет —
  # выяснится от оператора.
  if ! "${COMPOSE[@]}" up -d --remove-orphans --wait --wait-timeout 180; then
    echo "деплой: контейнер не стал здоровым — журнал ниже" >&2
    "${COMPOSE[@]}" logs --tail 80 app >&2
    die "выкладка не удалась; откат: ./deploy.sh rollback"
  fi

  # Именно `if`, а не `[ … ] && …`: под `set -e` неуспешная проверка в конце
  # блока завершает скрипт с кодом 1. На ПЕРВОМ деплое предыдущего образа нет,
  # и выкладка сообщала бы об ошибке сразу после успешного подъёма.
  if [ -n "$previous" ]; then
    echo "$previous" > "$LAST_IMAGE_FILE"
  fi

  echo "деплой: готово"
  "${COMPOSE[@]}" ps
}

# Разбирает DATABASE_URL из ./env на части для pg_dump.
#
# Ходить в базу тем же способом, что и приложение, — единственный честный
# вариант: если строка подключения врёт, бэкап обязан упасть здесь, а не
# выясниться в момент восстановления.
db_conn() {
  # ./env заполняет оператор, в git его нет — статически проверять нечего.
  set -a
  # shellcheck disable=SC1091
  . ./env 2>/dev/null || true
  set +a

  [ -n "${DATABASE_URL:-}" ] || die "в ./env нет DATABASE_URL"

  # postgresql+asyncpg://user:pass@/dbname?host=/var/run/postgresql
  #
  # Разбор нежадный (`[^@]*`) — ровно как у SQLAlchemy: проверено, `make_url`
  # на пароле с сырым `@` тоже обрезает по первому. Совпадение важнее
  # «правильности»: если разбор здесь и в приложении разойдётся, бэкап будет
  # ходить под другим паролем, и выяснится это в момент восстановления.
  DB_USER=$(printf '%s' "$DATABASE_URL" | sed -n 's#.*://\([^:]*\):.*#\1#p')
  DB_PASS=$(printf '%s' "$DATABASE_URL" | sed -n 's#.*://[^:]*:\([^@]*\)@.*#\1#p')
  DB_NAME=$(printf '%s' "$DATABASE_URL" | sed -n 's#.*@/\([^?]*\).*#\1#p')
  DB_SOCK=$(printf '%s' "$DATABASE_URL" | sed -n 's#.*[?&]host=\([^&]*\).*#\1#p')
  DB_SOCK=${DB_SOCK:-/var/run/postgresql}

  # Процент-декодирование. Спецсимволы в пароле обязаны быть закодированы
  # (`@` → `%40`), и SQLAlchemy их раскодирует. Без этого шага приложение
  # получало бы `p@ss`, а pg_dump — литерал `p%40ss`: аутентификация падала бы
  # ТОЛЬКО в бэкапе, то есть там, где это заметят позже всего.
  DB_PASS=$(printf '%b' "${DB_PASS//%/\\x}")

  [ -n "$DB_USER" ] && [ -n "$DB_NAME" ] || die "не разобрал DATABASE_URL: ожидается postgresql+asyncpg://user:pass@/db?host=/var/run/postgresql"
}

cmd_backup() {
  # 0700/0600 с самого начала. Дамп — это ВСЯ база: промт-библиотека, проекты,
  # хеши паролей. Права по умолчанию (0644) отдают его любому пользователю
  # системы, а замечают это, когда пользователь уже появился.
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  umask 077
  local stamp file
  stamp=$(date +%Y%m%d-%H%M%S)
  file="$BACKUP_DIR/db-$stamp.sql.gz"

  db_conn

  if ! command -v pg_dump >/dev/null 2>&1; then
    die "нет pg_dump на хосте — база стоит здесь же, ставьте postgresql-client"
  fi
  if ! PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo "деплой: база не отвечает — бэкап пропущен (postgres поднимается?)"
    return 0
  fi

  # Дамп снимается ОТ СУПЕРПОЛЬЗОВАТЕЛЯ, и это не лень, а необходимость.
  #
  # Роль приложения владеет таблицами, но ревизия 0006 ставит на них
  # `FORCE ROW LEVEL SECURITY` — а она действует и на владельца. pg_dump под
  # ролью приложения падает на первой же таблице:
  #
  #   ERROR: query would be affected by row-level security policy for table "artifacts"
  #
  # Обойти это можно тремя способами, и два из них плохие: снять FORCE (потеря
  # изоляции) или выдать роли приложения BYPASSRLS (то же самое, только
  # незаметнее). Третий — снимать дамп отдельной привилегией, и пусть она
  # живёт в sudoers, а не паролем в ./env: утёкший ./env тогда даёт роль,
  # ограниченную арендатором, а не полный слепок базы.
  #
  # Правило заводит setup-postgres.sh: `<пользователь> ALL=(postgres) NOPASSWD: …/pg_dump`.
  # Проба дешёвая: `--version` разрешён тем же правилом sudoers, что и сам
  # дамп. Пробовать настоящим дампом значило бы выгружать базу дважды.
  if ! sudo -n -u postgres pg_dump --version >/dev/null 2>&1; then
    die "$(cat <<'EOM'
не могу снять дамп от имени postgres.

  Роль приложения не годится: FORCE ROW LEVEL SECURITY действует и на владельца
  таблиц, и pg_dump под ней падает на первой же таблице.

  Разрешить (один раз, от root):
      echo '<пользователь> ALL=(postgres) NOPASSWD: /usr/bin/pg_dump' \
        > /etc/sudoers.d/studio-backup && chmod 440 /etc/sudoers.d/studio-backup

  Это же делает sudo ./setup-postgres.sh.
EOM
)"
  fi

  sudo -n -u postgres pg_dump -d "$DB_NAME" | gzip > "$file"

  echo "деплой: дамп $file ($(du -h "$file" | cut -f1))"
  # Держим последние 14: дампы этой базы — мегабайты, но каталог без уборки
  # однажды займёт диск, и узнают об этом по остановившейся студии.
  # shellcheck disable=SC2012  # имена дампов задаём мы: db-<timestamp>.sql.gz
  ls -1t "$BACKUP_DIR"/db-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --
}

cmd_rollback() {
  [ -f "$LAST_IMAGE_FILE" ] || die "нет $LAST_IMAGE_FILE — откатываться некуда"
  local previous
  previous=$(cat "$LAST_IMAGE_FILE")
  echo "деплой: откат на $previous"
  echo "деплой: ВНИМАНИЕ — схему базы откат не трогает. Если проблема в"
  echo "        миграции, восстанавливайте из $BACKUP_DIR."
  STUDIO_IMAGE="$previous" "${COMPOSE[@]}" up -d --wait --wait-timeout 180
  "${COMPOSE[@]}" ps
}

cmd_admin() {
  require_env_file
  echo "деплой: заводим администратора студии"
  # Пароль печатается ОДИН раз и нигде не сохраняется — в базе argon2id-хеш.
  "${COMPOSE[@]}" exec app python -m app.seed_admin "$@"
}

cmd_status() {
  "${COMPOSE[@]}" ps
  echo
  echo "образ приложения: $(docker inspect --format '{{index .Config.Image}}' studio-app-1 2>/dev/null || echo '—')"
  echo "здоровье:        $(docker inspect --format '{{.State.Health.Status}}' studio-app-1 2>/dev/null || echo '—')"
  if [ -f "$LAST_IMAGE_FILE" ]; then
    echo "откат на:        $(cat "$LAST_IMAGE_FILE")"
  fi
  echo
  if db_conn 2>/dev/null && PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo "база (хост):     $DB_NAME на $DB_SOCK — отвечает"
  else
    echo "база (хост):     НЕ ОТВЕЧАЕТ — systemctl status postgresql"
  fi
  echo
  df -h /var/lib/docker 2>/dev/null | tail -1 || true
}

case "${1:-deploy}" in
  deploy|"")   cmd_deploy ;;
  backup)      cmd_backup ;;
  rollback)    cmd_rollback ;;
  admin)       shift; cmd_admin "$@" ;;
  status)      cmd_status ;;
  logs)        shift; "${COMPOSE[@]}" logs -f --tail 200 "${@:-app}" ;;
  *)           die "неизвестная команда ${1}; есть: deploy, backup, rollback, admin, status, logs" ;;
esac
