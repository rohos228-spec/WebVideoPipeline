import assert from "node:assert/strict";
import { test } from "node:test";

import { neutralEnd, pickTaskBadge } from "./orchestratorBadge.ts";
import type { ChatMessage, TaskView } from "./orchestratorSession.ts";

function task(id: string, status: string, lastSeq: number, extra: Partial<TaskView> = {}): TaskView {
  return {
    id,
    title: "Задача",
    status: status as TaskView["status"],
    steps: [],
    checkpoints: [],
    validators: [],
    summary: "",
    error: "",
    reason: "",
    plan: [],
    lastSeq,
    stepSeq: 0,
    ...extra,
  };
}

function reply(seq: number): ChatMessage {
  return {
    id: `m-${seq}`,
    role: "assistant",
    text: "Пожалуйста.",
    taskId: null,
    seq,
    messageId: null,
    streaming: false,
    final: true,
    kind: "text",
  };
}

test("a new message hides the finished task's status while waiting for the answer", () => {
  const old = task("1", "done", 10);
  const badge = pickTaskBadge([old], { echo: "привет", text: "принял, разбираю", seq: 20 }, []);
  assert.equal(badge.kind, "thinking");
});

test("an answer without a task does not bring back the old red status", () => {
  const old = task("1", "failed", 10);
  const badge = pickTaskBadge([old], { echo: "?", text: "", seq: 20 }, [reply(21)]);
  assert.equal(badge.kind, "none");
});

test("a new task newer than the message is shown", () => {
  const old = task("1", "done", 10);
  const fresh = task("2", "planning", 22);
  const badge = pickTaskBadge([old, fresh], { echo: "запусти", text: "", seq: 20 }, []);
  assert.deepEqual(badge, { kind: "task", task: fresh });
});

test("a question during a running task keeps the running task", () => {
  const running = task("3", "executing", 10);
  const badge = pickTaskBadge([running], { echo: "как там?", text: "", seq: 20 }, [reply(21)]);
  assert.deepEqual(badge, { kind: "task", task: running });
});

test("rejection and a not understood request are grey, not an error", () => {
  assert.equal(neutralEnd(task("1", "cancelled", 1, { reason: "rejected" })), "cancelled");
  assert.equal(neutralEnd(task("1", "cancelled", 1, { reason: "empty_plan" })), "unclear");
  assert.equal(neutralEnd(task("1", "cancelled", 1, { reason: "answered" })), "answered");
  assert.equal(neutralEnd(task("1", "failed", 1, { error: "step_failed" })), null);
});
