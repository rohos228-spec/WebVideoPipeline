# Порт housepc@78d74100 — разблокировка кликов монтажа + salvage apply-ops

Источник: `rohos228-spec/video-pipeline@78d74100`
«fix(studio): unlock montage clicks after apply; salvage truncated apply-ops JSON».

## Что портируем (5 файлов + 2 теста)

1. `app/services/db_apply.py` — `close_truncated_json()` + хук в
   `extract_apply_ops_json`: оборванный JSON закрывается локально вместо
   падения «модель не вернула apply-ops».
2. `app/services/gpt_api.py` — 2 блока в `_chat_unscoped`: при контракте
   `apply_ops` спарсенный salvage пропускает `continue`-доборку.
3. `app/orchestrator/steps/enrich_xlsx.py` — хелпер
   `_complete_excel_gpt_noderun` + ранний вызов после записи apply-ops в БД;
   поздний дублирующий try-блок удалён.
4. `app/services/run_sync.py` — heal overflow slot 0 (`fw_*`):
   pending + completed_key → done без сброса meta.
5. `web/.../assemble-montage-board.tsx` — `restoreDocumentPointerEvents`,
   `DropdownMenu modal={false}`, `applyRunningRef`, idle → `handleApplyTerminal`.
6. Тесты: `test_extract_apply_ops_json_closes_truncated_nested_bits`,
   `test_sync_heals_overflow_pending_completed_to_done`.

## Не портируем

- `montage-frame-refs.tsx`, `montage-scene-cells.tsx` — файлов нет в вебе.
- `web/out/`, `STUDIO_VERSION` — артефакты/версия локальной машины.

## Риски

- Poll-эффект apply теперь завершает job на `idle` — если бэкенд отдаст
  `idle` при живом job, увидим ложный «Генерация завершена». Страховка:
  эффект живёт только пока `applyRunning=true`, а WS-guard требует
  `seenRunning || applyRunning`.
