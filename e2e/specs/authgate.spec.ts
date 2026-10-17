import { test, expect } from '@playwright/test';

/** P2: публичные страницы отдаются со статическим контентом — без пустой вспышки. */
const PUBLIC = ['/home', '/login', '/register', '/forgot-password', '/reset-password', '/verify-email', '/legal/offer', '/legal/privacy'];

function visibleText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

for (const p of PUBLIC) {
  test(`публичная ${p}: контент есть в статическом HTML и без JS`, async ({ request, browser }) => {
    const r = await request.get(p);
    expect(r.ok(), `${p} → ${r.status()}`).toBeTruthy();
    expect(visibleText(await r.text()).length, `${p}: пустой статический HTML`).toBeGreaterThan(40);
    const ctx = await browser.newContext({ javaScriptEnabled: false });
    const pg = await ctx.newPage();
    await pg.goto(p);
    expect((await pg.locator('body').innerText()).trim().length, `${p}: пусто без JS`).toBeGreaterThan(20);
    await ctx.close();
  });
}

test('публичные страницы: нет ошибок гидратации', async ({ page }) => {
  const errs: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && /hydrat|did not match|server rendered/i.test(m.text())) errs.push(m.text());
  });
  for (const p of ['/home', '/login', '/register', '/legal/offer']) {
    await page.goto(p);
    await page.waitForLoadState('networkidle');
  }
  expect(errs).toEqual([]);
});

test('приватная страница: загрузчик вместо пустоты, затем контент или /login', async ({ page, request }) => {
  const st = await (await request.get('/api/auth/status')).json();
  await page.goto('/profile');
  if (st.auth_required) {
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  } else {
    await page.waitForLoadState('networkidle');
    expect((await page.locator('body').innerText()).trim().length).toBeGreaterThan(20);
  }
  // статический HTML приватной страницы содержит брендированный загрузчик, а не пустое body
  const html = await (await request.get('/profile')).text();
  expect(html).toContain('auth-gate-loading');
});
