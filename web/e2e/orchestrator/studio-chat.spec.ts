import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "@playwright/test";
import { OPERATOR_TOKEN, stubStudioApi, stubStudioSockets, type StudioStubOptions } from "./studio-stubs";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

const CATALOG = [
  { id: "node.run_step", title_ru: "Запустить шаг проекта" },
  { id: "step.get_run_cost_basis", title_ru: "Сколько стоит повтор шага" },
];

// Свой проект — своя сессия мока: карточки из прошлых тестов сюда не попадают.
const RUN = Date.now() % 1_000_000;
let projectSerial = 0;
function nextProject(): number {
  projectSerial += 1;
  return 600_000_000 + RUN * 10 + projectSerial;
}

async function openStudio(
  page: Page,
  {
    project = 13,
    scenario = "idle",
    mobile = false,
    ...stub
  }: { project?: number | null; scenario?: string; mobile?: boolean } & StudioStubOptions = {},
) {
  await page.addInitScript(
    ([pid]) => {
      if (pid == null) localStorage.removeItem("vp-studio-selected-project-id");
      else localStorage.setItem("vp-studio-selected-project-id", JSON.stringify(pid));
      localStorage.removeItem("vp-orch-api-token");
    },
    [project] as const,
  );
  await stubStudioApi(page, { operatorToken: OPERATOR_TOKEN, catalog: CATALOG, ...stub });
  const sockets = await stubStudioSockets(page);
  await page.goto(`/?scenario=${scenario}`);
  if (mobile) {
    await expect(page.getByTestId("rail-orchestrator")).toBeVisible();
    return sockets;
  }
  await expect(page.getByTestId("orch-dock")).toBeVisible();
  await expect(page.getByTestId("studio-orch-chat")).toBeVisible();
  return sockets;
}

async function sendChat(page: Page, text: string) {
  const chat = page.getByTestId("studio-orch-chat");
  await chat.getByTestId("orch-composer").fill(text);
  await chat.getByTestId("orch-send").click();
}

test.describe("чат оркестратора в студии", () => {
  test("на главной: проект студии и вход оператора без отдельного токена", async ({ page }) => {
    await openStudio(page);
    const chat = page.getByTestId("studio-orch-chat");
    await expect(chat).toHaveAttribute("data-session", "studio-p13");
    await expect(page.getByTestId("studio-orch-scope")).toContainText("Проект #13");
    const posted = page.waitForRequest((req) => req.method() === "POST" && req.url().includes("/message"));
    await sendChat(page, "что с проектом?");
    const req = await posted;
    expect(req.url()).toContain("/api/orchestrator/chat/studio-p13/message");
    const headers = await req.allHeaders();
    expect(headers["x-vp-operator"]).toBe(OPERATOR_TOKEN);
    expect(headers.authorization).toBeUndefined();
    expect((req.postDataJSON() as { project_id?: number }).project_id).toBe(13);
    await expect(chat.getByTestId("orch-ack")).toContainText("принял, разбираю");
    await expect(page.getByTestId("orch-token-gate")).toHaveCount(0);
    await saveShot(page, "studio-chat-main");
  });

  test("вариант «вся фабрика»: своя сессия и без project_id", async ({ page }) => {
    await openStudio(page);
    await page.getByTestId("orch-scope-factory").click();
    const chat = page.getByTestId("studio-orch-chat");
    await expect(chat).toHaveAttribute("data-session", "studio-factory");
    await expect(page.getByTestId("studio-orch-scope")).toContainText("Вся фабрика");
    const posted = page.waitForRequest((req) => req.method() === "POST" && req.url().includes("/message"));
    await sendChat(page, "какие проекты идут?");
    const req = await posted;
    expect(req.url()).toContain("/chat/studio-factory/message");
    expect(req.postDataJSON()).not.toHaveProperty("project_id");
    await page.getByTestId("orch-scope-project").click();
    await expect(page.getByTestId("studio-orch-chat")).toHaveAttribute("data-session", "studio-p13");
  });

  test("карточка подтверждения: цена, риск, предупреждения и ход долгого шага", async ({ page }) => {
    await openStudio(page, { scenario: "riskwarn" });
    await sendChat(page, "запусти видео сцен 1–3");
    const chat = page.getByTestId("studio-orch-chat");
    const card = chat.getByTestId("orch-approval");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Запустить шаг «Видео»");
    await expect(card.getByTestId("orch-approval-risk")).toContainText("риск: высокий");
    await expect(card.getByTestId("orch-approval-cost")).toContainText("около $3.2");
    const steps = card.getByTestId("orch-approval-step");
    // Чтение статуса ничего не меняет: оно свёрнуто в строку под списком, в списке только запуск.
    await expect(steps).toHaveCount(1);
    await expect(card.getByTestId("orch-approval-reads")).toContainText("статус проекта");
    await expect(steps.nth(0)).toContainText("шаг видео; сцены 1–3; проект #13");
    await expect(steps.nth(0)).toContainText("платно");
    await expect(card.getByTestId("orch-approval-warnings")).toContainText("повтор потратит деньги ещё раз");
    await expect(card.getByTestId("orch-approval-effects")).toContainText("Старые клипы сцен 1–3 будут заменены");
    await expect(card.getByTestId("orch-approval-effects")).not.toContainText("Внимание:");
    await expect(card).toContainText("Запустить шаг проекта");
    await expect(card.getByTestId("orch-approve")).toHaveText("Подтвердить");
    await expect(card.getByTestId("orch-reject")).toHaveText("Отклонить");
    await saveShot(page, "studio-chat-approval");
    const decided = page.waitForRequest(
      (req) =>
        req.method() === "POST" &&
        /\/api\/orchestrator\/chat\/[^/]+\/approve$/.test(new URL(req.url()).pathname) &&
        (req.postDataJSON() as { change_id?: string }).change_id === "chg-riskwarn",
    );
    await card.getByTestId("orch-approve").click();
    await decided;
    await expect(chat.getByTestId("orch-approval")).toHaveCount(0);
    await expect(chat.getByTestId("orch-task-panel")).toHaveAttribute("data-open", "false");
    await chat.getByTestId("orch-tasks-expand").click();
    const step = chat.getByTestId("orch-step").filter({ hasText: "Видео сцен 1–3" });
    await expect(step).toHaveAttribute("data-step-status", "running");
    await expect(step).toContainText("готово 2 из 5 клипов");
    await expect(step.getByTestId("orch-step-progress").locator("div")).toHaveAttribute("style", /width: 40%/);
    await saveShot(page, "studio-chat-progress");
    await expect(chat.getByTestId("orch-task-status")).toContainText("готово", { timeout: 10_000 });
  });

  // Эта линия: участник студии (role=member) не видит сумм в $ — ни в карточке, ни в плане.
  test("участник студии: в карточке нет цены в $, вместо неё пометка", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("vp.token", "member-session-token"));
    await openStudio(page, {
      scenario: "riskwarn",
      project: nextProject(),
      handle: async (route: Route, url: URL) => {
        const reply = (body: unknown) =>
          route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
        if (url.pathname === "/api/auth/status") {
          await reply({ auth_required: true, accounts: true });
          return true;
        }
        if (url.pathname === "/api/me") {
          await reply({ sso_enabled: false, tenant_id: "t-1", email: "member@studio.local", brand: "", balance_micro: 0,
                        balance_credits: "0", provisioned_now: false, role: "member" });
          return true;
        }
        return false;
      },
    });
    await sendChat(page, "запусти видео сцен 1–3");
    const card = page.getByTestId("studio-orch-chat").getByTestId("orch-approval");
    await expect(card).toBeVisible();
    const cost = card.getByTestId("orch-approval-cost");
    await expect(cost).toHaveAttribute("data-hidden", "true");
    await expect(cost).toHaveText("цена видна админу студии");
    await expect(card).not.toContainText("$");
  });

  test("ошибки задачи по-русски, без технических подробностей", async ({ page }) => {
    await openStudio(page, { scenario: "techfail" });
    await sendChat(page, "собери ролик");
    const panel = page.getByTestId("studio-orch-chat").getByTestId("orch-task-panel");
    const error = panel.getByTestId("orch-task-error");
    await expect(error).toContainText("внутренней ошибки");
    await expect(error).toContainText("Журнал");
    for (const bad of ["ValueError", "plan_hash", "{", "9f3a2b1c"]) {
      await expect(error).not.toContainText(bad);
    }
    const failed = panel.getByTestId("orch-step").filter({ hasText: "Сборка ролика" });
    await expect(failed).toHaveAttribute("data-step-status", "failed");
    await expect(failed).toContainText("шаг не удался");
    await expect(failed).not.toContainText("Traceback");
  });

  test("уведомления: догрузка, WebSocket без повторов, прочитано и кнопка → план", async ({ page }) => {
    const backlog = {
      id: 41,
      project_id: 13,
      level: "warning",
      title: "Шаг «Видео» упал, повторю через минуту",
      text: "Провайдер не ответил вовремя.",
      kind: "step_failure_retry",
      created_at: "2026-10-19T11:58:00Z",
      suggested_actions: [
        {
          action_id: "step.get_run_cost_basis",
          label: "Сколько стоит повтор",
          params: { project_id: 13, step_code: "video" },
          risk: "read",
          needs_approval: false,
        },
      ],
      read: false,
      read_at: null,
    };
    const preview = {
      plan_id: "plan-n1",
      preview: {
        ok: true,
        errors: [],
        plan_hash: "h",
        risk_max: "paid",
        needs_approval: true,
        steps: [
          {
            index: 0,
            action_id: "step.get_run_cost_basis",
            title_ru: "Сколько стоит повтор шага",
            risk: "paid",
            spends_money: true,
            valid: true,
            errors: [],
            precondition_failures: [],
            cost: { low: 2, high: 3, basis: "по прошлому прогону", known: true },
            params: { project_id: 13, step_code: "video" },
          },
        ],
        cost: { low: 2, high: 3, unknown_steps: 0, known: true },
        destructive_effects: [{ index: 0, action_id: "step.get_run_cost_basis", text: "Внимание: шаг уже запускался сегодня" }],
        warnings: [{ index: 0, action_id: "step.get_run_cost_basis", text: "шаг уже запускался сегодня" }],
      },
    };
    const reads: string[] = [];
    const previews: unknown[] = [];
    const sockets = await openStudio(page, {
      notices: [backlog],
      preview,
      handle: async (route: Route, url: URL) => {
        if (url.pathname.endsWith("/read")) reads.push(url.pathname);
        if (url.pathname === "/api/action-layer/plans/preview") previews.push(route.request().postDataJSON());
        return false;
      },
    });
    const chat = page.getByTestId("studio-orch-chat");
    await expect(chat.getByTestId("chat-notice")).toHaveCount(1);
    await expect(chat.getByTestId("chat-notice").first()).toHaveAttribute("data-level", "warning");
    await expect(page.getByTestId("studio-orch-unread")).toContainText("1");

    await expect.poll(() => sockets.has("global") && sockets.has("projects.13")).toBe(true);
    const fresh = {
      id: 42,
      project_id: 13,
      level: "error",
      title: "Проект на паузе: шаг «Видео» остановлен",
      text: "Три сбоя подряд.",
      kind: "step_failure_abandon",
      created_at: "2026-10-19T12:00:00Z",
      suggested_actions: [],
      read: false,
      read_at: null,
    };
    const frame = JSON.stringify({ type: "chat_notice", project_id: 13, notice: fresh });
    sockets.get("projects.13")!.send(frame);
    sockets.get("global")!.send(frame);
    sockets.get("global")!.send(
      JSON.stringify({ type: "chat_notice", project_id: 99, notice: { ...fresh, id: 43, project_id: 99, title: "чужой проект" } }),
    );
    await expect(chat.getByTestId("chat-notice")).toHaveCount(2);
    await expect(chat.getByTestId("chat-notice").filter({ hasText: "Проект на паузе" })).toHaveAttribute("data-level", "error");
    await expect(chat).not.toContainText("чужой проект");
    await saveShot(page, "studio-chat-notices");

    const first = chat.getByTestId("chat-notice").filter({ hasText: "Шаг «Видео» упал" });
    await first.getByTestId("chat-notice-read").click();
    await expect(first).toHaveAttribute("data-read", "true");
    await expect.poll(() => reads).toContain("/api/chat-notices/41/read");

    await first.getByTestId("chat-notice-action").click();
    const card = first.getByTestId("action-plan-card");
    await expect(card).toBeVisible();
    await expect(card.getByTestId("plan-warnings")).toContainText("шаг уже запускался сегодня");
    await expect(card).not.toContainText("Будет стёрто");
    await expect(card.getByRole("button", { name: "Подтвердить и выполнить…" })).toBeVisible();
    await expect(card.getByTestId("plan-reject")).toBeVisible();
    expect(previews[0]).toMatchObject({ steps: [{ action_id: "step.get_run_cost_basis", params: { step_code: "video" } }] });
    await saveShot(page, "studio-chat-notice-plan");
    await card.getByTestId("plan-reject").click();
    await expect(first.getByTestId("action-plan-card")).toHaveCount(0);
  });

  // Эта линия: в «Служебном» только блоки с ручками на сервере (резервы, план действий,
  // контроль изменений); старый чат — свой, main (удаление проектов, push), без запуска шага
  // и смены модели кнопкой — их делает новый чат.
  test("регистратор свёрнут, старый чат на месте и отвечает", async ({ page }) => {
    await openStudio(page, {
      legacyChat: {
        reply: "Проект #13: картинки готовы.",
        applied: null,
        actions_run: [],
        ui_actions: [],
        pending_confirm: [],
        error: null,
      },
    });
    await page.getByTestId("orch-dock-tab-registrar").click();
    const sections = page.getByTestId("registrar-sections");
    await expect(sections).toBeVisible();
    const toggles = sections.locator("[data-testid^='registrar-toggle-']");
    await expect(toggles).toHaveCount(3);
    for (const id of ["reserves", "actions", "changes"]) {
      await expect(sections.getByTestId(`registrar-toggle-${id}`)).toHaveAttribute("aria-expanded", "false");
    }
    await saveShot(page, "studio-registrar");

    await page.getByTestId("orch-dock-tab-legacy").click();
    const legacy = page.getByTestId("legacy-chat");
    await expect(legacy).toBeVisible();
    await legacy.getByPlaceholder("сообщение оркестратору…").fill("что с проектом?");
    await legacy.getByRole("button", { name: "Отправить" }).click();
    await expect(legacy).toContainText("Проект #13: картинки готовы.");
    await page.getByTestId("orch-dock-tab-chat").click();
    await expect(page.getByTestId("studio-orch-chat")).toBeVisible();
  });

  test("ссылка в навигации открывает чат", async ({ page }) => {
    await openStudio(page);
    await page.getByTestId("orch-dock-tab-registrar").click();
    await page.getByTestId("orch-dock-toggle").click();
    await expect(page.getByTestId("orch-dock")).toHaveAttribute("data-open", "false");
    await page.getByTestId("rail-orchestrator").click();
    await expect(page.getByTestId("orch-dock")).toHaveAttribute("data-open", "true");
    await expect(page.getByTestId("orch-dock-tab-chat")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("studio-orch-chat")).toBeVisible();
  });

  test("заблокированная карточка на ноутбуке 1523x784: причина сверху, карточка целиком, серая кнопка", async ({ page }) => {
    await page.setViewportSize({ width: 1523, height: 784 });
    await openStudio(page, { scenario: "queueall", project: nextProject() });
    await sendChat(page, "запусти все");
    const chat = page.getByTestId("studio-orch-chat");
    const card = chat.getByTestId("orch-approval");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("orch-dock")).toHaveAttribute("data-size", "big");
    await page.waitForTimeout(400);
    await saveShot(page, "studio-chat-blocked-1523");
    const blocked = card.getByTestId("orch-approval-blocked");
    const steps = card.getByTestId("orch-approval-steps");
    expect((await blocked.boundingBox())!.y).toBeLessThan((await steps.boundingBox())!.y);
    await expect(blocked).toContainText("проект #5: стоимость шага неизвестна");
    await expect(blocked).not.toContainText("шаг 1:");
    await expect(card.getByTestId("orch-approval-step")).toHaveCount(1);
    await expect(card.getByTestId("orch-approval-reads")).toContainText("статус проекта (14 раз)");
    const cost = card.getByTestId("orch-approval-cost");
    await expect(cost).toHaveAttribute("data-unknown", "true");
    await expect(cost).not.toContainText("$0");
    const approve = card.getByTestId("orch-approve");
    await expect(approve).toBeDisabled();
    await expect(approve).toHaveText("Подтвердить нельзя");
    await expect(approve).not.toHaveClass(/bg-primary/);
    const box = (await card.boundingBox())!;
    const view = (await chat.getByTestId("orch-transcript").boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(view.y - 1);
    expect(box.y + box.height).toBeLessThanOrEqual(view.y + view.height + 1);
  });

  test("мобильная ширина: панель во весь экран, без горизонтальной прокрутки", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openStudio(page, { scenario: "riskwarn", mobile: true, project: nextProject() });
    const dock = page.getByTestId("orch-dock");
    await expect(dock).toHaveAttribute("data-open", "false");
    // Свёрнутую полоску на узком экране не видно: чат открывает плавающая кнопка «Чат».
    const fab = page.getByTestId("orch-dock-fab");
    await expect(fab).toBeVisible();
    await fab.click();
    await expect(dock).toHaveAttribute("data-open", "true");
    await expect(fab).toHaveCount(0);
    await page.getByTestId("orch-dock-toggle").click();
    await expect(fab).toBeVisible();
    await page.getByTestId("rail-orchestrator").click();
    await expect(dock).toHaveAttribute("data-open", "true");
    await expect(dock).toBeVisible();
    const box = await dock.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(390.5);
    const chat = page.getByTestId("studio-orch-chat");
    await sendChat(page, "запусти видео");
    await expect(chat.getByTestId("orch-approval")).toBeVisible();
    const cardBox = await chat.getByTestId("orch-approval").boundingBox();
    expect(cardBox!.x + cardBox!.width).toBeLessThanOrEqual(390.5);
    const overflow = await dock.evaluate((el) => {
      const nodes = [el, ...Array.from(el.querySelectorAll<HTMLElement>("*"))];
      return nodes.filter((n) => n.getBoundingClientRect().right > window.innerWidth + 0.5 && n.offsetParent !== null).length;
    });
    expect(overflow).toBe(0);
    await saveShot(page, "studio-chat-mobile");
    await page.getByTestId("studio-orch-mobile-toggle").click();
    await expect(chat.getByTestId("orch-task-panel")).toBeVisible();
    await saveShot(page, "studio-chat-mobile-tasks");
  });
});
