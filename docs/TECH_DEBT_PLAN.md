# Технический долг — мастер-план

> Составлен 2026-08-21 в ветке `minimax-all-fixes`; перенесён в
> `hygiene-rebuild` 2026-08-22. Волна 1 закрыта здесь (см. HANDOVER §7),
> статусы ниже относятся к моменту составления — сверяйтесь с кодом.
>
> **Закрыто в ветке `hardening` (2026-08-22), таблица ниже это НЕ отражает:**
>
> | # | Пункт | Где |
> | --- | --- | --- |
> | 8 | Реконсайлеры: единый вход, единый критерий живости, один фоновый цикл. **Половина** — вариант «из журнала» не сделан: журнала событий в схеме нет | `app/services/reconciler.py` |
> | 11 | Alembic вместо `create_all` + ad-hoc `ALTER` (baseline + 4 ревизии) | `migrations/`, `app/db_migrations.py` |
> | 16 | Retry-collapse в части `Retry-After`: провайдерская пауза бьёт нашу экспоненту | `app/services/provider_breaker.py` |
> | 17 | Лимитер темпа + circuit breaker per-провайдер; 429 намеренно не размыкает цепь | там же |
> | 24 | Media fact accounting — таблица `media_calls`, врезка в 5 точек генерации, ключ `media` в дашборде | `app/services/media_ledger.py` |
>
> Сверх плана там же: fail-closed публикация кадров (кадры уезжали на
> анонимные файлохостинги), гейт (pre-commit + `.claude/verify.json` +
> храповик по падениям), изоляция прогона от рабочего дерева.
> Дата: 2026-08-21.
> Назначение: единый план-граф для ~30 технических долгов из аудита. Каждый
> пункт — отдельная спека с acceptance-критериями; здесь — срез, приоритеты,
> зависимости и разбивка по волнам.

---

## 0. Резюме

| #   | Пункт                                               | Волна | Зависит от | Бюджет   | Blast radius |
| --- | --------------------------------------------------- | ----- | ---------- | -------- | ------------ |
| 1   | AITUNNEL_API_KEY — проверить/убрать из репо         | 1     | —          | 0        | none         |
| 2   | HANDOVER.md — актуализировать                       | 1     | —          | 0        | none         |
| 3   | mypy + ruff — выйти в 0                             | 1     | —          | 0        | low (CI)     |
| 4   | 5 наборов инструкций агентов → 1                    | 1     | —          | 0        | low          |
| 5   | auto_review — починить/снять                        | 1     | —          | 0        | low          |
| 6   | STYLE-lock — включить обратно                       | 3     | 5          | 0        | med          |
| 7   | Harness-gate в тестах — autouse off                 | 1     | —          | 0        | low          |
| 8   | 4 реконсайлера → 1 (из журнала)                     | 2     | —          | 0        | high         |
| 9   | Lease с TTL/owner на кадры                          | 2     | 8          | 0        | high         |
| 10  | Межпроцессный lock (воркер)                         | 2     | 8, 9       | 0        | high         |
| 11  | Alembic вместо ручного ALTER                        | 2     | —          | 0        | med          |
| 12  | SQLite WAL + backup + fsync                         | 3     | 11         | 0        | med          |
| 13  | xlsx: рантайм-чтение выключить                      | 4     | 8, 12      | $500–700 | very high    |
| 14  | Prompt caching на релее                             | 3     | 3          | 0        | med          |
| 15  | 900k context trim (gpt_api.py:66)                   | 3     | 3          | 0        | med          |
| 16  | Retry-collapse (5 слоёв → 1)                        | 3     | 15         | 0        | med          |
| 17  | Лимитеры + circuit breaker per-провайдер            | 3     | 14, 16     | 0        | med          |
| 18  | Outsee max refs 2 → динамический                    | 3     | —          | 0        | low          |
| 19  | check_analysis.py — парсинг вердиктов               | 3     | 17         | 0        | med          |
| 20  | 4 контура проверок → 1                              | 3     | 5, 19      | 0        | high         |
| 21  | Проверка озвучки + сценария                         | 3     | 20         | 0        | med          |
| 22  | MasterPrompt: версия доходит до вызова              | 3     | 3          | 0        | low          |
| 23  | VPS-relay: трафик + fallback                        | 3     | 14, 17     | 0        | med          |
| 24  | Media fact accounting (kie/outsee/grsai/ElevenLabs) | 4     | 14, 17     | 0        | high         |
| 25  | ElevenLabs → API (вместо CDP)                       | 4     | 24         | $-budget | high         |
| 26  | Golden-set тестов на ноду                           | 5     | 22         | $300–400 | med          |
| 27  | RAG «память завода»                                 | 5     | 8, 22, 26  | $500–700 | very high    |
| 28  | main.py / auto_advance.py — вынести воркер-цикл     | 2     | 8, 10      | 0        | med          |
| 29  | Проверка/схлопывание 14 recover-скриптов            | 4     | 8, 12, 13  | 0        | med          |
| 30  | docs/ — инвентаризация + cleanup                    | 1     | 2, 4       | 0        | low          |

**Итого:** 30 пунктов, 6 фаз, ~12–18 недель календарного времени при
одном инженере, ~$1300–1700 внешних расходов (golden-set + RAG + ElevenLabs API).

---

## 1. Reality check (расхождения с заявкой)

Аудит местами устарел — фиксируем реальное состояние кода на `1e773cc`,
чтобы спека не врала:

| Заявка                                                                                                       | Реальность                                                                                                                                                                                                                                                                                                                                                                                        | Вердикт                                                                                       |
| ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `main.py (46k)`                                                                                              | `app/main.py` = **1032 строки**                                                                                                                                                                                                                                                                                                                                                                   | Скорее всего имелся в виду `app/bots/outsee.py` (8465) или общий объём `app/`. Перепроверить. |
| 3 точки старта воркера                                                                                       | `app/worker.py` **уже выведен** из эксплуатации (этап 2, F.1) — SystemExit при запуске. Реально стартует из **2 точек**: `app/main.py:961` и `app/web/api.py:187` через `pipeline_worker.ensure_pipeline_worker_started`.                                                                                                                                                                         | Пункт остаётся, но baseline = 2 точки, не 3.                                                  |
| 4 реконсайлера: `clamp_status_to_data`, `gen_queue_reconcile`, `_reconcile_stale_node_runs`, `startup_guard` | `clamp_status_to_data` и `startup_guard` **не существуют**. Реальные 3 стартап-реконсайлера: `gen_queue_reconcile` (`app/services/gen_queue.py:358`), `reconcile_stale_montage_jobs_on_startup` (`app/services/montage_board_job_state.py:53`), `reconcile_stale_node_runs_on_startup` (`app/services/run_sync.py:1600`). Плюс фоновый `background_node_run_reconcile_loop` (`run_sync.py:1605`). | Пункт остаётся, но схлопываем 3 стартап-вызова + 1 фоновый.                                   |
| `INFLIGHT_ATTR` без owner                                                                                    | Подтверждено: `app/orchestrator/steps/generate_images.py:78, 1021, 1080` — `attrs.pop(INFLIGHT_ATTR)` вслепую.                                                                                                                                                                                                                                                                                    | Пункт валиден.                                                                                |
| `_OUTSEE_MAX_REFS=2`                                                                                         | Подтверждено: `app/orchestrator/steps/generate_images.py:187`.                                                                                                                                                                                                                                                                                                                                    | Пункт валиден.                                                                                |
| `gpt_api.py:66` — 900k контекст                                                                              | Подтверждено: `_XLSX_CONTEXT_MAX_CHARS = 900_000` (`app/services/gpt_api.py:66`).                                                                                                                                                                                                                                                                                                                 | Пункт валиден.                                                                                |
| `conftest.py:26` — harness gate выключен autouse                                                             | Подтверждено: `tests/conftest.py` имеет `monkeypatch.setattr(settings, "harness_gate_disabled", True)`.                                                                                                                                                                                                                                                                                           | Пункт валиден.                                                                                |
| `ALTER TABLE` в `main.py:85`                                                                                 | Подтверждено: `await conn.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {col} {ctype}")` (`app/main.py:85`).                                                                                                                                                                                                                                                                                  | Пункт валиден.                                                                                |
| STYLE-lock физически выключен (обёртки no-op)                                                                | `app/bots/chatgpt.py` нет упоминаний `STYLE_LOCK`/`style_lock`/`canonical style`. В коде только CSS-стили страниц.                                                                                                                                                                                                                                                                                | Пункт валиден.                                                                                |
| `auto_review мёртв — промпты под .gitignore`                                                                 | `app/services/auto_review.py` существует и пишет проверки, но `.gitignore` исключает `prompts/*` кроме `prompts/05_excel_gpt/sd_*.md` — `prompts/check_<kind>/default.md` (путь загрузки в `auto_review.py:8`) **не закоммичен**.                                                                                                                                                                 | Пункт валиден.                                                                                |
| `check_analysis.py (1587 строк, 52 регулярки)`                                                               | Реально **1714 строк, 72 regex** (`grep -cE "(re\.\|_RE\s*=\|REGEX)"`).                                                                                                                                                                                                                                                                                                                           | Пункт валиден, метрики выросли.                                                               |
| `mypy ~178, ruff ~50`                                                                                        | Реально **mypy = 373, ruff = 877**. Долг вырос, не уменьшился.                                                                                                                                                                                                                                                                                                                                    | Пункт валиден, число больше.                                                                  |
| 14 recover-скриптов                                                                                          | Подтверждено: 5 в `app/services/` + 2 в корне + 2 в `scripts/` + 5 в `tests/` (тесты не считаются как прод-скрипты) = **9 прод-скриптов**.                                                                                                                                                                                                                                                        | Пункт валиден, но точное число = 9.                                                           |
| 5 наборов инструкций агентов                                                                                 | Подтверждено: `.cursor/` (rules + skills), `.clinerules/.clinerules` (1 файл 1.8k), `.ai-orchestra-mcp/server.js` (ролевая система для Cline), `ai-pack/` (генерируется из `scripts/build_ai_pack.py`), `docs/AGENT_MAP.md`.                                                                                                                                                                      | Пункт валиден.                                                                                |
| 2-й ключ AITUNNEL в репо                                                                                     | **НЕ утекает** — в `.ai-orchestra-mcp/server.js:12` только `process.env.AITUNNEL_API_KEY` (имя env-переменной). В git history (`git log -S 'AITUNNEL_API_KEY\s*=\s*"'`) ни одного коммита с значением.                                                                                                                                                                                            | **Закрыт**: false alarm, удалять нечего. Но см. п. 1.4.                                       |
| `HANDOVER.md` устарел                                                                                        | Сам файл уже помечен `⚠️ ДОКУМЕНТ УСТАРЕЛ` в шапке.                                                                                                                                                                                                                                                                                                                                               | Пункт валиден, нужна перезапись или удаление.                                                 |
| ElevenLabs через браузер                                                                                     | Подтверждено: `app/bots/elevenlabs.py` — `page.locator(...)` по UI elevenlabs.io.                                                                                                                                                                                                                                                                                                                 | Пункт валиден.                                                                                |

---

## 2. Граф зависимостей (верхний уровень)

```
┌─ Волна 1 (гигиена, параллельно) ─┐
│  1, 2, 3, 4, 5, 7, 30             │
└──────────────┬────────────────────┘
               │ все независимы
┌─ Волна 2 (состояние) ────────────┐
│  8 (reconcile) ──┬─► 9 (lease)   │
│                   └─► 10 (lock)  │
│  11 (alembic)                     │
│  28 (вынести воркер-цикл)         │
└──────────────┬────────────────────┘
               │
┌─ Волна 3 (качество/деньги) ──────┐
│  14, 15, 16, 17 (LLM-стоимость)   │
│  12 (SQLite WAL)  ◄─ 11           │
│  18 (Outsee refs)                 │
│  19 (verdict parsing)  ◄─ 17      │
│  20 (4 контура → 1)   ◄─ 5, 19    │
│  21 (озвучка/сценарий)◄─ 20       │
│  22 (MasterPrompt ver)            │
│  6 (STYLE-lock)        ◄─ 5       │
│  23 (VPS-relay)        ◄─ 14, 17  │
└──────────────┬────────────────────┘
               │
┌─ Волна 4 (xlsx + media) ─────────┐
│  13 (xlsx SoT)         ◄─ 8, 12  │
│  24 (media fact)       ◄─ 14, 17 │
│  25 (ElevenLabs API)   ◄─ 24     │
│  29 (recover-скрипты)  ◄─ 8, 12, 13│
└──────────────┬────────────────────┘
               │
┌─ Волна 5 ($-долгострой) ─────────┐
│  26 (golden set)       ◄─ 22     │
│  27 (RAG factory)      ◄─ 8, 22, 26│
└──────────────────────────────────┘
```

**Критические пути (longest):**

1. `8 → 9 → 10 → 28` (4 недели) — фундамент состояния
2. `11 → 12 → 13` (5 недель) — самый дорогой кусок, $500–700
3. `5 → 20 → 21` (3 недели) — контуры качества
4. `14, 15, 16, 17 → 24` (3 недели) — учёт медиа

---

## 3. Волны (детально)

### Волна 1 — Гигиена / процесс (1–2 недели)

Параллелизуется. Не зависит ни от чего. Цель — убрать шум и обновить
документацию до того, как начнём рефакторить.

- **1.** `AITUNNEL_API_KEY` — закрыт: ключ в репо не утекает. Действие:
  добавить pre-commit-guard `gitleaks` (или `detect-secrets`) на любые
  `*_API_KEY\s*=\s*['"]` в коммитах.
- **2.** `HANDOVER.md` — файл уже самопомечен устаревшим. Решение:
  переписать с нуля (~300 строк) или удалить (есть `ai-pack/START_HERE.md`).
  Рекомендация: **переписать** (на него ссылаются).
- **3.** `mypy` (373) + `ruff` (877) — выйти в 0. Не одним коммитом:
  - `ruff check --fix` (autofix), потом ручная чистка остатков;
  - `mypy --strict` по слоям (services → orchestrator → web → bots).
- **4.** 5 наборов инструкций → 1. Канон: `ai-pack/START_HERE.md` +
  `docs/AGENT_MAP.md` (pointers). Действия:
  - `.clinerules/.clinerules` — удалить (дублирует AGENTS.md);
  - `.ai-orchestra-mcp/` — заархивировать в `legacy/`, ключ AITUNNEL уже не в проде;
  - `.cursor/rules/` и `.cursor/skills/` — оставить как Cursor-специфичный
    оверлей поверх общего канона;
  - `ai-pack/` — генерируется скриптом, не редактируется руками.
- **5.** `auto_review` — починить промпты ИЛИ удалить модуль. Промпты
  `prompts/check_<kind>/default.md` живут в gitignored директории.
  Рекомендация: **удалить** (пункт 20 перепишет контуры проверок целиком).
- **7.** Harness-gate в тестах. Убрать `harness_gate_disabled=True` из
  autouse-фикстуры; добавить opt-in marker `@pytest.mark.real_harness`
  для тестов, которым нужен gate off.
- **30.** Документация. После 2 и 4 — убрать дубли в `docs/` (5+ файлов
  перекрываются с `ai-pack/`).

**Exit criteria волны:**

- `mypy app/ = 0`, `ruff check . = 0`;
- `git grep AITUNNEL_API_KEY` — только в server.js (env-имя);
- `pytest -q` зелёный с включённым harness-gate;
- новый коммит: `wf1: hygiene + lint baseline + docs dedup`.

---

### Волна 2 — Состояние (3–4 недели)

Самая технически плотная. Сначала reconcile, потом lease, потом lock —
**порядок критичен** (лочить грязное состояние = удвоить баги).

- **8.** Один реконсайлер. Текущие 3 стартап-вызова + 1 фоновый
  (`app/services/gen_queue.py:358`, `app/services/montage_board_job_state.py:53`,
  `app/services/run_sync.py:1600`, `app/services/run_sync.py:1605`) → одна
  функция `reconcile_state_on_startup()` в `app/services/state_reconcile.py`,
  читающая **журнал** (новый `state_events`) и пересчитывающая
  `Project.status` по данным. Цель: состояние — **derived view**, не ручное поле.
- **9.** Lease с TTL/owner. `INFLIGHT_ATTR` (сейчас `app/orchestrator/steps/generate_images.py:78`)
  → структура `{owner: <pid+uuid>, acquired_at, ttl_sec}`. Cron-чистка
  просроченных. Запрет брать lease, если он чужой и не истёк.
- **10.** Межпроцессный lock. Варианты:
  - (a) SQLite `BEGIN IMMEDIATE` транзакции на старте `_run_worker_loop`;
  - (b) `fcntl` flock на `data/worker.lock`;
  - (c) Postgres advisory lock (если пойдём в PG, п. 13).
    Рекомендация: **(a)** для SQLite-этапа, **(c)** при миграции в PG.
    Текущий in-process guard в `app/services/pipeline_worker.py:14-21`
    остаётся первой линией (бесплатный).
- **11.** Alembic. Поднять `alembic/`, baseline от текущей схемы
  (`models.py` + существующие миграции в `main.py:80-100`). Удалить ручной
  ALTER.
- **28.** Воркер-цикл из `app/main.py` (1032 строк) и
  `app/orchestrator/auto_advance.py` (2002 строк) → один
  `app/services/orchestrator/loop.py`. `main.py` остаётся entrypoint,
  не бизнес-логикой.

**Exit criteria волны:**

- 3 стартап-вызова реконсайлера удалены, 1 фоновый тоже;
- `Project.status` нигде не пишется напрямую (кроме reconcile);
- `INFLIGHT_ATTR` имеет owner + TTL;
- 2 одновременных `python -m app.main` не дают двойной генерации (тест);
- `alembic upgrade head` работает на чистой БД и на существующей.

---

### Волна 3 — Качество / деньги (3–4 недели)

Связка: prompt caching + 900k trim + retry collapse + circuit breaker
— четыре рычага одной задачи **«сократить input-стоимость без потери
качества»**. Делать вместе, иначе фикс одного пункта маскирует эффект другого.

- **14.** Prompt caching. Релеи vibecode/kie поддерживают
  `cache_control: {type: "ephemeral"}` для блоков system/instructions.
  Цель: master-промпты 40–130 КБ → кэшируемые блоки. Ожидаемо
  −30..70% input-токенов.
- **15.** 900k context trim. `_XLSX_CONTEXT_MAX_CHARS = 900_000` (`gpt_api.py:66`)
  → 80–120k (на уровне `_PDF_CONTEXT_MAX_CHARS`). Сначала собрать телеметрию:
  сколько байт реально используется в топ-50 вызовах. Потом резать
  по полезности (PINNED_ROWS + концентрат, остальное — summary).
- **16.** Retry collapse. 5 вложенных retry-слоёв (`outsee_retry`,
  `finalize_or_retry`, `_BATCH_BACKOFF_S`, `chatgpt` send loop, `gpt_api`
  429/5xx) → один middleware `RetryPolicy` с **общим state** (видел
  эту попытку, backoff, jitter, dead-letter). Ключ: один
  attempt-counter per logical call, не per HTTP request.
- **17.** Per-провайдер rate-limiter + circuit breaker. Провайдеры:
  vibecode, kie, grsai, outsee, elevenlabs. Три состояния CB: closed /
  half-open / open. На 429 — read `Retry-After`, иначе exponential +
  jitter. Глобальный потолок токенов/мин на провайдера.
- **18.** Outsee `_OUTSEE_MAX_REFS=2` (`generate_images.py:187`) →
  динамический лимит: `min(нужно_персонажей, лимит_провайдера)`.
  При 3+ персонажах — **две генерации** (split по группам) или
  scene_rewrite на 2 ключевых + рисуем остальных без жёсткой привязки.
- **19.** `check_analysis.py` — парсинг вердиктов. 1714 строк, 72 regex
  → парсер на Pydantic с discriminated union:
  - `Verdict = Approved | Failed | InvalidJSON | NeedsRewrite`
  - `InvalidJSON` ≠ `Failed` (текущая ошибка).
    Дополнительно: structured output (json_schema) вместо regex.
- **20.** 4 контура → 1. Текущие: `vision_check_loop`, `gpt_verdict_review`,
  harness-gate (`auto_advance.py:1225`), `auto_review` (мёртв, см. 5) →
  один `app/services/quality_gate.py` с pipeline stages.
- **21.** Проверки озвучки и сценария. Сейчас только
  «файл существует» / «длина ≥ 200». Добавить:
  - сценарий: кол-во слов, pacing, диалоги, кульминация;
  - озвучка: ASR-обратный прогон (transcribe → diff с эталоном),
    пиковая громкость, тишина >N сек.
- **22.** `MasterPrompt.version` доходит до вызова. Сейчас
  `app/models.py:510` хранит version, но `assemble_*_master_prompt`
  не пишет её в лог вызова. Связка с п. 26 (golden set требует знать
  версию для сравнения).
- **6.** STYLE-lock — включить обратно. Восстановить обёртку из
  reverted-коммита `a616b22b` (см. `git log --all -S "STYLE"`,
  коммит `3bbf5c3` от 2026-08-20), но с защитой от регрессии:
  тест на инвариант «каждая img_op идёт через lock».
- **23.** VPS-relay. Трафик считать (вход/выход байты → отдельная
  таблица `relay_traffic`), fallback на второй релей при 5xx-серии.

**Exit criteria волны:**

- input-токены на один проект упали на 30%+ (телеметрия из п. 14);
- `gpt_api` имеет один retry-middleware, остальные 4 удалены;
- `check_analysis` — 0 regex (Pydantic discriminated union);
- 4 контура проверок → 1, зелёный тест на каждый переход;
- STYLE-lock пишет в лог версию, тест на инвариант зелёный.

---

### Волна 4 — xlsx SoT + media fact (4–6 недель)

Самый рискованный кусок. **Только после волн 2 и 3.**

- **12.** SQLite WAL + fsync. Включить `PRAGMA journal_mode=WAL`,
  `synchronous=NORMAL`, backup через `.backup API`. Сейчас теряет
  данные → это причина существования 9 recover-скриптов (п. 29).
- **13.** xlsx: рантайм-чтение выключить. Текущая проблема:
  61 модуль читает xlsx в рантайме (`xlsx_text_writeback.py`,
  `xlsx_flow_locks.py`, `xlsx_sync.py`, `xlsx_v8_import.py`,
  `xlsx_versioning.py` + 56 вхождений). Контракт: xlsx — только
  экспорт/импорт, источник правды — БД (apply-ops из
  `docs/PROMPT_CONTRACT.md`). Этапы:
  1. Собрать матрицу: кто читает xlsx, зачем, какой срез нужен.
  2. Перевести всех на чтение из БД (новые read-only API).
  3. Удалить read-пути в xlsx-файлах, оставить только writeback.
  4. Удалить 9 recover-скриптов, использующих xlsx как ground truth.
- **24.** Media fact accounting. Сейчас котировка до генерации (примерная),
  факт — нигде. Провайдеры: vibecode (текст — п. 3 + llm_ledger), kie
  (image/video — outsee_resp), grsai (image), ElevenLabs (chars).
  Цель: таблица `media_ledger` с фактическими единицами.
- **25.** ElevenLabs → API. Сейчас `app/bots/elevenlabs.py` через CDP.
  ElevenLabs имеет официальный API (платный, ~$5/мес за 30k символов
  starter). Переход снимет риск CDP и даст фактический учёт символов.
  Бюджет: подписка + время на миграцию.
- **29.** 9 recover-скриптов → пересмотреть. После п. 12 (SQLite надёжен)
  и п. 13 (xlsx не SoT) часть скриптов становится не нужна.
  Архивировать в `scripts/legacy/recovery/` с пометкой, что они —
  одноразовые спасатели, не часть pipeline.

**Exit criteria волны:**

- `git grep "open.*xlsx" app/` — пусто (xlsx не читается в рантайме);
- `data/state.db` переживает kill -9 без потерь (тест);
- `media_ledger` заполняется фактическими единицами по всем 5 провайдерам;
- ElevenLabs работает через API, CDP-путь удалён;
- 9 recover-скриптов заархивированы или удалены.

---

### Волна 5 — $-долгострой (4–8 недель)

Требует внешних данных и бюджета. Делается **только при подтверждённых $-лимитах**.

- **26.** Golden-set тестов на ноду. ~$300–400 на ручную разметку
  50–100 эталонных сценариев. Запуск: каждый diff промпта
  прогоняется через golden set, скоры сравниваются (regression gate).
  Деплой: 1 коммит = авто-прогон + отчёт.
- **27.** RAG «память завода». ~$500–700: индекс по удачным сценам
  (высокий score), канон стиля/персонажей, few-shot подбор. Источник
  данных: golden set (п. 26) + история approved-проектов.
  Зависит от п. 8 (состояние) и п. 22 (версия промпта).

**Exit criteria волны:**

- golden set запускается в CI, regression >2% = блок PR;
- RAG поднимает score на сценах с 3+ персонажами (baseline сравнение).

---

## 4. Что **не** входит в эту спеку

- Миграция на Postgres (обсуждается после волны 4, если SQLite перестанет
  справляться — отдельный openspec-change).
- Миграция на LangGraph/Temporal (явно **нет** в плане, см. WORK_PLAN.md).
- Замена FastAPI / SQLAlchemy.
- Новая модель данных для БД v2 (apply-ops уже сделана, см. DB_V2.md).

---

## 5. Открытые вопросы (нужен ввод владельца)

1. **Бюджет волны 5** — есть ли $1300–1700 на golden-set + RAG + ElevenLabs
   API? Без них 5-я волна — no-op.
2. **Приоритет волн** — рекомендую 1 → 2 → 3 → 4 → 5. Если деньги важнее
   технического фундамента, можно 1 → 3 → 4 → 2 → 5 (но волна 2
   блокирует волну 4 по xlsx).
3. **Грязное дерево** в `minimax-all-fixes` сейчас содержит stage-3 work
   (3 файла + 7 новых): llm_ledger, gpt_api, llm_override, тест хука,
   onboarding-промпты. Два варианта:
   - (a) Закоммитить stage-3 отдельным коммитом в `minimax-all-fixes`,
     потом работать;
   - (b) Закоммитить stage-3 в `stage-3-cost-accounting`, потом
     rebase `minimax-all-fixes` на чистый HEAD.
     Рекомендую (b).
4. **MyPy 373 / Ruff 877** — это число больше заявленных ~178 / ~50.
   Делать полный зеро-бас или «остановиться на топ-N шумных файлах»?
   Полный — 2 недели монотонной работы; топ-10 — 3 дня.
5. **STYLE-lock revert** — в коммите `3bbf5c3` откатили `a616b22b`
   за 94 секунды. Прежде чем включать обратно — нужна защита от регрессии
   (тест на инвариант). Ок?
6. **auto_review** — чинить (долго, оживает мёртвый контур) или удалить
   (быстрее, п. 20 переписывает проверки)? Рекомендую удалить.

---

## 6. Definition of Done (для всей спеки)

- `mypy app/ = 0`, `ruff check . = 0`.
- `pytest -q` зелёный с включённым harness-gate.
- `git grep "open.*xlsx" app/` — пусто.
- `Project.status` derived, не пишется напрямую.
- 2 одновременных процесса не дают двойной генерации.
- input-токены на проект −30% (телеметрия llm_ledger).
- 4 контура проверок → 1.
- 9 recover-скриптов заархивированы.
- `HANDOVER.md` актуален, `ai-pack/` = single source of truth для агентов.

---

## 7. Ссылки

- `docs/AGENT_MAP.md` — канонный указатель для агентов.
- `ai-pack/START_HERE.md` — точка входа для ИИ.
- `docs/DB_V2.md` — apply-ops контракт.
- `docs/PROMPT_CONTRACT.md` — контракт промптов GPT↔DB.
- `docs/OPERATOR_BIBLE.md` — операторская шпаргалка.
- `WORK_PLAN.md` — предыдущий план (этапы 0–5), basis для этой спеки.
- `AGENTS.md` — Cloud/dev контракт, push-ветка.
