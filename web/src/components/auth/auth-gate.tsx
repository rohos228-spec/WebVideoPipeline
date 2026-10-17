"use client";

/**
 * Client auth gate (VIA_AUTH_ENABLED=1): before mounting protected pages ask the public
 * /api/auth/status; without a session redirect to /login instead of firing dozens of
 * protected API calls that would all answer 401. With auth off it renders immediately.
 */
import { useEffect, useState } from "react";

const PUBLIC_PATHS = ["/home", "/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/legal", "/pricing"];

function isPublic(path: string): boolean {
  const p = path.replace(/\/+$/, "") || "/";
  return PUBLIC_PATHS.some((x) => p === x || p.startsWith(x + "/"));
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  // false on the server and first client render (static export: avoids hydration mismatch)
  const [ok, setOk] = useState(false);

  useEffect(() => {
    if (isPublic(window.location.pathname)) {
      setOk(true);
      return;
    }
    let cancelled = false;
    fetch("/api/auth/status", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((st: { auth_required?: boolean; user?: unknown } | null) => {
        if (cancelled) return;
        let hasToken = false;
        try {
          hasToken = Boolean(window.localStorage.getItem("vp_token"));
        } catch {
          /* ignore */
        }
        if (st && st.auth_required && !st.user && !hasToken) {
          const next = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.replace(`/login?next=${next}`);
          return;
        }
        setOk(true);
      })
      .catch(() => {
        if (!cancelled) setOk(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return ok ? <>{children}</> : null;
}
