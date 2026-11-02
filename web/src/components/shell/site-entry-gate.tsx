"use client";

import { useEffect, useState } from "react";

/**
 * Вход на сайт начинается с лендинга, а не со студии.
 *
 * В main это делает сервер (SITE_AUTH_REQUIRED=1 + app/web/access.py): анонимному
 * посетителю отдаётся лендинг, студия — только после входа. В этом бэкенде такого
 * гейта пока нет, поэтому проверяем на клиенте: если вход требуется, а сессии нет,
 * уводим на /home. В режиме владельца (вход выключен) студия открывается сразу,
 * как и в локальной сборке.
 */
export function SiteEntryGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const st = await fetch("/api/auth/status", { credentials: "same-origin" }).then((r) => r.json());
        const required = Boolean(st?.auth_required || st?.site_auth_required);
        if (required) {
          const probe = await fetch("/api/projects", { credentials: "same-origin" });
          if (probe.status === 401 || probe.status === 403) {
            window.location.replace("/home");
            return;
          }
        }
      } catch {
        // Бэкенд недоступен — не запираем студию из-за сетевой ошибки.
      }
      if (alive) setReady(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!ready) return null;
  return <>{children}</>;
}
