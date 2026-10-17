import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { PROVIDER_RE, UNSAFE_BUTTON_RE, loginAsQa } from './helpers';

/**
 * P2 full QA pack: каждая кнопка/меню/вкладка/модалка на каждой странице,
 * 2 экрана (1280x800, 1920x1080). Режим авторизации — тот, в котором запущен сервер
 * (прогоняется дважды: VIA_AUTH_ENABLED=0 и =1). Итог: e2e/qa-full-report/qa-full-auth-{on|off}.json
 * (строка пишется сразу после каждого теста, падение одного теста не теряет остальные).
 */
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1920, height: 1080 },
];
const PAGES = [
  '/home', '/login', '/register', '/verify-email', '/forgot-password', '/reset-password',
  '/legal/offer', '/legal/privacy', '/profile', '/billing', '/',
];
const PRIVATE = new Set(['/profile', '/billing', '/']);
const BTN = 'button:visible, [role="button"]:visible, [role="tab"]:visible, [role="menuitem"]:visible, a[class*="btn"]:visible, summary:visible';
/** Сверх общего списка: ничего, что тратит генерации/LLM или шлёт сообщения. */
const EXTRA_UNSAFE = /генерир|улучш|отправ|отсоедин|вставить|send|выйти|logout|telegram|google|vk|вконтакте/i;
const REPORT_DIR = path.join(__dirname, '..', 'qa-full-report');

type Issue = { kind: string; detail: string };
type Row = { page: string; viewport: string; auth: boolean; clicked: number; skipped: string[]; issues: Issue[] };

function watch(page: Page, issues: Issue[], authOn: boolean) {
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (authOn && /401|Unauthorized/i.test(t)) return; // ожидаемые 401 до логина
    if (/Failed to load resource: the server responded with a status of 40[134]/.test(t)) return; // учитываем по response
    issues.push({ kind: 'console', detail: t.slice(0, 300) });
  });
  page.on('pageerror', (e) => issues.push({ kind: 'pageerror', detail: String(e).slice(0, 300) }));
  page.on('response', (r) => {
    const s = r.status();
    const u = r.url();
    if (s >= 500) issues.push({ kind: '5xx', detail: `${s} ${r.request().method()} ${u}` });
    else if (s === 404 && u.includes('/api/')) issues.push({ kind: '404', detail: `${r.request().method()} ${u}` });
    else if (s === 422 && u.includes('/api/')) issues.push({ kind: '422', detail: `${r.request().method()} ${u}` });
  });
}

async function layoutIssues(page: Page, issues: Issue[]) {
  const ov = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, w: window.innerWidth }));
  if (ov.sw > ov.w + 2) issues.push({ kind: 'layout', detail: `горизонтальный скролл: ${ov.sw} > ${ov.w}` });
  const text = await page.locator('body').innerText().catch(() => '');
  const m = text.match(PROVIDER_RE);
  if (m) issues.push({ kind: 'provider', detail: `виден «${m[0]}»` });
}

/** Закрыть всё, что открыто поверх страницы: Esc, кнопка «Закрыть» в диалоге. */
async function closeOverlays(page: Page) {
  for (let k = 0; k < 3; k++) {
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(120);
    const dlg = page.locator('[role="dialog"]:visible').last();
    if (!(await dlg.isVisible().catch(() => false))) return;
    const close = dlg.locator('button[aria-label*="акрыть"], button[aria-label*="lose"]').first();
    if (await close.isVisible().catch(() => false)) await close.click({ timeout: 1500 }).catch(() => {});
  }
}

async function clickAll(page: Page, route: string, issues: Issue[], scope = 'body', reopen?: () => Promise<void>) {
  const buttons = page.locator(scope).first().locator(BTN);
  const count = await buttons.count();
  let clicked = 0;
  const skipped: string[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < count && i < 150; i++) {
    const btn = buttons.nth(i);
    if (!(await btn.isVisible().catch(() => false))) continue;
    const label = ((await btn.innerText().catch(() => '')) || (await btn.getAttribute('aria-label').catch(() => '')) ||
      (await btn.getAttribute('title').catch(() => '')) || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    if (UNSAFE_BUTTON_RE.test(label) || EXTRA_UNSAFE.test(label)) { if (!seen.has(label)) skipped.push(label); seen.add(label); continue; }
    if (await btn.isDisabled().catch(() => false)) continue;
    let ok = false;
    for (let attempt = 0; attempt < 2 && !ok; attempt++) {
      try {
        await btn.click({ timeout: 1500 });
        ok = true;
      } catch (e) {
        const msg = String(e);
        if (!/intercepts pointer events/.test(msg)) break;
        if (attempt === 0) {
          // перекрыто тем, что открыл предыдущий клик — закрываем и пробуем снова
          await closeOverlays(page);
          if (reopen) await reopen();
        } else issues.push({ kind: 'covered', detail: `«${label || '#' + i}» перекрыта другим элементом` });
      }
    }
    if (ok) clicked++;
    await page.waitForTimeout(150);
    const dlg = page.locator('[role="dialog"]:visible').first();
    if (await dlg.isVisible().catch(() => false)) {
      const t = await dlg.innerText().catch(() => '');
      const m = t.match(PROVIDER_RE);
      if (m) issues.push({ kind: 'provider', detail: `в модалке после «${label}»: «${m[0]}»` });
    }
    if (scope === 'body') await closeOverlays(page);
    else await page.keyboard.press('Escape').catch(() => {});
    const u = new URL(page.url());
    if (u.pathname.replace(/\/$/, '') !== route.replace(/\/$/, '')) {
      await page.goto(route).catch(() => {});
      await page.waitForLoadState('networkidle').catch(() => {});
      if (reopen) await reopen();
    }
  }
  return { clicked, skipped };
}

function saveRow(row: Row) {
  fs.mkdirSync(REPORT_DIR, { recursive: true });
  const f = path.join(REPORT_DIR, `qa-full-auth-${row.auth ? 'on' : 'off'}.json`);
  let rows: Row[] = [];
  try { rows = JSON.parse(fs.readFileSync(f, 'utf-8')); } catch { rows = []; }
  rows = rows.filter((r) => !(r.page === row.page && r.viewport === row.viewport));
  rows.push(row);
  fs.writeFileSync(f, JSON.stringify(rows, null, 2), 'utf-8');
}

for (const vp of VIEWPORTS) {
  for (const route of PAGES) {
    test(`QA ${route} @${vp.width}x${vp.height}`, async ({ page, request }) => {
      test.setTimeout(route === '/' ? 900_000 : 240_000);
      const st = await (await request.get('/api/auth/status')).json().catch(() => ({}));
      const authOn = Boolean(st?.auth_required);
      await page.setViewportSize(vp);
      const issues: Issue[] = [];
      let clicked = 0;
      let skipped: string[] = [];
      watch(page, issues, authOn);
      try {
        if (PRIVATE.has(route) && authOn) await loginAsQa(page);
        await page.goto(route);
        await page.waitForLoadState('networkidle');
        await layoutIssues(page, issues);
        const r = await clickAll(page, route, issues);
        clicked = r.clicked;
        skipped = r.skipped;
        if (route === '/') {
          await page.goto(route);
          await page.waitForLoadState('networkidle');
          // студия: каждая нода → панель настроек → все кнопки внутри
          const nodes = page.locator('.react-flow__node');
          const n = await nodes.count();
          for (let i = 0; i < n; i++) {
            const openNode = async () => {
              await nodes.nth(i).click({ timeout: 4000 }).catch((e) => issues.push({ kind: 'covered', detail: `нода #${i}: ${String(e).slice(0, 120)}` }));
              await page.waitForTimeout(200);
            };
            await openNode();
            const sel = '[data-testid="node-step-params-panel"], [class*="node-step-params"]';
            if (await page.locator(sel).first().isVisible().catch(() => false)) {
              const rr = await clickAll(page, route, issues, sel, openNode);
              clicked += rr.clicked;
            }
            await closeOverlays(page);
          }
          // оркестратор: кнопки панели (без отправки сообщений)
          const orch = page.locator('[data-testid="orchestrator-panel"]');
          if (await orch.isVisible().catch(() => false)) {
            const rr = await clickAll(page, route, issues, '[data-testid="orchestrator-panel"]');
            clicked += rr.clicked;
          } else issues.push({ kind: 'missing', detail: 'панель оркестратора не видна' });
          await layoutIssues(page, issues);
        }
      } finally {
        saveRow({ page: route, viewport: `${vp.width}x${vp.height}`, auth: authOn, clicked, skipped, issues });
      }
      test.info().annotations.push({ type: 'issues', description: JSON.stringify(issues).slice(0, 2000) });
      expect(issues, `${route} @${vp.width}x${vp.height}: ${issues.map((x) => x.kind + ': ' + x.detail).join(' | ')}`).toEqual([]);
    });
  }
}
