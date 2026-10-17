import { Page, expect } from '@playwright/test';

/** Страницы из ТЗ (раздел B). */
export const PAGES = [
  '/', '/home', '/login', '/register', '/verify-email',
  '/forgot-password', '/reset-password', '/profile',
];

/** Путь к канвасу студии — ЕДИНСТВЕННОЕ место правки, если роут другой. */
export const STUDIO_PATH = '/studio';

/** Запрещённые имена провайдеров (ТЗ п.22). */
export const PROVIDER_RE =
  /\b(52pay|kie|outsee|vibecode|wavespeed|elevenlabs|anthropic|openai)\b/i;

/** Небезопасные кнопки — НЕ кликаем (оплата, удаление, платная генерация). */
export const UNSAFE_BUTTON_RE =
  /оплат|купить|checkout|pay\b|подписа|оформить|удал|delete|remove|очист|clear all|запустить|сгенерир|generate|run\b|старт|start render/i;

export type PageProblems = { consoleErrors: string[]; serverErrors: string[] };

/** Вешает сборщики ошибок консоли и сетевых 5xx на страницу. */
export function collectProblems(page: Page): PageProblems {
  const problems: PageProblems = { consoleErrors: [], serverErrors: [] };
  page.on('console', (msg) => {
    if (msg.type() === 'error') problems.consoleErrors.push(msg.text());
  });
  page.on('response', (resp) => {
    if (resp.status() >= 500) {
      problems.serverErrors.push(`${resp.status()} ${resp.request().method()} ${resp.url()}`);
    }
  });
  return problems;
}

export function assertNoProblems(problems: PageProblems, where: string) {
  expect(problems.serverErrors, `5xx на ${where}`).toEqual([]);
  expect(problems.consoleErrors, `ошибки консоли на ${where}`).toEqual([]);
}

/** Проверяет видимый текст страницы на имена провайдеров. */
export async function assertNoProviderNames(page: Page, where: string) {
  const text = await page.locator('body').innerText();
  const match = text.match(PROVIDER_RE);
  expect(match, `провайдер «${match?.[0]}» виден на ${where}`).toBeNull();
}

/** Логин тестовым пользователем (создаётся seed-скриптом перед e2e). */
export async function loginAsQa(page: Page) {
  await page.goto('/login');
  await page.getByLabel(/почта|email/i).fill(process.env.QA_EMAIL ?? 'qa@test.local');
  await page.getByLabel(/пароль|password/i).fill(process.env.QA_PASSWORD ?? 'Str0ng!pass');
  await page.getByRole('button', { name: /войти|login/i }).click();
  await page.waitForLoadState('networkidle');
}
