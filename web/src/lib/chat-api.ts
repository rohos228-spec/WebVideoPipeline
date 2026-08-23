/**
 * Клиент чата студии: отправить сообщение, читать ленту событий.
 *
 * **Почему `fetch`, а не `EventSource`.** Браузерному `EventSource` нельзя
 * поставить заголовок, а токен биллинга ходит в `Authorization`. Оставался бы
 * либо токен в query-строке — он оседает в логах прокси и живёт неделю, — либо
 * cookie, которая в dev не долетает (фронт на :3000, API на :8765, CORS без
 * credentials). `fetch` со стримом умеет и то и другое: заголовок ставится,
 * ответ читается по мере поступления.
 *
 * Разбор SSE здесь свой и намеренно маленький: формат — это `event:` и `data:`,
 * разделённые пустой строкой. Тянуть ради этого зависимость значит принять её
 * обновления и её же поломки.
 */

import { authHeaders } from "./identity-api";

export type AgentEventType =
  | "tool_call"
  | "tool_result"
  | "tool_error"
  | "message"
  | "limit"
  | "error"
  | "done";

export interface AgentEvent {
  type: AgentEventType;
  payload: Record<string, unknown>;
}

export interface ChatHistoryItem {
  role: "user" | "assistant";
  content: string;
}

// Заголовок авторизации — общий с остальным фронтом (`identity-api`):
// два места, где живёт имя ключа в localStorage, однажды разойдутся.

/**
 * Отправить сообщение и получать события по мере готовности.
 *
 * Вызов инструмента виден в ленте в момент вызова, а не после того, как
 * модель договорит: генерация кадров идёт минутами, и пустой экран всё это
 * время означает для человека «сломалось».
 */
export async function* streamChat(
  message: string,
  history: ChatHistoryItem[] = [],
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ message, history }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // События разделены пустой строкой. Хвост буфера может быть неполным —
    // его оставляем до следующего чтения, иначе половина JSON уйдёт в парсер.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = parseEvent(chunk);
      if (event) yield event;
      split = buffer.indexOf("\n\n");
    }
  }
}

function parseEvent(chunk: string): AgentEvent | null {
  let type = "message";
  const dataLines: string[] = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return {
      type: type as AgentEventType,
      payload: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
    };
  } catch {
    // Битое событие не должно рвать поток: следующее может быть целым.
    return null;
  }
}

export interface ToolInfo {
  name: string;
  description: string;
  args: Record<string, unknown>;
}

/**
 * Выполнить инструмент напрямую — это кнопка, а не разговор.
 *
 * Человек нажал «подтверждаю» под ценой; пересказывать его решение модели
 * значит дать ей шанс понять его иначе. Согласие на списание доезжает до
 * кассы буквой.
 */
export async function callTool(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/chat/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ args }),
  });
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
  return (await res.json()) as Record<string, unknown>;
}

export async function fetchAgentTools(): Promise<ToolInfo[]> {
  const res = await fetch("/api/chat/tools", { headers: authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as ToolInfo[];
}
