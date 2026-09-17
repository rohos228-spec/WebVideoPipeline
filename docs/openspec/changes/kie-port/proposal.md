# Change: kie-port

**Дата:** 2026-09-17
**Основание:** решение владельца — вернуть рабочие Kie-модели (фото/видео/аудио
во вкладке Генерация). Образец: локалка `main@c53a238b`
(`app/bots/kie_http.py` 559 строк, `app/services/kie_catalog.py` ~82КБ,
`app/web/routers/kie_create.py` 171 строка, тесты `test_kie_catalog.py`,
`test_kie_create_api.py`).

## Why

Фронт Create уже готов (`api.kieCatalog/kieGenerate`, `ModelPickerPopover`,
`kie-pricing.ts`) и стучит в `/api/kie-create/*` → 404: бэкенда нет.
Вкладка Аудио пуста по той же причине (аудио локалки = Kie-каталог:
suno/elevenlabs через kie). Порт возвращает: фото (Flux/Seedream/Z/Qwen),
видео (Seedance/Kling-3.0/Hailuo/WAN/PixVerse/Topaz), аудио (Suno/ElevenLabs).

## 0. Что НЕ берем

- Слой ключей НЕ портируем: у веба свой (`kie_api_key()` с GPT-фолбэком,
  без failover и VPS-relay в `kie_kling.py`). Адаптируем `kie_http` под него,
  а не тянем чужой key-менеджмент (иначе разъедется существующий Kling 2.6).
- Minimax не трогаем. Доки (`NODE_MODELS.md` и др.) — за Antigravity.
- По SR-3: поле `key_fingerprint` из `_key_debug` роутера НЕ портируем
  (отпечаток ключа в ответе — утечка).

## 1. Берем

### 1.1 `app/bots/kie_http.py` (новый, адаптированный)
Логика 1:1 с локалкой (jobs/veo/suno/runway, `create_task/poll_task/download`,
`run_generation`, `generate_kie_video/image`, `_suno_clip_audio_urls`,
`_POLL_PATHS`, `callBackUrl`-заглушка), но транспорт на веб-конвенциях:
`_headers()` = Bearer `kie_api_key()` (одиночный ключ),
`kie_post_json` без перебора ключей. `KieHttpError(KieKlingError)` — как в
локалке (совместимость с `outsee_retry`).

### 1.2 `app/services/kie_catalog.py` (новый, дословно)
Каталог + `catalog_for_ui/get_model/defaults/validate/build_payload/
estimate_credits/resolve_model_id`, `CREDIT_USD`. Импорты только stdlib —
портируется как есть.

### 1.3 `app/settings.py`
Только `kie_upload_base_url` (`KIE_UPLOAD_BASE_URL`,
default `https://kieai.redpandaai.co`) для `upload_file`.

### 1.4 `app/web/routers/kie_create.py` (новый) + маунт в `app/web/api.py`
Эндпоинты `/catalog /credits /estimate /upload /generate /jobs/{id}`,
`enqueue_generation(provider="kie")`. Без `key_fingerprint` (см. §0).

### 1.5 Тесты
Портируем `tests/test_kie_catalog.py`, `tests/test_kie_create_api.py`
(адаптируя импорты при необходимости).

## 2. Верификация
- `ruff`, `mypy`, `policy_check`, коллекция, затронутые тесты.
- Бесплатно: `GET /kie-create/catalog`, `/credits` (ключи есть: KIE_API_KEY set).
- Живые генерации за деньги — только по команде владельца (видео $0.3–0.5).
- Коммит/пуш — после ручной проверки владельцем.
