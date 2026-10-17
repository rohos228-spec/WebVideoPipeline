import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { PROVIDER_RE, UNSAFE_BUTTON_RE, loginAsQa } from './helpers';

/**
 * P2 full QA pack: каждая кнопка/меню/вкладка/модалка на каждой странице,
 * 2 экрана (1280x800, 1920x1080). Режим авторизации — тот, в котором запущен сервер
 * (прогоняется дважды: VIA_AUTH_ENABLED=0 и =1). Итог пишется в e2e/qa-full-report/*.json.
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

type Issue = { kind: string; detail: string };
type Row = { page: string; viewport: string; auth: boolean; clicked: number; skipped: string[]; issues: Issue[] };

function watch(page: Page, issues: Issue[], authOn: boolean) {
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    // ожидаемые 401 до логина в режиме авторизации — не баг
    if (authOn && /401|Unauthorized/i.test(t)) return;
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
  const ov = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    w: window.innerWidth,
  }));
  if (ov.sw > ov.w + 2) issues.push({ kind: 'layout', detail: `горизонтальный скролл: ${ov.sw} > ${ov.w}` });
  const text = await page.locator('body').innerText().catch(() => '');
  const m = text.match(PROVIDER_RE);
  if (m) issues.push({ kind: 'provider', detail: `виден «${m[0]}»` });
}

async function clickAll(page: Page, route: string, issues: Issue[], scope = 'body') {
  const root = page.locator(scope).first();
  const buttons = root.locator(BTN);
  const count = await buttons.count();
  let clicked = 0;
  const skipped: string[] = [];
  for (let i = 0; i < count && i < 150; i++) {
    const btn = buttons.nth(i);
    if (!(await btn.isVisible().catch(() => false))) continue;
    const label = ((await btn.innerText().catch(() => '')) || (await btn.getAttribute('aria-label').catch(() => '')) ||
      (await btn.getAttribute('title').catch(() => '')) || '').trim().slice(0, 60);
    if (UNSAFE_BUTTON_RE.test(label)) { skipped.push(label); continue; }
    if (/выйти|logout|telegram|google|vk|вконтакте/i.test(label)) { skipped.push(label); continue; }
    if (await btn.isDisabled().catch(() => false)) continue;
    try {
      await btn.click({ timeout: 2500 });
      clicked++;
    } catch (e) {
      const msg = String(e);
      if (/intercepts pointer events/.test(msg)) issues.push({ kind: 'covered', detail: `«${label || '#' + i}» перекрыта другим элементом` });
    }
    await page.waitForTimeout(150);
    // модалка открылась — проверим в ней провайдеров и закроем
    const dlg = page.locator('[role="dialog"]:visible').first();
    if (await dlg.isVisible().catch(() => false)) {
      const t = await dlg.innerText().catch(() => '');
      const m = t.match(PROVIDER_RE);
      if (m) issues.push({ kind: 'provider', detail: `в модалке после «${label}»: «${m[0]}»` });
    }
    await page.keyboard.press('Escape').catch(() => {});
    const u = new URL(page.url());
    if (u.pathname.replace(/\/$/, '') !== route.replace(/\/$/, '') || u.host !== new URL(page.url()).host) {
      await page.goto(route).catch(() => {});
      await page.waitForLoadState('networkidle').catch(() => {});
    }
  }
  return { clicked, skipped };
}

const rows: Row[] = [];

test.describe.configure({ mode: 'serial' });

test.afterAll(async () => {
  const dir = path.join(__dirname, '..', 'qa-full-report');
  fs.mkdirSync(dir, { recursive: true });
  const auth = rows[0]?.auth ? 'on' : 'off';
  fs.writeFileSync(path.join(dir, `qa-full-auth-${auth}.json`), JSON.stringify(rows, null, 2), 'utf-8');
});

for (const vp of VIEWPORTS) {
  for (const route of PAGES) {
    test(`QA ${route} @${vp.width}x${vp.height}`, async ({ page, request }) => {
      test.setTimeout(240_000);
      const st = await (await request.get('/api/auth/status')).json().catch(() => ({}));
      const authOn = Boolean(st?.auth_required);
      await page.setViewportSize(vp);
      const issues: Issue[] = [];
      watch(page, issues, authOn);
      if (PRIVATE.has(route) && authOn) await loginAsQa(page);
      await page.goto(route);
      await page.waitForLoadState('networkidle');
      await layoutIssues(page, issues);
      const r = await clickAll(page, route, issues);
      let clicked = r.clicked;
      if (route === '/') {
        // студия: каждая нода → панель настроек → все кнопки внутри
        const nodes = page.locator('.react-flow__node');
        const n = await nodes.count();
        for (let i = 0; i < n; i++) {
          await nodes.nth(i).click({ timeout: 4000 }).catch((e) => issues.push({ kind: 'covered', detail: `нода #${i}: ${String(e).slice(0, 120)}` }));
          const panel = page.locator('[data-testid="node-step-params-panel"], [class*="node-step-params"]').first();
          if (await panel.isVisible().catch(() => false)) {
            const rr = await clickAll(page, route, issues, '[data-testid="node-step-params-panel"], [class*="node-step-params"]');
            clicked += rr.clicked;
          }
          await page.keyboard.press('Escape').catch(() => {});
        }
        // оркестратор: открыть/закрыть, развернуть/свернуть (без отправки сообщений)
        const orch = page.locator('[data-testid="orchestrator-panel"]');
        if (await orch.isVisible().catch(() => false)) {
          const rr = await clickAll(page, route, issues, '[data-testid="orchestrator-panel"]');
          clicked += rr.clicked;
        } else issues.push({ kind: 'missing', detail: 'панель оркестратора не видна' });
        await layoutIssues(page, issues);
      }
      rows.push({ page: route, viewport: `${vp.width}x${vp.height}`, auth: authOn, clicked, skipped: r.skipped, issues });
      test.info().annotations.push({ type: 'issues', description: JSON.stringify(issues).slice(0, 2000) });
      expect(issues, `${route} @${vp.width}x${vp.height}: ${issues.map((x) => x.kind + ': ' + x.detail).join(' | ')}`).toEqual([]);
    });
  }
}
