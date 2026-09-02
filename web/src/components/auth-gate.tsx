"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { Button } from "@/components/ui/bits";
import { Working } from "@/components/ui/bits";

/**
 * Дверь студии. Три состояния, и первое из них — не «вошёл или нет».
 *
 * Установка бывает без учётных записей вовсе: одна машина, SQLite, владелец за
 * своим столом (`accounts_enabled: false`). Показывать там форму входа значит
 * запереть человека перед дверью, у которой нет замка. Поэтому сперва
 * спрашивается `/api/auth/status`, и только потом решается, что рисовать.
 *
 * Токен живёт в `localStorage`, а не в памяти: перезагрузка страницы не должна
 * стоить входа. Заголовок `Authorization` ставит `lib/api`; параллельно тот же
 * токен лежит в cookie `HttpOnly`, которую поставил сервер, — она нужна
 * `EventSource` и WebSocket, потому что заголовков они не ставят.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data: status, isPending } = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 60_000,
  });

  // Читаем хранилище только на клиенте: на сервере его нет, и обращение к нему
  // при отрисовке уронило бы страницу целиком.
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setToken(api.readToken());
    setReady(true);
  }, []);

  if (isPending || !ready) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Working label="Проверяю доступ" />
      </div>
    );
  }

  // Учёток нет — дверь без замка. Пускаем сразу.
  if (!status?.auth_required) return <>{children}</>;

  if (!token) {
    return (
      <LoginScreen
        onDone={(fresh) => {
          setToken(fresh);
          // Всё, что успело ответить 401 до входа, надо перезапросить.
          qc.invalidateQueries();
        }}
      />
    );
  }

  return <>{children}</>;
}

function LoginScreen({ onDone }: { onDone: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!email.trim() || !password || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.login(email.trim(), password);
      api.saveToken(res.token);
      onDone(res.token);
    } catch (e) {
      // Текст приходит с сервера и намеренно один на все причины: «нет такого
      // адреса» и «не тот пароль» снаружи неразличимы, иначе форма входа
      // работает справочником заведённых аккаунтов.
      setError(e instanceof Error ? e.message : "не вышло войти");
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-[420px] flex-col justify-center px-8">
      <h1 className="font-display text-[32px] leading-tight text-content">Видеостудия</h1>
      <p className="mt-2 text-[14px] text-content-muted">
        Внутренний инструмент студии. Вход по учётной записи.
      </p>

      <form
        className="mt-8 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] uppercase tracking-wide text-content-faint">Почта</span>
          <input
            autoFocus
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="h-10 rounded-md border border-border bg-surface-raised px-3 text-[15px] text-content outline-none focus-visible:border-accent"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] uppercase tracking-wide text-content-faint">Пароль</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-10 rounded-md border border-border bg-surface-raised px-3 font-mono text-[14px] text-content outline-none focus-visible:border-accent"
          />
        </label>

        {/* Место под ошибку занято всегда: иначе форма прыгает на каждой
            неудачной попытке, и поле пароля уезжает из-под курсора. */}
        <p className="min-h-[18px] text-[13px] text-danger" role="alert">
          {error}
        </p>

        <Button type="submit" variant="primary" disabled={!email.trim() || !password || busy}>
          {busy ? "Проверяю…" : "Войти"}
        </Button>
      </form>

      {/* Команду восстановления сюда не пишем. Этим экраном пользуется вся
          студия, а не только тот, кто держит сервер: «выполните python3 -m …»
          человеку, который пришёл смонтировать ролик, сказать нечего. */}
      <p className="mt-6 text-[12px] leading-relaxed text-content-faint">
        Учётные записи заводит администратор студии — к нему же за новым паролем.
      </p>
    </div>
  );
}
