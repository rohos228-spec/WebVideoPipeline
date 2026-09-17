# Change: catalog-actualization

**Дата:** 2026-09-17
**Основание:** решение владельца; живой `/api/v1/models` Outsee уже списка
(картинки: `nano-banana-2`, `gpt-image-2` — только 2K; видео: `veo-3-1-lite` —
только 720p/8с), опции Студии шире и молча подменяются дефолтом.

## Why

Расхождения живого каталога и опций:
- `nano_banana_2_lite` нет ни в живом каталоге, ни в `OUTSEE_WIRED_IMAGE_MODELS`
  — маппер тихо подменяет дефолтом (решение 2026-09-17: удалить опцию).
- Разрешения 4K (картинки) и 1080p (видео) живой шлюз не отдает (только 2K/720p).
- `media_prices.json`: нет `outsee:gpt-image-2-vip` (реальный slug economic!) —
  лог `единицы посчитаны, цена неизвестна`; цены вписывает владелец (AR-8:
  не выдумывать), здесь только ключи.
- Kie/Minimax живьем не проверить (нет list-эндпоинта / ключа) — не трогаем.

## 0. Что НЕ берем

- Поведение генерации не меняется: убираются только опции без бэкенда.
- Цены USD не выдумываем: новые ключи с `null`, вписывает владелец.
- `grsai:*` ключи прайса удаляем (провайдер удален); старые `outsee:*`
  ключи снятых моделей оставляем (история леджера).
- Доки (`NODE_MODELS.md` и др.) — за Antigravity.

## 1. Берем

### 1.1 Бэкенд (`app/generation_options.py`)
- Удалить опцию `nano_banana_2_lite`; ключи разрешений снятых генераторов
  уже чистые (проверено).
- Картинки: убрать `4k` из `IMAGE_RESOLUTIONS`, `IMAGE_RESOLUTIONS_BY_GENERATOR`
  (ключи `*_4k`), `order` в `clamp_image_resolution_id`; дефолт clamp `2k` ок.
- Видео: убрать `1080p` из `VIDEO_RESOLUTIONS`; дефолт роутера
  `generation_options.py` (`routers/`) проверить на `1080p`.
- `media_prices.json`: добавить `outsee:gpt-image-2-vip` (null),
  удалить блок `grsai:*`, поправить комментарий (без `/ grsai`).

### 1.2 Фронт (`web/src/lib/node-model-catalog.ts`, `outsee-catalog.ts`)
- Те же удаления (lite/4K/1080p, снятые модели); бамп `STUDIO_VERSION`, сборка.

### 1.3 Тесты
- Обновить parity/adge-тесты под новый набор; paradigms:
  `test_outsee_image_ui_parity`, `test_project_gen_models`,
  `test_billing_api`, `test_db_v2_api`, `test_media_ledger`,
  `test_media_route`, `test_minimax`, `test_outsee_retry`,
  `test_outsee_video_fallback`, `test_studio_agent`, `test_vibecode_catalog`.

## 2. Верификация
- `ruff`, `mypy`, `policy_check`, `create_app()` smoke.
- Точечные тесты §1.3 + коллекция; живой опрос `/api/v1/models`
  (бесплатно). Живых генераций за деньги нет — финал за владельцем.
- Коммит/пуш — после ручной проверки владельцем.
