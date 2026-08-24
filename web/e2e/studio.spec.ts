import { test, expect } from "@playwright/test";

/**
 * Дым по новому интерфейсу: экран идеи, список роликов, семь стадий.
 * Гоняется по живой студии (STUDIO_URL, по умолчанию 127.0.0.1:8765).
 */

test.describe("Студия", () => {
  test("бэкенд жив, открывается экран идеи", async ({ page, request }) => {
    const health = await request.get("/api/health");
    expect(health.ok()).toBeTruthy();

    await page.goto("/");
    await page.evaluate(() => localStorage.removeItem("vp.last-project"));
    await page.reload();

    await expect(page.getByRole("heading", { name: "О чём ролик?" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Начать" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Новый ролик" })).toBeVisible();
  });

  test("у ролика семь стадий, цена и ровно одно активное действие", async ({ page, request }) => {
    const list = await (await request.get("/api/projects")).json();
    test.skip(list.length === 0, "в студии нет ни одного ролика");

    await page.goto("/");
    await page.locator("nav li button").first().click();

    const stages = page.locator("main section");
    await expect(stages).toHaveCount(7);
    await expect(stages.first().getByRole("heading")).toHaveText("Сценарий");

    // Цена шага — моноширинная строка «… кр» или «бесплатно».
    await expect(stages.first()).toContainText(/кр|бесплатно/);

    // Активная кнопка «Сгенерировать» не может быть больше одной: следующий
    // шаг ровно один, остальные либо готовы, либо закрыты.
    const runnable = page.getByRole("button", { name: "Сгенерировать" });
    const enabled = await runnable.evaluateAll((els) =>
      els.filter((e) => !(e as HTMLButtonElement).disabled).length,
    );
    expect(enabled).toBeLessThanOrEqual(1);
  });

  test("стадии приходят с сервера вместе с ценой", async ({ request }) => {
    const list = await (await request.get("/api/projects")).json();
    test.skip(list.length === 0, "в студии нет ни одного ролика");

    const res = await request.get(`/api/projects/${list[0].id}/stages`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.stages).toHaveLength(7);
    for (const s of body.stages) {
      expect(s).toHaveProperty("price_credits");
      expect(["locked", "ready", "running", "done", "failed", "paused"]).toContain(s.state);
    }
  });
});
