/**
 * Клиент чата студии: отправить сообщение, читать ленту событий.
 *
 * **Почему `fetch`, а не `EventSource`.** Браузерному `EventSource` нельзя
 * поставить заголовок, а токен сессии ходит в `Authorization`. `fetch` со
 * стримом умеет и заголовок, и чтение по мере поступления.
 *
 * Разбор SSE здесь свой и намеренно маленький: формат — это `event:` и
 * `data:`, разделённые пустой строкой.
 */

import { api } from "./stage-api";

export type AgentEventType =
  | "tool_call"
  | "tool_result"
  | "tool_error"
  | "message"
  | "limit"
  | "history"
  | "error"
  | "done";

export interface AgentEvent {
  type: AgentEventType;
  payload: Record<string, unknown>;
}

/**
 * Реплика истории — то, что сервер отдал в событии `history`. Текст или
 * блоки (text / tool_use / tool_result) в формате модели. Клиент их не
 * разбирает и не собирает сам: хранит и возвращает как есть.
 */
export interface ChatHistoryItem {
  role: "user" | "assistant";
  content: string | Record<string, unknown>[];
}

function headers(): Record<string, string> {
  const token = api.readToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { authorization: `Bearer ${token}` } : {}),
  };
}

/**
 * Отправить сообщение и получать события по мере готовности.
 *
 * Вызов инструмента виден в ленте в момент вызова, а не после того, как
 * модель договорит: шаг идёт минутами, и пустой экран всё это время
 * означает для человека «сломалось».
 */
export async function* streamChat(
  message: string,
  history: ChatHistoryItem[] = [],
  projectId: number | null = null,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ message, history, project_id: projectId }),
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
    // sse-starlette разделяет строки `\r\n`, а не `\n`: без нормализации
    // граница события `\n\n` не находится никогда, и лента остаётся пустой,
    // хотя сервер всё отдал. Нормализуем буфер целиком, а не чанк: `\r` в
    // конце одного чанка и `\n` в начале следующего должны склеиться.
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
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
    return null;
  }
}

/**
 * Выполнить инструмент напрямую — это кнопка, а не разговор.
 *
 * Человек нажал «подтверждаю»; пересказывать его решение модели значит дать
 * ей шанс понять его иначе. Согласие доезжает до кассы буквой.
 */
export async function callTool(
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/chat/tools/${name}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ args }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* тело не json */
    }
    throw new Error(detail);
  }
  return (await res.json()) as Record<string, unknown>;
}
