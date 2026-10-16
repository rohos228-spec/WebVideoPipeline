import { expect, test } from "@playwright/test";

test("billing page: balance, plans, yearly discount, in development", async ({ page }) => {
  await page.goto("/billing");
  await expect(page.getByText("Баланс VIA")).toBeVisible();
  await expect(page.getByText("−25% при оплате за год")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Заводы" })).toBeVisible();
  await expect(page.getByTestId("plan-conveyor")).toBeVisible();
  await expect(page.getByText("в разработке").first()).toBeVisible();
  const monthPrice = await page.getByTestId("plan-start").innerText();
  await page.getByRole("button", { name: "Год" }).click();
  await expect(page.getByTestId("plan-start")).toContainText("/ год");
  expect(await page.getByTestId("plan-start").innerText()).not.toEqual(monthPrice);
  const body = await page.locator("body").innerText();
  expect(body).not.toMatch(/outsee|kie|52pay|vibecode|yookassa|юkassa/i);
});

test("topup goes to sandbox checkout", async ({ page }) => {
  await page.goto("/billing");
  await page.getByRole("button", { name: "890 ₽" }).click();
  await page.getByRole("button", { name: "Пополнить" }).click();
  await page.waitForURL(/payment=mock|checkout/);
});
