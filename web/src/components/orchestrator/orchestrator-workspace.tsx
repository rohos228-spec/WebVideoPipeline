"use client";

import { MEMBER_PRICE_TEXT, useHideMoney } from "@/hooks/use-hide-money";
import { useEffect, useState, type MouseEvent } from "react";
import { BellRing, Bot, ChevronDown, ChevronRight, CircleAlert, CircleCheck, KeyRound, OctagonX, Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useActionTitles } from "@/lib/action-layer";
import {
  ackAnswered,
  approvalLost,
  currentTask,
  groupPlanSteps,
  hasTaskSummary,
  isNodeResultAction,
  isTerminalBlock,
  openApprovals,
  outcomeCovered,
  taskDetails,
  TASK_STATUS_LABEL,
  type AgentView,
  type ApprovalView,
  type ChatMessage,
  type KillNoticeView,
  type OrchScreenState,
  type PlanStepView,
  type StepView,
  type TaskView,
} from "@/lib/orchestratorSession";
import { neutralEnd, pickTaskBadge } from "@/lib/orchestratorBadge";
import type { ApprovalReason, NodeDef, TaskStatus } from "@/lib/orchestratorTypes";
import { orchTaskModeEnabled } from "@/lib/orchestratorApi";
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
  taskFailureRu,
  validatorRu,
} from "@/lib/orchestratorHuman";
import {
  blockReasonText,
  cardPrice,
  detailsWorthShowing,
  moneyCents,
  nodeKeyRu,
  pluralRu,
  roundMoneyText,
  splitCardSteps,
} from "@/lib/orchestratorCard";
import { cn } from "@/lib/utils";
import { ChatMarkdown } from "./chat-markdown";
import { useOrchestratorChat } from "./use-orchestrator-chat";

const BUDGET_EXHAUSTED = "budget_exhausted" satisfies ApprovalReason;
const CRITIC_ESCALATION = "critic_escalation" satisfies ApprovalReason;

const KILL_HONEST =
  "Неплатные шаги задачи отменены, новые действия задачи не запускаются. Платный шаг остановится на границе.";

const REVISE_LIMIT = "revise_limit";
const MISSING_PROJECT = "missing_project_id" satisfies ApprovalReason;
const RISK_APPROVAL = "risk" satisfies ApprovalReason;

export const BLOCK_COPY: Record<string, { title: string; body: string; testId?: string }> = {
  [BUDGET_EXHAUSTED]: {
    title: "Бюджет исчерпан",
    body: "Лимит прогона выбран. Задача ждёт подтверждения, новые платные шаги не стартуют.",
    testId: "orch-budget",
  },
  [CRITIC_ESCALATION]: {
    title: "Критик не одобрил план после 2 правок",
    body: "Две правки не помогли. Задача ждёт решения, план сам не продолжится.",
    testId: "orch-critic",
  },
  [REVISE_LIMIT]: {
    title: "Критик не одобрил план после 2 правок",
    body: "Две правки не помогли. Задача ждёт решения, план сам не продолжится.",
    testId: "orch-critic",
  },
  [MISSING_PROJECT]: {
    title: "Не выбран проект: план содержит платный или изменяющий шаг",
    body: "Задача ждёт подтверждения. Платный или изменяющий шаг без проекта не стартует.",
    testId: "orch-missing-project",
  },
  [RISK_APPROVAL]: {
    title: "Шаг с риском: нужно подтверждение",
    body: "Шаг не стартует, пока вы его не подтвердите.",
    testId: "orch-risk",
  },
  deletion_blocked: {
    title: "Удаление запрещено",
    body: "Шаг удаляет кадр или сцену. Одобрить нельзя, задача остановлена.",
    testId: "orch-deletion",
  },
  missing_run_id: {
    title: "Нет номера прогона",
    body: "Платный шаг нельзя начать, пока у проекта нет прогона. Задача остановлена.",
    testId: "orch-missing-run",
  },
  empty_plan: {
    title: "Подходящих действий не нашлось",
    body: "План получился пустым: ничего не сделано и подтверждать нечего. Уточните, что сделать и с каким проектом.",
    testId: "orch-empty-plan",
  },
};

export function taskErrorText(error: string, reason = ""): string {
  if (!error) return "";
  const copy = BLOCK_COPY[error];
  if (copy) return `${copy.title}. ${copy.body}`;
  const known = taskFailureRu(error, reason);
  if (known) return humanErrorText(known);
  return humanDetail(
    error,
    "Задача остановлена из-за внутренней ошибки. Попросите повторить другими словами; подробности есть во вкладке «Регистратор» → «Журнал».",
  );
}

export function killBannerText(notice: KillNoticeView): string {
  if (notice.phase === "stopping") return "Останавливаю…";
  if (notice.phase === "unconfirmed") {
    return notice.text.trim() || "Задача отменена, стоп-ключ не подтверждён.";
  }
  if (notice.phase === "error") return notice.text.trim() || "Остановка не подтверждена";
  const lead = "Остановлено";
  if (!notice.paidBoundary) {
    const extra = notice.text.trim();
    return extra ? `${lead}. ${extra}` : lead;
  }
  const extra = notice.text.trim();
  return extra ? `${lead}. ${KILL_HONEST} ${extra}` : `${lead}. ${KILL_HONEST}`;
}

export function statusBadgeVariant(status: string): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (status === "done" || status === "idle") return "success";
  if (status === "failed") return "destructive";
  if (status === "paused" || status === "waiting_approval" || status === "waiting") return "warning";
  if (status === "running" || status === "executing" || status === "planning" || status === "critic_review") return "info";
  return "muted";
}

type Chat = ReturnType<typeof useOrchestratorChat>;

export function OrchestratorWorkspace() {
  const chat = useOrchestratorChat();
  const titles = useActionTitles();
  const [draft, setDraft] = useState("");
  const [taskMode, setTaskMode] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);

  const send = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    void chat.sendMessage(text, orchTaskModeEnabled() && taskMode ? "task" : undefined).then((sent) => {
      // Не отправилось — текст человека не теряется: возвращаем, если поле ещё пустое.
      if (!sent) setDraft((current) => current || text);
    });
  };

  return (
    <div className="flex h-[calc(100vh-48px)] min-h-0 flex-col bg-background" data-orch-page="">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <Bot className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-semibold tracking-tight text-white">Оркестратор</span>
          <TaskStatusBadge state={chat.state} />
          <ApprovalJump chat={chat} />
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-xs"
            onClick={() => window.location.assign("/")}
          >
            В студию
          </Button>
          {/* «Отчёты» (редактор отчётов оркестратора) не перенесены: ручек /api/orchestrator/reports
              и /api/orchestrator/actions/*_report на этой линии нет. Отчёты по нодам и проектам
              чат отдаёт файлами (report.nodes / report.projects). */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={() => {
              // Не подставлять текущий токен (это токен входа студии): поле пустое.
              setTokenDraft("");
              setTokenOpen(true);
            }}
          >
            <KeyRound className="h-3.5 w-3.5" />
            Токен
          </Button>
          <KillButton chat={chat} onClick={() => setKillOpen(true)} />
        </div>
      </div>

      <ConnBanners chat={chat} />

      {chat.needsToken ? (
        <TokenGate
          error={chat.authError}
          value={tokenDraft}
          onChange={setTokenDraft}
          onSubmit={() => chat.submitToken(tokenDraft)}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <section className="flex min-h-0 min-w-0 flex-1 flex-col border-white/[0.06] md:border-r">
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="orch-transcript">
              {chat.state.messages.length === 0 ? (
                <p className="text-xs leading-relaxed text-white/35">
                  Пишите обычными словами. Сообщение можно отправить и посреди задачи: сначала придёт «принял,
                  разбираю», затем оркестратор решит, прервать задачу, уточнить её или поставить новую.
                </p>
              ) : null}
              {chat.state.messages.map((message) => (
                <ChatFeedItem key={message.id} message={message} chat={chat} titles={titles} />
              ))}
              <TranscriptCards chat={chat} titles={titles} />
            </div>
            <Composer chat={chat} draft={draft} setDraft={setDraft} onSend={send} taskMode={taskMode} setTaskMode={setTaskMode} />
          </section>

          <aside
            className="flex max-h-[40vh] shrink-0 flex-col bg-black/30 md:max-h-none md:w-[340px]"
            data-testid="orch-task-panel"
          >
            <AgentsGrid agents={chat.state.agents} />
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <TaskList tasks={chat.state.tasks} titles={titles} />
            </div>
          </aside>
        </div>
      )}

      <Dialog open={tokenOpen} onOpenChange={setTokenOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Токен оркестратора</DialogTitle>
            <DialogDescription>
              Нужен, только если сервер не пускает по входу студии. Хранится в этом браузере.
            </DialogDescription>
          </DialogHeader>
          <Input
            type="password"
            value={tokenDraft}
            onChange={(event) => setTokenDraft(event.target.value)}
            placeholder="токен"
            autoComplete="off"
          />
          <DialogFooter>
            <Button
              type="button"
              onClick={() => {
                chat.submitToken(tokenDraft);
                setTokenOpen(false);
              }}
              disabled={!tokenDraft.trim()}
            >
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <KillDialog open={killOpen} onOpenChange={setKillOpen} onConfirm={() => void chat.kill()} />
    </div>
  );
}

export function TaskStatusBadge({ state }: { state: Pick<OrchScreenState, "tasks" | "ack" | "messages"> }) {
  const badge = pickTaskBadge(state.tasks, state.ack, state.messages);
  if (badge.kind === "thinking") {
    return (
      <Badge variant="info" data-testid="orch-task-status" className="normal-case tracking-normal">
        Думаю…
      </Badge>
    );
  }
  if (badge.kind === "none") {
    return (
      <span className="text-[11px] text-white/35" data-testid="orch-task-status">
        нет задачи
      </span>
    );
  }
  const task = badge.task;
  const neutral = neutralEnd(task);
  const label =
    neutral === "unclear"
      ? "нужно уточнение"
      : neutral === "answered"
        ? "ответил"
        : (TASK_STATUS_LABEL[task.status as TaskStatus] ?? "в работе");
  return (
    <Badge
      variant={neutral ? "muted" : statusBadgeVariant(task.status)}
      data-testid="orch-task-status"
      data-task-id={task.id}
      className="normal-case tracking-normal"
      title={isHumanText(task.title) && task.title !== "Задача" ? task.title : undefined}
    >
      {task.status === "failed" && !neutral ? `Задача: ${label} — подробности в чате` : `Задача: ${label}`}
    </Badge>
  );
}

export function KillButton({ chat, onClick, compact = false }: { chat: Chat; onClick: () => void; compact?: boolean }) {
  return (
    <Button
      type="button"
      variant="destructive"
      size="sm"
      className={cn("gap-1.5 text-xs font-semibold", compact ? "h-7 px-2" : "h-9 px-3")}
      data-testid="orch-kill"
      disabled={chat.needsToken}
      onClick={onClick}
      title="Остановить задачу этого чата и его проект. Платный шаг остановится на границе. Аварийный стоп всей студии — в «Регистраторе»."
    >
      <OctagonX className="h-3.5 w-3.5" />
      {compact ? "Стоп задачи" : "Остановить задачу"}
    </Button>
  );
}

export function KillDialog({
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
            Неплатные шаги задачи отменятся сразу, новые действия задачи не запустятся, текущий проект чата
            будет остановлен. Платный шаг остановится на границе — оборвать оплату посередине нельзя.
            Аварийный стоп всей студии эта кнопка не включает: он в разделе «Регистратор».
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

export function ConnBanners({ chat }: { chat: Chat }) {
  return (
    <>
      {chat.conn === "reconnecting" || chat.conn === "restored" ? (
        <div
          data-testid="orch-reconnect"
          className={cn(
            "shrink-0 border-b px-4 py-2 text-xs",
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

      {chat.state.killNotice ? (
        <div
          data-testid="orch-kill-status"
          data-kill-phase={chat.state.killNotice.phase}
          className="shrink-0 border-b border-destructive/40 bg-destructive/15 px-4 py-2 text-xs text-white"
        >
          <div>Остановка задачи: {killBannerText(chat.state.killNotice)}</div>
          {chat.state.killNotice.phase === "error" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-2 h-7 text-[11px]"
              data-testid="orch-kill-retry"
              onClick={() => void chat.kill()}
            >
              Повторить
            </Button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function ChatLine({ role, text, typing = false }: { role: "user" | "assistant"; text: string; typing?: boolean }) {
  const user = role === "user";
  return (
    <div className={cn("mb-2 flex", user ? "justify-end" : "justify-start")} data-testid="orch-line" data-role={role}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-relaxed",
          user ? "rounded-br-md bg-primary/15 text-white" : "rounded-bl-md border border-white/[0.06] bg-white/[0.04] text-white/90",
        )}
      >
        <span className="sr-only">{user ? "вы: " : "оркестратор: "}</span>
        {user ? (
          <span className="whitespace-pre-wrap" data-line-text="">
            {text}
          </span>
        ) : (
          <div className="min-w-0 break-words" data-line-text="" data-testid="orch-assistant">
            <ChatMarkdown text={roundMoneyText(text)} />
          </div>
        )}
        {typing && !text ? <span className="text-white/40">печатает…</span> : null}
      </div>
    </div>
  );
}

export function ChatFeedItem({
  message,
  chat,
  titles,
}: {
  message: ChatMessage;
  chat: Chat;
  titles: Record<string, string>;
}) {
  if (message.kind === "text") {
    if (message.detail && hasTaskSummary(chat.state.messages, message.taskId)) return null;
    const line = <ChatLine role={message.role} text={message.text} typing={message.streaming} />;
    if (!message.summary || message.role !== "assistant") return line;
    const owner = chat.state.tasks.find((item) => item.id === message.taskId);
    const last = chat.state.messages.findLast((item) => item.summary && item.taskId === message.taskId);
    if (last?.id !== message.id) return line;
    if (owner && owner.status === "failed") {
      return (
        <>
          {line}
          <TaskFailureDetails task={owner} titles={titles} />
        </>
      );
    }
    const details = taskDetails(chat.state.messages, message.taskId);
    if (details.length === 0) return line;
    if (owner && !detailsWorthShowing(owner.plan, owner.steps)) return line;
    return (
      <>
        {line}
        <StepResultDetails taskId={message.taskId} lines={details} />
      </>
    );
  }
  const task = chat.state.tasks.find((item) => item.id === message.taskId);
  if (!task) return null;
  if (hasTaskSummary(chat.state.messages, task.id)) return null;
  if (message.kind === "done" && outcomeCovered(chat.state.messages, task)) return null;
  return <TaskOutcomeCard kind={message.kind} task={task} titles={titles} />;
}

function StepResultDetails({ taskId, lines }: { taskId: string | null; lines: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="-mt-1 mb-2 pl-1 text-[11px]" data-testid="orch-step-details" data-task-id={taskId ?? ""}>
      <button
        type="button"
        className="flex items-center gap-1 text-white/45 hover:text-white/80"
        data-testid="orch-step-details-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Подробности
      </button>
      {open ? (
        <div
          className="mt-1 max-w-[85%] space-y-0.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 leading-relaxed text-white/65"
          data-testid="orch-step-details-body"
        >
          {lines.map((line, index) => (
            <p key={index}>{line}</p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function failureCopy(task: TaskView) {
  const block = BLOCK_COPY[task.error];
  if (block) return { what: block.title, why: block.body, todo: "" };
  return humanTaskFailure(task.error, task.reason);
}

function TaskFailureDetails({ task, titles }: { task: TaskView; titles: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const copy = failureCopy(task);
  const checks = [...new Set(task.validators.filter((item) => !item.pass).map((item) => validatorRu(item.name)))];
  const broken = task.steps.filter((step) => step.status === "failed").map((step) => stepLabel(step, titles));
  return (
    <div className="-mt-1 mb-2 pl-1 text-[11px]" data-testid="orch-task-details" data-task-id={task.id}>
      <button
        type="button"
        className="flex items-center gap-1 text-white/45 hover:text-white/80"
        data-testid="orch-task-details-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Подробности
      </button>
      {open ? (
        <div
          className="mt-1 max-w-[85%] space-y-0.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 leading-relaxed text-white/65"
          data-testid="orch-task-details-body"
        >
          <p className="text-white/80">{withDot(copy.what)}</p>
          {copy.why ? <p>Почему: {withDot(copy.why)}</p> : null}
          {checks.length > 0 ? <p>Не прошли проверки: {checks.join(", ")}.</p> : null}
          {broken.length > 0 ? <p>Не выполнились шаги: {broken.join(", ")}.</p> : null}
          {copy.todo ? <p>Что сделать: {withDot(copy.todo)}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

const APPROVAL_TARGET = "[data-testid='orch-approval'][data-terminal='false']";

export function jumpToApproval(from: Element | null): boolean {
  const root = from?.closest("[data-testid='studio-orch-chat'], [data-orch-page]") ?? document;
  const card = root.querySelector<HTMLElement>(APPROVAL_TARGET);
  if (!card) return false;
  card.scrollIntoView({ block: "center", behavior: "smooth" });
  const button = card.querySelector<HTMLButtonElement>("[data-testid='orch-approve']");
  button?.focus({ preventScroll: true });
  card.dataset.flash = "true";
  window.setTimeout(() => {
    delete card.dataset.flash;
  }, 1500);
  return true;
}

export function ApprovalJump({ chat, onJump }: { chat: Chat; onJump?: () => void }) {
  const waiting = openApprovals(chat.state.approvals).filter((item) => !isTerminalBlock(item.reason)).length;
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

function LostApprovalNotice({ chat }: { chat: Chat }) {
  const lost = approvalLost(currentTask(chat.state.tasks), chat.state.approvals);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!lost) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), 1500);
    return () => window.clearTimeout(timer);
  }, [lost]);
  if (!lost || !shown) return null;
  return (
    <div
      data-testid="orch-approval-lost"
      className="mb-3 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2.5 text-[12px] leading-relaxed"
    >
      <div className="font-semibold text-white">Задача ждёт подтверждения, а карточка до чата не дошла</div>
      <p className="mt-1 text-white/70">
        Обновите ленту — сервер пришлёт карточку заново. Без вашего решения задача не продолжится.
      </p>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="mt-2 h-7 text-[11px]"
        data-testid="orch-approval-resync"
        onClick={chat.resync}
      >
        Обновить ленту
      </Button>
    </div>
  );
}

function withDot(text: string): string {
  const t = text.trim();
  if (!t) return "";
  return /[.!?…»]$/.test(t) ? t : `${t}.`;
}

function stepLabel(step: StepView, titles: Record<string, string>): string {
  const label = actionLabel(step.action, titles);
  if (label) return label;
  return isHumanText(step.title) ? step.title : "Шаг";
}

function taskName(task: TaskView): string {
  return task.title !== "Задача" && isHumanText(task.title) ? task.title : "";
}

export function TaskOutcomeCard({
  kind,
  task,
  titles,
}: {
  kind: "done" | "failed";
  task: TaskView;
  titles: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const name = taskName(task);
  if (kind === "failed") {
    const copy = humanTaskFailure(task.error, task.reason);
    const checks = [...new Set(task.validators.filter((item) => !item.pass).map((item) => validatorRu(item.name)))];
    const broken = task.steps.filter((step) => step.status === "failed").map((step) => stepLabel(step, titles));
    return (
      <div
        data-testid="orch-task-outcome"
        data-outcome="failed"
        data-task-id={task.id}
        className="mb-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-[12px] leading-relaxed"
      >
        <div className="flex items-center gap-1.5 font-semibold text-white">
          <CircleAlert className="h-3.5 w-3.5 shrink-0 text-red-300" />
          {copy.what}
        </div>
        {name ? <div className="text-[11px] text-white/50">Задача: {name}</div> : null}
        <p className="mt-1 text-white/75">Почему: {withDot(copy.why)}</p>
        {checks.length > 0 ? <p className="text-white/60">Не прошли проверки: {checks.join(", ")}.</p> : null}
        {broken.length > 0 ? <p className="text-white/60">Не выполнились шаги: {broken.join(", ")}.</p> : null}
        <p className="mt-1 text-white/90">
          <span className="font-medium">Что сделать:</span> {withDot(copy.todo)}
        </p>
      </div>
    );
  }
  const summary = isHumanText(task.summary) ? task.summary.trim() : "";
  return (
    <div
      data-testid="orch-task-outcome"
      data-outcome="done"
      data-task-id={task.id}
      className="mb-2 rounded-xl border border-success/30 bg-success/[0.07] px-3 py-2.5 text-[12px] leading-relaxed"
    >
      <div className="flex items-center gap-1.5 font-semibold text-white">
        <CircleCheck className="h-3.5 w-3.5 shrink-0 text-emerald-300" />
        {name ? `Готово — ${name}` : "Готово"}
      </div>
      {summary ? <p className="mt-1 text-white/80">{summary}</p> : null}
      {task.steps.length > 0 ? (
        <button
          type="button"
          className="mt-1 flex items-center gap-1 text-[11px] text-white/60 hover:text-white"
          data-testid="orch-task-outcome-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          Результаты шагов ({task.steps.length})
        </button>
      ) : null}
      {open ? (
        <ul className="mt-1 space-y-1 border-t border-white/[0.06] pt-1.5" data-testid="orch-task-outcome-steps">
          {task.steps.map((step) => {
            const result = humanDetail(step.result, "");
            return (
              <li key={step.id} className="text-[11px] text-white/75">
                <span className="text-white/90">{stepLabel(step, titles)}</span>
                <span className="text-white/40"> · {STEP_STATUS_RU[step.status]}</span>
                {result ? <div className="text-white/65">{result}</div> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function TranscriptCards({ chat, titles }: { chat: Chat; titles: Record<string, string> }) {
  const visible = openApprovals(chat.state.approvals).filter(
    (item) => !(isTerminalBlock(item.reason) && !item.serverId && hasTaskSummary(chat.state.messages, item.taskId)),
  );
  // Отклонённая карточка не исчезает бесследно: свёрнутая строка остаётся, пока это последняя задача чата.
  const badge = pickTaskBadge(chat.state.tasks, chat.state.ack, chat.state.messages);
  const rejected =
    badge.kind === "task"
      ? chat.state.approvals.filter((item) => item.resolved === "rejected" && item.taskId === badge.task.id)
      : [];
  return (
    <>
      {chat.state.tasks.flatMap((item) =>
        item.steps
          .filter((step) => step.node && isNodeResultAction(step.action))
          .map((step) => <NodeResultCard key={`${item.id}:${step.id}`} step={step} titles={titles} />),
      )}
      {rejected.map((approval) => (
        <RejectedApprovalNote key={approval.id} approval={approval} titles={titles} />
      ))}
      {visible.map((approval) => (
        <ApprovalCard
          key={approval.id}
          approval={approval}
          titles={titles}
          pending={chat.decisionPendingId === approval.id}
          error={chat.decisionError?.id === approval.id ? chat.decisionError.message : ""}
          onApprove={() => void chat.decide(approval.id, approval.taskId, "approved")}
          onReject={() => void chat.decide(approval.id, approval.taskId, "rejected")}
          onClose={() => chat.dismissBlock(approval.id)}
        />
      ))}
      <LostApprovalNotice chat={chat} />
    </>
  );
}

function RejectedApprovalNote({ approval, titles }: { approval: ApprovalView; titles: Record<string, string> }) {
  const named = isHumanText(approval.title) && approval.title !== "Нужно подтверждение" ? approval.title : "";
  const subject = named || actionLabel(approval.action, titles) || "шаг";
  const projects = approval.groupProjects && approval.groupProjects.length > 1 ? approval.groupProjects : [];
  return (
    <div
      data-testid="orch-approval-rejected"
      data-approval-id={approval.serverId ? approval.id : undefined}
      className="mb-3 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] leading-relaxed text-white/60"
    >
      <span className="font-semibold text-white/80">Отклонено:</span> {subject}
      {projects.length > 0 ? ` (${projects.map((id) => `#${id}`).join(", ")})` : ""}. Ничего не изменено.
    </div>
  );
}

export function Composer({
  chat,
  draft,
  setDraft,
  onSend,
  taskMode,
  setTaskMode,
  placeholder,
  extra,
  compact = false,
}: {
  chat: Chat;
  draft: string;
  setDraft: (v: string) => void;
  onSend: () => void;
  taskMode: boolean;
  setTaskMode: (v: boolean) => void;
  placeholder?: string;
  extra?: React.ReactNode;
  compact?: boolean;
}) {
  const ack = chat.state.ack && !ackAnswered(chat.state.messages, chat.state.ack) ? chat.state.ack : null;
  const classification = compact && !ack ? null : chat.state.classification;
  return (
    <>
      {orchTaskModeEnabled() && !compact ? (
        <label className="flex shrink-0 items-start gap-2 px-4 pb-2 pt-1 text-[11px] text-white/60">
          <input
            type="checkbox"
            className="mt-0.5"
            data-testid="orch-mode-task"
            checked={taskMode}
            onChange={(event) => setTaskMode(event.target.checked)}
          />
          <span>
            Новая задача
            <span className="block text-[10px] text-white/35" data-testid="orch-mode-task-hint">
              Встаёт в очередь за текущей и не прерывает её.
            </span>
          </span>
        </label>
      ) : null}
      <div
        className={cn(
          "shrink-0 border-t border-white/[0.06] px-3",
          compact ? "space-y-1.5 py-2" : "space-y-2 py-2 sm:px-4 sm:py-3",
        )}
      >
        {ack ? (
          <div
            data-testid="orch-ack"
            data-ack-ms={chat.ackMs == null ? undefined : String(Math.round(chat.ackMs))}
            data-ack-echo={ack.echo}
            className="text-[11px] text-info"
          >
            {ack.text || "принял, разбираю"}
            {ack.echo ? ` — «${ack.echo}»` : ""}
          </div>
        ) : null}
        {classification ? (
          <div
            className="flex flex-wrap items-center gap-2 text-[11px]"
            data-testid="orch-classification"
            data-mode={classification.killSwitch ? "kill" : classification.mode}
          >
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
            <span className="text-white/55">
              {classification.killSwitch
                ? "стоп из текста сообщения"
                : humanDetail(classification.detail, "")}
              {classification.deterministic ? " · по правилу" : ""}
            </span>
          </div>
        ) : null}
        {chat.sendError ? (
          <p className="text-[11px] text-destructive" role="alert">
            {chat.sendError}
          </p>
        ) : null}
        <div className="flex items-end gap-2">
          <Textarea
            data-testid="orch-composer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSend();
              }
            }}
            placeholder={placeholder ?? "Например: что с проектом? почему упал шаг? запусти видео"}
            className={cn("flex-1 resize-none py-2 text-xs", compact ? "min-h-[40px]" : "min-h-[44px]")}
            rows={compact ? 1 : 2}
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
        {compact && orchTaskModeEnabled() ? (
          <label className="flex items-center gap-1.5 text-[11px] text-white/55" title="Встаёт в очередь за текущей и не прерывает её.">
            <input
              type="checkbox"
              data-testid="orch-mode-task"
              checked={taskMode}
              onChange={(event) => setTaskMode(event.target.checked)}
            />
            Новая задача
            <span className="text-[10px] text-white/35" data-testid="orch-mode-task-hint">
              — встанет в очередь за текущей
            </span>
          </label>
        ) : null}
        {extra}
      </div>
    </>
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

export function AgentsRow({ agents }: { agents: AgentView[] }) {
  return (
    <div className="grid grid-cols-3 gap-1 border-b border-white/[0.06] px-2.5 py-1.5" data-testid="orch-agents-row" aria-label="Агенты">
      {agents.map((agent) => {
        const name = AGENT_RU[agent.role] ?? "Агент";
        const status = agentStatusRu(agent.status);
        return (
          <span
            key={agent.role}
            data-testid={`orch-agent-${agent.role}`}
            data-status={agent.status}
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

export function AgentsGrid({ agents, compact = false }: { agents: AgentView[]; compact?: boolean }) {
  return (
    <div className={cn("border-b border-white/[0.06]", compact ? "px-3 py-2" : "px-4 py-3")}>
      <div className="text-[11px] font-medium text-white/45">Агенты</div>
      <div className={cn("mt-2 grid gap-2", compact ? "grid-cols-3" : "grid-cols-2")}>
        {agents.map((agent) => (
          <div
            key={agent.role}
            data-testid={`orch-agent-${agent.role}`}
            data-status={agent.status}
            className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1.5"
          >
            <div className="truncate text-[11px] font-medium text-white/80">{AGENT_RU[agent.role] ?? "Агент"}</div>
            <div className="mt-0.5 text-[10px] text-white/45">{agentStatusRu(agent.status)}</div>
            {!compact && agent.detail && isHumanText(agent.detail) ? (
              <div className="mt-1 truncate text-[10px] text-white/35">{agent.detail}</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function stepMark(step: StepView): string {
  if (step.status === "running") return "…";
  if (step.status === "done") return "●";
  if (step.status === "failed") return "✕";
  if (step.status === "skipped") return "–";
  return "○";
}

const STEP_STATUS_RU: Record<StepView["status"], string> = {
  running: "идёт",
  done: "готово",
  failed: "ошибка",
  skipped: "пропущен",
};

export function StepRow({ step, titles }: { step: StepView; titles: Record<string, string> }) {
  const label = actionLabel(step.action, titles);
  const title = isHumanText(step.title) ? step.title : label || step.title;
  return (
    <li className="text-[11px] text-white/75" data-testid="orch-step" data-step-status={step.status}>
      <span className={cn("text-white/40", step.status === "failed" && "text-red-300")}>{stepMark(step)}</span> {title}
      <span className="text-white/40"> · {STEP_STATUS_RU[step.status]}</span>
      {step.paid ? " · платный" : ""}
      {step.note ? ` · ${humanDetail(step.note, step.status === "failed" ? "шаг не удался" : "")}` : ""}
      {step.status === "running" ? <StepProgress progress={step.progress} /> : null}
      {step.node ? <NodeDefMarks node={step.node} /> : null}
    </li>
  );
}

function StepProgress({ progress }: { progress: number | null }) {
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded bg-white/[0.06]" data-testid="orch-step-progress">
      {progress == null ? (
        <div className="h-full w-1/3 animate-pulse rounded bg-sky-400/50" />
      ) : (
        <div className="h-full rounded bg-sky-400/70 transition-all" style={{ width: `${Math.round(progress * 100)}%` }} />
      )}
    </div>
  );
}

export function TaskList({
  tasks,
  titles,
  heading = true,
}: {
  tasks: TaskView[];
  titles: Record<string, string>;
  heading?: boolean;
}) {
  return (
    <>
      {heading ? <div className="text-[11px] font-medium text-white/45">Задачи</div> : null}
      {tasks.length === 0 ? (
        <p className="mt-2 text-[11px] text-white/35">Шаги и ход выполнения появятся вместе с задачей.</p>
      ) : (
        tasks.map((item) => (
          <div key={item.id} className="mt-3 rounded-xl border border-white/[0.08] bg-card/80 p-3">
            <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
              <div className="min-w-0 break-words text-xs font-semibold text-white/90">{item.title}</div>
              <Badge variant={statusBadgeVariant(item.status)} className="shrink-0 whitespace-nowrap normal-case tracking-normal">
                {TASK_STATUS_LABEL[item.status] ?? "в работе"}
              </Badge>
            </div>
            {item.summary ? (
              <p className="mt-2 text-[11px] text-white/70" data-testid="orch-task-summary">
                {item.summary}
              </p>
            ) : null}
            {item.error ? (
              <p className="mt-2 text-[11px] text-destructive" data-testid="orch-task-error">
                {taskErrorText(item.error, item.reason)}
              </p>
            ) : null}
            {item.plan.length > 0 ? <PlanList steps={item.plan} titles={titles} /> : null}
            <ul className="mt-2 space-y-1.5">
              {item.steps.map((step) => (
                <StepRow key={step.id} step={step} titles={titles} />
              ))}
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
        ))
      )}
    </>
  );
}

function TokenGate({
  error,
  value,
  onChange,
  onSubmit,
}: {
  error: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-6" data-testid="orch-token-gate">
      <Card className="w-full max-w-md border-white/10 bg-card/95">
        <CardHeader>
          <CardTitle className="text-base">Доступ к оркестратору</CardTitle>
          <CardDescription>
            Сервер не пустил по входу студии. Если администратор выдал отдельный токен оркестратора, введите его один
            раз — он сохранится в этом браузере.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? (
            <p className="text-xs text-destructive" data-testid="orch-token-error">
              {error}
            </p>
          ) : null}
          <Input
            data-testid="orch-token-input"
            type="password"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="токен оркестратора"
            autoComplete="off"
            onKeyDown={(event) => {
              if (event.key === "Enter") onSubmit();
            }}
          />
          <Button type="button" data-testid="orch-token-submit" onClick={onSubmit} disabled={!value.trim()}>
            Войти
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function formatCost(value: number, paid: boolean): string {
  if (value > 0) return `~${moneyCents(value)}`;
  return paid ? "цена неизвестна" : "бесплатно";
}

/** Точка сохранения плана: задача продолжит с неё после сбоя. Слово «чекпоинт» человеку не нужно. */
function checkpointRu(summary: string): string {
  const text = summary.trim();
  if (!text || text === "план" || text === "чекпоинт") return "План сохранён: после сбоя задача продолжит с этого места";
  return `Сохранено: ${text}`;
}

export function PlanList({ steps, titles }: { steps: PlanStepView[]; titles: Record<string, string> }) {
  const groups = groupPlanSteps(steps);
  const hideMoney = useHideMoney();
  return (
    <div className="mt-2" data-testid="orch-plan">
      <div className="text-[11px] font-medium text-white/45">План</div>
      {groups.map((group) => (
        <div
          key={group.key}
          className="mt-1.5"
          data-testid={group.parallel ? "orch-plan-parallel" : "orch-plan-serial"}
          data-parallel-group={group.group || undefined}
        >
          {group.parallel ? <div className="text-[10px] text-info">параллельно · {group.group}</div> : null}
          {group.steps.map((step, index) => {
            const label = actionLabel(step.action, titles);
            return (
              <div key={`${group.key}-${index}`} data-testid="orch-plan-step" className="text-[11px] text-white/75">
                {label || "Действие студии"}
                <span data-testid="orch-plan-risk" data-risk={step.risk}>
                  {step.risk === "none" ? " · без риска" : ` · риск ${riskRu(step.risk)}`}
                </span>
                {hideMoney ? null : (
                  <span data-testid="orch-plan-cost"> · {formatCost(step.estimatedCost, step.paid)}</span>
                )}
                {step.paid ? <span className="text-amber-200/80"> · платный</span> : null}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function NodeDefMarks({ node }: { node: NodeDef }) {
  const fromDb = node.source === "db";
  if (!fromDb && !node.stale_base) return null;
  const version = typeof node.db_version === "number" ? node.db_version : null;
  return (
    <div className="mt-1 flex flex-col items-start gap-1">
      {fromDb ? (
        <Badge data-testid="orch-node-badge" variant="info" className="normal-case tracking-normal">
          {version != null ? `из БД, v${version}` : "из БД"}
        </Badge>
      ) : null}
      {node.stale_base ? (
        <p data-testid="orch-node-stale" className="text-[10px] leading-snug text-warning">
          git-шаблон изменился после версии в БД
        </p>
      ) : null}
    </div>
  );
}

function NodeResultCard({ step, titles }: { step: StepView; titles: Record<string, string> }) {
  if (!step.node) return null;
  const label = actionLabel(step.action, titles) || "Нода";
  return (
    <div data-testid="orch-node-result" className="mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
      <div className="text-[11px] text-white/70">
        {label}
        {nodeKeyRu(step.node.node_key) ? ` · ${nodeKeyRu(step.node.node_key)}` : ""}
      </div>
      <NodeDefMarks node={step.node} />
    </div>
  );
}

function projectsWord(count: number): string {
  return pluralRu(count, "проект", "проекта", "проектов");
}

export function ApprovalCard({
  approval,
  titles,
  pending,
  error,
  onApprove,
  onReject,
  onClose,
}: {
  approval: ApprovalView;
  titles: Record<string, string>;
  pending: boolean;
  error: string;
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}) {
  const copy = BLOCK_COPY[approval.reason];
  const terminal = isTerminalBlock(approval.reason);
  const title = copy?.title || approval.title || "Нужно подтверждение";
  const subtitle = copy && approval.title && approval.title !== copy.title && isHumanText(approval.title) ? approval.title : "";
  const body =
    copy?.body ||
    (isHumanText(approval.reason)
      ? approval.reason
      : approval.reason
        ? `Задача ждёт вашего решения. Причина: ${validatorRu(approval.reason)}.`
        : "");
  const label = actionLabel(approval.action, titles);
  const shownLabel = label && label !== title && label !== subtitle ? label : "";
  const hideMoney = useHideMoney();
  const price = cardPrice({
    costEstimate: approval.costEstimate,
    costCredits: approval.costCredits,
    costText: approval.costText,
    paid: approval.steps.some((step) => step.paid),
    effects: approval.effects,
  });
  const { main: mainSteps, reads } = splitCardSteps(approval.steps);
  const blocked = approval.blocked === true;
  const tone = RISK_TONE[approval.risk] ?? "muted";
  return (
    <div
      data-testid="orch-approval"
      data-approval-id={approval.serverId ? approval.id : undefined}
      data-terminal={terminal ? "true" : "false"}
      data-blocked={blocked ? "true" : undefined}
      data-action={approval.action || undefined}
      className={cn(
        "mb-3 scroll-mt-2 rounded-xl border px-3 py-3 transition-shadow data-[flash=true]:ring-2 data-[flash=true]:ring-amber-300/70",
        terminal ? "border-destructive/40 bg-destructive/10" : "border-warning/40 bg-warning/10",
      )}
    >
      <div className="text-xs font-semibold text-white" data-testid={copy?.testId}>
        {title}
      </div>
      {subtitle ? (
        <div className="mt-0.5 text-[12px] text-white/90" data-testid="orch-approval-subject">
          {subtitle}
        </div>
      ) : null}
      {/* Почему запуск не пройдёт — первым делом, до списков шагов: иначе причина уезжает под край окна. */}
      {blocked || (approval.blockReasons?.length ?? 0) > 0 ? (
        <div className="mt-2 rounded-md border border-red-400/60 bg-red-500/15 px-2 py-1.5" data-testid="orch-approval-blocked">
          <div className="text-[11px] font-medium text-red-100">
            {blocked ? "Запуск не пройдёт, подтвердить нельзя" : "Проверка перед запуском"}
          </div>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-red-50/90">
            {(approval.blockReasons ?? []).map((text) => (
              <li key={text}>{blockReasonText(text)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {body && !blocked ? <p className="mt-1 text-[11px] leading-relaxed text-white/70">{body}</p> : null}
      {approval.groupProjects && approval.groupProjects.length > 1 ? (
        <p className="mt-1 text-[11px] text-white/85" data-testid="orch-approval-group">
          Одно подтверждение на {approval.groupProjects.length} {projectsWord(approval.groupProjects.length)}:{" "}
          {approval.groupProjects.map((id) => `#${id}`).join(", ")}
        </p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        {shownLabel ? <span className="text-white/80">{shownLabel}</span> : null}
        {approval.risk ? (
          <Badge variant={tone} className="normal-case tracking-normal" data-testid="orch-approval-risk">
            риск: {riskRu(approval.risk)}
          </Badge>
        ) : null}
        {terminal ? null : hideMoney ? (
          <span className="text-white/60" data-testid="orch-approval-cost" data-hidden="true">
            {MEMBER_PRICE_TEXT}
          </span>
        ) : price.unknown ? (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-amber-400/50 bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-100"
            data-testid="orch-approval-cost"
            data-unknown="true"
          >
            <CircleAlert className="h-3 w-3 shrink-0" />
            {price.text}
          </span>
        ) : (
          <span className="text-white/70" data-testid="orch-approval-cost" data-unknown="false">
            цена: {price.text}
          </span>
        )}
      </div>
      {mainSteps.length > 0 ? (
        <div className="mt-2 rounded-md border border-white/10 bg-black/20 px-2 py-1.5" data-testid="orch-approval-steps">
          <div className="text-[11px] font-medium text-white/85">Что будет сделано</div>
          <ol className="mt-0.5 list-decimal space-y-0.5 pl-4 text-[11px] text-white/80">
            {mainSteps.map((step, index) => (
              <li key={`${index}-${step.action}`} data-testid="orch-approval-step" data-action={step.action || undefined}>
                <span className="text-white/90">{step.title}</span>
                {step.paramsText ? <span className="text-white/60"> — {step.paramsText}</span> : null}
                {step.paid ? <span className="text-amber-200"> · платно</span> : null}
                {step.risk ? <span className="text-white/45"> · риск: {riskRu(step.risk)}</span> : null}
              </li>
            ))}
          </ol>
          {reads ? (
            <p className="mt-0.5 text-[10px] text-white/50" data-testid="orch-approval-reads">
              Перед этим только посмотрю, ничего не меняя: {reads}.
            </p>
          ) : null}
        </div>
      ) : null}
      {approval.warnings.length > 0 ? (
        <div className="mt-2 rounded-md border border-amber-400/40 bg-amber-500/10 px-2 py-1.5" data-testid="orch-approval-warnings">
          <div className="text-[11px] font-medium text-amber-100">Предупреждения</div>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-amber-50/90">
            {approval.warnings.map((text) => (
              <li key={text}>{roundMoneyText(text)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {approval.effects.length > 0 ? (
        <div className="mt-2 rounded-md border border-red-400/40 bg-red-500/10 px-2 py-1.5" data-testid="orch-approval-effects">
          <div className="text-[11px] font-medium text-red-100" data-testid="orch-approval-effects-title">
            {approval.effectsReversible ? "Что изменится" : "Что изменится безвозвратно"}
          </div>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-red-50/90">
            {approval.effects.map((text) => (
              <li key={text}>{roundMoneyText(text)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-2">
        {terminal ? (
          <Button type="button" size="sm" variant="outline" data-testid="orch-block-close" onClick={onClose}>
            Закрыть
          </Button>
        ) : approval.serverId ? (
          <>
            <Button
              type="button"
              size="sm"
              variant={blocked ? "secondary" : "default"}
              className={blocked ? "text-white/40" : undefined}
              data-testid="orch-approve"
              disabled={pending || blocked}
              onClick={onApprove}
            >
              {pending ? "Записываю…" : blocked ? "Подтвердить нельзя" : "Подтвердить"}
            </Button>
            <Button type="button" size="sm" variant="outline" data-testid="orch-reject" disabled={pending} onClick={onReject}>
              Отклонить
            </Button>
          </>
        ) : (
          <p className="text-[11px] text-white/55" data-testid="orch-approval-noid">
            Сервер не прислал номер подтверждения, поэтому подтвердить отсюда нельзя.
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

