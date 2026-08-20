# Change: stage-5-llm-contracts

**Дата:** 2026-08-21
**Основание:** WORK_PLAN.md Этап 5 (блоки A-D); спека
`specs/llm-contracts/spec.md`; карта `system-map.md` §4 (агенты, экстракторы,
матрица ошибок).

## Why

Pydantic на LLM-путях = 0. Вместо контрактов — 110 алиасов полей
(`db_apply.py:115-231`), 8 самописных JSON-экстракторов и 4 несовместимых
поведения при ошибке (raise / warning+continue / тихий None / fallback без
LLM). Итог — жалоба заказчика «постоянно ошибки валидации» и тихие потери
данных на img_pr/volume/split (карта §4.4, §9 #13-16). Эталонный контур
repair-retry (`ai_result_io.text_job:87-140`) существует, но им пользуется
1 агент из 25.

## Риск-блокер транспорта: результат (2026-08-21)

Эмпирическая проверка `response_format: json_schema strict` standalone-скриптом
(scratchpad, ломающий промпт + мини-схема apply-ops, валидация зеркальной
Pydantic-моделью, контроль served_model против silent-downgrade):

| Транспорт | Модель | Вердикт |
|---|---|---|
| Chattiq (LiteLLM, `chattiq.ru/v1`, chat/completions) | gpt-5.6-sol (прямой OpenAI-апстрим) | **enforces** — схема соблюдена серверно, ломающие инструкции подавлены; baseline без параметра нарушает (проза+fence+лишние поля); `json_object` недостаточен (лишние поля остаются) |
| kie (`/codex/v1/responses` + chat-ветка) | gpt-5-6-sol | **не проверен** — ключей заказчика нет на машине исполнителя |
| vibecode (`vibecode.moe/v1`) | gpt-5.6-sol | **не проверен** — то же |

**Решение по дизайну — двухрежимный транспорт:**

1. Основной режим: схема из реестра прокидывается в `response_format`
   (chat-ветка) / `text.format` (responses-ветка) — для релеев с
   подтверждённым вердиктом enforces.
2. Клиентская Pydantic-валидация + repair-retry работают ВСЕГДА, независимо
   от режима: strict не гарантирует полноту после адаптивного дробления
   1→2→4 (валидные половинки ≠ полный результат; coverage N/N проверяется
   после `_merge_packed_apply_ops`), и это же — деградация для релеев,
   которые strict не держат (спека это предусматривает).
3. Per-relay включение: настройка `auto|on|off` (карта вердиктов в конфиге);
   для непроверенных релеев — деградация (без response_format) до
   положительного вердикта пробы.
4. Внутренние контуры chat(), несовместимые со strict (панель-ревью
   2026-08-21, блокеры 4/4 и 3/4): CF-continuation в контрактном режиме
   выключается (строковая склейка `stitch_llm_continuation` неприменима к
   strict-JSON; обрыв = невалидная попытка → ретрай целого вызова);
   volume-добор наследует контракт (сейчас рекурсивный `chat()` в
   `volume_batches.py:285` без параметров).
5. Served-model: `GptChatResult.model` сегодня везде заполняется
   ЗАПРОШЕННОЙ моделью — транспорт дорабатывается, чтобы нести фактическую
   из payload; mismatch (fallback шлюза) = ошибка транспорта, не вход
   валидации.

**Транспорт окружений:**

- Дев и приёмочные прогоны — Chattiq: `GPT_BASE_URL=https://chattiq.ru`,
  `GPT_CHAT_PATH=/v1/chat/completions`, `GPT_MODEL=gpt-5.6-sol`,
  `GPT_RELAY_TOKEN` пуст. Правок кода не требует.
- Прод — решение заказчика (его kie/vibecode либо шлюз/self-host LiteLLM).
  Открытые пункты: (а) проба kie/vibecode по появлении ключей; (б) при
  проде через LiteLLM-шлюз — отключить `default_fallbacks` (silent
  downgrade на minimax-m3) для ключа пайплайна.

## What Changes

- **A. Транспорт** (`gpt_api.py`): опциональный параметр схемы в `chat()`,
  прокинутый через все шесть контуров — `_chat_adaptive_1_2_4` /
  `chat_pdf_in_chunks` / `ApiGptClient.ask_with_files` / операторский слой /
  CF-continuation (выключение в контрактном режиме) / volume-добор
  (наследование); сериализация в `response_format` (chat) и `text.format`
  (responses); режим `auto|on|off` per-relay; served-model детект.
  Минимально инвазивно: тело `chat()` не переписывается, только сборка
  body и ветвления режима.
- **B. Реестр контрактов** `app/contracts/` (Pydantic v2): `ApplyOpsEnvelope`,
  `SkeletonPayload` + `SceneSlice`×4, `AssemblePayload`, `FrameSpec`,
  `ImgPrOps`, `AnimPrOps`, `VoiceoverPayload`, `CheckReport` (vp.check.v1).
  Алиасы: словарь `FIELD_ALIASES` (`db_apply.py:115-231,:240`) остаётся
  единственным источником — contracts строит из него `AliasChoices`
  (немигрированные потребители не ломаются, дрейфа двух копий нет);
  канон-нормализация ключей (`_canon_key`-эквивалент) — pre-validation.
  Семантические ремонты uuid (Хэмминг :290, номер→uuid :335) — сохраняются
  как pre-validation нормализация с логом в meta; salvage-маркер
  `_salvaged_partial` снимается до валидации → в meta, не в payload.
- **C. Миграция приёмочного множества** (состав зафиксирован спекой, не
  меняется): A12 apply-ops ядро (+A14), A3 split, scene_design-комплекс
  (A5-A11 как одна позиция), A15 img_pr, типизация check-отчётов (A17).
- **D. `LlmContractError` + единая repair-политика** по образцу
  `ai_result_io.text_job:87-140`: фидбек с текстом ошибки, лимиты попыток,
  отклонённые ответы на диск, fail-closed на исчерпании. Закрытие тихих
  путей: img_pr `if all_ops: continue`; volume-добор break; script «весь
  ответ целиком»; локальные fallback — только с маркером `degraded_no_llm`;
  salvage — только с добором до полного покрытия.
- **Шаблон миграции** (инструкция в `docs/openspec/`) для остальных агентов
  силами кодеров заказчика.

## Impact

- Affected specs: `llm-contracts` (дельта: MODIFIED риск-блокер —
  результат + per-relay статус; ADDED двухрежимный транспорт).
- Affected code: `app/services/gpt_api.py`, `app/services/gpt_client.py`,
  `app/services/gpt_operator_client.py`, новый `app/contracts/`,
  `app/services/db_apply.py`, `app/services/xlsx_step_runners.py`,
  `app/services/scene_design/*`, `app/services/img_pr_batches.py`,
  `app/services/volume_batches.py`, `app/services/check_analysis.py`,
  `app/settings.py`, тесты.
- Поведенческий риск: мигрированные пути перестают тихо глотать ошибки —
  шаги, которые раньше «зеленели» на частичном результате, начнут падать в
  `LlmContractError` → step_failure_policy (pause). Это желаемое поведение,
  но первые живые прогоны покажут реальную частоту.

## Приёмка (порог зафиксирован)

Серия ≥10 живых прогонов на реальных темах (транспорт — Chattiq):
0 `LlmContractError` после исчерпания repair-попыток на приёмочном
множестве И **repair-rate ≤ 10%**. Определение метрики: знаменатель —
логическая единица работы политики (агент-вызов/батч), числитель — единицы
с ≥1 repair-попыткой (повтор из-за parse/validate-fail контракта);
транспортные ретраи chat(), дробление 1→2→4 и continuation repair'ом не
считаются (отдельные счётчики). Счётчики — NodeRun.meta, сбор по всем
NodeRun серии включая failed; до llm_calls этапа 3. Прогон с
`degraded_no_llm` на агенте приёмочного множества в серию не
засчитывается.
