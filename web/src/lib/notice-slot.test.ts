import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChatNotice } from "./chat-notices.ts";
import type { ChatMessage } from "./orchestratorSession.ts";
import { noticeSlotByTime } from "./notice-slot.ts";

const msg = (id: string, at?: string): ChatMessage => ({ id, role: "assistant", text: id, taskId: null, at });
const notice = (created_at: string | null): ChatNotice => ({ id: 1, created_at }) as unknown as ChatNotice;

test("карточка встаёт перед первой репликой позже неё", () => {
  const messages = [
    msg("a", "2026-11-03T10:00:00.000000Z"),
    msg("b", "2026-11-03T10:05:00.000000Z"),
    msg("c", "2026-11-03T10:20:00.000000Z"),
  ];
  assert.equal(noticeSlotByTime(notice("2026-11-03T10:12:00Z"), messages), 2);
  assert.equal(noticeSlotByTime(notice("2026-11-03T09:00:00Z"), messages), 0);
});

test("карточка новее всей переписки — в конец ленты", () => {
  const messages = [msg("a", "2026-11-03T10:00:00.000000Z")];
  assert.equal(noticeSlotByTime(notice("2026-11-03T10:12:00Z"), messages), 1);
});

test("реплики без времени пропускаются", () => {
  const messages = [msg("a", "2026-11-03T10:00:00Z"), msg("live"), msg("b", "2026-11-03T10:30:00Z")];
  assert.equal(noticeSlotByTime(notice("2026-11-03T10:12:00Z"), messages), 2);
});

test("сравнить не с чем — null, остаётся прежнее место", () => {
  assert.equal(noticeSlotByTime(notice(null), [msg("a", "2026-11-03T10:00:00Z")]), null);
  assert.equal(noticeSlotByTime(notice("2026-11-03T10:12:00Z"), [msg("a"), msg("b")]), null);
  assert.equal(noticeSlotByTime(notice("2026-11-03T10:12:00Z"), []), null);
});
