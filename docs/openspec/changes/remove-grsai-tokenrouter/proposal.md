# Change: remove-grsai-tokenrouter

**Дата:** 2026-09-16
**Основание:** решение владельца — провайдеры Grsai и TokenRouter не используются.
В локальной версии (`rohos228-spec/video-pipeline`, `main@c53a238b`) чистка уже
выполнена (GRSAI-01..05): провайдер выпилен, остались только graceful-упоминания
«не Grsai». Второй разработчик перенос не выполнил — портируем здесь.
Образец: локалка `app/services/media_route.py`, `app/settings.py:82-89,98-99,163-167`.

## Why

Grsai — мертвый провайдер с дефолтом на себя (`IMAGE/VIDEO_PROVIDER=grsai`):
новый пользователь без ключа упирается в нерабочий дефолт, код держит две
параллельные ветки генерации (`outsee_retry.py`: `use_grsai` / `use_grsai_video`),
отдельный роутер, прайсинг и тесты. TokenRouter — неиспользуемый текстовый
провайдер (текст: `kie` по умолчанию + `vibecode` как альтернатива).

## 0. Что НЕ берем (не архитектурный сдвиг, вне скоупа)

| Пункт | Причина |
|---|---|
| Minimax (`bots/minimax.py` и вызовы) | Отдельный провайдер, указаний не было; владелец им не пользовался, но и не просил удалять |
| Изолированные `project.db`, API-трекер | Подтверждено владельцем 2026-09-16: в веб-версии не нужны |
| `docs/*.md`, `ai-pack/*` с упоминаниями Grsai | Документацию доводит Antigravity (у него карта переноса); список мест — в отчете к задаче |
| Переименование `telegram_style_ask_*` в тестах | Не мессенджер, имена методов; отдельно, не в этом чейнже |

## 1. Берем

### 1.1 Настройки (`app/settings.py`)
- Удалить поля: `grsai_api_key`, `grsai_base_url`, `grsai_default_image_model`,
  `grsai_default_video_model`, `create_max_parallel_grsai`.
- Дефолты: `image_provider="outsee"`, `video_provider="outsee"`
  (как в локалке); комментарии `outsee | minimax` вместо `outsee | grsai | minimax`
  там, где grsai был вариантом выбора.
- GPT-фолбэк — прямой, как в локалке (`gpt_api_effective_key/base_url` только
  из `GPT_*`, без `grsai_*`); `host` в диагностике: `kie.ai` / `API`.
- `tokenrouter` убрать из текстовых вариантов (остаются `kie` + `vibecode`).

### 1.2 Удаляемые файлы
- `app/bots/grsai.py` (~730 строк), `app/web/routers/grsai.py`,
  `app/services/grsai_pricing.py`, `tests/test_grsai_client.py`,
  `tests/test_grsai_pricing.py`; размонтировать роутер в `app/web/api.py`.

### 1.3 Ветки генерации (`app/services/outsee_retry.py`)
- Убрать `use_grsai` / `use_grsai_video`, `studio_id_to_grsai_slug*`,
  `GRSAI_WIRED_*_MODELS`, `quote_generation` из `grsai_pricing`
  (заменить на существующий outsee-прайсинг рядом), `provider="grsai"`
  в ledger-записях → фактический провайдер.

### 1.4 Остальные ~20 файлов (по одному, с чтением контекста)
`generation_options.py`, `models.py`, `bots/outsee_http.py`, `create_jobs.py`,
`error_catalog.py`, `generation_storage.py`, `gpt_api.py`, `image_transport.py`,
`img_pr_budget.py`, `img_streams.py`, `knowledge_index.py`, `media_ledger.py`,
`media_route.py` (фолбэк `outsee`, докстринг), `montage_board_regen.py`,
`video_error_policy.py`, `web/api.py`, `web/identity.py`,
`web/routers/create_queue.py`, `web/routers/outsee_http.py`,
`services/text_llm_catalog.py`, `web/routers/outsee_create.py`
(tokenrouter), тесты (17 файлов: правка `provider="grsai"` → фактический,
параметризация).

### 1.5 Шаблоны окружения (функциональные, не доки)
- `deploy/studio/env.template`: убрать `GRSAI_*`, дефолты
  `IMAGE/VIDEO_PROVIDER=outsee`.
- Корневой `.env.example`: привести провайдерные дефолты к outsee
  (проверено: `GRSAI_*/TOKENROUTER_*/TELEGRAM_*` там уже отсутствуют).

## 2. Верификация
`ruff check`, `ruff format --check`, `mypy --no-warn-unused-ignores`,
`policy_check.py`, `create_app()` smoke, `pytest --collect-only`,
точечно затронутые тесты. Полная суита — по команде владельца
(храповик + неполные промты). Коммит/пуш — только после ручной проверки
владельцем.

## 3. Открытые вопросы к владельцу (не решаю сам)
- Minimax: оставить как есть (принято по умолчанию)?
- `CREATE_MAX_PARALLEL_GRSAI=10` — удалить или переиспользовать под outsee?
