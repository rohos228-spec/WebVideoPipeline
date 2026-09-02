/**
 * Личность и деньги на стороне фронта.
 *
 * Студия работает в двух режимах, и различать их обязан интерфейс, а не
 * человек. В режиме владельца личности нет вовсе: кредитов не существует,
 * владелец платит провайдерам напрямую, и показывать ему баланс значит врать.
 * В режиме SaaS всё наоборот — без токена не работает ничего, а баланс должен
 * быть виден всегда, потому что каждый шаг его тратит.
 *
 * Отсюда три состояния, а не два: «вошёл», «не вошёл» и «личностей здесь
 * нет». Свести их к «вошёл / не вошёл» значит показать владельцу форму входа,
 * которой у него нет, либо клиенту — интерфейс без баланса.
 */

// Тот же ключ, что у stage-api: одна сессия на оба интерфейса.
const TOKEN_KEY = "vp.token";

export interface AuthStatus {
  auth_required: boolean;
  /** true — личность приходит из биллинга; false — режим владельца. */
  sso: boolean;
  brand: string;
}

export interface Me {
  sso_enabled: boolean;
  tenant_id: string | null;
  email: string;
  brand: string;
  balance_micro: number;
  balance_credits: string;
  provisioned_now: boolean;
}

export interface FreeTier {
  active: boolean;
  granted_usd: number;
  cap_usd: number;
  projects: number;
  max_projects: number;
  reason: string;
}

export interface Balance {
  tenant_id: string | null;
  balance_micro: number;
  balance_credits: string;
  /** Деньги под идущими шагами: они уже вычтены из остатка. */
  held_micro: number;
  free_tier: FreeTier;
  entries: Array<{
    kind: string;
    delta_micro: number;
    delta_credits: string;
    cost_usd: number | null;
    memo: string;
    created_at: string | null;
  }>;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Единственная ручка, доступная до токена: куда вообще входить. */
export const fetchAuthStatus = () => get<AuthStatus>("/api/auth/status");

export const fetchMe = () => get<Me>("/api/me");

export const fetchBalance = () => get<Balance>("/api/billing/balance");

/**
 * Токен из адресной строки после возврата из биллинга.
 *
 * Забирается один раз и сразу вычищается из URL: адрес с токеном человек
 * скопирует в чат, вставит в тикет и оставит в истории браузера, а живёт
 * токен неделю.
 */
export function captureTokenFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return null;
  setToken(token);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.toString());
  return token;
}
