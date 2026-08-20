# Change: stage-0-security-hygiene

**Дата:** 2026-08-20
**Основание:** WORK_PLAN.md Этап 0; аудит §4 (fail-open), §5 (секреты).

## Why

В публичном репо закоммичены рабочие секреты: живой VIBECODE_API_KEY
(`.env.example:95`, коммит 08f79b6) и креды SOCKS5-прокси (`HANDOVER.md:54`);
есть публичный форк Kir0029/video-pipeline-copy с той же утечкой. Отдельно —
fail-open: без API-ключа операторские проверки получают stub-вердикт `pass`
(`gpt_operator_client.py`), то есть отвал ключа молча зеленит весь контроль
качества. Обе дыры закрываются до любых других работ.

Чистка git-истории не делается сознательно: ключ ротируется, репозитории
становятся приватными, а форк уже скопировал утечку — переписывание истории
не даёт ничего, кроме риска.

## What Changes

- `.env.example`: значение VIBECODE_API_KEY → пустой плейсхолдер.
- `HANDOVER.md:54`: креды прокси → `socks5://user:pass@host:port` + отсылка
  к `TELEGRAM_PROXY_URL` в `.env`.
- Живые значения переезжают в локальный `.env` (в `.gitignore` уже покрыт).
- Fail-closed: check-роли без ключа поднимают `RuntimeError`; stub-путь для
  проверок — только по явному `VP_ALLOW_STUB_CHECKS=1` (dev/tests). Новый
  флаг в `app/settings.py`, опт-ин в `tests/conftest.py`, регрессионные
  тесты `tests/test_operator_fail_closed.py`.
- Вне репо (делает заказчик): ротация VIBECODE_API_KEY, смена пароля прокси,
  приватизация обоих репо, предупреждение владельцу форка.

## Impact

- Affected specs: `checks-cascade` (ADDED: fail-closed requirement).
- Affected code: `app/settings.py`, `app/services/gpt_operator_client.py`,
  `tests/conftest.py`, `.env.example`, `HANDOVER.md`.
- Поведенческий риск: прод без ключа теперь падает ошибкой вместо тихого
  `pass` — это желаемое поведение; dev-окружения без ключа должны выставить
  `VP_ALLOW_STUB_CHECKS=1`.
