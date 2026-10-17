import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { STUDIO_PATH, loginAsQa, PROVIDER_RE } from './helpers';
import { reconcileNodeRunStatus } from '../../web/src/lib/node-run-status';

/** P3 design parity with housepc (Fable acceptance checks). No generations: generation endpoints are aborted. */
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
];

async function openStudio(page: Page, vp = VIEWPORTS[2]) {
  await page.route(/\/api\/.*(generate|\/run$|\/start$)/, (r) => (r.request().method() === 'POST' ? r.abort() : r.continue()));
  await page.setViewportSize(vp);
  await loginAsQa(page);
  await page.goto(STUDIO_PATH);
  await page.waitForLoadState('networkidle');
  await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);
}

function boxesIntersect(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }, tol = 8) {
  return a.x + tol < b.x + b.width && b.x + tol < a.x + a.width && a.y + tol < b.y + b.height && b.y + tol < a.y + a.height;
}

test('1-4: no forbidden rail buttons, no inspector, right float intact, no copy-selected toolbar button', async ({ page }) => {
  await openStudio(page);
  for (const name of ['API', 'Баг', 'Логи', 'Сеть', 'База', 'Чат']) {
    await expect(page.getByRole('button', { name, exact: true }), name).toHaveCount(0);
    await expect(page.locator(`[title="${name}"]`), name).toHaveCount(0);
  }
  await expect(page.locator('[data-testid="inspector"]')).toHaveCount(0);
  await page.locator('.react-flow__node').first().click();
  await expect(page.locator('[data-testid="inspector"]')).toHaveCount(0);
  const f = page.getByTestId('right-float');
  await expect(f).toBeVisible();
  expect(await f.evaluate((el) => getComputedStyle(el).position)).toBe('absolute');
  expect(Math.round((await f.boundingBox())!.width)).toBe(300);
  await expect(f.locator('.rounded-2xl.border-sky-500\\/25')).toHaveCount(1);
  await expect(f.locator('#vp-right-controls button')).not.toHaveCount(0);
  await expect(f.getByText('Потоки генерации')).toBeVisible();
  await expect(page.getByRole('button', { name: /Копировать выделенные/ })).toHaveCount(0);
  await expect(page.locator('[title*="Копировать выделенные"]')).toHaveCount(0);
});

test('5: copy via node right-click menu is available (Ctrl+C kept)', async ({ page }) => {
  await openStudio(page);
  await page.locator('.react-flow__node').first().click({ button: 'right' });
  const menu = page.getByTestId('node-context-menu');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem', { name: /Копировать/ })).toBeVisible();
  await expect(menu).toContainText('Ctrl+C');
  await page.keyboard.press('Escape');
  await page.mouse.click(5, 5);
});

test('6: edges are housepc white dashed 2px', async ({ page }) => {
  await openStudio(page);
  const e = page.locator('.react-flow__edge-path').first();
  await expect(e).toBeAttached();
  const s = await e.evaluate((el) => { const c = getComputedStyle(el); return { stroke: c.stroke, w: c.strokeWidth, d: c.strokeDasharray }; });
  expect(s.stroke.replace(/\s/g, '')).toBe('rgba(255,255,255,0.5)');
  expect(s.w).toBe('2px');
  expect(s.d.replace(/px/g, '').replace(/,/g, ' ').replace(/\s+/g, ' ').trim()).toBe('5 6');
});

test('8-9: library dialog is housepc LibraryBrowser; «Стиль» node is findable', async ({ page }) => {
  await openStudio(page);
  await page.getByRole('button', { name: 'Добавить', exact: true }).first().click();
  const dlg = page.locator('[role="dialog"]').last();
  await expect(dlg.getByText('Библиотека', { exact: true })).toBeVisible();
  expect(await dlg.getByText('Библиотека', { exact: true }).evaluate((el) => getComputedStyle(el).fontSize)).toBe('15px');
  const search = dlg.locator('input[placeholder^="Поиск"]').first();
  await expect(search).toHaveClass(/h-9/);
  await expect(search).toHaveClass(/rounded-none/);
  await expect(search.locator('xpath=..')).toHaveClass(/w-\[240px\]/);
  await expect(dlg.getByRole('button', { name: /Группы/ })).not.toHaveCount(0);
  await expect(dlg.locator('input.h-7')).toHaveCount(0);
  await search.fill('Стиль');
  await page.waitForTimeout(400);
  await expect(dlg.getByText(/Стиль/).first()).toBeVisible();
  const imgs = await dlg.evaluate((el) => [...el.querySelectorAll('img')].filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.src));
  expect(imgs, 'broken images in library').toEqual([]);
  expect((await dlg.innerText()).match(PROVIDER_RE)?.[0] ?? null, 'provider in library').toBeNull();
  await page.keyboard.press('Escape');
});

test('10-11: orchestrator is a bottom overlay and remembers expanded state', async ({ page }) => {
  await openStudio(page);
  const o = page.getByTestId('orchestrator-panel');
  await expect(o).toBeVisible();
  const st = await o.evaluate((el) => { const c = getComputedStyle(el); return { pos: c.position, bottom: c.bottom, left: c.left, right: c.right }; });
  expect(st.pos).toBe('absolute');
  expect(st.bottom).toBe('0px');
  const flow = (await page.locator('.react-flow').first().boundingBox())!;
  const main = (await page.locator('main').first().boundingBox())!;
  expect(Math.abs(flow.height - main.height), 'canvas not shrunk by orchestrator').toBeLessThan(4);
  const keys = await page.evaluate(() => Object.keys(localStorage));
  expect(keys).not.toContain('vp-orchestrator-expanded-v2');
  await page.evaluate(() => localStorage.setItem('vp-orchestrator-expanded', 'true'));
  await page.reload();
  await page.waitForLoadState('networkidle');
  await expect(o).toBeVisible();
  expect(Math.round((await o.boundingBox())!.height)).toBeGreaterThan(300);
  await page.evaluate(() => localStorage.setItem('vp-orchestrator-expanded', 'false'));
  await page.reload();
  await page.waitForLoadState('networkidle');
  expect(Math.round((await o.boundingBox())!.height)).toBeLessThanOrEqual(260);
});

for (const vp of VIEWPORTS) {
  test(`12: auto-fit keeps nodes clear of orchestrator + right float @${vp.width}x${vp.height}`, async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('vp-orchestrator-open', 'true'); localStorage.setItem('vp-orchestrator-expanded', 'false'); });
    await openStudio(page, vp);
    const flow = (await page.locator('.react-flow').first().boundingBox())!;
    const orch = (await page.getByTestId('orchestrator-panel').boundingBox())!;
    const fl = (await page.getByTestId('right-float').boundingBox())!;
    const nodes = page.locator('.react-flow__node');
    const n = await nodes.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const b = await nodes.nth(i).boundingBox();
      if (!b) continue;
      expect(boxesIntersect(b, orch), `node #${i} under orchestrator`).toBeFalsy();
      expect(boxesIntersect(b, fl), `node #${i} under right float`).toBeFalsy();
      expect(b.x >= flow.x - 1 && b.y >= flow.y - 1 && b.x + b.width <= flow.x + flow.width + 1, `node #${i} outside canvas`).toBeTruthy();
    }
  });
}

test('13: Excel-GPT loop highlighting (current = running, done = done)', () => {
  const opts = { activeExcelGptKey: 'n_excel_2', completedExcelGptKeys: ['n_excel_1'] };
  expect(reconcileNodeRunStatus('excel_gpt', 'pending', 'enriching_rows', { ...opts, nodeKey: 'n_excel_2' })).toBe('running');
  expect(reconcileNodeRunStatus('excel_gpt', 'pending', 'enriching_rows', { ...opts, nodeKey: 'n_excel_1' })).toBe('done');
  expect(reconcileNodeRunStatus('excel_gpt', 'pending', 'enriching_rows', { ...opts, nodeKey: 'n_excel_3' })).toBe('pending');
  expect(reconcileNodeRunStatus('script', 'pending', 'enriching_rows', { ...opts, nodeKey: 'n_excel_2' })).toBe('pending');
});

test('14-15: no broken images / image 4xx / provider names on studio', async ({ page }) => {
  const bad: string[] = [];
  page.on('response', (r) => { if (r.status() >= 400 && (r.request().resourceType() === 'image' || /\.(png|jpe?g|webp|svg|gif)(\?|$)/.test(r.url()))) bad.push(`${r.status()} ${r.url()}`); });
  await openStudio(page);
  const broken = await page.evaluate(() => [...document.querySelectorAll('img')].filter((i) => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map((i) => i.src));
  expect(broken).toEqual([]);
  expect(bad).toEqual([]);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text.match(PROVIDER_RE)?.[0] ?? null).toBeNull();
});

test('16: no BOM in web sources', () => {
  const root = path.resolve(__dirname, '../../web/src');
  const bad: string[] = [];
  const walk = (d: string) => { for (const f of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, f.name); if (f.isDirectory()) walk(p); else if (/\.(tsx?|css)$/.test(f.name) && fs.readFileSync(p).subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))) bad.push(p); } };
  walk(root);
  expect(bad).toEqual([]);
});
