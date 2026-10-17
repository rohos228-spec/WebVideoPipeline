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
    {
      name: "chromium",
      // full-HD: the housepc canvas fits ~20 nodes; at 720p node triggers are too small to click
      use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } },
    },
  ],
});
