# Change: relay-fix-housepc-bb3d92e3

**Дата:** 2026-09-18
**Образец:** `housepc@bb3d92e3` «send vibecode GPT 5.6 Sol through VPS relay»
(прямой vibecode.moe с ПК упирается в ddos-guard; Caddy `/v1/*` уже
проксирует). Прямо связано с новым `GPT_RELAY_TOKEN`.

## Why

Без фикса текстовые вызовы vibecode идут напрямую в `vibecode.moe`
и ловят ddos-guard `upstream_error`; envelope-ошибка убивает весь
парсинг SSE, даже если часть чанков уже пришла.

## 0. Что НЕ портируем

- Nostream-salvage (`_chat_completions_nostream`): в вебе такого
  отправителя нет, писать его с нуля — отдельный чейндж, не часть фикса.
- Условие `text_llm_is_tokenrouter` из `_hitting_vps_relay`: в вебе
  такого свойства нет (tokenrouter retired) — опущено осознанно.

## 1. Берем

1. `_override_vibecode_base_url`: VPS `/v1/*` если relay задан,
   иначе `vibecode.moe` (1:1).
2. `_hitting_vps_relay()` (без tokenrouter-условия, см. §0).
3. `_headers`: токен `X-VP-Relay-Token` когда реально бьем в relay
   (вместо «никогда для vibecode»).
4. `parse_chat_completions_sse_lines`: собирать первую envelope-ошибку
   и кидать ее только если текста нет (salvage вместо fail-fast).
5. Докстринг `gpt_api_effective_base_url` в `settings.py` (текст 1:1).

## 2. Верификация

- `ruff`, `mypy`, `policy_check`.
- Юнит: envelope-ошибка + текст в чанках → текст возвращается, ошибки нет;
  только envelope-ошибка → она же и кидается.
- Живой вызов за деньги — только по команде владельца.
- Коммит/пуш — после ручной проверки владельцем.
