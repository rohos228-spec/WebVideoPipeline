"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, ExternalLink, ListChecks, MessageSquare, PanelRightClose, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { useActionTitles } from "@/lib/action-layer";
import type { ChatNotice } from "@/lib/chat-notices";
import { tasksTabLabel } from "@/lib/orchestratorCard";
import { currentTask, isTerminalBlock, openApprovals as openApprovalList } from "@/lib/orchestratorSession";
import { cn } from "@/lib/utils";
import { ChatNoticeCard } from "./chat-notice-card";
import {
  AgentsRow,
  ApprovalJump,
  ChatFeedItem,
  Composer,
  ConnBanners,
  KillButton,
  KillDialog,
  statusBadgeVariant,
  TaskList,
  TaskStatusBadge,
  TranscriptCards,
} from "./orchestrator-workspace";
import { useChatNotices } from "./use-chat-notices";
import { useOrchestratorChat } from "./use-orchestrator-chat";

export type ChatScope = "project" | "factory";

/** Событие для панели: сколько карточек подтверждения ждёт решения в открытом чате. */
export const ORCH_CARD_WAITING = "studio-orch-card-waiting";

const STRIP_DOT: Record<string, string> = {
  success: "bg-emerald-400",
  destructive: "bg-red-400",
  warning: "bg-amber-400",
  info: "animate-pulse bg-sky-400",
};

export function studioChatSession(projectId: number | null): string {
  return projectId != null ? `studio-p${projectId}` : "studio-factory";
}

export function StudioOrchestratorChat({ projectId, scope }: { projectId: number | null; scope: ChatScope }) {
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(true), 0);
    return () => window.clearTimeout(timer);
  }, []);
  const effective = scope === "project" ? projectId : null;
  const session = studioChatSession(effective);
  if (!settled) return null;
  return <ChatPane key={session} session={session} projectId={effective} wantedProject={scope === "project"} />;
}

function ChatPane({
  session,
  projectId,
  wantedProject,
}: {
  session: string;
  projectId: number | null;
  wantedProject: boolean;
}) {
  const chat = useOrchestratorChat({ session, projectId });
  const notices = useChatNotices(projectId);
  const titles = useActionTitles();
  const [draft, setDraft] = useState("");
  const [taskMode, setTaskMode] = useState(false);
  const [killOpen, setKillOpen] = useState(false);
  const [mobileView, setMobileView] = useState<"chat" | "tasks">("chat");
  const [tasksOpen, setTasksOpen] = usePersistedState("vp-orch-tasks-open", false);
  const [anchors, setAnchors] = useState<Record<number, number>>({});
  const messageCount = chat.state.messages.length;
  const messageCountRef = useRef(messageCount);
  messageCountRef.current = messageCount;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const approvalsSeen = useRef(0);

  useEffect(() => {
    setAnchors((cur) => {
      let changed = false;
      const next = { ...cur };
      for (const n of notices.notices) {
        if (next[n.id] == null) {
          next[n.id] = messageCountRef.current;
          changed = true;
        }
      }
      return changed ? next : cur;
    });
  }, [notices.notices]);

  const lastText = chat.state.messages.at(-1)?.text.length ?? 0;
  const openApprovals = chat.state.approvals.filter((a) => a.resolved == null).length;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const grew = openApprovals > approvalsSeen.current;
    approvalsSeen.current = openApprovals;
    const cards = el.querySelectorAll<HTMLElement>("[data-testid='orch-approval']");
    const card = cards.length > 0 ? cards[cards.length - 1] : null;
    if (!card) {
      el.scrollTop = el.scrollHeight;
      return;
    }
    const top = card.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 8;
    const bottom = el.scrollHeight - el.clientHeight;
    el.scrollTop = grew ? top : Math.min(bottom, top);
  }, [messageCount, lastText, notices.notices.length, openApprovals]);

  const task = currentTask(chat.state.tasks);
  const tasksLabel = tasksTabLabel(chat.state.tasks);
  const waitingCards = openApprovalList(chat.state.approvals).filter((item) => !isTerminalBlock(item.reason)).length;
  useEffect(() => {
    // Карточка подтверждения велика для обычной высоты панели: панель сама становится большой.
    window.dispatchEvent(new CustomEvent(ORCH_CARD_WAITING, { detail: { waiting: waitingCards } }));
  }, [waitingCards]);

  const send = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    setMobileView("chat");
    void chat.sendMessage(text, taskMode ? "task" : undefined);
  };

  const noticesAt = (index: number, last: boolean): ChatNotice[] =>
    notices.notices.filter((n) => {
      const a = anchors[n.id] ?? 0;
      return last ? a >= index : a === index;
    });

  const renderNotice = (n: ChatNotice) => (
    <ChatNoticeCard key={`notice-${n.id}`} notice={n} showProject={projectId == null} onRead={notices.markRead} />
  );

  const fullPageHref = projectId != null ? `/orchestrator?project=${projectId}&session=${session}` : `/orchestrator?session=${session}`;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="studio-orch-chat" data-session={session}>
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/[0.06] px-3 py-1.5 text-[11px]">
        <span className="text-white/55" data-testid="studio-orch-scope">
          {projectId != null
            ? `Проект #${projectId}`
            : wantedProject
              ? "Проект не выбран — чат по всей фабрике"
              : "Вся фабрика"}
        </span>
        <TaskStatusBadge state={chat.state} />
        <ApprovalJump chat={chat} onJump={() => setMobileView("chat")} />
        {notices.unread > 0 ? (
          <span className="flex items-center gap-1 text-amber-200" data-testid="studio-orch-unread">
            <Bell className="h-3 w-3" />
            {notices.unread}
          </span>
        ) : null}
        <div className="flex-1" />
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-[11px] text-white/60 md:hidden"
          onClick={() => setMobileView((v) => (v === "chat" ? "tasks" : "chat"))}
          data-testid="studio-orch-mobile-toggle"
        >
          {mobileView === "chat" ? <ListChecks className="h-3.5 w-3.5" /> : <MessageSquare className="h-3.5 w-3.5" />}
          {mobileView === "chat" ? "Ход задачи" : "Чат"}
        </Button>
        <a
          href={fullPageHref}
          className="hidden items-center gap-1 rounded px-2 py-1 text-white/50 hover:bg-white/[0.05] hover:text-white sm:flex"
          title="Открыть чат на всю страницу"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          На всю страницу
        </a>
        <KillButton chat={chat} compact onClick={() => setKillOpen(true)} />
      </div>

      <ConnBanners chat={chat} />

      {chat.needsToken ? (
        <AccessNotice chat={chat} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <section
            className={cn(
              "min-h-0 min-w-0 flex-1 flex-col md:flex",
              mobileView === "chat" ? "flex" : "hidden",
            )}
          >
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-2 sm:px-4" data-testid="orch-transcript">
              {notices.loadError ? <p className="mb-2 text-[11px] text-white/40">{notices.loadError}</p> : null}
              {messageCount === 0 && notices.notices.length === 0 ? (
                <p className="text-xs leading-relaxed text-white/40">
                  {projectId != null
                    ? "Спросите про проект обычными словами: что с проектом, почему упал шаг, сколько потрачено. Можно попросить запустить шаг, сменить модель ноды или поправить промт — платное и удаляющее сначала придёт карточкой на подтверждение."
                    : "Чат по всей фабрике: спросите, какие проекты идут, где ошибки, сколько потрачено, или попросите запустить пачку проектов."}
                </p>
              ) : null}
              {chat.state.messages.map((message, index) => (
                <div key={message.id}>
                  {noticesAt(index, false).map(renderNotice)}
                  <ChatFeedItem message={message} chat={chat} titles={titles} />
                </div>
              ))}
              {noticesAt(messageCount, true).map(renderNotice)}
              <TranscriptCards chat={chat} titles={titles} />
            </div>
            <Composer
              chat={chat}
              draft={draft}
              setDraft={setDraft}
              onSend={send}
              taskMode={taskMode}
              setTaskMode={setTaskMode}
              compact
              placeholder={
                projectId != null
                  ? "Например: что с проектом? почему упал шаг? запусти видео"
                  : "Например: какие проекты сейчас идут? запусти три проекта по образцу"
              }
            />
          </section>
          <aside
            className={cn(
              "min-h-0 w-full shrink-0 flex-col border-l border-white/[0.06] bg-black/30 md:flex",
              mobileView === "tasks" ? "flex" : "hidden",
              tasksOpen ? "md:w-[260px]" : "md:w-9",
            )}
            data-testid="orch-task-panel"
            data-open={tasksOpen ? "true" : "false"}
          >
            {!tasksOpen ? (
              <button
                type="button"
                data-testid="orch-tasks-expand"
                className="hidden h-full w-full flex-col items-center gap-2 py-2 text-[11px] text-white/60 hover:bg-white/[0.04] hover:text-white md:flex"
                onClick={() => setTasksOpen(true)}
                title="Показать задачи и агентов"
                aria-expanded={false}
              >
                <PanelRightOpen className="h-4 w-4 shrink-0" />
                <span className="rotate-180 whitespace-nowrap [writing-mode:vertical-rl]">{tasksLabel}</span>
                {task ? (
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      STRIP_DOT[statusBadgeVariant(task.status)] ?? "bg-white/25",
                    )}
                  />
                ) : null}
              </button>
            ) : null}
            <div className={cn("min-h-0 flex-1 flex-col", tasksOpen ? "flex" : "flex md:hidden")}>
              <div className="flex shrink-0 items-center gap-1 border-b border-white/[0.06] px-2.5 py-1 text-[11px]">
                <span className="font-medium text-white/60">{tasksLabel}</span>
                <div className="flex-1" />
                <button
                  type="button"
                  data-testid="orch-tasks-collapse"
                  className="hidden h-6 w-6 items-center justify-center rounded text-white/50 hover:bg-white/[0.06] hover:text-white md:flex"
                  onClick={() => setTasksOpen(false)}
                  title="Свернуть задачи"
                  aria-expanded
                >
                  <PanelRightClose className="h-3.5 w-3.5" />
                </button>
              </div>
              <AgentsRow agents={chat.state.agents} />
              <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                <TaskList tasks={chat.state.tasks} titles={titles} heading={false} />
              </div>
            </div>
          </aside>
        </div>
      )}

      <KillDialog open={killOpen} onOpenChange={setKillOpen} onConfirm={() => void chat.kill()} />
    </div>
  );
}

function AccessNotice({ chat }: { chat: ReturnType<typeof useOrchestratorChat> }) {
  const [showToken, setShowToken] = useState(false);
  const [value, setValue] = useState("");
  return (
    <div className="flex flex-1 items-start justify-center overflow-y-auto p-4" data-testid="studio-orch-access">
      <div className="w-full max-w-md space-y-2 rounded-xl border border-white/10 bg-card/90 p-4 text-[12px]">
        <div className="font-semibold text-white/90">Чат оркестратора пока не пускает</div>
        <p className="text-white/70" data-testid="orch-token-error">
          {chat.authError || "Сервер не принял вход студии."}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={chat.retryAccess}>
            Повторить
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setShowToken((v) => !v)}>
            У меня есть токен оркестратора
          </Button>
        </div>
        {showToken ? (
          <div className="flex gap-2">
            <Input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="токен оркестратора"
              autoComplete="off"
              className="h-8 text-xs"
              data-testid="orch-token-input"
            />
            <Button
              type="button"
              size="sm"
              disabled={!value.trim()}
              data-testid="orch-token-submit"
              onClick={() => void chat.submitToken(value)}
            >
              Войти
            </Button>
          </div>
        ) : null}
        <p className="text-[11px] text-white/45">
          Пока чат недоступен, работает старый чат — вкладка «Старый чат (архив)» в этой же панели.
        </p>
      </div>
    </div>
  );
}
