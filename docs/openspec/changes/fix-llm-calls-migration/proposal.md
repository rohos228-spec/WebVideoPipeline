# Change: fix-llm-calls-migration

**Дата:** 2026-09-17
**Основание:** прод-баг — `(sqlite3.OperationalError) no such table: llm_calls`,
учёт звонков уходил в очередь на дозапись (видно в `data/backend.log`).

## Why

Таблицы `llm_calls` (`LlmCall`) и `work_leases` (`WorkLease`) добавлены в
`app.models` ПОСЛЕ baseline 0001, а миграции под них не заведено (0004
упоминает `llm_calls` в докстринге, но создает только `media_calls`).
Свежие базы получали таблицы через `create_all` в 0001, а базы заказчика
(stamp 0001 + upgrade) — нет. Проверка живой БД: 33 таблицы в моделях,
в `data/state.db` отсутствуют ровно эти две, ревизия 0013.

## 0. Что НЕ берем

- Существующие ревизии не правим (AR-2): только новая 0014.
- Продовая `data/state.db` руками не трогаем: ее вылечит авто-апгрейд
  при рестарте (`upgrade_to_head`); проверено на копии БД.

## 1. Берем

- `migrations/versions/0014_llm_calls_work_leases.py`: создание обеих
  таблиц при отсутствии (идемпотентно, схема 1:1 с моделями) + рабочий
  downgrade до 0013 (для локальной разработки).
- Тест `test_managed_db_missing_late_tables_is_healed`: симуляция базы
  заказчика (0013 без таблиц) → upgrade → таблицы + INSERT проходит;
  downgrade → upgrade roundtrip.

## 2. Верификация

- `pytest tests/test_db_migrations.py` (5 зелено), `ruff`, `mypy`, `policy_check`.
- Прогон миграции на копии живой `data/state.db`: 0013→0014, таблицы
  созданы, INSERT проходит.
