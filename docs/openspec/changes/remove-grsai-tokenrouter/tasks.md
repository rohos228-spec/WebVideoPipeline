# Tasks: remove-grsai-tokenrouter

Порядок: A → B → C → D. Каждый блок — гейт перед следующим.

## A. Подготовка

- [x] A.1 Инвентарь: 25 файлов `app/` с `grsai` (~230 хитов), 2 файла
      с `tokenrouter`, 17 тестовых файлов, `web/src` — чисто (0 хитов),
      `deploy/studio/env.template`, доки (за Antigravity).
- [x] A.2 Образец из локалки сверен (`media_route.py`,
      `settings.py:82-89,98-99,163-167`).

## B. Настройки и роутинг

- [x] B.1 `app/settings.py`: удалены 5 grsai-полей, дефолты outsee/outsee,
      прямой GPT-фолбэк, tokenrouter убран из текстовых вариантов.
- [x] B.2 `app/services/media_route.py`: фолбэк outsee, докстринг
      «Outsee vs kie Kling».

## C. Удаление провайдера

- [x] C.1 Удалены файлы: `app/bots/grsai.py`, `app/web/routers/grsai.py`,
      `app/services/grsai_pricing.py`, `tests/test_grsai_client.py`,
      `tests/test_grsai_pricing.py`; роутер размонтирован в `app/web/api.py`.
- [x] C.2 `app/services/outsee_retry.py`: убраны `use_grsai*`-ветки;
      добавлен клауз `and outsee is None` в no_key-гейты (зеркало локалки).
- [x] C.3 Остальные файлы §1.4 спеки — все обработаны; каталоги урезаны
      (вариант B, решение владельца 2026-09-16); дефолты роутера
      `gpt_image_2_vip` / `veo_3_1_lite`.
- [x] C.4 `deploy/studio/env.template`: убраны `GRSAI_*`, дефолты outsee.
- [x] C.5 Корневой `.env.example`: уже outsee-дефолты, без GRSAI (проверено).

## D. Тесты и гейт

- [x] D.1 Обновлены затронутые тесты (пулы, транспорт, роутинг, каталоги,
      леджер, surface, identity, parity); `test_project_gen_models`
      проверяет отклонение удаленных ID.
- [x] D.2 `ruff check` + `ruff format --check` — чисто (3 унаследованных
      несформатированных файла не наши: `main.py`, `auto_advance.py`,
      `test_test_prompt.py`).
- [x] D.3 `mypy --no-warn-unused-ignores` — чисто (370 файлов).
- [x] D.4 `scripts/policy_check.py` — exit 0.
- [x] D.5 `create_app()` smoke + `pytest --collect-only` (3282, дельта −15 =
      удаленные grsai-тесты) + 196 затронутых тестов зелено.
- [ ] D.6 Отчет владельцу (этот файл + сообщение). Без коммита до команды.
