# Tasks: merge-customer-fork-2

Порядок: A → B → C(4 блока параллельно в worktree) → D → E.

## A. Подготовка

- [x] A.1 Ветка `merge/customer-fork-2` от `main`; `theirs/main` = `c53a238b`.
- [x] A.2 Четыре разбора диапазона `17eff87d..theirs/main` (фронт, медиа,
      звук/сцены/чат, Roadmap V2) — выжимки в scratchpad сессии, решения в
      proposal §0/§1.

## B. Фронт

- [x] B.1 21 файл целиком, 3 файла 3-way, `node-model-catalog.ts` вручную
      (kling-3-0 `online:false`).
- [x] B.2 tsc, next build, hex-гейт зелёные; `STUDIO_VERSION` 515.
- [x] B.3 Коммит `e9a71186` с трейлером `Source: theirs/main c53a238b`.

## C. Бэкенд (по блокам, каждый — свой коммит и гейт)

- [x] C.1 audio (`08a2b64e`): sfx_gen / sfx_plan / sfx_mix / assemble (variant2) /
      montage/variant2 / node_registry / auto_advance / generate_music /
      gpt_text_builder; тесты `test_customer_fork_2_audio.py`.
- [x] C.2 core (`d59c3867`): db_apply / gen_queue / project_state / `_IMG_EXTENSIONS` ×7 /
      assembly + frame_audio таймауты / commit_with_retry в роутерах /
      get_running_loop / снос tokenrouter; тесты `test_customer_fork_2_roadmap.py`.
- [x] C.3 media (`8b2dcd41`): frames.py `regenerate-video` / generate_hero / generate_items /
      generate_images / outsee.py / outsee_http.py / main.py /
      vibecode_catalog / xlsx_step_runners (clamp, hint, streams) /
      monitor/report; тесты `test_customer_fork_2_media.py`.
- [x] C.4 llm (`4cff0ffc`): gpt_workspace / run_sync + settings_default / gpt_api [DONE] /
      anthropic_messages on_delta / xlsx_step_runners парсеры /
      excel_gpt_node + gpt_operator лейбл / prompt_library fallback /
      text_llm_catalog group+модели / vibecode_catalog алиасы; тесты.

## D. Слияние и гейт

- [x] D.1 Четыре блока в `merge/customer-fork-2` через `cherry-pick -x`.
      Конфликт оказался один — `tests/test_text_llm_provider.py` (оба блока
      чистили tokenrouter); разрешён в пользу блока llm, его тесты каталога
      сохранены.
- [x] D.2 ruff, ruff-format, mypy — 0; тесты блоков — 213 passed, 3 skipped.
      Полная суита с храповиком — прогон идёт.
- [ ] D.3 Смоук на копии базы: `scripts/smoke_studio.sh` + вручную:
      SFX-нода с результатом не пустая (`meta.sfx_generated`/`sfx_ready`),
      «Перегенерировать клип», пикер LLM с группами, мастер проекта.
- [ ] D.4 `docs/DEBT.md` — строки на файлы, где diff-cov не дотянул.

## E. Закрытие

- [ ] E.1 Память сессии: `customer-fork-merge` обновить (Source c53a238b,
      что не взято, открытые вопросы 7.1–7.5).
- [ ] E.2 Владельцу: вопросы §3 proposal.
