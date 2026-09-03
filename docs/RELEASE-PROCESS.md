# Регламент: изменение → проверка → релиз

Порядок, в котором изменение доходит до прода заказчика. Всё, что ниже,
уже автоматизировано гейтом (`.claude/verify.json`), CI и Release; регламент
описывает, **в какой последовательности** это включается и что делать
руками. Заведён 2026-09-04 по итогам переноса форка заказчика
(`docs/openspec/changes/merge-customer-fork/`).

## 0. Карта

| Ярус | Где | Что гоняет | Когда |
|---|---|---|---|
| turn | Stop-хук агента | ruff, ruff-format, mypy, goldens, prompt-contract | после каждого хода агента |
| commit | pre-commit | + secrets, tests-touched (`scripts/pytest_touched.py`) | `git commit` |
| push | pre-push | + pytest-ratchet (полная суита с покрытием), cov-ratchet, diff-cov, migrations, **rls (нужен Postgres)**, api-surface, ai-pack | `git push` |
| CI | `.github/workflows/ci.yml` | зеркало гейта + RLS на живом Postgres + Фронт (tsc, hex-гейт, next build) | push / PR |
| Release | `.github/workflows/release.yml` | CI как гейт → образ в GHCR → ssh на VPS, деплой **дайджестом** | push в `main` |

Обход хуков (`--no-verify`, `core.hooksPath`) закрыт guard'ом агента.
Гасит гейт только человек, и это исключение, а не путь.

## 1. Изменение

1. Ветка от `main`: `git checkout -b <тема>`. На `main` напрямую не коммитим.
2. Крупное изменение — сначала спека: `docs/openspec/changes/<id>/proposal.md`
   + `tasks.md` (образец — `merge-customer-fork`). Решения владельца —
   в спеку с датой, не в чат.
3. Чужой код (форк заказчика, порт из другого репо):
   - **BOM**: их файлы приходят с UTF-8 BOM, `ruff format` на нём падает
     с паникой. Снимать до коммита: `python3 -c "..."` или `sed -i '1s/^\xEF\xBB\xBF//'`.
   - Файлы, взятые через `git checkout <ref> -- <path>`, попадают в индекс
     минуя `git diff` — линтить **staged**: `ruff check --fix $(git diff --cached --name-only | grep '\.py$')`.
   - Истории без общего предка сливаются только с явной базой:
     `git merge-tree --write-tree --merge-base=<base> main <theirs>`;
     чистые файлы берутся из полученного дерева (`git show <tree>:<path>`),
     конфликтные — `git diff <base> <theirs> -- <file> | git apply -3`.
4. Фронт: `cd web && npx tsc --noEmit -p . && npm run build`. Hex-гейт —
   граница по каталогам (`ci.yml`, job Фронт): каталоги студии заказчика
   исключены, наше (`app/`, `components/ui`, `editors`, `pipeline`, `chat`,
   `stage-*`, `project-*`) — строго.

## 2. Проверка перед коммитом

```sh
.venv/bin/ruff check --fix . && .venv/bin/ruff format .
.venv/bin/python -m mypy app/ --ignore-missing-imports
.venv/bin/pytest -q -p no:cacheprovider tests/<затронутые>.py
```

`git commit` сам прогонит commit-ярус. Красное — чинить, не обходить.
Если тест был красным на `main` **до** изменения (ratchet это знает),
но упал в tests-touched — приводить тест к факту, с комментарием почему.

## 3. Смоук студии (обязателен для фронта и маршрутов под него)

Без метки сессии — headless Chromium, не agent-browser:

```sh
cd web && npm run build && cd ..
scripts/smoke_studio.sh <sqlite-копия> [project_id]   # скриншоты в /tmp/vp-smoke
```

Копию базы снять с прода один раз: `ssh studio 'docker cp studio-app-1:/app/data/state.db /tmp/state.db'`
и `scp`. Смоук проверяет: тему через инспектор (сохранение в API), мастер
проекта, «Генерацию», чат, меню съёмки, мета-агент, что лист `/` не
перекрашен, и ноль ошибок консоли. Ключи провайдеров не нужны.

## 4. Push (гейт push-яруса)

1. **Поднять Postgres** для `rls`: `podman start vp-pg` (иначе ярус
   красный на `connection refused`).
2. Push долгий (полная суита с покрытием, 5–10 мин). Из сессии агента —
   **отвязанным процессом**, фоновые задачи харнесса гибнут:
   ```sh
   setsid nohup sh -c 'git push origin <ветка> > /tmp/push.log 2>&1; echo PUSH_EXIT=$? >> /tmp/push.log' &
   ```
   Итог читать из лога.
3. Красное:
   - `pytest-ratchet: НОВЫЕ падения` — регрессия, чинить.
   - `cov-ratchet: покрытие просело` — тесты; осознанно — `scripts/coverage_ratchet.py --update` с записью в спеку.
   - `diff-cov: изменённый код без тестов` — тесты на изменённые строки.
     Исключение файла в `verify.json` (`--exclude`) — **только решением
     владельца и только как долг** (см. §6).
   - `rls` — см. п.1.

## 5. Релиз

1. PR ветки в `main` (или fast-forward, если ветка линейна). CI на ветке
   должен быть зелёным до слияния.
2. Push в `main` запускает Release. Следить без gh CLI:
   ```sh
   python3 scripts/watch_release.py <sha>      # печатает джобы и RUN_DONE <итог>
   ```
   Типовые красноты: gitleaks на новых файлах (демо-строки, имена моделей)
   — отпечаток в `.gitleaksignore` (`<commit>:<file>:<rule>:<line>`)
   отдельным коммитом, хеш коммита с находкой не меняется.
3. Проверка прода после `RUN_DONE success`:
   ```sh
   ssh studio 'docker ps --format "{{.Names}} {{.Status}}" | grep studio-app; \
     docker exec studio-app-1 sh -c "cat /app/STUDIO_REV 2>/dev/null; ls /app/web/out/_next/static/chunks/app/pipeline/"'
   ```
   Контейнер `healthy`, rev = задеплоенный sha, чанк `/pipeline` свежий.
   Healthcheck снаружи: `/api/health` через Caddy (порт 8000 на хосте не
   проброшен, `curl` в него даст 000 — это не ошибка).
4. Заказчику: адрес `/pipeline?project=N` и Ctrl+Shift+R (статика кэшируется).
5. Известное: в логе старта прода `startup maintenance failed: Read-only
   file system: /app/prompts/.history` — промты бинд-маунтом только для
   чтения, к выкладке не относится.

## 6. Тест-долг

Реестр — `docs/openspec/changes/merge-customer-fork/proposal.md` §8 и
`_test_debt` в `verify.json` рядом с `diff-cov`. Правило: список
исключений только уменьшается. Погасить долг по файлу = написать тест на
его изменённые строки, убрать файл из `--exclude`, прогнать
`scripts/diff_coverage.py --lcov coverage/lcov.info --base origin/main`
без исключения — зелёный.

## 7. Чего не делать

- Не пушить в `main` фронт без смоука §3: tsc и build пропускают «кнопка
  зовёт адрес, которого нет» (для этого `tests/test_frontend_api_paths.py`
  и `test_front_api_surface.py`, но они не видят пропсов и кликов).
- Не давать одному ревью-агенту 30 файлов диффа: два таких зависли на
  4 часа. Делить по файлам.
- Не читать `.env` и не коммитить `web/out`, `.env.*`, лаунчеры.
