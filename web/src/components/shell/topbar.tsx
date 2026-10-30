"use client";

import { useState, useEffect, createContext, useContext } from "react";
import { Film, Bot, User, Settings, FolderOpen, HardDrive, CreditCard, Wallet, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { FramesGrid } from "@/components/frames/frames-grid";
import { StudioVersionBadge } from "@/components/shell/studio-version-badge";
import { TextLlmPicker } from "@/components/shell/text-llm-picker";
import { BugReportButton } from "@/components/shell/bug-report-button";
import { UserPrefsApplier } from "@/components/panels/top/user-panel";
import { StoragePanel } from "@/components/panels/left-top/storage-panel";
import { PaymentsPanel } from "@/components/panels/left-bottom/payments-panel";
import { PayoutsPanel } from "@/components/panels/left-bottom/payouts-panel";

interface UiState {
  framesProjectId: number | null;
  openFrames: (projectId: number) => void;
  openOutsee: (projectId?: number | null) => void;
}

const UiContext = createContext<UiState | null>(null);

export function useUi(): UiState {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error("useUi must be used within AppShell / Topbar");
  return ctx;
}

export function Topbar({ children }: { children?: React.ReactNode }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [railPanel, setRailPanel] = useState<null | "Хранилище" | "Оплата" | "Выплаты">(null);
  const togglePanel = (k: "Хранилище" | "Оплата" | "Выплаты") => setRailPanel((v) => (v === k ? null : k));
  const [framesOpen, setFramesOpen] = useState(false);
  const [framesProjectId, setFramesProjectId] = useState<number | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [orchestratorOpen, setOrchestratorOpen] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("vp-orchestrator-open");
      if (saved !== null) {
        setOrchestratorOpen(JSON.parse(saved));
      }
    } catch {}

    const onStateChange = (e: Event) => {
      const detail = (e as CustomEvent<{ open?: boolean }>).detail;
      if (detail && typeof detail.open === "boolean") {
        setOrchestratorOpen(detail.open);
      }
    };
    window.addEventListener("studio-orchestrator-state-changed", onStateChange);
    return () => {
      window.removeEventListener("studio-orchestrator-state-changed", onStateChange);
    };
  }, []);

  const handleToggleOrchestrator = () => {
    window.dispatchEvent(new CustomEvent("studio-toggle-orchestrator"));
  };

  useEffect(() => {
    let alive = true;
    fetch("/api/admin/access")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { is_admin?: boolean } | null) => {
        if (alive && d?.is_admin) setIsAdmin(true);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const openFrames = (id: number) => {
    setFramesProjectId(id);
    setFramesOpen(true);
  };

  const openOutsee = (projectId?: number | null) => {
    window.dispatchEvent(
      new CustomEvent("studio-open-outsee", {
        detail: { projectId: projectId ?? null },
      }),
    );
  };

  return (
    <UiContext.Provider value={{ framesProjectId, openFrames, openOutsee }}>
      <div className="flex h-full min-h-0 w-full flex-1 flex-row">
      <nav className="flex w-[64px] shrink-0 flex-col items-center gap-1 border-r border-white/[0.06] bg-[#0e0e10] py-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <a href="/home" title="Главная"><img src="/via-logo.png" alt="VIA" className="mb-3 w-[46px] select-none" draggable={false} /></a>
        <RailBtn icon={<Film className="h-[18px] w-[18px]" />} label="Генерация" title="Полный интерфейс генерации" onClick={() => openOutsee()} />
        <RailBtn icon={<FolderOpen className="h-[18px] w-[18px]" />} label="Проекты" title="Показать / скрыть панель проектов" onClick={() => window.dispatchEvent(new CustomEvent("studio-toggle-projects-sidebar"))} />
        <OrchestratorRailBtn active={orchestratorOpen} onClick={handleToggleOrchestrator} />
        <RailBtn icon={<Bot className="h-[18px] w-[18px]" />} label="Чат" title="Чат GPT" onClick={() => window.dispatchEvent(new CustomEvent("studio-open-gpt"))} />
        <BugReportButton />
        {isAdmin ? (
          <RailBtn
            icon={<HardDrive className="h-[18px] w-[18px]" />}
            label="Хранилище"
            active={railPanel === "Хранилище"}
            onClick={() => togglePanel("Хранилище")}
          />
        ) : null}
        {isAdmin ? <AdminRailLink /> : null}
        <div className="flex-1" />
        <RailBtn icon={<CreditCard className="h-[18px] w-[18px]" />} label="Оплата" active={railPanel === "Оплата"} onClick={() => togglePanel("Оплата")} />
        <RailBtn icon={<Wallet className="h-[18px] w-[18px]" />} label="Выплаты" active={railPanel === "Выплаты"} onClick={() => togglePanel("Выплаты")} />
        {railPanel === "Хранилище" ? <StoragePanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel === "Оплата" ? <PaymentsPanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel === "Выплаты" ? <PayoutsPanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel && railPanel !== "Хранилище" && railPanel !== "Оплата" && railPanel !== "Выплаты" ? (
          <div data-rail-panel={railPanel} className="fixed left-[72px] top-3 z-50 w-[320px] rounded-[14px] border border-white/10 bg-[#0a0a0a] p-4 shadow-2xl" style={{ fontFamily: "Rubik, sans-serif" }}>
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[15px] font-medium text-white">{railPanel}</span>
              <button type="button" className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-white" onClick={() => setRailPanel(null)} aria-label="Закрыть">×</button>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-black/60 px-3 py-6 text-center text-[13px] text-zinc-400">скоро</div>
          </div>
        ) : null}
        <button type="button" onClick={() => { window.location.href = "/profile"; }} className="flex h-10 w-10 items-center justify-center text-zinc-400 hover:bg-white/[0.05] hover:text-white" title="Пользователь" aria-label="Пользователь">
          <User className="h-5 w-5" strokeWidth={1.6} />
        </button>
        <div className="relative">
          <button type="button" onClick={() => setSettingsOpen((v) => !v)} className={"flex h-10 w-10 items-center justify-center hover:bg-white/[0.05] hover:text-white " + (settingsOpen ? "text-white" : "text-zinc-400")} title="Настройки" aria-label="Настройки">
            <Settings className="h-5 w-5" strokeWidth={1.6} />
          </button>
          {settingsOpen ? (
            <div className="absolute bottom-0 left-full z-50 ml-2 w-[300px] border border-white/10 bg-[#141417] p-3 shadow-2xl">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[13px] font-medium uppercase tracking-[0.12em] text-zinc-300">Настройки</span>
                <button type="button" className="text-zinc-500 hover:text-white" onClick={() => setSettingsOpen(false)} aria-label="Закрыть">×</button>
              </div>
              <div className="mb-3 text-[13px] text-zinc-500">Текстовая модель</div>
              <TextLlmPicker />
              <div className="mt-3 flex items-center gap-2 text-[13px] text-zinc-500">Версия <StudioVersionBadge /></div>
            </div>
          ) : null}
        </div>
        <UserPrefsApplier />
      </nav>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <FramesGrid
        projectId={framesProjectId}
        open={framesOpen}
        onOpenChange={setFramesOpen}
      />
      {children != null ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      ) : null}
      </div>
      </div>
    </UiContext.Provider>
  );
}

function RailBtn({ icon, label, title, onClick, active }: { icon: React.ReactNode; label: string; title?: string; onClick: () => void; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={title ?? label} className={"flex w-[62px] flex-col items-center gap-1 py-1.5 text-[11px] tracking-[-0.02em] transition-colors " + (active ? "text-white" : "text-zinc-400") + " hover:bg-white/[0.05] hover:text-white"}>
      {icon}
      <span className="leading-none">{label}</span>
    </button>
  );
}

/** Вход в /admin — только для администратора. */
function AdminRailLink() {
  return (
    <RailBtn
      icon={<ShieldCheck className="h-[18px] w-[18px]" />}
      label="Админка"
      title="Админ-панель: метрики, пользователи, генерации, запросы и начисления"
      onClick={() => {
        window.location.href = "/admin";
      }}
    />
  );
}

/** Кнопка «Оркестратор» в левой панели с эффектом пульсирующих фиолетовых круговых волн каждые 2 сек. */
function OrchestratorRailBtn({
  active,
  onClick,
}: {
  active?: boolean;
  onClick: () => void;
}) {
  const [imgError, setImgError] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      title={active ? "Скрыть Оркестратор" : "Открыть Оркестратор (ИИ-режиссёр проекта)"}
      className={cn(
        "group relative flex w-[62px] flex-col items-center gap-1.5 py-1.5 text-[11px] tracking-[-0.02em] transition-all rounded-md select-none",
        active
          ? "text-purple-300 bg-purple-500/10"
          : "text-zinc-400 hover:text-purple-200 hover:bg-white/[0.05]",
      )}
    >
      {/* Контейнер иконки с эффектом расходящихся фиолетовых волн */}
      <div className="relative flex h-7 w-7 items-center justify-center">
        {/* Круговые фиолетовые волны пульсации раз в 2 сек */}
        <span
          className="absolute inset-0 rounded-full border border-purple-500/60 pointer-events-none animate-orch-wave"
          aria-hidden="true"
        />
        <span
          className="absolute inset-0 rounded-full border border-fuchsia-400/40 pointer-events-none animate-orch-wave-delayed"
          aria-hidden="true"
        />

        {/* Круглый аватар / иконка оркестратора */}
        <div
          className={cn(
            "relative z-10 flex h-[26px] w-[26px] items-center justify-center rounded-full overflow-hidden transition-all duration-200",
            "border border-purple-400/50 bg-gradient-to-br from-[#1d1233] to-[#0c0a17]",
            active
              ? "ring-2 ring-purple-400 ring-offset-1 ring-offset-[#0e0e10] shadow-[0_0_12px_rgba(168,85,247,0.7)]"
              : "shadow-[0_0_8px_rgba(168,85,247,0.35)] group-hover:border-purple-300 group-hover:scale-105",
          )}
        >
          {!imgError ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src="/orchestrator-avatar.jpg"
              alt="Оркестратор"
              className="h-full w-full object-cover rounded-full"
              onError={() => setImgError(true)}
            />
          ) : (
            <Bot className="h-3.5 w-3.5 text-purple-300" />
          )}
        </div>
      </div>

      <span
        className={cn(
          "leading-none transition-colors",
          active ? "text-purple-300 font-medium" : "text-zinc-400 group-hover:text-purple-200",
        )}
      >
        Оркестратор
      </span>
    </button>
  );
}
