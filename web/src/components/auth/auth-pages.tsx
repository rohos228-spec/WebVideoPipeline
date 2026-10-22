"use client";

import { useEffect, useMemo, useState } from "react";
import { safeAuthNext } from "@/lib/safe-next";
import { ArrowLeft, Check, CircleAlert, Eye, EyeOff, Loader2, Lock, Mail, MailCheck, User, ImageIcon } from "lucide-react";

export const AUTH_TOKEN_KEY = "vp-auth-token";
const A = "#2dd4bf";
const FONT = { fontFamily: "Rubik, sans-serif" } as const;

export type AuthUser = { id: number; email: string; username: string; name: string; role: string; email_verified: boolean; created_at: number; last_login?: number | null };

export function getToken(): string | null {
  try { return localStorage.getItem(AUTH_TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  // Cookie vp_session ставит сервер (HttpOnly, SameSite=Lax) — из JS её не трогаем,
  // иначе можно случайно перетереть/удалить серверную сессию. localStorage остаётся
  // только fallback'ом для Bearer-запросов, если сервер вернул токен.
  try {
    if (t) localStorage.setItem(AUTH_TOKEN_KEY, t);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {}
}
export async function authApi<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const t = getToken();
  const r = await fetch(`/api/auth${path}`, {
    method: method ?? (body !== undefined ? "POST" : "GET"),
    // same-origin: браузер обязан отправлять/принимать HttpOnly cookie сессии.
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data: any = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? "Проверьте поля формы" : `Ошибка ${r.status}`);
  }
  return data as T;
}
export async function logout() {
  try { await authApi("/logout", {}); } catch {}
  setToken(null);
  setStoredUser(null);
}

const AUTH_USER_KEY = "vp-auth-user";
export function setStoredUser(u: AuthUser | null) {
  try {
    if (u) localStorage.setItem(AUTH_USER_KEY, JSON.stringify(u));
    else localStorage.removeItem(AUTH_USER_KEY);
  } catch {}
}
function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(AUTH_USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch { return null; }
}

/** Кто вошёл. В этом бэкенде ручки «кто я» нет: почту и роль кладём при входе,
 *  а живость сессии проверяем пробой продуктовой ручки. */
export function useAuthUser() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    fetch("/api/projects", { credentials: "same-origin" })
      .then((r) => { if (alive) setUser(r.ok ? getStoredUser() : null); })
      .catch(() => { if (alive) setUser(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);
  return { user, loading, setUser };
}

/* ---------- validation ---------- */
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const USER_RE = /^[a-zA-Z0-9_.-]{3,32}$/;
function pwScore(p: string) {
  let s = 0;
  if (p.length >= 8) s++;
  if (p.length >= 12) s++;
  if (/[a-zа-я]/.test(p) && /[A-ZА-Я]/.test(p)) s++;
  if (/\d/.test(p)) s++;
  if (/[^A-Za-zА-Яа-я0-9]/.test(p)) s++;
  return Math.min(4, s);
}
function pwValid(p: string) { return p.length >= 8 && /[A-Za-zА-Яа-я]/.test(p) && /\d/.test(p); }

/* ---------- UI ---------- */
function Shell({ title, sub, children, foot }: { title: string; sub?: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="min-h-screen w-full bg-[#070909] text-white" style={FONT}>
      <style>{`@keyframes viaIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}@keyframes viaGlow{0%,100%{opacity:.35}50%{opacity:.6}}.via-in{animation:viaIn .45s cubic-bezier(.2,.8,.2,1) both}`}</style>
      <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
        <aside className="relative hidden overflow-hidden border-r border-white/[0.06] lg:block">
          <div className="absolute inset-0" style={{ background: `radial-gradient(60% 50% at 30% 30%, ${A}22, transparent 70%), radial-gradient(50% 40% at 80% 90%, ${A}14, transparent 70%)`, animation: "viaGlow 6s ease-in-out infinite" }} />
          <div className="relative flex h-full flex-col p-12">
            <a href="/home" className="flex items-center gap-3 text-[28px] font-bold tracking-wide"><span className="grid h-11 w-11 place-items-center rounded-[12px] text-[18px] text-black" style={{ background: A }}>V</span>VIA</a>
            <div className="mt-auto">
              <div className="mb-8 grid grid-cols-2 gap-3">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className={`flex items-center justify-center rounded-[16px] border border-dashed border-white/10 bg-white/[0.02] text-zinc-600 ${i === 0 ? "col-span-2 aspect-[16/7]" : "aspect-[4/3]"}`}>
                    <div className="flex flex-col items-center gap-2 text-[13px]"><ImageIcon className="h-6 w-6" />изображение</div>
                  </div>
                ))}
              </div>
              <h2 className="text-[40px] font-semibold leading-[1.1]">Контент-завод<br /><span style={{ color: A }}>мультфильмов и сериалов</span></h2>
              <p className="mt-4 max-w-md text-[17px] text-zinc-400">Сценарий, персонажи, кадры, анимация, озвучка и монтаж — в одном пайплайне.</p>
            </div>
          </div>
        </aside>
        <main className="flex items-center justify-center px-6 py-12">
          <div className="via-in w-full max-w-[460px]">
            <a href="/home" className="mb-8 flex items-center gap-3 text-[24px] font-bold lg:hidden"><span className="grid h-10 w-10 place-items-center rounded-[12px] text-black" style={{ background: A }}>V</span>VIA</a>
            <h1 className="text-[36px] font-semibold leading-tight">{title}</h1>
            {sub ? <p className="mt-2 text-[17px] text-zinc-400">{sub}</p> : null}
            <div className="mt-8">{children}</div>
            {foot ? <div className="mt-8 text-[16px] text-zinc-400">{foot}</div> : null}
          </div>
        </main>
      </div>
    </div>
  );
}

function Field({ label, icon, value, onChange, type = "text", error, ok, placeholder, autoComplete, name, reveal, hint }: {
  label: string; icon: React.ReactNode; value: string; onChange: (v: string) => void; type?: string; error?: string | null; ok?: boolean;
  placeholder?: string; autoComplete?: string; name: string; reveal?: boolean; hint?: React.ReactNode;
}) {
  const [show, setShow] = useState(false);
  const t = reveal ? (show ? "text" : "password") : type;
  const border = error ? "border-red-500/60" : ok ? "border-teal-400/40" : "border-white/10 focus-within:border-teal-400/50";
  return (
    <label className="mb-5 block">
      <span className="mb-2 block text-[15px] text-zinc-300">{label}</span>
      <span className={`flex h-[56px] items-center gap-3 rounded-[14px] border bg-white/[0.03] px-4 transition-colors duration-200 ${border}`}>
        <span className="text-zinc-500">{icon}</span>
        <input name={name} type={t} value={value} placeholder={placeholder} autoComplete={autoComplete} onChange={(e) => onChange(e.target.value)}
          className="h-full min-w-0 flex-1 bg-transparent text-[17px] text-white outline-none placeholder:text-zinc-600" />
        {ok && !error ? <Check className="h-5 w-5" style={{ color: A }} /> : null}
        {reveal ? (
          <button type="button" aria-label={show ? "Скрыть пароль" : "Показать пароль"} onClick={() => setShow((s) => !s)} className="text-zinc-500 transition-colors hover:text-zinc-200">
            {show ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
          </button>
        ) : null}
      </span>
      {error ? <span className="mt-1.5 flex items-center gap-1.5 text-[14px] text-red-400"><CircleAlert className="h-4 w-4" />{error}</span> : hint ? <span className="mt-1.5 block text-[14px] text-zinc-500">{hint}</span> : null}
    </label>
  );
}

function Strength({ pw }: { pw: string }) {
  const s = pwScore(pw);
  const labels = ["Очень слабый", "Слабый", "Средний", "Хороший", "Надёжный"];
  const colors = ["#ef4444", "#f97316", "#eab308", "#5eead4", A];
  if (!pw) return null;
  return (
    <div className="-mt-3 mb-5">
      <div className="flex gap-1.5">{[0, 1, 2, 3].map((i) => <div key={i} className="h-1.5 flex-1 rounded-full transition-all duration-300" style={{ background: i < Math.max(1, s) ? colors[s] : "rgba(255,255,255,.08)" }} />)}</div>
      <div className="mt-1.5 text-[14px]" style={{ color: colors[s] }}>Надёжность: {labels[s]}</div>
    </div>
  );
}

function Submit({ loading, children, disabled }: { loading?: boolean; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button type="submit" disabled={loading || disabled}
      className="flex h-[56px] w-full items-center justify-center gap-2 rounded-[14px] border text-[17px] font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50"
      style={{ borderColor: A + "55", background: A + "1f", color: "#ccfbf1" }}>
      {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : null}{children}
    </button>
  );
}
function Alert({ kind, children }: { kind: "error" | "ok" | "dev"; children: React.ReactNode }) {
  const st = kind === "error" ? "border-red-500/30 bg-red-500/[0.08] text-red-300" : kind === "ok" ? "border-teal-400/30 bg-teal-400/[0.07] text-teal-200" : "border-amber-400/25 bg-amber-400/[0.06] text-amber-200";
  return <div className={`via-in mb-5 rounded-[14px] border px-4 py-3 text-[15px] leading-relaxed break-words ${st}`}>{children}</div>;
}
const L = ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href} className="transition-colors hover:underline" style={{ color: A }}>{children}</a>;

/* ---------- pages ---------- */
export function LoginPage() {
  const [login, setLogin] = useState(""); const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [ok, setOk] = useState(false);
  const [touched, setTouched] = useState(false);
  const next = typeof window !== "undefined"
    ? safeAuthNext(new URLSearchParams(location.search).get("next"), "/")
    : "/";
  const reset = typeof window !== "undefined" && new URLSearchParams(location.search).get("reset") === "1";
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setTouched(true); setErr(null);
    if (!login.trim() || !pw) return;
    setLoading(true);
    try {
      // Контракт этого бэкенда: адрес и пароль (email + password), в ответ — сессия.
      const r = await authApi<{ token?: string | null; email?: string; role?: string }>("/login", { email: login.trim(), password: pw });
      setToken(r.token ?? null);
      const mail = r.email ?? login.trim();
      setStoredUser({ id: 0, email: mail, username: mail.split("@")[0], name: "", role: r.role ?? "member", email_verified: true, created_at: Date.now() });
      setOk(true); setTimeout(() => { location.href = next; }, 700);
    } catch (e: any) { setErr(e.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Вход в VIA" sub="Рады видеть снова" foot={<>Нет аккаунта? <L href="/register">Зарегистрироваться</L></>}>
      <form onSubmit={submit} noValidate>
        {reset ? <Alert kind="ok">Пароль изменён. Войдите с новым паролем.</Alert> : null}
        {err ? <Alert kind="error">{err}</Alert> : null}
        {ok ? <Alert kind="ok">Вход выполнен, открываем студию…</Alert> : null}
        <Field name="login" label="Email" icon={<Mail className="h-5 w-5" />} value={login} onChange={setLogin} type="email" autoComplete="username" placeholder="you@mail.com" error={touched && !login.trim() ? "Введите email" : null} />
        <Field name="password" label="Пароль" icon={<Lock className="h-5 w-5" />} value={pw} onChange={setPw} reveal autoComplete="current-password" placeholder="••••••••" error={touched && !pw ? "Введите пароль" : null} />
        <div className="-mt-2 mb-6 text-right text-[15px]"><L href="/forgot-password">Забыли пароль?</L></div>
        <Submit loading={loading}>Войти</Submit>
        <a href="/home" className="mt-4 flex h-[52px] items-center justify-center gap-2 rounded-[14px] border border-white/10 text-[16px] text-zinc-400 transition-colors hover:text-zinc-200"><ArrowLeft className="h-4 w-4" />На главную</a>
      </form>
    </Shell>
  );
}

export function RegisterPage() {
  const [f, setF] = useState({ name: "", email: "", password: "", confirm: "", code: "" });
  const [agree, setAgree] = useState(false);
  const [step, setStep] = useState<"form" | "code">("form");
  const [sentTo, setSentTo] = useState("");
  const [touched, setTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const e = useMemo(() => ({
    email: !f.email ? "Введите email" : !EMAIL_RE.test(f.email) ? "Некорректный email" : null,
    password: !f.password ? "Придумайте пароль" : !pwValid(f.password) ? "Минимум 8 символов, буквы и цифры" : null,
    confirm: f.confirm !== f.password || !f.confirm ? "Пароли не совпадают" : null,
    agree: !agree ? "Нужно согласие" : null,
  }), [f, agree]);
  const show = (k: keyof typeof e, v: string) => (touched || v ? e[k] : null);
  async function sendCode(ev: React.FormEvent) {
    ev.preventDefault(); setTouched(true); setErr(null); setMsg(null);
    if (Object.values(e).some(Boolean)) return;
    setLoading(true);
    try {
      // Здесь регистрация двухшаговая: сначала код на почту, потом подтверждение.
      await authApi("/register/send-code", { email: f.email.trim(), password: f.password, display_name: f.name.trim() });
      setSentTo(f.email.trim());
      setStep("code");
      setMsg(`Код отправлен на ${f.email.trim()}. Он действует 15 минут.`);
    } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  async function confirm(ev: React.FormEvent) {
    ev.preventDefault(); setErr(null);
    if (f.code.trim().length < 4) { setErr("Введите код из письма"); return; }
    setLoading(true);
    try {
      const r = await authApi<{ token?: string | null; email?: string; role?: string }>("/register/confirm", {
        email: sentTo,
        code: f.code.trim(),
        password: f.password,
        display_name: f.name.trim(),
      });
      setToken(r.token ?? null);
      const mail = r.email ?? sentTo;
      setStoredUser({ id: 0, email: mail, username: mail.split("@")[0], name: f.name.trim(), role: r.role ?? "member", email_verified: true, created_at: Date.now() });
      location.href = "/";
    } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Создать аккаунт" sub="Пара минут — и можно собирать первый ролик" foot={<>Уже есть аккаунт? <L href="/login">Войти</L></>}>
      {step === "form" ? (
        <form onSubmit={sendCode} noValidate>
          {err ? <Alert kind="error">{err}</Alert> : null}
          <Field name="name" label="Имя (необязательно)" icon={<User className="h-5 w-5" />} value={f.name} onChange={set("name")} autoComplete="name" placeholder="Как к вам обращаться" />
          <Field name="email" label="Email" icon={<Mail className="h-5 w-5" />} value={f.email} onChange={set("email")} type="email" autoComplete="email" placeholder="you@mail.com" error={show("email", f.email)} ok={!e.email} />
          <Field name="password" label="Пароль" icon={<Lock className="h-5 w-5" />} value={f.password} onChange={set("password")} reveal autoComplete="new-password" placeholder="Минимум 8 символов" error={show("password", f.password)} />
          <Strength pw={f.password} />
          <Field name="confirm" label="Повторите пароль" icon={<Lock className="h-5 w-5" />} value={f.confirm} onChange={set("confirm")} reveal autoComplete="new-password" error={show("confirm", f.confirm)} ok={!e.confirm} />
          <label className="mb-6 flex cursor-pointer items-start gap-3 text-[15px] text-zinc-400">
            <input type="checkbox" name="agree" checked={agree} onChange={(x) => setAgree(x.target.checked)} className="mt-1 h-4 w-4 accent-teal-400" />
            <span>Принимаю условия использования и политику конфиденциальности{touched && e.agree ? <span className="block text-red-400">{e.agree}</span> : null}</span>
          </label>
          <Submit loading={loading}>Получить код на почту</Submit>
        </form>
      ) : (
        <form onSubmit={confirm} noValidate>
          {msg ? <Alert kind="ok">{msg}</Alert> : null}
          {err ? <Alert kind="error">{err}</Alert> : null}
          <Field name="code" label="Код из письма" icon={<MailCheck className="h-5 w-5" />} value={f.code} onChange={set("code")} autoComplete="one-time-code" placeholder="123456" />
          <Submit loading={loading}>Зарегистрироваться</Submit>
          <button type="button" onClick={() => { setStep("form"); setMsg(null); setErr(null); }} className="mt-4 h-[52px] w-full rounded-[14px] border border-white/10 text-[16px] text-zinc-400 transition-colors hover:text-zinc-200">Изменить данные</button>
        </form>
      )}
    </Shell>
  );
}

export function VerifyEmailPage() {
  // В этом бэкенде почта подтверждается кодом прямо в регистрации — отдельного шага нет.
  return (
    <Shell title="Подтверждение не требуется" sub="Почта подтверждается кодом во время регистрации" foot={<><L href="/login">Ко входу</L></>}>
      <a href="/login" className="flex h-[56px] items-center justify-center rounded-[14px] border text-[17px] font-medium" style={{ borderColor: A + "55", background: A + "1f", color: "#ccfbf1" }}>Войти</a>
    </Shell>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [touched, setTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const bad = !email ? "Введите email" : !EMAIL_RE.test(email) ? "Некорректный email" : null;
  async function sendCode(ev: React.FormEvent) {
    ev.preventDefault(); setTouched(true); setErr(null); setMsg(null);
    if (bad) return;
    setLoading(true);
    try {
      await authApi("/reset-password/send-code", { email: email.trim() });
      setStep("code");
      setMsg(`Если аккаунт с таким адресом есть, код уже отправлен на ${email.trim()}.`);
    } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  async function confirm(ev: React.FormEvent) {
    ev.preventDefault(); setErr(null);
    if (code.trim().length < 4) { setErr("Введите код из письма"); return; }
    if (!pwValid(pw)) { setErr("Пароль: минимум 8 символов, буквы и цифры"); return; }
    setLoading(true);
    try {
      await authApi("/reset-password/confirm", { email: email.trim(), code: code.trim(), new_password: pw });
      location.href = "/login?reset=1";
    } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Восстановление пароля" sub={step === "email" ? "Пришлём код на почту" : "Введите код и новый пароль"} foot={<><L href="/login">← Вернуться ко входу</L></>}>
      {step === "email" ? (
        <form onSubmit={sendCode} noValidate>
          {err ? <Alert kind="error">{err}</Alert> : null}
          <Field name="email" label="Email" icon={<Mail className="h-5 w-5" />} value={email} onChange={setEmail} type="email" autoComplete="email" placeholder="you@mail.com" error={touched ? bad : null} />
          <Submit loading={loading}>Получить код</Submit>
        </form>
      ) : (
        <form onSubmit={confirm} noValidate>
          {msg ? <Alert kind="ok">{msg}</Alert> : null}
          {err ? <Alert kind="error">{err}</Alert> : null}
          <Field name="code" label="Код из письма" icon={<MailCheck className="h-5 w-5" />} value={code} onChange={setCode} autoComplete="one-time-code" placeholder="123456" />
          <Field name="password" label="Новый пароль" icon={<Lock className="h-5 w-5" />} value={pw} onChange={setPw} reveal autoComplete="new-password" placeholder="Минимум 8 символов" />
          <Strength pw={pw} />
          <Submit loading={loading}>Сменить пароль</Submit>
        </form>
      )}
    </Shell>
  );
}

export function ResetPasswordPage() {
  // Сброс пароля идёт кодом из письма — это та же страница, что и «забыли пароль».
  return <ForgotPasswordPage />;
}
