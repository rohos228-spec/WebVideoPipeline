# Tasks: kie-port

- [x] A.1 Разведка: фронт готов, не хватает 3 файлов; зависимости
      (`kie_api_key*`, `kie_post_json`, relay-failover) в вебе отсутствуют —
      решение: адаптированный порт, не дословный.
- [x] B.1 `app/bots/kie_http.py` (адаптированный, §1.1 спеки).
- [x] B.2 `app/services/kie_catalog.py` (дословно).
- [x] B.3 `app/settings.py`: `kie_upload_base_url`.
- [x] B.4 `app/web/routers/kie_create.py` + маунт (без `key_fingerprint`).
- [x] C.1 Тесты `test_kie_catalog.py`, `test_kie_create_api.py` (24 passed).
- [x] C.2 Гейт (ruff/mypy/policy/collect) + бесплатные живые проверки:
      `/catalog` 200/53 модели, `/credits` 200 (баланс виден, ключ живой).
- [ ] C.3 Отчет владельцу. Без коммита до команды.
