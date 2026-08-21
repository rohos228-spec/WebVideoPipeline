# Delta: cache-resume (stage-2-cache-resume)

## MODIFIED Requirements

### Requirement: Lease с TTL и владельцем (атомарный захват в БД)

Решение по хранению зафиксировано (опция из target-спеки закрыта):
таблица `work_leases` живёт в ОСНОВНОЙ SQLite (WAL + busy_timeout уже
включены, `app/db.py`). Вынос в отдельный `lease.db` — только по факту
contention на живом прогоне («database is locked» на lease-путях);
превентивный замер не делается — ретраи ×3 уже есть в воркере
(`main.py:345-360`).

Уточнение объёма замен: `video_gen_skip` — ручной маркер оператора, не
lease, НЕ заменяется; `vision_check_passed` — материал этапа 4;
`montage_lane` остаётся как есть (per target-спека).

#### Scenario: Contention на живом прогоне
- **WHEN** живой прогон показывает «database is locked» на операциях
  захвата/renew lease
- **THEN** таблица выносится в отдельный `lease.db` тем же helper'ом;
  интерфейс `work_lease.py` не меняется

### Requirement: Рестарт различает resume и recovery

Уточнение к target-спеке: политика безопасности «не продолжать старую
работу автоматически» СОХРАНЯЕТСЯ. startup_guard НЕ откатывает статус и
НЕ сбрасывает NodeRun, но `arm_auto_await_manual_start` остаётся —
продолжение с курсора происходит по ▶ оператора (или существующей
авто-политике после ▶), не самопроизвольно на старте процесса. Метки
`startup_rollback_to`/`startup_blocked_running_status` заменяются на
`orphaned_running` + timestamp.

#### Scenario: Рестарт при живом прогрессе (уточнённый)
- **WHEN** процесс перезапущен, у шага есть курсор; оператор жмёт ▶
- **THEN** шаг стартует с ТОГО ЖЕ running-статуса и продолжает с курсора;
  recovery-скан (диск→БД) выполнен до продолжения; статус назад не
  откатывался ни в какой момент
