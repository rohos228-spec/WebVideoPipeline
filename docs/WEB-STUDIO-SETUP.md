# Руководство по установке и запуску Web Studio (Video Pipeline)

Данный документ описывает процесс первоначальной установки, конфигурации и запуска **Video Pipeline Web Studio** — современной среды генерации и оркестрации видео на стеке FastAPI и Next.js.

---

## 1. Архитектура Web Studio

После полного удаления устаревшей подсистемы Telegram (PR #5) проект работает в чистой Web-ориентированной архитектуре:

- **Бэкенд (FastAPI / Python 3.12):** 
  - Оркестратор DAG-графа шагов (`app/orchestrator/`).
  - Реестр шагов и зависимостей (`app.orchestrator.pipeline_steps`).
  - Real-time шина событий на базе Server-Sent Events (SSE) в `app/web/routers/events.py`.
  - Надежное хранилище состояния: SQLite в режиме WAL (`data/state.db`) с ретраями транзакций (`busy_timeout=60000`).
  - Раздача статики собранного фронтенда прямо из каталога `web/out/`.
- **Фронтенд (Next.js / React / Tailwind):**
  - Интерактивный канвас пайплайна (`flow-canvas.tsx`).
  - Инспектор нод и настроек проекта.
  - Фабрика массового производства роликов («Фабрика видео»).
  - Доска монтажа, просмотр ассетов и редактор промптов.
- **Интеграция с ИИ-сервисами:**
  - Текстовые модели (OpenAI, Anthropic, DeepSeek, Kie, TokenRouter).
  - Генерация изображений и видео (Outsee, Grsai, Kling).
  - Озвучка и монтаж (ElevenLabs, Whisper, FFmpeg).
  - Chrome CDP (:29229) для браузерной автоматизации.

---

## 2. Системные требования

| Компонент | Минимальные требования | Рекомендуется |
|-----------|------------------------|---------------|
| **ОС** | Windows 10/11 64-bit или Linux (Ubuntu 22.04+) | Windows 11 64-bit |
| **Python** | 3.12 64-bit | 3.12.7+ |
| **Node.js** | v20.x LTS | v20.x или v22.x LTS (с npm/pnpm) |
| **Браузер** | Google Chrome | Google Chrome (с поддержкой удаленного порта отладки CDP) |
| **Медиа** | FFmpeg 6.0+ | FFmpeg в системном `PATH` |
| **Память** | 16 ГБ RAM | 32 ГБ RAM + быстрый NVMe SSD |

---

## 3. Пошаговая установка

### Шаг 1. Клонирование репозитория

```powershell
git clone <URL_РЕПОЗИТОРИЯ> video-pipeline
cd video-pipeline
```

### Шаг 2. Настройка виртуального окружения Python

Создайте изолированное окружение на базе Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Обновите `pip` и установите зависимости проекта:

```powershell
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
pip install argon2-cffi pyjwt anthropic
```

### Шаг 3. Сборка фронтенда (Web Studio UI)

Фронтенд собирается в статическую сборку `web/out/`, которую FastAPI раздает напрямую:

```powershell
cd web
npm install
npm run build
cd ..
```

> [!NOTE]
> Каталог `web/out/` добавлен в `.gitignore` и собирается локально. В git-репозитории он не хранится.

### Шаг 4. Настройка кодировки на Windows

В конфигурационном файле миграций `alembic.ini` используется 100% чистый ASCII (без кириллических комментариев). Это предотвращает ошибку `UnicodeDecodeError (charmap / cp1251)` при запуске Alembic в стандартной консоли Windows.

Применение миграций базы данных:

```powershell
alembic upgrade head
```

---

## 4. Конфигурация (`.env`)

Создайте файл `.env` в корне репозитория (на основе `deploy/studio/env.template`):

```powershell
Copy-Item deploy\studio\env.template .env
```

Отредактируйте параметры в файле `.env`:

```ini
# Базовые параметры сервера
WEB_ENABLED=true
WEB_HOST=127.0.0.1
WEB_PORT=8765

# Путь к локальной базе данных
SQLITE_PATH=./data/state.db

# Chrome CDP порт для автоматизации Outsee / ChatGPT
BROWSER_CDP_URL=http://127.0.0.1:29229

# Ключи текстовых LLM
TEXT_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Генерация медиа
OUTSEE_API_KEY=...
GRSAI_API_KEY=...

# Озвучка (ElevenLabs)
ELEVENLABS_API_KEY=...

# ── Telegram (Legacy / Deprecated) ──────────────────────────────────────────
# Подсистема Telegram удалена. Переменные сохранены для обратной совместимости:
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
```

---

## 5. Запуск Studio

### Вариант 1. Через командный лаунчер (Рекомендуется на Windows)

В корне репозитория дважды кликните по файлу **`STUDIO.cmd`** или выполните в консоли:

```powershell
.\STUDIO.cmd 1
```

**Пункты меню `STUDIO.cmd`:**
- **`[1] Запустить студию`** — автоматически запускает Chrome с CDP портом 29229, поднимает бэкенд FastAPI на порту 8765 и открывает интерфейс в браузере.
- **`[2] Остановить всё`** — останавливает процесс бэкенда.
- **`[3] Браузер с ИИ`** — запускает Chrome CDP с выделенным профилем пользователя для ручной авторизации в сервисах (ChatGPT, Outsee).
- **`[4] Обновить и запустить`** — синхронизация с репозиторием и перезапуск.
- **`[5] Починить установку`** — переустановка зависимостей и пересборка `web/out/`.
- **`[6] Диагностика`** — проверка статуса окружения, версий и портов.

### Вариант 2. Прямой запуск через PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.main
```

Веб-студия будет доступна по адресу:
👉 **`http://127.0.0.1:8765/pipeline`** (или `http://127.0.0.1:8765`).

---

## 6. Структура проекта

```
video-pipeline/
├── app/
│   ├── main.py                     # Точка входа приложения FastAPI + воркер
│   ├── settings.py                 # Настройки конфигурации Pydantic
│   ├── orchestrator/               # Движок исполнения шагов пайплайна
│   │   ├── pipeline_steps.py       # Реестр шагов и их зависимостей
│   │   └── steps/                  # Исполняемые шаги генерации
│   ├── web/                        # API бэкенд Studio
│   │   ├── app.py                  # Инициализация FastAPI и монтирование static/SPA
│   │   └── routers/                # Эндпоинты (projects, node_runs, events, batches)
│   └── services/                   # Бизнес-логика, хранилище, интеграции
├── web/
│   ├── src/                        # Исходный код интерфейса Studio (Next.js / TS)
│   └── out/                        # Скомпилированная статика UI (Next export)
├── data/                           # Рабочие данные, state.db, логи, ассеты
├── deploy/studio/env.template      # Шаблон переменных окружения
├── scripts/                        # Скрипты обслуживания, проверок и сборки
└── STUDIO.cmd                      # Главный Windows-лаунчер
```

---

## 7. Проверка качества и гейты (Quality Gates)

Перед отправкой изменений в репозиторий обязательно выполнение всех локальных проверок:

```powershell
# 1. Быстрый линтинг кода
.venv\Scripts\python.exe -m ruff check .

# 2. Статическая проверка типов
.venv\Scripts\python.exe -m mypy app/ --ignore-missing-imports --no-warn-unused-ignores

# 3. Валидация инженерной политики и долга
.venv\Scripts\python.exe scripts/policy_check.py

# 4. Прогон модульных и интеграционных тестов
.venv\Scripts\python.exe -m pytest tests/
```
