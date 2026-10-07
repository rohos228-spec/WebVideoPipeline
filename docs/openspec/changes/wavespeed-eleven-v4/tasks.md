# Tasks: wavespeed-eleven-v4

- [x] Шаг 1: Конфигурация и окружение
  - [x] Ветка `feat/wavespeed-eleven-v4` от `main`.
  - [x] Добавление `WAVESPEED_API_KEY` в локальный `.env`.
  - [x] Добавление плейсхолдера `WAVESPEED_API_KEY=` в `.env.example`.
  - [x] Добавление полей в `app/settings.py`.
- [x] Шаг 2: Каталог голосов (200 русских голосов)
  - [x] Парсинг MP3 файлов из `Telegram Desktop`.
  - [x] Генерация манифеста `ru-voices.json` (ID, имя, описание, пол).
  - [x] Размещение сэмплов для предпрослушивания в UI (`data/voices/samples/` и `/api/voices/{id}/sample`).
- [x] Шаг 3: Бэкенд-клиент WaveSpeed AI
  - [x] Модуль `app/bots/wavespeed_http.py` (submit, poll, download).
  - [x] Интеграция с роутером Create (`app/web/routers/kie_create.py`).
  - [x] Unit-тесты для клиента WaveSpeed (`tests/test_wavespeed.py`).
- [x] Шаг 4: UI интерфейс во вкладке «Создать» -> «Аудио»
  - [x] Движок «Озвучка ElevenLabs v4» рядом с Suno.
  - [x] Интерактивные чипсы аудио-тегов (эмоции/режимы/неречь/SFX/акценты) со вставкой в позицию курсора.
  - [x] Модальное окно выбора голоса с поиском, фильтрами и предпрослушиванием.
  - [x] Слайдеры Stability и Similarity.
  - [x] Плеер `AudioStudioPlayer` для сгенерированного аудио.
- [x] Шаг 5: Проверка и верификация
  - [x] Тестовая генерация речи через живой API WaveSpeed.
  - [x] Проверка `scripts/policy_check.py`.
  - [x] Сборка фронтенда `npm run build`.
- [ ] Финал: Приёмка и утверждение пользователем (User review & approval)

