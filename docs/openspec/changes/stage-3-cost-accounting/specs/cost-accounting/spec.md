# Delta: cost-accounting (stage-3-cost-accounting)

## MODIFIED Requirements

### Requirement: Один хук учёта в gpt_api.chat()

Уточнение точки врезки (target-спека остаётся в силе): «один хук» = один
модуль учёта (`app/services/llm_ledger.py`), врезанный в ТРИ физические
точки самого нижнего HTTP-слоя — `_chat_responses_stream`,
`_chat_completions_stream` и non-stream ветку `chat()`. Ретраи цикла
`chat()` живут НАД этими точками — каждый фактический POST даёт свою
строку. `fetch_completed_response` (GET готового ответа по resp_id после
обрыва SSE) отдельной строкой НЕ является — это догрузка той же
генерации, её usage попадает в строку исходного POST.

`logical_call_id` — contextvar на входе внешнего `chat()` /
`chat_pdf_in_chunks` (один id на PDF-документ); set — только если
contextvar пуст, reset в finally [панель 3/3]: вложенные слои
(adaptive 1→2→4, packed parallel, continuation, volume-добор) зовут тот
же публичный `chat()` и наследуют id родителя, включая дочерние задачи
`asyncio.gather`; последовательные внешние операции получают разные id.
Запись оборачивает всё тело транспортной функции (usage догрузки по
resp_id и классификация ошибок попадают в ту же строку).

Контекст project/node — расширение `bind_project_llm`
(`llm_override.py`): accounting-contextvar ставится всегда (не только при
vibecode-override). Вызовы вне обвязки шага (workspace, test_prompt,
чат оркестратора) учитываются с project_id=NULL, node_key="adhoc".

#### Scenario: Вызов вне обвязки шага
- **WHEN** оператор зовёт LLM из workspace/чата (нет running-шага)
- **THEN** строка записана с node_key="adhoc" без project_id; в дашборд
  прогона не входит, в разбивке по моделям видна

### Requirement: Таблица llm_calls + прайс-таблица

Уточнения реализации: таблица — в основной SQLite через
`Base.metadata.create_all` (паттерн work_leases этапа 2, без миграций);
мёртвая таблица `Attempt` не используется. Прайс —
`app/services/llm_prices.json` ({model → input/output_usd_per_m},
сырые цены релея, НЕ ×3 markup `vibecode_models_snapshot.json` —
тот остаётся концерном UI). `cost_usd` считается на записи; пересчёт
задним числом при смене прайса — вне сметы. Запись — best effort:
сбой INSERT логируется и не валит платный вызов.

`prompt_version_hash`: основной источник — биндинг из call-site'ов, уже
считающих хэш для чекпоинтов этапа 2 (text_job, scene_design, img_pr,
split) — учёт и кэш несут ОДИН хэш; fallback —
`prompt_version_hash(prompt)` от prompt-аргумента внешнего вызова (тот же
модуль `input_hash`). Второй хэш-механизм не вводится.

#### Scenario: Хэш совпадает с чекпоинтом
- **WHEN** шаг с чекпоинтом этапа 2 (например text_job) сделал вызов
- **THEN** prompt_version_hash строки равен prompt_hash чекпоинта —
  версии сравнимы между кэшем и учётом без пересчёта

## ADDED Requirements

### Requirement: Механика бюджет-предохранителя — паттерн паузы этапа 4

Превышение бюджета SHALL обнаруживаться на входе внешнего `chat()` ДО
платного вызова и перед каждой retry-попыткой (`BudgetExhausted`,
не-retryable) и приводить к немедленной паузе БЕЗ sleep-циклов
step_failure_policy. Контуры с широким `except Exception` вокруг
LLM-вызовов SHALL re-raise BudgetExhausted (иначе шаг «успешно» доехал
бы с деградированным результатом без паузы) [панель 2/3, блокер].
Отказ записи учёта SHALL быть видимым (счётчик упавших INSERT в API)
и консервативным для бюджета (оценочная стоимость незаписанных строк
включается в spent). Допустимый перерасход при параллельных задачах
одного вызова ограничен N_parallel × цена вызова; атомарная
резервация — роадмап. Механика паузы:
`status=paused`, `meta["pause_reason"] = {code: "budget_exhausted",
spent_usd, budget_usd, node}`, уведомление — существующая ветка
`notify_step_done` (второй канал не вводится). Бюджет: default
`settings.llm_budget_usd` (env `LLM_BUDGET_USD`; 0 = выключен),
per-project override `meta["llm_budget_usd"]`. Решение оператора —
`POST /api/projects/{id}/llm-budget` (поднимает бюджет, чистит причину);
перезапуск ▶ без поднятия — повторная пауза на первом же вызове
(spent — агрегат по таблице, не сбрасывается).

#### Scenario: Перезапуск без поднятия бюджета
- **WHEN** проект в pause по бюджету, оператор жмёт ▶ не подняв бюджет
- **THEN** первый же вызов LLM снова переводит проект в paused с той же
  причиной; платных вызовов не сделано
