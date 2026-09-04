# Как здесь работать

Три документа, по порядку важности:

1. **`docs/constitution.md`** — железные правила кода, архитектуры и
   безопасности. Меняются только через ADR.
2. **`docs/ENGINEERING-POLICY.md`** — что обязательно в процессе: когда
   нужна спека, что значит «готово», как идёт релиз, откат, инцидент,
   долг. У каждого правила указан машинный исполнитель.
3. **`docs/RELEASE-PROCESS.md`** — как выполнить шаги руками, с граблями.

## Минимальный цикл

```sh
git config core.hooksPath .githooks          # один раз на клон
git checkout -b feat/<тема>                  # в main напрямую нельзя
# …правки…
.venv/bin/ruff check --fix . && .venv/bin/ruff format .
.venv/bin/python -m mypy app/ --ignore-missing-imports
.venv/bin/pytest -q tests/<затронутые>.py
git commit                                   # хуки прогонят гейт сами
scripts/preflight.sh                         # полный гейт до пуша
```

Правки фронта — плюс смоук: `cd web && npm run build && cd .. &&
scripts/smoke_studio.sh <копия-базы>`.

Релиз: слить ветку в `main`, затем `scripts/release.sh`. Откат:
`scripts/rollback.sh`.

## Что остановит

- Красный гейт — чинится, не обходится (`--no-verify` запрещён).
- Сообщение коммита не по конвенции `тип(область): суть`.
- Контрактное изменение без спеки в `docs/openspec/changes/`.
- Рост числа исключений в гейтах без строки в `docs/DEBT.md`.

Если правило мешает делу — это повод завести ADR, а не обойти правило.
