import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.STUDIO_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    // Вход отдельным проектом-зависимостью: он выполняется один раз и кладёт
    // состояние в e2e/.auth/user.json, откуда его берут все спеки. Логиниться
    // внутри каждого теста значило бы тратить argon2-проверку на каждый —
    // это десятки миллисекунд по построению, а не по недосмотру.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/user.json" },
      dependencies: ["setup"],
    },
  ],
});
