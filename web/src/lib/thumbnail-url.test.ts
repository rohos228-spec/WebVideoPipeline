import assert from "node:assert/strict";
import { test } from "node:test";

import { toThumbnailUrl } from "./thumbnail-url.ts";

test("toThumbnailUrl returns undefined for empty/null values", () => {
  assert.equal(toThumbnailUrl(null), undefined);
  assert.equal(toThumbnailUrl(undefined), undefined);
  assert.equal(toThumbnailUrl(""), undefined);
});

test("toThumbnailUrl appends thumb=1 to /api/artifacts/.../file", () => {
  assert.equal(
    toThumbnailUrl("/api/artifacts/abc123uuid/file"),
    "/api/artifacts/abc123uuid/file?thumb=1"
  );
  assert.equal(
    toThumbnailUrl("/api/artifacts/abc123uuid/file?download=0"),
    "/api/artifacts/abc123uuid/file?download=0&thumb=1"
  );
});

test("toThumbnailUrl appends thumb=1 to /api/artifacts/project-cover/...", () => {
  assert.equal(
    toThumbnailUrl("/api/artifacts/project-cover/29"),
    "/api/artifacts/project-cover/29?thumb=1"
  );
  assert.equal(
    toThumbnailUrl("/api/artifacts/project-cover/29?v=123"),
    "/api/artifacts/project-cover/29?v=123&thumb=1"
  );
});

test("toThumbnailUrl appends thumb=1 to /api/files?path=...", () => {
  assert.equal(
    toThumbnailUrl("/api/files?path=/data/hero.png"),
    "/api/files?path=/data/hero.png&thumb=1"
  );
});

test("toThumbnailUrl does not duplicate thumb=1 if already present", () => {
  assert.equal(
    toThumbnailUrl("/api/files?path=/data/hero.png&thumb=1"),
    "/api/files?path=/data/hero.png&thumb=1"
  );
  assert.equal(
    toThumbnailUrl("/api/artifacts/abc/file?thumb=1"),
    "/api/artifacts/abc/file?thumb=1"
  );
});

test("toThumbnailUrl does not modify video or audio URLs", () => {
  assert.equal(
    toThumbnailUrl("/api/files?path=/data/video.mp4"),
    "/api/files?path=/data/video.mp4"
  );
  assert.equal(
    toThumbnailUrl("/api/files?path=/data/audio.mp3"),
    "/api/files?path=/data/audio.mp3"
  );
});

test("toThumbnailUrl leaves external non-api URLs intact", () => {
  assert.equal(
    toThumbnailUrl("https://example.com/image.png"),
    "https://example.com/image.png"
  );
});
