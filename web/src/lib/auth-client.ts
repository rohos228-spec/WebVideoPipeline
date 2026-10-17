/**
 * Типизированный fetch-клиент: /api/auth, /api/auth/oauth, /api/profile, /api/admin.
 * Сессия: localStorage `vp_token`, заголовок `Authorization: Bearer`.
 */

const API_BASE = "/api";
const TOKEN_KEY = "vp_token";

export type UserRole = "owner" | "admin" | "user" | "member";
export type OAuthProvider = "telegram" | "google" | "vk";

export interface OAuthLink {
  provider: OAuthProvider;
  name: string;
  linked_at: number;
}

export interface AuthUser {
  id: number | null;
  email: string | null;
  username: string | null;
  name: string | null;
  role: UserRole;
  plan: string | null;
  effective_plan: string | null;
  team_allowed: boolean;
  email_verified: boolean;
  oauth: OAuthLink[];
}

export interface AuthSession {
  id: number;
  created_at: number;
  expires_at: number;
  user_agent: string;
  current: boolean;
}

export interface AuthStatus {
  auth_required: boolean;
  registration: boolean;
  mail_configured: boolean;
  oauth: Record<OAuthProvider, boolean>;
  user: AuthUser | null;
}

export interface AuthResult {
  ok: boolean;
  token: string;
  access_token: string;
  user: AuthUser | null;
  auth_required?: boolean;
  mail_sent?: boolean;
}

export interface RegisterBody {
  email: string;
  password: string;
  username?: string;
  name?: string;
}

export interface LoginBody {
  email?: string;
  username?: string;
  password: string;
}

export interface OAuthProviderInfo {
  id: OAuthProvider;
  title: string;
  enabled: boolean;
}

export interface OAuthStartResult {
  provider: OAuthProvider;
  state: string;
  url?: string;
  widget?: { bot_username: string };
}

export interface OAuthCallbackResult {
  ok: boolean;
  linked?: boolean;
  oauth?: OAuthLink[];
  token?: string;
  access_token?: string;
  user?: AuthUser;
}

export interface RoleList {
  roles: UserRole[];
  assignable: UserRole[];
  users: Array<{ id: number; email: string; role: UserRole }>;
}

export interface TeamInfo {
  team_allowed: boolean;
  owner_id: number;
  members: AuthUser[];
}

export type OAuthPayload = Record<string, unknown>;

export class AuthError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AuthError";
    this.status = status;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

function storage(): Storage | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage;
}

export function getToken(): string | null {
  const value = storage()?.getItem(TOKEN_KEY);
  return value && value.length > 0 ? value : null;
}

export function setToken(token: string): void {
  storage()?.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  storage()?.removeItem(TOKEN_KEY);
}

function isRole(value: unknown): value is UserRole {
  return value === "owner" || value === "admin" || value === "user" || value === "member";
}

function isProvider(value: unknown): value is OAuthProvider {
  return value === "telegram" || value === "google" || value === "vk";
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asLink(value: unknown): OAuthLink | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (!isProvider(row.provider)) return null;
  return {
    provider: row.provider,
    name: asString(row.name) ?? "",
    linked_at: asNumber(row.linked_at) ?? 0,
  };
}

function asLinks(value: unknown): OAuthLink[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const link = asLink(item);
    return link ? [link] : [];
  });
}

function providerFlags(value: unknown): Record<OAuthProvider, boolean> {
  const row = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return {
    telegram: row.telegram === true,
    google: row.google === true,
    vk: row.vk === true,
  };
}

function asUser(value: unknown): AuthUser {
  const row = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return {
    id: asNumber(row.id),
    email: asString(row.email),
    username: asString(row.username),
    name: asString(row.name),
    role: isRole(row.role) ? row.role : "user",
    plan: asString(row.plan),
    effective_plan: "effective_plan" in row ? asString(row.effective_plan) : null,
    team_allowed: row.team_allowed === true,
    email_verified: row.email_verified === true,
    oauth: asLinks(row.oauth),
  };
}

function asSession(value: unknown): AuthSession | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const id = asNumber(row.id);
  if (id === null) return null;
  return {
    id,
    created_at: asNumber(row.created_at) ?? 0,
    expires_at: asNumber(row.expires_at) ?? 0,
    user_agent: asString(row.user_agent) ?? "",
    current: row.current === true,
  };
}

function asRoles(value: unknown): UserRole[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRole);
}

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail.trim() ? detail : null;
  if (Array.isArray(detail)) {
    const parts = detail.flatMap((item) => {
      if (typeof item === "string" && item.trim()) return [item];
      if (!item || typeof item !== "object") return [];
      const row = item as { msg?: unknown; loc?: unknown };
      if (typeof row.msg !== "string" || !row.msg.trim()) return [];
      const loc = Array.isArray(row.loc)
        ? row.loc.filter((part) => part !== "body").map(String).join(".")
        : "";
      return [loc ? `${loc}: ${row.msg}` : row.msg];
    });
    return parts.length > 0 ? parts.join("; ") : null;
  }
  if (detail && typeof detail === "object") {
    const row = detail as Record<string, unknown>;
    if (typeof row.msg === "string" && row.msg.trim()) return row.msg;
    if (typeof row.message === "string" && row.message.trim()) return row.message;
  }
  return null;
}

async function errorMessage(res: Response): Promise<string> {
  const text = await res.text();
  if (!text.trim()) return res.statusText || `HTTP ${res.status}`;
  try {
    const data: unknown = JSON.parse(text);
    if (data && typeof data === "object" && "detail" in data) {
      const message = detailText((data as { detail: unknown }).detail);
      if (message) return message;
    }
  } catch {
    return text;
  }
  return text;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) params.set(key, String(value));
  }
  const query = params.toString();
  const url = `${API_BASE}${path}${query ? `?${query}` : ""}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    const message = err instanceof Error && err.message ? err.message : "Сеть недоступна";
    throw new AuthError(0, message);
  }

  if (!res.ok) throw new AuthError(res.status, await errorMessage(res));
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text.trim()) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new AuthError(res.status, "Некорректный ответ сервера");
  }
}

function rememberToken(body: { token?: unknown; access_token?: unknown }): string {
  const token =
    typeof body.token === "string" && body.token
      ? body.token
      : typeof body.access_token === "string" && body.access_token
        ? body.access_token
        : "";
  if (token) setToken(token);
  return token;
}

function asAuthResult(raw: Record<string, unknown>): AuthResult {
  const token = rememberToken(raw);
  const result: AuthResult = {
    ok: raw.ok === true,
    token,
    access_token: asString(raw.access_token) ?? token,
    user: raw.user && typeof raw.user === "object" ? asUser(raw.user) : null,
  };
  if (typeof raw.auth_required === "boolean") result.auth_required = raw.auth_required;
  if (typeof raw.mail_sent === "boolean") result.mail_sent = raw.mail_sent;
  return result;
}

export function authStatus(): Promise<AuthStatus> {
  return request<Record<string, unknown>>("/auth/status").then((raw) => ({
    auth_required: raw.auth_required === true,
    registration: raw.registration === true,
    mail_configured: raw.mail_configured === true,
    oauth: providerFlags(raw.oauth),
    user: raw.user && typeof raw.user === "object" ? asUser(raw.user) : null,
  }));
}

export function register(body: RegisterBody): Promise<AuthResult> {
  return request<Record<string, unknown>>("/auth/register", { method: "POST", body }).then(asAuthResult);
}

export function login(body: LoginBody): Promise<AuthResult> {
  return request<Record<string, unknown>>("/auth/login", { method: "POST", body }).then(asAuthResult);
}

export async function logout(): Promise<{ ok: boolean }> {
  try {
    return await request<{ ok: boolean }>("/auth/logout", { method: "POST" });
  } finally {
    clearToken();
  }
}

export function me(): Promise<AuthUser> {
  return request<unknown>("/auth/me").then(asUser);
}

export function verifyEmail(token: string): Promise<{ ok: boolean }> {
  return request("/auth/verify-email", { method: "POST", body: { token } });
}

export function verifyCode(code: string): Promise<{ ok: boolean }> {
  return request("/auth/verify-email", { method: "POST", body: { code } });
}

export function resendVerification(): Promise<{ ok: boolean; already_verified?: boolean; mail_sent?: boolean }> {
  return request("/auth/verify-email/resend", { method: "POST" });
}

export function forgotPassword(email: string): Promise<{ ok: boolean }> {
  return request("/auth/forgot-password", { method: "POST", body: { email } });
}

export function checkResetToken(token: string): Promise<{ valid: boolean }> {
  return request("/auth/reset/check", { query: { token } });
}

export function resetPassword(token: string, password: string): Promise<{ ok: boolean }> {
  return request("/auth/reset-password", { method: "POST", body: { token, password } });
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<{ ok: boolean; token: string }> {
  const raw = await request<{ ok: boolean; token?: string }>("/auth/change-password", {
    method: "POST",
    body: { old_password: oldPassword, new_password: newPassword },
  });
  const token = rememberToken(raw);
  return { ok: raw.ok === true, token };
}

export function listSessions(): Promise<AuthSession[]> {
  return request<{ sessions?: unknown }>("/auth/sessions").then((raw) => {
    if (!Array.isArray(raw.sessions)) return [];
    return raw.sessions.flatMap((item) => {
      const session = asSession(item);
      return session ? [session] : [];
    });
  });
}

export function revokeSession(id: number): Promise<{ ok: boolean }> {
  return request(`/auth/sessions/${encodeURIComponent(String(id))}`, { method: "DELETE" });
}

export async function deleteAccount(password: string): Promise<{ ok: boolean }> {
  const raw = await request<{ ok: boolean }>("/auth/delete", { method: "POST", body: { password } });
  clearToken();
  return raw;
}

export function oauthProviders(): Promise<OAuthProviderInfo[]> {
  return request<{ providers?: unknown }>("/auth/oauth/providers").then((raw) => {
    if (!Array.isArray(raw.providers)) return [];
    return raw.providers.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const row = item as Record<string, unknown>;
      if (!isProvider(row.id)) return [];
      return [{ id: row.id, title: asString(row.title) ?? row.id, enabled: row.enabled === true }];
    });
  });
}

export function oauthStart(provider: OAuthProvider): Promise<OAuthStartResult> {
  return request<Record<string, unknown>>(`/auth/oauth/${provider}/start`).then((raw) => {
    const widget = raw.widget && typeof raw.widget === "object" ? (raw.widget as Record<string, unknown>) : null;
    const result: OAuthStartResult = {
      provider: isProvider(raw.provider) ? raw.provider : provider,
      state: asString(raw.state) ?? "",
    };
    const url = asString(raw.url);
    if (url) result.url = url;
    const bot = widget ? asString(widget.bot_username) : null;
    if (bot) result.widget = { bot_username: bot };
    return result;
  });
}

export function oauthLink(provider: OAuthProvider, payload: OAuthPayload): Promise<{ ok: boolean; oauth: OAuthLink[] }> {
  return request<{ ok?: unknown; oauth?: unknown }>(`/auth/oauth/${provider}/link`, {
    method: "POST",
    body: payload,
  }).then((raw) => ({ ok: raw.ok === true, oauth: asLinks(raw.oauth) }));
}

export function oauthUnlink(provider: OAuthProvider): Promise<{ ok: boolean; oauth: OAuthLink[] }> {
  return request<{ ok?: unknown; oauth?: unknown }>(`/auth/oauth/${provider}`, { method: "DELETE" }).then((raw) => ({
    ok: raw.ok === true,
    oauth: asLinks(raw.oauth),
  }));
}

export function oauthCallback(provider: OAuthProvider, payload: OAuthPayload): Promise<OAuthCallbackResult> {
  return request<Record<string, unknown>>(`/auth/oauth/${provider}/callback`, {
    method: "POST",
    body: payload,
  }).then((raw) => {
    const token = rememberToken(raw);
    const result: OAuthCallbackResult = { ok: raw.ok === true };
    if (raw.linked === true) result.linked = true;
    if (Array.isArray(raw.oauth)) result.oauth = asLinks(raw.oauth);
    if (token) {
      result.token = token;
      result.access_token = asString(raw.access_token) ?? token;
    }
    if (raw.user && typeof raw.user === "object") result.user = asUser(raw.user);
    return result;
  });
}

export function profileMe(): Promise<AuthUser & { permissions: string[] }> {
  return request<Record<string, unknown>>("/profile/me").then((raw) => ({
    ...asUser(raw),
    permissions: Array.isArray(raw.permissions) ? raw.permissions.filter((item): item is string => typeof item === "string") : [],
  }));
}

export function listRoles(): Promise<RoleList> {
  return request<Record<string, unknown>>("/profile/roles").then((raw) => ({
    roles: asRoles(raw.roles),
    assignable: asRoles(raw.assignable),
    users: Array.isArray(raw.users)
      ? raw.users.flatMap((item) => {
          if (!item || typeof item !== "object") return [];
          const row = item as Record<string, unknown>;
          const id = asNumber(row.id);
          const email = asString(row.email);
          if (id === null || email === null || !isRole(row.role)) return [];
          return [{ id, email, role: row.role }];
        })
      : [],
  }));
}

export function setRole(userId: number, role: UserRole): Promise<{ ok: boolean; user: AuthUser }> {
  return request<{ ok?: unknown; user?: unknown }>(`/profile/roles/${encodeURIComponent(String(userId))}`, {
    method: "PATCH",
    body: { role },
  }).then((raw) => ({ ok: raw.ok === true, user: asUser(raw.user) }));
}

export function getTeam(): Promise<TeamInfo> {
  return request<Record<string, unknown>>("/profile/team").then((raw) => ({
    team_allowed: raw.team_allowed === true,
    owner_id: asNumber(raw.owner_id) ?? 0,
    members: Array.isArray(raw.members) ? raw.members.map(asUser) : [],
  }));
}

export function addTeamMember(email: string): Promise<{ ok: boolean; members: AuthUser[] }> {
  return request<Record<string, unknown>>("/profile/team", { method: "POST", body: { email } }).then((raw) => ({
    ok: raw.ok === true,
    members: Array.isArray(raw.members) ? raw.members.map(asUser) : [],
  }));
}

export function removeTeamMember(id: number): Promise<{ ok: boolean }> {
  return request(`/profile/team/${encodeURIComponent(String(id))}`, { method: "DELETE" });
}

export function adminUsers(): Promise<{ users: AuthUser[] }> {
  return request<{ users?: unknown }>("/admin/users").then((raw) => ({
    users: Array.isArray(raw.users) ? raw.users.map(asUser) : [],
  }));
}
