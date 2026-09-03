# Tasks: merge-customer-fork

Порядок: A → B → C → D → E → F. B и C можно вести параллельно (разные
файлы), но смоук F только на обоих вместе — их фронт без их маршрутов
показывает мёртвые кнопки. Правило: PR на блок, гейт на PR, после блока
`git restore ai-pack/`. Черновик v0.1 — блоки G/H ждут ревью §3.3/§3.4.

## A. Подготовка

- [x] A.1 Ветка `merge/customer-fork` от `main`; remote `theirs` уже
      подключён, `theirs/main` = `17eff87d` (зафиксировать в PR).
- [x] A.2 Снимок «до»: `git merge-tree --merge-base=3bbf5c31 main theirs/main`
      → список конфликтов в PR как чеклист (97 файлов), чтобы видеть,
      что закрыто переносом, а что сознательно пропущено.
- [x] A.3 Решения владельца 2026-09-03: kie-create не переносим, UI за
      флагом `NEXT_PUBLIC_KIE_CREATE=0`; `/` остаётся.

## B. Фронт: их `web/src` как база

- [x] B.1 `git checkout theirs/main -- web/src web/public/favicon.svg
      web/public/icon.svg`; удалить их `web/src/app/page.tsx` (наш `/`
      остаётся), не брать `web/out`.
- [x] B.2 Вернуть наши ours-only файлы (33, список §1.1 п.2) — они не
      тронуты checkout'ом, проверить `git status`.
- [x] B.3 Наложить наши дельты на 9 общих файлов по таблице §1.1 п.3
      (`git diff e49b1aab main -- <f> | git apply -3`; где не ложится —
      руками). Отдельный коммит на `flow-canvas.tsx`: граф-слой
      (автосейв `project_graph`, предложение агента, диалог сброса,
      выделение без `nodes`-prop) обязателен, их `RunOverlay` — поверх.
- [x] B.4 `globals.css`: их правки (шрифты, `--ring`) перенести в
      `studio-theme.css` под `body:has([data-studio-scope])`; `/` не
      перекрашивается. Проверить скриншотом `/` до/после.
- [x] B.5 `lib/api.ts`: их клиент + наши граф-функции; `http()` — наш
      (identity-заголовки, `vp.token`). Их прямые `fetch` вне `api.ts`
      (`grep -n "fetch(" web/src`) перевести на `http()` или убедиться,
      что идут с credentials.
- [x] B.6 `node-model-catalog.ts`: kie-модели `online:false`; вкладка
      kie в «Генерации» и kie-ветки пикера — за флагом
      `NEXT_PUBLIC_KIE_CREATE` (дефолт выкл.); `kie-pricing.ts` и
      клиентские функции `kie*` в `api.ts` остаются мёртвым кодом.
- [x] B.7 `orchestrator-panel.tsx`: дефолт свёрнут (наш комментарий).
- [x] B.8 `app/pipeline/page.tsx`: сверить пропсы компонентов с их
      сигнатурами (Inspector, ProjectSidebar, StudioWorkspace,
      OutseeCreateWorkspace, GptWorkspace изменились); `?project=` /
      `?template=1` работают.
- [x] B.9 `tsc --noEmit` и `next build` зелёные; `web/STUDIO_VERSION`
      — взять их (504…) с пометкой источника.
- [x] B.10 Коммит с трейлером `Source: theirs/main 17eff87d`.

## C. Бэкенд под их фронт (§1.3, без kie)

- [x] C.1 meta-agent: `routers/meta_agent.py`, `services/meta_prompt_compiler.py`,
      `templates/`, правки `node_groups.py:302-330`,
      `prompt_library.py:146-153`; зарегистрировать в `api.py`;
      `tests/test_meta_prompt_compiler.py` — переписать `:89`, чтобы не
      создавал каталоги в `prompts/` (tmp_path).
- [x] C.2 shot-menu: `db_browser.py` (+142), `services/shot_menu.py`,
      `db_virtual_xlsx.py`; `vo_shot_expand.py` — по вердикту §3.4
      (до него: перенести, но не подключать к нодам);
      `tests/test_shot_menu.py`, `test_virtual_db_xlsx.py`.
- [x] C.3 create-queue: cancel/delete + sidecar cleanup + orphan sweep
      (9bee18c1) — сверить с нашим `create_jobs.py` (конфликт в
      merge-tree), взять их логику отмены, наш учёт.
- [x] C.4 outsee-create: `enhance-prompt` (LLM — наш транспорт,
      `text_llm` дефолт), `download` (конвертация форматов), `DELETE
      /history`, cancel/delete jobs в `outsee_http` роутере.
- [x] C.5 gpt-workspace `ask-stream`: SSE поверх нашего
      `anthropic_messages.py`; abort с фронта → отмена задачи.
- [x] C.6 `schemas.py` camelCase-валидатор + новые поля проекта;
      `project_ops.py` (`sheets/active_sheet/col_letters/truncated_rows`);
      `projects.py` (+33) — сверить с нашим RLS/tenant.
- [ ] C.7 (отложено — их TS-каталог взят как UI-каталог, kie offline) Тест «TS-каталог ⊆ Python-каталог по id» (§1.4) — новый,
      падает на расхождении.
- [x] C.8 api-surface тесты + `tests/test_apply_ops_batches.py`,
      `test_excel_gpt_overflow.py`, `test_vo_shot_expand.py` из их набора;
      `test_batch_optimization.py:22` — 6, не 8.

## D. Точечные фиксы провайдеров (§1.4)

- [x] D.1 `gpt_api.py`: `store: False` + `_responses_reasoning_block`
      (0be45ae2) поверх нашего salvage.
- [x] D.2 `outsee_http.py`: ожидание `concurrency_limit` (8b539721),
      `raise last_err` вынести из-под `while True`.
- [x] D.3 `settings.py`: `env_file_encoding="utf-8-sig"`,
      `env_ignore_empty=True`; `.env.env` НЕ читать.
- [ ] D.4 (не взято — TTS без CDP не подтверждён) ElevenLabs прямой TTS (052db0b1) — только по подтверждению,
      что TTS без CDP нужен; иначе пропустить.
- [x] D.5 Не тащить: `/credits` fingerprint, VPS-роутинг kie,
      `KIE_VIDEO_IDS`×11, `_WORKING_KEY`-кэш ключей.

## E. Гейт и CI

- [x] E.1 `ci.yml` «Никакого hex вне токенов»: храповик по файлам →
      исключение по каталогам студии (§1.2) с комментарием «дизайн
      заказчика»; для `app/`, `components/ui`, `editors/pipeline/chat`,
      `stage-*`, `project-*` — строго.
- [x] E.2 `.gitleaksignore`: отпечатки на `node-model-catalog.ts`
      (`kling-*` ложные) и `baza-workspace.tsx` (уже есть) — по факту
      прогона `gitleaks detect --log-opts=main..HEAD`.
- [x] E.3 `.gitignore`: убедиться, что `web/out` игнорируется, `.env.env`
      добавить (на будущее).
- [x] E.4 Hex-гейт, tsc, next build, `pytest` api-surface — зелёные в CI.

## F. Смоук и приёмка

- [x] F.1 Смоук на копии базы (`run_web.sh` + `smoke.mjs`, headless
      Chromium, без метки сессии): клик по «Тема ролика» → инспектор →
      сохранение; мастер «Новый проект»; меню съёмки открывается и
      правит ячейку; meta-agent компилирует; «Генерация» показывает
      enhancer/batch/negative; чат стримит и останавливается; отмена
      джобы; переходы проект ↔ шаблон; консоль без ошибок.
- [x] F.2 Скриншоты `/` и `/pipeline` до/после — `/` не изменился.
- [x] F.3 Обновить `docs/PIPELINE-UI-MAP.md` (акт четвёртый: студия
      заказчика 2026-09) и память `studio-shell-restored`.
- [ ] F.4 (после блоков G/H) Release через `main` → GHCR → VPS; проверка `rev` в контейнере
      и наличия новых чанков; заказчику — `/pipeline?project=N` с
      Ctrl+Shift+R.

## G. Оркестрация Kir0029 — закрыто (§3.3)

- [x] G.1 Поля, которые читает их фронт: `node_runs[].started_at/finished_at`
      — уже в нашем `NodeRunDTO`; `types.ts` у них не менялся.
- [x] G.2 Решение: реестр статусов не переносим, контракт совпадает.

## H. Цепочка промтов — закрыто (§3.4)

- [x] H.1 `prompts/05_excel_gpt/sd_*.md` остаются наши; их промты цепочки
      лежат в `templates/` и никем не читаются, пока группа скрыта.
- [x] H.2 Их цепочку не переносим; группа «Сценарий → промпты кадров + QC»
      скрыта в `node_groups.py` с комментарием. Отдельный change после
      решения заказчика, чья цепочка основная.

## I. Тест-долг (§8) — открыто

- [ ] I.1 `outsee_http.py`: тесты на ожидание concurrency_limit и Veo-кадры (start+end, только end).
- [ ] I.2 `gpt_api.py`: on_delta в обоих SSE-циклах (дельта доходит, ошибка колбэка не роняет разбор).
- [ ] I.3 `shot_menu.py` / `vo_shot_expand.py`: ячейка → шоты, правка поля, fallback на project.xlsx.
- [ ] I.4 остальные 21 файл по мере касания; каждый закрытый — минус строка в `--exclude`.
