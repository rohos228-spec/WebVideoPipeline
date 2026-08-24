/** Тонкий клиент REST-а студии. Одна функция запроса, дальше — вызовы. */

import type {
  Asset,
  AuthStatus,
  Balance,
  Frame,
  GenerationOptions,
  LoginResult,
  MediaFrame,
  Me,
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

/**
 * Ключ сессии в хранилище браузера.
 *
 * Тот же токен сервер кладёт в cookie `HttpOnly` — она нужна `EventSource` и
 * WebSocket, которым заголовок поставить нечем. Здесь копия для заголовка
 * `Authorization`: cookie из скрипта не читается, а без неё каждый запрос
 * зависел бы от того, отправил ли браузер cookie на этот путь.
 */
const TOKEN_KEY = "vp.token";

function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Приватное окно, запрет на хранилище — вход просто не переживёт
    // перезагрузку. Ронять страницу из-за этого незачем.
    return null;
  }
}

function saveToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* см. readToken */
  }
}

function forgetToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* см. readToken */
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readToken();
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  // Токен протух или отозван (сменили пароль, отключили учётку). Держать его
  // дальше незачем: каждый следующий запрос получит тот же 401, а человек
  // будет видеть пустой интерфейс вместо формы входа.
  if (res.status === 401 && token && !path.startsWith("/auth/")) {
    forgetToken();
    if (typeof window !== "undefined") window.location.reload();
  }
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

  authStatus: () => req<AuthStatus>("/auth/status"),
  login: (email: string, password: string) => post<LoginResult>("/auth/login", { email, password }),
  logout: async () => {
    await post<unknown>("/auth/logout");
    forgetToken();
  },
  me: () => req<Me>("/me"),

  readToken,
  saveToken,
  forgetToken,
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
