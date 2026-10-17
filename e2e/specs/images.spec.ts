import { test, expect, type Page } from '@playwright/test';
import { STUDIO_PATH, loginAsQa, assertNoProviderNames } from './helpers';

/** P3: images ported from housepc (node covers, prompt cards, style picker) must load — no broken <img>, no image 4xx. */
async function imageState(page: Page) {
  return page.evaluate(() => {
    const imgs = [...document.querySelectorAll('img')].filter((i) => i.getAttribute('src'));
    const broken = imgs.filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.getAttribute('src'));
    const bg = [...document.querySelectorAll<HTMLElement>('*')]
      .map((e) => getComputedStyle(e).backgroundImage)
      .filter((b) => b && b.includes('url('))
      .flatMap((b) => [...b.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]))
      .filter((u) => !u.startsWith('data:'));
    return { total: imgs.length, broken, bg: [...new Set(bg)] };
  });
}

async function checkUrls(page: Page, urls: string[]) {
  const bad: string[] = [];
  for (const u of urls.slice(0, 80)) {
    const r = await page.request.get(u).catch(() => null);
    if (!r || r.status() >= 400) bad.push(`${r?.status()} ${u}`);
  }
  return bad;
}

test('studio images: node covers + right float, no broken images', async ({ page }) => {
  test.setTimeout(180_000);
  const bad4xx: string[] = [];
  page.on('response', (r) => { if (r.status() >= 400 && r.request().resourceType() === 'image') bad4xx.push(`${r.status()} ${r.url()}`); });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await loginAsQa(page);
  await page.goto(STUDIO_PATH);
  await page.waitForLoadState('networkidle');
  await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
  const st = await imageState(page);
  const nodeCovers = st.bg.filter((u) => u.includes('/prompt-bg/'));
  expect(nodeCovers.length, 'node cover images (prompt-bg) are rendered').toBeGreaterThan(0);
  expect(st.broken, 'broken <img>').toEqual([]);
  expect(await checkUrls(page, st.bg), 'background images load').toEqual([]);
  expect(bad4xx, 'image 4xx').toEqual([]);
});

test('create workspace: prompt assistant with style picker images', async ({ page }) => {
  test.setTimeout(180_000);
  const bad4xx: string[] = [];
  page.on('response', (r) => { if (r.status() >= 400 && r.request().resourceType() === 'image') bad4xx.push(`${r.status()} ${r.url()}`); });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await loginAsQa(page);
  await page.goto(STUDIO_PATH);
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('studio-open-create', { detail: { projectId: null } })));
  const ws = page.locator('[data-testid="create-workspace"]');
  await expect(ws).toBeVisible({ timeout: 20_000 });
  await ws.getByRole('button', { name: /Помощник/ }).first().click();
  await page.waitForTimeout(2500);
  await page.waitForLoadState('networkidle');
  const st = await imageState(page);
  expect(st.broken, 'broken <img> in assistant').toEqual([]);
  expect(await checkUrls(page, st.bg), 'background images load').toEqual([]);
  expect(bad4xx, 'image 4xx').toEqual([]);
  await assertNoProviderNames(page, 'create assistant');
});

test('ported static images are served', async ({ request }) => {
  for (const u of ['/prompt-bg/01.webp', '/prompt-bg/node/script.webp', '/prompt-bg/gen/topic-default.webp', '/gen-styles/infographic_tutor.jpg']) {
    const r = await request.get(u);
    expect(r.status(), u).toBe(200);
  }
});
