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
  local target="${1:-./env}"
  [ -f "$target" ] || die "нет файла $target — скопируйте env.template и заполните"
  local mode
  mode=$(stat -c '%a' "$target")
  [ "$mode" = "600" ] || die "$target с правами $mode — нужно 600 (chmod 600 $target)"
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
  local target="${1:-app}"
  local env_file="./env"
  local svc="app"
  local container="studio-app-1"
  local img="${STUDIO_IMAGE:-}"
  local last_file="$LAST_IMAGE_FILE"

  if [ "$target" = "v2" ]; then
    env_file="./env.v2"
    svc="app-v2"
    container="studio-app-v2-1"
    img="${STUDIO_V2_IMAGE:-}"
    last_file=".last-image-v2"
  fi

  require_env_file "$env_file"
  require_prompts

  if [ -n "$img" ]; then
    if [ "$target" = "v2" ]; then
      export STUDIO_V2_IMAGE="$img"
    else
      export STUDIO_IMAGE="$img"
    fi
    echo "деплой ($svc): образ $img"
  else
    echo "деплой ($svc): переменная не задана — берём то, что в compose"
  fi

  # Что работает сейчас — чтобы было куда откатиться.
  local previous
  previous=$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true)

  db_conn "$env_file"
  if ! PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    die "Postgres на хосте не отвечает ($DB_SOCK). Проверьте: systemctl status postgresql"
  fi
  echo "деплой ($svc): база отвечает ($DB_NAME на $DB_SOCK)"

  echo "деплой ($svc): бэкап базы"
  cmd_backup "$env_file"

  echo "деплой ($svc): тянем образы"
  docker compose --env-file "$env_file" pull --quiet "$svc"

  echo "деплой ($svc): поднимаем"
  if ! docker compose --env-file "$env_file" up -d --remove-orphans --wait --wait-timeout 180 "$svc"; then
    echo "деплой ($svc): контейнер не стал здоровым — журнал ниже" >&2
    docker compose --env-file "$env_file" logs --tail 80 "$svc" >&2
    die "выкладка $svc не удалась; откат: ./deploy.sh rollback $target"
  fi

  if [ -n "$img" ] && [[ "$img" == *@sha256:* ]]; then
    local tag_name="latest"
    [ "$target" = "v2" ] && tag_name="v2"
    docker tag "$img" "${img%@*}:$tag_name" \
      || echo "деплой: тег $tag_name не обновлён (не критично)" >&2
  fi

  if [ -n "$previous" ]; then
    echo "$previous" > "$last_file"
  fi

  echo "деплой ($svc): готово"
  docker compose --env-file "$env_file" ps "$svc"
}

# Разбирает DATABASE_URL из ./env на части для pg_dump.
#
# Ходить в базу тем же способом, что и приложение, — единственный честный
# вариант: если строка подключения врёт, бэкап обязан упасть здесь, а не
# выясниться в момент восстановления.
db_conn() {
  local target_env="${1:-./env}"
  # ./env заполняет оператор, в git его нет — статически проверять нечего.
  set -a
  # shellcheck disable=SC1090
  . "$target_env" 2>/dev/null || true
  set +a

  [ -n "${DATABASE_URL:-}" ] || die "в $target_env нет DATABASE_URL"

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
  local target_env="${1:-./env}"
  # 0700/0600 с самого начала. Дамп — это ВСЯ база: промт-библиотека, проекты,
  # хеши паролей. Права по умолчанию (0644) отдают его любому пользователю
  # системы, а замечают это, когда пользователь уже появился.
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  umask 077
  local stamp file
  stamp=$(date +%Y%m%d-%H%M%S)
  file="$BACKUP_DIR/db-$stamp.sql.gz"

  db_conn "$target_env"

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
  local target="${1:-app}"
  local env_file="./env"
  local svc="app"
  local last_file="$LAST_IMAGE_FILE"

  if [ "$target" = "v2" ]; then
    env_file="./env.v2"
    svc="app-v2"
    last_file=".last-image-v2"
  fi

  require_env_file "$env_file"
  [ -f "$last_file" ] || die "нет $last_file — откатываться некуда"
  local previous
  previous=$(cat "$last_file")
  echo "деплой ($svc): откат на $previous"
  echo "деплой ($svc): ВНИМАНИЕ — схему базы откат не трогает. Если проблема в"
  echo "        миграции, восстанавливайте из $BACKUP_DIR."
  if [ "$target" = "v2" ]; then
    STUDIO_V2_IMAGE="$previous" docker compose --env-file "$env_file" up -d --wait --wait-timeout 180 "$svc"
  else
    STUDIO_IMAGE="$previous" docker compose --env-file "$env_file" up -d --wait --wait-timeout 180 "$svc"
  fi
  docker compose --env-file "$env_file" ps "$svc"
}

cmd_admin() {
  local svc="app"
  local env_file="./env"
  if [ "${1:-}" = "v2" ]; then
    svc="app-v2"
    env_file="./env.v2"
    shift
  fi
  require_env_file "$env_file"
  echo "деплой ($svc): заводим администратора студии"
  # Пароль печатается ОДИН раз и нигде не сохраняется — в базе argon2id-хеш.
  docker compose --env-file "$env_file" exec "$svc" python -m app.seed_admin "$@"
}

cmd_status() {
  "${COMPOSE[@]}" ps
  echo
  echo "образ приложения v1: $(docker inspect --format '{{index .Config.Image}}' studio-app-1 2>/dev/null || echo '—')"
  echo "здоровье v1:        $(docker inspect --format '{{.State.Health.Status}}' studio-app-1 2>/dev/null || echo '—')"
  echo "образ приложения v2: $(docker inspect --format '{{index .Config.Image}}' studio-app-v2-1 2>/dev/null || echo '—')"
  echo "здоровье v2:        $(docker inspect --format '{{.State.Health.Status}}' studio-app-v2-1 2>/dev/null || echo '—')"
  if [ -f "$LAST_IMAGE_FILE" ]; then
    echo "откат v1 на:        $(cat "$LAST_IMAGE_FILE")"
  fi
  if [ -f ".last-image-v2" ]; then
    echo "откат v2 на:        $(cat ".last-image-v2")"
  fi
  echo
  if db_conn 2>/dev/null && PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo "база v1 (хост):     $DB_NAME на $DB_SOCK — отвечает"
  else
    echo "база v1 (хост):     НЕ ОТВЕЧАЕТ — systemctl status postgresql"
  fi
  if db_conn ./env.v2 2>/dev/null && PGPASSWORD="$DB_PASS" pg_isready -h "$DB_SOCK" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo "база v2 (хост):     $DB_NAME на $DB_SOCK — отвечает"
  fi
  echo
  df -h /var/lib/docker 2>/dev/null | tail -1 || true
}

case "${1:-deploy}" in
  deploy|"")   cmd_deploy "${2:-app}" ;;
  v2)          cmd_deploy v2 ;;
  backup)      cmd_backup "${2:-./env}" ;;
  rollback)    cmd_rollback "${2:-app}" ;;
  admin)       shift; cmd_admin "$@" ;;
  status)      cmd_status ;;
  logs)        shift; docker compose --env-file ./env logs -f --tail 200 "${@:-app}" ;;
  *)           die "неизвестная команда ${1}; есть: deploy, v2, backup, rollback, admin, status, logs" ;;
esac
