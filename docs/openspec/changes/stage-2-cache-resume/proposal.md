# Change: stage-2-cache-resume

**Дата:** 2026-08-21
**Основание:** WORK_PLAN.md Этап 2; спека `specs/cache-resume/spec.md`;
карта `system-map.md` §3 (воркер/startup), §6 (лизинги), §8 (реконсайлеры);
реестр контрактов этапа 5 (`app/contracts/`).

## Why

Состояние конвейера — один enum `Project.status`; рестарт процесса
откатывает все running к `step.requires` (`startup_guard.py:70-187`,
«рестарт = откат назад»). Кэша по входу нет (кроме ASR): чекпоинты
img_pr/scene_design/ai_jobs короткозамыкают по имени/uuid БЕЗ проверки
входа — смена промпта подтягивает протухшие результаты. INFLIGHT-маркеры
(`img_streams.py:20-21`) — без TTL/owner: упавший процесс оставляет кадр
«занятым» навсегда, два процесса генерят дважды. Второй legacy-воркер
(`app/worker.py:29-47`) со своим списком статусов — точка двойного
исполнения. Итог для заказчика: убитый на 80% прогон оплачивается заново.

## Ключевые решения дизайна

### 1. Единицы работы и input_hash

Три уровня единиц, у каждого своё место хранения hash (существующие
JSON-колонки, новых таблиц под hash нет):

| Уровень | Примеры | Хранение |
|---|---|---|
| шаг (NodeRun) | plan, script, split, enrich_N, img_pr, anim_pr | `NodeRun.meta["input_hash"]` |
| батч/чанк | батчи apply_ops, батчи img_pr (файловый чекпоинт), chunk'и scene_design, `ai_jobs[name]` | поле `input_hash` внутри записи чекпоинта |
| кадр-генерация | img/video/audio на кадр | `Frame.attrs` |

`input_hash = sha256(канонический JSON от:)`

- нормализованный вход единицы (для LLM — тот же payload, что уходит в
  модель: срез кадров/закадр/контекст);
- имя + версия контрактной схемы из `app/contracts/_REGISTRY` — нормативный
  «вход» этапа 5, второй не изобретаем; для медиа-единиц вместо схемы —
  provider + endpoint;
- sha256 эффективного текста промпта — `get_effective_text(project,
  step_code)` (`gpt_text_builder.py:479`) + вшитые хинты кода (`_*_DB_HINT`,
  футеры батчей). НЕ git-ревизия (prompts/* под .gitignore). Тот же хэш =
  `prompt_version_hash` этапа 3 — один модуль-источник;
- модель + параметры генерации (для медиа: размер/aspect/refs).

Cache hit — только полные принятые результаты: `_salvaged_partial` и
батчи, применённые до падения соседнего (commit-per-batch,
`enrich_xlsx.py:1063`), хитом не считаются — добор/повтор.

**Детерминизм сборки входа [панель 3/3]:** hash строится по explicit
allowlist входных полей per-единица, НЕ «всё, что видно». Канонизация:
сортировка списков (кадры — по uuid, не по порядку выборки), нормализация
переводов строк/BOM в текстах промптов, референсы — по sha256 содержимого
файла, НЕ по пути/mtime (сейчас hero-реф выбирается «новейший по mtime»,
`generate_images.py:284-289` — touch файла менял бы вход). Hash
медиа-кадра считается от АКТУАЛЬНОГО `Frame.image_prompt` — включая
правки vision-контура (db_patch/[VISION_FIX]): исправленный промпт =
другой вход. GPT-rewrite/moderation-rewrite провайдерного слоя
(`outsee_retry`) в hash осознанно НЕ входит — это ретрай-механика того же
входа, фиксируется в коде комментарием.

### 2. STEP_DEPENDENCIES — явный конус

Константа `STEP_DEPENDENCIES: dict[step_code, tuple[parent_step_code, ...]]`
рядом с `node_registry.py`. НЕ выводится из `_STATUS_ORDER`
(коллизии ord, `menu.py:137-142`, §9#3) и не из if/elif диспетчера.

Семантика [панель 3/3]: это чистый **data-DAG** («выход X — вход Y»),
БЕЗ back-edges (возвраты vision_check_loop — не зависимость данных) и БЕЗ
условных веток исполнения. Опциональные шаги (scene_design по флагу, sfx
pass-through) присутствуют в DAG статически; при вычислении конуса
конкретного проекта применяется runtime-фильтр «шаг включён у проекта» —
каскад не сбрасывает шаги, которых у проекта нет. Data-deps ≠
`StepDef.requires` (пререквизиты запуска) ≠ `TRANSITIONS` (порядок
статусов) — это три разных отношения; тест согласованности — проверка
ПРОЕКЦИЕЙ (каждая data-зависимость достижима в порядке статусов), не
равенством множеств. Существующие skip-set'ы исключений
(`reset_step.py:942-960`: music независим от audio, sd_* — upstream
сборщика) — входной материал для DAG, не дублируются. Конус доходит до
конца: assemble/publish — в DAG (регенерация videos инвалидирует финальный
mp4, иначе сборка возьмёт старый «новейший по mtime», `assemble.py:54`).

### 3. Инвалидация: lazy по hash + явный каскад

- **Lazy (защитный контур):** при загрузке любого чекпоинта сверяется его
  `input_hash` с текущим; mismatch → чекпоинт сбрасывается, единица
  выполняется заново. Чекпоинт без hash (legacy) = mismatch. Гарантия:
  протухший результат не переиспользуется НИКОГДА, каким бы путём шаг ни
  запустился.
- **Каскад вниз (активный триггер) [панель 2/3]:** lazy сам по себе не
  переоткрывает доехавшие шаги (`*_ready` подтверждается наличием данных
  без hash, `step_data_guard.py:384-409`) — поэтому пересчёт запускается
  ЯВНО: оператор меняет промпт → запускает пересчёт шага X (существующие
  UI/меню reset), `reset_step` каскадно инвалидирует чекпоинты/hash ровно
  конуса по STEP_DEPENDENCIES (с runtime-фильтром включённых шагов) и
  возвращает статус к точке пересчёта. Автодетект смены промпта на
  доехавшем проекте — вне этапа (осознанно: правка файла не должна сама
  запускать платный пересчёт).
- **Стык с vision-контуром (этап 4 не ждём) [панель 3/3]:** инвалидация
  медиа-единицы чистит её токен из `meta.vision_check_passed` — иначе
  перегенерённый кадр никогда не попадёт на recheck
  (`filter_image_paths_for_recheck`, `vision_check_loop.py:802-855`,
  «[ok] навсегда»). Правка vision'ом `Frame.image_prompt` меняет hash
  кадра (см. §1) — done_keys курсора кадр с изменённым промптом не скроет.

### 4. Персистентный курсор

Единый helper (`app/services/step_progress.py`): формат
`{input_hash, done_keys: [...], updated_at}`. Существующие чекпоинты
(img_pr файловый `img_pr_batches.py:91-126`, chunk'и scene_design,
`ai_jobs` в `ai_result_io.py:26-63`) переводятся на этот формат/валидацию,
не заменяются. Для медиа-шагов курсор фактически есть (диск = истина +
`Frame.attrs`) — добавляется hash-валидация, не второй учёт.

### 5. work_leases — TTL + owner, атомарный захват

Таблица `work_leases` (project_id, unit_key, owner, expires_at;
unique(project_id, unit_key); индекс по expires_at) в основной SQLite —
WAL + busy_timeout уже включены (`app/db.py`). Захват — один оператор:
`INSERT … ON CONFLICT(project_id, unit_key) DO UPDATE SET owner=:me,
expires_at=:new WHERE work_leases.expires_at < :now`, проверка rowcount.
`owner = host:pid:task_uuid` (per-asyncio-task). Release в `finally`;
потерянная задача отдаёт lease по TTL; для длинных единиц — renew.

**Fencing [панель 3/3, блокер]:** release и renew — ТОЛЬКО со сверкой
владельца: `release = DELETE … WHERE unit_key=:k AND owner=:me`;
`renew = UPDATE … SET expires_at=:new WHERE unit_key=:k AND owner=:me AND
expires_at > :now`. rowcount=0 на renew = lease потерян (перехвачен либо
истёк) → задача НЕМЕДЛЕННО прекращает единицу работы и НЕ публикует
результат; перед записью результата (файл/БД) — та же проверка владения.
Иначе зомби-задача, пережившая TTL, удаляет чужой lease и пишет поверх
перехватившего.

**Транзакционные границы [панель 2/3]:** acquire/renew/release — каждый в
СОБСТВЕННОМ коротком `session_scope` с немедленным commit и своими
ретраями на «database is locked»; НЕ внутри долгой сессии шага (NullPool +
commit-в-конце держал бы writer-lock до конца шага, а ретраи воркера
`main.py:341-360` оборачивают весь advance — фейл release после платного
вызова перезапустил бы handler).

**Момент захвата [панель 2/3]:** acquire — непосредственно перед началом
работы единицы, ПОСЛЕ прохождения gen_queue-hold и слотов (иначе TTL
истекает в очереди). Renew-fail посреди долгой генерации = остановка
единицы (результат выбрасывается), не «доработать и посмотреть».

**Step-level lease [панель 2/3, закрывает критерий E]:** помимо
per-кадровых lease, вход в handler любого шага берёт lease
`unit_key="step:<code>"` на project_id (renew по ходу шага, release в
finally). Это даёт БД-видимый признак «шаг живой» для ВСЕХ шагов — не
только img/video/split — и определяет критерий осиротевшести (§6). Захват
step-lease происходит атомарно ДО смены статуса на running (окно
«running без lease» отсутствует by construction; обратное окно «lease без
running» безвредно — истечёт по TTL).

**Решение по хранению (фиксируем, замер не делаем заранее):** основная
SQLite. Вынос в отдельный `lease.db` — только если живой прогон покажет
contention («database is locked» на lease-путях); ретраи ×3 в воркере уже
есть (`main.py:345-360`).

Заменяются: `img_gen_inflight`/`video_gen_inflight` (`img_streams.py:20-21`)
и in-process `_SPLIT_LOCK` (`step_global_lock.py:14-35` → lease с
`project_id=0, unit_key="step:split"`). НЕ трогаем: `montage_lane`
(остаётся как есть), `video_gen_skip` (ручной маркер, не lease),
`vision_check_passed` (материал этапа 4).

### 6. startup_guard: resume вместо отката

`block_pipeline_autorun_on_startup` перестаёт откатывать `Project.status`
и сбрасывать NodeRun. Вместо этого: running-проект, чей step-lease (§5)
отсутствует или просрочен, помечается `meta["orphaned_running"]`
(+ метка времени); lease-строки чужих/мёртвых owner'ов истекают по TTL.
Критерий «живости» — step-lease в БД, НЕ in-process
`is_generation_active` (`step_cancel.py:75` остаётся как быстрый
локальный фильтр, но реконсайлеры смотрят в БД) [панель 2/3]. Политика «не продолжать старую работу
автоматически» СОХРАНЯЕТСЯ: `arm_auto_await_manual_start` остаётся — по ▶
(или авто-политике) шаг стартует с того же running-статуса и доезжает с
курсора. Recovery-скан диск→БД (`_backfill_from_disk`, recover_*) — ДО
продолжения, как сейчас.

Реконсайлеры, построенные на «рестарт = откат», переводятся на контракт
lease+курсор в этом же change — список в tasks (§8 карты):
`reconcile_stale_node_runs`, `background_node_run_reconcile_loop`,
`_reset_matching_noderuns_after_rollback` (умирает вместе с откатом),
mass-pause батчей на старте.

### 7. Один канонический воркер

`app/worker.py` → редирект на канонический путь
(`pipeline_worker.ensure_pipeline_worker_started`) либо exit с указанием.
Список активных статусов — один (закрывает §9#1: sfx-статусы отсутствуют
в `main.py:300-321`). Коллизии ord `_STATUS_ORDER` (§9#3) чинятся как
гигиена в этом же change: на них нельзя опереться нигде.

## Границы (что НЕ делаем)

- Оркестратор (poll + if/elif) не переписывается; врезки — `NodeRun.meta`,
  существующие чекпоинты, `startup_guard`, `run_sync`, `reset_step`.
- Таблица `Attempt` не используется (мёртвая, §7 карты).
- `montage_lane`, `vision_check_passed`, контуры проверок — этап 4.
- Учёт стоимости — этап 3 (наш prompt-hash модуль ему отдаётся как есть).
- xlsx-слой, studio-ui, telegram-меню — только если точка врезки требует.

## What Changes

- **A. Хэш-модуль** `app/services/input_hash.py`: канонизация, сборка
  hash, prompt_version_hash (единый с этапом 3).
- **B. STEP_DEPENDENCIES** рядом с `node_registry.py` + тест
  согласованности + фикс коллизий ord.
- **C. Курсор** `app/services/step_progress.py` + перевод существующих
  чекпоинтов (img_pr, scene_design, ai_jobs) на формат с input_hash;
  lazy-инвалидация при загрузке; каскад в `reset_step` по
  STEP_DEPENDENCIES; правило «полный принятый результат».
- **D. work_leases**: модель + helper `app/services/work_lease.py`;
  замена img/video inflight и _SPLIT_LOCK; renew для длинных единиц.
- **E. startup_guard/реконсайлеры**: orphaned_running вместо отката;
  адаптация реконсайлеров; NodeRun не сбрасывается на рестарте.
- **F. Один воркер**: редирект `app/worker.py`, единый ACTIVE_STATUSES
  (+sfx), правка 4 точек поднятия не требуется — они уже идут через
  singleton, кроме legacy.

## Impact

- Affected specs: `cache-resume` (дельта: MODIFIED хранение lease —
  решение зафиксировано; MODIFIED resume — auto_await сохраняется).
- Affected code: `app/models.py` (+WorkLease), `app/services/{input_hash,
  step_progress,work_lease}.py` (новые), `startup_guard.py`, `run_sync.py`,
  `reset_step.py`, `img_pr_batches.py`, `ai_result_io.py`,
  `scene_design/*` (чекпоинты), `generate_images.py`,
  `generate_videos.py`, `step_global_lock.py`, `app/worker.py`,
  `app/main.py` (ACTIVE_STATUSES), `node_registry.py`, `menu.py` (ord),
  тесты.
- Поведенческий риск 1: рестарт больше не откатывает статус — сценарии,
  молча чинившиеся откатом (полусгенерённый мусор на диске), теперь должны
  чиниться recovery-сканом; первые живые прогоны покажут.
- Поведенческий риск 2: lazy-инвалидация legacy-чекпоинтов без hash — 
  первый прогон после деплоя пересчитает то, что раньше короткозамыкалось
  (одноразовая стоимость прогрева).
- Миграция БД: одна новая таблица (`work_leases`), создаётся
  `Base.metadata.create_all` — как существующие.

## Панель-ревью (2026-08-21)

3 голоса из 4 (kimi-k2.6 — http 000 дважды); сырые рецензии —
`~/.agents/var/panel/2026-08-21__10-25-24/`, `…__10-29-49/`. 6 блокеров —
все закрыты правками этого proposal/tasks (маркеры [панель N/3] по
тексту): fencing lease (D.2), data-DAG без back-edges + тест проекцией
(B.1/B.2), стык с vision (C.6/C.6a), критерий осиротевшести через
step-lease (D.2a/E.3), покрытие step-level hash (C.0), явный триггер
каскада + конус до assemble (C.7/B.1). Риски панели (транзакционные
границы, детерминизм hash, момент захвата, целостность файла,
mass-pause) — врезаны в соответствующие задачи. Осознанно принято:
GPT-rewrite провайдерного слоя не в hash; автодетект смены промпта на
доехавшем проекте — вне этапа; гранулярность каскада — шаг, не батч
(частичные правки крупных проектов — кандидат роадмапа).

## Приёмка (критерий этапа, из спеки)

Живой прогон (Chattiq): (1) прогон убит kill -9 на ~80% медиа-шага —
рестарт доезжает за стоимость остатка (по счётчику вызовов; llm_metrics
этапа 5 уже пишется); двойной генерации нет; (2) смена мастер-промпта
img_pr пересчитывает ровно конус images → anim_pr → videos; закадр,
split, scene_design, hero — кэш-хиты.
