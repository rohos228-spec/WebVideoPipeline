import assert from "node:assert/strict";
import { test } from "node:test";

import { findMarkdownLink, safeHref } from "./chat-links.ts";

test("ссылка с пробелом после скобки остаётся ссылкой", () => {
  const found = findMarkdownLink("[Скачать отчёт в HTML]( /api/projects/reports/projects_report_20261023-143326.html) · ещё");
  assert.ok(found);
  assert.equal(found.label, "Скачать отчёт в HTML");
  assert.equal(found.href, "/api/projects/reports/projects_report_20261023-143326.html");
});

test("ссылка без пробела и с пробелом перед закрывающей скобкой", () => {
  assert.equal(findMarkdownLink("[x](/api/projects/5/reports/a.xlsx)")?.href, "/api/projects/5/reports/a.xlsx");
  assert.equal(findMarkdownLink("[x]( /api/projects/5/reports/a.xlsx )")?.href, "/api/projects/5/reports/a.xlsx");
});

test("общий отчёт и отчёт проекта открываются, чужие адреса нет", () => {
  assert.equal(safeHref("/api/projects/reports/projects_report_1.xlsx"), "/api/projects/reports/projects_report_1.xlsx");
  assert.equal(safeHref(" /api/projects/7/reports/nodes_report_7.html "), "/api/projects/7/reports/nodes_report_7.html");
  assert.equal(safeHref("/api/projects/reports/../secret.html"), null);
  assert.equal(safeHref("/api/settings"), null);
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("https://example.com/a"), "https://example.com/a");
});
