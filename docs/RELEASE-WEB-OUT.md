# web/out: как выпускается интерфейс студии

## Почему web/out лежит в git

У пользователей на ПК нет Node.js. Обновление студии (`STUDIO.cmd` → `scripts/studio.ps1` →
`scripts/StudioUpdateCore.ps1`) забирает готовый интерфейс прямо из git:

```
git checkout origin/<ветка> -- web/out web/STUDIO_VERSION
```

`Restore-Ui.ps1` и `Fix-StudioVersion160.ps1` делают то же самое, а `StudioUpdateCore.ps1`
сверяет версию, вшитую в `web/out/index.html`, с `web/STUDIO_VERSION`. Поэтому, как и в main,
`web/out` **коммитится** (в housepc его временно добавляли в `.gitignore`; это убрано).

## Как выпустить новую сборку интерфейса (одна команда)

```
powershell -ExecutionPolicy Bypass -File scripts\release_build_web.ps1            # собрать и застейджить
powershell -ExecutionPolicy Bypass -File scripts\release_build_web.ps1 -Commit    # + коммит (без push)
```

Скрипт ждёт `build.lock` на рабочем столе (одна сборка Next за раз), запускает `npm ci`
(если нет node_modules) и `npm run build`, проверяет `web/out/index.html`, прогоняет
`scripts/merge_checks/check_providers.py --out web/out` (если найдено имя провайдера — релиз
прерывается) и делает `git add -A web/out web/STUDIO_VERSION`. Push — только вручную.

## Как это получает пользователь

1. `STUDIO.cmd` → «Обновить»: `git fetch` + восстановление `web/out` из ветки.
2. Если после обновления `web/out/index.html` нет (например, dev-checkout без сборки), а Node.js
   установлен — `studio.ps1` сам запускает `npm install && npm run build` (пункт «Починить»).
3. Тот же автозапуск сборки теперь срабатывает и при обычном старте ([1] Запуск), если
   `web/out` отсутствует и есть npm; без npm выводится понятное сообщение.

## CI

`.github/workflows/ci.yml` собирает `web/out` сам и проверяет его `check_providers.py`;
закоммиченная сборка обновляется скриптом выше при каждом релизе интерфейса.
