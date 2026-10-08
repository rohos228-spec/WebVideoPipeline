# Runbook: слияние БД second-mechanic → main (фаза 5, операция)

> Выполняется в окно обслуживания, руками, по шагам. Код уже проверен
> локально на SQLite-фикстурах (dry-run + apply + верификация).
> Backups first: снапшот тома Postgres И `pg_dump` обеих баз до шага 4.

## 0. Предусловия

- Обе базы на head одной ревизии (`audit_merge.py` это проверяет, иначе СТОП).
- Единый код задеплоен (ветка unified влита): v2-домен уже умеет читать
  `pipeline_mode`, иначе импортированные `v2`-проекты увидят старый код.
- Окно: запись остановлена (стоп контейнеры app И app-v2 — воркеры пишут
  журналы/лизинги, слияние на живой записи запрещено).

## 1. Аудит (read-only, безопасно в любое время)

```bash
python scripts/audit_merge.py \
  --main-url "postgresql+asyncpg://app@/vp?host=/var/run/postgresql" \
  --v2-url "postgresql+asyncpg://app@/vp2?host=/var/run/postgresql"
```

Ожидаем: пересечения slugs (пойдут с суффиксом `-v2`), общие emails
(склейка тенантов), перекрытие PK (решается ремаппингом). Блокер: разный
head alembic или разный набор таблиц.

## 2. Dry-run слияния (записи нет, ROLLBACK в конце)

```bash
python scripts/merge_v2_database.py --main-url ... --v2-url ...
```

Проверить в отчёте: число вставленных по таблицам, `переименованные слаги`,
`конфликты (оставлен main)`, `тенанты: склеено/новых`. Правила скрипта:
проекты из v2 получают `pipeline_mode='v2'` по происхождению; справочники
дедуплятся (main побеждает); `used_count` купонов пересчитывается по факту;
дефолт workflow остаётся main; `project_routes`/`work_leases`/`email_verifications`
не копируются (маршруты пересоздаст триггер 0007).

## 3. Тома файлов (после dry-run, до apply)

```bash
# карта переименований — из отчёта dry-run (раздел "переименованные слаги")
rsync -a /var/lib/docker/volumes/studio_app-data-v2/_data/videos/ \
         /var/lib/docker/volumes/studio_app-data/_data/videos/
rsync -a .../batches/ .../batches/
rsync -a .../generations/ .../generations/   # уже tenants-скоупы, конфликтов нет
# переименованные слаги: mv <old> <old-v2> в соответствующих подпапках
```

Проверка: `du -sh` до/после, выборочный `ls` переименованных.

## 4. Apply (окно обслуживания)

```bash
python scripts/merge_v2_database.py --main-url ... --v2-url ... --apply
```

## 5. Верификация (must-pass, иначе откат)

```sql
-- счётчики сошлись с dry-run
SELECT pipeline_mode, COUNT(*) FROM projects GROUP BY 1;
-- проводки на месте, балансы не уплыли
SELECT tenant_id, balance_micro FROM credit_accounts;
-- маршруты пересоздались триггером
SELECT COUNT(*) FROM project_routes;  -- = COUNT(*) FROM projects
-- сиквенсы за максимумом (скрипт делает setval; перепроверить)
SELECT last_value FROM projects_id_seq;  -- > MAX(id)
```

Плюс smoke в UI обеих механик (смoke-чеклист фазы 3).

## 6. Откат

Откат = восстановление main-БД из снапшота шага 0 + удаление скопированных
файлов томов по списку из шага 3. Частичный откат слияния средствами SQL
не предусмотрен сознательно (редактировать руками дешевле, чем чинить).

## 7. После слияния (фаза 5.3, отдельно)

Единый контейнер, решение по домену v2 (рекомендация: staging-слот),
архивация ветки `second-mechanic`, обновление SAAS-PIVOT/AGENT_MAP.
