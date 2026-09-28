"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { Button, Working } from "@/components/ui/bits";
import { ArrowLeft, CheckCircle2, KeyRound, Mail, RefreshCw, User } from "lucide-react";

/**
 * Дверь студии с поддержкой входа, регистрации и восстановления пароля.
 *
 * Если учётные записи отключены (режим владельца на одной машине) — пускаем сразу.
 * Если включены — показываем форму аутентификации.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data: status, isPending } = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 60_000,
  });

  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setToken(api.readToken());
    setReady(true);
  }, []);

  if (isPending || !ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-surface-base">
        <Working label="Проверяю доступ" />
      </div>
    );
  }

  // Учёток нет — режим владельца без замка. Пускаем сразу.
  if (!status?.auth_required) return <>{children}</>;

  if (!token) {
    return (
      <AuthScreen
        onDone={(fresh) => {
          setToken(fresh);
          qc.invalidateQueries();
        }}
      />
    );
  }

  return <>{children}</>;
}

type AuthMode = "login" | "register" | "register_code" | "forgot" | "forgot_code";

function AuthScreen({ onDone }: { onDone: (token: string) => void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const [error, setError] = useState("");
  const [successInfo, setSuccessInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const [countdown, setCountdown] = useState(0);

  // Таймер обратного отсчета для повторной отправки кода (60 сек)
  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => {
      setCountdown((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  const clearMessages = () => {
    setError("");
    setSuccessInfo("");
  };

  const switchMode = (next: AuthMode) => {
    clearMessages();
    setCode("");
    setMode(next);
  };

  // 1. Вход по логину и паролю
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password || busy) return;
    setBusy(true);
    clearMessages();
    try {
      const res = await api.login(email.trim(), password);
      api.saveToken(res.token);
      onDone(res.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Неверный адрес или пароль");
    } finally {
      setBusy(false);
    }
  };

  // 2. Отправка проверочного кода для регистрации
  const handleSendRegisterCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password || busy) return;
    setBusy(true);
    clearMessages();
    try {
      const res = await api.registerSendCode({
        email: email.trim(),
        password,
        display_name: displayName.trim(),
      });
      setSuccessInfo(res.message || "Код подтверждения отправлен на вашу почту");
      setCountdown(60);
      setMode("register_code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить код");
    } finally {
      setBusy(false);
    }
  };

  // 3. Подтверждение кода регистрации и создание аккаунта
  const handleConfirmRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    clearMessages();
    try {
      const res = await api.registerConfirm({
        email: email.trim(),
        code: code.trim(),
        password,
        display_name: displayName.trim(),
      });
      api.saveToken(res.token);
      onDone(res.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка подтверждения кода");
    } finally {
      setBusy(false);
    }
  };

  // 4. Запрос кода для сброса пароля
  const handleSendResetCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    clearMessages();
    try {
      const res = await api.resetPasswordSendCode({ email: email.trim() });
      setSuccessInfo(res.message || "Код сброса отправлен на вашу почту");
      setCountdown(60);
      setMode("forgot_code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка отправки кода");
    } finally {
      setBusy(false);
    }
  };

  // 5. Подтверждение кода и установка нового пароля
  const handleConfirmReset = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || !newPassword || busy) return;
    setBusy(true);
    clearMessages();
    try {
      const res = await api.resetPasswordConfirm({
        email: email.trim(),
        code: code.trim(),
        new_password: newPassword,
      });
      api.saveToken(res.token);
      onDone(res.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сменить пароль");
    } finally {
      setBusy(false);
    }
  };

  // Повторная отправка кода
  const handleResendCode = async () => {
    if (countdown > 0 || busy) return;
    setBusy(true);
    clearMessages();
    try {
      if (mode === "register_code") {
        await api.registerSendCode({
          email: email.trim(),
          password,
          display_name: displayName.trim(),
        });
      } else if (mode === "forgot_code") {
        await api.resetPasswordSendCode({ email: email.trim() });
      }
      setSuccessInfo("Новый проверочный код отправлен");
      setCountdown(60);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить код повторно");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-[420px] flex-col justify-center px-6 py-12">
      {/* Шапка формы */}
      <div className="mb-6">
        <h1 className="font-display text-[30px] font-semibold tracking-tight text-content">
          Видеостудия
        </h1>
        <p className="mt-1.5 text-[14px] text-content-muted">
          {mode === "login" && "Вход по учётной записи студии"}
          {mode === "register" && "Создание новой учётной записи"}
          {mode === "register_code" && "Подтверждение адреса электронной почты"}
          {mode === "forgot" && "Восстановление доступа к аккаунту"}
          {mode === "forgot_code" && "Ввод кода и установка нового пароля"}
        </p>
      </div>

      {/* Сообщение об успешной отправке кода */}
      {successInfo && (
        <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-ok/30 bg-ok-muted/20 p-3 text-[13px] text-ok">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-ok" />
          <span>{successInfo}</span>
        </div>
      )}

      {/* Ошибка */}
      {error && (
        <div className="mb-4 rounded-lg border border-danger/30 bg-danger-muted/20 p-3 text-[13px] text-danger" role="alert">
          {error}
        </div>
      )}

      {/* ── 1. Экран ВХОДА ──────────────────────────────────────────────── */}
      {mode === "login" && (
        <form className="flex flex-col gap-4" onSubmit={handleLogin}>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Почта
            </span>
            <input
              autoFocus
              type="email"
              autoComplete="username"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
                Пароль
              </span>
              <button
                type="button"
                onClick={() => switchMode("forgot")}
                className="text-[12px] text-accent hover:underline focus:outline-none"
              >
                Забыли пароль?
              </button>
            </div>
            <input
              type="password"
              autoComplete="current-password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!email.trim() || !password || busy}
            className="mt-2 w-full"
          >
            {busy ? "Вход…" : "Войти"}
          </Button>

          <div className="mt-4 flex items-center justify-center gap-1.5 border-t border-border/40 pt-4 text-[13px] text-content-muted">
            <span>Ещё нет аккаунта?</span>
            <button
              type="button"
              onClick={() => switchMode("register")}
              className="font-medium text-accent hover:underline focus:outline-none"
            >
              Зарегистрироваться
            </button>
          </div>
        </form>
      )}

      {/* ── 2. Экран РЕГИСТРАЦИИ (Ввод данных) ─────────────────────────── */}
      {mode === "register" && (
        <form className="flex flex-col gap-4" onSubmit={handleSendRegisterCode}>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Почта
            </span>
            <input
              autoFocus
              type="email"
              autoComplete="email"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Ваше имя <span className="text-content-faint/60 lowercase">(необязательно)</span>
            </span>
            <input
              type="text"
              placeholder="Иван"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Пароль <span className="text-content-faint/60 lowercase">(от 12 символов)</span>
            </span>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Минимум 12 символов"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!email.trim() || password.length < 6 || busy}
            className="mt-2 w-full gap-2"
          >
            <Mail className="h-4 w-4" />
            {busy ? "Отправка кода…" : "Получить код подтверждения"}
          </Button>

          <div className="mt-4 flex items-center justify-center gap-1.5 border-t border-border/40 pt-4 text-[13px] text-content-muted">
            <span>Уже зарегистрированы?</span>
            <button
              type="button"
              onClick={() => switchMode("login")}
              className="font-medium text-accent hover:underline focus:outline-none"
            >
              Войти
            </button>
          </div>
        </form>
      )}

      {/* ── 3. Экран ВВОДА КОДА РЕГИСТРАЦИИ ────────────────────────────── */}
      {mode === "register_code" && (
        <form className="flex flex-col gap-4" onSubmit={handleConfirmRegister}>
          <div className="rounded-lg border border-border/60 bg-surface-sunken/40 p-3 text-[13px] text-content-muted">
            Код подтверждения отправлен на <strong className="text-content">{email}</strong>.
            Проверьте входящие или папку «Спам».
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              6-значный проверочный код
            </span>
            <input
              autoFocus
              type="text"
              maxLength={8}
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="h-12 rounded-md border border-border bg-surface-raised px-3 text-center font-mono text-[22px] tracking-[6px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={code.trim().length < 4 || busy}
            className="mt-2 w-full"
          >
            {busy ? "Проверяю…" : "Подтвердить и войти"}
          </Button>

          <div className="mt-3 flex flex-col items-center gap-2 text-[13px]">
            <button
              type="button"
              disabled={countdown > 0 || busy}
              onClick={handleResendCode}
              className="inline-flex items-center gap-1.5 text-accent hover:underline disabled:pointer-events-none disabled:text-content-faint focus:outline-none"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
              {countdown > 0 ? `Отправить код повторно (${countdown} с)` : "Отправить код повторно"}
            </button>

            <button
              type="button"
              onClick={() => switchMode("register")}
              className="inline-flex items-center gap-1 text-content-faint hover:text-content hover:underline focus:outline-none"
            >
              <ArrowLeft className="h-3 w-3" />
              Изменить email или пароль
            </button>
          </div>
        </form>
      )}

      {/* ── 4. Экран ЗАПРОСА СБРОСА ПАРОЛЯ ──────────────────────────────── */}
      {mode === "forgot" && (
        <form className="flex flex-col gap-4" onSubmit={handleSendResetCode}>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Почта аккаунта
            </span>
            <input
              autoFocus
              type="email"
              autoComplete="email"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!email.trim() || busy}
            className="mt-2 w-full gap-2"
          >
            <KeyRound className="h-4 w-4" />
            {busy ? "Отправка…" : "Отправить код сброса"}
          </Button>

          <div className="mt-4 flex items-center justify-center border-t border-border/40 pt-4 text-[13px]">
            <button
              type="button"
              onClick={() => switchMode("login")}
              className="inline-flex items-center gap-1 text-content-muted hover:text-content hover:underline focus:outline-none"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Вернуться ко входу
            </button>
          </div>
        </form>
      )}

      {/* ── 5. Экран ПОДТВЕРЖДЕНИЯ СБРОСА И НОВОГО ПАРОЛЯ ────────────────── */}
      {mode === "forgot_code" && (
        <form className="flex flex-col gap-4" onSubmit={handleConfirmReset}>
          <div className="rounded-lg border border-border/60 bg-surface-sunken/40 p-3 text-[13px] text-content-muted">
            Код сброса пароля отправлен на <strong className="text-content">{email}</strong>.
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Код из письма
            </span>
            <input
              autoFocus
              type="text"
              maxLength={8}
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="h-12 rounded-md border border-border bg-surface-raised px-3 text-center font-mono text-[22px] tracking-[6px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Новый пароль <span className="text-content-faint/60 lowercase">(от 12 символов)</span>
            </span>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Новый надёжный пароль"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={code.trim().length < 4 || newPassword.length < 6 || busy}
            className="mt-2 w-full"
          >
            {busy ? "Сохраняю…" : "Сменить пароль и войти"}
          </Button>

          <div className="mt-3 flex flex-col items-center gap-2 text-[13px]">
            <button
              type="button"
              disabled={countdown > 0 || busy}
              onClick={handleResendCode}
              className="inline-flex items-center gap-1.5 text-accent hover:underline disabled:pointer-events-none disabled:text-content-faint focus:outline-none"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
              {countdown > 0 ? `Отправить код повторно (${countdown} с)` : "Отправить код повторно"}
            </button>

            <button
              type="button"
              onClick={() => switchMode("login")}
              className="inline-flex items-center gap-1 text-content-faint hover:text-content hover:underline focus:outline-none"
            >
              <ArrowLeft className="h-3 w-3" />
              Отмена
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
