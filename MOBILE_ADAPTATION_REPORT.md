# Мобильная адаптация WebVideoPipeline

## Результат

Ветка `mobile-test-20261105` от `origin/test` (`59f16e31eb63fd9556598682f592c5ec048f8095`), подготовленная для публикации в GitHub по просьбе пользователя. Исходная housepc не переключалась, данные и backend не менялись. Merge в test/main и deploy не выполнялись.

- Мобильная навигация и проекты открываются в drawers с backdrop/Escape, выбор проекта закрывает список.
- Генерация и GPT: история открывается отдельно, формы и popup ограничены экраном.
- Чаты, диалоги и формы входа не выходят за узкий экран; floating AI не перекрывает студию при первом открытии на телефоне.
- Добавлены изолированные mobile Playwright тесты с read-only mock API; внешняя сеть и мутации блокируются.

## Проверка

- `npm.cmd run typecheck` — PASS.
- `npm.cmd run build` — PASS (Next 15.5.18).
- `npx.cmd playwright test -c e2e/mobile/playwright.config.ts` — **40 passed**, 32.3s; 320/390/768/1440px. Формы, студия, navigation/projects, new-project dialog, dock chat с длинным markdown, Generation/GPT, landing и profile.
- `git diff --check` — PASS.
- Независимый review mobile-qa: блокеров в diff не выявлено.
- `npm.cmd run test:unit` — 51 passed, 2 failed: существующие extensionless imports orchestratorActionCards в orchestratorSession.ts не разрешаются Node 24. Эти lib-файлы не изменены (подтверждено review).

## Изменённые файлы

- web/src/app/globals.css
- web/src/app/page.tsx
- web/src/components/auth/auth-pages.tsx
- web/src/components/canvas/flow-canvas.tsx
- web/src/components/gpt/gpt-workspace.tsx
- web/src/components/home/home-landing.tsx
- web/src/components/orchestrator/orchestrator-panel.tsx
- web/src/components/outsee/outsee-create-workspace.tsx
- web/src/components/shell/app-shell.tsx
- web/src/components/shell/topbar.tsx
- web/src/components/shell/use-mobile-layout.ts (новый)
- web/src/components/sidebar/project-sidebar.tsx
- web/src/components/studio/studio-workspace.tsx
- web/src/components/ui/dialog.tsx
- web/e2e/mobile/: playwright.config.ts, mobile.spec.ts, fixtures.mjs, static-server.mjs, README.md, .gitignore (новые)

## Границы приёмки

Реальный iOS/Android, клавиатура и live авторизованный backend не проверены. Canvas с выбранным проектом и сложные node-specific панели не покрыты пустыми mock fixtures. Скриншоты сохранены тестами, но визуально не просмотрены: модель не поддерживает image input. Это проверенная базовая адаптация основных поверхностей, не обещание проверки каждого экрана сайта.

Версия UI поднята штатным скриптом до v544; после bump повторно прошли typecheck/build. Локальная сборка лежит в web/out, HTML-отчёт Playwright — web/e2e/mobile/artifacts/report/index.html (артефакты в git не входят). Публикация отдельной ветки не запускает Release: workflow реагирует на main/test. VPS недоступен по HTTPS/SSH на момент проверки, deploy не выполнялся. Для проверки на v2 ещё нужен review/merge в test и штатный deploy после восстановления VPS.
