# Change: stage-3-cost-accounting

**Дата:** 2026-08-21
**Основание:** WORK_PLAN.md Этап 3 (блоки A-E); спека
`specs/cost-accounting/spec.md` (решения зафиксированы 2026-08-20:
самописный учёт, НЕ Langfuse); карта `system-map.md` §4.1 (транспорт,
слои ретраев), §9 #4 (Attempt — мёртвая таблица, учёт на ней не строить);
инфраструктура этапов 5 (`app/contracts/policy.py`, llm_metrics.jsonl),
2 (`input_hash.prompt_version_hash`, WorkLease-паттерн таблицы) и 4
(`meta["pause_reason"]` + ветка в `notify_step_done`).

## Why

usage парсится транспортом и выбрасывается; при склейках теряется кратно
(`usage=first.usage` — packed/adaptive/continuation/volume-добор); полей
стоимости в БД нет; прайс-таблицы текстовых моделей нет. Ретраи вложены в
5 слоёв, не знающих друг о друге (аудит §3) — заказчик не видит, во что
обошёлся логический вызов, нода и прогон, и не имеет предохранителя от
разгона ретраев. Неуспешные вызовы (timeout/429/500 после N ретраев) —
тоже деньги, сейчас невидимы полностью.

## Ключевые решения дизайна

### 1. Хук — в САМОМ нижнем HTTP-слое, три физические точки

Карта даёт «один хук в `chat()` (:2052)», но фактический HTTP живёт ниже:
`chat()` — цикл ретраев над тремя транспортными функциями. Хук ставится
там, где реально уходит POST (иначе ретраи внутри одного `chat()` слились
бы в одну строку):

1. `_chat_responses_stream` (`gpt_api.py:1550`) — SSE Responses API;
2. `_chat_completions_stream` (`gpt_api.py:1773`) — SSE chat/completions
   (vibecode/OpenAI-совместимые);
3. non-stream ветка `chat()` (`client.post`, `gpt_api.py:2502-2513`).

Каждая точка оборачивается try/finally-записью `llm_ledger.record(...)`
вокруг ВСЕГО тела транспортной функции, не одного `client.post`
[панель 3/3 — при обёртке всего тела в строку попадают и usage догрузки
`fetch_completed_response` (вызывается внутри `:1674`), и классификация
ошибок 429/500/empty_stream]: строка пишется и на успехе, и на
исключении. `fetch_completed_response` (GET готового ответа по resp_id
после обрыва SSE) — НЕ отдельная строка: это та же генерация,
догруженная вторым запросом; её usage попадает в строку исходного POST.

Один модуль `app/services/llm_ledger.py` — сама запись, контекст, цены,
бюджет. Правки `gpt_api.py` — только врезки вызовов (тело функций не
переписывается). Все 41 call-site и обёртки (adaptive, continuation,
volume-добор, PDF-chunks, операторский слой) проходят через эти три
точки — покрытие полное by construction.

### 2. logical_call_id — contextvar на внешнем вызове

`logical_call_id = uuid4` ставится contextvar'ом на входе ВНЕШНЕГО
`chat()` (публичный вызов call-site'а; рекурсивные вызовы — adaptive
1→2→4, packed parallel, continuation, volume-добор — контекст наследуют,
в т.ч. через `asyncio.gather`: дочерние задачи получают копию контекста
с тем же значением). `chat_pdf_in_chunks` ставит один id на весь
документ (куски — физические вызовы одной логической единицы).

Guard вложенности [панель 3/3, блокер]: рекурсивные слои зовут тот же
публичный `chat()` (packed `:2011-2024`, adaptive `:2083`/`:2129`,
pdf-чанки `:2701`, volume-добор `volume_batches.py:271,289`) —
безусловный uuid4 на входе перетёр бы id родителя. Правило: **set
только если contextvar пуст**, reset(token) в finally; тест — «две
последовательные внешние операции → разные id, вложенные вызовы →
id родителя». Сценарий спеки «1→2→4 = 7 строк с одним logical_call_id»
выполняется без знания слоёв друг о друге.

### 3. Контекст project/node — расширение bind_project_llm

`bind_project_llm` (`llm_override.py:49`, обвязка шага в
`pipeline.py:176`) сегодня ставит contextvar ТОЛЬКО при наличии
vibecode-override (иначе None) и не несёт project/node. Расширение:
второй contextvar `llm_accounting_context` (project_id, node_key),
который bind_project_llm ставит ВСЕГДА:

- node_key: `meta.active_excel_gpt_node_key` (excel_gpt/check-ноды) |
  sd-node key (уже вычисляется) | node_type статуса — та же логика
  выбора, что сейчас для override, но без условия «есть override».
- Вызовы вне обвязки (gpt_workspace, test_prompt, db_browser-чат):
  project_id=NULL, node_key="adhoc" — учитываются, в дашборд прогона не
  входят (нет project_id), видны в сумме по моделям.

41 call-site не трогается — контекст ставится в одной обвязке.

### 4. prompt_version_hash — существующий модуль, два источника

Импорт из `app/services/input_hash.py` (этап 2 сделал его общим
сознательно — второй хэш не изобретаем):

- **Основной путь:** call-site'ы, уже считающие prompt_hash для
  чекпоинтов (text_job `ai_result_io.py:143`, scene_design
  `runner.py:407`, img_pr `xlsx_step_runners.py:803`, split
  `split_frames.py:38`), кладут ЕГО в accounting-контекст
  (`llm_ledger.bind_prompt_hash(...)`) — записи несут тот же хэш, что
  чекпоинты: версии сравнимы между кэшем и учётом.
- **Fallback (контекст не установлен):** хук считает
  `prompt_version_hash(prompt)` от prompt-аргумента внешнего `chat()` —
  та же функция, честная семантика «версия текста, ушедшего в модель».
  Для шагов с динамикой в prompt хэш будет уникальнее (шум), но
  требование спеки «смена мастер-промпта → другой хэш» выполняется;
  расширение биндинга на остальные call-site'ы — по мере их миграции.

### 5. Таблица llm_calls — основная SQLite, паттерн work_leases

`app/models.py`, `Base.metadata.create_all` (как WorkLease этапа 2,
миграций нет). Одна строка = один фактический HTTP-вызов; агрегаты —
всегда SUM (по logical_call_id / node_key / project_id), строк-сумм нет.
Таблица `Attempt` (`models.py:522`) не используется — мёртвая (§9#4).

Колонки: `id`, `created_at`, `project_id` (nullable, index),
`node_key`, `logical_call_id` (index), `model` (запрошенная),
`served_model` (фактическая, этап 5), `relay` (хост из URL вызова:
chattiq/kie/vibecode/…), `endpoint` (responses|chat),
`prompt_tokens`/`completion_tokens`/`total_tokens` (nullable),
`cost_usd` (по прайсу на момент записи), `result` (ok|error),
`error_kind` (timeout|network|http_NNN|empty_stream|…),
`unbilled` (bool: usage не вернулся — tokens NULL),
`contract_rejected` (bool, дополняется колбэком политики),
`prompt_version_hash`, `response_id`, `duration_ms`.

Запись — best effort в собственном коротком session_scope: сбой INSERT
логируется WARNING и не валит платный вызов. Отказ учёта видим, а не
молчит [панель 2/3, риск «предохранитель тихо выключается при болезни
БД»]: ledger держит in-process счётчик упавших INSERT и их суммарную
оценочную стоимость (`unpersisted_spent`); стоимость упавших записей
ВКЛЮЧАЕТСЯ в spent бюджет-проверки до конца процесса, счётчик отдаётся
в API дашборда (ненулевой = данные неполные). `Точность`: cost
считается на записи по прайс-таблице; пересчёт задним числом при смене
прайса — вне сметы (агрегатам достаточно).

Семантика unbilled [панель 1/3]: unbilled = НИ одного токен-поля в
usage (обрыв без тела). Частичный usage (есть prompt_tokens, нет
completion) — НЕ unbilled: cost по имеющимся компонентам, отсутствующие
поля NULL.

### 6. Прайс-таблица — JSON в репо, unknown = 0 + warning

`app/services/llm_prices.json`: `{model: {input_usd_per_m,
output_usd_per_m}}` + loader в llm_ledger (lru_cache; смена цен
подхватывается рестартом воркера — осознанно, TTL не нужен). Ключ
поиска цены [панель 2/3, вопрос]: приоритет `served_model` (фактическая
модель ответа, этап 5), fallback — запрошенная `model`; нормализация
имени как `_norm_model_name` (провайдер-префикс не значим). Прайс v1 —
единый по модели, БЕЗ разбивки по релеям: сидируется под релей
приёмки (Chattiq); расхождение тарифов между релеями — роадмап.
Неизвестная модель → цена 0 + WARNING (один раз на модель за процесс,
не падает).
Существующий `vibecode_models_snapshot.json` — НЕ источник: это цены UI
с PRICE_MARKUP ×3 (маркетинговый концерн), учёт же должен сходиться с
биллингом релея; известные модели сидируются его сырыми значениями.

### 7. Суммирование usage на склейках (блок B спеки)

`usage=first.usage` теряет кратно. Хелпер `sum_usage(*usages)`
(prompt/completion/total покомпонентно, отсутствующие = 0, все пустые →
{}) и правки:

- `_chat_packed_parallel` (`gpt_api.py:2035`) — sum по parts;
- `_chat_adaptive_1_2_4` (`gpt_api.py:2151`) — sum по parts;
- continuation responses/vibecode (`gpt_api.py:2349`, `:2455`) —
  sum(result.usage, cont.usage) с накоплением по кругам;
- volume-добор: `volume_complete_apply_ops_reply` возвращает и суммарный
  usage доборов (сейчас `(text, did)` — usage выбрасывается);
  `_maybe_volume_complete_chat_result` (`gpt_api.py:1938`) суммирует;
- PDF-склейка `chat_pdf_in_chunks` (`gpt_api.py:2802`,
  `usage=last.usage`) — sum по кускам [находка проверки: не была в
  исходном списке блока B].
- merge SSE-чанков (`:1837`, `:1702`) — НЕ склейка: usage приходит в
  финальном чанке одного вызова, там корректно.

Ledger-строки точны независимо от этих правок (пишутся на нижнем слое) —
блок B чинит usage в `GptChatResult` для потребителей и для сверки
с биллингом по логическому вызову.

### 8. contract_rejected — колбэк из repair-политики этапа 5

`run_with_contract` (`app/contracts/policy.py:157`) оборачивает каждый
`call()` в `llm_ledger.capture_attempt()` — contextvar-скоуп, собирающий
id строк, записанных в этой попытке. Механика [панель 3/3, вопрос
закрыт]: contextvar держит **mutable-коллектор** (list) — дочерние
задачи `gather` получают копию контекста, указывающую на ТОТ ЖЕ
объект, append'ы видны родителю (присваивание нового значения — нет);
`record()` дописывает id строки в коллектор, если тот установлен;
set/reset(token) — строго в try/finally, иначе после
CancelledError скоуп утёк бы в следующую попытку той же Task. При
`LlmContractError` попытки (parse/validate — HTTP был успешен, контракт
отверг) политика зовёт `llm_ledger.mark_contract_rejected(ids)` —
UPDATE флага строк. «Доля
неуспешных» в агрегатах = error ∪ contract_rejected. llm_metrics.jsonl
этапа 5 не трогается — это другой учёт (единицы работы политики);
прямой импорт ledger в policy — как уже сделано для метрик (best
effort, сбой не валит repair-цикл).

### 9. Бюджет-предохранитель — паттерн паузы этапа 4

- Default из конфига: `settings.llm_budget_usd` (env `LLM_BUDGET_USD`,
  default 10.0; 0 = выключен — dev-режим). Per-project override —
  `project.meta["llm_budget_usd"]`; семантика [панель 1/3]: ключа нет →
  default из конфига; ключ = 0 → выключен для проекта (явно).
- Проверка — на входе внешнего `chat()` (до платного вызова) И перед
  каждой retry-попыткой внутри цикла `chat()` (дешёвая, по кэшу) —
  ретраи одного вызова не пробивают бюджет [панель 2/3]. spent =
  `SUM(cost_usd) по project_id` — кэш в ledger: инвалидация на
  собственную запись + TTL (~10 с) для чужих процессов [панель 1/3:
  кэш процесс-локальный, а процессов бывает больше одного —
  `pipeline.py:130-140`]; плюс in-memory `unpersisted_spent` упавших
  INSERT (§5). ≥ бюджета → `BudgetExhausted` (не-retryable).
- **Допустимый перерасход зафиксирован** [панель 2/3]: параллельные
  задачи `gather` (`_chat_packed_parallel`, до 3 конкурентных) могли
  прочитать spent < budget одновременно — перерасход ограничен
  N_parallel × цена одного вызова; атомарная резервация — роадмап
  (ломала бы best-effort запись).
- **BudgetExhausted не глотается** [панель 2/3, блокер]: исключение из
  `chat()` проходит контуры с широким `except Exception` — без правок
  шаг «успешно» доехал бы с мусором (chunked_partial, локальный
  композер), пауза бы не случилась (деньги защищены проверкой до POST,
  ломается семантика паузы). Явный re-raise BudgetExhausted (как уже
  сделано для LlmContractError) в: `_maybe_volume_complete_chat_result`
  (`gpt_api.py:1923-1931`), `chat_pdf_in_chunks._one_piece`
  (`:2718-2760`), `make_animation_prompts.py:87/:281/:314`; остальные
  warning+continue пути карты §4.4 добить на кодинге grep'ом по
  `except Exception` вокруг LLM-вызовов + тест на каждый правленый
  путь.
- `step_failure_policy.on_step_failure` получает раннюю ветку:
  `BudgetExhausted` → БЕЗ sleep-циклов и счёта фейлов сразу
  `status=paused`, `meta["pause_reason"] = {code: "budget_exhausted",
  spent_usd, budget_usd, node}`, `mark_running_node_failed`, уведомление —
  существующий канал `notify_step_done` (ветка по code, как
  vision_rounds_exhausted у этапа 4). Второй канал не изобретается.
- Решение оператора: поднять бюджет — `POST
  /api/projects/{id}/llm-budget {budget_usd}` (пишет meta-override,
  чистит pause_reason, лог); продолжение — существующий ▶. Перезапуск
  без поднятия бюджета — безопасная ловушка: первый же chat() снова
  паузит (счётчик не сбрасывается — это SUM по таблице).

### 10. Дашборд — один, в смете (решение заказчика 2026-08-20)

- API: `app/web/routers/llm_costs.py`:
  - `GET /api/projects/{id}/llm-costs` — по нодам (вызовы/токены/$/из
    них неуспешные + unbilled количеством), сумма прогона, разбивка по
    моделям, статус бюджета (spent/budget) + счётчик упавших INSERT
    (§5: ненулевой = данные неполные);
  - `GET /api/llm-costs/projects` — тоталы по прогонам (прогон =
    проект, `WorkflowRun` 1:1) для графика динамики + отдельная строка
    «adhoc» (project_id=NULL: workspace/чат — иначе эти строки не видны
    ни в одном срезе [панель 1/3]).
- Студия: страница `web/src/app/costs/` — таблица «нода → вызовы /
  токены / $ / неуспешные», сумма прогона, два графика (разбивка
  стоимости по нодам, динамика по прогонам), поле бюджета (endpoint §9).
  Единственная правка next.js в смете; аналитический контур сверх —
  роадмап.

## Границы (что НЕ делаем)

- Таблица `Attempt` не используется и не удаляется (§9#4).
- llm_metrics.jsonl / scripts/llm_repair_rate.py (этап 5) — не сливаем,
  не ломаем: другой учёт.
- Медиа-генерация (img/video/voiceover) — вне учёта: спека «стоимость
  ТЕКСТОВЫХ LLM» (vision-вызовы — текстовый транспорт, учитываются).
- Пересчёт cost задним числом при смене прайса; Langfuse/внешние
  трекеры; аналитика сверх одного дашборда — роадмап.
- Атомарная резервация бюджета (write-ahead строка до вызова) и
  JSONL-буфер упавших INSERT для reconcile — роадмап [панель GROWTH];
  v1 закрывает риски in-memory `unpersisted_spent` + счётчиком в API.
- Per-relay прайс (разные тарифы одного model у разных релеев) —
  роадмап; v1 — единый прайс под релей приёмки.
- Next.js-студия вне страницы дашборда не трогается.

## What Changes

- **A. Хук учёта**: `app/services/llm_ledger.py` (новый) — record в 3
  HTTP-точках `gpt_api.py`, logical_call_id, accounting-контекст в
  `llm_override.bind_project_llm`, биндинг prompt_hash в 4 call-site'ах
  этапа 2.
- **B. Суммирование usage**: `sum_usage` + 4 точки склейки в
  `gpt_api.py`, возврат usage из `volume_batches`.
- **C. Модель + прайс**: `app/models.py` (+LlmCall),
  `app/services/llm_prices.json`, loader; запись неуспешных/unbilled.
- **D. Связки**: prompt_version_hash в записи; `contract_rejected` —
  capture_attempt в `app/contracts/policy.py`.
- **E. Бюджет + дашборд**: `app/settings.py` (+LLM_BUDGET_USD),
  BudgetExhausted-ветка в `step_failure_policy.py`, ветка в
  `notify_step_done` (`app/telegram/bot.py`), роутер
  `app/web/routers/llm_costs.py`, страница `web/src/app/costs/`.

## Impact

- Affected specs: `cost-accounting` (дельта: MODIFIED «один хук» — три
  физические точки нижнего слоя одного модуля; ADDED механика бюджета
  по паттерну pause_reason; уточнение прайс-файла и fallback-хэша).
- Affected code: `app/services/{gpt_api,llm_override,llm_ledger,
  volume_batches}.py`, `app/models.py`, `app/contracts/policy.py`,
  `app/services/step_failure_policy.py`, `app/telegram/bot.py`,
  `app/settings.py`, `app/web/routers/llm_costs.py` (+регистрация),
  `web/src/app/costs/`, 4 call-site'а биндинга prompt_hash, тесты.
- Поведенческий риск 1: INSERT на каждый HTTP-вызов в основную SQLite
  (WAL) — объём мал (десятки строк на шаг), запись best effort; при
  contention видно по WARNING, вынос в отдельный файл — по факту.
- Поведенческий риск 2: бюджет-предохранитель с default 10$ начнёт
  паузить дорогие прогоны — желаемое поведение; порог — конфиг,
  калибровка заказчиком (как VISION_CHECK_MAX_ROUNDS).
- Поведенческий риск 3: `BudgetExhausted` внутри шага = шаг падает
  посреди работы — чекпоинты/lease этапа 2 гарантируют resume без
  двойной оплаты после поднятия бюджета.

## Панель-ревью (2026-08-21)

3 голоса из 4 (kimi-k2.6 — таймаут 600s); сырые рецензии —
`~/.agents/var/panel/2026-08-21__21-39-51/`. 2 блокера — закрыты
правками этого proposal/tasks (маркеры [панель N/3] по тексту):
guard вложенности logical_call_id (set-if-unset + reset в finally);
re-raise BudgetExhausted в контурах с широким except (иначе пауза не
случается — шаг доезжает с мусором). Риски врезаны: unpersisted_spent
+ счётчик упавших INSERT в API (отказ учёта видим); TTL кэша spent
(межпроцессная видимость); проверка бюджета перед retry-попытками +
зафиксированный допустимый перерасход gather; приоритет served_model
в прайсе; adhoc-срез в API; семантика unbilled при частичном usage и
meta-бюджета 0/отсутствует. Принято к блоку B: PDF-склейка
usage=last.usage (`:2802`) — четвёртая течь. Механика capture_attempt
доопределена (mutable-коллектор, try/finally). Отброшено проверкой по
коду: синхронный session_scope (движок async), «finally запишет строку
до fetch_completed_response» (GET внутри тела функции), «volume/PDF —
свой HTTP-клиент» (идут через chat()), «WorkflowRun >1 на проект»
(unique), фантомные id в mark_contract_rejected.

## Приёмка (критерий этапа, из спеки)

Живой прогон (Chattiq): стоимость ролика по агентам видна в UI, включая
неуспешные вызовы; цифры сходятся с биллингом релея по строкам с usage
(допуск — округление прайса; unbilled показаны количеством). Разгон
ретраев выше бюджета → paused с причиной «бюджет исчерпан: $X из $Y».
Зелёные юнит-тесты — необходимое, не достаточное (harness-гейт в тестах
выключен).
