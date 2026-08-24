/** Тонкий клиент REST-а студии. Одна функция запроса, дальше — вызовы. */

import type {
  Asset,
  Balance,
  Frame,
  GenerationOptions,
  MediaFrame,
  Project,
  ProjectSummary,
  StageId,
  StagesResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "content-type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* тело не json — оставляем код */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const patch = <T>(path: string, body: unknown) =>
  req<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const api = {
  projects: () => req<ProjectSummary[]>("/projects"),
  project: (id: number) => req<Project>(`/projects/${id}`),
  createProject: (title: string) => post<Project>("/projects", { title, hero_mode: "auto" }),
  deleteProject: (id: number) => req<void>(`/projects/${id}`, { method: "DELETE" }),
  patchProject: (id: number, body: Partial<Project>) => patch<Project>(`/projects/${id}`, body),

  stages: (id: number) => req<StagesResponse>(`/projects/${id}/stages`),
  runStage: (id: number, stage: StageId) => post<unknown>(`/projects/${id}/stages/${stage}/run`),
  stopStage: (id: number) => post<unknown>(`/projects/${id}/stages/stop`),

  frames: (id: number) => req<Frame[]>(`/projects/${id}/frames`),
  patchFrame: (projectId: number, frameId: number, body: Partial<Frame>) =>
    patch<Frame>(`/projects/${projectId}/frames/${frameId}`, body),

  media: (id: number, kind: "images" | "videos") =>
    req<MediaFrame[]>(`/projects/${id}/media-review?kind=${kind}`),
  assets: (id: number) => req<Asset[]>(`/projects/${id}/assets`),

  balance: () => req<Balance>("/billing/balance"),
  options: () => req<GenerationOptions>("/generation-options/wizard"),
};

/** Живые события проекта. Возвращает функцию отписки. */
export function subscribeProject(projectId: number, onEvent: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(`${proto}//${window.location.host}/ws/projects.${projectId}`);
    ws.onmessage = () => onEvent();
    ws.onclose = () => {
      if (!closed) retry = setTimeout(connect, 2_000);
    };
    ws.onerror = () => ws?.close();
  };
  connect();

  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    ws?.close();
  };
}
