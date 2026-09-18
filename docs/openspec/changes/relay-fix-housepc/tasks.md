# Tasks: relay-fix-housepc

- [x] A.1 Дифф `bb3d92e3` разобран (3 части + докстринг); граница §0 спеки.
- [x] B.1 `_override_vibecode_base_url` + `_hitting_vps_relay` + `_headers`.
- [x] B.2 `parse_chat_completions_sse_lines`: envelope-salvage.
- [x] B.3 Докстринг `gpt_api_effective_base_url`.
- [x] C.1 Гейт (ruff/mypy/policy) + юнит-тесты salvage.
- [x] C.2 CI-правки: `test_headers_include_relay_token` (relay-база в тест),
      `test_node_override_vibecode_*` (bypass→via relay + токен); 80/80.
- [ ] C.3 Отчет владельцу. Без коммита до команды.
