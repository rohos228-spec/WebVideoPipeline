# Tasks: stage-3-cost-accounting

Порядок исполнения: **C → A → B → D → E** (таблица+прайс — фундамент, хук
пишет в неё, суммы независимы, связки поверх хука, бюджет+дашборд —
потребители). Нумерация блоков — по WORK_PLAN (A-E). Правило: коммит на
блок, после блока — прогон затронутых тестов; полный прогон — сверка
СПИСКА фейлов с baseline (69 предсуществующих, снят 2026-08-21 до первой
правки), после прогона `git restore ai-pack/`.

## C. Таблица llm_calls + прайс-таблица (первым — фундамент)

- [x] C.1 `app/models.py`: модель `LlmCall` (см. колонки в proposal §5;
      индексы: project_id, logical_call_id, created_at);
      `Base.metadata.create_all` — как WorkLease этапа 2, миграций нет.
      Таблица `Attempt` (`models.py:522`) НЕ используется (§9#4).
- [x] C.2 `app/services/llm_prices.json`: {model → input/output_usd_per_m},
      сидировать известные модели СЫРЫМИ ценами (не ×3 markup из
      `vibecode_models_snapshot.json` — тот остаётся UI-концерном).
      Loader в llm_ledger: нормализация имени (`_norm_model_name`-логика),
      неизвестная модель → цена 0 + WARNING один раз на модель, не падает.
- [x] C.3 `app/services/llm_ledger.py` (каркас): `record(...)` —
      собственный короткий session_scope, best effort (сбой INSERT =
      WARNING, платный вызов не валится); расчёт cost_usd на записи —
      цена по `served_model`, fallback `model` [панель 2/3];
      unbilled=true ТОЛЬКО при отсутствии всех токен-полей; частичный
      usage — не unbilled, отсутствующие поля NULL [панель 1/3].
      Отказ учёта видим [панель 2/3]: счётчик упавших INSERT +
      in-memory `unpersisted_spent` (оценочная стоимость незаписанных
      строк, входит в spent бюджета до конца процесса).
- [x] C.4 Юнит-тесты: расчёт цены (в т.ч. unknown model = 0 + warning;
      served_model приоритет), запись unbilled/частичного usage,
      best-effort при недоступной БД (счётчик и unpersisted_spent
      растут, вызов не валится).

## A. Хук в нижнем HTTP-слое + контекст

- [x] A.1 logical_call_id: contextvar в llm_ledger; ставится на входе
      внешнего `chat()` (`gpt_api.py:2162`) и `chat_pdf_in_chunks`
      (`:2622`, один id на документ). Guard [панель 3/3, блокер]:
      **set только если contextvar пуст** + reset(token) в finally —
      рекурсивные слои (adaptive `:2083`/`:2129`, packed `:2011`,
      volume-добор `volume_batches.py:271,289`, pdf-чанки `:2701`)
      зовут тот же публичный chat() и наследуют id родителя, в т.ч.
      через `asyncio.gather` (копия контекста).
- [x] A.2 Запись в 3 HTTP-точках — try/finally вокруг ВСЕГО тела
      транспортной функции, не одного client.post [панель 3/3: внутри
      тела и `fetch_completed_response` (:1674), и классификация
      429/500/empty_stream]: `_chat_responses_stream` (`:1550`),
      `_chat_completions_stream` (`:1773`), non-stream ветка `chat()`
      (`:2502-2513`). Успех И исключение — строка всегда;
      `fetch_completed_response` — НЕ отдельная строка (usage
      догруженного ответа попадает в строку исходного POST). relay —
      хост URL; endpoint — responses|chat; served_model/response_id —
      из результата.
- [x] A.3 Accounting-контекст: contextvar (project_id, node_key) в
      `llm_override.py`; `bind_project_llm` ставит ВСЕГДА (не только при
      vibecode-override): node_key = active_excel_gpt_node_key | sd-key |
      node_type статуса. Вне обвязки: project_id=NULL, node_key="adhoc".
      41 call-site не трогается.
- [x] A.4 Тесты (мок httpx, как тесты транспорта этапа 5): каждый
      физический вызов = строка; ретраи chat() = N строк с одним
      logical_call_id; adaptive 1→2→4 = 7 строк, один id; две
      ПОСЛЕДОВАТЕЛЬНЫЕ внешние операции → РАЗНЫЕ id [панель 3/3];
      неуспешный вызов записан с error_kind; контекст project/node
      доезжает; adhoc-вызов не падает.

## B. Суммирование usage на склейках

- [x] B.1 `sum_usage(*usages)` в gpt_api (или llm_ledger): покомпонентно
      prompt/completion/total, отсутствующие = 0; все пустые → {}.
- [x] B.2 Правки `usage=first.usage` → сумма: `_chat_packed_parallel`
      (`:2035`), `_chat_adaptive_1_2_4` (`:2151`); continuation — 
      накопление sum(result.usage, cont.usage) по кругам (`:2349`,
      `:2455`).
- [x] B.3 volume-добор: `volume_complete_apply_ops_reply`
      (`volume_batches.py:232`) возвращает и суммарный usage доборов
      (сейчас `(text, did)` — usage выбрасывается);
      `_maybe_volume_complete_chat_result` (`:1938`) суммирует с
      родителем.
- [x] B.3a PDF-склейка: `chat_pdf_in_chunks` (`:2802`,
      `usage=last.usage`) → sum по кускам [находка проверки плана].
- [x] B.4 Тесты: continuation ×2 → usage = сумма трёх (сценарий спеки);
      packed/adaptive — сумма частей; volume-добор — сумма с родителем;
      PDF — сумма кусков.

## D. prompt_version_hash + contract_rejected

- [x] D.1 `llm_ledger.bind_prompt_hash(...)` (contextvar) + биндинг в 4
      call-site'ах, уже считающих хэш для чекпоинтов этапа 2:
      text_job (`ai_result_io.py:143`), scene_design (`runner.py:407`),
      img_pr (`xlsx_step_runners.py:803`), split
      (`split_frames.py:38`) — в записи тот же хэш, что в чекпоинте.
      Fallback в хуке: `prompt_version_hash(prompt)` от prompt-аргумента
      внешнего chat() (импорт из `input_hash.py:70` — второй хэш не
      изобретаем).
- [x] D.2 `capture_attempt()` в llm_ledger — contextvar с
      MUTABLE-коллектором (list) [панель 3/3, вопрос закрыт]: дети
      `gather` получают копию контекста на ТОТ ЖЕ объект — append'ы
      видны родителю; `record()` дописывает id в коллектор, если
      установлен; set/reset(token) строго в try/finally (утечка скоупа
      после CancelledError). `run_with_contract`
      (`app/contracts/policy.py:196`) оборачивает каждый `call()`; при
      `LlmContractError` попытки — `mark_contract_rejected(ids)`
      (UPDATE флага, best effort). llm_metrics.jsonl НЕ трогается
      (другой учёт, этап 5).
- [x] D.3 Тесты: запись несёт хэш из биндинга; fallback работает;
      parse-fail политики помечает строки попытки contract_rejected;
      успешная попытка после repair — НЕ помечена; после исключения из
      mark_* скоуп не течёт в следующую попытку.

## E. Бюджет-предохранитель + дашборд

- [x] E.1 `app/settings.py`: `llm_budget_usd` (env `LLM_BUDGET_USD`,
      default 10.0; 0 = выключен). Per-project override —
      `project.meta["llm_budget_usd"]`; ключа нет → default; ключ=0 →
      выключен для проекта [панель 1/3].
- [x] E.2 Проверка на входе внешнего `chat()` ДО платного вызова И
      перед каждой retry-попыткой цикла (`:2268`, по кэшу) [панель
      2/3]: spent(project) ≥ budget → `BudgetExhausted` (не-retryable).
      spent — кэш суммы в ledger: инвалидация на собственную запись +
      TTL ~10 с (чужие процессы [панель 1/3]) + `unpersisted_spent`
      (C.3). Допустимый перерасход gather (≤ N_parallel × вызов) —
      зафиксирован в proposal, резервация — роадмап.
- [x] E.2a Re-raise BudgetExhausted в контурах с широким except
      [панель 2/3, блокер — иначе пауза не случается, шаг доезжает с
      мусором]: `_maybe_volume_complete_chat_result`
      (`gpt_api.py:1923-1931`, как LlmContractError),
      `chat_pdf_in_chunks._one_piece` (`:2718-2760`),
      `make_animation_prompts.py:87/:281/:314`; пройти grep'ом
      остальные `except Exception` вокруг LLM-вызовов (карта §4.4
      warning+continue) — re-raise везде.
- [x] E.3 `step_failure_policy.on_step_failure`: ранняя ветка
      BudgetExhausted → сразу `paused` + `meta["pause_reason"] =
      {code: "budget_exhausted", spent_usd, budget_usd, node}` +
      `mark_running_node_failed`, БЕЗ sleep-циклов и счёта фейлов
      (паттерн этапа 4). Ветка в `notify_step_done`
      (`app/telegram/bot.py:6925` — рядом с vision_rounds_exhausted):
      текст «бюджет исчерпан: $X из $Y» + как поднять.
- [x] E.4 `POST /api/projects/{id}/llm-budget {budget_usd}` — meta
      override + очистка pause_reason (code=budget_exhausted) + лог.
      Перезапуск ▶ без поднятия — безопасная ловушка: первый chat()
      снова паузит (spent — SUM по таблице, не сбрасывается).
- [x] E.5 API дашборда `app/web/routers/llm_costs.py`:
      `GET /api/projects/{id}/llm-costs` (по нодам: вызовы/токены/$/
      неуспешные (error ∪ contract_rejected)/unbilled количеством;
      сумма прогона; разбивка по моделям; статус бюджета; счётчик
      упавших INSERT — ненулевой = данные неполные);
      `GET /api/llm-costs/projects` (тоталы по прогонам для динамики
      + строка «adhoc» для project_id=NULL [панель 1/3]).
- [x] E.6 Студия: страница `web/src/app/costs/` — таблица нод, сумма
      прогона, график разбивки по нодам, график динамики по прогонам,
      поле бюджета (E.4). ОДИН дашборд — смета; сверх — роадмап.
- [x] E.7 Тесты: превышение бюджета → BudgetExhausted до вызова и
      перед retry-попыткой; policy-ветка паузит с причиной без
      sleep-циклов; re-raise из volume/PDF/anim_pr-контуров (E.2a) —
      исключение доходит до policy, а не глотается; endpoint поднимает
      бюджет и чистит причину; API агрегатов (фикстурные строки
      llm_calls) считает ноды/модели/unbilled/adhoc верно.

## Приёмка

Живой прогон (Chattiq): стоимость ролика по агентам видна в UI, включая
неуспешные вызовы; цифры сходятся с биллингом релея по строкам с usage
(допуск — округление прайса; unbilled — количеством); разгон выше
бюджета → paused «бюджет исчерпан: $X из $Y». Полный прогон тестов —
список фейлов совпадает с baseline (69). По приёмке — архив change.

## Отклонения реализации от плана (зафиксировано по факту, 2026-08-22)

- **A.2, обёртка**: транспортные функции не переписывались — переименованы
  в `*_impl`, публичные имена стали обёртками `_record_transport_call`
  (вокруг всего тела). Non-stream POST выделен в `_chat_completions_plain`
  (заодно получил `response_id` из payload — раньше не заполнялся).
  `chat()`/`chat_pdf_in_chunks()` — тонкие обёртки `**kwargs` со скоупами
  над `_chat_unscoped`/`_chat_pdf_in_chunks_unscoped` (сигнатуры были
  keyword-only — вызовы не меняются).
- **A.4, adaptive 1→2→4 = 7 строк**: не симулируется юнитом (нужны живые
  db_frames-срезы); вложенность проверена напрямую — `chat()` внутри
  открытого скоупа наследует id родителя + unit-тест guard'а. Живой
  прогон подтвердит 7 строк на одном logical_call_id.
- **B.3, volume-добор**: сигнатура `(text, did)` сохранена (обратная
  совместимость тестов и моков этапа 5); usage отдаётся через опциональный
  out-параметр `usage_acc`.
- **D.1, prompt-hash**: fallback хэшируется от `prompt`-аргумента внешнего
  chat() (без system/attachments) — шум для call-site'ов с мастер-промптом
  во вложении (`ask_with_files`), пока те не забиндят хэш явно.
- **E.2, кэш spent**: не «инвалидация на запись», а инкремент кэша
  собственной записью (`_bump_spent`) + TTL 10 с на перечитывание SUM
  (чужие процессы) + `unpersisted_spent`. Кэш бюджета (meta) — тот же
  TTL; endpoint поднятия бюджета инвалидирует кэш своего процесса.
- **E.2a, grep-проход**: re-raise добавлен в `_maybe_volume_complete`,
  PDF `_one_piece`, anim_pr фаза 1 (`:314`), `apply_ops_batches._run_adaptive`
  (дробление бессмысленно), `enrich_xlsx` retry-loop. `anim_pr :87`
  уже re-raise'ит не-outage ошибки, `:281` — чтение файла, не LLM.
  Остальные широкие except в LLM-модулях (проверено по коду) — вокруг
  скачиваний/снапшотов/парсеров, не вокруг вызовов.
- **E.3, Telegram**: два канала сообщений уже существовали (main.py по
  action и notify_step_done по статусу) — ветка добавлена в оба;
  policy возвращает новый action `pause_budget` (не `pause_infra` — у того
  текст про Chrome).
- **E.6, студия**: не страница-маршрут, а Sheet-панель «Стоимость» из
  topbar (студия — single-page, панели открываются CustomEvent'ами, как
  Сеть/База). Графики — CSS-бары (chart-библиотеки в проекте нет).
  `node_modules` на машине исполнителя нет — TS не прогнан через tsc,
  проверен чтением; сборка студии у заказчика (`npm run build`) — часть
  приёмки.
- **Тесты**: новые — test_llm_ledger / _hook / _contract / _usage_sum /
  _budget; существующие тесты не менялись (старая семантика
  `usage=first.usage` тестами не фиксировалась).

## Ревью кода (code-critic, 2026-08-22)

Панель чужих моделей НЕ ответила: все 4 модели — `http 401 Key is
blocked` на chattiq.ru (разблокировка `/key/unblock` — админ шлюза,
не исполнитель). Разбор — собственная проверка агента code-critic по
коду/запуском; голосов панели нет. Повторный прогон панели — после
разблокировки ключа (до приёмки).

Закрыто фикс-коммитом:
- дашборд «по моделям»: `group_by("model")` резолвился SQLite во входную
  колонку `llm_calls.model` — разные served-модели одной запрошенной
  сливались в одну строку; теперь GROUP BY по выражению coalesce
  (тест в test_llm_budget);
- `error_code=budget_exhausted` не доезжал до NodeRun/события:
  `mark_running_node_failed` перезаписывает код через `describe_error`
  → был "unknown"; добавлен `budget_exhausted` в ERROR_CATALOG + матч по
  типу BudgetExhausted (тест);
- бар бюджета вылезал за контейнер при spent > budget — клэмп;
- PDF: fallback prompt-hash уровня документа (куски хэшировались каждый
  по своему piece_prompt внутри одного logical_call_id).

Осознанно принято:
- ветка budget в `notify_step_done` (bot.py) недостижима сегодня — пауза
  идёт путём отказа (main.py по action `pause_budget`); оставлена как
  защита на случай появления notify на failure-пути (двойного сообщения
  сейчас нет);
- двойной учёт строки в кэше spent при параллели (record → чужой
  TTL-refresh уже включает строку → `_bump_spent` добавляет ещё раз) —
  консервативно (пауза раньше), самолечится на следующем refresh,
  в пределах допустимого перерасхода proposal.
