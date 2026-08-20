# Delta: checks-cascade (stage-0-security-hygiene)

## ADDED Requirements

### Requirement: Проверка без провайдера не зеленеет (fail-closed)

Без API-ключа (`settings.gpt_api_enabled` = false) check-роли (`review`,
`gate`, `compare`, `check_mode=True`) SHALL завершаться ошибкой, а не
stub-вердиктом `pass`. Stub-путь для проверок доступен только по явному
опт-ину `VP_ALLOW_STUB_CHECKS=1` (dev/tests).

#### Scenario: Нет ключа в проде

- **WHEN** `run_operator_api` вызван с check-ролью, ключ не задан, опт-ина нет
- **THEN** поднимается `RuntimeError` с пометкой fail-closed; вердикт не
  создаётся, гейт не проходится

#### Scenario: Dev/tests с опт-ином

- **WHEN** то же, но `VP_ALLOW_STUB_CHECKS=1`
- **THEN** прежнее stub-поведение (детерминированный вердикт по маркерам)

#### Scenario: Генеративная роль без ключа

- **WHEN** `run_operator_api` вызван с не-check ролью без ключа
- **THEN** dev-stub вывода работает как раньше (fail-open не опасен — это
  не вердикт качества)
