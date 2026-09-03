# video-pipeline — OpenSpec root

Каркас SDD для контракта стабилизации (основание: `WORK_PLAN.md` в корне,
аудит `../job/data/video_pipeline_audit.md`). Развёрнут 2026-08-20.

## Capability map

Спекуемые capability (этапы контракта):

| Capability | Этап | Статус спеки |
|---|---|---|
| [`checks-cascade`](specs/checks-cascade/spec.md) | 0 + 4 | полная; Req 1 внедрён (change `stage-0-security-hygiene`), Req 2-8 — target этапа 4 |
| [`llm-contracts`](specs/llm-contracts/spec.md) | 5 (исполняется 2-м) | полная (target); дизайн транспорта — после проверки риск-блокера |
| [`cache-resume`](specs/cache-resume/spec.md) | 2 (исполняется 3-м) | полная (target) |
| [`cost-accounting`](specs/cost-accounting/spec.md) | 3 (в любой момент) | полная (target) |
| [`merge-customer-fork`](changes/merge-customer-fork/proposal.md) | вне контракта | change 2026-09-03: перенос форка заказчика — фронт целиком, бэкенд выборочно; черновик v0.1 |

Порядок исполнения этапов: 1 → 5 → 2 → 4 → 3. Почему контракты (5) до
кэша (2): (а) выход апстрим-агента = вход даунстрима — пока выход не
типизирован, «вход» для input_hash не определён нормативно; (б) миграция
агентов меняет промпты и форматы — построенные до неё input_hash массово
инвалидировались бы, кэш пришлось бы прогревать дважды. Порядок 2 ↔ 4 —
вопрос приоритета (деньги vs качество), не зависимость; заказчик может
поменять местами без пересмотра спек.

## Не спекуется (заглушки — фон, не работа)

- **orchestration** — самописный poll-оркестратор остаётся как есть; карта
  шагов — в [`system-map.md`](system-map.md), это описание, не спека.
- **media-generation** — боты/API генерации (img/video/voiceover) — трогаем
  только в местах врезки проверок и кэша.
- **studio-ui** (Next.js), **xlsx-слой** (второй SoT, 61 модуль),
  **telegram-bot** — вне текущей сметы; xlsx — кандидат роадмапа, последним.

## Артефакты

- [`system-map.md`](system-map.md) — карта системы: 25 логических агентов,
  фактические вход/выход, где ломается. Deliverable этапа 1.
- [`docs-inventory.md`](docs-inventory.md) — реестр существующей документации
  (31 док) с вердиктами по сверке с кодом: чему верить, что противоречит.
- `changes/` — активные changes; `changes/archive/YYYY-MM-DD-<id>/` — принятые.
