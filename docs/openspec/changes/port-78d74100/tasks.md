# Задачи — порт housepc@78d74100

- [x] A.1 `db_apply.close_truncated_json` + хук в `extract`.
- [x] A.2 `gpt_api._chat_unscoped`: 2 salvage-блока (оба continue-цикла).
- [x] A.3 `enrich_xlsx._complete_excel_gpt_noderun` + ранний вызов, поздний блок удалён.
- [x] A.4 `run_sync`: heal overflow slot 0 → done.
- [x] B.1 `assemble-montage-board.tsx`: весь набор разблокировки.
- [x] B.2 `tsc --noEmit` чист (eslint в проекте не настроен — предсущее).
- [x] C.1 2 теста портированы: 19/19 в файлах, 115/115 в связанных модулях.
- [x] C.2 Гейт: ruff check/format clean (3 unformatted — предсущие),
      mypy clean, policy_check clean.
- [ ] C.3 Отчет владельцу. Без коммита до команды.
