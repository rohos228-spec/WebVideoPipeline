# Tasks: fix-llm-calls-migration

- [x] Диагноз: сверка `Base.metadata` (33 таблицы) с живой `data/state.db` —
      отсутствуют `llm_calls`, `work_leases`; ни одна ревизия их не создает.
- [x] `migrations/versions/0014_llm_calls_work_leases.py` (upgrade + downgrade).
- [x] Тест `test_managed_db_missing_late_tables_is_healed` + прогон на копии
      живой БД (0013→0014, INSERT OK).
- [x] Гейт: `pytest` 5/5, `ruff check/format`, `mypy`, `policy_check` exit 0.
- [ ] Отчет владельцу. Без коммита до команды.
