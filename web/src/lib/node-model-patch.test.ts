import assert from "node:assert/strict";
import { test } from "node:test";

import { applyGlobalTextModel, applyNodeDataPatch } from "./node-model-patch.ts";

type N = { id: string; data: Record<string, unknown> };

function graph(): N[] {
  return [
    { id: "n_plan", data: { type: "plan", modelId: "old" } },
    { id: "n_script", data: { type: "script", modelId: "old" } },
    { id: "n_split", data: { type: "split", modelId: "old" } },
    { id: "n_img", data: { type: "image", modelId: "img-model" } },
  ];
}

const isText = (n: N) => n.data.type !== "image";

test("picking a model on n_plan changes only n_plan and pins it", () => {
  const before = graph();
  const after = applyNodeDataPatch(before, "n_plan", {
    modelId: "gpt-new",
    modelChannel: "stable",
  });
  assert.equal(after[0].data.modelId, "gpt-new");
  assert.equal(after[0].data.modelChannel, "stable");
  assert.equal(after[0].data.modelPinned, true);
  assert.equal(after[0].data.type, "plan");
  assert.equal(after[1], before[1]);
  assert.equal(after[2], before[2]);
  assert.equal(after[3], before[3]);
  assert.equal(after[1].data.modelId, "old");
  assert.equal(after[2].data.modelId, "old");
  assert.equal(before[0].data.modelId, "old");
});

test("patch without modelId does not set modelPinned", () => {
  const after = applyNodeDataPatch(graph(), "n_plan", { label: "Сценарий 2" });
  assert.equal(after[0].data.label, "Сценарий 2");
  assert.equal(after[0].data.modelPinned, undefined);
  assert.equal(after[0].data.modelId, "old");
});

test("global model updates unpinned text nodes only", () => {
  const pinned = applyNodeDataPatch(graph(), "n_plan", { modelId: "mine" });
  const after = applyGlobalTextModel(pinned, "global", isText);
  assert.equal(after[0].data.modelId, "mine");
  assert.equal(after[1].data.modelId, "global");
  assert.equal(after[2].data.modelId, "global");
  assert.equal(after[3].data.modelId, "img-model");
  assert.equal(after[3], pinned[3]);
});

test("global model returns same array when nothing changes", () => {
  const nodes = graph().map((n) => ({ ...n, data: { ...n.data, modelPinned: true } }));
  assert.equal(applyGlobalTextModel(nodes, "global", isText), nodes);
});
