import { test, expect } from '@playwright/test';
import { loginAsQa, collectProblems, assertNoProblems } from './helpers';

/** ТЗ п.45–46: выплаты и хранилище — интерфейс оставить, метка «в разработке». */
const WIP_RE = /в разработке/i;

const SECTIONS = [
  { name: 'выплаты',   open: /выплат/i,   panel: '[data-testid="payouts-panel"], [class*="payouts"]' },
  { name: 'хранилище', open: /хранилищ/i, panel: '[data-testid="storage-panel"], [class*="storage"]' },
];

for (const s of SECTIONS) {
  test(`раздел «${s.name}» виден и помечен «в разработке»`, async ({ page }) => {
    const problems = collectProblems(page);
    await loginAsQa(page);
    await page.goto('/profile');
    await page.waitForLoadState('networkidle');

    const trigger = page.getByRole('button', { name: s.open })
      .or(page.getByRole('link', { name: s.open })).first();
    await expect(trigger, `нет входа в раздел «${s.name}»`).toBeVisible();
    await trigger.click();

    const panel = page.locator(s.panel).first();
    await expect(panel, `панель «${s.name}» не открылась`).toBeVisible();
    await expect(panel, `нет метки «в разработке» в «${s.name}»`).toContainText(WIP_RE);

    assertNoProblems(problems, `раздел «${s.name}»`);
  });
}
