import { test, expect } from "@playwright/test";
import { pickProjectId, selectProjectInSidebar } from "./helpers";

/**
 * Чеклист §3 FULL-VERIFICATION — оболочка UI (без живых генераций).
 */
test.describe("Full UI shell", () => {
  // housepc design: left rail (Проекты/Логи/API) instead of main's topbar "Промты".
  test("rail: Проекты, Логи, API", async ({ page, request }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Проекты", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Логи", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Логи", exact: true }).click();
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 8000 });
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("link", { name: "API", exact: true }).or(page.getByRole("button", { name: "API", exact: true })).first(),
    ).toBeVisible();
    const health = await request.get("/api/health");
    expect(health.ok()).toBeTruthy();
  });

  test("sidebar: search, new project wizard open", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByPlaceholder("Поиск проектов…")).toBeVisible();
    await page.getByRole("button", { name: "Новый проект" }).click();
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 8000 });
    await page.keyboard.press("Escape");
  });

  test("canvas toolbar: save graph control exists", async ({ page, request }) => {
    const id = await pickProjectId(request);
    await selectProjectInSidebar(page, request);
    // housepc toolbar: workflows (WF) save/load + "Добавить" node menu.
    await expect(
      page.getByRole("button", { name: /Сохранить граф|^WF$/ }).first(),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByRole("button", { name: "Добавить", exact: true }).or(page.locator("select").filter({ hasText: "+ Нода" })).first(),
    ).toBeVisible();
  });

  test("inspector opens with project selected", async ({ page, request }) => {
    await selectProjectInSidebar(page, request);
    await expect(
      page.locator("aside").getByText("Инспектор", { exact: true }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("run bar: Create Run or Restart visible", async ({ page, request }) => {
    await selectProjectInSidebar(page, request);
    await expect(
      page.getByRole("button", { name: /Создать Run|Перезапустить|Продолжить/ }).first(),
    ).toBeVisible({ timeout: 20_000 });
  });
});
