# Задачи — порт housepc@f9e86d93

- [x] A.1 `main.py`: fallback-unstick (адаптирован под single-DB).
- [x] A.2 `run_sync`: failed→pending + failed→done (park/user_stop — пропуск, нет в вебе).
- [x] A.3 `montage_board_apply`: parent-skip + `coverage_parent_map`.
- [x] A.4 `vo_shot_expand`: цепочка из 7 функций/констант.
- [x] A.5 `apply_ops_batches`: `pack_call_timeout_s` + `wait_for`.
- [x] A.6 `step_failure_policy`: пропуск (текст есть, реплика — dual-DB).
- [x] B.1 Фронт: `flow-canvas` + `node-run-status`; `tsc` чист.
- [x] C.1 Тесты: 2 портированы (advance_status — пропуск, dual-DB).
- [x] C.2 Гейт: ruff/mypy/policy чисто; 105/105 в связанных модулях.
- [ ] C.3 Отчет владельцу. Без коммита до команды.
