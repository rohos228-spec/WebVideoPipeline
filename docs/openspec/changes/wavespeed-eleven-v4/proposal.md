# Change: wavespeed-eleven-v4

**Дата:** 2026-10-07
**Основание:** запрос пользователя — интеграция модели озвучки ElevenLabs v4 через нового провайдера WaveSpeed AI (https://wavespeed.ai/models/elevenlabs/eleven-v4).

## Why

1. Добавление новой современной модели TTS ElevenLabs v4 через API WaveSpeed.
2. Поддержка параметров:
   - `text`: диапазон 1–10 000 символов.
   - `voice_id`: выбор из каталога голосов (200 русских голосов: 100 женских + 100 мужских) с предпрослушиванием.
   - `stability`: 0.0–1.0 (по умолчанию 0.5).
   - `similarity`: 0.0–1.0 (по умолчанию 0.75).
3. Интерактивная вставка аудио-тегов эмоций и режимов подачи (mood, non-speech, SFX, accents) в текущую позицию курсора.
4. Размещение в интерфейсе Web Studio: вкладка «Создать» (Create) -> раздел «Аудио» как отдельный движок «Озвучка ElevenLabs v4» с центрированным крупным аудиоплеером Studio.

## Architecture & Components

1. **Конфигурация:**
   - Переменные окружения `WAVESPEED_API_KEY` и `WAVESPEED_API_BASE_URL`.
   - Настройки в `app/settings.py`.

2. **Каталог голосов (Voice Library):**
   - Парсинг 200 локальных файлов образцов голосов (`C:\Users\EternalFlow\Downloads\Telegram Desktop\ru_voices_*`).
   - JSON-манифест `ru-voices.json` с полями `{id, name, desc, gender, sample_path}`.
   - Раздача статических аудио-образцов для мгновенного прослушивания в интерфейсе без задержек.

3. **Бэкенд-клиент WaveSpeed (`app/bots/wavespeed_http.py`):**
   - Метод отправки задачи: `POST /api/v3/elevenlabs/eleven-v4` с телом `{"text": ..., "voice_id": ..., "stability": ..., "similarity": ...}`.
   - Опрос статуса: `GET /api/v3/predictions/{id}/result`.
   - Скачивание результата из `data.outputs[0]` и сохранение локально в проект/хранилище.

4. **API эндпоинты (`app/web/routers/`):**
   - Поддержка генерации TTS через WaveSpeed в `kie_create` / dedicated роутере.
   - Предоставление каталога голосов и эндпоинта стриминга/раздачи сэмплов.

5. **Фронтенд (`web/src/components/outsee/`):**
   - Поддержка переключения на ElevenLabs v4 в блоке Аудио.
   - Панель эмоциональных аудио-тегов (chips) со вставкой в позицию курсора.
   - Модальное окно библиотеки голосов с поиском, фильтром по полу и аудиоплеером сэмплов.
   - Слайдеры stability и similarity.
   - Воспроизведение через центрированный `AudioStudioPlayer`.
