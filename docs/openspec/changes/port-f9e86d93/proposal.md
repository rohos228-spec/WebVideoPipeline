# Порт housepc@f9e86d93 — pause/retry sync, montage parent-skip, timeout пачки

Источник: `rohos228-spec/video-pipeline@f9e86d93`
«fix(studio): persist pause/retry sync, montage parent-skip, and apply-ops timeout».

## Портировано

1. `app/main.py` — fallback-unstick после падения `record_step_failure`:
   `failed → pending` через `prepare_node_for_step_start` (адаптация:
   `session_scope` вместо `project_db_session_scope`, `pipeline_steps`
   вместо `telegram.menu`).
2. `app/services/run_sync.py` — `prepare_node failed → pending`
   (auto_unstick); `complete_excel_gpt_node_by_key failed → done` через
   `heal_failed_node_done`.
3. `app/services/montage_board_apply.py` — `failed_parent_skip_reason` +
   `coverage_parent_map` + проброс `parent_of` в 3 фазы: ребёнок упавшего
   родителя пропускается, а не валится следом.
4. `app/services/vo_shot_expand.py` — цепочка для parent_map:
   `_COVERAGE_SHOT_RE`, `parse_coverage_shot`, `parent_still_suppressed`,
   `uses_parent_still`, `coverage_shot_id`, `coverage_parent_shot_id`,
   `find_coverage_parent_frame` (в вебе отсутствовали).
5. `app/services/apply_ops_batches.py` — `pack_call_timeout_s()`
   (max(180, GPT_TIMEOUT_S)) + `asyncio.wait_for` вокруг вызова пачки:
   вечное зависание превращается в split адаптивной схемой.
6. Фронт: `flow-canvas.tsx` (meta-ключи в reconcile + dep) +
   `node-run-status.ts` (ветка `enriching_*`: completed → done, active → running).
7. Тесты: `test_pack_call_timeout_uses_gpt_timeout_not_90`,
   `test_apply_skips_child_when_parent_fails`.

## Осознанно пропущено (непортируемо в single-DB веб)

- `app/project_db.py`, `app/web/routers/projects.py`, `park_leftover`
  user_stop-ветка, replica-sync в `step_failure_policy` — dual-DB
  (`pull_master`/`copy_noderuns`/`run_on_replica_project`), в вебе не к чему.
- Тесты `test_advance_status_sync.py` (+3) — все про dual-DB.
- Текст «Повтор N из M» в вебе уже был.

## Риски

- `wait_for` отменяет висящий `run_operator_api` — таймаут идёт в split,
  при живом GPT просто дробит пачки мельче (дороже, но не висит).
- Poll-эффект apply (из прошлого порта) + новые done-ветки: двойного
  закрытия NodeRun нет (heal идемпотентен через статусы).
