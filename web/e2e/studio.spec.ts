import { test, expect } from "@playwright/test";

/**
 * Дым по интерфейсу Студии: здоровье бэкенда, статус авторизации,
 * список проектов и стадии конвейера.
 * Гоняется по живой студии (STUDIO_URL, по умолчанию 127.0.0.1:8765).
 */

test.describe("Студия", () => {
  test("бэкенд жив и отдает статус здоровья", async ({ request }) => {
    const health = await request.get("/api/health");
    expect(health.ok()).toBeTruthy();
    const body = await health.json();
    expect(body.status).toBe("ok");
  });

  test("статус авторизации доступен", async ({ request }) => {
    const status = await request.get("/api/auth/status");
    expect(status.ok()).toBeTruthy();
    const body = await status.json();
    expect(body).toHaveProperty("auth_required");
  });

  test("страница студии загружается без критических ошибок", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();

    // Проверяем, что форма не зависла в падении
    await expect(page.locator("body")).toBeVisible();
  });

  test("список проектов возвращает корректную структуру", async ({ request }) => {
    const res = await request.get("/api/projects");
    expect(res.ok()).toBeTruthy();
    const list = await res.json();
    expect(Array.isArray(list)).toBeTruthy();

    if (list.length > 0) {
      const first = list[0];
      expect(first).toHaveProperty("id");
      expect(first).toHaveProperty("name");
    }
  });

  test("стадии проекта возвращаются с сервера", async ({ request }) => {
    const listRes = await request.get("/api/projects");
    const list = await listRes.json();
    test.skip(list.length === 0, "в студии нет ни одного ролика для проверки стадий");

    const res = await request.get(`/api/projects/${list[0].id}/stages`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("stages");
    expect(Array.isArray(body.stages)).toBeTruthy();
    for (const s of body.stages) {
      expect(s).toHaveProperty("id");
      expect(["locked", "ready", "running", "done", "failed", "paused", "pending"]).toContain(s.state);
    }
  });
});
