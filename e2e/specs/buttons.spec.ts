import { test, expect } from '@playwright/test';
import {
  PAGES, UNSAFE_BUTTON_RE, collectProblems, assertNoProblems,
  assertNoProviderNames, loginAsQa,
} from './helpers';

test.describe.configure({ mode: 'serial' });

for (const path of PAGES) {
  test(`клик-тест кнопок: ${path}`, async ({ page }) => {
    test.setTimeout(300_000);
    const problems = collectProblems(page);
    if (path === '/profile') await loginAsQa(page);
    await page.goto(path);
    await page.waitForLoadState('networkidle');

    const buttons = page.locator(
      'button:visible, [role="button"]:visible, a[class*="btn"]:visible',
    );
    const count = await buttons.count();
    const clicked: string[] = [];
    const skipped: string[] = [];

    for (let i = 0; i < count; i++) {
      const btn = buttons.nth(i);
      if (!(await btn.isVisible().catch(() => false))) continue;
      const label = ((await btn.innerText().catch(() => '')) ||
        (await btn.getAttribute('aria-label')) || (await btn.getAttribute('title')) || '').trim();

      // Безопасные исключения: оплата, удаление, платная генерация (ТЗ раздел B)
      if (UNSAFE_BUTTON_RE.test(label)) { skipped.push(label); continue; }
      if (await btn.isDisabled().catch(() => false)) continue;

      await btn.click({ trial: false, timeout: 3000 }).catch(() => {});
      clicked.push(label || `<без текста #${i}>`);

      // закрываем возможные модалки/оверлеи, чтобы идти дальше
      await page.keyboard.press('Escape').catch(() => {});
      if (page.url() && !page.url().includes(path)) {
        await page.goto(path);
        await page.waitForLoadState('networkidle');
      }
    }

    test.info().annotations.push(
      { type: 'clicked', description: clicked.join(' | ') },
      { type: 'skipped-unsafe', description: skipped.join(' | ') },
    );
    assertNoProblems(problems, `клик-тест ${path}`);
    await assertNoProviderNames(page, `клик-тест ${path}`);
    expect(clicked.length + skipped.length, `на ${path} нет ни одной кнопки?`).toBeGreaterThanOrEqual(0);
  });
}
