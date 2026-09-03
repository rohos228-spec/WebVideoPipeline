# Change: merge-customer-fork

**Дата:** 2026-09-03 (v0.2: блоки A–F закрыты на ветке `merge/customer-fork`, G/H — решение в §3.3/§3.4)
**Основание:** задание владельца 2026-09-03 «помержить в нашу версию то,
что накодили сверх старой версии» + уточнение «фронт нам точно полностью
нужен из их версии». Источник: `https://github.com/rohos228-spec/video-pipeline`
(remote `theirs`). Ревью качества их диапазона — четыре отчёта
2026-09-03 (фронт/гигиена, провайдеры/безопасность, оркестрация, цепочка
промтов), выжимки в §3.

## Why

Две команды две недели писали поверх одного снимка. Наш корневой коммит
`e49b1aab` (2026-08-20) — байт-в-байт дерево их коммита `3bbf5c31`;
общих коммитов в истории **ноль**. С тех пор: у них 148 non-merge коммитов
(439 файлов, +26.6k/−19.0k), у нас 204. Сухой трёхсторонний прогон с явной
базой (`git merge-tree --merge-base=3bbf5c31 main theirs/main`) — 97
конфликтующих файлов, из них ~70 содержательных (32 в `app/services`, 6 в
оркестраторе, 7 во фронте, 15 в `tests/`).

Заказчик живёт в своей ветке: его студия (UI Kir0029) — то, что он видит
каждый день, и то, с чем он сравнивает наш прод. Владелец решил: **фронт
берётся из их версии целиком**, бэкенд — выборочно. Голый `git merge`
даёт кентавра: у них дефолтный текстовый LLM — GPT 5.6 через kie и
VPS-релей, у нас kie похоронен, Opus 5 через vibecode `/v1/messages`.

## Что НЕ берём (решено ревью, не обсуждается в тасках)

- `web/out/` в git (у них 40 файлов на HEAD, 39.6 МБ блобов за две недели,
  уже рассинхрон с `web/src`). У нас `out` собирается в CI/образе.
- `.env.example` их версии: на HEAD живой `VIBECODE_API_KEY=vk-9Ea…`
  (строка 90). Ключ ротировать — это сообщение заказчику, не наш таск.
- `app/settings.py::_settings_env_files` с чтением `.env.env`
  (файл не в их `.gitignore`).
- `GET /api/kie-create/credits` с отпечатком ключа (`key[:4]…key[-4:]`,
  `key_source`, `base_url`) — у них API без auth и CORS `*`; у нас
  IdentityMiddleware, но отпечаток ключа в ответе не нужен никому.
- Принудительный VPS-роутинг kie (`kie_kling.py::kie_api_base_url`
  перебивает явный `KIE_API_BASE_URL`, если задан `GPT_RELAY_TOKEN`).
- `ARCHITECTURE_AUDIT.md` (273 строки, устарел в день написания),
  `BACKLOG.md`, 33 Windows-лаунчера `.cmd/.ps1`, `markdown-renderer.tsx`
  (338 строк самописного regex-парсера — см. §2.4, берём вместе с чатом
  как есть, но помечаем кандидатом на замену).
- Их каталоги как источник истины (§3.2).

## 1. Ключевые решения дизайна

### 1.1 Фронт: их `web/src` — база, наши дельты — поверх

Не «мерж двух фронтов», а **замена с обратным наложением**. Порядок:

1. `web/src` берётся из `theirs/main` целиком (55 изменённых + 8 новых
   файлов относительно базы).
2. Наши файлы, которых у них нет (33), остаются: упрощённый режим `/`
   (`stage-*`, `editors/*`, `project-view`, `idea-composer`,
   `stage-api/stage-types`), auth (`auth-gate`, `login-gate`,
   `use-identity`, `identity-api`), `app/pipeline/page.tsx`,
   `studio-theme.css`, `graph-reset-dialog`, `graph-edit`, `chat/*`.
3. Наши дельты в 9 общих файлах накладываются заново — ручная работа,
   список закрытый:

   | Файл | Наша дельта (vs база) | Что делать |
   |---|---|---|
   | `app/layout.tsx` | providers, identity, studio-theme импорт | взять наш, добавить их `icon.svg`/метаданные |
   | `app/globals.css` | лист `/` на токенах (+188/−338) | наш; их правки (шрифты Inter/JetBrains, `--ring` 188 86% 53%) уходят в `studio-theme.css` под скоуп `body:has([data-studio-scope])` |
   | `canvas/flow-canvas.tsx` | автосейв через `project_graph`, предложение агента, диалог сброса, выделение не через `nodes` prop (+398) | конфликт с их −244/+78 в `RunOverlay` и 439-строчным диффом; сливать руками, наш граф-слой обязателен |
   | `canvas/pipeline-node.tsx` | +10 (цена шага) | их + наши 10 строк |
   | `shell/topbar.tsx` | UiContext, наш auth/баланс-бейдж (146) | наш скелет + их 30 строк (LLM sync пикер, таймеры) |
   | `studio/studio-workspace.tsx` | +10 | их + наши 10 |
   | `orchestrator/orchestrator-panel.tsx` | дефолт свёрнут (иначе панель закрывает холст) | их + наш дефолт |
   | `lib/api.ts` | `getProjectGraph/saveProjectGraph/projectGraphDiff/applyGraphProposal/discardGraphProposal/resetProjectGraph`, identity-заголовки | их (+408: shot-menu, meta-agent, kie, enhance, ask-stream, cancel) + наши функции; `http()` — наш (identity) |
   | `lib/node-model-catalog.ts` | kie `online:false` (баланс −2.15) | см. §1.4 |

4. Маршрутизация: у них один роут `/` = вся студия. У нас `/` —
   упрощённый режим, `/pipeline` — студия. **Сохраняем наше**: их
   `app/page.tsx` не берём; студия монтируется через наш
   `app/pipeline/page.tsx` (оболочка, `?project=N`, `?template=1`,
   `data-studio-scope`), который уже оборачивает те же компоненты.
   Открытый вопрос владельцу — нужен ли ещё `/` (§7.3).

### 1.2 Дизайн-гейт: скоуп заказчика освобождается целиком

Их редизайн — 183 hex-литерала (95 в `outsee-create-workspace.tsx`, 53 в
`gpt-workspace.tsx`, 23 в `node-model-picker.tsx`), 72 `backdrop-blur`,
Inter. По нашим правилам (`web/Design.md`, `~/.agents/rules/design-with-agents.md`)
это антипаттерны, по решению владельца — дизайн заказчика. Гейт
«никакого hex вне токенов» в `ci.yml` меняет форму: вместо храповика по
файлам — **исключение по каталогам студии** (`components/{canvas,gpt,
outsee,inspector,sidebar,studio,shell,hitl,frames,baza,fleet,costs,logs,
prompts,orchestrator}`, `lib/*-catalog.ts`, `lib/kie-pricing.ts`) с явным
комментарием «дизайн заказчика, литералы его». Гейт остаётся строгим для
`app/`, `components/ui`, `components/{editors,pipeline,chat}`,
`stage-*`, `project-*` — наших файлов.

### 1.3 Бэкенд под их фронт: 20 маршрутов, которых у нас нет

Их фронт зовёт (сверка таблиц `@router.*` обеих сторон):

| Группа | Маршруты | Что тянет | Решение |
|---|---|---|---|
| meta-agent | `POST /meta-agent/compile`, `/save-and-activate` | `routers/meta_agent.py` (101), `services/meta_prompt_compiler.py` (165), `templates/` (5 промтов, 834 строки), `node_groups.py:302-330`, `prompt_library.py:146-153` | **берём** |
| shot-menu | `GET/POST/PATCH /db/projects/{id}/shot-menu[/cell|/shot-field]` | `db_browser.py` (+142), `services/shot_menu.py` (727), `vo_shot_expand.py` (474), `db_virtual_xlsx.py` (195) | **берём**; `vo_shot_expand` — после ревью цепочки (§7.1) |
| create-queue | `POST /create/jobs/{id}/cancel`, `DELETE /create/jobs/{id}` | `create_queue.py` (+12), sidecar cleanup, orphan sweep (9bee18c1) | **берём** |
| outsee-create | `POST /enhance-prompt`, `GET /download`, `DELETE /history`, `POST /outsee/jobs/{id}/cancel`, `DELETE /outsee/jobs/{id}` | `outsee_create.py` (+307), `outsee_http.py` роутер (+21) | **берём**; enhancer — через наш текстовый LLM (§1.4) |
| gpt-workspace | `POST /sessions/{id}/ask-stream` | `gpt_workspace.py` (+29), стриминг в `services/gpt_workspace.py` | **берём**, транспорт — наш (`anthropic_messages.py`/vibecode), не kie |
| kie-create | `GET /catalog`, `/credits`, `/jobs/{id}`, `POST /estimate`, `/generate`, `/upload` | `routers/kie_create.py` (171), `services/kie_catalog.py` (1784), `kie_kling.py` правки, `lib/kie-pricing.ts` | **не берём** (§7.2); UI за флагом |
| projects/schemas | camelCase→snake `model_validator` в `schemas.py`, `topic/aspect_ratio/...` поля, `sheets/active_sheet/col_letters` в `project_ops.py` | +33/+39/+24 | **берём** (фронт шлёт camelCase) |

Плюс изменения формы ответов существующих маршрутов, которые читает их
фронт (реестр статусов, таймеры `started_at/finished_at`, бейджи
результатов/ошибок, `node_runs`) — состав уточняется ревью оркестрации
(§7.1). До него: считать, что **их фронт без их реестра статусов покажет
ноды без таймеров и с неверными бейджами**, и это блокер приёмки.

### 1.4 Провайдеры: их фронт, наш транспорт

- Текстовый LLM: дефолт остаётся `claude-opus-5-vibecode` через
  `/v1/messages`. Их `text_llm_catalog.py` (переписан с обеих сторон, 18
  hunk'ов у каждой) **не мержится**; их UI-пикер (`text-llm-picker`,
  «global LLM sync») читает наш каталог. Их `tests/test_vibecode_catalog.py`
  (гоняет `claude-sonnet-5` через `/v1/chat/completions`) не берём.
- Каталоги медиа-моделей: у них источник истины раздвоен вручную
  (`vibecode_catalog.py` ≡ `node-model-catalog.ts` построчно, цена
  `nano-banana-2` в трёх местах даёт 0.8 / «3» / $0.118). Берём их **TS**
  как UI-каталог (фронт целиком), Python-каталог — наш; расхождения
  цен закрываем тестом «TS-каталог ⊆ Python-каталог по id» (новый).
- Точечные фиксы из их диапазона, независимые от каталогов — берём:
  `store:false` + `reasoning.effort` для Responses (0be45ae2), ожидание
  `concurrency_limit` у Outsee (8b539721; недостижимый `raise` после
  `while True` — поправить), прямой ElevenLabs TTS (052db0b1, только если
  TTS без CDP нужен — открыто), `env_file_encoding="utf-8-sig"` +
  `env_ignore_empty=True`.
- Известный баг у них, не тащим: `outsee_http.py:1244-1253` теряет
  **только-конечный** кадр Veo (`if frame and last / elif frame`).

### 1.5 Механика переноса

Истории не связаны; `git merge` без графта невозможен, с графтом
(`git replace --graft e49b1aab 3bbf5c31`) — возможен, но даёт 97
конфликтов и тянет всё, что в §0 «не берём». Поэтому:

- **Фронт** — `git checkout theirs/main -- web/src` + удаление их
  `app/page.tsx` + наложение наших дельт (§1.1) патчами
  `git diff e49b1aab main -- <file> | git apply -3`.
- **Бэкенд** — черри-пик по группам §1.3 с `git cherry-pick -x` там, где
  коммит атомарный, и `git checkout theirs/main -- <file>` + ручная
  сверка там, где коммиты Kir0029 «stage-N» (по 10–20 файлов, смешаны
  фронт и бэк).
- Один PR на группу, каждая группа проходит гейт (`.claude/verify.json`,
  CI Фронт: tsc + hex + next build; api-surface тесты) и смоук на копии
  базы (рецепт — память `studio-shell-restored`, headless Chromium без
  метки сессии).
- Итоговый коммит фронта помечает происхождение: `Source: theirs/main
  17eff87d`, чтобы следующий перенос считался от него.

## 2. Что ценно функционально в их фронте (для приёмки)

2.1 Меню съёмки (`shot-menu-board/panel`, `lib/shot-menu.ts`) — доска
кадров с лентой закадра, свои строки, SET/стык из `camera.json`.
2.2 Meta-agent compiler (`meta-prompt-dialog.tsx`) — сборка
мастер-промта ноды из intent + правил, «сохранить и активировать».
2.3 «Генерация» (`outsee-create-workspace.tsx`, 1656 → 3243 строк):
enhancer промта, 50 «шедевров», батч 1×/2×/4×, negative prompt, до 8
референсов, пресеты стиля, lightbox, конвертация PNG/JPG/WEBP при
скачивании, удаление упавших джоб, отмена.
2.4 Чат (`gpt-workspace.tsx`, 1621 строк диффа): стриминг с abort,
действия над сообщениями, поиск, код-блоки, `markdown-renderer.tsx`.
2.5 Канвас: `items-config-panel`, просмотр результатов ноды
(`node-result-badge`, Stage 8), таймеры исполнения, бейдж «−» у пустых
завершённых шагов, LLM sync по всем нодам, шаг сетки 380px.
2.6 Мастер проекта (`new-project-wizard`, +148): автозаполнение темы.
2.7 Виртуальные листы БД в Studio (`xlsx-sheets.ts`, `db_virtual_xlsx.py`).

## 3. Выжимка ревью (2026-09-03)

### 3.1 Фронт/гигиена
- Пересечение с нами — 9 несущих файлов (§1.1); их редизайн глобальный,
  скоупа `data-studio-scope` у них нет.
- Тесты: ~19 из 24 новых файлов честные (сеть замокана, SQLite in-memory);
  образец — `test_apply_ops_batches.py:366-412`. Пустышки:
  `test_batch_optimization.py:19-22` (уже красный: ждёт 8, поставили 6),
  `test_scene_design_concurrency_lock.py:27-30`, `test_bug_report_directory.py:8-12`.
  `test_kie_create_api.py:30` требует `KIE_API_KEY`;
  `test_meta_prompt_compiler.py:89` пишет в `prompts/`.
- Чистка legacy 7ce99778 — добротно (−3162 строки, убит скрытый
  `git reset --hard` в `STUDIO.cmd`); переносим сами, не мержем.

### 3.2 Провайдеры/безопасность
- См. §0 (ключ в `.env.example`, `/credits`, `.env.env`, VPS-роутинг).
- Добротно: `store:false`/`reasoning`, `concurrency_limit`, прямой
  ElevenLabs TTS, лаунчер перестал писать `.env`, `_usable_kie_key()`
  отсекает `vk-*` от kie.
- Сомнительно: `kie_post_json` кэширует «рабочий» ключ в модульных
  глобалах `_WORKING_KEY` (гонка воркеров); `configured: True` захардкожен
  в `kie_create.py:32`.
- `media_route.py:29-41` расширил `KIE_VIDEO_IDS` до 11 моделей — против
  нашего «kie мёртв»; при §7.2 = «нет» не берём.

### 3.3 Оркестрация (Kir0029, этапы 1–15) — закрыто 2026-09-03 без ревью

Два ревью-агента за четыре часа не продвинулись (перегрузка API), убиты;
вердикт — по диффам. **Контракт API их фронт не менял:** `web/src/lib/
types.ts` в их диапазоне не тронут, `NodeRunDTO` у нас уже отдаёт
`started_at`/`finished_at`, таймеры и бейджи их канвас считает из наших
полей (`flow-canvas.tsx:384-418`). Блокера «ноды без таймеров» нет.
Их бэкенд-правки оркестрации — внутренние и мелкие поверх файлов, которые
мы переписали в разы сильнее (`project_state.py` +31 у них / +98 у нас,
`main.py` +8/−24 против +219/−196, `telegram/menu.py`, `worker.py`,
`generate_hero.py` +7 против +367): резолвер sfx-статусов, флаг
`hero_skipped_empty`, маркеры ошибок в `step_failure_policy`, `sd_skel`
вместо `sd_style` в `reset_step`. Всё это привязано к их цепочке промтов
(§3.4) и к их статусам; у нас после hardening и живых прогонов свои.
**Не переносим.** Что взято раньше: режим ▶ full/resume, отмена джоб,
`commit_with_retry`, UI-ноды в реестре и планировщике.

### 3.4 Цепочка текстовых нод и промтов — закрыто 2026-09-03 без ревью

Их цепочка разошлась с нашей концептуально, не текстуально: `sd_skel`
(скелет) вместо `sd_style`, «сценарист → main_action → shots coverage → QC»
вместо `script_frames_qc`, батчи по 6 кадров, промты вынесены из git в
`templates/`. Объём: `enrich_xlsx.py` +957/−119, `apply_ops_batches.py`
+936/−41, `camera_expand.py` +195, `montage_ai_change.py` +144 — на файлах,
которые у нас тоже переписаны (`runner.py` +459, `generate_images.py` +413).
У нас `sd_style`/`sd_action_vlog`/`prompts/05_excel_gpt/sd_*.md` живые,
два полных ролика собраны 2026-09-02/03 на нашей цепочке. Слияние двух
пайплайнов — не merge, а выбор одного из них по живому прогону; это
отдельный change с решением заказчика «чья цепочка основная».
**Не переносим.** Следствие для фронта: группа палитры «Сценарий →
промпты кадров + QC» (`_script_frames_qc_group`) скрыта из каталога —
её ноды исполняются их `enrich_xlsx`/`fw_frames`; промты группы лежат в
`templates/`, код цел, включается одной строкой. `prompts/` не тронуты.

## 4. Границы

- Наш упрощённый режим `/` и граф-слой (`project_graph`, стадии поверх
  графа, предложение агента) не тронуты.
- Наш auth/identity, RLS, гейт, CI/CD (GHCR → VPS) — как есть.
- Их Windows-лаунчеры, `web/out`, доки-рапорты — не переносятся.
- Промты (`prompts/`): они удалили `05_excel_gpt/sd_*.md`, которые у нас
  живы и используются (`sd_action_vlog` наш). Решение — после §3.4; до
  него `prompts/` не трогаем.

## 5. Риски

- **Форма ответов** (§1.3, последний абзац): их фронт может читать поля,
  которые пишет только их реестр статусов. Обнаружится на смоуке, не на
  tsc. Митигация: смоук на копии базы по списку §2 до PR.
- **`flow-canvas.tsx`**: два 400-строчных диффа в одном файле; наш
  граф-слой — обязательный, их `RunOverlay` — желательный. Резерв: день.
- **kie**: половина «Генерации» и весь `kie-pricing.ts` завязаны на kie.
  При §7.2 = «нет» вкладка kie в UI должна быть спрятана флагом, не
  вырезана (иначе следующий перенос от них снова конфликтует).
- **Размер**: `outsee-create-workspace.tsx` 3243 строки, 46 сырых
  `<button>`, 0 `<Button>`. Не рефакторим в этом change — иначе следующий
  перенос невозможен. Фиксируем как долг.

## 6. Оценка

| Блок | Объём |
|---|---|
| Фронт: замена + 9 дельт + гейт + смоук | 2 дня (из них `flow-canvas` — до 1) |
| Бэкенд §1.3 без kie: meta-agent, shot-menu, cancel/delete, enhance, ask-stream, схемы | 1–1.5 дня |
| Форма ответов (реестр статусов/таймеры) — после §3.3 | 0.5–2 дня, зависит от вердикта |
| Точечные фиксы провайдеров §1.4 | 0.5 дня |

## 7. Открытые вопросы

7.1 (ревью) Оркестрация Kir0029 и цепочка промтов: брать реестр статусов
    целиком или только контракт ответа под фронт; что делать с
    `vo_shot_expand`/`scene_design` при наших жанровых валидаторах.
7.2 РЕШЕНО 2026-09-03 (владелец): kie-create бэкенд **не переносим**.
    Вкладка kie в UI прячется флагом `NEXT_PUBLIC_KIE_CREATE=0`
    (дефолт), файлы `kie-pricing.ts` и kie-ветки каталога остаются в
    коде — иначе следующий перенос от заказчика снова конфликтует.
7.3 РЕШЕНО 2026-09-03 (владелец): упрощённый режим `/` **остаётся**,
    студия — на `/pipeline` через нашу оболочку.
7.4 (владелец → заказчик) Ротация `VIBECODE_API_KEY` из их `.env.example`
    и добавление `.env.env` в их `.gitignore`. Вне нашего репо.

## 8. Тест-долг (решение владельца 2026-09-04)

Перенесённый код заказчика уехал на прод без тестов на изменённые строки.
diff-cov исключает файлы ниже (`.claude/verify.json`, `_test_debt`),
планка cov-ratchet обновлена. Список обязан только уменьшаться: тест на
файл написан — файл из `--exclude` убран.

| Файл | Непокрытых изменённых строк | Что там |
|---|---|---|
| `app/web/routers/outsee_create.py` | 121 | enhancer, скачивание с конвертацией, история |
| `app/services/vo_shot_expand.py` | 100 | нарезка закадра по шотам (меню съёмки) |
| `app/services/node_groups.py` | 92 | скрытая группа script_frames_qc |
| `app/bots/outsee_http.py` | 78 | concurrency-ожидание, Veo-кадры, NBP-бан |
| `app/services/gpt_workspace.py` | 65 | ask_stream |
| `app/services/shot_menu.py` | 63 | меню съёмки |
| `app/services/create_jobs.py` | 47 | cancel_job, уборка сайдкара |
| `app/services/generation_storage.py` | 40 | таймеры, sweep сирот |
| `app/services/gpt_api.py` | 34 | on_delta в SSE-циклах |
| `app/web/routers/db_browser.py` | 19 | маршруты shot-menu |
| `app/web/routers/project_ops.py` | 15 | виртуальные листы |
| `app/web/routers/meta_agent.py` | 14 | compile / save-and-activate |
| `app/db.py` | 13 | commit_with_retry |
| `app/services/plan_shot2.py` | 12 | реф shot_02 от родителя |
| `app/web/routers/gpt_workspace.py` | 11 | ask-stream endpoint |
| `app/services/step_registry.py` | 10 | реестр шагов |
| `app/services/db_virtual_xlsx.py` | 10 | виртуальные листы |
| `app/web/routers/projects.py` | 9 | camelCase, mode=resume |
| `app/web/routers/outsee_http.py` | 7 | cancel |
| `app/services/prompt_library.py` | 7 | шаблоны excel_gpt |
| `app/orchestrator/graph/planner.py` | 7 | UI-ноды как side sink |
| `app/web/schemas.py` | 6 | camelCase-валидатор |
| `app/services/step_replace.py` | 6 | замена шага |
| `app/web/routers/create_queue.py` | 3 | cancel |
| `app/services/meta_prompt_compiler.py` | 3 | компилятор |

Приоритет гашения: `outsee_http.py` и `gpt_api.py` (деньги и сеть), затем
`shot_menu`/`vo_shot_expand` (данные заказчика), остальное — по мере
касания. Регламент — `docs/RELEASE-PROCESS.md` §6.
