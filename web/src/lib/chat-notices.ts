import { http } from "./api";

export type NoticeLevel = "info" | "success" | "warning" | "error";

export interface NoticeAction {
  action_id: string;
  label: string;
  params: Record<string, unknown>;
  risk?: string;
  needs_approval?: boolean;
}

export interface ChatNotice {
  id: number;
  project_id: number | null;
  level: NoticeLevel;
  title: string;
  text: string;
  kind: string;
  created_at: string | null;
  suggested_actions: NoticeAction[];
  read: boolean;
  read_at: string | null;
}

export interface NoticeListResponse {
  notices: ChatNotice[];
  count: number;
  last_id: number;
}

export const listChatNotices = (projectId: number | null, sinceId = 0, limit = 50) => {
  const q = new URLSearchParams({ since_id: String(sinceId), limit: String(limit) });
  if (projectId != null) q.set("project_id", String(projectId));
  return http<NoticeListResponse>(`/api/chat-notices?${q.toString()}`);
};

export const markChatNoticeRead = (id: number) =>
  http<{ notice: ChatNotice }>(`/api/chat-notices/${id}/read`, { method: "POST", body: "{}" });

const LEVELS = new Set<NoticeLevel>(["info", "success", "warning", "error"]);

export function readNotice(raw: unknown): ChatNotice | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "number" ? o.id : Number(o.id);
  if (!Number.isFinite(id) || id <= 0) return null;
  const pid = o.project_id == null ? null : Number(o.project_id);
  const level = LEVELS.has(o.level as NoticeLevel) ? (o.level as NoticeLevel) : "info";
  const actions = Array.isArray(o.suggested_actions)
    ? o.suggested_actions.flatMap((a): NoticeAction[] => {
        if (!a || typeof a !== "object") return [];
        const r = a as Record<string, unknown>;
        if (typeof r.action_id !== "string" || !r.action_id) return [];
        return [
          {
            action_id: r.action_id,
            label: typeof r.label === "string" && r.label ? r.label : "Открыть действие",
            params: r.params && typeof r.params === "object" ? (r.params as Record<string, unknown>) : {},
            risk: typeof r.risk === "string" ? r.risk : undefined,
            needs_approval: r.needs_approval === true,
          },
        ];
      })
    : [];
  return {
    id,
    project_id: pid != null && Number.isFinite(pid) ? pid : null,
    level,
    title: typeof o.title === "string" ? o.title : "",
    text: typeof o.text === "string" ? o.text : "",
    kind: typeof o.kind === "string" ? o.kind : "",
    created_at: typeof o.created_at === "string" ? o.created_at : null,
    suggested_actions: actions,
    read: o.read === true || typeof o.read_at === "string",
    read_at: typeof o.read_at === "string" ? o.read_at : null,
  };
}

export function noticeInScope(notice: ChatNotice, projectId: number | null): boolean {
  return projectId == null || notice.project_id == null || notice.project_id === projectId;
}

export function mergeNotices(current: ChatNotice[], incoming: ChatNotice[]): ChatNotice[] {
  if (incoming.length === 0) return current;
  const byId = new Map(current.map((n) => [n.id, n]));
  for (const n of incoming) {
    const prev = byId.get(n.id);
    byId.set(n.id, prev && prev.read && !n.read ? { ...n, read: true, read_at: prev.read_at } : n);
  }
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

export function markNoticeReadLocal(current: ChatNotice[], id: number): ChatNotice[] {
  return current.map((n) => (n.id === id && !n.read ? { ...n, read: true, read_at: n.read_at ?? new Date().toISOString() } : n));
}

export const NOTICE_LEVEL_RU: Record<NoticeLevel, string> = {
  info: "сообщение",
  success: "готово",
  warning: "внимание",
  error: "ошибка",
};

export function formatNoticeTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
