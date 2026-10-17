import { test, expect } from '@playwright/test';
import {
  STUDIO_PATH, PROVIDER_RE, collectProblems, assertNoProblems, loginAsQa,
} from './helpers';

/** Селектор панели настроек ноды — правим здесь при расхождении. */
const SETTINGS_PANEL = '[data-testid="node-step-params-panel"], [class*="node-step-params"]';
const NODE_SELECTOR = '.react-flow__node';

/** P2: без «свернуть панель» и без DOM-кликов — ноды должны быть кликабельны на любом экране. */
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
];

for (const vp of VIEWPORTS) {
  test(`настройки каждой ноды открываются (${vp.width}x${vp.height}); в панели нет провайдеров и ошибок`, async ({ page }) => {
    await page.setViewportSize(vp);
    const problems = collectProblems(page);
    await loginAsQa(page);
    await page.goto(STUDIO_PATH);
    await page.waitForLoadState('networkidle');

    const nodes = page.locator(NODE_SELECTOR);
    await expect(nodes.first(), 'на канвасе нет нод — нужен seed тестового проекта').toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(800);
    const count = await nodes.count();

    const visited: string[] = [];
    for (let i = 0; i < count; i++) {
      const node = nodes.nth(i);
      const title = (await node.innerText().catch(() => `node-${i}`)).split('\n')[0];
      await node.click({ timeout: 5000 });
      const panel = page.locator(SETTINGS_PANEL).first();
      // некоторые ноды открывают настройки по двойному клику
      if (!(await panel.isVisible().catch(() => false))) {
        await node.dblclick({ timeout: 5000 });
      }
      await expect(panel, `панель настроек ноды «${title}» не открылась`).toBeVisible({ timeout: 5000 });

      const text = await panel.innerText();
      const leak = text.match(PROVIDER_RE);
      expect(leak, `провайдер «${leak?.[0]}» в настройках ноды «${title}»`).toBeNull();

      visited.push(title);
      await page.keyboard.press('Escape');
    }

    test.info().annotations.push({ type: 'nodes-visited', description: visited.join(' | ') });
    assertNoProblems(problems, `настройки нод (${STUDIO_PATH}, ${vp.width}x${vp.height})`);
  });
}
