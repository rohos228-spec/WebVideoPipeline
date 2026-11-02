"use client";

/**
 * Чат супервизора в доке студии (вкладка «Чат» панели «Оркестратор»).
 *
 * Работает на общем хуке useOrchestratorChat и общем состоянии orchestratorSession — тех же,
 * что страница /orchestrator. Своя сессия на проект (`studio-p<id>`) или на всю фабрику.
 * Компактные карточки дока написаны здесь: в окне дока всё по-русски, без кодов и номеров.
 */

import { useEffect, useRef, useState, type MouseEvent } from "react";
import {
  Bell,
  BellRing,
  ExternalLink,
  ListChecks,
  MessageSquare,
  OctagonX,
  PanelRightClose,
  PanelRightOpen,
  Send,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { MEMBER_PRICE_TEXT, useHideMoney } from "@/hooks/use-hide-money";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { useActionTitles } from "@/lib/action-layer";
import type { ChatNotice } from "@/lib/chat-notices";
import { orchTaskModeEnabled } from "@/lib/orchestratorApi";
import { moneyCents, nodeKeyRu, roundMoneyText, tasksTabLabel } from "@/lib/orchestratorCard";
import {
  actionLabel,
  AGENT_RU,
  agentStatusRu,
  humanDetail,
  humanErrorText,
  humanTaskFailure,
  isHumanText,
  MODE_RU,
  riskRu,
  RISK_TONE,
  validatorRu,
} from "@/lib/orchestratorHuman";
import {
  approvalButtons,
  approvalChangeId,
  groupPlanSteps,
  isNodeResultAction,
  isTerminalBlock,
  PAID_DECISION_LABEL,
  paidDecisionsOf,
  partialDecisionsOf,
  resumeButtonVisible,
  TASK_STATUS_LABEL,
  type AgentView,
  type ApprovalView,
  type ChatMessage,
  type PlanStepView,
  type StepView,
  type TaskView,
} from "@/lib/orchestratorSession";
import { cn } from "@/lib/utils";
import { ActionResultCard } from "./action-result-card";
import { ChatMarkdown } from "./chat-markdown";
import { ChatNoticeCard } from "./chat-notice-card";
import { killBannerText, statusBadgeVariant } from "./orchestrator-workspace";
import { useChatNotices } from "./use-chat-notices";
import { useOrchestratorChat } from "./use-orchestrator-chat";

export type ChatScope = "project" | "factory";

type Chat = ReturnType<typeof useOrchestratorChat>;

/** Событие для панели: сколько карточек подтверждения ждёт решения в открытом чате. */
export const ORCH_CARD_WAITING = "studio-orch-card-waiting";

const STRIP_DOT: Record<string, string> = {
  success: "bg-emerald-400",
  destructive: "bg-red-400",
  warning: "bg-amber-400",
  info: "animate-pulse bg-sky-400",
};

const TERMINAL_TASK = new Set(["done", "failed", "cancelled"]);

/** Тексты карточек дока: коротко и без служебных слов. */
const DOCK_BLOCK_COPY: Record<string, { title: string; body: string }> = {
  budget_exhausted: {
    title: "Бюджет исчерпан",
    body: "Лимит прогона выбран. Новые платные шаги не начнутся, пока вы не решите.",
  },
  critic_escalation: {
    title: "Критик не одобрил план после двух правок",
    body: "План сам не продолжится. Подтвердите его как есть или отклоните.",
  },
  revise_limit: {
    title: "Критик не одобрил план после двух правок",
    body: "План сам не продолжится. Подтвердите его как есть или отклоните.",
  },
  missing_project_id: {
    title: "Не выбран проект",
    body: "В плане есть платный или изменяющий шаг, а проект не указан. Укажите номер проекта.",
  },
  risk: {
    title: "Шаг с риском: нужно подтверждение",
    body: "Шаг не начнётся, пока вы его не подтвердите.",
  },
  deletion_blocked: {
    title: "Удаление запрещено",
    body: "Шаг удаляет кадр или сцену. Одобрить нельзя, задача остановлена.",
  },
  deletion_forbidden: {
    title: "Удаление кадра запрещено",
    body: "Удалять кадры этим действием нельзя. Задача остановлена.",
  },
  missing_run_id: {
    title: "Нет прогона проекта",
    body: "Платный шаг нельзя начать, пока у проекта нет прогона. Задача остановлена.",
  },
  empty_plan: {
    title: "Подходящих действий не нашлось",
    body: "План получился пустым: ничего не сделано. Уточните, что сделать и с каким проектом.",
  },
  partial_write: {
    title: "Шаг записал изменения частично",
    body: "Отката нет. Принять — закрыть шаг и идти дальше. Отказать — остановить задачу.",
  },
  paid_outcome_unknown: {
    title: "Неизвестно, выполнился ли платный шаг",
    body: "Проверьте результат шага и выберите, что делать дальше. Отката нет.",
  },
  not_in_whitelist: {
    title: "Такое действие не разрешено",
    body: "Действия нет в списке разрешённых. Одобрить и выполнить его нельзя.",
  },
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

function openApprovalsOf(approvals: ApprovalView[]): ApprovalView[] {
  return approvals.filter((item) => item.resolved == null);
}

/** Задача, о которой говорит шапка: последняя незавершённая, иначе последняя. */
function currentTaskOf(tasks: TaskView[]): TaskView | null {
  for (let i = tasks.length - 1; i >= 0; i -= 1) {
    if (!TERMINAL_TASK.has(tasks[i].status)) return tasks[i];
  }
  return tasks.at(-1) ?? null;
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
  const openCount = openApprovalsOf(chat.state.approvals).length;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const grew = openCount > approvalsSeen.current;
    approvalsSeen.current = openCount;
    const cards = el.querySelectorAll<HTMLElement>("[data-testid='orch-approval']");
    const card = cards.length > 0 ? cards[cards.length - 1] : null;
    if (!card) {
      el.scrollTop = el.scrollHeight;
      return;
    }
    const top = card.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 8;
    const bottom = el.scrollHeight - el.clientHeight;
    el.scrollTop = grew ? top : Math.min(bottom, top);
  }, [messageCount, lastText, notices.notices.length, openCount]);

  const task = currentTaskOf(chat.state.tasks);
  const tasksLabel = tasksTabLabel(chat.state.tasks);
  const waitingCards = openApprovalsOf(chat.state.approvals).filter((item) => !isTerminalBlock(item.reason)).length;
  useEffect(() => {
    // Карточка подтверждения велика для обычной высоты панели: панель сама становится большой.
    window.dispatchEvent(new CustomEvent(ORCH_CARD_WAITING, { detail: { waiting: waitingCards } }));
  }, [waitingCards]);

  const send = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    setMobileView("chat");
    void chat.sendMessage(text, taskMode ? "task" : undefined).then((sent) => {
      // Не отправилось — текст человека не теряется: возвращаем, если поле ещё пустое.
      if (!sent) setDraft((current) => current || text);
    });
  };

  const noticesAt = (index: number, last: boolean): ChatNotice[] =>
    notices.notices.filter((n) => {
      const a = anchors[n.id] ?? 0;
      return last ? a >= index : a === index;
    });

  const renderNotice = (n: ChatNotice) => (
    <ChatNoticeCard key={`notice-${n.id}`} notice={n} showProject={projectId == null} onRead={notices.markRead} />
  );

  const fullPageHref =
    projectId != null ? `/orchestrator?project=${projectId}&session=${session}` : `/orchestrator?session=${session}`;

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
        <TaskStatusBadge chat={chat} />
        <ApprovalJump chat={chat} onJump={() => setMobileView("chat")} />
        {notices.unread > 0 ? (
          <span className="flex items-center gap-1 text-amber-200" data-testid="studio-orch-unread" title="Непрочитанные уведомления">
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
        <Button
          type="button"
          variant="destructive"
          size="sm"
          className="h-7 gap-1.5 px-2 text-xs font-semibold"
          data-testid="orch-kill"
          disabled={chat.needsToken || chat.state.killNotice?.phase === "stopping"}
          onClick={() => setKillOpen(true)}
          title="Остановить задачу этого чата. Платный шаг остановится на границе. Аварийный стоп всей студии — во вкладке «Регистратор»."
        >
          <OctagonX className="h-3.5 w-3.5" />
          Стоп задачи
        </Button>
      </div>

      <ConnBanners chat={chat} task={task} />

      {chat.needsToken ? (
        <div className="flex flex-1 items-center justify-center p-4 text-[12px] text-white/50" data-testid="studio-orch-access">
          Подключаю чат…
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <section
            className={cn("min-h-0 min-w-0 flex-1 flex-col md:flex", mobileView === "chat" ? "flex" : "hidden")}
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
                  <ChatLine message={message} />
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
                    className={cn("h-2 w-2 shrink-0 rounded-full", STRIP_DOT[statusBadgeVariant(task.status)] ?? "bg-white/25")}
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
                <TaskList tasks={chat.state.tasks} titles={titles} />
              </div>
            </div>
          </aside>
        </div>
      )}

      <KillDialog open={killOpen} onOpenChange={setKillOpen} onConfirm={() => void chat.kill()} />
    </div>
  );
}

function TaskStatusBadge({ chat }: { chat: Chat }) {
  const last = chat.state.messages.at(-1);
  const task = currentTaskOf(chat.state.tasks);
  const thinking = chat.pending || (last?.role === "user" && chat.state.ack != null && !(task && TERMINAL_TASK.has(task.status)));
  if (thinking) {
    return (
      <Badge variant="info" data-testid="orch-task-status" className="normal-case tracking-normal">
        Думаю…
      </Badge>
    );
  }
  if (!task) {
    return (
      <span className="text-[11px] text-white/35" data-testid="orch-task-status">
        нет задачи
      </span>
    );
  }
  const label = TASK_STATUS_LABEL[task.status] ?? "в работе";
  return (
    <Badge
      variant={statusBadgeVariant(task.status)}
      data-testid="orch-task-status"
      className="normal-case tracking-normal"
      title={isHumanText(task.title) && task.title !== "Задача" ? task.title : undefined}
    >
      {task.status === "failed" ? `Задача: ${label} — подробности в чате` : `Задача: ${label}`}
    </Badge>
  );
}

const APPROVAL_TARGET = "[data-testid='orch-approval'][data-terminal='false']";

function jumpToApproval(from: Element | null): boolean {
  const root = from?.closest("[data-testid='studio-orch-chat']") ?? document;
  const card = root.querySelector<HTMLElement>(APPROVAL_TARGET);
  if (!card) return false;
  card.scrollIntoView({ block: "center", behavior: "smooth" });
  card.dataset.flash = "true";
  window.setTimeout(() => {
    delete card.dataset.flash;
  }, 1500);
  return true;
}

function ApprovalJump({ chat, onJump }: { chat: Chat; onJump?: () => void }) {
  const waiting = openApprovalsOf(chat.state.approvals).filter((item) => !isTerminalBlock(item.reason)).length;
  if (waiting === 0) return null;
  const go = (event: MouseEvent<HTMLButtonElement>) => {
    const from = event.currentTarget;
    onJump?.();
    window.requestAnimationFrame(() => {
      if (!jumpToApproval(from)) window.setTimeout(() => jumpToApproval(from), 120);
    });
  };
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="h-7 gap-1 border-amber-400/50 bg-amber-500/10 px-2 text-[11px] text-amber-100 hover:bg-amber-500/20"
      data-testid="orch-approval-jump"
      onClick={go}
    >
      <BellRing className="h-3.5 w-3.5" />
      {waiting > 1 ? `Перейти к подтверждению (${waiting})` : "Перейти к подтверждению"}
    </Button>
  );
}

function KillDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Остановить задачу чата?</DialogTitle>
          <DialogDescription>
            Неплатные шаги задачи отменятся сразу, новые действия задачи не запустятся. Платный шаг остановится на
            границе — оборвать оплату посередине нельзя. Аварийный стоп всей студии эта кнопка не включает.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Не сейчас
          </Button>
          <Button
            type="button"
            variant="destructive"
            data-testid="orch-kill-confirm"
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            Остановить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function stripTechTail(text: string): string {
  return text.replace(/\s*\([a-z _-]+\)\s*$/i, "").trim();
}

function ConnBanners({ chat, task }: { chat: Chat; task: TaskView | null }) {
  const kill = chat.state.killNotice;
  return (
    <>
      {chat.conn === "reconnecting" || chat.conn === "restored" ? (
        <div
          data-testid="orch-reconnect"
          className={cn(
            "shrink-0 border-b px-3 py-1.5 text-[11px]",
            chat.conn === "reconnecting"
              ? "border-warning/30 bg-warning/10 text-warning"
              : "border-success/30 bg-success/10 text-success",
          )}
        >
          {chat.conn === "reconnecting"
            ? "Соединение прервано — переподключаюсь…"
            : "Соединение восстановлено, пропущенные события повторены"}
        </div>
      ) : null}

      {chat.authError ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-destructive/40 bg-destructive/10 px-3 py-1.5 text-[11px] text-white" role="alert">
          <span>{humanDetail(chat.authError, "Чат не пускает: вход в студию закончился.")}</span>
          <Button type="button" size="sm" variant="outline" className="h-6 text-[11px]" onClick={() => window.location.reload()}>
            Обновить страницу
          </Button>
        </div>
      ) : null}

      {kill ? (
        <div
          data-testid="orch-kill-status"
          data-kill-phase={kill.phase}
          className="shrink-0 border-b border-destructive/40 bg-destructive/15 px-3 py-1.5 text-[11px] text-white"
        >
          <div>Остановка задачи: {killBannerText(kill)}</div>
          {kill.lateStopWarning && isHumanText(kill.lateStopWarning) ? <div className="mt-1">{kill.lateStopWarning}</div> : null}
          {kill.phase === "error" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-1.5 h-6 text-[11px]"
              data-testid="orch-kill-retry"
              onClick={() => void chat.kill()}
            >
              Повторить
            </Button>
          ) : null}
        </div>
      ) : null}

      {!chat.needsToken && chat.partialWrite ? (
        <div data-testid="orch-partial" className="shrink-0 border-b border-warning/40 bg-warning/10 px-3 py-1.5 text-[11px] text-warning">
          Шаг записал изменения частично
          {chat.partialIds && chat.partialIds.length > 0 ? ` (изменений: ${chat.partialIds.length})` : ""}. Откат не
          выполняется — решите в карточке ниже, принять или остановить задачу.
          {chat.partialError ? <span className="ml-1 text-destructive">Список изменений не загрузился.</span> : null}
        </div>
      ) : null}

      {!chat.needsToken && resumeButtonVisible(task) ? (
        <div
          data-testid="orch-resume-bar"
          className="flex shrink-0 flex-wrap items-center gap-2 border-b border-warning/30 bg-warning/10 px-3 py-1.5 text-[11px] text-warning"
        >
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 text-[11px]"
            data-testid="orch-resume"
            disabled={chat.resumePending || chat.resumeLocked}
            onClick={() => void chat.resume()}
          >
            Продолжить
          </Button>
          {chat.resumeError ? (
            <span data-testid="orch-resume-error" className="text-destructive">
              {humanDetail(stripTechTail(chat.resumeError), "Продолжить не получилось. Попробуйте ещё раз.")}
            </span>
          ) : (
            <span>Задача на паузе — можно продолжить с последнего сохранённого места.</span>
          )}
        </div>
      ) : null}
    </>
  );
}

function ChatLine({ message }: { message: ChatMessage }) {
  const user = message.role === "user";
  return (
    <div className={cn("mb-2 flex", user ? "justify-end" : "justify-start")} data-testid="orch-line" data-role={message.role}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-relaxed",
          user ? "rounded-br-md bg-primary/15 text-white" : "rounded-bl-md border border-white/[0.06] bg-white/[0.04] text-white/90",
        )}
      >
        <span className="sr-only">{user ? "вы: " : "оркестратор: "}</span>
        {user ? (
          <span className="whitespace-pre-wrap">{message.text}</span>
        ) : (
          <div className="min-w-0 break-words" data-testid="orch-assistant">
            <ChatMarkdown text={roundMoneyText(message.text)} />
          </div>
        )}
      </div>
    </div>
  );
}

function TranscriptCards({ chat, titles }: { chat: Chat; titles: Record<string, string> }) {
  const open = openApprovalsOf(chat.state.approvals);
  return (
    <>
      {chat.state.tasks.flatMap((item) =>
        item.steps
          .filter((step) => step.node && isNodeResultAction(step.action))
          .map((step) => <NodeResultCard key={`${item.id}:${step.id}`} step={step} titles={titles} />),
      )}
      {chat.state.actionResults.map((result) => (
        <ActionResultCard
          key={result.id}
          result={result}
          onReportEdit={chat.sendReportEdit}
          onRestore={chat.sendRestore}
          onConflictRetry={chat.sendConflictRetry}
        />
      ))}
      {open.map((approval) => (
        <DockApprovalCard key={approval.id} approval={approval} chat={chat} titles={titles} />
      ))}
    </>
  );
}

function NodeResultCard({ step, titles }: { step: StepView; titles: Record<string, string> }) {
  if (!step.node) return null;
  const label = actionLabel(step.action, titles) || "Нода";
  const node = nodeKeyRu(step.node.node_key);
  const version = typeof step.node.db_version === "number" ? step.node.db_version : null;
  return (
    <div data-testid="orch-node-result" className="mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
      <div className="text-[11px] text-white/70">
        {label}
        {node ? ` · ${node}` : ""}
      </div>
      {step.node.source === "db" ? (
        <div className="mt-1 text-[10px] text-sky-300/80">
          {version != null ? `настройка ноды из студии, версия ${version}` : "настройка ноды из студии"}
        </div>
      ) : null}
      {step.node.stale_base ? (
        <div className="mt-1 text-[10px] text-warning">исходный шаблон ноды изменился после этой версии</div>
      ) : null}
    </div>
  );
}

function decisionErrorText(chat: Chat, approvalId: string): string {
  const err = chat.decisionError;
  if (!err || err.id !== approvalId) return "";
  if (err.locked) return "Решение по этой карточке уже записано на сервере — ждём обновлённую карточку.";
  return humanDetail(err.message, "Решение не записалось. Попробуйте ещё раз.");
}

function DockApprovalCard({ approval, chat, titles }: { approval: ApprovalView; chat: Chat; titles: Record<string, string> }) {
  const [projectDraft, setProjectDraft] = useState("");
  const hideMoney = useHideMoney();
  const copy = DOCK_BLOCK_COPY[approval.reason];
  const terminal = isTerminalBlock(approval.reason);
  const buttons = approvalButtons(approval.reason);
  const decisions = paidDecisionsOf(approval) ?? partialDecisionsOf(approval);
  const hasId = approvalChangeId(approval.changeId) != null;
  const pending = chat.decisionPendingId === approval.id;
  const locked = chat.decisionError?.id === approval.id && chat.decisionError.locked === true;
  const named = isHumanText(approval.title) && approval.title !== "Нужно подтверждение" ? approval.title : "";
  const title = copy?.title || named || "Нужно подтверждение";
  const subtitle = copy && named && named !== copy.title ? named : "";
  const body = copy?.body || "Задача ждёт вашего решения.";
  const label = actionLabel(approval.action, titles);
  const shownLabel = label && label !== title && label !== subtitle ? label : "";
  const tone = RISK_TONE[approval.risk] ?? "muted";
  const projectId = Number(projectDraft);
  const error = decisionErrorText(chat, approval.id);
  return (
    <div
      data-testid="orch-approval"
      data-terminal={terminal ? "true" : "false"}
      className={cn(
        "mb-3 scroll-mt-2 rounded-xl border px-3 py-3 transition-shadow data-[flash=true]:ring-2 data-[flash=true]:ring-amber-300/70",
        terminal ? "border-destructive/40 bg-destructive/10" : "border-warning/40 bg-warning/10",
      )}
    >
      <div className="text-xs font-semibold text-white">{title}</div>
      {subtitle ? <div className="mt-0.5 text-[12px] text-white/90">{subtitle}</div> : null}
      <p className="mt-1 text-[11px] leading-relaxed text-white/70">{body}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        {shownLabel ? <span className="text-white/80">{shownLabel}</span> : null}
        {approval.risk ? (
          <Badge variant={tone} className="normal-case tracking-normal">
            риск: {riskRu(approval.risk)}
          </Badge>
        ) : null}
        {terminal || approval.costEstimate == null ? null : hideMoney ? (
          <span className="text-white/60">{MEMBER_PRICE_TEXT}</span>
        ) : (
          <span className="text-white/70">цена: около {moneyCents(approval.costEstimate)}</span>
        )}
      </div>
      {approval.plan.length > 0 ? <PlanList steps={approval.plan} titles={titles} /> : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {terminal ? (
          <Button type="button" size="sm" variant="outline" data-testid="orch-block-close" onClick={() => chat.dismissBlock(approval.id)}>
            Закрыть
          </Button>
        ) : decisions && hasId ? (
          decisions.map((decision) => (
            <Button
              key={decision}
              type="button"
              size="sm"
              variant={decision === "approve" ? "default" : "outline"}
              data-testid="orch-decision"
              data-decision={decision}
              disabled={pending || locked}
              onClick={() => void chat.decidePaid(approval.id, approval.taskId, decision)}
            >
              {PAID_DECISION_LABEL[decision]}
            </Button>
          ))
        ) : approval.reason === "missing_project_id" ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (Number.isInteger(projectId) && projectId >= 1) void chat.bindProject(approval.id, projectId);
            }}
          >
            <Input
              value={projectDraft}
              inputMode="numeric"
              className="h-8 w-28 text-xs"
              placeholder="номер проекта"
              aria-label="Номер проекта"
              onChange={(event) => setProjectDraft(event.target.value)}
            />
            <Button type="submit" size="sm" disabled={pending || !(Number.isInteger(projectId) && projectId >= 1)}>
              Привязать проект
            </Button>
          </form>
        ) : hasId && buttons.length > 0 ? (
          <>
            {buttons.includes("approve") ? (
              <Button
                type="button"
                size="sm"
                data-testid="orch-approve"
                disabled={pending || locked}
                onClick={() => void chat.decide(approval.id, approval.taskId, "approved")}
              >
                {pending ? "Записываю…" : approval.plan.length ? "Подтвердить план" : "Подтвердить"}
              </Button>
            ) : null}
            {buttons.includes("retry_step") ? (
              <Button
                type="button"
                size="sm"
                disabled={pending || locked}
                onClick={() => void chat.decide(approval.id, approval.taskId, "retry_step")}
              >
                Перезапустить шаг
              </Button>
            ) : null}
            {buttons.includes("deny") ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="orch-reject"
                disabled={pending || locked}
                onClick={() => void chat.decide(approval.id, approval.taskId, "rejected")}
              >
                Отклонить
              </Button>
            ) : null}
          </>
        ) : (
          <p className="text-[11px] text-white/55" data-testid="orch-approval-noid">
            Сервер не прислал номер подтверждения, поэтому подтвердить отсюда нельзя. Откройте чат на всю страницу.
          </p>
        )}
      </div>
      {error ? (
        <p data-testid="orch-approval-error" role="alert" className="mt-2 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Composer({
  chat,
  draft,
  setDraft,
  onSend,
  taskMode,
  setTaskMode,
  placeholder,
}: {
  chat: Chat;
  draft: string;
  setDraft: (v: string) => void;
  onSend: () => void;
  taskMode: boolean;
  setTaskMode: (v: boolean) => void;
  placeholder: string;
}) {
  const last = chat.state.messages.at(-1);
  // «Принял, разбираю» висит, пока нет ответа на последнюю реплику.
  const ack = chat.state.ack && last?.role === "user" ? chat.state.ack : null;
  const classification = ack ? chat.state.classification : null;
  const detail = classification ? humanDetail(classification.detail, "") : "";
  return (
    <div className="shrink-0 space-y-1.5 border-t border-white/[0.06] px-3 py-2">
      {ack ? (
        <div data-testid="orch-ack" className="text-[11px] text-info">
          {isHumanText(ack.text) ? ack.text : "принял, разбираю"}
          {ack.echo ? ` — «${ack.echo}»` : ""}
        </div>
      ) : null}
      {classification ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px]" data-testid="orch-classification">
          <Badge
            variant={
              classification.killSwitch || classification.mode === "interrupt"
                ? "warning"
                : classification.mode === "steer"
                  ? "info"
                  : "success"
            }
            className="normal-case tracking-normal"
          >
            {classification.killSwitch ? "остановка" : MODE_RU[classification.mode] ?? "запрос"}
          </Badge>
          {classification.killSwitch || detail ? (
            <span className="text-white/55">{classification.killSwitch ? "стоп из текста сообщения" : detail}</span>
          ) : null}
        </div>
      ) : null}
      {chat.sendError ? (
        <p className="text-[11px] text-destructive" role="alert">
          {humanDetail(chat.sendError, "Сообщение не отправилось. Попробуйте ещё раз.")}
        </p>
      ) : null}
      <div className="flex items-end gap-2">
        <Textarea
          data-testid="orch-composer"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
            event.preventDefault();
            onSend();
          }}
          placeholder={placeholder}
          className="min-h-[40px] flex-1 resize-none py-2 text-xs"
          rows={1}
        />
        <Button
          type="button"
          size="sm"
          className="h-10 gap-1.5 text-xs"
          data-testid="orch-send"
          disabled={!draft.trim() || !chat.ready}
          onClick={onSend}
        >
          <Send className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{chat.pending ? "…" : "Отправить"}</span>
        </Button>
      </div>
      {orchTaskModeEnabled() ? (
        <label className="flex items-center gap-1.5 text-[11px] text-white/55" title="Встаёт в очередь за текущей и не прерывает её.">
          <input type="checkbox" data-testid="orch-mode-task" checked={taskMode} onChange={(event) => setTaskMode(event.target.checked)} />
          Новая задача
          <span className="text-[10px] text-white/35">— встанет в очередь за текущей</span>
        </label>
      ) : null}
    </div>
  );
}

function agentDot(status: string): string {
  if (status === "running" || status === "executing" || status === "planning" || status === "critic_review") {
    return "animate-pulse bg-sky-400";
  }
  if (status === "done") return "bg-emerald-400";
  if (status === "failed" || status === "cancelled" || status === "killed") return "bg-red-400";
  if (status === "waiting" || status === "waiting_approval" || status === "paused") return "bg-amber-400";
  return "bg-white/25";
}

function AgentsRow({ agents }: { agents: AgentView[] }) {
  if (agents.length === 0) return null;
  return (
    <div className="grid grid-cols-3 gap-1 border-b border-white/[0.06] px-2.5 py-1.5" data-testid="orch-agents-row" aria-label="Агенты">
      {agents.map((agent) => {
        const name = AGENT_RU[agent.role] ?? "Агент";
        const status = agentStatusRu(agent.status);
        return (
          <span
            key={agent.role}
            data-testid={`orch-agent-${agent.role}`}
            title={`${name}: ${status}`}
            className="flex min-w-0 items-center gap-1 rounded-md bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-white/70"
          >
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", agentDot(agent.status))} />
            <span className="truncate">{name}</span>
            <span className="sr-only">: {status}</span>
          </span>
        );
      })}
    </div>
  );
}

function costText(value: number, paid: boolean): string {
  if (value > 0) return `около ${moneyCents(value)}`;
  return paid ? "цена неизвестна" : "бесплатно";
}

function PlanList({ steps, titles }: { steps: PlanStepView[]; titles: Record<string, string> }) {
  const groups = groupPlanSteps(steps);
  const hideMoney = useHideMoney();
  return (
    <div className="mt-2" data-testid="orch-plan">
      <div className="text-[11px] font-medium text-white/45">План</div>
      {groups.map((group) => (
        <div key={group.key} className="mt-1.5">
          {group.parallel ? <div className="text-[10px] text-info">эти шаги идут одновременно</div> : null}
          {group.steps.map((step, index) => (
            <div key={`${group.key}-${index}`} data-testid="orch-plan-step" className="text-[11px] text-white/75">
              {actionLabel(step.action, titles) || "Действие студии"}
              <span>{step.risk === "none" || !step.risk ? " · без риска" : ` · риск ${riskRu(step.risk)}`}</span>
              {hideMoney ? null : <span> · {costText(step.estimatedCost, step.paid)}</span>}
              {step.paid ? <span className="text-amber-200/80"> · платный</span> : null}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

const STEP_STATUS_RU: Record<StepView["status"], string> = {
  running: "идёт",
  done: "готово",
  failed: "ошибка",
};

function stepMark(step: StepView): string {
  if (step.status === "running") return "…";
  if (step.status === "done") return "●";
  return "✕";
}

/** Точка сохранения плана: задача продолжит с неё после сбоя. */
function checkpointRu(summary: string): string {
  const text = summary.trim();
  if (!text || text === "план" || text === "чекпоинт" || !isHumanText(text)) {
    return "План сохранён: после сбоя задача продолжит с этого места";
  }
  return `Сохранено: ${text}`;
}

function taskErrorText(error: string): string {
  const copy = DOCK_BLOCK_COPY[error];
  if (copy) return `${copy.title}. ${copy.body}`;
  return humanErrorText(humanTaskFailure(error));
}

function TaskList({ tasks, titles }: { tasks: TaskView[]; titles: Record<string, string> }) {
  if (tasks.length === 0) {
    return <p className="mt-2 text-[11px] text-white/35">Шаги и ход выполнения появятся вместе с задачей.</p>;
  }
  return (
    <>
      {tasks.map((item) => (
        <div key={item.id} className="mt-3 rounded-xl border border-white/[0.08] bg-card/80 p-3">
          <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
            <div className="min-w-0 break-words text-xs font-semibold text-white/90">
              {isHumanText(item.title) ? item.title : "Задача"}
            </div>
            <Badge variant={statusBadgeVariant(item.status)} className="shrink-0 whitespace-nowrap normal-case tracking-normal">
              {TASK_STATUS_LABEL[item.status] ?? "в работе"}
            </Badge>
          </div>
          {item.summary && isHumanText(item.summary) ? (
            <p className="mt-2 text-[11px] text-white/70" data-testid="orch-task-summary">
              {item.summary}
            </p>
          ) : null}
          {item.error ? (
            <p className="mt-2 text-[11px] text-destructive" data-testid="orch-task-error">
              {taskErrorText(item.error)}
            </p>
          ) : null}
          {item.plan.length > 0 ? <PlanList steps={item.plan} titles={titles} /> : null}
          <ul className="mt-2 space-y-1.5">
            {item.steps.map((step) => {
              const label = actionLabel(step.action, titles);
              const name = isHumanText(step.title) ? step.title : label || "Шаг";
              const note = step.note ? humanDetail(step.note, step.status === "failed" ? "шаг не удался" : "") : "";
              return (
                <li key={step.id} className="text-[11px] text-white/75" data-testid="orch-step" data-step-status={step.status}>
                  <span className={cn("text-white/40", step.status === "failed" && "text-red-300")}>{stepMark(step)}</span> {name}
                  <span className="text-white/40"> · {STEP_STATUS_RU[step.status]}</span>
                  {step.paid ? " · платный" : ""}
                  {note ? ` · ${note}` : ""}
                </li>
              );
            })}
          </ul>
          {item.checkpoints.map((checkpoint) => (
            <div key={checkpoint.id} className="mt-2 rounded-md border border-white/[0.06] px-2 py-1.5 text-[11px] text-white/60">
              {checkpointRu(checkpoint.summary)}
            </div>
          ))}
          {item.validators.map((validator, index) => (
            <div key={`${validator.name}-${index}`} className="mt-1 text-[10px] text-white/40">
              {validatorRu(validator.name)}: {validator.pass ? "пройдена" : "не пройдена"}
              {validator.details && isHumanText(validator.details) ? ` — ${validator.details}` : ""}
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
