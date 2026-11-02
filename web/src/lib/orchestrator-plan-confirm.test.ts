/**
 * A7 · Ц1С2: подтверждение плана на экране.
 *
 * Ядро (A6) кладёт план в payload существующего `approval_required` —
 * контракт не меняется: `kind="approval_plan"`, `plan: [{action, risk, cost, group}]`,
 * `plan_id: "plan-<hex>"`, `change_id: "orch-appr-*"`. Approve идёт по change_id.
 * Проверяем, что редуктор экрана поднимает шаги и не ломает карточку, когда
 * поля `plan` нет (старый бэкенд / не-plan approval).
 *
 * Запуск (в песочнице изоляция воркеров запрещена):
 *   node --experimental-strip-types --import ./scripts/ts-resolve.mjs \
 *     --test --test-isolation=none src/lib/orchestrator-plan-confirm.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import type { OrchEvent } from "./orchestratorTypes.ts";
import {
  initialOrchScreen,
  readApprovalPlan,
  readPlanSteps,
  reduceOrchScreen,
  type OrchScreenState,
} from "./orchestratorSession.ts";

function event(payload: Record<string, unknown>, seq = 7): OrchEvent {
  return { type: "approval_required", task_id: 42, seq, payload };
}

function apply(state: OrchScreenState, payload: Record<string, unknown>): OrchScreenState {
  return reduceOrchScreen(state, { type: "event", event: event(payload) });
}

test("readPlanSteps: формат CheckpointData.plan (snake_case) не сломан", () => {
  const steps = readPlanSteps([
    { action: "orch.node_run_step", risk: "paid", estimated_cost: 2.5, parallel_group: "reads" },
    { action: "orch.node_get_node", paid: false, mutating: false },
  ]);
  assert.equal(steps.length, 2);
  assert.equal(steps[0].risk, "paid");
  assert.equal(steps[0].estimatedCost, 2.5);
  assert.equal(steps[0].parallelGroup, "reads");
  assert.equal(steps[1].estimatedCost, 0);
});

test("readApprovalPlan: компактный формат A6 (cost/group) разобран", () => {
  const steps = readApprovalPlan([
    { action: "orch.node_run_step", risk: "paid", cost: 3.25, group: "reads" },
    { action: "orch.node_set_prompt_variant", risk: "low", cost: 0, group: null },
  ]);
  assert.equal(steps.length, 2);
  assert.equal(steps[0].action, "orch.node_run_step");
  assert.equal(steps[0].estimatedCost, 3.25);
  assert.equal(steps[0].parallelGroup, "reads");
  assert.equal(steps[1].parallelGroup, null);
});

test("readApprovalPlan: мусор и пустое поле не создают шагов", () => {
  assert.deepEqual(readApprovalPlan(undefined), []);
  assert.deepEqual(readApprovalPlan({ plan: [] }), []);
  assert.deepEqual(readApprovalPlan([{ no_action: true }, null, "x"]), []);
});

test("approval_required с планом: карточка получает шаги, plan_id не мешает approve", () => {
  const state = apply(initialOrchScreen("s1"), {
    kind: "approval_plan",
    reason: "plan_approval",
    change_id: "orch-appr-42-1",
    plan_id: "plan-0123456789ab",
    plan_kind: "initial",
    needs_approval: true,
    cost_low: 3.25,
    cost_high: 3.25,
    preview: ["orch.node_run_step"],
    plan: [
      { action: "orch.node_run_step", risk: "paid", cost: 3.25, group: null },
      { action: "orch.node_set_prompt_variant", risk: "low", cost: 0, group: null },
    ],
  });
  assert.equal(state.approvals.length, 1);
  const card = state.approvals[0];
  assert.equal(card.changeId, "orch-appr-42-1");
  assert.equal(card.plan.length, 2);
  assert.equal(card.plan[0].estimatedCost, 3.25);
  assert.equal(card.resolved, null);
});

test("approval_required без плана: карточка прежняя, plan пустой", () => {
  const state = apply(initialOrchScreen("s1"), {
    reason: "approval_required",
    change_id: "orch-appr-42-2",
    action: "orch.node_run_step",
    risk: "paid",
  });
  const card = state.approvals[0];
  assert.deepEqual(card.plan, []);
  assert.equal(card.changeId, "orch-appr-42-2");
});

test("повторный тот же change_id: карточка одна, план не дублируется", () => {
  const plan = [{ action: "orch.node_run_step", risk: "paid", cost: 1 }];
  let state = apply(initialOrchScreen("s1"), {
    kind: "approval_plan",
    change_id: "orch-appr-42-3",
    plan,
  });
  state = apply(state, {
    kind: "approval_plan",
    change_id: "orch-appr-42-3",
    plan_kind: "revise",
    plan,
  });
  assert.equal(state.approvals.length, 1);
  assert.equal(state.approvals[0].plan.length, 1);
});
