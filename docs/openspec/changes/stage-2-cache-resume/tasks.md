# Tasks: stage-2-cache-resume

Порядок A→F из proposal. Правило работы: одна задача за раз, перед кодом —
короткий план врезки и ок исполнителя. Тесты: `.venv/bin/python -m pytest
tests/ -q` (базовый уровень среды — 69 предсуществующих фейлов, сверять
СПИСОК с чистым деревом; после прогона `git restore ai-pack/`).

## A. Хэш-модуль

- [ ] A.1 `app/services/input_hash.py`: канонический JSON (sort_keys,
      ensure_ascii, без insignificant whitespace), `compute_input_hash(...)`
      из компонентов: вход единицы, contract (имя+версия из
      `app/contracts/_REGISTRY`) | provider+endpoint для медиа,
      prompt_version_hash, модель, параметры. Детерминизм [панель 3/3]:
      explicit allowlist полей per-единица; списки сортируются (кадры по
      uuid); тексты нормализуются (\r\n→\n, BOM strip); файлы-референсы —
      по sha256 содержимого, НЕ по пути/mtime (hero-реф сейчас «новейший
      по mtime», `generate_images.py:284-289`).
- [ ] A.2 `prompt_version_hash(project, step_code)`: sha256 от
      `get_effective_text` + вшитые хинты call-site (хинт передаётся
      параметром — сам модуль код шагов не импортирует). Экспорт для
      этапа 3 (cost-accounting использует тот же хэш).
- [ ] A.3 Юнит-тесты: стабильность hash (порядок ключей/пробелы не влияют),
      чувствительность к каждому компоненту, смена промпта → смена hash.

## B. Явный граф зависимостей

- [ ] B.1 `STEP_DEPENDENCIES` рядом с `node_registry.py`: чистый data-DAG
      «выход X — вход Y», step_code → родители; БЕЗ back-edges (возвраты
      vision — не data-deps) [панель 3/3]. Включает assemble/publish
      (конус до конца — иначе после регенерации videos сборка возьмёт
      старый mp4 по mtime, `assemble.py:54`). Опциональные шаги
      (scene_design, sfx) — в DAG статически; конус конкретного проекта =
      обход DAG × runtime-фильтр «шаг включён у проекта». Входной
      материал — skip-set'ы `reset_step.py:942-960` (music ⊥ audio,
      sd_* upstream сборщика), не дублировать.
- [ ] B.2 Тест согласованности ПРОЕКЦИЕЙ [панель 3/3]: каждая
      data-зависимость достижима в порядке `StepDef.requires`
      (`menu.py:212-271`) / `TRANSITIONS` (`auto_advance.py:103-206`);
      НЕ равенство трёх множеств (requires ≠ data-deps ≠ transitions —
      разные отношения, равенство падало бы на здоровом коде).
- [ ] B.3 Фикс коллизий ord `_STATUS_ORDER` (`menu.py:137-142`,
      music_ready=36=sfx_planning и др.) — гигиена, на порядок опираться
      нельзя, но коллизии ломают сравнения меню.

## C. Курсор + инвалидация

- [ ] C.0 Step-level input_hash в `NodeRun.meta` для ВСЕХ LLM-шагов
      [панель 1/3, разрыв спека↔tasks подтверждён]: plan, script, split,
      hero, items, anim_pr — hash пишется на успехе шага, сверяется на
      входе. Частный случай: split короткозамыкает по
      `meta.split_completed` БЕЗ проверки входа (`split_frames.py:28-36`)
      — смена script должна инвалидировать разбивку.
- [ ] C.1 `app/services/step_progress.py`: формат курсора
      `{input_hash, done_keys, updated_at}`; load с валидацией hash
      (mismatch/нет hash → сброс чекпоинта, лог «invalidated: input
      changed»); save только полных принятых единиц.
- [ ] C.2 img_pr: файловый чекпоинт (`img_pr_batches.py:91-126`) — поле
      input_hash + валидация при load; `_salvaged_partial`-единицы в
      done_keys не попадают (добор, этап 5 уже требует N/N).
- [ ] C.3 scene_design: chunk-чекпоинты в meta — тот же формат/валидация.
- [ ] C.4 `ai_jobs` (`ai_result_io.py:26-63`): input_hash в запись джоба;
      load сверяет.
- [ ] C.5 apply_ops-батчи (commit-per-batch, `enrich_xlsx.py:1063`):
      курсор батчей с hash; применённые до падения соседнего батча — не
      кэш-хит (перепроверка/добор при рестарте). Явно [панель 2/3]:
      unit_key батча = позиция+hash (одинаковые батчи не коллизируют);
      повтор непринятого батча = НОВЫЙ LLM-вызов — идемпотентен для
      field-set ops, но `replace_frames` деструктивен → шаги с
      replace_frames (split) резюмируются только целиком, не по батчам.
- [ ] C.6 Медиа-шаги (images/videos/audio): hash-валидация поверх
      «диск = истина» — `Frame.attrs["gen_input_hash"]`; PNG/mp4 при
      mismatch не переиспользуется. Hash — от АКТУАЛЬНОГО
      `Frame.image_prompt` (включая vision-правки db_patch/[VISION_FIX])
      [панель 3/3]. Целостность [панель 2/3]: файл пишется temp+rename,
      `gen_input_hash` проставляется ТОЛЬКО после успешного rename —
      kill -9 посреди записи не оставляет битый «кэш-хит»; перед
      переиспользованием — probe (`media_probe.py` есть, врезка точечная).
- [ ] C.6a Инвалидация медиа-единицы чистит её токен из
      `meta.vision_check_passed` [панель 3/3, блокер]: перегенерённый
      кадр обязан попасть на recheck (сейчас passed-кадры исключаются
      навсегда, `vision_check_loop.py:802-855`).
- [ ] C.7 Каскад: `reset_step` инвалидирует конус по STEP_DEPENDENCIES ×
      runtime-фильтр включённых шагов (сброс чекпоинтов/hash + возврат
      статуса к точке пересчёта), независимые не трогает. Триггер —
      ЯВНЫЙ запуск пересчёта оператором (существующие reset-пути UI/меню);
      lazy-проверка — защитный контур, автодетект смены промпта на
      доехавшем проекте — вне этапа [панель 2/3].
- [ ] C.8 Тесты: протухший чекпоинт не подтягивается; смена промпта
      img_pr инвалидирует ровно images→anim_pr→videos→assemble (юнит на
      каскад); опциональный шаг вне проекта каскадом не сбрасывается;
      vision_check_passed чистится при инвалидации кадра.

## D. work_leases

- [ ] D.1 Модель `WorkLease` (`app/models.py`): project_id, unit_key,
      owner, expires_at; unique(project_id, unit_key), индекс expires_at.
- [ ] D.2 `app/services/work_lease.py`: `acquire(...)` одним
      INSERT…ON CONFLICT…WHERE expires_at < :now (rowcount), `release`
      в finally, `renew`, owner=host:pid:task_uuid; TTL из settings
      (default по типу единицы: img 15 мин, video 30 мин, шаг 60 мин).
      Fencing [панель 3/3, блокер]: release = DELETE WHERE owner=:me;
      renew = UPDATE WHERE owner=:me AND expires_at > :now; rowcount=0
      на renew → задача прекращает единицу и НЕ публикует результат;
      проверка владения перед записью результата. Каждая операция — в
      собственном коротком session_scope с немедленным commit и своими
      ретраями [панель 2/3] — не внутри долгой сессии шага.
- [ ] D.2a Step-level lease `unit_key="step:<code>"` на входе в handler
      (захват ДО смены статуса на running, renew по ходу, release в
      finally) — БД-видимый признак живости для всех шагов; критерий E.3.
      Момент захвата per-unit lease — после gen_queue-hold/слотов, перед
      самой работой; renew-fail посреди генерации = остановка единицы
      [панель 2/3].
- [ ] D.3 Замена `img_gen_inflight`/`video_gen_inflight`
      (`generate_images.py`, `generate_videos.py`, recover-пути) на lease;
      маркеры в attrs перестают писаться, legacy-маркеры игнорируются
      (одноразовая чистка в recovery).
- [ ] D.4 Замена `_SPLIT_LOCK` (`step_global_lock.py`) на lease
      `project_id=0, unit_key="step:split"` — работает и между процессами.
- [ ] D.5 Тесты: конкурентный захват (rowcount=1 ровно у одного), перехват
      просроченного, различение задач одного процесса по task_uuid,
      release при отмене.

## E. startup_guard → resume; реконсайлеры

- [ ] E.1 `startup_guard`: для running-проектов — НЕ откатывать статус и
      НЕ сбрасывать NodeRun; пометка `meta["orphaned_running"]` + время;
      `arm_auto_await_manual_start` сохраняется (политика «без ▶ не
      продолжаем» не меняется). Метки startup_rollback_* больше не пишутся.
- [ ] E.2 Продолжение шага: handler'ы стартуют с курсора (C.*) — вход в
      шаг при наличии валидного курсора пропускает done_keys; recovery-скан
      диск→БД до продолжения (порядок `_startup_maintenance` сохраняется).
- [ ] E.3 Реконсайлеры на контракт lease+курсор:
      `reconcile_stale_node_runs` (`run_sync.py:1576`) и 60c-петля
      (`run_sync.py:1382-1421`) — критерий осиротевшести = отсутствующий/
      просроченный step-lease (D.2a) в БД, НЕ in-process
      `is_generation_active` (`step_cancel.py:75` — остаётся быстрым
      локальным фильтром) [панель 2/3];
      `_reset_matching_noderuns_after_rollback` удаляется вместе с
      откатом. Mass-pause батчей на старте — явная политика [панель 2/3]:
      running-батч остаётся running, его проекты резюмятся по общему
      правилу (orphaned + ▶); mass-pause включается ТОЛЬКО если у батча
      есть проект в running без курсора И без step-lease (неизвестное
      состояние — как сейчас, безопасная пауза).
- [ ] E.4 STOP оператора: курсор сохраняется на границе единицы (уже
      получается из C.* — проверить путь step_cancel), продолжение с места.
- [ ] E.5 Тест: симуляция рестарта (guard на фикстурной БД с running +
      курсором) — статус не откатился, orphaned-метка есть, NodeRun цел.

## F. Один канонический воркер

- [ ] F.1 `app/worker.py`: main() → предупреждение + запуск канонического
      пути (`pipeline_worker.ensure_pipeline_worker_started` через
      `app.main`) либо exit с указанием; собственный цикл и
      ACTIVE_STATUSES удаляются.
- [ ] F.2 Единый список активных статусов (SoT — один модуль), добавить
      sfx_planning/generating_sfx (§9#1); `main.py:300-321` читает оттуда.
- [ ] F.3 Тест: список покрывает все running-статусы enum (кроме
      осознанных исключений — фиксируются в тесте явно).

## Приёмка

Живой прогон на Chattiq: (1) kill -9 на ~80% медиа-шага → рестарт, ▶ —
доезжает за остаток (счётчик вызовов/llm_metrics), двойной генерации нет;
(2) смена мастер-промпта img_pr → пересчёт ровно конуса
images→anim_pr→videos, остальное кэш-хиты (по логам «cache hit»).
Зелёные юнит-тесты — необходимое, не достаточное. По приёмке — архив
change.
