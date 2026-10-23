import assert from "node:assert/strict";
import { test } from "node:test";

import { clearToken } from "./identity-api.ts";
import { chooseChatToken, ORCH_TOKEN_STORAGE_KEY } from "./orchestratorToken.ts";

test("login token wins over a chat token left in the browser", () => {
  assert.equal(chooseChatToken("jwt-of-current-user", "orch-token-of-previous-user"), "jwt-of-current-user");
});

test("separate chat token only without a login", () => {
  assert.equal(chooseChatToken(null, "orch-dev-token"), "orch-dev-token");
  assert.equal(chooseChatToken("", " orch-dev-token "), "orch-dev-token");
  assert.equal(chooseChatToken(undefined, null), "");
});

test("logout also forgets the chat token", () => {
  const store = new Map<string, string>([
    ["vp.token", "jwt"],
    [ORCH_TOKEN_STORAGE_KEY, "orch-token-of-this-user"],
  ]);
  const g = globalThis as unknown as { window?: unknown };
  const saved = g.window;
  g.window = {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
    dispatchEvent: () => true,
  };
  try {
    clearToken();
  } finally {
    g.window = saved;
  }
  assert.equal(store.has("vp.token"), false);
  assert.equal(store.has(ORCH_TOKEN_STORAGE_KEY), false);
});
