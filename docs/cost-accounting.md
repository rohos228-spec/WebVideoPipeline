# Учёт расходов: контракт и картина мира (v1, рабочая версия)

Документ — общая точка опоры для нескольких исполнителей. Часть «Контракт API»
и «Что где лежит» заморожены: их надо соблюдать, а не перепридумывать. Часть
«Как считать» архитектор уточняет и расширяет — но без ломки контракта.

## Что уже есть в коде

| Место | Что делает |
| --- | --- |
| `app/services/llm_ledger.py` | обёртка `llm_call(...)`: пишет строку в `llm_calls` с ценой из `llm_prices.json` |
| `app/services/media_ledger.py` | обёртка `media_call(...)`: пишет в `media_calls`, считает `cost_usd` из `media_prices.json`, ставит `unpriced` |
| `app/services/step_billing.py` | прайс генераций в студии: цена шага, холды, `settle` в `credit_entries` |
| `app/services/credit_ledger.py` | кредитный леджер: `topup`, `hold`, `release`, `settle` |
| `app/services/credits.py` | единицы: `MICRO = 1_000_000` микрокредитов в кредите, `format_credits` |
| `app/services/cost_attribution.py` | привязка расходов к проекту |
| `app/web/routers/billing.py` | `/api/billing/balance`, `/api/billing/usage-history`, расценки шагов |
| `app/web/routers/llm_costs.py` | `/api/llm-costs/projects`, `/api/projects/{id}/llm-costs` (владелец) |
| `app/services/llm_prices.json` | 44 модели с ценами за токены |
| `app/services/media_prices.json` | 57 моделей с ценой за единицу (картинка/секунда/вызов) |
| `app/backfill_media_costs.py` | пересчёт исторических `media_calls` из прайса (`--apply`, `--tenant`) |
| `web/src/components/panels/left-bottom/payments-panel.tsx` | панель «Оплата» для любого пользователя |

Таблицы Postgres (`videopipeline_v2`, RLS по `app.tenant_id`):

- `llm_calls` — факт вызова модели: провайдер, модель, токены, `cost_usd`, проект, тенант, время;
- `media_calls` — факт генерации: `kind` (image/video/…), провайдер, модель, `units`, `unit`, `cost_usd`, `unpriced`, проект, время;
- `credit_entries` — леджер: `kind` (`topup`/`hold`/`release`/`settle`), `delta_micro` (знаковое: пополнение +, списание −), `cost_usd`, `margin`, `ref_table`, `ref_ids` (JSON-массив id вызовов), проект, время.

Замечание про доступ: RLS ограничивает выборку арендатором запроса, роль `admin` даёт доступ к инструментам владельца, но не отменяет политику. Поэтому ручки `/api/admin/costs/*` считают в границах арендатора; платформенный тотал — отдельная задача (роль с BYPASSRLS или SECURITY DEFINER), в v1 её нет.

Деньги: 1 кредит = 1 000 000 микрокредитов; в кредит кладётся себестоимость × маржа (в `credit_entries.settle` маржа 3.0).

## Что требуется (цель заказчика)

1. Учтено **всё**, что тратит деньги: генерация видео во вкладке «Генерация» (включая провайдеров KIE/Outsee), работа LLM-нод графа проекта, генерация картинок, чат студии.
2. Ни одного «нуля там, где просто нет цены»: если цены нет — так и помечено (`unpriced`), цифра не подставляется.
3. Админ видит в личном кабинете: по дням, моделям, провайдерам, видам (видео/картинки/LLM), проектам; факт расхода против списанных кредитов; покрытие прайса и список нерасцененных вызовов.
4. Цифры сходятся: сумма фактических вызовов vs `credit_entries.settle`, с явным расхождением.

## Контракт API v1 (заморожен, только владелец)

Все ручки: префикс `/api/admin/costs`, доступ — только `admin`/владелец (иначе 404, как `llm_costs.py`), параметры `from`/`to` в ISO (`YYYY-MM-DD`), по умолчанию — последние 30 дней, `tz` = Europe/Moscow.

```
GET  /api/admin/costs/overview
     -> { "from": "…", "to": "…",
          "totals": { "actual_usd": 0.0, "credits_charged": 0.0, "credits_micro": 0,
                      "calls": 0, "unpriced_calls": 0, "price_coverage_pct": 0.0 },
          "by_kind": [ { "kind": "image", "calls": 0, "units": 0.0,
                         "actual_usd": 0.0, "credits_charged": 0.0, "unpriced_calls": 0 } ] }

GET  /api/admin/costs/by-day?kind=&provider=&model=&project_id=
     -> [ { "day": "2026-10-22", "kind": "image", "calls": 0,
            "actual_usd": 0.0, "credits_charged": 0.0, "unpriced_calls": 0 } ]

GET  /api/admin/costs/by-model?kind=&provider=&project_id=
     -> [ { "kind": "video", "provider": "kie", "model": "veo-3-1-lite",
            "calls": 0, "units": 0.0, "unit": "second",
            "actual_usd": 0.0, "credits_charged": 0.0,
            "avg_usd": 0.0, "unpriced_calls": 0, "has_price": true } ]

GET  /api/admin/costs/by-project
     -> [ { "project_id": "…", "project_title": "…",
            "actual_usd": 0.0, "credits_charged": 0.0, "calls": 0, "unpriced_calls": 0 } ]

GET  /api/admin/costs/prices?kind=
     -> [ { "kind": "image", "provider": "outsee", "model": "nano-banana-2",
            "unit": "item", "usd_per_unit": 0.02, "usd_per_call": null,
            "has_price": true, "calls_30d": 0, "unpriced_30d": 0 } ]

POST /api/admin/costs/recount      тело: { "from": "…", "to": "…", "dry_run": true }
     -> { "scanned": 0, "priced": 0, "still_unpriced": 0, "added_usd": 0.0, "dry_run": true }

GET  /api/admin/costs/reconcile
     -> { "actual_usd": 0.0, "charged_usd": 0.0, "delta_usd": 0.0, "ratio": 0.0,
          "by_ref_table": [ { "ref_table": "create_generations", "charged_usd": 0.0,
                              "entries": 0, "actual_usd": 0.0 } ] }
```

## Что и где лежит (правила работы)

- Ветка `design-main-ui/test`, рабочий каталог `C:\Users\Admin\Desktop\video-pipeline\.worktrees\design-main-ui`. Локальный `main` не трогать.
- Пушить и выкатывать умеет только ведущий: исполнители правят файлы и оставляют рабочее дерево, коммит делает ведущий.
- Новые Python-модули: роутер — `app/web/routers/admin_costs.py`, подключение — там же, где подключаются остальные роутеры. Схемы — рядом с роутером, как в `billing.py`.
- Миграции — только если без них нельзя. Предпочтительно обойтись существующими таблицами.
- UI: `web/src/app/admin` (страницы), `web/src/components/admin` (блоки), проксирование — как у существующих ручек (`web/src/app/api/...`). Себестоимость — только админу; участнику ручки отдают 404, UI обязан это переживать.
- Проверки: `pytest tests/... -q` (питон из `.venv`), `ruff check app`, `mypy` по изменённым файлам, `tsc --noEmit`, `npm run build` в `web`.
