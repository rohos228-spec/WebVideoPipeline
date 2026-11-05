import { createReadStream } from "node:fs";
import { access, stat } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chatEvent, readFixtures } from "./fixtures.mjs";

// Only web/out is served. No proxy, backend process, builds or writes.
const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const outRoot = path.join(webRoot, "out");
const port = Number(process.env.MOBILE_WEB_PORT ?? "3197");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid MOBILE_WEB_PORT");
await access(path.join(outRoot, "index.html"));
const contentTypes = {
  ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8",
  ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
  ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
  ".mp4": "video/mp4", ".mp3": "audio/mpeg",
};
const sockets = new Set();
const streams = new Set();
function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
    // Fail closed even if somebody visits this test server without Playwright's guard.
    if (req.method !== "GET" && req.method !== "HEAD") {
      return json(res, 405, { detail: "Mobile fixture server is read-only" });
    }
    if (url.pathname === "/__mobile_health") return json(res, 200, { mobile_fixture_server: true });
    if (url.pathname.startsWith("/api/")) {
      const stream = url.pathname.match(/^\/api\/orchestrator\/chat\/([^/]+)\/stream$/);
      if (stream) {
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
        res.flushHeaders();
        const event = chatEvent(decodeURIComponent(stream[1]));
        const lastId = Number(req.headers["last-event-id"] ?? 0);
        if (lastId < event.seq) res.write(`id: ${event.seq}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
        const timer = setInterval(() => res.write(": fixture heartbeat\n\n"), 10_000);
        streams.add(res);
        res.on("close", () => { clearInterval(timer); streams.delete(res); });
        return;
      }
      if (Object.hasOwn(readFixtures, url.pathname)) return json(res, 200, readFixtures[url.pathname]);
      return json(res, 404, { detail: `No mobile read fixture: ${url.pathname}` });
    }
    if (url.pathname.startsWith("/ws/")) return json(res, 404, { detail: "WebSockets are mocked by Playwright" });

    const decoded = decodeURIComponent(url.pathname);
    if (decoded.includes("\0") || decoded.includes("\\")) return json(res, 400, { detail: "Invalid path" });
    const target = path.resolve(outRoot, `.${decoded}`);
    if (target !== outRoot && !target.startsWith(`${outRoot}${path.sep}`)) return json(res, 403, { detail: "Outside static export" });
    const candidates = target === outRoot ? [path.join(outRoot, "index.html")] : path.extname(target) ? [target] : [target, `${target}.html`, path.join(target, "index.html")];
    for (const candidate of candidates) {
      let info;
      try { info = await stat(candidate); } catch { continue; }
      if (!info.isFile()) continue;
      res.writeHead(200, {
        "Content-Type": contentTypes[path.extname(candidate)] ?? "application/octet-stream",
        "Content-Length": info.size,
        "Cache-Control": "no-store",
      });
      if (req.method === "HEAD") res.end();
      else {
        const file = createReadStream(candidate);
        file.on("error", () => res.destroy());
        file.pipe(res);
      }
      return;
    }
    return json(res, 404, { detail: "Not found in web/out; build the worktree before testing" });
  } catch (error) {
    if (!res.headersSent) json(res, 500, { detail: String(error) });
    else res.destroy();
  }
});
server.on("connection", (socket) => { sockets.add(socket); socket.on("close", () => sockets.delete(socket)); });
server.on("upgrade", (_req, socket) => socket.destroy());
server.listen(port, "127.0.0.1", () => console.log(`Mobile static fixtures: http://127.0.0.1:${port}`));

let closing = false;
function shutdown() {
  if (closing) return;
  closing = true;
  for (const response of streams) response.end();
  server.close(() => process.exit(0));
  for (const socket of sockets) socket.destroy();
  setTimeout(() => process.exit(1), 3_000).unref();
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
