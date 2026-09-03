// Смоук студии заказчика на /pipeline: тема ролика через инспектор, «Генерация»,
// чат, меню съёмки, мета-агент, лист / не перекрашен. Запуск — scripts/smoke_studio.sh.
import { createRequire } from "node:module";
const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium } = require("playwright");

const S = process.env.S || "/tmp/vp-smoke";
const BASE = process.env.BASE || "http://127.0.0.1:8765";
const PROJECT = process.env.PROJECT || "3";
const log = (...a) => console.log(...a);
let failures = 0;

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1600, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push("PAGEERROR " + e.message));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("404")) errors.push("CONSOLE " + m.text().slice(0, 200)); });

const insp = () => page.locator("aside").last();
const inspectorHead = () => insp().locator("span").first().innerText().catch(() => "");
const fresh = async () => { await page.goto(`${BASE}/pipeline?project=${PROJECT}`, { waitUntil: "networkidle" }); await page.waitForTimeout(1200); };
const pan = async (dx, dy) => {
  const p = await page.locator(".react-flow__pane").boundingBox();
  const x = p.x + p.width / 2, y = p.y + p.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 5 }); await page.mouse.move(x + dx, y + dy, { steps: 5 });
  await page.mouse.up(); await page.waitForTimeout(400);
};
// Нода может лежать под сайдбаром (сохранённый viewport) — сдвигаем холст, потом кликаем.
const clickNode = async (id) => {
  const n = page.locator(`[data-id="${id}"]`);
  let bb = await n.boundingBox();
  const pane = await page.locator(".react-flow__pane").boundingBox();
  if (bb.x < pane.x + 20) { await pan(pane.x + 60 - bb.x, 0); bb = await n.boundingBox(); }
  await page.mouse.click(bb.x + 12, bb.y + 8); await page.waitForTimeout(800);
};
const check = (name, ok, detail = "") => { log(`${ok ? "ok " : "FAIL"} ${name}${detail ? " — " + detail : ""}`); if (!ok) failures++; };
const step = async (name, fn) => { try { await fn(); } catch (e) { failures++; log(`FAIL ${name}: ${String(e.message).split("\n")[0].slice(0, 160)}`); } };

await fresh();
check("оболочка", (await page.getByText("Видео студия").count()) === 1);
check("ноды на канвасе", (await page.locator(".react-flow__node").count()) > 0);

await step("тема ролика", async () => {
  await clickNode("n_topic");
  check("инспектор: тема", (await inspectorHead()).toUpperCase().includes("ТЕМА"));
  const ta = insp().locator("textarea");
  check("поле темы", (await ta.count()) === 1);
  const text = `Смоук ${new Date().toISOString().slice(0, 16)}`;
  await ta.first().fill(text);
  await insp().getByRole("button", { name: /Сохранить/ }).first().click(); await page.waitForTimeout(800);
  const pr = await (await page.request.get(`${BASE}/api/projects/${PROJECT}`)).json();
  check("тема сохранена через API", pr.topic === text, pr.topic);
  await page.screenshot({ path: `${S}/01-topic.png` });
});

await fresh();
await step("мастер проекта", async () => {
  await page.locator('[title="Новый проект"]').first().click(); await page.waitForTimeout(600);
  const dlg = page.locator('[role="dialog"]');
  check("диалог мастера с полем названия", (await dlg.count()) === 1 && (await dlg.locator("input").count()) >= 1);
  await page.screenshot({ path: `${S}/02-wizard.png` });
});

await fresh();
await step("генерация", async () => {
  await page.locator('[title="Полный интерфейс генерации outsee"]').first().click(); await page.waitForTimeout(1500);
  check("кнопка «Улучшить»", (await page.getByRole("button", { name: /Улучшить/ }).count()) === 1);
  check("батч 1x/2x/4x", (await page.getByText(/^[124]x$/).count()) === 3);
  check("kie не в пикере", (await page.getByText(/kie:/).count()) === 0);
  await page.screenshot({ path: `${S}/03-create.png` });
});

await fresh();
await step("чат", async () => {
  await page.locator('[title^="Свободный чат"]').first().click(); await page.waitForTimeout(1500);
  check("поиск диалогов", (await page.locator('[placeholder="Поиск диалогов..."]').count()) === 1);
  await page.screenshot({ path: `${S}/04-chat.png` });
});

await fresh();
await step("меню съёмки", async () => {
  await page.getByRole("button", { name: /Добавить/ }).first().click(); await page.waitForTimeout(600);
  await page.getByText("Меню съёмки").first().click(); await page.waitForTimeout(300);
  await page.getByRole("button", { name: /Добавить на канвас/ }).click(); await page.waitForTimeout(1000);
  check("нода shot_menu на канвасе", (await page.locator('.react-flow__node[data-id*="shot_menu"]').count()) === 1);
  const r = await page.request.get(`${BASE}/api/db/projects/${PROJECT}/shot-menu`);
  check("shot-menu API", r.status() === 200, String(r.status()));
  await page.screenshot({ path: `${S}/05-shotmenu.png` });
});

await fresh();
await step("мета-агент", async () => {
  await clickNode("n_script");
  await page.getByRole("button", { name: /^Промпты$/ }).first().click(); await page.waitForTimeout(500);
  const sub = page.getByRole("button", { name: /Промт закадрового текста/ });
  if (await sub.count()) { await sub.first().click(); await page.waitForTimeout(700); }
  const btn = page.getByText("Создать с ИИ-агентом");
  check("кнопка мета-агента", (await btn.count()) >= 1);
  if (await btn.count()) { await btn.first().click(); await page.waitForTimeout(600); check("диалог мета-агента", (await page.locator('[role="dialog"]').last().locator("textarea").count()) >= 1); }
  await page.screenshot({ path: `${S}/06-meta-agent.png` });
});

await step("лист /", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" }); await page.waitForTimeout(800);
  const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  check("/ на Manrope, не перекрашен студией", font.startsWith("Manrope"), font.slice(0, 30));
  await page.screenshot({ path: `${S}/07-root.png` });
});

check("ошибок консоли нет", errors.length === 0, errors.slice(0, 3).join(" | "));
await b.close();
log(failures ? `СМОУК КРАСНЫЙ: ${failures}` : "СМОУК ЗЕЛЁНЫЙ");
process.exit(failures ? 1 : 0);
