# Документация Video Pipeline Web Studio

Данный каталог содержит актуальную нормативную и техническую документацию проекта.

## Архитектура и спецификации

- **База данных и модели**: [DB_V2.md](DB_V2.md) — схема SQLite, версионирование, транзакции и снимки состояния.
- **Система нод**: [NODE_SYSTEM.md](NODE_SYSTEM.md) и [NODE_MODELS.md](NODE_MODELS.md) — архитектура графа пайплайна, типы нод и привязка моделей.
- **Промпт-контракт**: [PROMPT_CONTRACT.md](PROMPT_CONTRACT.md) и [PROMPTS_BLOCKS.md](PROMPTS_BLOCKS.md) — правила сборки промптов для всех этапов.
- **Оркестратор**: [ORCHESTRATOR-V2.md](ORCHESTRATOR-V2.md) — конечный автомат, воркер и продвижение стадий.

## Эксплуатация и регламенты

- **Руководство оператора**: [OPERATOR_BIBLE.md](OPERATOR_BIBLE.md) — регламент ручной и полуавтоматической работы со студией.
- **Массовая генерация**: [MASS_CREATION.md](MASS_CREATION.md) — инструкции по пакетной генерации видео.
- **Инженерная политика**: [ENGINEERING-POLICY.md](ENGINEERING-POLICY.md) — стандарты кода, изоляция веток, гейты качества.
- **Релизы**: [RELEASE-PROCESS.md](RELEASE-PROCESS.md) и [RELEASES.md](RELEASES.md) — версионирование и процесс выкатки.
- **Технический долг**: [DEBT.md](DEBT.md) и [TECH_DEBT_PLAN.md](TECH_DEBT_PLAN.md).

## Структура подкаталогов

- dr/ — Архитектурные решения (Architecture Decision Records).
- openspec/ — Спецификации изменений OpenSpec.
- plans/ — Планы реализации функционала.
- superpowers/ — Скиллы и специнструменты.
- rchive/ — Исторические отчеты прогонов, логи QA и устаревшие заметки (см. [archive/README.md](archive/README.md)).
