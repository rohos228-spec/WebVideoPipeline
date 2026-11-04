import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldHideMoney } from "./money-visibility.ts";

test("no /api/me yet hides money", () => {
  assert.equal(shouldHideMoney(undefined), true);
  assert.equal(shouldHideMoney(null), true);
});

test("owner mode shows money although role is empty", () => {
  assert.equal(shouldHideMoney({ accounts_enabled: false, role: "" }), false);
});

test("accounts mode shows money only to admin", () => {
  assert.equal(shouldHideMoney({ accounts_enabled: true, role: "admin" }), false);
  assert.equal(shouldHideMoney({ accounts_enabled: true, role: "member" }), true);
  assert.equal(shouldHideMoney({ accounts_enabled: true, role: "" }), true);
});

test("old backend without accounts_enabled keeps the role rule", () => {
  assert.equal(shouldHideMoney({ role: "admin" }), false);
  assert.equal(shouldHideMoney({ role: "" }), true);
});
