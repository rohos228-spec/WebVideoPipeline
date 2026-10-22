import { test } from "node:test";
import assert from "node:assert/strict";
import type { WorkflowDetail } from "./types.ts";
import { workflowStructureKey } from "./node-run-status.ts";

function wf(label: string, x: number, groupTitle = "Группа"): WorkflowDetail {
  return {
    id: 1,
    nodes: [
      { id: "a", type: "excel_gpt", position: { x, y: 0 }, data: { label, slotIndex: 1 } },
      {
        id: "b",
        type: "images",
        position: { x: x + 200, y: 40 },
        data: { label: "Картинки", groupId: "g1", groupTitle },
      },
    ],
    edges: [{ id: "e1", source: "a", target: "b" }],
  } as unknown as WorkflowDetail;
}

test("workflowStructureKey: меняется при переименовании ноды", () => {
  assert.notEqual(workflowStructureKey(wf("Сценарий", 0)), workflowStructureKey(wf("Сюжет", 0)));
});

test("workflowStructureKey: меняется при смене группы", () => {
  assert.notEqual(
    workflowStructureKey(wf("Сценарий", 0, "Группа")),
    workflowStructureKey(wf("Сценарий", 0, "Другая")),
  );
});

test("workflowStructureKey: не меняется при перетаскивании (только позиции)", () => {
  assert.equal(workflowStructureKey(wf("Сценарий", 0)), workflowStructureKey(wf("Сценарий", 500)));
});
