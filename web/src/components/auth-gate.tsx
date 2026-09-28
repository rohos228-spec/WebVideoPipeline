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

  // Предпросмотр экрана авторизации для локального тестирования UI (?auth=1 или #auth)
  const previewAuth =
    typeof window !== "undefined" &&
    (new URLSearchParams(window.location.search).has("auth") ||
      window.location.hash === "#auth");

  // Учёток нет — режим владельца без замка. Пускаем сразу (если не запрошен ?auth=1)
  if (!status?.auth_required && !previewAuth) return <>{children}</>;

  if (!token || previewAuth) {
    return (
      <AuthScreen
        isLocalPreview={!status?.auth_required}
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

function AuthScreen({
  onDone,
  isLocalPreview = false,
}: {
  onDone: (token: string) => void;
  isLocalPreview?: boolean;
}) {
  const [mode, setMode] = useState<AuthMode>("login");

  // Вход
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  // Регистрация
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regDisplayName, setRegDisplayName] = useState("");
  const [regCode, setRegCode] = useState("");

  // Сброс пароля
  const [resetEmail, setResetEmail] = useState("");
  const [resetCode, setResetCode] = useState("");
  const [resetNewPassword, setResetNewPassword] = useState("");

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
    if (next === "register") {
      setRegEmail("");
      setRegPassword("");
      setRegDisplayName("");
      setRegCode("");
    } else if (next === "login") {
      setLoginPassword("");
    } else if (next === "forgot") {
      setResetEmail("");
      setResetCode("");
      setResetNewPassword("");
    }
    setMode(next);
  };

  // 1. Вход по логину и паролю
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginEmail.trim() || !loginPassword || busy) return;
    if (isLocalPreview) {
      setSuccessInfo("Тестовый вход выполнен (локальный режим)");
      setTimeout(() => onDone("test-token"), 600);
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      const res = await api.login(loginEmail.trim(), loginPassword);
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
    if (!regEmail.trim() || regPassword.length < 8 || busy) return;
    if (isLocalPreview) {
      setSuccessInfo("Тестовый режим (локально): проверочный код — 123456");
      setCountdown(60);
      setMode("register_code");
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      const res = await api.registerSendCode({
        email: regEmail.trim(),
        password: regPassword,
        display_name: regDisplayName.trim(),
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
    if (!regCode.trim() || busy) return;
    if (isLocalPreview) {
      setSuccessInfo("Тестовая регистрация успешно пройдена!");
      setTimeout(() => onDone("test-token"), 600);
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      const res = await api.registerConfirm({
        email: regEmail.trim(),
        code: regCode.trim(),
        password: regPassword,
        display_name: regDisplayName.trim(),
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
    if (!resetEmail.trim() || busy) return;
    if (isLocalPreview) {
      setSuccessInfo("Тестовый режим (локально): проверочный код сброса — 123456");
      setCountdown(60);
      setMode("forgot_code");
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      const res = await api.resetPasswordSendCode({ email: resetEmail.trim() });
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
    if (!resetCode.trim() || resetNewPassword.length < 8 || busy) return;
    if (isLocalPreview) {
      setSuccessInfo("Тестовый режим (локально): пароль успешно изменён!");
      setTimeout(() => onDone("test-token"), 600);
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      const res = await api.resetPasswordConfirm({
        email: resetEmail.trim(),
        code: resetCode.trim(),
        new_password: resetNewPassword,
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
    if (isLocalPreview) {
      setSuccessInfo("Новый проверочный код отправлен: 123456");
      setCountdown(60);
      return;
    }
    setBusy(true);
    clearMessages();
    try {
      if (mode === "register_code") {
        await api.registerSendCode({
          email: regEmail.trim(),
          password: regPassword,
          display_name: regDisplayName.trim(),
        });
      } else if (mode === "forgot_code") {
        await api.resetPasswordSendCode({ email: resetEmail.trim() });
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
      {/* Баннер локального предпросмотра */}
      {isLocalPreview && (
        <div className="mb-4 rounded-lg border border-accent/40 bg-accent/10 p-2.5 text-center text-[12px] text-accent">
          Режим локальной проверки UI. Тестовый проверочный код: <strong>123456</strong>
        </div>
      )}

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
              name="login_email"
              autoComplete="username"
              placeholder="name@example.com"
              value={loginEmail}
              onChange={(e) => setLoginEmail(e.target.value)}
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
              name="login_password"
              autoComplete="current-password"
              placeholder="••••••••••••"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!loginEmail.trim() || !loginPassword || busy}
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
        <form className="flex flex-col gap-4" onSubmit={handleSendRegisterCode} autoComplete="off">
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Почта
            </span>
            <input
              autoFocus
              type="email"
              name="reg_email"
              autoComplete="off"
              placeholder="name@example.com"
              value={regEmail}
              onChange={(e) => setRegEmail(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Ваше имя <span className="text-content-faint/60 lowercase">(необязательно)</span>
            </span>
            <input
              type="text"
              name="reg_name"
              autoComplete="off"
              placeholder="Иван"
              value={regDisplayName}
              onChange={(e) => setRegDisplayName(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Пароль <span className="text-content-faint/60 lowercase">(от 8 символов)</span>
            </span>
            <input
              type="password"
              name="reg_password"
              autoComplete="new-password"
              placeholder="Минимум 8 символов"
              value={regPassword}
              onChange={(e) => setRegPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!regEmail.trim() || regPassword.length < 8 || busy}
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
        <form className="flex flex-col gap-4" onSubmit={handleConfirmRegister} autoComplete="off">
          <div className="rounded-lg border border-border/60 bg-surface-sunken/40 p-3 text-[13px] text-content-muted">
            Код подтверждения отправлен на <strong className="text-content">{regEmail}</strong>.
            Проверьте входящие или папку «Спам».
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              6-значный проверочный код
            </span>
            <input
              autoFocus
              type="text"
              name="reg_code"
              maxLength={8}
              autoComplete="one-time-code"
              placeholder="123456"
              value={regCode}
              onChange={(e) => setRegCode(e.target.value)}
              className="h-12 rounded-md border border-border bg-surface-raised px-3 text-center font-mono text-[22px] tracking-[6px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={regCode.trim().length < 4 || busy}
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
              onClick={() => {
                clearMessages();
                setMode("register");
              }}
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
        <form className="flex flex-col gap-4" onSubmit={handleSendResetCode} autoComplete="off">
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Почта аккаунта
            </span>
            <input
              autoFocus
              type="email"
              name="reset_email"
              autoComplete="off"
              placeholder="name@example.com"
              value={resetEmail}
              onChange={(e) => setResetEmail(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={!resetEmail.trim() || busy}
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
        <form className="flex flex-col gap-4" onSubmit={handleConfirmReset} autoComplete="off">
          <div className="rounded-lg border border-border/60 bg-surface-sunken/40 p-3 text-[13px] text-content-muted">
            Код сброса пароля отправлен на <strong className="text-content">{resetEmail}</strong>.
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Код из письма
            </span>
            <input
              autoFocus
              type="text"
              name="reset_code"
              maxLength={8}
              autoComplete="one-time-code"
              placeholder="123456"
              value={resetCode}
              onChange={(e) => setResetCode(e.target.value)}
              className="h-12 rounded-md border border-border bg-surface-raised px-3 text-center font-mono text-[22px] tracking-[6px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium uppercase tracking-wider text-content-faint">
              Новый пароль <span className="text-content-faint/60 lowercase">(от 8 символов)</span>
            </span>
            <input
              type="password"
              name="reset_new_password"
              autoComplete="new-password"
              placeholder="Минимум 8 символов"
              value={resetNewPassword}
              onChange={(e) => setResetNewPassword(e.target.value)}
              className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
            />
          </label>

          <Button
            type="submit"
            variant="primary"
            disabled={resetCode.trim().length < 4 || resetNewPassword.length < 8 || busy}
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
