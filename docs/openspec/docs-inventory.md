# Реестр существующей документации

Дата сверки: 2026-08-20, HEAD 3bbf5c3. Каждый док проверен по коду
(git-история сквоширована, даты файлов бесполезны — сверка только по
исходникам). Назначение: что из старых доков можно втягивать в спеки
ссылками, а чему верить нельзя. Существующие доки — фон, не source of truth
(openspec brownfield-правило).

## Сводный реестр

| Док | Тип | Вердикт | Capability |
|---|---|---|---|
| HANDOVER.md | норм.+опер. | **ПРОТИВОРЕЧИТ КОДУ** | orchestration |
| AGENTS.md | норм.+опер. | ЧАСТИЧНО | llm-contracts / orchestration |
| README.md | описат. | ЧАСТИЧНО | прочее |
| HOW_TO_RUN.md | операц. | ЧАСТИЧНО | прочее |
| ai-pack/START_HERE.md | норм. | **АКТУАЛЕН** | llm-contracts / orchestration |
| docs/NODE_SYSTEM.md | норм. | ЧАСТИЧНО (каталог нод отстал) | orchestration / checks-cascade |
| docs/DB_V2.md | норм. | **ПРОТИВОРЕЧИТ СЕБЕ И КОДУ** | xlsx / llm-contracts |
| docs/PROMPT_CONTRACT.md | норм. | **АКТУАЛЕН** | llm-contracts |
| docs/NODE_MODELS.md | норм.+опис. | ЧАСТИЧНО (цифры хрупкие) | cost-accounting / media |
| docs/OPERATOR_BIBLE.md | операц. | **АКТУАЛЕН** | ui |
| docs/AGENT_MAP.md | норм. (навигация) | ЧАСТИЧНО | orchestration |
| docs/PROMPTS_BLOCKS.md | описат. | АКТУАЛЕН | llm-contracts |
| docs/MASS_CREATION.md | описат. | АКТУАЛЕН | orchestration |
| docs/PROJECTS_MENU_NODES.md | описат. | ЧАСТИЧНО (битая ссылка) | ui |
| docs/SPEC-RELIABILITY-QUEUE-GPT-MUSIC.md | норм. | **АКТУАЛЕН** (спека = код) | orchestration / cache-resume |
| docs/QA-CINEMA-CRITERIA.md | норм. | АКТУАЛЕН (sd_style снят с волн) | checks-cascade |
| docs/TASK_skeleton_draft_editor.md | ист. (ТЗ) | ИСТОРИЧЕСКИЙ (реализовано) | llm-contracts |
| docs/character_registry_database_agent_v3.md | норм. (промпт) | АКТУАЛЕН | llm-contracts |
| docs/scene_grammar_unified_agent_v1.md | норм. (промпт) | АКТУАЛЕН (копии разошлись) | llm-contracts |
| docs/SERIES_PRODUCTION_PLAN.md | норм. (план) | ЧАСТИЧНО (замысел, не система) | series |
| docs/SERIES_OPERATOR_CHEATSHEET.md | операц. | **УСТАРЕЛ** (промптов нет в чекауте) | series |
| docs/SERIES_XLSX_WORKBOOK.md | описат. | ЧАСТИЧНО | xlsx / series |
| docs/SERIES_CAMERA_ANGLES.md | норм. (домен) | АКТУАЛЕН | series |
| docs/HANDOFF-STUDIO-WEB.md, AUTONOMOUS-AUDIT-REPORT.md, QA-FINAL-REPORT.md, QA-RUN-*.md/json | ист. | ИСТОРИЧЕСКИЕ | — |
| docs/FULL-VERIFICATION.md | операц. | ЧАСТИЧНО (метод жив, адреса устарели) | ui / checks-cascade |
| docs/superpowers/, docs/plans/ | ист. | ИСТОРИЧЕСКИЕ | — |

## Ключевые сверки (то, из-за чего вердикты)

**HANDOVER.md — не верить ничему нормативному:**
- «канон — devin/windows-installer, в main не коммитить» ↔ реально ветки ПК
  через ORCHESTRATOR_GIT_BRANCH (`scripts/studio.ps1:32-64`);
- «ChatGPT web пишет план/сценарий» ↔ текст давно API-only (`gpt_api.py:1`);
- «MAX_FAIL=3 → failed» ↔ константы нет, `failed` отменён
  (`main.py:271-273`), реальная политика — `step_failure_policy.py:29-31`
  (3 цикла × 3 = 9, sleep 30 мин), а vision-цикл — вообще 20 кругов
  (`vision_check_loop.py:39`) вопреки записанному запрету;
- «8 шагов, 4 бота» ↔ 18 шагов, 10 ботов.
- PII: `HANDOVER.md:26-27` — owner chat_id и имя бота; убрать при
  переписывании (в списке ротации этапа 0).

**DB_V2.md — противоречит сам себе:** шапка «write-through отключён»
соответствует коду (`db_apply.py:1092` `export_xlsx=False`), но §5/§7/§7.1/§10
в четырёх местах утверждают обратное («export_xlsx: true по умолчанию»).
Плюс «явный Импорт (excel_io)» не существует: `import_project_xlsx` не
подключён ни к одному роуту/UI. Самый цитируемый и самый опасный док —
приоритет на переписывание.

**AGENTS.md vs PROMPT_CONTRACT.md — взаимоисключающие правила:** AGENTS.md
учит TSV/xlsx-writeback как контракту, PROMPT_CONTRACT.md (и код:
`gpt_operator_client.py:45,829` — TSV только как fallback-ветка) объявляет
его deprecated. Верен PROMPT_CONTRACT. Также AGENTS.md называет kie/GPT_API_KEY
дефолтом текста — реально дефолт vibecode (`vibecode_catalog.py:22`,
`gpt_api.py:186-196`).

**Каталог нод разошёлся в трёх доках одинаково:** NODE_SYSTEM §4,
AGENT_MAP §5, README-mermaid показывают scene_design монолитом и теряют
sfx_plan/sfx_gen. SoT — `node_registry.py:33-121, :156-177` (канон:
sd_agent×5 + sd_assemble; sd_style снят с волн → активных 5).

**Меню STUDIO:** из четырёх описаний верно только ai-pack/START_HERE.md §3;
SoT — `scripts/studio.ps1:983-990`.

**Битые ссылки:** docs/UI_BUTTON_AUDIT.md (из PROJECTS_MENU_NODES),
scripts/studio.py (из README; есть studio.ps1), prompts/steps/series/**
(из AGENT_MAP §13 и SERIES_*-доков).

**ai-pack/ рассинхронизирован с docs/:** diff непуст для AGENT_MAP, DB_V2,
OPERATOR_BIBLE; `build_ai_pack.py`/`verify_ai_pack.py` на месте, но давно
не прогонялись.

**README:** экспорт в xlsx есть (`db_browser.py:207`), импорта нет;
`OUTSEE_TOKEN` → реально `OUTSEE_API_KEY` (`settings.py:51`).

## Что втягивается в спеки (проверенное)

- `PROMPT_CONTRACT.md` — опорный док для [[llm-contracts]]: SoT = DB,
  три пути записи (apply-ops / artifact / staging), TSV deprecated.
  Слабина: WRITEBACK_HINT всё ещё подмешивается через `llm_contract.py:13`.
- `SPEC-RELIABILITY-QUEUE-GPT-MUSIC.md` — образец «их спека, совпавшая с
  кодом»; политика отказов для [[cache-resume]]. В спеке нет исключений из
  sleep (SQLite busy `step_failure_policy.py:226`, HTTP 402 `:228`) — учтено.
- `QA-CINEMA-CRITERIA.md` — критерии осей для [[checks-cascade]].
- `ai-pack/START_HERE.md` — единственный верный операционный вход для агентов.
- `NODE_MODELS.md` — имена ключей и id моделей подтверждены; прайс-таблицы
  «×3 markup» — только UI-отображение, НЕ учёт затрат: полей стоимости в
  моделях/сервисах нет вообще → [[cost-accounting]] строится с нуля, как и
  заложено.

## Правки по итогам сверки (применены 2026-08-20)

1. [x] HANDOVER.md — шапка-предупреждение «УСТАРЕЛ, см.
   ai-pack/START_HERE.md»; PII (chat_id, имя бота) убраны → отсылка к
   `TELEGRAM_OWNER_CHAT_ID` в `.env`.
2. [x] DB_V2.md — тело приведено к собственной шапке и коду в 6 местах
   (write-through off, `export_xlsx=false` default, true = legacy opt-in).
3. [x] Каталог нод — SoT-примечания в NODE_SYSTEM.md §4, AGENT_MAP.md §5,
   README (веер sd_agent×5 + sd_assemble, ноды sfx_*).
4. [x] AGENTS.md — TSV-writeback помечен DEPRECATED fallback (согласован с
   PROMPT_CONTRACT), дефолт-провайдер текста исправлен на vibecode.
5. [x] ai-pack пересобран и верифицирован (`build_ai_pack.py` +
   `verify_ai_pack.py` → OK, 13 файлов, HEAD 3bbf5c3).

Не делалось (решение заказчика): переименование/удаление исторических доков,
починка битых ссылок в series-доках (промпты вне git).
