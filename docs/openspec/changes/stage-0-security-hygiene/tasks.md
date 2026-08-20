# Tasks: stage-0-security-hygiene

## 1. Секреты из дерева

- [x] 1.1 `HANDOVER.md:54` — креды SOCKS5 → плейсхолдер + отсылка к
      `TELEGRAM_PROXY_URL` (применено sed'ом, без вывода значения в чат).
- [ ] 1.2 `.env.example` — VIBECODE_API_KEY → пустое значение. Файл закрыт
      пермишенами агента; заказчик выполняет:
      `! sed -i 's/^VIBECODE_API_KEY=.*/VIBECODE_API_KEY=/' .env.example`
- [ ] 1.3 Скан `.env.example` на прочие длинные значения-секреты (см. команду
      в 1.2-отчёте) — по результату повторить п.1.2 для найденного.
- [ ] 1.4 Живые значения — в локальный `.env` (заказчик).

## 2. Fail-closed проверок

- [x] 2.1 `app/settings.py` — флаг `allow_stub_checks` (VP_ALLOW_STUB_CHECKS,
      default False).
- [x] 2.2 `gpt_operator_client.py::run_operator_api` — RuntimeError для
      check-ролей без ключа и без опт-ина.
- [x] 2.3 `tests/conftest.py` — опт-ин в autouse-фикстуре (тесты stub-пути
      живы: 94 passed; 1 фейл `test_check_fix_writeback_applies_tsv` —
      предсуществующий, воспроизводится на чистом дереве).
- [x] 2.4 Регрессионные тесты `tests/test_operator_fail_closed.py`
      (6 кейсов: 3 роли + check_mode + не-check роль + опт-ин).

## 3. Вне репо (заказчик)

- [ ] 3.1 Ротировать VIBECODE_API_KEY.
- [ ] 3.2 Сменить пароль SOCKS5-прокси.
- [ ] 3.3 Приватизировать оба репозитория.
- [ ] 3.4 Предупредить владельца форка Kir0029/video-pipeline-copy.

## Приёмка

Секретов в дереве нет (grep-скан чистый); при отсутствии ключа check-роли
падают RuntimeError (регрессионные тесты + живая проверка на машине без
ключа). По приёмке — архив change в `changes/archive/2026-MM-DD-stage-0-…`.
