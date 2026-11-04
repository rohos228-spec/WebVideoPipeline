import assert from "node:assert/strict";
import { test } from "node:test";

import type { OrchEvent } from "./orchestratorTypes.ts";
import { initialOrchScreen, reduceOrchScreen, type OrchScreenState } from "./orchestratorSession.ts";

function ev(type: OrchEvent["type"], taskId: number, seq: number, payload: Record<string, unknown>): OrchEvent {
  return { type, task_id: taskId, seq, session_id: "studio-p4", payload } as OrchEvent;
}

function apply(state: OrchScreenState, event: OrchEvent): OrchScreenState {
  return reduceOrchScreen(state, { type: "event", event });
}

test("плашка пустого плана старой задачи закрывается, когда пошла новая задача", () => {
  let s = apply(initialOrchScreen("studio-p4"), ev("agent_status", 36, 738, { status: "planning" }));
  s = apply(s, ev("task_failed", 36, 739, { error: "empty_plan" }));
  assert.equal(s.approvals.filter((a) => a.resolved == null).length, 1);
  s = apply(s, ev("agent_status", 36, 740, { status: "failed" }));
  assert.equal(s.approvals.filter((a) => a.resolved == null).length, 1, "своя задача плашку не закрывает");
  s = apply(s, ev("agent_status", 1095, 1160, { status: "planning" }));
  assert.deepEqual(
    s.approvals.map((a) => [a.reason, a.resolved]),
    [["empty_plan", "closed"]],
  );
});

test("карточка с кнопками старой задачи не закрывается сама", () => {
  let s = apply(
    initialOrchScreen("studio-p4"),
    ev("approval_required", 50, 10, { reason: "risk", change_id: "orch-appr-50-1", title: "Шаг" }),
  );
  s = apply(s, ev("agent_status", 51, 11, { status: "planning" }));
  assert.equal(s.approvals.filter((a) => a.resolved == null).length, 1);
});
