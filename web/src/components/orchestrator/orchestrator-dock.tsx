"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, ChevronUp, Maximize2, MessageSquare, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { NARROW_QUERY, useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";
import { RegistrarSections } from "./registrar-sections";
import { ORCH_CARD_WAITING, StudioOrchestratorChat, type ChatScope } from "./studio-orchestrator-chat";

export type DockTab = "chat" | "registrar";

export const OPEN_ORCHESTRATOR_CHAT = "studio-open-orchestrator-chat";

const TABS: { id: DockTab; label: string; short: string }[] = [
  { id: "chat", label: "Чат", short: "Чат" },
  { id: "registrar", label: "Регистратор", short: "Регистр." },
];

export function OrchestratorDock({ projectId }: { projectId: number | null }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  // По умолчанию док свёрнут в полоску «Оркестратор»: рядом есть окно «ИИ-агент».
  // Ключ новый, чтобы старое «открыто» из прежней сборки не раскрывало док само.
  const [openStored, setOpenStored] = usePersistedState("vp-orch-dock-open-v2", false);
  const [touched, setTouched] = useState(false);
  const open = openStored && !(narrow && !touched);
  const [expandedStored, setExpanded] = usePersistedState("vp-orch-dock-expanded", false);
  // Пришла карточка подтверждения: панель на время становится большой, чтобы карточка
  // была видна целиком. Свернул человек — до следующей новой карточки не раскрываем.
  const [cardBig, setCardBig] = useState(false);
  const cardsSeen = useRef(0);
  const [waiting, setWaiting] = useState(0);
  const expanded = expandedStored || cardBig;
  const [tab, setTab] = usePersistedState<DockTab>("vp-orch-dock-tab", "chat");
  const [scope, setScope] = usePersistedState<ChatScope>("vp-orch-dock-scope", "project");

  const setOpen = useCallback(
    (v: boolean) => {
      setTouched(true);
      setOpenStored(v);
      // Одно окно чата на экране: открыли док — плавающий «ИИ-агент» закрывается.
      if (v) window.dispatchEvent(new Event("studio-close-orchestrator"));
    },
    [setOpenStored],
  );

  // Открыли «ИИ-агент» (кнопка в левой панели) — док сворачивается, чтобы окна не перекрывались.
  useEffect(() => {
    const collapse = () => setOpenStored(false);
    window.addEventListener("studio-open-orchestrator", collapse);
    window.addEventListener("studio-toggle-orchestrator", collapse);
    return () => {
      window.removeEventListener("studio-open-orchestrator", collapse);
      window.removeEventListener("studio-toggle-orchestrator", collapse);
    };
  }, [setOpenStored]);

  useEffect(() => {
    const onOpen = (ev: Event) => {
      const detail = (ev as CustomEvent<{ tab?: DockTab; scope?: ChatScope }>).detail;
      setTab(detail?.tab ?? "chat");
      if (detail?.scope) setScope(detail.scope);
      setOpen(true);
    };
    window.addEventListener(OPEN_ORCHESTRATOR_CHAT, onOpen);
    return () => window.removeEventListener(OPEN_ORCHESTRATOR_CHAT, onOpen);
  }, [setOpen, setScope, setTab]);

  useEffect(() => {
    const onCards = (ev: Event) => {
      const waiting = Number((ev as CustomEvent<{ waiting?: number }>).detail?.waiting ?? 0);
      if (waiting > cardsSeen.current) setCardBig(true);
      if (waiting === 0) setCardBig(false);
      cardsSeen.current = waiting;
      setWaiting(waiting);
    };
    window.addEventListener(ORCH_CARD_WAITING, onCards);
    return () => window.removeEventListener(ORCH_CARD_WAITING, onCards);
  }, []);

  const activeTab: DockTab = TABS.some((t) => t.id === tab) ? tab : "chat";

  return (
    <>
    {narrow && !open ? (
      <button
        type="button"
        data-testid="orch-dock-fab"
        className="fixed bottom-4 right-4 z-50 flex h-12 items-center gap-2 rounded-full border border-primary/40 bg-[#0c0c0c] px-4 text-[13px] font-semibold text-white shadow-2xl"
        onClick={() => {
          setTab("chat");
          setOpen(true);
        }}
        title="Открыть чат оркестратора"
      >
        <MessageSquare className="h-4 w-4 text-primary" />
        Оркестратор
      </button>
    ) : null}
    <div
      data-testid="orch-dock"
      data-open={open ? "true" : "false"}
      data-size={!open ? "closed" : narrow ? "full" : expanded ? "big" : "normal"}
      className={cn(
        "flex flex-col border-t border-white/[0.1] bg-[#0c0c0c]/97 shadow-2xl backdrop-blur transition-all duration-200",
        open && narrow ? "fixed inset-0 z-50 bg-[#0c0c0c]" : "absolute inset-x-0 bottom-0 z-40",
        // На узком экране свёрнутая полоска не видна (её съедает список проектов): вместо неё кнопка «Чат».
        narrow && !open ? "hidden" : "",
        !open
          ? "h-10"
          : narrow
            ? ""
            : expanded
              ? "top-2 rounded-t-xl"
              : "h-[62vh] min-h-[420px]",
      )}
    >
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-white/[0.08] px-2">
        <button
          type="button"
          className="flex min-w-0 shrink-0 items-center gap-1.5 px-1 text-[12px] font-semibold text-white/85"
          onClick={() => setOpen(!open)}
          title={open ? "Свернуть панель" : "Открыть панель"}
        >
          <Bot className="h-4 w-4 shrink-0 text-primary" />
          <span className="hidden sm:inline">Оркестратор</span>
        </button>
        <div role="tablist" aria-label="Разделы оркестратора" className="flex min-w-0 items-center gap-0.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={activeTab === t.id}
              data-testid={`orch-dock-tab-${t.id}`}
              className={cn(
                "rounded-md px-2 py-1 text-[11px] transition-colors",
                activeTab === t.id ? "bg-white/[0.1] text-white" : "text-white/50 hover:bg-white/[0.05] hover:text-white/80",
              )}
              onClick={() => {
                setTab(t.id);
                if (!open) setOpen(true);
              }}
            >
              <span className="hidden sm:inline">{t.label}</span>
              <span className="sm:hidden">{t.short}</span>
            </button>
          ))}
        </div>
        {!open && waiting > 0 ? (
          <button
            type="button"
            data-testid="orch-dock-waiting"
            className="ml-1 shrink-0 rounded-md border border-amber-400/50 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-100 hover:bg-amber-500/20"
            onClick={() => {
              setTab("chat");
              setOpen(true);
            }}
          >
            {waiting > 1 ? `Ждёт подтверждения: ${waiting}` : "Ждёт подтверждения"}
          </button>
        ) : null}
        <div className="flex-1" />
        {activeTab === "chat" ? (
          <div className="flex items-center rounded-md border border-white/10 p-0.5" role="group" aria-label="Чем занимается чат">
            <button
              type="button"
              data-testid="orch-scope-project"
              aria-pressed={scope === "project"}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] sm:text-[11px]",
                scope === "project" ? "bg-primary/25 text-white" : "text-white/50 hover:text-white/80",
              )}
              onClick={() => setScope("project")}
              title="Чат работает с выбранным проектом студии"
            >
              Проект
            </button>
            <button
              type="button"
              data-testid="orch-scope-factory"
              aria-pressed={scope === "factory"}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] sm:text-[11px]",
                scope === "factory" ? "bg-primary/25 text-white" : "text-white/50 hover:text-white/80",
              )}
              onClick={() => setScope("factory")}
              title="Чат по всем проектам фабрики"
            >
              Вся фабрика
            </button>
          </div>
        ) : null}
        {!narrow ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 w-7 shrink-0 p-0 text-white/50"
            onClick={() => {
              if (!open) {
                setOpen(true);
                setExpanded(true);
                return;
              }
              if (expanded) setCardBig(false);
              setExpanded(!expanded);
            }}
            title={expanded && open ? "Меньше окно" : "Большое окно"}
          >
            {expanded && open ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50"
          data-testid="orch-dock-toggle"
          onClick={() => setOpen(!open)}
          title={open ? "Свернуть панель" : "Открыть панель"}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </Button>
      </div>

      <div className={cn("min-h-0 flex-1", open ? "flex flex-col" : "hidden")}>
        <div className={cn("min-h-0 flex-1 flex-col", activeTab === "chat" ? "flex" : "hidden")}>
          <StudioOrchestratorChat projectId={projectId} scope={scope} />
        </div>
        {activeTab === "registrar" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2" data-testid="orch-dock-registrar">
            <p className="mb-2 text-[11px] text-white/40">
              Служебные блоки оркестратора: автономия при сбое, резервы, действия по плану, контроль изменений. Откройте нужный.
            </p>
            <RegistrarSections projectId={projectId} />
          </div>
        ) : null}
      </div>
    </div>
    </>
  );
}
