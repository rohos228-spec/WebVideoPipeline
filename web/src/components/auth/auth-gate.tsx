"use client";

/**
 * Client auth gate for PRIVATE routes only (studio "/", /profile, /billing).
 * Public pages (/home, /login, /register, /legal/*, …) are NOT wrapped, so their static HTML
 * paints immediately. Here: ask the public /api/auth/status; without a session redirect to
 * /login?next=…; with auth off render at once. The last "auth not required" answer is cached in
 * sessionStorage so in-app navigation does not flash the loader.
 */
import { useEffect, useState } from "react";

const CACHE_KEY = "vp-auth-open";

type Status = { auth_required?: boolean; user?: unknown } | null;

function hasLocalToken(): boolean {
  try {
    return Boolean(window.localStorage.getItem("vp_token"));
  } catch {
    return false;
  }
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  // "checking" on the server and on the first client render (static export: no hydration mismatch)
  const [state, setState] = useState<"checking" | "ok">("checking");

  useEffect(() => {
    let cancelled = false;
    try {
      if (window.sessionStorage.getItem(CACHE_KEY) === "1") setState("ok");
    } catch {
      /* ignore */
    }
    fetch("/api/auth/status", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((st: Status) => {
        if (cancelled) return;
        const open = !st || !st.auth_required;
        try {
          window.sessionStorage.setItem(CACHE_KEY, open ? "1" : "0");
        } catch {
          /* ignore */
        }
        if (!open && !st?.user && !hasLocalToken()) {
          const next = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.replace(`/login?next=${next}`);
          return;
        }
        setState("ok");
      })
      .catch(() => {
        if (!cancelled) setState("ok");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "ok") return <>{children}</>;
  return (
    <div
      data-testid="auth-gate-loading"
      className="flex min-h-screen w-full items-center justify-center bg-[#07080b]"
      aria-busy="true"
    >
      <div className="flex flex-col items-center gap-3 text-white/60">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-white/15 border-t-[#22d3ee]" />
        <span className="font-mono text-xs tracking-widest">VIA</span>
      </div>
    </div>
  );
}
