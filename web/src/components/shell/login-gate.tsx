"use client";

/**
 * Приглашение войти. Формы здесь нет и быть не должно.
 *
 * Единственный источник личности — биллинг (`docs/SAAS-PIVOT.md` §4.1). Своя
 * форма означала бы вторую базу пользователей, второе восстановление пароля и
 * второе место, где можно ошибиться с паролями. Поэтому здесь только кнопка,
 * уводящая туда, где вход уже написан и работает в проде.
 *
 * Экран закрывает интерфейс целиком, а не показывается баннером сверху. Без
 * токена не работает ни одна ручка `/api/*`, и оставить под баннером
 * мёртвый канвас значит предложить человеку нажимать на то, что молча не
 * отвечает.
 *
 * В режиме владельца этот экран не появляется никогда: там личностей нет.
 */

import { useEffect } from "react";
import { KeyRound, LogIn } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuthStatus } from "@/hooks/use-identity";
import { clearToken } from "@/lib/identity-api";

/** Куда уводить за токеном. Домен задаётся сборкой, а не зашит в код. */
function loginUrl(brand: string): string {
  const base = process.env.NEXT_PUBLIC_BILLING_URL || "";
  const back = typeof window !== "undefined" ? window.location.origin : "";
  const params = new URLSearchParams({ redirect: back });
  if (brand) params.set("brand", brand);
  return base ? `${base}/login?${params}` : "";
}

export function LoginGate() {
  const { data: status } = useAuthStatus();
  const url = loginUrl(status?.brand ?? "");

  // Если токен в браузере протух/недействителен, и включена локальная авторизация —
  // сбрасываем его и переходим к форме входа студии.
  useEffect(() => {
    if (typeof window !== "undefined" && status?.auth_required) {
      clearToken();
      window.location.reload();
    }
  }, [status?.auth_required]);

  const handleLocalLogin = () => {
    if (typeof window !== "undefined") {
      clearToken();
      window.location.reload();
    }
  };

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background p-6 text-foreground">
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-white/[0.02] p-6">
        <h1 className="mb-2 text-lg font-medium">Нужно войти</h1>
        <p className="mb-5 text-sm text-muted-foreground">
          Студия не заводит своих учётных записей — вход общий с личным
          кабинетом. Раскадровка и кадры бесплатны, оплата начинается с рендера
          видео.
        </p>
        {url ? (
          <Button asChild className="w-full gap-2">
            <a href={url}>
              <LogIn className="h-4 w-4" />
              Войти через личный кабинет
            </a>
          </Button>
        ) : (
          <div className="space-y-3">
            <Button onClick={handleLocalLogin} className="w-full gap-2">
              <KeyRound className="h-4 w-4" />
              Войти по логину и паролю
            </Button>
            <p className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-xs text-muted-foreground">
              Адрес внешнего кабинета не настроен (NEXT_PUBLIC_BILLING_URL). Используется локальный вход студии.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
