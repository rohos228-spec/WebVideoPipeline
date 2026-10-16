import { test, expect } from '@playwright/test';
import {
  STUDIO_PATH, PROVIDER_RE, collectProblems, assertNoProblems, loginAsQa,
} from './helpers';

/** Селектор панели настроек ноды — правим здесь при расхождении. */
const SETTINGS_PANEL = '[data-testid="node-step-params-panel"], [class*="node-step-params"]';
const NODE_SELECTOR = '.react-flow__node';

test('настройки каждой ноды открываются; в панели нет провайдеров и ошибок', async ({ page }) => {
  const problems = collectProblems(page);
  await loginAsQa(page);
  await page.goto(STUDIO_PATH);
  await page.waitForLoadState('networkidle');

  const nodes = page.locator(NODE_SELECTOR);
  const count = await nodes.count();
  expect(count, 'на канвасе нет нод — нужен seed тестового проекта').toBeGreaterThan(0);

  const visited: string[] = [];
  for (let i = 0; i < count; i++) {
    const node = nodes.nth(i);
    const title = (await node.innerText().catch(() => `node-${i}`)).split('\n')[0];
    await node.click({ timeout: 3000 }).catch(() => {});
    // некоторые ноды открывают настройки по двойному клику
    const panel = page.locator(SETTINGS_PANEL).first();
    if (!(await panel.isVisible().catch(() => false))) {
      await node.dblclick({ timeout: 3000 }).catch(() => {});
    }
    await expect(panel, `панель настроек ноды «${title}» не открылась`).toBeVisible({ timeout: 5000 });

    const text = await panel.innerText();
    const leak = text.match(PROVIDER_RE);
    expect(leak, `провайдер «${leak?.[0]}» в настройках ноды «${title}»`).toBeNull();

    visited.push(title);
    await page.keyboard.press('Escape').catch(() => {});
  }

  test.info().annotations.push({ type: 'nodes-visited', description: visited.join(' | ') });
  assertNoProblems(problems, `настройки нод (${STUDIO_PATH})`);
});
