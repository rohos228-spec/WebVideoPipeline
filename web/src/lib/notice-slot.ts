import type { ChatNotice } from "./chat-notices";
import type { ChatMessage } from "./orchestratorSession";

/**
 * Индекс реплики, перед которой встаёт карточка уведомления: первая реплика позже карточки,
 * иначе конец ленты. null — сравнить не с чем (у карточки или у всех реплик нет времени).
 */
export function noticeSlotByTime(notice: ChatNotice, messages: readonly ChatMessage[]): number | null {
  const t = notice.created_at ? Date.parse(notice.created_at) : Number.NaN;
  if (Number.isNaN(t)) return null;
  let timed = false;
  for (let i = 0; i < messages.length; i += 1) {
    const raw = messages[i].at;
    const at = raw ? Date.parse(raw) : Number.NaN;
    if (Number.isNaN(at)) continue;
    timed = true;
    if (at > t) return i;
  }
  return timed ? messages.length : null;
}
