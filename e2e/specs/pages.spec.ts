import { test, expect } from '@playwright/test';
import {
  PAGES, collectProblems, assertNoProblems, assertNoProviderNames,
} from './helpers';

for (const path of PAGES) {
  test(`страница ${path}: открывается, без ошибок консоли/5xx, без провайдеров`, async ({ page }) => {
    const problems = collectProblems(page);
    const resp = await page.goto(path);
    expect(resp, `нет ответа на ${path}`).not.toBeNull();
    expect(resp!.status(), `HTTP-статус ${path}`).toBeLessThan(500);
    await page.waitForLoadState('networkidle');
    assertNoProblems(problems, path);
    await assertNoProviderNames(page, path);
  });
}
