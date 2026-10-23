import type { Page, Route, WebSocketRoute } from "@playwright/test";

const WEB_ORIGIN = new URL(process.env.ORCH_WEB_URL ?? "http://127.0.0.1:3100").origin;

export const OPERATOR_TOKEN = "op-dev-token";

export interface StudioStubOptions {
  operatorToken?: string | null;
  notices?: unknown[];
  preview?: unknown;
  legacyChat?: unknown;
  catalog?: { id: string; title_ru: string }[];
  handle?: (route: Route, url: URL) => Promise<boolean> | boolean;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function stubStudioApi(page: Page, opts: StudioStubOptions = {}) {
  await page.route(
    (url) => url.origin === WEB_ORIGIN && url.pathname.startsWith("/api/"),
    async (route) => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      if (opts.handle && (await opts.handle(route, url))) return;
      const path = url.pathname;
      if (path === "/api/auth/status") return json(route, { auth_required: false });
      if (path === "/api/auth/operator-token") return json(route, { token: opts.operatorToken ?? null });
      if (path === "/api/chat-notices" && method === "GET") {
        const notices = opts.notices ?? [];
        const ids = notices.map((n) => Number((n as { id?: number }).id) || 0);
        return json(route, { notices, count: notices.length, last_id: Math.max(0, ...ids) });
      }
      const read = path.match(/^\/api\/chat-notices\/(\d+)\/read$/);
      if (read && method === "POST") {
        const found = (opts.notices ?? []).find((n) => Number((n as { id?: number }).id) === Number(read[1]));
        if (!found) return json(route, { detail: "Уведомление не найдено" }, 404);
        return json(route, { notice: { ...(found as object), read: true, read_at: "2026-10-19T12:00:00Z" } });
      }
      if (path === "/api/action-layer/catalog") {
        const actions = (opts.catalog ?? []).map((a) => ({ ...a, description_ru: "", params_schema: {}, risk: "read" }));
        return json(route, { count: actions.length, actions });
      }
      if (path === "/api/action-layer/plans/preview" && method === "POST" && opts.preview) {
        return json(route, opts.preview);
      }
      if (path === "/api/projects" && method === "GET") return json(route, []);
      if (/^\/api\/db\/projects\/\d+\/orchestrator\/chat$/.test(path) && method === "POST" && opts.legacyChat) {
        return json(route, opts.legacyChat);
      }
      return json(route, { detail: "нет в тесте" }, 404);
    },
  );
}

export async function stubStudioSockets(page: Page): Promise<Map<string, WebSocketRoute>> {
  const sockets = new Map<string, WebSocketRoute>();
  await page.routeWebSocket(/\/ws\//, (ws) => {
    const channel = new URL(ws.url()).pathname.replace(/^\/ws\//, "");
    sockets.set(channel, ws);
  });
  return sockets;
}
