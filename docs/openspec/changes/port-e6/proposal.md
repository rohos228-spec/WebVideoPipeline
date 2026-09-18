# Батч-порт E6 — цепочка запуска студии (net-эффект 5 коммитов housepc)

Источники: `94a20b7b`, `21765ef9`, `d53588ba`, `717253cd`
(`b0eb7106` пропущен целиком — gen_assistant/style_analyzer в вебе нет).

## Портировано

1. `app/main.py` — воркер берёт шаг, который уже `running`, вместо
   `continue` по гейтам gen_queue/until_node.
2. `app/services/node_groups.py` — `canvas_has_script_frames_qc`,
   `script_frames_qc_needs_upgrade`, `_forget_dropped_excel_gpt_key`
   (drop_*-функций в вебе нет — вызовы forget некуда ставить, оставлена впрок).
3. `app/web/routers/projects.py` — GET-guard (upgrade только если нужен) +
   `run_project_step`: дефолт `mode=resume`, wipe только при явном `force_wipe`.
4. `app/services/project_steps.py` — ручной ▶ включает `auto_mode`
   (цепочка нод). Дефолт wipe для `img_pr` сохранён (веб-специфика).
5. Фронт (`node-studio`, `studio-workspace`, `api.ts`) — дефолт `resume`,
   тосты «Запущен» вместо «начисто».
6. `app/services/run_sync.py` — `_reconcile_stale_node_runs`: сброс
   `enriching_*` неактивного ключа в `pending`.
7. `app/services/gpt_api.py` — `CancelledError`/`KeyboardInterrupt`
   больше не глотаются в `stream_err` (отмена не уходит в ретрай).
8. `scene_design/agents.py` + `runner.py` — `"style": "style_arc"` и
   tolerant `load_checkpoint` (input_hash-логика веба не тронута).
9. Тесты: `test_start_step_ui_play…`, `test_load_checkpoint_legacy_style…`,
   `test_stream_cancelled_error_is_not_retryable`,
   `test_script_frames_qc_needs_upgrade_flags_stale_group` (адаптирован, без drop).

## Пропущено

- Весь dual-DB (`project_db`, `GET /run`, реплики, `allow_replica_status_write`,
  тесты `advance_status_sync`).
- `drop_script_frames_qc_*` — flow удаления qc-группы в вебе отсутствует.
- Тесты, требующие drop (`after_drop_check`, `drop_check_keeps_active_key`).

## Риски

- ▶ по умолчанию resume: возврат денег/времени — доделка вместо перезапуска.
  Полный wipe — явным `force_wipe=true` / кнопкой full (вторая кнопка в node-studio).
- `img_pr` ▶ по-прежнему wipe (сохранено осознанно).
