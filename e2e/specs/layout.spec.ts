import { test, expect, Page } from '@playwright/test';
import { STUDIO_PATH, loginAsQa } from './helpers';

type Box = { x: number; y: number; width: number; height: number };

const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
];

async function boxOf(page: Page, sel: string): Promise<Box | null> {
  const l = page.locator(sel).first();
  if (!(await l.isVisible().catch(() => false))) return null;
  return l.boundingBox();
}

function overlap(a: Box | null, b: Box | null): boolean {
  if (!a || !b) return false;
  return a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1;
}

for (const vp of VIEWPORTS) {
  test(`раскладка ${vp.width}x${vp.height}: панели не перекрывают канвас, ноды в зоне видимости`, async ({ page }) => {
    await page.setViewportSize(vp);
    await loginAsQa(page);
    await page.goto(STUDIO_PATH);
    await page.waitForLoadState('networkidle');
    const nodes = page.locator('.react-flow__node');
    await expect(nodes.first()).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(900);

    const canvas = await boxOf(page, '.react-flow');
    expect(canvas, 'канвас не найден').not.toBeNull();
    for (const sel of ['[data-testid="project-sidebar"]'] /* orchestrator + right float overlay the canvas by design (housepc); nodes must stay uncovered (checked below) */) {
      expect(overlap(canvas, await boxOf(page, sel)), `${sel} перекрывает канвас`).toBeFalsy();
    }
    // оркестратор виден и его можно открыть/закрыть
    await expect(page.locator('[data-testid="orchestrator-panel"]')).toBeVisible();
    // housepc design: no inspector, no API/Bug/Logs/Network/Base rail buttons; right float with run controls + streams
    await expect(page.locator('[data-testid="inspector"]')).toHaveCount(0);
    for (const t of ['API', 'Логи', 'Сеть', 'База']) await expect(page.locator(`button[title="${t}"], button:has-text("${t}")`).filter({ hasText: new RegExp(`^${t}$`) })).toHaveCount(0);
    await expect(page.locator('[data-testid="right-float"] #vp-right-controls')).toHaveCount(1);

    const n = await nodes.count();
    const c = canvas!;
    for (let i = 0; i < n; i++) {
      const nb = await nodes.nth(i).boundingBox();
      if (!nb) continue;
      const cx = nb.x + nb.width / 2;
      const cy = nb.y + nb.height / 2;
      expect(
        cx >= c.x && cx <= c.x + c.width && cy >= c.y && cy <= c.y + c.height,
        `нода #${i} вне видимой области канваса (${Math.round(cx)},${Math.round(cy)})`,
      ).toBeTruthy();
      const onCanvas = await page.evaluate(([x, y]) => {
        const el = document.elementFromPoint(x, y);
        return !!el && !!el.closest('.react-flow');
      }, [cx, cy]);
      expect(onCanvas, `нода #${i} перекрыта панелью`).toBeTruthy();
    }
  });
}
