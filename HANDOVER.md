# Handover — video-pipeline

> **Актуальная ветка:** `hardening` (от `hygiene-rebuild`, `56897a4`).
> **Аудитория:** новый разработчик / ИИ-агент, который впервые открывает
> репозиторий и хочет понять «что это, как запустить, куда смотреть».
> **Нормативные ссылки:** [`AGENTS.md`](AGENTS.md) (cloud/dev контракт),
> [`ai-pack/START_HERE.md`](ai-pack/START_HERE.md) (пакет знаний для ИИ),
> [`docs/TECH_DEBT_PLAN.md`](docs/TECH_DEBT_PLAN.md) (план-граф работ).

---

## 1. Что это

**video-pipeline** — единое Python 3.11+ приложение (без Docker),
автоматизирующее производство коротких вертикальных роликов (60–75 сек, 9:16):
план → сценарий → кадры → картинки → видеопромты → видео → монтаж. HITL
через Telegram-бот (опционально) и/или Studio Web UI на `:8765`.

Точка входа: `python -m app.main` из корня репозитория. На Windows —
`STUDIO.cmd` (лаунчер; меню — `scripts/studio.ps1`).

> ⚠️ `STUDIO.cmd` при каждом запуске тянет `scripts/studio.ps1` с
> `raw.githubusercontent.com/.../main` и делает `git fetch origin main` +
> `git reset --hard origin/main` — **локальные незакоммиченные правки
> теряются**. Для работы в своей ветке запускайте `scripts/studio.ps1`
> напрямую (или `python -m app.main`).

---

## 2. Quick start

```bash
# Клонировать и перейти в свою ветку ПК
git clone <repo-url> video-pipeline
cd video-pipeline
git checkout <ORCHESTRATOR_GIT_BRANCH>   # housepc / tompc / strangepc / workpc / main
git pull --ff-only

# Зависимости
python3 -m pip install -e ".[dev]"      # Linux
# или pip install -e ".[dev]"          # Windows (если .venv не .venv)

# Опционально: NVIDIA ASR (ПК монтажа)
pip install -e ".[nvidia]"
ASR_BACKEND=nvidia python3 scripts/download_nvidia_asr.py

# Запуск
python3 -m app.main                       # Linux / PowerShell
# или STUDIO.cmd → [1]                   # Windows

# Smoke-tests
python3 -m pytest tests/test_auto_review_stub.py -q
python3 -m pytest tests/ -q              # полный прогон (~5.5 мин)
```

> **Linux:** `python` нет в PATH — только `python3`. На машине стоит
> Python 3.12 (удовлетворяет `>=3.11,<3.13`). Dev-утилиты (`ruff`, `mypy`,
> `pytest`, `playwright`) ставятся в `~/.local/bin` — добавьте в `PATH`.

> **Telegram опционален:** если `TELEGRAM_ENABLED=false` и токен пустой —
> остаётся worker + FastAPI + Web UI на `:8765`, HITL через Studio.

---

## 2.5. Промт-библиотека — её нет в git

**Свежий клон конвейер не запустит.** Мастер-промты намеренно не
версионируются: `.gitignore` содержит `prompts/*`, в репозитории лежат
только два исключения — `prompts/scene_design/` (агенты дизайна сцен) и
`prompts/05_excel_gpt/sd_*.md` (веер scene-агентов). Всё остальное —
`01_plan`, `02_script`, `03_razbivka`, `04_hero`, `05_image_prompts`,
`07_animation`, `prompts/steps|blocks|styles` (blocks v2) и
`prompts/check_operator/` — приезжает вне git.

Проверить состояние:

```bash
python3 scripts/check_prompts.py          # человекочитаемо, exit 1 если неполно
python3 scripts/check_prompts.py --json   # для CI / агентов
```

Последствия отсутствия:

| Чего нет | Что происходит |
| --- | --- |
| `<шаг>/default.md` | шаг падает `FileNotFoundError: prompt file not found` (`prompt_library.read_prompt`) — громко, не тихо |
| `prompts/steps/` | `compose_step` недоступен, blocks v2 выключен; красными идут `test_prompt_composer`, `test_prompt_step_presets`, `test_update_step_preset` |
| `prompts/check_operator/<шаг>/default.md` | проверка ноды «не настроена» → `auto_review` отдаёт `skipped` (fail-closed, stage-0 п.2), ноды проверок не попадают в каталог групп |

**Откуда брать.** Источник — рабочая машина владельца; на Windows промты
восстанавливаются `RECOVER-PROMPTS.cmd` →
`scripts/return_prompts_from_stash.py` (aside-бэкап
`%LOCALAPPDATA%\video-pipeline\prompts_aside_*` + `git stash`). Осмысленно
это работает только там, где промты уже были: восстановление из stash не
создаёт библиотеку с нуля. На новую машину папку `prompts/` переносят
руками.

> ⚠️ Практическое следствие: **«тесты зелёные» не воспроизводится ни у
> кого, кроме держателя промтов.** Прежде чем чинить красноту — прогоните
> `check_prompts.py` и сравните с базовой веткой; на 2026-08-22 из 59
> собранных падений 57 воспроизводились и на `main`.

---

## 3. Git: per-PC ветки

Локальная ветка определяется **`ORCHESTRATOR_GIT_BRANCH`** из `.env` —
не угадывайте. Возможные значения: `housepc`, `tompc`, `strangepc`,
`workpc`, плюс `main`.

После любого коммита — обязательно `git push origin <ORCHESTRATOR_GIT_BRANCH>`
и сообщить пользователю `git HEAD: <sha> уже в <ветка>`. В другую ветку
не пушить без явного запроса.

Текущая ветка — **`hardening`**, см. раздел 7.

---

## 4. Провайдеры LLM и медиа

Текст (plan / script / checks / anim_pr / hero-prompt / music-prompt):
только API, **никакого ChatGPT через браузер** для текста. Дефолт —
**kie** (`GPT_API_KEY`, `GPT_BASE_URL=https://api.kie.ai`, модель
`gpt-5-6-sol`; `app/settings.py` — `text_llm_provider="kie"`).
Альтернативы — **vibecode** (`VIBECODE_API_KEY`, модель `gpt-5.6-sol`,
[`app/services/vibecode_catalog.py`](app/services/vibecode_catalog.py)),
**tokenrouter** (`TOKENROUTER_API_KEY`) и **grsai** (`GRSAI_API_KEY`).
Переключение текста — `TEXT_LLM_PROVIDER` в `.env` (или Studio UI /
`data/text_llm_choice.json`), медиа — `IMAGE_PROVIDER` / `VIDEO_PROVIDER`.
Каталог и приоритеты — [`app/services/text_llm_catalog.py`](app/services/text_llm_catalog.py).

Картинки и видео:

- **outsee** (Developer API, `OUTSEE_API_KEY`, `IMAGE_PROVIDER=outsee`,
  см. [`app/bots/outsee_http.py`](app/bots/outsee_http.py));
- **grsai** (`GRSAI_API_KEY`, см. [`app/bots/grsai.py`](app/bots/grsai.py));
- CDP fallback для outsee — `OUTSEE_HTTP_FALLBACK_CDP=true`.

Озвучка (TTS): **через ElevenLabs HTTP API** (`app/bots/elevenlabs_api.py`,
`ELEVENLABS_API_KEY`) — браузер не нужен, конвейер работает headless.
Legacy-путь через Chrome CDP (`app/bots/elevenlabs.py`) остаётся за флагом
`ELEVENLABS_USE_CDP=true` и включается автоматически, если ключа нет.

**MiniMax** (`MINIMAX_API_KEY`) закрывает текст, картинки и видео одним
ключом: `TEXT_LLM_PROVIDER=minimax` / `IMAGE_PROVIDER=minimax` /
`VIDEO_PROVIDER=minimax` (`app/bots/minimax.py`). Кадры и рефы уходят
base64 — публиковать их наружу не нужно.

Браузер: одно Chrome-окно с `--remote-debugging-port=29229`,
подключение по CDP. Не открывать второй Chrome.

---

## 5. Архитектура одной картинкой

```
                ┌─────────────── Telegram (опционально, через SOCKS5) ──────────────┐
                │                                                                 │
                ▼                                                                 │
   ┌─────────────────────┐         ┌──────────────────────┐                        │
   │   aiogram bot       │◄────────┤  OWNER ✅/🔁/✏️/❌   │                        │
   └─────────┬───────────┘         └──────────────────────┘                        │
             │                              ▲                                       │
             ▼                              │                                       │
   ┌─────────────────────┐  step_advance   ┌──────────────────────────────┐         │
   │ pipeline_worker     │◄────────────────┤  auto_advance (harness-gate) │         │
   └─────────┬───────────┘                 └──────────────────────────────┘         │
             │                                                                      │
             ▼                                                                      │
   ┌─────────────────────┐         ┌──────────────────────────────┐                │
   │ orchestrator/steps/ │────────►│  services/                   │                │
   │  make_plan, split,  │         │   gpt_api, prompt_composer,  │                │
   │  images, anim_pr,   │         │   gen_queue, agent_harness   │                │
   │  videos, assemble…  │         │   mass_factory, xlsx_v8_*    │                │
   └─────────┬───────────┘         └──────────────────────────────┘                │
             │                                                                      │
             ▼                                                                      │
   ┌─────────────────────┐         ┌──────────────────────────────┐                 │
   │   SQLite state.db   │◄────────┤  FastAPI :8765 (Studio UI)   │──── web/out ───┘
   │   (auto-created)    │         │  routers: db_browser, hitl,  │
   └─────────────────────┘         │  gpt_workspace, llm_costs…   │
                                   └──────────────────────────────┘
```

Подробная карта по модулям и функциям — [`docs/AGENT_MAP.md`](docs/AGENT_MAP.md)
и его «quick where is X» в §15.

---

## 6. Команды разработки

| Задача                                                      | Команда                                                                 |
| ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| Install deps                                                | `pip install -e ".[dev]"`                                               |
| Lint                                                        | `ruff check .`                                                          |
| Type check                                                  | `mypy app/ --ignore-missing-imports`                                    |
| Tests                                                       | `python3 -m pytest tests/ -v`                                           |
| Seed pilot project                                          | `python3 -m app.seed_pilot`                                             |
| Bump Studio version (перед коммитом с правкой `web/src/**`) | `python3 scripts/bump_studio_version.py`                                |
| Сборка/проверка AI-pack                                     | `python3 scripts/build_ai_pack.py && python3 scripts/verify_ai_pack.py` |

> **Lint:** `ruff check .` и `mypy app` — оба в нуле (было 888 / 409).
> Держать нулём: правки, которые не проходят, в main не едут.
> Ловушка: `ruff --fix --unsafe-fixes` умеет снести импорт, который
> тесты monkeypatch-ят через модуль (см. noqa в `frame_timeline_sync`).

### 6.1. Гейт

До 2026-08-22 в проекте не было **ни CI, ни pre-commit, ни git-хуков**:
ноль по ruff/mypy держался только вручную, и суита успела уехать в
десятки падений незамеченной. Теперь есть два независимых механизма.

**Переносимый (для машин заказчика)** — `.pre-commit-config.yaml`:

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
pre-commit run --all-files      # разово по всему дереву
```

commit: ruff + ruff format + mypy + тесты по изменённым модулям
(`scripts/pytest_touched.py`).
push: полная суита через храповик + сверка ai-pack.

**Локальный gauntlet** (только там, где стоит `~/.agents`) —
`.claude/verify.json`, ярусы turn / commit / push, плюс скан секретов.

**Храповик по тестам** — `scripts/pytest_ratchet.py`. Гейт «должно быть
зелено» здесь невозможен: без промт-библиотеки (§2.5) часть тестов падает
на отсутствии данных, а вечно красный гейт отключают в первый же день.
Проверка падает, только если появилось падение, которого нет в
`.claude/baselines/pytest-failures.txt`.

```bash
python3 scripts/pytest_ratchet.py            # сверить
python3 scripts/pytest_ratchet.py --update   # сузить базу после починки
```

База сама не обновляется: сужение должно быть видно в диффе, иначе
храповик тихо проворачивается назад.

---

## 7. Текущее состояние: ветка `hardening`

Рабочая ветка — **`hardening`** от `hygiene-rebuild` (`56897a4`).
Предыдущая — `hygiene-rebuild` от `099933e` (`stage-3-cost-accounting`).
Семантика гигиенических правок перенесена из `minimax-all-fixes` поверх
чистого format-коммита — там реформат был смешан с правками, а часть
переименований «под mypy» оставила старые имена в использовании
(5 багов, 32 упавших теста). Подробности — в сообщениях коммитов.
План-граф работ — [`docs/TECH_DEBT_PLAN.md`](docs/TECH_DEBT_PLAN.md)
(30 пунктов, 5 волн).

**Wave 1 — гигиена / процесс** (в работе):

| #   | Пункт                                                                              | Статус                        |
| --- | ---------------------------------------------------------------------------------- | ----------------------------- |
| 1   | AITUNNEL_API_KEY — false alarm, в репо только имя env-переменной   | закрыт (проверено)            |
| 2   | HANDOVER.md — этот файл                                                            | done                          |
| 3   | mypy (409) + ruff (888) → 0                                                        | done                          |
| 4   | 5 наборов инструкций → 3 (ai-pack + cursor overlay + AGENT_MAP)                    | done                          |
| 5   | auto_review + verdict-review — skip вместо auto-approve на ненастроенном промте    | done                          |
| 7   | harness-gate autouse ON + `@pytest.mark.no_harness_gate` opt-out                   | done                          |
| 30  | docs cleanup — удаление/указатели устаревших файлов                                | done                          |

Сверх Wave 1 в ветке `hardening` закрыто (2026-08-22):

| #   | Пункт                                                                  | Где                                                   |
| --- | ---------------------------------------------------------------------- | ----------------------------------------------------- |
| —   | Кадр больше не уезжает на анонимные файлохостинги (Yandex-only)        | `app/bots/outsee_http.py`                             |
| —   | Гейт: pre-commit + `.claude/verify.json` + храповик по тестам          | §6.1                                                  |
| —   | Прогон не пишет в `logs/` и `data/library` репозитория                 | `app/services/log_paths.py`, `local_library.py`        |
| 8   | Единый вход реконсайлеров + единый критерий живости (безопасная половина) | `app/services/reconciler.py`                        |
| 11  | Alembic вместо `create_all` + ad-hoc `ALTER`                           | `migrations/`, `app/db_migrations.py`                 |
| 16  | Схлопывание слепых ретраев: `Retry-After` вместо экспоненты            | `app/services/provider_breaker.py`                    |
| 17  | Circuit breaker per-провайдер                                          | там же                                                |
| 24  | Учёт денег за медиа (`media_calls`)                                    | `app/services/media_ledger.py`                        |

**Wave 2 — состояние** (частично сделано в этапах 2/5, см. `app/services/`):
lease с TTL/owner — `work_lease.py`, межпроцессный lock —
`step_global_lock.py` + `startup_guard.py`, кэш по хэшу входа —
`input_hash.py` + `orchestrator/step_dependencies.py`, alembic —
`migrations/` + `app/db_migrations.py`, порядок реконсайлеров —
`reconciler.py`. Осталось: реконсайлер **из журнала** (п.8 целиком —
журнала событий в схеме нет), вынос воркер-цикла из `main.py` /
`auto_advance.py`.

**Wave 3 — качество / деньги** (частично): контракты LLM-ответов на
Pydantic — `app/contracts/`, учёт стоимости и бюджет — `llm_ledger.py`,
`web/routers/llm_costs.py`, `LLM_BUDGET_USD`, брейкер и Retry-After —
`provider_breaker.py`. Осталось: prompt caching, 900k trim.

**Wave 4 — xlsx SoT + media fact:** SQLite WAL (есть, `db.py:36`),
`media_ledger` (есть — цены в `media_prices.json` заполняет владелец),
xlsx только экспорт/импорт, ElevenLabs API.

**Wave 5 — $-долгострой:** golden-set тестов, RAG «память завода».

---

## 8. Точки входа для ИИ-агента

1. **Первое чтение (обязательно):** [`ai-pack/START_HERE.md`](ai-pack/START_HERE.md)
   — правила игры, порядок чтения, где живёт SoT.
2. **Контракт промптов:** [`ai-pack/01_data_prompts/PROMPT_CONTRACT.md`](ai-pack/01_data_prompts/PROMPT_CONTRACT.md) +
   [`docs/PROMPT_CONTRACT.md`](docs/PROMPT_CONTRACT.md) (apply-ops, Excel/TSV
   стоп-лист).
3. **БД v2 (apply-ops, кнопка «База»):** [`docs/DB_V2.md`](docs/DB_V2.md).
4. **Архитектура / «where is X»:** [`docs/AGENT_MAP.md`](docs/AGENT_MAP.md).
5. **Операторская шпаргалка:** [`docs/OPERATOR_BIBLE.md`](docs/OPERATOR_BIBLE.md).

Антипаттерны (см. также `AGENTS.md`):

- **Не** использовать `.xlsx` как источник правды для apply-ops (только
  экспорт/импорт; правки — apply-ops в БД).
- **Не** дёргать ChatGPT через браузер для текста (только API).
- **Не** коммитить секреты / `.env`; шаблоны — `.env.example` с пустыми
  плейсхолдерами.
- **Не** редактировать `ai-pack/00_*` руками (генерится из корня через
  `scripts/build_ai_pack.py`); править оригиналы в `AGENTS.md`,
  `docs/AGENT_MAP.md` и др., потом пересобрать пакет.

---

## 9. Известные gotchas

- **Studio UI версия:** перед коммитом с правкой `web/src/**` — `python3
scripts/bump_studio_version.py`. `web/out/` в git НЕ хранится — сборка
  локальная (`npm run build` через меню студии, пункт [6]).
- **SQLite:** живёт в `data/state.db` (auto-create). Reset =
  `rm -f data/state.db; python3 -m app.seed_pilot`.
- **CHECK constraint** на enum-полях: добавление значения в
  `ProjectStatus` / `HITLDecision` / `FrameStatus` требует
  полного DB reset (старые значения могут нарушить).
- **PowerShell** пожирает кавычки в `python -c "..."` — для SQL лучше
  писать в heredoc-файл.
- **4 recover-скрипта** (`scripts/recover_heroes.py`,
  `recover_scene_images.py`, `Recover-VoiceoverFromRecycleBin.ps1`,
  `Dump-Recovered-Voiceovers.ps1`) — исторические одноразовые спасатели;
  план их архивации — п. 29 [`docs/TECH_DEBT_PLAN.md`](docs/TECH_DEBT_PLAN.md).
- **Branch `devin/*` / старые ветки** — НЕ актуальны, работаем в
  `hardening`.

---

## 10. Что НЕ входит в этот handover

- **Деплой / VPS / systemd** — см. `deploy/`, отдельная тема.
- **Series track** (длинные ролики, не shorts) — `docs/SERIES_*`,
  `templates/project_template_series_v1.xlsx`.
- **Сторонние модели / новые провайдеры** — отдельные spec-доки.

---

## 11. Указатели на канон

| Тема                                               | Файл                        |
| -------------------------------------------------- | --------------------------- |
| Cloud/dev контракт, push-ветка, providers, GPT API | `AGENTS.md`                 |
| Пакет знаний для ИИ (копии гайдов)                 | `ai-pack/START_HERE.md`     |
| Архитектура / where is X                           | `docs/AGENT_MAP.md`         |
| Операторская шпаргалка                             | `docs/OPERATOR_BIBLE.md`    |
| Контракт промптов (apply-ops)                      | `docs/PROMPT_CONTRACT.md`   |
| БД v2                                              | `docs/DB_V2.md`             |
| Система нод оркестратора                           | `docs/NODE_SYSTEM.md`       |
| Мастер-план техдолга (30 пунктов)                  | `docs/TECH_DEBT_PLAN.md` |
