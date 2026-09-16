# Tasks: studio-run-checks

Порядок: A → B → C.

## A. Разведка (без кода)

- [x] A.1 Бэкенд сверен: `run_project_step(dry_run)` (`routers/projects.py:514`),
      `vision_decision` (`routers/projects.py:568`),
      `validate_project_step_dry_run` + `FORBIDDEN_DRY_RUN_STEPS`
      (`studio_dry_run.py:16`), `apply_vision_decision`
      (`vision_check_loop.py:1208`), `pause_reason.code` в API проекта.
- [x] A.2 Фронт сверен: `api.runProjectStep({dryRun})` умеет (`lib/api.ts`),
      вызовов с `dryRun: true` нет; `vision-decision` в `web/src` нет;
      точки вставки: `node-studio.tsx:375` (`runStep`), `stage-card.tsx`
      (`NodeRow`), `hitl-banner.tsx` + `waiting_hitl` в `stage-card.tsx:24`.

## B. Код

- [x] B.1 `node-studio.tsx`: кнопка «Проверить» + показ `would_status/warnings`
      через тост `step_dry_run_ok` (скрыта для hero/items/img/video/audio/music).
- [x] B.2 `stage-card.tsx`: dry_run-кнопка в `NodeRow` по ховеру
      (`onDryRunNode`, те же forbidden-шаги).
- [x] B.3 HITL-зона: новый `hitl/vision-pause-banner.tsx` («Ещё круги» /
      «Принять как есть»), только при `paused + vision_rounds_exhausted`;
      смонтирован в `project-view.tsx` и `studio-workspace.tsx` (оверлей).
      Плюс API-слой: `stage-api.runStep(dryRun)` + `visionDecision`,
      `lib/api.visionDecision`, тип `BusEvent step_dry_run_ok`, тост в
      `use-bus.ts`, `meta` в типе `Project`. Бэкенд не менялся.
- [x] B.4 `STUDIO_VERSION` бамп → 517 (`scripts/bump_studio_version.py`).

## C. Гейт

- [x] C.1 `tsc --noEmit` чисто, `pnpm build` зелено, hex-гейт (новых цветов нет).
- [ ] C.2 Смоук-кейсы §2 спеки — за владельцем на живом Studio
      (dry_run plan=200/img=400; vision-кнопки только в нужной паузе).
- [ ] C.3 Существующие e2e/guardian — прогнать перед мержем.
- [ ] C.4 Отчет + дифф владельцу. Без коммита до команды.
