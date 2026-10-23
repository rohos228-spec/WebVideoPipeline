/**
 * Значок «Задача: …» над чатом.
 *
 * Следит за самым новым ходом, а не за последней задачей: пока на новую
 * реплику нет ответа — «думаю…», ответ пришёл без задачи — статус прошлой
 * задачи больше не показывается. Отказ человека и непонятая просьба — серые,
 * это не ошибка.
 */

import type { AckView, ChatMessage, TaskView } from "./orchestratorSession";

export type TaskBadge = { kind: "none" } | { kind: "thinking" } | { kind: "task"; task: TaskView };

const FINISHED = new Set<string>(["done", "failed", "cancelled"]);

// Здесь, а не в orchestratorSession: модуль без зависимостей проверяется node --test.
export function currentTask(tasks: TaskView[]): TaskView | null {
  let best: TaskView | null = null;
  for (const task of tasks) {
    if (!best || task.lastSeq >= best.lastSeq) best = task;
  }
  return best;
}

export function ackAnswered(messages: ChatMessage[], ack: AckView): boolean {
  return messages.some((item) => item.role === "assistant" && item.seq != null && item.seq > ack.seq);
}

export function pickTaskBadge(tasks: TaskView[], ack: AckView | null, messages: ChatMessage[]): TaskBadge {
  const task = currentTask(tasks);
  if (!ack || (task && task.lastSeq > ack.seq)) return task ? { kind: "task", task } : { kind: "none" };
  // Реплика новее задачи. Идущая задача остаётся главной: вопрос по ходу её не отменяет.
  if (task && !FINISHED.has(task.status)) return { kind: "task", task };
  return ackAnswered(messages, ack) ? { kind: "none" } : { kind: "thinking" };
}

/** Серый исход вместо красного: «нужно уточнение» (просьба не понята), «ответил» (чат ответил без шагов) или «отменена». */
export function neutralEnd(task: TaskView): "unclear" | "answered" | "cancelled" | null {
  if (task.status === "cancelled") {
    if (task.reason === "answered") return "answered";
    return task.reason === "empty_plan" ? "unclear" : "cancelled";
  }
  if (task.status === "failed" && (task.error === "empty_plan" || task.reason === "empty_plan")) return "unclear";
  return null;
}
