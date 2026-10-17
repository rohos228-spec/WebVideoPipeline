import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './specs',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://127.0.0.1:8765',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    locale: 'ru-RU',
  },
  // Бэкенд поднимается заранее (uvicorn app.web.api:create_app --factory --port 8765),
  // фронт собран в web/out и раздаётся бэкендом. См. ci.yml.
});
