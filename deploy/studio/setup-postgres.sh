#!/usr/bin/env bash
# Postgres на самом VPS, не в контейнере. Один раз, от root.
#
#     sudo ./setup-postgres.sh
#
# Скрипт идемпотентен: повторный запуск ничего не ломает и не меняет пароль
# уже заведённой роли.
#
# **Что и почему делается именно так**
#
# *Соединение — unix-сокет, а не TCP.* Приложение живёт в контейнере, база на
# хосте, и связать их можно двумя способами. TCP через `host.docker.internal`
# требует поднять `listen_addresses`, открыть порт на интерфейсе моста и
# разрешить подсеть в `pg_hba.conf` — то есть завести слушающий сокет там, где
# его раньше не было, и дальше следить, чтобы фаервол не отстал от docker.
# Смонтированный сокет не требует ничего из этого: TCP у базы остаётся
# выключенным, снаружи её не видно в принципе.
#
# *Аутентификация по паролю, а не peer.* `peer` сопоставляет системного
# пользователя с ролью — но в контейнере другой uid (10001), и peer он не
# пройдёт никогда. Поэтому для роли приложения добавляется строка
# `scram-sha-256`, и только для её базы.
#
# *Роль обычная, без SUPERUSER и BYPASSRLS.* Изоляция арендаторов держится на
# row-level security, а суперпользователя политики не касаются по определению.
# Работа под суперпользователем означала бы отсутствие изоляции без единой
# ошибки в журнале — приложение проверяет это на старте и отказывается
# подниматься (`app/services/rls_check.py`).
#
# *Схема отдаётся во владение роли.* `FORCE ROW LEVEL SECURITY` достаёт как раз
# владельца таблиц; таблицы создают миграции, которые запускает приложение.
set -euo pipefail

PG_VERSION="${PG_VERSION:-16}"
DB_NAME="${DB_NAME:-videopipeline}"
DB_USER="${DB_USER:-app}"
DB_PASSWORD="${DB_PASSWORD:-}"
SOCKET_DIR="${SOCKET_DIR:-/var/run/postgresql}"

die() { echo "setup-postgres: $*" >&2; exit 1; }
say() { echo "setup-postgres: $*"; }

[ "$(id -u)" -eq 0 ] || die "нужен root: sudo ./setup-postgres.sh"

if [ -z "$DB_PASSWORD" ]; then
  # Генерируем сами: пароль, который админ придумает на ходу, окажется в
  # истории shell и в переписке. Печатается один раз, дальше живёт в ./env.
  DB_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
  GENERATED=1
else
  GENERATED=0
fi

# ── 1. Установка ────────────────────────────────────────────────────────────
if ! command -v psql >/dev/null 2>&1; then
  say "ставлю PostgreSQL $PG_VERSION"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "postgresql-$PG_VERSION" postgresql-client-common openssl
else
  say "PostgreSQL уже стоит: $(psql --version)"
fi

systemctl enable --now postgresql

# ── 2. База и роль ──────────────────────────────────────────────────────────
psql_su() { su - postgres -c "psql -v ON_ERROR_STOP=1 -tAc \"$1\""; }
# Отдельно для запросов ВНУТРИ базы студии: `psql_su` ходит в базу `postgres`,
# и проверка владельца схемы в ней смотрела бы не туда.
psql_db() { su - postgres -c "psql -v ON_ERROR_STOP=1 -d '$DB_NAME' -tAc \"$1\""; }

if [ "$(psql_su "select 1 from pg_roles where rolname='$DB_USER'")" = "1" ]; then
  say "роль $DB_USER уже есть — пароль не трогаю"
  GENERATED=0
else
  say "завожу роль $DB_USER"
  # NOSUPERUSER / NOBYPASSRLS явно, а не «по умолчанию»: умолчание может
  # смениться, а тихо появившийся BYPASSRLS означает утечку без ошибки.
  su - postgres -c "psql -v ON_ERROR_STOP=1" <<SQL
    create role "$DB_USER" login password '$DB_PASSWORD'
      nosuperuser nocreatedb nocreaterole noinherit noreplication nobypassrls;
SQL
fi

if [ "$(psql_su "select 1 from pg_database where datname='$DB_NAME'")" = "1" ]; then
  say "база $DB_NAME уже есть"
else
  say "создаю базу $DB_NAME"
  su - postgres -c "createdb --owner='$DB_USER' --encoding=UTF8 --lc-collate=C.UTF-8 --lc-ctype=C.UTF-8 --template=template0 '$DB_NAME'"
fi

# Владение схемой — обязательно: FORCE ROW LEVEL SECURITY действует на
# владельца таблиц, а таблицы создаст тот, кто гонит миграции.
su - postgres -c "psql -v ON_ERROR_STOP=1 -d '$DB_NAME'" <<SQL
  grant all on schema public to "$DB_USER";
  alter schema public owner to "$DB_USER";
SQL

# ── 3. Доступ по сокету ─────────────────────────────────────────────────────
HBA="$(su - postgres -c "psql -tAc 'show hba_file'")"
MARK="# video-pipeline: доступ из контейнера по сокету"

if grep -qF "$MARK" "$HBA"; then
  say "правило в $HBA уже есть"
else
  say "добавляю правило в $HBA"
  cp "$HBA" "$HBA.bak-$(date +%F)"
  # В НАЧАЛО файла: pg_hba читается сверху вниз, первое совпадение выигрывает,
  # и общая строка `local all all peer` выше нашей перехватила бы соединение.
  tmp="$(mktemp)"
  {
    echo "$MARK"
    echo "# Пароль, а не peer: uid в контейнере (10001) с ролью не совпадает."
    printf 'local   %-16s %-16s scram-sha-256\n' "$DB_NAME" "$DB_USER"
    echo
    cat "$HBA"
  } > "$tmp"
  install -o postgres -g postgres -m 640 "$tmp" "$HBA"
  rm -f "$tmp"
  systemctl reload postgresql
fi

# ── 4. Право снимать дамп ───────────────────────────────────────────────────
# `pg_dump` под ролью приложения НЕ РАБОТАЕТ, и это не настройка, а следствие
# устройства изоляции: роль владеет таблицами, а ревизия 0006 ставит на них
# `FORCE ROW LEVEL SECURITY`, которая действует и на владельца. Дамп падает на
# первой же таблице:
#
#   ERROR: query would be affected by row-level security policy for table "artifacts"
#
# Обойти можно тремя способами. Снять FORCE — потерять изоляцию. Выдать роли
# приложения BYPASSRLS — то же самое, только незаметнее. Третий, выбранный
# здесь: привилегия живёт в sudoers, а не паролем в ./env. Утёкший ./env тогда
# даёт роль, ограниченную арендатором, а не полный слепок базы.
DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
SUDOERS=/etc/sudoers.d/studio-backup

if [ -z "$DEPLOY_USER" ] || [ "$DEPLOY_USER" = "root" ]; then
  say "DEPLOY_USER не определён — правило для бэкапа не завожу"
  say "  задайте явно: sudo DEPLOY_USER=deploy ./setup-postgres.sh"
elif [ -f "$SUDOERS" ] && grep -q "^$DEPLOY_USER " "$SUDOERS"; then
  say "правило sudoers для $DEPLOY_USER уже есть"
else
  say "разрешаю $DEPLOY_USER снимать дамп от имени postgres"
  PGDUMP_BIN="$(command -v pg_dump || echo /usr/bin/pg_dump)"
  tmp="$(mktemp)"
  printf '%s ALL=(postgres) NOPASSWD: %s\n' "$DEPLOY_USER" "$PGDUMP_BIN" > "$tmp"
  # visudo -c на временном файле: битый файл в sudoers.d ломает sudo целиком,
  # включая тот sudo, которым это чинят.
  if visudo -c -f "$tmp" >/dev/null; then
    install -o root -g root -m 440 "$tmp" "$SUDOERS"
  else
    rm -f "$tmp"
    die "сгенерированное правило sudoers не прошло visudo -c"
  fi
  rm -f "$tmp"
fi

# ── 5. Проверки ─────────────────────────────────────────────────────────────
# Не «скрипт отработал», а «получилось то, ради чего он есть». Каждая проверка
# закрывает молчаливый отказ: неправильная роль не даёт ошибки — она даёт
# отсутствие изоляции.
problems=0

is_super="$(psql_su "select rolsuper from pg_roles where rolname='$DB_USER'")"
[ "$is_super" = "f" ] || { echo "  ✗ роль $DB_USER — суперпользователь, RLS её не касается" >&2; problems=1; }

is_bypass="$(psql_su "select rolbypassrls from pg_roles where rolname='$DB_USER'")"
[ "$is_bypass" = "f" ] || { echo "  ✗ у роли $DB_USER есть BYPASSRLS — политики игнорируются" >&2; problems=1; }

# Владелец схемы. Без этого `FORCE ROW LEVEL SECURITY` не подействует на того,
# кто создал таблицы, — то есть на само приложение.
owner="$(psql_db "select nspowner::regrole::text from pg_namespace where nspname='public'")"
[ "$owner" = "$DB_USER" ] || { echo "  ✗ схема public принадлежит '$owner', а должна '$DB_USER'" >&2; problems=1; }

[ -S "$SOCKET_DIR/.s.PGSQL.5432" ] || { echo "  ✗ нет сокета $SOCKET_DIR/.s.PGSQL.5432" >&2; problems=1; }

# Приложение в контейнере ходит под uid 10001, никак не связанным с postgres.
# Чтобы дотянуться до сокета, ему нужно ПРОЙТИ каталог (бит x для «остальных»)
# и открыть сам сокет. На Debian каталог 1775, сокет 0777 — годится; проверяем,
# а не полагаемся: нестандартный umask даёт отказ вида
# `PermissionError: [Errno 13]` из глубины asyncpg, без слова «права».
dir_mode=$(stat -c '%a' "$SOCKET_DIR")
case "$dir_mode" in
  *[1357]) : ;;  # последняя цифра нечётная → бит x у «остальных» есть
  *) echo "  ✗ каталог $SOCKET_DIR имеет права $dir_mode — контейнер (uid 10001) его не пройдёт" >&2
     echo "    почините: chmod o+x $SOCKET_DIR" >&2
     problems=1 ;;
esac

sock_mode=$(stat -c '%a' "$SOCKET_DIR/.s.PGSQL.5432" 2>/dev/null || echo 000)
case "$sock_mode" in
  *[2367]) : ;;  # у «остальных» есть запись → connect() пройдёт
  *) echo "  ✗ сокет имеет права $sock_mode — подключиться из контейнера нельзя" >&2
     echo "    проверьте unix_socket_permissions в postgresql.conf (должно быть 0777)" >&2
     problems=1 ;;
esac

if [ "$GENERATED" = "1" ]; then
  if ! PGPASSWORD="$DB_PASSWORD" psql -h "$SOCKET_DIR" -U "$DB_USER" -d "$DB_NAME" -tAc "select 1" >/dev/null 2>&1; then
    echo "  ✗ подключиться по сокету паролем не вышло — проверьте $HBA" >&2
    problems=1
  fi
fi

[ "$problems" = "0" ] || die "проверки не прошли, см. выше"

say "готово"
echo
echo "  DATABASE_URL для ./env:"
echo
if [ "$GENERATED" = "1" ]; then
  echo "    DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASSWORD@/$DB_NAME?host=/var/run/postgresql"
  echo "    POSTGRES_SOCKET_DIR=$SOCKET_DIR"
  echo
  echo "  Пароль показан ОДИН раз."
  echo "  Спецсимволы в пароле кодируются: @ → %40, # → %23, % → %25."
else
  echo "    DATABASE_URL=postgresql+asyncpg://$DB_USER:<пароль>@/$DB_NAME?host=/var/run/postgresql"
  echo "    POSTGRES_SOCKET_DIR=$SOCKET_DIR"
  echo
  echo "  Роль уже существовала — пароль возьмите тот, что заводили ранее."
  echo "  Сменить: sudo -u postgres psql -c \"alter role $DB_USER password '…'\""
fi
echo
echo "  В ?host= стоит путь ВНУТРИ КОНТЕЙНЕРА — он всегда /var/run/postgresql."
echo "  POSTGRES_SOCKET_DIR — путь на хосте, откуда сокет монтируется."
echo
echo "  TCP у базы остаётся выключенным: контейнер ходит через смонтированный"
echo "  сокет, снаружи Postgres не виден."
