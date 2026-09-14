# Change: merge-customer-fork-2

**Дата:** 2026-09-14
**Основание:** задание владельца 2026-09-14 «забери фичи, которых нет,
за последнюю неделю, без архитектурных сдвигов — текущая архитектура
правильная». Источник: `https://github.com/rohos228-spec/video-pipeline`
(remote `theirs`), диапазон `17eff87d..c53a238b` (2026-09-03 → 2026-09-14).
Предыдущий перенос — `docs/openspec/changes/merge-customer-fork/`
(трейлер `Source: theirs/main 17eff87d` на `3d29ea4b`/`edbe2191`).

## Why

С прошлого переноса у заказчика 22 содержательных коммита, 188 файлов,
+14.9k/−4.3k. Три четверти объёма — три вещи, которые мы не берём
(§0). Остальное — фичи Студии и фиксы, которых у нас нет: SFX по
видео-склейке, перегенерация одного клипа, healing имён персонажей,
многоуровневые парсеры ответа плана, живые результаты нод, чат по
фотографии. Фронт мы после 17eff87d не трогали (кроме 4 файлов), поэтому
их `web/src` ложится почти целиком; бэкенд разошёлся по всем файлам, и
диффы переносятся по хункам.

## 0. Что НЕ берём (архитектурные сдвиги и исключённое ранее)

| Что | Объём | Почему |
|---|---|---|
| Изоляция БД по проектам: `app/project_db.py` (+731), `get_project_session`, `project_db_session_scope`, `sync_project_row_to_master_db`, дубли `Artifact` в master, `scripts/migrate_to_project_dbs.py`, `tests/test_isolated_project_db_concurrency.py`, `test_dual_db_healing.py` (кроме 3 тестов воркфлоу) | ~60 % бэкенд-диффов | Ответ на SQLite table locks. У нас Postgres + RLS + арендаторы; отдельный SQLite на проект — движение в обратную сторону |
| `api-tracker/` (+4009), `app/services/api_tracker_hook.py`, `record_api_call`/`check_api_allowed` во всех провайдерах, kill-switch, Supabase-синк, `API_TRACKER.cmd` | новый сервис | Отдельный процесс учёта затрат с облачной синхронизацией. У нас леджер, холды и котировки внутри студии (`llm_ledger`) |
| kie: `kie_http.py`, `kie_kling.py` ретраи, `media_route`, выбор kie-видеомодели в `outsee_retry.py`, `usd_per_video` у kie-записей | — | kie мёртв (решение 2026-08-26); `kling-3-0` остаётся `online:false` |
| Windows: `SETUP.cmd`, `scripts/setup.ps1`, `studio.ps1`, `create-desktop-shortcut.cmd`, `assets/icon.*`, `.gitattributes` CRLF, обработчик `ProactorEventLoop` в `main.py`, `web/VideoPipelineStudio.ps1` | — | Не наша платформа |
| Auto-sync xlsx: `project_sheet.write_general` v8 (regex-парсинг плана по координатам листа), `patch_project` → запись в `project.xlsx`, `export_xlsx=True` в `enrich_xlsx`, перестановка `write_general`/snapshot в `make_plan`/`make_script` | +152/+22 | Решение 2026-08: Excel — только экспорт, источник истины — БД. Это вторая реализация схемы данных внутри xlsx-слоя |
| Их Anthropic-транспорт в `gpt_api.py` (+375), `text_llm_catalog` GPT-роутинг 5.5→5.6 | — | У нас `anthropic_messages.py` на официальном SDK с брейкером, tool_use, structured outputs. Их вариант беднее. Берём только идею `on_delta` |
| `vibecode_models_snapshot.json` их версии | — | Два разных слепка `/v1/models`; переснимать целиком, не мержить |
| `is_parent_grid_frame` + `need[:30]` в `generate_image_prompts.py` | — | Прямой конфликт с нашим веером `camera_expand` (дочерние шоты нужны для img_pr) |
| Анонимные файлохостинги uguu/0x0/telegra.ph в `ensure_public_image_url` | — | У нас публичные хосты за `OUTSEE_ALLOW_PUBLIC_HOSTS`; расширять безусловно — регресс приватности |
| Удаление `3k` из `generation_options.py`, их ожидания в `test_outsee_image_ui_parity.py` | — | Их снимок каталога Outsee; наш `3k` живой, тест паритета требует |
| Отключение CDP для видео (`generate_videos.py` −48) | — | Архитектурное; развилка `_video_http_primary()` у нас уже есть |
| `prompts/04_hero/default.md` их редакции, `test_prompt_library_fallback::test_hero_default_prompt_exists` | +80 | Наш жанровый контент |
| `read_prompt` тихий фоллбэк на `available[0]` при отсутствии `default.md` | — | Маскирует пропавший промт произвольным; у нас fail-fast |
| Доки: `ARCHITECTURE_AUDIT*.md`, `BACKLOG.md`, `README.md`, `HOW_TO_RUN.md`, `.clinerules`, `START_AI_ORCHESTRA_PROMPT.txt`, `ai-pack/*` | — | Как в прошлом переносе |
| `POST /api/meta-agent/assist-project` | +142 | Фронт после `a8b5a03f` не зовёт (ИИ-кнопки мастера убраны в бэклог) |

## 1. Что берём — по блокам

### 1.1 Фронт (`web/src`, коммит `e9a71186`)

25 файлов: 21 целиком (`git checkout theirs/main --`), 3 трёхсторонним
патчем (`api.ts`, `studio-workspace.tsx`, `node-v-menu.tsx`),
`node-model-catalog.ts` — вручную (kling-3-0 остаётся `online:false`,
берётся только `usd_per_video`). `package.json` и `types.ts` не менялись.
Версия студии — их 514 + 1 = 515.

Фичи: `SfxPlanView`/`SfxGenView` и панели настроек SFX-нод; аудио-плеер и
текстовые артефакты в результатах; редактируемый общий план (текст/таблица);
превью кадра у промтов анимации; поллинг результатов картинок/видео 5 с;
кнопка «Перегенерировать клип»; оптимистичное сообщение, лайтбокс,
автоскролл в чате; группы Anthropic/xAI/Google в пикере LLM; мастер проекта
в один экран; редактор промта слота в «ИИ-редакторе сцен»; расчёт числа
кадров под сценарий; цена за клип и метка T2V в пикере моделей.

Контракт с бэкендом (проверено по вызовам):
- **`POST /projects/{id}/frames/{fid}/regenerate-video`** — новая ручка,
  без неё кнопка даёт 404 → блок 1.4.
- `models[].group` в `/api/text-llm` — без него Claude попадает в группу
  «OpenAI» → блок 1.5.
- `pricing.usd_per_video` в каталоге видео → блок 1.4.
- `meta.sfx_generated` — **проверено, бэкенд не нужен**: файлы звуков
  приходят в ноду ассетами из `data_dir/sfx`, и ветка с файлами флаг не
  читает вовсе. Флаг меняет только подпись ноды БЕЗ файлов
  («сгенерированы» против «ещё не сгенерированы»). Заводить его ради
  этого не стали: у нас готовность звуков — `ProjectStatus.sfx_ready`,
  а не ключ `meta`.

### 1.2 Звук (блок audio)

Из `15df9f01`, `89630978`, `2cc9b708`: SFX по фактической видео-склейке
(`sfx_plan.frame_timeline` с ASR/`Frame.start_ts`, `frame_starts` и
`offset_in_frame` в `sfx_plan.json`, `collect_sfx_inputs(video_frame_starts=)`);
ElevenLabs SFX: эндпойнт `/v1/sound-generation` вместо `/v1/sound-effects`,
3 ретрая, допуск длительности, фоллбэк на локальный синтез, `sfx_provider=local_synth`;
SFX и `voice_gain` в variant2 (P1-4); `sfx_plan`/`sfx_gen` в `LINEAR_NODE_TYPES`;
фоллбэки `_apply_approve` для канваса без SFX-нод; чистка Suno-промта.
Не берём: variant3-хунк `assemble.py`, `elevenlabs_proxy_url`.

### 1.3 Ядро (блок core, из `89630978`)

P1-1 спаны сцен с курсором (`db_apply`); P1-2 `step_registry.running_statuses()`
в `gen_queue`/`project_state` (у нас в очереди не хватало
`scene_designing`/`scene_assembling`); P2-1 `_IMG_EXTENSIONS` одним
определением в 7 файлах; P2-4 `commit_with_retry` в роутерах (их список
расширен на `project_ops.py` — 42 голых commit); P2-5 таймауты ffmpeg;
P2-2 `get_running_loop`; P1-3 снос tokenrouter/Kimi.
«Уязвимости P1–P3» из названия PR #313 — приоритеты их архитектурного
аудита, security-фиксов там нет.

### 1.4 Медиа-шаги (блок media)

Ручка `regenerate-video` (на наших `get_session`/`archive_older_frame_clips`);
healing имён персонажей вместо `raise`; disk-фоллбэки готовности
(`characters/*.png`, `items/predmet*`); commit перед refresh в hero;
`render_hero_text` дописывает стиль/бриф без плейсхолдеров; HTML-отчёт
монитора; `raise_if_cancelled` + `acquire_image_slot` в предметах;
`_resolve_item_descriptions` — только ветка Entity; событие `image_generated`;
реквей кадра на transient-ошибке сети; httpx-фоллбэк скачивания и ретраи
хостов; ускорение старта (`skip_assembled`); `clamp_parent_frames` + хинт
Shorts; `img_pr_streams` из meta; `usd_per_video` в `apply_markup`.

### 1.5 LLM и чат (блок llm)

Чат: анализ приложенного фото без веб-поиска, строже `_looks_like_document`;
`break` на `[DONE]` в SSE; **`on_delta` для Claude-стрима** (наш пробел:
чат не видел токены по мере генерации); восстановление дефолтного
воркфлоу (`run_sync`/`settings_default`); многоуровневый
`extract_general_plan_from_gpt_reply` и plain-JSON ветка в `run_script_xlsx`
(хвост «весь ответ как закадр» не берём — fail-closed остаётся); лейбл
«ИИ-редактор сцен»; `fallback` как источник промта; каталог: `group`,
модели из нашего снапшота (Terra, Luna, Gemini 3.1 Pro / 3 Flash, Opus 4.8,
Fable 5, Grok 4.6), `TEXT_MODEL_ALIASES`. Дефолт Opus 5 не меняется.

## 2. Механика

Как в прошлом переносе: фронт — checkout + 3-way с базой 17eff87d;
бэкенд — вручную по хункам, четыре агента в отдельных worktree по
непересекающимся файлам, по коммиту на блок, каждый с трейлером
`Source: theirs/main c53a238b`. Гейт на каждом коммите (ruff, mypy 0,
тесты блока, голдены). Смоук фронта на копии базы после слияния блоков.

## 3. Открытые вопросы владельцу

7.1 **Приоритет Entity над ручным hero** (`2286a55b`,
    `_load_excel_hero_from_xlsx`): их правка убирает ранний выход по
    `has_manual_hero` — Entity сцен-агента становятся источником каста,
    ручное описание уступает. Это наш вопрос «двух писателей каста»
    (память `two-writers-of-cast`). **Не перенесено**, ждёт решения.
7.2 **Стиль в реф-вариации** (`build_ref_variation_sheet_prompt(style=hero_style_content[:300])`):
    наш комментарий у этой строки утверждает обратное (длинный style
    заставляет модель игнорировать reference). **Не перенесено.**
7.3 Пересъём `vibecode_models_snapshot.json` с живого `/v1/models` — после
    него можно добавить Astra / Gemini 3.7–3.8 / DeepSeek / Fable 5.1.
7.4 `lock_ui_field(p, "general_plan")` в `patch_project` — возможно наш
    собственный пропуск (для `script_text` лок ставится). Проверить.
7.5 Legacy у нас к удалению: `recover_from_disk.py`, `recover_project_state.py`,
    `scripts/_ensure_env_housepc.py` — проверить вызовы.

## 4. Тест-долг

Блоки идут с тестами (свои + адаптированные из их
`test_audit_fixes_v2.py`, `test_hero_multicharacter_and_report.py`,
`test_img_pr_parallel_and_split_limits.py`, `test_generate_items.py`,
`test_gpt_workspace.py`, `test_audio_sfx_agents.py`, `test_dual_db_healing.py`).
Файлы, где diff-cov всё же не дотянет, заводятся в `docs/DEBT.md` строкой
с этой спекой — список только уменьшается.
