import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.MOBILE_WEB_PORT ?? "3197");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid MOBILE_WEB_PORT");
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: here,
  testMatch: "mobile.spec.ts",
  outputDir: path.join(here, "artifacts", "results"),
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: path.join(here, "artifacts", "report"), open: "never" }]],
  use: {
    baseURL,
    browserName: "chromium",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    colorScheme: "dark",
    reducedMotion: "reduce",
    serviceWorkers: "block",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    ...(process.env.MOBILE_CHROME_PATH ? { launchOptions: { executablePath: process.env.MOBILE_CHROME_PATH } } : {}),
  },
  projects: [320, 390, 768, 1440].map((width) => ({
    name: `chromium-${width}`,
    use: { viewport: { width, height: width >= 768 ? 900 : 844 }, hasTouch: width < 768 },
  })),
  webServer: {
    command: `"${process.execPath}" "${path.join(here, "static-server.mjs")}"`,
    cwd: path.resolve(here, "../.."),
    url: `${baseURL}/__mobile_health`,
    reuseExistingServer: false,
    timeout: 15_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    env: { MOBILE_WEB_PORT: String(port) },
  },
});
