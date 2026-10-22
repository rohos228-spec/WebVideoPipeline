"use client";

/**
 * Админ-панель VIA: оболочка страницы /admin.
 * Гейт по правам (adminApi.access), шапка, вертикальное меню по табам,
 * синхронизация активного таба с location.hash.
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Coins,
  CreditCard,
  Gauge,
  Images,
  RefreshCw,
  ScrollText,
  Settings2,
  ShieldAlert,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { adminApi, isUnauthorized } from "@/lib/admin-api";
import { A, Badge, Btn, ErrorBox, FONT, Spinner } from "./admin-kit";
import { AdminOverviewTab } from "./admin-overview";
import { AdminUsersTab } from "./admin-users";
import { AdminGenerationsTab } from "./admin-generations";
import { AdminRequestsTab } from "./admin-requests";
import { AdminPlansTab } from "./admin-plans";
import { AdminSettingsTab } from "./admin-settings";
import { AdminAuditTab } from "./admin-audit";
import { AdminCostsTab } from "./admin-costs";

type TabId = "overview" | "users" | "generations" | "requests" | "costs" | "plans" | "settings" | "audit";

const TABS: { id: TabId; t: string; i: LucideIcon }[] = [
  { id: "overview", t: "Обзор", i: Gauge },
  { id: "users", t: "Пользователи", i: Users },
  { id: "generations", t: "Генерации", i: Images },
  { id: "requests", t: "Запросы", i: Activity },
  { id: "costs", t: "Расходы", i: Coins },
  { id: "plans", t: "Тарифы", i: CreditCard },
  { id: "settings", t: "Настройки", i: Settings2 },
  { id: "audit", t: "Аудит", i: ScrollText },
];

const isTabId = (v: string): v is TabId => TABS.some((t) => t.id === v);

function TabBody({ tab }: { tab: TabId }) {
  switch (tab) {
    case "overview":
      return <AdminOverviewTab />;
    case "users":
      return <AdminUsersTab />;
    case "generations":
      return <AdminGenerationsTab />;
    case "requests":
      return <AdminRequestsTab />;
    case "costs":
      return <AdminCostsTab />;
    case "plans":
      return <AdminPlansTab />;
    case "settings":
      return <AdminSettingsTab />;
    case "audit":
      return <AdminAuditTab />;
    default:
      return null;
  }
}

const linkBtnCls =
  "inline-flex items-center justify-center gap-2 rounded-[10px] bg-white/[0.05] px-3.5 py-2 text-[14px] text-zinc-200 transition-colors hover:bg-white/[0.09]";

const accentLinkCls = "inline-flex items-center justify-center gap-2 rounded-[10px] px-3.5 py-2 text-[14px] transition-opacity hover:opacity-90";

export function AdminPanel() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("overview");
  const [refreshing, setRefreshing] = useState(false);
  const access = useQuery({
    queryKey: ["admin", "access"],
    queryFn: () => adminApi.access(),
    staleTime: 30_000,
    retry: false,
  });

  useEffect(() => {
    const sync = () => {
      const h = window.location.hash.replace("#", "");
      if (isTabId(h)) setTab(h);
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const go = (t: TabId) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      const predicate = (q: { queryKey: readonly unknown[] }) =>
        Array.isArray(q.queryKey) && q.queryKey[0] === "admin";
      await qc.invalidateQueries({ predicate });
    } finally {
      setRefreshing(false);
    }
  };

  /* ── проверка доступа ── */

  if (access.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#141518] text-zinc-200" style={FONT}>
        <div className="flex items-center gap-3 text-[14px] text-zinc-500">
          <Spinner className="h-5 w-5" />
          Проверяем доступ…
        </div>
      </div>
    );
  }

  if (access.isError) {
    const needsLogin = isUnauthorized(access.error);
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#141518] px-5 text-zinc-200" style={FONT}>
        <div className="w-full max-w-[560px]">
          <ErrorBox err={access.error} retry={() => void access.refetch()} />
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {needsLogin ? (
              <a href="/login?next=%2Fadmin" className={accentLinkCls} style={{ background: A + "24", color: A }}>
                Войти
              </a>
            ) : null}
            <a href="/" className={linkBtnCls}>
              <ArrowLeft className="h-4 w-4" />В студию
            </a>
          </div>
        </div>
      </div>
    );
  }

  if (!access.data?.is_admin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#141518] px-5 text-zinc-200" style={FONT}>
        <div className="w-full max-w-[560px] rounded-[18px] border border-white/[0.07] bg-white/[0.025] p-6">
          <div className="flex items-start gap-3">
            <span
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[12px]"
              style={{ background: A + "1f", color: A }}
            >
              <ShieldAlert className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <div className="text-[19px] font-medium text-white">Нужен вход администратора</div>
              <div className="mt-0.5 text-[13px] text-zinc-500">
                Раздел доступен только аккаунтам с ролью admin
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-[10px] bg-black/25 px-3 py-2.5 text-[13px] leading-relaxed text-zinc-400">
            {access.data?.reason ||
              "Текущий аккаунт не имеет прав администратора. Войди под админским аккаунтом — или попроси владельца студии выдать роль admin."}
          </div>

          {access.data?.bootstrap ? (
            <div className="mt-2 text-[12px] text-zinc-600">
              Студия ещё не инициализирована: первый зарегистрированный пользователь становится администратором.
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-2">
            <a href="/login?next=%2Fadmin" className={accentLinkCls} style={{ background: A + "24", color: A }}>
              Войти
            </a>
            <a href="/" className={linkBtnCls}>
              <ArrowLeft className="h-4 w-4" />В студию
            </a>
          </div>
        </div>
      </div>
    );
  }

  const me = access.data.user;

  /* ── панель ── */

  return (
    <div className="min-h-screen bg-[#141518] text-zinc-200" style={FONT}>
      <style>{`@keyframes vpFadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}`}</style>
      <div className="mx-auto flex max-w-[1500px] flex-col gap-4 px-5 py-4">
        <header className="flex flex-wrap items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/via-logo.png" alt="VIA" className="h-7 opacity-90" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[22px] font-medium leading-tight text-white">Админ-панель</h1>
              <Badge tone="ok">{me?.role ?? "admin"}</Badge>
              {me?.plan ? <Badge tone="neutral">{me.plan}</Badge> : null}
            </div>
            <div className="text-[12px] text-zinc-500">
              {me ? `${me.name || me.username} · ${me.email}` : "VIA Studio"}
            </div>
          </div>
          <div className="flex-1" />
          <a href="/" className={linkBtnCls}>
            <ArrowLeft className="h-4 w-4" />В студию
          </a>
          <Btn onClick={() => void refresh()} disabled={refreshing} title="Обновить все данные админки">
            <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            Обновить
          </Btn>
        </header>

        <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
          <nav className="flex flex-row gap-1 overflow-x-auto lg:sticky lg:top-4 lg:flex-col lg:self-start">
            {TABS.map((t) => {
              const Icon = t.i;
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  data-admin-tab={t.id}
                  onClick={() => go(t.id)}
                  className="flex shrink-0 items-center gap-2.5 rounded-[11px] px-3.5 py-2.5 text-left text-[15px] transition-all duration-200"
                  style={active ? { background: A + "1f", color: A } : { color: "#a1a1aa" }}
                >
                  <Icon className="h-4 w-4" />
                  {t.t}
                </button>
              );
            })}
          </nav>

          <div key={tab} className="min-w-0" style={{ animation: "vpFadeUp .3s ease both" }}>
            <TabBody tab={tab} />
          </div>
        </div>
      </div>
    </div>
  );
}
