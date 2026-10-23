import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { OPERATOR_TOKEN, stubStudioApi, stubStudioSockets } from "./studio-stubs";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

const RUN = Date.now() % 1_000_000;
let projectSerial = 0;

const CATALOG = [
  { id: "project.status", title_ru: "Статус проекта" },
  { id: "report.nodes", title_ru: "Отчёт по нодам" },
];

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

function nextProject(): number {
  projectSerial += 1;
  return 500_000_000 + RUN * 10 + projectSerial;
}

async function openStudio(page: Page, scenario: string, project: number) {
  await page.setViewportSize({ width: 1523, height: 784 });
  await page.addInitScript(
    ([pid]) => {
      localStorage.setItem("vp-studio-selected-project-id", JSON.stringify(pid));
      localStorage.setItem("vp-orch-dock-expanded", JSON.stringify(true));
      localStorage.setItem("vp-orch-dock-open", "true");
      localStorage.setItem("vp-orch-dock-tab", JSON.stringify("chat"));
      localStorage.setItem("vp-orch-dock-scope", JSON.stringify("project"));
      localStorage.removeItem("vp-orch-api-token");
    },
    [project] as const,
  );
  await stubStudioApi(page, { operatorToken: OPERATOR_TOKEN, catalog: CATALOG });
  await stubStudioSockets(page);
  await page.goto(`/?scenario=${scenario}`);
  await expect(page.getByTestId("studio-orch-chat")).toHaveAttribute("data-session", `studio-p${project}`);
}

async function sendChat(page: Page, text: string) {
  const chat = page.getByTestId("studio-orch-chat");
  await chat.getByTestId("orch-composer").fill(text);
  await chat.getByTestId("orch-send").click();
}

async function assistantTexts(page: Page) {
  return page
    .getByTestId("studio-orch-chat")
    .locator("[data-testid='orch-line'][data-role='assistant'] [data-line-text]")
    .evaluateAll((nodes) => nodes.map((node) => (node.textContent ?? "").trim()));
}

async function inView(page: Page, testId: string) {
  const chat = page.getByTestId("studio-orch-chat");
  const box = await chat.getByTestId(testId).first().boundingBox();
  const area = await chat.getByTestId("orch-transcript").boundingBox();
  if (!box || !area) return false;
  return box.y >= area.y - 1 && box.y < area.y + area.height;
}

test.describe("чат: карточки, markdown, одно сообщение об итоге", () => {
  test("вторая карточка подтверждения в той же задаче видна после первой", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "twoappr", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "сделай отчёт по нодам");
    const card = chat.getByTestId("orch-approval");
    await expect(card).toHaveCount(1);
    await expect(card).toHaveAttribute("data-approval-id", /-1$/);
    await expect(card.getByTestId("orch-approval-step")).toContainText("Отчёт по нодам проекта");
    await expect(card.getByTestId("orch-approval-cost")).toContainText("бесплатно");
    await expect(card.getByTestId("orch-approval-cost")).toHaveAttribute("data-unknown", "false");
    await expect(chat.getByTestId("orch-task-status")).toContainText("ждёт подтверждения");
    await expect(chat.getByTestId("orch-approval-jump")).toBeVisible();
    await saveShot(page, "1-first-card");
    await card.getByTestId("orch-approve").click();
    await expect(card).toHaveCount(1);
    await expect(card).toHaveAttribute("data-approval-id", /-2$/);
    await expect(card).toBeVisible();
    await expect(card.getByTestId("orch-approve")).toBeEnabled();
    await expect(card.getByTestId("orch-approval-step")).toContainText("Перегенерировать картинки сцены 2");
    const cost = card.getByTestId("orch-approval-cost");
    await expect(cost).toHaveAttribute("data-unknown", "true");
    await expect(cost).toContainText("неизвестна");
    await expect(card.getByTestId("orch-approval-risk")).toContainText("высокий");
    await expect(chat.getByTestId("orch-task-status")).toContainText("ждёт подтверждения");
    await expect(chat.getByTestId("orch-approval-jump")).toBeVisible();
    await page.waitForTimeout(300);
    await saveShot(page, "1-second-card");
    await chat.getByTestId("orch-transcript").evaluate((el) => {
      el.scrollTop = 0;
    });
    await chat.getByTestId("orch-approval-jump").click();
    await expect.poll(() => inView(page, "orch-approval")).toBe(true);
    await card.getByTestId("orch-approve").click();
    await expect(chat.getByTestId("orch-approval")).toHaveCount(0);
    await expect(chat.getByTestId("orch-approval-jump")).toHaveCount(0);
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: готово");
  });

  test("карточка не дошла до чата: плашка и «Обновить ленту» возвращает её", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "lostappr", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "смени модель ноды картинок");
    await expect(chat.getByTestId("orch-task-status")).toContainText("ждёт подтверждения");
    const lost = chat.getByTestId("orch-approval-lost");
    await expect(lost).toBeVisible();
    await expect(chat.getByTestId("orch-approval")).toHaveCount(0);
    await saveShot(page, "1-lost-card");
    await lost.getByTestId("orch-approval-resync").click();
    const card = chat.getByTestId("orch-approval");
    await expect(card).toHaveAttribute("data-approval-id", /-1$/);
    await expect(card.getByTestId("orch-approval-step")).toContainText("Сменить модель ноды картинок");
    await expect(lost).toHaveCount(0);
    await expect(chat.getByTestId("orch-transcript")).toContainText("смени модель ноды картинок");
    await card.getByTestId("orch-approve").click();
    await expect(chat.getByTestId("orch-approval")).toHaveCount(0);
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: готово");
    await expect(chat.getByTestId("orch-transcript")).toContainText("Готово: модель ноды сменена.");
    await page.waitForTimeout(1800);
    await expect(lost).toHaveCount(0);
  });

  test("markdown в ответе: жирный, списки, код; без HTML-инъекций", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "markdown", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "что с проектом?");
    const line = chat.locator("[data-testid='orch-line'][data-role='assistant']").last();
    await expect(line).toContainText("плохая");
    const body = line.getByTestId("orch-assistant");
    await expect(body.locator("a", { hasText: "Подробнее" })).toBeVisible();
    await expect(body.locator("h2, h3, h4").first()).toHaveText("Что с проектом");
    await expect(body.locator("strong").first()).toHaveText("#13");
    await expect(body.locator("em").first()).toHaveText("осталось");
    await expect(body.locator("code").first()).toHaveText("node.run_step");
    await expect(body.locator("ul > li")).toHaveCount(3);
    await expect(body.locator("ul ul > li")).toHaveText(["клип 2 из 5"]);
    await expect(body.locator("ol > li")).toHaveText(["проверить кадры", "запустить монтаж"]);
    const pre = body.locator("pre");
    await expect(pre).toHaveCount(1);
    await expect(pre).toContainText("шаг: видео");
    await expect(pre).toContainText("статус: идёт");
    expect(await pre.evaluate((el) => getComputedStyle(el).overflowX)).toBe("auto");
    const text = (await body.textContent()) ?? "";
    expect(text).not.toContain("**");
    expect(text).not.toContain("```");
    expect(text).not.toMatch(/^- /m);
    await expect(body).toContainText("<script>");
    const link = body.locator("a", { hasText: "Подробнее" });
    await expect(link).toHaveAttribute("href", "https://example.com/docs");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);
    await expect(body.locator("a", { hasText: "плохая" })).toHaveCount(0);
    await expect(body.locator("a[href^='javascript']")).toHaveCount(0);
    await expect(chat.locator("script")).toHaveCount(0);
    await expect(chat.locator("img[src='x']")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __orchXss?: number }).__orchXss)).toBeUndefined();
    await line.scrollIntoViewIfNeeded();
    await saveShot(page, "2-markdown");
  });

  test("задача упала, есть итог сервера: одно сообщение, подробности свёрнуты", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "failsummary", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "покажи статус проекта");
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: ошибка — подробности в чате");
    const transcript = chat.getByTestId("orch-transcript");
    await expect(transcript).toContainText("Не получилось: сервер перезапускался");
    await page.waitForTimeout(300);
    await saveShot(page, "3-failed-with-summary");
    expect(await assistantTexts(page)).toHaveLength(1);
    await expect(transcript.locator("[data-testid='orch-task-outcome'][data-outcome='failed']")).toHaveCount(0);
    for (const bad of ["Сервер отказался", "Задача не выполнена", "внутренней ошибки", "внутренняя ошибка"]) {
      await expect(transcript).not.toContainText(bad);
    }
    const details = transcript.getByTestId("orch-task-details");
    await expect(details).toHaveCount(1);
    await expect(details.getByTestId("orch-task-details-body")).toHaveCount(0);
    await details.getByTestId("orch-task-details-toggle").click();
    await expect(details.getByTestId("orch-task-details-body")).toContainText("перезапускался");
    await saveShot(page, "3-failed-details-open");
  });

  test("пустой план и итог сервера: одно сообщение без плашки", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "emptysummary", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "сделай что-нибудь");
    const transcript = chat.getByTestId("orch-transcript");
    await expect(transcript).toContainText("Не получилось: подходящих действий не нашлось");
    await page.waitForTimeout(300);
    await saveShot(page, "4-empty-plan-with-summary");
    await expect(transcript.getByTestId("orch-empty-plan")).toHaveCount(0);
    await expect(transcript.getByTestId("orch-approval")).toHaveCount(0);
    await expect(transcript.locator("[data-testid='orch-task-outcome'][data-outcome='failed']")).toHaveCount(0);
    expect(await assistantTexts(page)).toHaveLength(1);
    // Пустой план — не ошибка, а серое «нужно уточнение».
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: нужно уточнение");
  });
});
