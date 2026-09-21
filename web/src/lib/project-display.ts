import type { ProjectStatus } from "@/lib/types";
import { isProjectRunningStatus } from "@/lib/project-running";

export type StatusBadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "destructive"
  | "info"
  | "muted";

/** Имя в сайдбаре — не topic ноды «Тема ролика». */
export function projectDisplayName(
  p: { title?: string | null; topic?: string | null; slug: string },
): string {
  const title = (p.title ?? "").trim();
  if (title) return title;
  const topic = (p.topic ?? "").trim();
  if (topic) return topic;
  return p.slug;
}

/**
 * Цветовой вариант бейджа статуса:
 * - running -> "warning" (янтарный / в работе)
 * - ready / done / assembled / published -> "success" (зелёный / готово)
 * - new -> "muted" (серый)
 * - paused / failed -> "destructive" (красный)
 */
export function projectStatusVariant(
  status: ProjectStatus | string | undefined | null,
): StatusBadgeVariant {
  if (!status || status === "new") return "muted";
  if (status === "paused" || status === "failed") return "destructive";
  if (isProjectRunningStatus(status)) return "warning";
  const s = String(status).toLowerCase();
  if (
    s === "published" ||
    s === "assembled" ||
    s.endsWith("_ready") ||
    s.includes("ready") ||
    s.endsWith("_done") ||
    s === "done"
  ) {
    return "success";
  }
  return "default";
}
