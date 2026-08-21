# Tasks: stage-5-llm-contracts

Порядок — A→D из WORK_PLAN. Правило работы: одна задача за раз, перед кодом —
короткий план врезки и ок исполнителя. Тесты: `.venv/bin/python -m pytest
tests/ -q` (известный предсуществующий фейл `test_check_fix_writeback_applies_tsv`).

## 0. Риск-блокер транспорта

- [x] 0.1 Проба Chattiq/gpt-5.6-sol: **enforces** (2026-08-21, скрипт в
      scratchpad, отчёт probe_out/report.json; baseline нарушает,
      json_object недостаточен, strict соблюдён; downgrade-контроль чист).
- [ ] 0.2 Проба kie (обе ветки: responses + chat) и vibecode — БЛОКИРОВАНО
      ключами заказчика. До вердикта оба релея работают в деградации
      (без response_format). Скрипт готов, цели kie-responses/kie-chat/
      vibecode-chat уже параметризованы.

## A. Транспорт (gpt_api.py)

- [x] A.1 `app/settings.py`: режим structured outputs `auto|on|off`
      (env `GPT_STRUCTURED_OUTPUTS`, default auto) + карта per-relay
      вердиктов В КОНФИГЕ, не хардкодом (chattiq=enforces;
      kie/vibecode=unverified) — смена вердикта без правки кода.
- [x] A.2 `chat()`: опциональный параметр контракта (имя схемы из реестра);
      сборка `response_format` (chat-ветка) / `text.format`
      (responses-ветка) без переписывания тела.
- [x] A.3 Прокинуть параметр через ВСЕ шесть контуров транспорта:
      `_chat_adaptive_1_2_4` (обе половины дробления получают ту же схему;
      coverage-валидация — ПОСЛЕ склейки `_merge_packed_apply_ops`,
      схемная валидность части ≠ успех целого), `chat_pdf_in_chunks`,
      `ApiGptClient.ask_with_files`, `run_operator_api`, плюс два контура
      ВНУТРИ chat(), не видных снаружи [панель 4/4 и 3/4]:
      - CF-continuation (`gpt_api.py:2171` responses, `:2270` vibecode):
        `cont_body` собирается с нуля, строковая склейка
        `stitch_llm_continuation` несовместима со strict-JSON (схема в
        continuation → `…}{…`; без схемы → strict-префикс + мусорный
        хвост). Решение: в контрактном режиме continuation ВЫКЛЮЧЕН —
        обрыв = невалидная попытка → обычный ретрай целого вызова.
      - volume-добор (`_maybe_volume_complete_chat_result`,
        `gpt_api.py:2230/:2312/:2349` → рекурсивный `chat()` в
        `volume_batches.py:285`, парс через старый экстрактор :306):
        рекурсивный вызов наследует контракт, добор парсится схемой.
- [x] A.4 Served-model детект: `GptChatResult.model` сейчас везде =
      ЗАПРОШЕННАЯ модель (`gpt_api.py:1622,:1746,:2219,:2302,:2347`),
      фактическая — только в raw. Вытащить фактическую из payload/SSE,
      нести в результате; mismatch на контрактном пути = ошибка транспорта
      (retryable), не вход валидации. [панель 4/4; поведение LiteLLM при
      fallback — проверить живой пробой, не проверено]
- [x] A.5 Тесты транспорта (мок httpx): параметр присутствует/отсутствует
      по режиму; continuation не активируется в контрактном режиме;
      volume-добор наследует схему; mismatch модели → ошибка; существующие
      вызовы без контракта не затронуты.

## B. Реестр контрактов app/contracts/

- [x] B.1 Каркас модуля + базовый класс контракта (schema → json_schema для
      response_format; единая точка `validate(payload)`).
- [x] B.2 `ApplyOpsEnvelope`: алиасы через `AliasChoices` (много синонимов
      на поле); неизвестное поле — ошибка с именем поля. Решения по
      находкам панели [3/4 и 4/4]:
      - `FIELD_ALIASES`/`PROJECT_FIELD_ALIASES` (`db_apply.py:115-231,:240`)
        ОСТАЮТСЯ на месте как единственный источник — contracts-модуль
        импортирует словарь и строит из него AliasChoices (нет дрейфа двух
        копий); немигрированные потребители (`plan :246`, `script :380`,
        A13 `scene_grammar_batches.py:192`, A24 `db_browser.py:2258`,
        операторский клиент) продолжают работать через `normalize_fields`.
        Удаление словаря — вне этапа, после миграции всех.
      - Канон-нормализация ключей как в `_canon_key` (`db_apply.py:257-259`:
        lower/strip/пробелы→`_`) — pre-validation, иначе «Промт Картинки»
        начнёт падать там, где сегодня чинится.
      - Два синонима одного поля в одном op: сегодня last-write-wins молча;
        сохранить last-write-wins, но с логом в meta (не превращать в
        «неизвестное поле» через extra=forbid).
- [x] B.3 Pre-validation нормализаторы: Хэмминг-ремонт uuid (:290),
      «номер→uuid» (:335) — ДО схемы, НЕ отвергать, лог ремонта в meta.
      Salvage (`db_apply.py:367`): маркер `_salvaged_partial` (:485)
      снимается ДО валидации → в meta единицы, НЕ в payload (иначе
      extra=forbid убьёт сам маркер) [панель 1/4, подтверждено кодом].
- [x] B.4 `FrameSpec` (split), `ImgPrOps`, `AnimPrOps`, `VoiceoverPayload`.
- [x] B.5 `SkeletonPayload` + `SceneSlice`×4 + `AssemblePayload`
      (формат {characters, scenes, ops, report}; используется и A13).
- [x] B.6 `CheckReport` (vp.check.v1) — типизация отчёта проверки.
- [ ] B.7 Юнит-тесты схем: живые примеры ответов (из data/ и логов) +
      ломаные кейсы; паритет с текущим `normalize_fields`.

## C. Миграция приёмочного множества (состав менять нельзя)

- [x] C.1 A12 apply-ops ядро (`enrich_xlsx.py:1084`, батчи
      `apply_ops_batches.py:251`; +A14 character_registry — тот же путь).
- [x] C.2 A3 split (`xlsx_step_runners.py:509`); локальная разбивка —
      `split_voiceover_locally` внутри `extract_frames_spec_from_gpt_reply`
      (`:471-474`; ссылка system-map `:591-594` устарела) — только с
      маркером `degraded_no_llm`.
- [x] C.3 scene_design-комплекс: A5 skeleton, A6 editor, A7-A10 срезы,
      A11 assemble (`skeleton.py`, `runner.py`).
- [x] C.4 A15 img_pr (`xlsx_step_runners.py:876`): убрать `if all_ops:
      continue` (:948, :967) — провал батча = ошибка батча; итоговый гейт —
      покрытие N/N кадров; salvage (`img_pr_batches.py:261`) — только с
      добором.
- [x] C.5 A17 check-отчёты: `parse_check_analysis` → `CheckReport`; битый
      ответ проверки = `LlmContractError` + repair, НЕ `verdict: fail`
      (закрыть `check_analysis.py:1334, :1346, :1351`).
      Радиус поражения [панель 2/4]: парсер общий — его зовут и
      операторские контуры вне приёмки (`gpt_operator.py:1101/:1636/:1691`,
      `gpt_operator_client.py:103/:275/:476/:792`). Strict-поведение —
      опт-ин параметром для мигрированного вызова; остальные вызовы
      сохраняют текущее поведение до своей миграции.

## D. Единая ошибка + repair-политика

- [x] D.1 `LlmContractError` + модуль политики по образцу
      `ai_result_io.text_job:87-140`: фидбек «ошибки прошлой попытки»,
      раздельные лимиты parse/validate (default по 2), отклонённые ответы
      на диск (data/<proj>/llm_rejects/, retention: последние 20 на проект
      либо 14 дней), fail-closed на исчерпании → step_failure_policy.
      Мультипликация вызовов [панель 2/4]: repair ложится ПОВЕРХ ретраев
      chat() (×5) и дробления (до 7 вызовов) — repair-попытка НЕ
      перезапускает дробление заново (ретраится только упавшая единица),
      общий cap платных вызовов на единицу работы логируется и
      ограничивается (default 12).
- [x] D.2 Закрыть volume-добор: break при ошибке
      (`volume_batches.py:296-305`) — недобор до N/N = ошибка; сам добор
      наследует контракт (см. A.3).
- [x] D.3 Закрыть script-fallback «весь ответ целиком»
      (`xlsx_step_runners.py:379-410`) — невалидный ответ = ошибка.
      (script не в приёмочном множестве, но путь в списке обязательных
      тихих путей спеки.)
- [x] D.4 anim_pr локальный композер (`make_animation_prompts.py:104`) —
      маркер деградации + предупреждение оператору. Реализация: маркеры
      `split_degraded_no_llm` / `anim_pr_degraded_no_llm` в project.meta
      (не NodeRun.meta — обвязка NodeRun для этих шагов не «одна строка»,
      а project.meta уже виден UI/harness); снимаются на свежем прогоне.
- [x] D.5 Метрика repair-rate — определение зафиксировано [панель 4/4]:
      - знаменатель: логическая единица работы политики (агент-вызов/батч),
        НЕ HTTP-запрос;
      - числитель: единицы с ≥1 repair-попыткой (repair = повторный вызов
        из-за parse/validate-fail контракта);
      - транспортные ретраи chat() (`gpt_max_retries=4`), дробление 1→2→4 и
        continuation repair'ом НЕ считаются — отдельные счётчики;
      - хранение (реализация отклонилась от NodeRun.meta в ПРОСТУЮ сторону):
        `data/<proj>/llm_metrics.jsonl`, одна строка = одна единица работы,
        пишет policy на успехе И на исчерпании — переживает рестарты и
        failed-runs без реконсиляции (исходный риск панели закрыт);
        сбор/вердикт приёмки — `scripts/llm_repair_rate.py`.

## E. Передача

- [x] E.1 Инструкция + шаблон миграции агента в `docs/openspec/`
      (схема → политика → тест на живой задаче) — проверить на A16 anim_pr
      как учебном примере (без включения в приёмку). A21 music не подходит
      [панель 1/4, подтверждено]: сырой текст без JSON — схему не на чем
      показать.

## Приёмка

Серия ≥10 живых прогонов (Chattiq): 0 `LlmContractError` после repair на
приёмочном множестве, repair-rate ≤ 10% (определение — D.5). Прогон, где
агент приёмочного множества ушёл в `degraded_no_llm` (LLM не звали), в
серию НЕ засчитывается [панель 1/4]. Зелёные юнит-тесты — необходимое,
не достаточное (harness-гейт в тестах выключен). По приёмке — архив change.
