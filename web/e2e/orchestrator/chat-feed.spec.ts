import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { OPERATOR_TOKEN, stubStudioApi, stubStudioSockets } from "./studio-stubs";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";
const RUN = Date.now() % 1_000_000;
let projectSerial = 0;

const CATALOG = [
  { id: "project.status", title_ru: "Статус проекта" },
  { id: "spend.summary", title_ru: "Сводка трат" },
  { id: "node.run_step", title_ru: "Запустить шаг проекта" },
];

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

function nextProject(): number {
  projectSerial += 1;
  return RUN * 100 + projectSerial;
}

async function openStudio(page: Page, scenario: string, project: number, expanded = true) {
  await page.setViewportSize({ width: 1523, height: 784 });
  await page.addInitScript(
    ([pid, big]) => {
      localStorage.setItem("vp-studio-selected-project-id", JSON.stringify(pid));
      localStorage.setItem("vp-orch-dock-expanded", JSON.stringify(big));
      localStorage.setItem("vp-orch-dock-open", "true");
      localStorage.setItem("vp-orch-dock-tab", JSON.stringify("chat"));
      localStorage.setItem("vp-orch-dock-scope", JSON.stringify("project"));
      localStorage.removeItem("vp-orch-api-token");
    },
    [project, expanded] as const,
  );
  await stubStudioApi(page, { operatorToken: OPERATOR_TOKEN, catalog: CATALOG });
  await stubStudioSockets(page);
  await page.goto(`/?scenario=${scenario}`);
  await expect(page.getByTestId("studio-orch-chat")).toBeVisible();
  await expect(page.getByTestId("studio-orch-chat")).toHaveAttribute("data-session", `studio-p${project}`);
}

async function sendChat(page: Page, text: string) {
  const chat = page.getByTestId("studio-orch-chat");
  await chat.getByTestId("orch-composer").fill(text);
  await chat.getByTestId("orch-send").click();
}

async function dropStream(page: Page, project: number, opts: { replayAll?: boolean; forgetTokens?: boolean }) {
  const res = await page.request.post(`${MOCK}/api/orchestrator/test/drop`, {
    headers: { "X-VP-Operator": OPERATOR_TOKEN },
    data: { session_id: `studio-p${project}`, replay_all: opts.replayAll === true, forget_tokens: opts.forgetTokens === true },
  });
  expect(res.ok()).toBe(true);
}

async function bubbles(page: Page) {
  return page
    .getByTestId("studio-orch-chat")
    .locator("[data-testid='orch-line']")
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        role: node.getAttribute("data-role") ?? "",
        text: (node.querySelector("[data-line-text]")?.textContent ?? "").trim(),
      })),
    );
}

test.describe("лента чата: порядок, дубли, итоги задач", () => {
  test("ответы не склеиваются и не дублируются при реплее", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "glue", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "что с проектом?");
    await expect(chat.getByTestId("orch-transcript")).toContainText("к докачке медиа.");
    await sendChat(page, "а что с видео?");
    await expect(chat.getByTestId("orch-transcript")).toContainText("дальше монтаж.");
    await page.waitForTimeout(400);
    await saveShot(page, "1-glue-live");
    const live = await bubbles(page);
    expect(live).toEqual([
      { role: "user", text: "что с проектом?" },
      { role: "assistant", text: "Проект #1 ждёт: осталось три файла к докачке медиа." },
      { role: "user", text: "а что с видео?" },
      { role: "assistant", text: "Проект #1 почти готов: шаг «Видео» закончен, дальше монтаж." },
    ]);

    await dropStream(page, project, { replayAll: true });
    await expect(chat.getByTestId("orch-reconnect")).toContainText("восстановлено");
    await page.waitForTimeout(300);
    expect(await bubbles(page)).toEqual(live);

    await dropStream(page, project, { forgetTokens: true });
    await page.reload();
    await expect(chat.getByTestId("orch-transcript")).toContainText("дальше монтаж.");
    await page.waitForTimeout(300);
    await saveShot(page, "1-glue-reload");
    expect(await bubbles(page)).toEqual(live);
    await expect(chat.getByTestId("orch-ack")).toHaveCount(0);
    await expect(chat.getByTestId("orch-task-status")).toContainText("нет задачи");
  });

  test("после перезагрузки вопрос стоит над ответом, локальная копия заменяется серверной", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "order", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "что сейчас с проектом и почему стоит?");
    await expect(chat.getByTestId("orch-transcript")).toContainText("ошибок нет");
    await page.waitForTimeout(300);
    const expected = [
      { role: "user", text: "что сейчас с проектом и почему стоит?" },
      { role: "assistant", text: "Сейчас проект #13 на шаге «Видео», ошибок нет." },
    ];
    expect(await bubbles(page)).toEqual(expected);
    await page.reload();
    await expect(chat.getByTestId("orch-transcript")).toContainText("ошибок нет");
    await page.waitForTimeout(300);
    await saveShot(page, "2-order-reload");
    expect(await bubbles(page)).toEqual(expected);
    await expect(chat.getByTestId("orch-ack")).toHaveCount(0);
  });

  test("задача упала на проверке: понятное сообщение в ленте и в шапке", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "taskfail", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "поправь общий промт сцен");
    await expect(chat.getByTestId("orch-task-status")).toContainText("ошибка");
    await page.waitForTimeout(200);
    await saveShot(page, "3-task-failed");
    const outcome = chat.getByTestId("orch-transcript").getByTestId("orch-task-outcome");
    await expect(outcome).toHaveCount(1);
    await expect(outcome).toHaveAttribute("data-outcome", "failed");
    await expect(outcome).toBeVisible();
    await expect(outcome).toContainText("Проверка плана не пропустила задачу");
    await expect(outcome).toContainText("Что сделать");
    for (const bad of ["validator_blocked", "whitelist", "edit_prompt_global"]) {
      await expect(outcome).not.toContainText(bad);
    }
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: ошибка — подробности в чате");
  });

  test("задача готова без итогового текста: «Готово» и результаты шагов", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "taskdone", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "собери сводку по проекту");
    const outcome = chat.getByTestId("orch-transcript").getByTestId("orch-task-outcome");
    await expect(outcome).toHaveAttribute("data-outcome", "done");
    await expect(outcome).toContainText("Готово");
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: готово");
    await outcome.getByTestId("orch-task-outcome-toggle").click();
    const results = outcome.getByTestId("orch-task-outcome-steps");
    await expect(results).toBeVisible();
    await expect(results).toContainText("Статус проекта");
    await expect(results).toContainText("12 кадров, 2 сцены без видео");
    await expect(results).toContainText("Потрачено $4.10");
    await saveShot(page, "3-task-done");
  });

  test("итоговое сообщение сервера не дублируется карточкой «Готово»", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "taskdonemsg", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "собери сводку по проекту");
    await expect(chat.getByTestId("orch-transcript")).toContainText("потрачено $4.10.");
    await page.waitForTimeout(300);
    await expect(chat.getByTestId("orch-task-outcome")).toHaveCount(0);
    const texts = (await bubbles(page)).filter((b) => b.role === "assistant").map((b) => b.text);
    expect(texts).toEqual(["Итог: в проекте #13 двенадцать кадров, потрачено $4.10."]);
  });

  test("промежуточное «Готово: шаг» не дублирует итог и прячется в «Подробности»", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "batchdone", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "создай два проекта: 3 факта о котах и о собаках");
    const summary = "Создал 2 проекта: «3 факта о котах» (#2), «3 факта о собаках» (#3).";
    await expect(chat.getByTestId("orch-transcript")).toContainText(summary);
    await page.waitForTimeout(300);
    const texts = (await bubbles(page)).filter((b) => b.role === "assistant").map((b) => b.text);
    expect(texts).toEqual([summary]);
    await expect(chat.getByTestId("orch-task-outcome")).toHaveCount(0);
    const details = chat.getByTestId("orch-step-details");
    await expect(details).toHaveCount(1);
    await expect(chat.getByTestId("orch-step-details-body")).toHaveCount(0);
    await details.getByTestId("orch-step-details-toggle").click();
    await expect(chat.getByTestId("orch-step-details-body")).toContainText("Готово: Создать пакет проектов по списку тем.");
    for (const bad of ["projects:", "settings", "hero_mode"]) {
      await expect(chat.getByTestId("orch-transcript")).not.toContainText(bad);
    }
  });

  test("отчёт xlsx: в итоге ссылка «Скачать отчёт (xlsx)»", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "reportlink", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "сделай отчёт по нодам в xlsx");
    const link = chat.getByRole("link", { name: "Скачать отчёт (xlsx)" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/api/projects/3/reports/nodes_report_3_20261020-030000.xlsx");
  });

  test("шапка показывает текущую задачу, а не прошлую ошибку", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "twotasks", project);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "сводка и правка промта");
    await expect(chat.getByTestId("orch-transcript").getByTestId("orch-task-outcome")).toHaveCount(2);
    await page.waitForTimeout(200);
    await saveShot(page, "5-header-current");
    await expect(chat.getByTestId("orch-task-status")).toHaveText("Задача: готово");
  });

  test("большое окно на ноутбуке: переписке достаётся почти вся высота, задачи свёрнуты", async ({ page }) => {
    const project = nextProject();
    await openStudio(page, "riskwarn", project);
    const chat = page.getByTestId("studio-orch-chat");
    const panel = chat.getByTestId("orch-task-panel");
    await expect(panel).toHaveAttribute("data-open", "false");
    await sendChat(page, "запусти видео сцен 1–3");
    const card = chat.getByTestId("orch-approval");
    await expect(card).toBeVisible();
    await page.waitForTimeout(400);
    await saveShot(page, "4-layout-collapsed");
    const transcript = await chat.getByTestId("orch-transcript").boundingBox();
    const panelBox = await panel.boundingBox();
    expect(transcript).not.toBeNull();
    expect(panelBox).not.toBeNull();
    expect(transcript!.height).toBeGreaterThanOrEqual(784 * 0.6);
    expect(panelBox!.width).toBeLessThanOrEqual(40);
    const cardBox = await card.boundingBox();
    expect(cardBox!.y).toBeGreaterThanOrEqual(transcript!.y - 1);
    expect(cardBox!.y).toBeLessThan(transcript!.y + transcript!.height);
    await expect(chat.getByTestId("orch-tasks-expand")).toContainText("Задачи (1 в работе)");
    await chat.getByTestId("orch-tasks-expand").click();
    await expect(panel).toHaveAttribute("data-open", "true");
    const open = await panel.boundingBox();
    expect(open!.width).toBeLessThanOrEqual(281);
    await expect(panel.getByTestId("orch-agents-row")).toBeVisible();
    const agentsRow = await panel.getByTestId("orch-agents-row").boundingBox();
    expect(agentsRow!.height).toBeLessThanOrEqual(60);
    await saveShot(page, "4-layout-tasks-open");
    await panel.getByTestId("orch-tasks-collapse").click();
    await expect(panel).toHaveAttribute("data-open", "false");
    await card.getByTestId("orch-reject").click();
    await expect(chat.getByTestId("orch-approval")).toHaveCount(0);
  });
});
