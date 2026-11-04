import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatRunOverlayLabel,
  humanizeSlug,
  nodeRunsFromPayload,
} from "./format-labels.ts";

test("humanizeSlug tolerates undefined and null", () => {
  assert.equal(humanizeSlug(undefined), "");
  assert.equal(humanizeSlug(null), "");
});

test("humanizeSlug still rewrites a real slug", () => {
  assert.equal(humanizeSlug("n_img_pr"), "img pr");
});

test("empty or slug-less run is the empty overlay state", () => {
  const empty: unknown[] = [
    undefined,
    null,
    [],
    {},
    { id: 1 },
    { status: "running" },
    { id: 1, status: "" },
    { id: 1, status: "   " },
  ];
  for (const payload of empty) {
    assert.equal(formatRunOverlayLabel(payload), null);
    assert.deepEqual(nodeRunsFromPayload(payload), []);
  }
});

test("a run with a status slug keeps the overlay label", () => {
  const run = {
    id: 4,
    status: "running",
    node_runs: [{ node_key: "n1" }],
  };
  assert.equal(formatRunOverlayLabel(run), "#4 · работает");
  assert.equal(nodeRunsFromPayload(run).length, 1);
});

test("a run without node_runs does not throw", () => {
  const run = { id: 2, status: "paused" };
  assert.equal(formatRunOverlayLabel(run), "#2 · пауза");
  assert.deepEqual(nodeRunsFromPayload(run), []);
});

test("workflow and node run statuses are in Russian", () => {
  assert.equal(formatRunOverlayLabel({ id: 26, status: "waiting_hitl" }), "#26 · ждёт проверки");
  assert.equal(formatRunOverlayLabel({ id: 27, status: "done" }), "#27 · готово");
  assert.equal(formatRunOverlayLabel({ id: 28, status: "queued" }), "#28 · в очереди");
  assert.equal(formatRunOverlayLabel({ id: 29, status: "skipped" }), "#29 · пропущен");
});

test("running run of a paused project reads as paused", () => {
  assert.equal(formatRunOverlayLabel({ id: 30, status: "running" }, "paused"), "#30 · пауза");
  assert.equal(formatRunOverlayLabel({ id: 30, status: "running" }, "scripting"), "#30 · работает");
  assert.equal(formatRunOverlayLabel({ id: 30, status: "done" }, "paused"), "#30 · готово");
});
