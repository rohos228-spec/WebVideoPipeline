import { expect, test as base, type Locator, type Page, type TestInfo } from "@playwright/test";

// Every browser HTTP request is confined to this newly-owned fixture server.
// Every WebSocket is intercepted (same approach as orchestrator/studio-stubs.ts).
const test = base.extend<{ isolatedNetwork: void }>({
  isolatedNetwork: [async ({ context, baseURL }, use) => {
    const origin = new URL(baseURL!).origin;
    const mutations: string[] = [];
    const errors: string[] = [];
    context.on("page", (page) => page.on("pageerror", (error) => errors.push(error.message)));
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin) return route.abort("blockedbyclient");
      if (request.method() !== "GET" && request.method() !== "HEAD") {
        mutations.push(`${request.method()} ${url.pathname}`);
        return route.fulfill({ status: 405, contentType: "application/json", body: '{"detail":"Blocked by mobile test"}' });
      }
      return route.continue();
    });
    await context.routeWebSocket(/.*/, () => {});
    await use();
    expect(mutations, "Layout tests must never request writes, login, payment or generation").toEqual([]);
    expect(errors, "No unhandled browser errors").toEqual([]);
  }, { auto: true }],
});

const isNarrow = (page: Page) => page.viewportSize()!.width < 768;

async function withinViewport(page: Page, locator: Locator, vertical = true) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  expect(box, "Element must have a rendered bounding box").not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(box!.x, "Element starts outside the left edge").toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width, "Element extends beyond the right edge").toBeLessThanOrEqual(viewport.width + 1);
  if (vertical) {
    expect(box!.y, "Element starts above the viewport").toBeGreaterThanOrEqual(-1);
    expect(box!.y + box!.height, "Element ends below the viewport").toBeLessThanOrEqual(viewport.height + 1);
  }
}

async function noPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    width: window.innerWidth,
    root: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(dimensions.root, "Root scrollWidth exceeds viewport").toBeLessThanOrEqual(dimensions.width + 1);
  expect(dimensions.body, "Body scrollWidth exceeds viewport").toBeLessThanOrEqual(dimensions.width + 1);
}

async function capture(page: Page, info: TestInfo, name: string) {
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: "disabled" });
}

async function openStudio(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("orch-dock")).toHaveAttribute("data-open", "false");
  if (isNarrow(page)) await expect(page.getByRole("button", { name: "Открыть навигацию", exact: true })).toBeVisible();
  else await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeVisible();
}

async function openProjects(page: Page) {
  if (isNarrow(page)) await page.getByRole("button", { name: "Открыть проекты", exact: true }).click();
  await expect(page.locator("#studio-projects-sidebar")).toBeVisible();
}

test("студия: навигация и проекты доступны без горизонтальной прокрутки", async ({ page }, info) => {
  await openStudio(page);
  await noPageOverflow(page);
  if (isNarrow(page)) {
    const trigger = page.locator('button[aria-controls="studio-navigation"]');
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("navigation", { name: "Основная навигация" })).toBeHidden();
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await withinViewport(page, page.getByRole("navigation", { name: "Основная навигация" }));
    await withinViewport(page, page.getByRole("button", { name: "Закрыть навигацию", exact: true }));
    await capture(page, info, "navigation-open");
    await page.keyboard.press("Escape");
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await page.getByRole("button", { name: "Закрыть навигацию", exact: true }).click();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
  }
  await openProjects(page);
  await withinViewport(page, page.locator("#studio-projects-sidebar"));
  await withinViewport(page, page.getByRole("button", { name: "Новый проект", exact: true }));
  await noPageOverflow(page);
  await capture(page, info, "projects-open");
  if (isNarrow(page)) {
    await page.keyboard.press("Escape");
    await expect(page.locator("#studio-projects-sidebar")).toBeHidden();
    await openProjects(page);
    await page.getByRole("button", { name: "Закрыть проекты", exact: true }).click();
    await expect(page.locator("#studio-projects-sidebar")).toBeHidden();
  }
});

test("диалог нового проекта помещается в экран; создание не запускается", async ({ page }, info) => {
  await openStudio(page);
  await openProjects(page);
  await page.getByRole("button", { name: "Новый проект", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Новый проект" });
  await withinViewport(page, dialog);
  await noPageOverflow(page);
  await capture(page, info, "new-project");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("чат: длинный ответ не растягивает окно, поле и сворачивание доступны", async ({ page }, info) => {
  await openStudio(page);
  if (isNarrow(page)) await page.getByTestId("orch-dock-fab").click();
  else await page.getByTestId("orch-dock-toggle").click();
  const dock = page.getByTestId("orch-dock");
  await expect(dock).toHaveAttribute("data-open", "true");
  const chat = page.getByTestId("studio-orch-chat");
  await expect(chat).toContainText("Мобильная проверка:");
  await withinViewport(page, dock);
  const composer = chat.getByTestId("orch-composer");
  await withinViewport(page, composer);
  await composer.fill("Черновик — не отправлять и не запускать генерации");
  await withinViewport(page, chat.getByTestId("orch-send"));
  await noPageOverflow(page);
  // A clipped body alone could conceal an oversized fixed panel, so check key boxes too.
  await withinViewport(page, dock.getByTestId("orch-dock-toggle"));
  await capture(page, info, "chat-long-markdown");
  await dock.getByTestId("orch-dock-toggle").click();
  await expect(dock).toHaveAttribute("data-open", "false");
});

test("лендинг: первый экран и переход ко входу", async ({ page }, info) => {
  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await noPageOverflow(page);
  await capture(page, info, "landing");
  if (page.viewportSize()!.width < 1024) {
    await page.getByRole("button", { name: "Меню", exact: true }).click();
  }
  const login = page.viewportSize()!.width < 1024
    ? page.locator("#home-mobile-menu").getByRole("link", { name: "Войти", exact: true })
    : page.locator("header").getByRole("link", { name: "Войти", exact: true });
  await login.scrollIntoViewIfNeeded();
  await withinViewport(page, login);
  await login.click();
  await expect(page).toHaveURL(/\/login(?:[/?#]|$)/);
  await expect(page.getByRole("heading", { name: "Вход в VIA" })).toBeVisible();
  await noPageOverflow(page);
});

for (const [url, title] of [["/login", "Вход в VIA"], ["/register", "Создать аккаунт"], ["/forgot-password", "Восстановление пароля"]] as const) {
  test(`форма ${url}: поля и кнопки не выходят за экран`, async ({ page }, info) => {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    await noPageOverflow(page);
    const fields = page.locator("main input:not([type=checkbox])");
    for (const field of await fields.all()) {
      await field.scrollIntoViewIfNeeded();
      await withinViewport(page, field);
    }
    const submit = page.locator("main button[type=submit]");
    await submit.scrollIntoViewIfNeeded();
    await withinViewport(page, submit);
    await capture(page, info, url.slice(1));
  });
}

for (const workspace of [
  { nav: "Генерация", root: "[data-generation-workspace]", openHistory: "Открыть историю генераций", closeHistory: "Закрыть историю генераций", history: "#generation-history", close: "Закрыть генерацию" },
  { nav: "Чат", root: "[data-gpt-workspace]", openHistory: "Открыть историю чатов", closeHistory: "Закрыть историю чатов", history: "#gpt-history", close: "Закрыть GPT чат" },
]) {
  test(`${workspace.nav}: workspace и история доступны без генераций`, async ({ page }, info) => {
    await openStudio(page);
    if (isNarrow(page)) await page.getByRole("button", { name: "Открыть навигацию", exact: true }).click();
    await page.getByRole("navigation", { name: "Основная навигация" }).getByRole("button", { name: workspace.nav, exact: true }).click();
    const root = page.locator(workspace.root);
    await withinViewport(page, root);
    await noPageOverflow(page);
    await withinViewport(page, root.getByRole("button", { name: workspace.close, exact: true }));
    if (isNarrow(page)) {
      await root.getByRole("button", { name: workspace.openHistory, exact: true }).click();
      await withinViewport(page, root.locator(workspace.history));
      await root.getByRole("button", { name: workspace.closeHistory, exact: true }).click();
      await expect(root.locator(workspace.history)).toBeHidden();
    }
    await capture(page, info, `${workspace.nav}-workspace`);
    await root.getByRole("button", { name: workspace.close, exact: true }).click();
    await expect(root).toBeHidden();
  });
}

 test("профиль: карточки и переключение разделов без записи данных", async ({ page }, info) => {
  await page.goto("/profile");
  await expect(page.getByRole("heading", { name: "Тестовый пользователь" })).toBeVisible();
  await noPageOverflow(page);
  await capture(page, info, "profile-overview");
  for (const title of ["Проекты", "Подключения и ключи", "Настройки"]) {
    const tab = page.getByRole("button", { name: title, exact: true }).first();
    await tab.scrollIntoViewIfNeeded();
    await tab.click();
    await noPageOverflow(page);
  }
  await capture(page, info, "profile-settings");
});
