# Студия на VPS: развёртывание

Образ собирает GitHub Actions и кладёт в GitHub Container Registry; VPS тянет
его и поднимает через `docker compose`. Здесь — что сделать один раз и что
происходит дальше само.

Не путать с `deploy/gpt-relay/`: там VPS работает тонким прокси и студии на нём
нет. Здесь наоборот — на VPS живёт вся студия.

---

## Что где

```
                 push в main
                      │
              ┌───────▼────────┐
              │ GitHub Actions │
              │  ci.yml — гейт │  ruff, mypy, храповик суиты, покрытие,
              │                │  RLS на живом Postgres, сборка фронта
              └───────┬────────┘
                      │ только если зелёный
              ┌───────▼────────┐
              │ release.yml    │  docker build → ghcr.io/<owner>/<repo>
              │                │  подпись cosign, SBOM, provenance
              └───────┬────────┘
                      │ ssh, образ передаётся ДАЙДЖЕСТОМ
              ┌───────▼────────────────────────────┐
              │ VPS /opt/studio/deploy.sh          │
              │  бэкап базы → pull → up --wait     │
              └───────┬────────────────────────────┘
                      │
        ┌─────────────┼──────────────┐
     caddy          app             db
    TLS, 443    FastAPI+воркер   Postgres 16
                  :8765            RLS
```

---

## Требования к VPS

| | Минимум | Почему |
| --- | --- | --- |
| RAM | 4 ГБ | argon2 берёт 64 МиБ на проверку пароля, ffmpeg на сборке — до гигабайта |
| Диск | 40 ГБ | ролики в томе `app-data`; при S3 хватит 20 |
| CPU | 2 ядра | упор в сеть и лимиты провайдеров, а не в процессор |
| ОС | Debian 12 / Ubuntu 22.04+ | нужен `docker compose` v2 |

**GPU не нужен и не используется.** Генерация идёт у провайдеров по API;
локальный ASR (`[nvidia]`, `[whisper]`) в образ не входит.

---

## Установка, один раз

### 1. Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker
```

### 2. Каталог студии

```bash
sudo install -d -o "$USER" -g "$USER" /opt/studio
cd /opt/studio
# Из репозитория — только содержимое deploy/studio/
scp -r <ПК>:video-pipeline/deploy/studio/. .
```

### 3. Окружение

```bash
cp env.template env && chmod 600 env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # STUDIO_SESSION_SECRET
openssl rand -base64 24                                          # POSTGRES_PASSWORD
openssl rand -base64 24                                          # POSTGRES_SUPERPASSWORD
nano env
```

`deploy.sh` откажется работать, если у `env` права не `600`: в файле секрет
подписи сессий и ключи провайдеров.

Если гоняете compose руками — обязательно с тем же флагом, что и `deploy.sh`:

```bash
docker compose --env-file ./env ps
```

Без него compose не увидит значений для `${…}` (файл называется `env`, а не
`.env`, намеренно) и откажется собирать конфиг.

### 4. Промт-библиотека

**Обязательно перед первым запуском.**

```bash
rsync -a <ПК>:video-pipeline/prompts/ /opt/studio/prompts/
```

Библиотека мастер-промтов намеренно вне git (`.gitignore: prompts/*`) и в
образ не входит: образ уезжает в реестр, а это содержательная часть продукта.
Монтируется томом на чтение и импортируется в базу на первом старте
(`app/web/api.py::_lifespan`). Дальше источник — таблица `prompt_library`, и
каталог можно не обновлять: правки оператор делает через интерфейс.

Без библиотеки конвейер падает на первом шаге, причём **данными, а не кодом** —
разбираться будут не там. Проверка: `python3 scripts/check_prompts.py`.

### 5. Доступ к реестру

Если пакет приватный (по умолчанию — да):

```bash
echo "<PAT с read:packages>" | docker login ghcr.io -u <owner> --password-stdin
```

### 6. Домен

A-запись на IP VPS **до** первого запуска: Caddy получает сертификат по
HTTP-01, и без правильной записи выдача не пройдёт.

### 7. Первый запуск

```bash
./deploy.sh
./deploy.sh admin --email boss@studio.local
```

Второй командой заводится администратор. **Пароль печатается один раз** — в
базе только argon2id-хеш, восстановить его нельзя, только сменить.

У админа нет баланса: у него нет кассы вовсе — шаги не тарифицируются
(`docs/SAAS-PIVOT.md` §5.8).

---

## Секреты и переменные в GitHub

Settings → Secrets and variables → Actions.

| Имя | Тип | Что |
| --- | --- | --- |
| `VPS_HOST` | secret | адрес VPS |
| `VPS_USER` | secret | пользователь деплоя |
| `VPS_SSH_KEY` | secret | приватный ключ, **отдельный только под деплой** |
| `VPS_KNOWN_HOSTS` | secret | вывод `ssh-keyscan <host>` |
| `VPS_PATH` | variable | каталог, по умолчанию `/opt/studio` |
| `STUDIO_URL` | variable | `https://studio.example.com` — по нему проверяется выкладка |

Ключ деплоя стоит ограничить в `~/.ssh/authorized_keys` на VPS:

```
command="/opt/studio/deploy.sh",no-agent-forwarding,no-port-forwarding,no-pty ssh-ed25519 AAAA...
```

Тогда украденный ключ даёт право выкатить образ, а не shell на сервере.

**Пауза перед продом.** Settings → Environments → `production` → Required
reviewers. Это единственное место, где человек может остановить выкладку;
кодом оно намеренно не задаётся — право на паузу не должен править тот же, кто
правит код.

---

## Работа

```bash
./deploy.sh status          # что запущено, здоровье, место на диске
./deploy.sh logs            # хвост журнала приложения
./deploy.sh logs db         # журнал базы
./deploy.sh backup          # дамп базы в ./backups
./deploy.sh rollback        # предыдущий образ
./deploy.sh admin --reset-password --email boss@studio.local
```

### Бэкап

`deploy.sh` снимает дамп **перед каждой выкладкой** и держит последние 14.
Порядок именно такой, потому что миграции применяются автоматически на старте,
а `downgrade` в этом проекте намеренно отказывает: откат образа схему назад не
вернёт. Единственный путь из плохой миграции — дамп, снятый до неё.

Дампы лежат на том же диске. Для настоящей сохранности — увозить их:

```bash
0 4 * * * cd /opt/studio && ./deploy.sh backup && \
  rclone copy backups remote:studio-backups --max-age 25h
```

Восстановление:

```bash
docker compose stop app
gunzip -c backups/db-20260825-040000.sql.gz | \
  docker compose exec -T db psql -U postgres -d videopipeline
docker compose start app
```

---

## Чего на VPS не будет

**Браузерная автоматизация.** CDP-путь (`app/bots/browser.py`) требует живого
Chrome с профилем и логинами. На сервере такого профиля нет, и Playwright с
браузером добавили бы ~700 МБ к образу ради кода, который там не запустится.

Следствия:

* `OUTSEE_HTTP_FALLBACK_CDP=false` **обязательно** — иначе шаг будет ждать
  браузер, которого нет, до таймаута;
* Outsee работает только по Developer API (`OUTSEE_API_KEY`);
* ElevenLabs — только по API (`ELEVENLABS_API_KEY`).

**Локальный ASR.** `[nvidia]` и `[whisper]` в образ не входят: это CUDA и
модель на 100+ МБ.

**Публикация кадра для Outsee.** Требует Yandex Object Storage
(`YANDEX_STORAGE_*`). Без него `ensure_public_image_url` падает `OutseeApiError`
— это не баг. Провайдеры `minimax` и `grsai` берут кадр base64 и файлохостинга
не требуют.

---

## Если не поднялось

```bash
./deploy.sh logs
```

| Что в журнале | Что делать |
| --- | --- |
| `STUDIO_SESSION_SECRET короче 32 байт` | сгенерировать заново: `token_urlsafe(48)` |
| `row-level security в этом движке не существует` | `DATABASE_URL` смотрит в SQLite — проверить `env` |
| `роль ... обходит политики` (`rls_check`) | приложение ходит под суперпользователем; должна быть роль `app` |
| `WEB_HOST=... без учётных записей` | не задан `STUDIO_SESSION_SECRET` |
| Caddy в цикле переполучает сертификат | A-запись не указывает на этот VPS, либо 80 порт занят |
| `промт-библиотека с диска {'seen': 0}` | не скопирован `prompts/` (шаг 4) |

Healthcheck смотрит `/api/health` — он не трогает базу и отвечает, пока
Postgres поднимается. Это осознанно: перезапускать приложение из-за недоступной
базы значит уйти в цикл перезапусков вместо ожидания.
