/**
 * API контракт админ-панели VIA (/api/admin/*).
 *
 * Один файл-источник правды для типов и запросов: и страница /admin, и все
 * вкладки используют только эти функции. Бэкенд — app/web/routers/admin.py.
 */

const TOKEN_KEY = "vp-auth-token";

function authToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Ошибка админского API с HTTP-статусом: по ней UI отличает «нет входа» от прочих сбоев. */
export class AdminHttpError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AdminHttpError";
    this.status = status;
  }
}

/** 401 — сессии нет/истекла: админке нужно увести на логин. */
export const isUnauthorized = (err: unknown): boolean =>
  err instanceof AdminHttpError && err.status === 401;

/** 404 — ручки нет или нет прав (участнику себестоимость отдаётся как 404). */
export const isNotFound = (err: unknown): boolean =>
  err instanceof AdminHttpError && err.status === 404;

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = authToken();
  const headers: Record<string, string> = {
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const r = await fetch(`/api/admin${path}`, {
    ...init,
    headers,
    // Сессия сайте живёт в HttpOnly cookie — шлём её всегда (same-origin).
    credentials: "same-origin",
  });
  let data: unknown = null;
  try {
    data = await r.json();
  } catch {
    /* пустой ответ */
  }
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Проверьте поля формы"
          : `HTTP ${r.status}`;
    // /access — публичная проба прав: её 401 разбирает сама admin-shell
    // (показывает «Войти»), автопереход отсюда дал бы лишний цикл.
    if (r.status === 401 && path !== "/access" && typeof window !== "undefined") {
      window.location.href = "/login?next=%2Fadmin";
    }
    throw new AdminHttpError(msg, r.status);
  }
  return data as T;
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/* ──────────────────────────── типы ──────────────────────────── */

export type AdminUserBrief = {
  id: number;
  email: string;
  username: string;
  name: string;
  role: string;
  plan: string | null;
  status: string;
  email_verified: boolean;
  created_at: number;
  last_login: number | null;
};

export type AdminAccess = {
  is_admin: boolean;
  auth_required: boolean;
  bootstrap: boolean;
  user: AdminUserBrief | null;
  reason?: string;
};

export type AdminSeriesPoint = {
  date: string;
  generations: number;
  requests: number;
  cost_usd: number;
  credits_spent: number;
};

export type AdminOverview = {
  generated_at: string;
  period_days: number;
  users: { total: number; new_period: number; active_7d: number; admins: number; blocked: number };
  projects: { total: number; active: number; done: number; failed: number };
  generations: { total: number; images: number; videos: number; audio: number; finals: number; period: number };
  requests: {
    total: number;
    cost_usd: number;
    errors: number;
    avg_duration: number;
    period_calls: number;
    period_cost_usd: number;
    period_errors: number;
  };
  credits: { issued: number; spent: number; balance_total: number; period_issued: number; period_spent: number };
  subscriptions: {
    active: number;
    mrr_rub: number;
    mrr_monthly_rub: number;
    expiring_7d: number;
    total_issued: number;
  };
  series: AdminSeriesPoint[];
  top_users: {
    user_id: number;
    username: string;
    name: string;
    generations: number;
    requests: number;
    cost_usd: number;
    credits_spent: number;
    balance_credits: number;
  }[];
  models: { model: string; provider: string; calls: number; cost_usd: number }[];
  sources: { users_db: boolean; tracker_db: boolean; state_db: boolean; cloud: boolean };
  unattributed: { projects: number; generations: number; requests: number; cost_usd: number };
};

export type AdminSubscription = {
  id: number;
  user_id: number;
  plan_id: string;
  plan_name: string;
  status: string;
  started_at: string;
  expires_at: string | null;
  auto_renew: boolean;
  credits_granted: number;
  note: string;
  created_by: string;
};

export type AdminLedgerEntry = {
  id: number;
  ts: string;
  kind: string;
  amount: number;
  reason: string;
  admin: string;
  balance_after: number;
};

export type AdminUserRow = AdminUserBrief & {
  balance_credits: number;
  spent_credits: number;
  generations: number;
  projects: number;
  requests: number;
  cost_usd: number;
  tracker_identity: string;
  device_name: string;
  plan_active: boolean;
  plan_expires_at: string | null;
  subscription: AdminSubscription | null;
  limits: Record<string, number | string>;
  note: string;
  last_activity: string | null;
};

export type AdminUserProject = {
  id: number;
  title: string;
  slug: string;
  status: string;
  generations: number;
  created_at: string;
};

export type AdminAuditEntry = {
  id: number;
  ts: string;
  admin: string;
  action: string;
  target: string;
  payload: Record<string, unknown>;
};

export type AdminUserDetail = AdminUserRow & {
  ledger: AdminLedgerEntry[];
  subscriptions_history: AdminSubscription[];
  projects_list: AdminUserProject[];
  audit: AdminAuditEntry[];
};

/** Сессия аккаунта в users.db (для блока «Сессии» в админке). */
export type AdminSession = {
  created_at: string;
  expires_at: string;
  user_agent: string;
  active: boolean;
};

export type AdminSessionList = {
  items: AdminSession[];
  total: number;
};

export type AdminCreateUserBody = {
  email: string;
  username: string;
  name?: string;
  password: string;
  role?: string;
  plan?: string | null;
};

export type AdminDeleteUserResult = {
  ok: boolean;
  deleted: boolean;
  id: number;
  sessions_revoked: number;
};

export type AdminGeneration = {
  id: number;
  project_id: number;
  project_title: string;
  project_slug: string;
  owner_id: number | null;
  owner_name: string;
  kind: string;
  frame_number: number | null;
  uuid: string;
  file_url: string;
  created_at: string;
  size_bytes: number | null;
  approved: boolean;
};

export type AdminGenerationPage = { items: AdminGeneration[]; total: number; limit: number; offset: number };

export type AdminRequest = {
  id: number;
  ts: string;
  user_name: string;
  device_name: string;
  provider: string;
  model: string;
  call_type: string;
  total_tokens: number;
  duration_sec: number;
  cost_usd: number;
  status_code: number;
  error: string;
  project_source: string;
  owner_user_id: number | null;
  owner_name: string;
};

export type AdminRequestsSummary = {
  total: number;
  cost_usd: number;
  errors: number;
  avg_duration: number;
  by_provider: { provider: string; calls: number; cost_usd: number }[];
  by_model: { model: string; provider: string; calls: number; cost_usd: number }[];
  by_user: { user_name: string; calls: number; cost_usd: number; owner_user_id: number | null; owner_name: string }[];
  daily: { date: string; calls: number; cost_usd: number }[];
};

export type AdminRequestPage = {
  items: AdminRequest[];
  total: number;
  limit: number;
  offset: number;
  summary: AdminRequestsSummary;
};

export type AdminPlan = {
  id: string;
  name: string;
  price_rub: number;
  group: string;
  note: string;
  credits: number;
  limits: Record<string, number | string>;
  perks: string[];
  is_active: boolean;
  sort: number;
  hot: boolean;
};

export type AdminSettings = {
  currency: string;
  usd_rub: number;
  credits_per_usd: number;
  registration_open: boolean;
  default_plan: string;
  spend_markup: number;
  low_balance_threshold: number;
  tracker_db_path: string;
  updated_at: string;
  updated_by: string;
};

/* ─────────────── расходы: /api/admin/costs/* (контракт v1) ─────────────── */

/** Общие параметры периода: ISO-даты, по умолчанию бэкенд берёт последние 30 дней. */
export type AdminCostPeriod = {
  from?: string;
  to?: string;
  kind?: string;
  provider?: string;
  model?: string;
  project_id?: string | number;
};

export type AdminCostTotals = {
  actual_usd: number;
  credits_charged: number;
  credits_micro: number;
  calls: number;
  unpriced_calls: number;
  /** Доля вызовов, у которых есть цена, в процентах (0–100). */
  price_coverage_pct: number;
};

export type AdminCostByKind = {
  kind: string;
  calls: number;
  units: number;
  actual_usd: number;
  credits_charged: number;
  unpriced_calls: number;
};

export type AdminCostOverview = {
  from: string;
  to: string;
  totals: AdminCostTotals;
  by_kind: AdminCostByKind[];
};

export type AdminCostDay = {
  day: string;
  kind: string;
  calls: number;
  actual_usd: number;
  credits_charged: number;
  unpriced_calls: number;
};

export type AdminCostModel = {
  kind: string;
  provider: string;
  model: string;
  calls: number;
  units: number;
  unit: string;
  actual_usd: number;
  credits_charged: number;
  avg_usd: number;
  unpriced_calls: number;
  has_price: boolean;
};

export type AdminCostProject = {
  project_id: string;
  project_title: string;
  actual_usd: number;
  credits_charged: number;
  calls: number;
  unpriced_calls: number;
};

export type AdminCostPrice = {
  kind: string;
  provider: string;
  model: string;
  unit: string;
  usd_per_unit: number | null;
  usd_per_call: number | null;
  has_price: boolean;
  calls_30d: number;
  unpriced_30d: number;
};

export type AdminCostRecount = {
  scanned: number;
  priced: number;
  still_unpriced: number;
  added_usd: number;
  dry_run: boolean;
};

export type AdminCostReconcileRow = {
  ref_table: string;
  charged_usd: number;
  entries: number;
  actual_usd: number;
};

export type AdminCostReconcile = {
  actual_usd: number;
  charged_usd: number;
  delta_usd: number;
  ratio: number;
  by_ref_table: AdminCostReconcileRow[];
};

/* ──────────────────────────── запросы ──────────────────────────── */

export const adminApi = {
  access: () => adminFetch<AdminAccess>("/access"),

  overview: (days = 30) => adminFetch<AdminOverview>(`/overview${qs({ days })}`),

  users: (params: { q?: string; status?: string; sort?: string; limit?: number; offset?: number } = {}) =>
    adminFetch<{ items: AdminUserRow[]; total: number; limit: number; offset: number }>(
      `/users${qs(params)}`,
    ),

  user: (id: number) => adminFetch<AdminUserDetail>(`/users/${id}`),

  /** Создать аккаунт из админки. 409 — email/username заняты, 400 — валидация. */
  createUser: (body: AdminCreateUserBody) =>
    adminFetch<AdminUserRow>("/users", { method: "POST", body: JSON.stringify(body) }),

  /** Удалить аккаунт (себя и последнего админа бэкенд не даёт — 400). */
  deleteUser: (id: number) =>
    adminFetch<AdminDeleteUserResult>(`/users/${id}`, { method: "DELETE" }),

  userSessions: (id: number) => adminFetch<AdminSessionList>(`/users/${id}/sessions`),

  revokeSessions: (id: number) =>
    adminFetch<{ ok: boolean; revoked: number }>(`/users/${id}/sessions/revoke`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  userLedger: (id: number, limit = 100) =>
    adminFetch<{ items: AdminLedgerEntry[]; total: number }>(`/users/${id}/ledger${qs({ limit })}`),

  accrue: (id: number, body: { amount: number; reason: string }) =>
    adminFetch<{ ok: boolean; entry: AdminLedgerEntry; balance_credits: number }>(`/users/${id}/credits`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  issueSubscription: (
    id: number,
    body: { plan_id: string; months?: number; credits?: number; auto_renew?: boolean; note?: string },
  ) =>
    adminFetch<{ ok: boolean; subscription: AdminSubscription; balance_credits: number }>(
      `/users/${id}/subscription`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  cancelSubscription: (id: number) =>
    adminFetch<{ ok: boolean; subscription: AdminSubscription }>(`/users/${id}/subscription/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  updateUser: (
    id: number,
    body: Partial<{
      plan: string | null;
      role: string;
      status: string;
      limits: Record<string, number | string>;
      tracker_identity: string;
      device_name: string;
      note: string;
    }>,
  ) => adminFetch<AdminUserRow>(`/users/${id}`, { method: "PUT", body: JSON.stringify(body) }),

  generations: (
    params: {
      user_id?: number | null;
      kind?: string;
      project_id?: number | null;
      days?: number;
      q?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => adminFetch<AdminGenerationPage>(`/generations${qs(params)}`),

  requests: (
    params: {
      user?: string;
      owner_user_id?: number | null;
      account_id?: number | null;
      provider?: string;
      model?: string;
      status?: string;
      days?: number;
      q?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => adminFetch<AdminRequestPage>(`/requests${qs(params)}`),

  requestsSummary: (
    params: {
      days?: number;
      user?: string;
      owner_user_id?: number | null;
      account_id?: number | null;
      provider?: string;
      model?: string;
      status?: string;
      q?: string;
    } = {},
  ) => adminFetch<AdminRequestsSummary>(`/requests/summary${qs(params)}`),

  /* ── расходы (себестоимость): только админ, участнику 404 ── */

  costsOverview: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostOverview>(`/costs/overview${qs(params)}`),

  costsByDay: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostDay[]>(`/costs/by-day${qs(params)}`),

  costsByModel: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostModel[]>(`/costs/by-model${qs(params)}`),

  costsByProject: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostProject[]>(`/costs/by-project${qs(params)}`),

  costsPrices: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostPrice[]>(`/costs/prices${qs(params)}`),

  /** Сверка факта и списаний кредитов за период. */
  costsReconcile: (params: AdminCostPeriod = {}) =>
    adminFetch<AdminCostReconcile>(`/costs/reconcile${qs(params)}`),

  /** Пересчёт себестоимости из прайсов; dry_run=true — только показать, что изменится. */
  costsRecount: (body: { from?: string; to?: string; dry_run: boolean }) =>
    adminFetch<AdminCostRecount>("/costs/recount", { method: "POST", body: JSON.stringify(body) }),

  plans: () => adminFetch<AdminPlan[]>("/plans"),

  savePlan: (plan: Partial<AdminPlan> & { id: string }) =>
    adminFetch<AdminPlan>(`/plans/${encodeURIComponent(plan.id)}`, {
      method: "PUT",
      body: JSON.stringify(plan),
    }),

  deletePlan: (id: string) =>
    adminFetch<{ ok: boolean; deactivated?: boolean }>(`/plans/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  settings: () => adminFetch<AdminSettings>("/settings"),

  saveSettings: (patch: Partial<AdminSettings>) =>
    adminFetch<AdminSettings>("/settings", { method: "PUT", body: JSON.stringify(patch) }),

  audit: (limit = 200, q = "") =>
    adminFetch<{ items: AdminAuditEntry[]; total: number }>(`/audit${qs({ limit, q })}`),

  /** Сбросить кэши агрегатов (после массовых правок). */
  refresh: () => adminFetch<{ ok: boolean }>("/refresh", { method: "POST", body: JSON.stringify({}) }),
};

export const GENERATION_KINDS: { id: string; label: string }[] = [
  { id: "", label: "Все" },
  { id: "scene_image", label: "Кадры" },
  { id: "scene_video", label: "Видео" },
  { id: "hero_reference", label: "Персонажи" },
  { id: "item_reference", label: "Предметы" },
  { id: "audio", label: "Озвучка" },
  { id: "music", label: "Музыка" },
  { id: "final_video", label: "Финалы" },
];

export const LEDGER_KIND_RU: Record<string, string> = {
  accrual: "Начисление",
  debit: "Списание",
  subscription: "Подписка",
  refund: "Возврат",
  adjustment: "Корректировка",
  reset: "Обнуление",
};
