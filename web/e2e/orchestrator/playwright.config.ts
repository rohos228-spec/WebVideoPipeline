import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const webPort = process.env.ORCH_WEB_PORT ?? "3100";
const baseURL = process.env.ORCH_WEB_URL ?? `http://127.0.0.1:${webPort}`;
const mockPort = process.env.ORCH_MOCK_PORT ?? "8799";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  testIgnore: /r6\//,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    locale: "ru-RU",
    ...(process.env.ORCH_CHROME_PATH ? { launchOptions: { executablePath: process.env.ORCH_CHROME_PATH } } : {}),
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/orchestrator/mock-server.mjs",
      cwd: webRoot,
      port: Number(mockPort),
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      // Эта линия ставит зависимости npm (package-lock.json), не pnpm.
      command: `npx next dev --turbopack --port ${webPort}`,
      cwd: webRoot,
      port: Number(webPort),
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        ...process.env,
        NEXT_PUBLIC_ORCH_TASK_MODE: "1",
        NEXT_PUBLIC_ORCH_TEST: "1",
        NEXT_PUBLIC_ORCH_API: process.env.ORCH_MOCK_URL ?? `http://127.0.0.1:${mockPort}`,
      },
    },
  ],
});
