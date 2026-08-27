# syntax=docker/dockerfile:1.7
#
# Образ студии: FastAPI + воркер + собранный статикой фронт в одном процессе.
#
# **Почему один процесс, а не два контейнера.** `python -m app.main` поднимает
# uvicorn, воркер конвейера и фоновую синхронизацию как задачи одного event
# loop; они делят ContextVar арендатора, кэш промт-библиотеки и пул к базе.
# Разнести их по контейнерам — не «правильнее», а переписать половину
# оркестратора: воркер берёт работу из той же сессии, что и веб.
#
# **Чего в образе НЕТ намеренно:**
#
# * *Playwright и Chrome.* CDP-путь (`app/bots/browser.py`, Outsee-fallback,
#   ElevenLabs) требует живого Chrome с профилем и логинами пользователя. На
#   VPS такого профиля нет и быть не может, а браузер добавил бы ~700 МБ к
#   образу ради кода, который там всё равно не запустится. На сервере работают
#   только API-провайдеры: `IMAGE_PROVIDER`/`VIDEO_PROVIDER` = `grsai`,
#   `minimax` или `outsee` с `OUTSEE_HTTP_FALLBACK_CDP=false`.
# * *`prompts/`.* Библиотека мастер-промтов намеренно вне git
#   (`.gitignore: prompts/*`), в git лежит только часть. Печь её в публичный
#   образ значит выложить содержательную часть продукта в реестр. Она
#   монтируется томом и на первом старте импортируется в базу
#   (`app/web/api.py::_lifespan`), дальше источник — база.
# * *`[nvidia]`.* NeMo Parakeet — ASR на CUDA для монтажного ПК. На сервере
#   CUDA нет.
# * *`scripts/`, `evals/`, `tests/`, `docs/`, `.github/`.* Обвязка
#   разработчика и владельца: QA-скрипты, Windows-лаунчеры, голденсеты,
#   храповики. Приложение из них ничего не импортирует; единственный вызов
#   (`app/main.py` → `scripts/return_prompts_from_stash.py`) восстанавливает
#   промты из git stash, а в контейнере нет ни git, ни репозитория — он и
#   так пропускался. Список исключений — `.dockerignore`; там же почему
#   шаблоны обязаны начинаться с `**/`.
#
# **Что есть, хотя сначала не было:** `[whisper]` (faster-whisper на CPU).
# Первая редакция исключала его как «ASR нужен только монтажу». Живой прогон
# финала показал обратное: озвучка (ElevenLabs API) записалась, а следующий
# же шаг — выравнивание голоса по кадрам — требует пословных таймкодов, и без
# ASR ролик не собирается вовсе. Модель `small`/int8 на CPU обрабатывает
# минуту речи за десятки секунд; веса тянутся при первом вызове в
# `data/.cache/huggingface` — том, переживает пересборку. Настройки —
# `ASR_BACKEND=whisper`, `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`
# (deploy/studio/env.template).
#
# **Что есть:** ffmpeg (сборка и монтаж ролика — `app/services/assembly.py`),
# драйверы Postgres и клиент S3.

# ── Фронт: статический экспорт ──────────────────────────────────────────────
# Next собран с `output: "export"` (web/next.config.ts) — на выходе обычная
# статика, которую отдаёт FastAPI. Отдельного node-процесса в проде нет.
FROM node:22-bookworm-slim AS web

WORKDIR /web
# Сперва манифесты — слой с `npm ci` переиспользуется, пока зависимости те же.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
# STUDIO_VERSION читает next.config.ts, чтобы показать версию сборки в углу.
RUN npm run build && test -d out || (echo "next build не создал out/ — проверьте output: export" && exit 1)


# ── Зависимости Python отдельным слоем ──────────────────────────────────────
# pyproject меняется редко, код — постоянно. Разделение экономит на каждой
# сборке минуты установки колёс.
FROM python:3.12-slim-bookworm AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
# Пустой пакет: `pip install .` требует, чтобы модуль существовал, а копировать
# ради этого весь код значит терять кэш слоя при каждой правке.
RUN mkdir -p app && touch app/__init__.py

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip && pip install ".[postgres,s3,whisper]"


# ── Рантайм ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# `find_project_root()` ищет pyproject.toml вверх от модуля: приложение обязано
# жить деревом исходников, а не в site-packages. От этого же зависят пути
# `data/`, `logs/`, `prompts/` — они считаются от корня, а не от CWD.
WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Внутри контейнера слушаем все интерфейсы: наружу порт публикует compose,
    # а не приложение. Проверка «0.0.0.0 без учётных записей» при этом
    # остаётся в силе — она смотрит на STUDIO_SESSION_SECRET.
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8765 \
    TELEGRAM_ENABLED=false

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

# Непривилегированный пользователь. Данные и журналы пишутся в тома, поэтому
# каталоги создаются заранее и с правильным владельцем: смонтированный том
# наследует права точки монтирования, и root-only каталог сделал бы запись
# невозможной без внятной ошибки.
RUN useradd --create-home --uid 10001 studio \
    && mkdir -p /app/data /app/logs /app/prompts \
    && chown -R studio:studio /app

COPY --chown=studio:studio pyproject.toml alembic.ini ./
COPY --chown=studio:studio app/ ./app/
COPY --chown=studio:studio migrations/ ./migrations/
# `scripts/` и `evals/` в образ НЕ кладутся (см. шапку). README.md тоже:
# pyproject его не объявляет (`readme` не задан), pip без него собирает.
# Промты в образ НЕ кладутся. Раньше здесь стоял `COPY prompts/`, и это была
# ошибка: команда копирует то, что есть в git, — четыре каталога
# (scene_design, 05_image_prompts, 04_hero, 05_excel_gpt). То есть заметная
# часть библиотеки уезжала в реестр вместе с образом, хотя весь смысл
# `.gitignore: prompts/*` в том, чтобы она туда не уезжала.
#
# На работу это не влияет: полная библиотека монтируется томом и на первом
# старте импортируется в базу (`app/web/api.py::_lifespan`). Каталог создаётся
# ниже пустым — точкой монтирования.
COPY --chown=studio:studio --from=web /web/out ./web/out
COPY --chown=studio:studio web/STUDIO_VERSION ./web/STUDIO_VERSION

USER studio

EXPOSE 8765

# `/api/health` не трогает базу — он отвечает и когда Postgres ещё
# поднимается. Для «жив ли конвейер» этого мало, но для рестарта контейнера
# ровно то, что нужно: перезапускать приложение из-за недоступной базы значит
# уйти в цикл перезапусков вместо ожидания.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${WEB_PORT}/api/health" || exit 1

# tini как PID 1: uvicorn и воркер живут задачами одного процесса, и без
# init-обёртки SIGTERM от `docker compose down` доходит не до всех — контейнер
# убивается по таймауту, а шаг конвейера обрывается на середине.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app.main"]
