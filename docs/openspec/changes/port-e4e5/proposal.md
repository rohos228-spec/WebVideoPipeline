# Change: port-e4e5

**Дата:** 2026-09-19  
**Ветка:** `feat/port-e4e5`  
**Образец:** Коммиты батчей E4 (6 коммитов) + E5 (2 коммита) из SOURCE `origin/housepc` (`https://github.com/rohos228-spec/video-pipeline`):
- `0d7f1ff3`: shot templates, partition & expansion
- `b69c2896`: character sheet refs & hero prompt integration
- `b62258fa`: montage force_clear empty queue on project change & close
- `48b586bf`: montage child→parent removes parent still from refs & generation
- `a1d12811`: montage child→parent writes role immediately, chips below preview
- `ee0c827b`: montage parent chip, parent-first apply, keep edits and viewport
- `0b88a643`: montage AI change uses frame action instead of scene chain
- `1fe8724b`: transport fixes (Outsee & GPT), quarantine disk media, montage delete/merge coverage

## 1. Why

В ветке `origin/housepc` была доработана и стабилизирована монтажная доска (Studio), включая:
- Покрытие кадров (coverage): родительские и дочерние кадры, вставка, удаление, объединение сцен.
- Защиту от рассинхрона таймлайна и оживления удалённых кадров с диска.
- Надежность сетевого транспорта Outsee HTTP (таймауты 60s/180s, curl fallback для POST, пропуск Windows CRL для Yandex).
- Устойчивость GPT API к пустым стримам SSE (nostream fallback salvage, парсинг `{error: ...}` JSON envelopes).
- Сохранение положения вьюпорта и правок оператора при перегенерации.

В целевом репозитории `video-pipeline-web` весь этот функционал отсутствовал или был частично устаревшим, что приводило к сбоям регенерации и потере правок.

## 2. Архитектурные адаптации под TARGET (Single-DB Postgres)

1. **Single-DB архитектура:** Все вызовы `project_db` и SQLite-fallback полностью устранены. Новые эндпоинты в `app/web/routers/project_ops.py` (`montage_board_insert_frame`, `montage_board_delete_frame`, `montage_board_merge_scenes`, `montage_board_set_voiceover`) используют исключительно `Depends(get_session)` и `commit_with_retry(session)` в Postgres.
2. **Сортировка по sort_key:** В сервисе монтажной доски `montage_board.py` выборка кадров упорядочена по `(Frame.sort_key.asc(), Frame.number.asc())`, сохраняя дробные/вставленные кадры на своих позициях в таймлайне.
3. **Разделение провайдеров LLM:** Сохранена маршрутизация Claude через `_anthropic_route` (Anthropic SDK), а механизм `_chat_completions_nostream` используется для стандартных OpenAI-совместимых эндпоинтов.

## 3. Что сделано

### Backend:
- `app/bots/outsee_http.py`: таймауты 60/180с, `--ssl-no-revoke`, curl fallback для POST, Yandex download preference.
- `app/services/outsee_retry.py`: защита identity-блока при обрезке, fallback на Kling, таймауты без переписывания промта при сетевых ошибках.
- `app/services/gpt_api.py`: `_openai_error_retryable`, envelope-парсинг, salvage пустых SSE-стримов через `_chat_completions_nostream`.
- `app/services/montage_board_frames.py`: `insert_montage_frame`, `delete_montage_frame`, `merge_montage_scenes`, `set_montage_voiceover`.
- `app/services/montage_coverage_ops.py`: операции покрытия (кадры, план, действие, ракурс, ститч, цепь сцен).
- `app/services/image_ref_lock.py`: классификация и привязка рефов, обрезка и блокировка айдентики.
- `app/services/shot_templates.py`: шаблоны шотов T0..T10, нарезка закадра `kadry_vo_partition_aligned`.
- `app/services/vo_shot_expand.py`: расширение шотов, родительский still-лок.
- `app/services/apply_ops_batches.py`: пропуск промтов и действий (`SKIP_PROMPTS_AND_ACTION`).
- `app/services/ensure_frames_from_disk.py`: карантин удалённых кадров (`.quarantine`).
- `app/web/routers/project_ops.py`: роуты монтажных операций над кадрами и сценами.

### Frontend:
- `web/src/lib/types.ts`: DTO и типы для Montage (`MontageManualRef`, `MontageRefAsset`, `MontageAnchorRow`, coverage-поля в `MontageBoardFrame`, `MontageTemplateChoice`).
- `web/src/lib/api.ts`: API методы `mergeMontageScenes`, `insertMontageFrame`, `setMontageVoiceover`, `deleteMontageFrame`.
- `web/src/components/canvas/assemble-montage-board.tsx`: отображение строк покрытия, чипы родителя, сохранение вьюпорта и правок.
- `web/src/components/canvas/flow-canvas.tsx`: поддержка сохранения вьюпорта.
- `web/src/lib/pipeline-viewport.ts`, `web/src/lib/montage-ai-change-memory.ts`: утилиты памяти правок и вьюпорта.

## 4. Верификация

- **Frontend:** `pnpm run typecheck` (tsc) — 0 ошибок.
- **Linters & Style:** `ruff check app/ tests/` — 0 ошибок.
- **Typecheck:** `mypy app/` — 0 ошибок в 382 файлах.
- **Policy:** `python scripts/policy_check.py` — PASSED.
- **Tests:** `pytest` на всех связанных тестах (`test_montage_*.py`, `test_outsee_*.py`, `test_gpt_api.py`, `test_shot_templates.py`, `test_image_ref_lock.py`, `test_db_frames_*.py`, `test_ensure_frames_*.py`) — **405/405 passed**.
