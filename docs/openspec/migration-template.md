# Шаблон миграции LLM-агента на контракты (этап 5)

Инструкция для переноса немигрированного агента (карта: `system-map.md`
§4.2, A1-A25) на реестр контрактов `app/contracts/` силами разработчиков
заказчика — без чтения всей кодовой базы. Мигрированные образцы для
подглядывания: apply-ops (`gpt_operator_client._run_operator_api_real`,
project_file-ветка), split (`xlsx_step_runners.run_split_xlsx`),
scene_design (`scene_design/runner._run_one_agent`).

## Что даёт миграция

1. Схема ответа уезжает в `response_format` на релеях с вердиктом enforces
   (сейчас — chattiq; настройка `GPT_STRUCTURED_OUTPUTS` /
   `GPT_STRUCTURED_RELAYS` в `app/settings.py`).
2. Невалидный ответ → repair-повтор с текстом ошибки («# ОШИБКИ ПРОШЛОЙ
   ПОПЫТКИ»), отклонённые ответы на диск `data/<proj>/llm_rejects/`.
3. Исчерпание лимита → `LlmContractError` наверх (fail-closed, шаг падает
   в step_failure_policy) — никаких тихих None/частичных результатов.
4. Метрика repair-rate — автоматически в `data/<proj>/llm_metrics.jsonl`
   (свод: `scripts/llm_repair_rate.py`).

## Шаг 1 — объяви схему

`app/contracts/<agent>.py`, Pydantic v2. Правила:

- Модель повторяет ФАКТИЧЕСКИЙ формат ответа агента (посмотри живые ответы
  в `data/<proj>/tmp_gpt/` / `excel_gpt_uploads/`), а не желаемый.
- Русские синонимы ключей — `validation_alias=AliasChoices(...)`;
  для полей apply-ops НЕ дублируй словарь — импортируй
  `db_apply.FIELD_ALIASES` (единственный источник, см.
  `contracts/apply_ops.canonicalize_fields`).
- Верхний уровень — `extra="forbid"` (ловит поля-выдумки), свободные
  информационные конверты (отчёты) — `extra="allow"` + обязательное ядро.
- Если ответ — apply-ops с ограниченным набором полей, унаследуй
  `ApplyOpsEnvelope` и добавь allowlist-валидатор (образец —
  `contracts/prompt_ops.ImgPrEnvelope`).

```python
from pydantic import BaseModel, ConfigDict, model_validator
from app.contracts.base import LlmContract

class MusicPrompt(BaseModel):          # пример: A21 music
    model_config = ConfigDict(extra="forbid")
    style: str
    prompt: str

    @model_validator(mode="after")
    def _non_trivial(self) -> "MusicPrompt":
        if len(self.prompt.strip()) < 20:
            raise ValueError("prompt короче 20 символов — это не промт музыки")
        return self

MUSIC = LlmContract(name="vp_music", model=MusicPrompt)
```

Зарегистрируй в `app/contracts/__init__.py` (`_REGISTRY` + `__all__`).

`strict=True` только с ручной `schema_override` (см. `prompt_ops.py`):
pydantic-генерённая схема в OpenAI strict-режиме невалидна (нужны
`additionalProperties: false` и полный `required`; опциональное поле =
обязательное nullable). Без override оставляй `strict=False` — схема
всё равно уйдёт в response_format как best-effort.

## Шаг 2 — врежь политику в call-site

Найди вызов LLM агента (карта §4.2 даёт file:line) и оберни его:

```python
from app.contracts import MUSIC, LlmContractError
from app.contracts.policy import run_with_contract

async def _call(feedback: str | None) -> str:
    msg = base_prompt if not feedback else f"{base_prompt}\n\n{feedback}"
    return await gpt_client.gpt_ask_fresh(
        msg,
        timeout=timeout,
        project_id=project.id,
        response_schema=MUSIC.response_schema(),   # схема в транспорт
    )

res = await run_with_contract(
    contract=MUSIC,
    call=_call,
    reject_dir=project.data_dir / "llm_rejects",
    label="music",
    # validate=... — семантика поверх схемы (coverage N/N, доменные
    # проверки): вернуть список проблем, пустой = ок.
)
payload = res.payload          # типизированный MusicPrompt
```

Правила врезки:

- Существующие доменные парсеры/нормализаторы НЕ переписывай — зови их в
  `validate=` (образец: `_run_one_agent._semantic` в scene_design).
- Первая попытка политики может быть уже полученным ответом — если вызов
  уже случился выше по коду (образец: apply-ops в operator-клиенте).
- Лимиты: default 2+2 (parse/validate). Для тяжёлых контекстов (сотни КБ)
  ставь 1+1 — repair платный.
- Локальный fallback без LLM допустим ТОЛЬКО после исчерпания политики и
  ТОЛЬКО с маркером `*_degraded_no_llm` в project.meta + WARNING
  (образец: split). Молчаливый фолбэк — регресс спеки.
- Наружу пропускай `LlmContractError` (или оборачивай в доменный тип, как
  scene_design — `SceneDesignAgentError(str(e))`), но НЕ глотай.

## Шаг 3 — тесты

1. Юнит: валидный ответ / ломаный JSON / нарушение схемы / лишние поля —
   образцы `tests/test_contracts_narrow.py`.
2. Врезка: мок `chat`/`gpt_ask_fresh` (см.
   `tests/test_check_contract_strict.py`): битый→валидный = 2 вызова и
   фидбек в промпте; всегда битый = `LlmContractError` + rejects на диске.

## Шаг 4 — живая проверка (обязательна)

Зелёные юнит-тесты ≠ приёмка (harness-гейт в тестах выключен). Прогони
шаг на реальном проекте, затем:

```
.venv/bin/python scripts/llm_repair_rate.py data
ls data/<proj>/llm_rejects/          # что отклонялось и почему
```

Ожидание: 0 исчерпаний, repair-rate по агенту ≤10%. Если repair-rate
высокий — чини ПРОМПТ агента (форматная секция), а не ослабляй схему.

## Учебный пример

A16 anim_pr: контракт `ANIM_PR` уже объявлен (`contracts/prompt_ops.py`),
фаза 1 (`make_animation_prompts.py:290` → `ask_anim_pr_batch`) ещё не
мигрирована — хорошее первое упражнение: врезать политику вокруг
`parse_animation_reply`, семантику оставить в `validate=`.
A21 music для шаблона НЕ подходит: отдаёт сырой текст без JSON.
