/**
 * Сведение потока OrchEvent в состояние экрана.
 * Дедуп по seq делает вызывающий код до dispatch.
 */

import {
  AGENT_ROLES,
  BLOCK_OUTCOMES,
  type AgentRole,
  type AgentStatusPayload,
  type ApprovalPayload,
  type CheckpointPayload,
  type ClassificationPayload,
  type MessageMode,
  type NodeDef,
  type OrchEvent,
  type StepPayload,
  TASK_STATUSES,
  type TaskStatus,
  type ValidatorResultPayload,
} from "./orchestratorTypes";

const NODE_RESULT_ACTIONS = new Set([
  "get_node",
  "resolve_node",
  "edit_node",
  "edit_node_prompt",
  "edit_node_params",
]);

export function isNodeResultAction(action: string): boolean {
  return NODE_RESULT_ACTIONS.has(action);
}

function optNum(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function readNodeDef(value: unknown): NodeDef | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (raw.source !== "git" && raw.source !== "db") return null;
  const params = raw.params && typeof raw.params === "object" ? (raw.params as Record<string, unknown>) : {};
  return {
    node_key: typeof raw.node_key === "string" ? raw.node_key : "",
    prompt_template: typeof raw.prompt_template === "string" ? raw.prompt_template : "",
    params,
    source: raw.source,
    version: optNum(raw.version) ?? 0,
    db_version: optNum(raw.db_version),
    git_sha256: typeof raw.git_sha256 === "string" ? raw.git_sha256 : null,
    stale_base: raw.stale_base === true,
  };
}

export type ChatMessageKind = "text" | "done" | "failed";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  taskId: string | null;
  seq: number | null;
  messageId: string | null;
  streaming: boolean;
  final: boolean;
  kind: ChatMessageKind;
  summary?: boolean;
  detail?: boolean;
}

export interface AgentView {
  role: AgentRole;
  status: string;
  detail: string;
}

export interface StepView {
  id: string;
  title: string;
  action: string;
  paid: boolean;
  status: "running" | "done" | "failed" | "skipped";
  note: string;
  node: NodeDef | null;
  progress: number | null;
  result: string;
}

/** Шаг из CheckpointData.plan. Поля как у PlanStep. */
export interface PlanStepView {
  action: string;
  expected: string;
  paid: boolean;
  mutating: boolean;
  risk: string;
  parallelGroup: string | null;
  estimatedCost: number;
}

export interface PlanGroup {
  key: string;
  parallel: boolean;
  group: string;
  steps: PlanStepView[];
}

const BLOCKING_PLAN_RISK = new Set(["medium", "high"]);

export function readPlanSteps(value: unknown): PlanStepView[] {
  if (!Array.isArray(value)) return [];
  const steps: PlanStepView[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const raw = item as Record<string, unknown>;
    const action = typeof raw.action === "string" ? raw.action : "";
    if (!action) continue;
    const group = raw.parallel_group;
    const cost = raw.estimated_cost;
    steps.push({
      action,
      expected: typeof raw.expected === "string" ? raw.expected : "",
      paid: raw.paid === true,
      mutating: raw.mutating === true,
      risk: typeof raw.risk === "string" && raw.risk ? raw.risk : "none",
      parallelGroup: typeof group === "string" && group ? group : null,
      estimatedCost: typeof cost === "number" && Number.isFinite(cost) ? cost : 0,
    });
  }
  return steps;
}

/** Read-only шаг можно класть в параллельную группу: не mutating, не paid, риск не medium/high. */
export function isParallelReadonly(step: PlanStepView): boolean {
  return Boolean(step.parallelGroup) && !step.mutating && !step.paid && !BLOCKING_PLAN_RISK.has(step.risk);
}

export function groupPlanSteps(steps: PlanStepView[]): PlanGroup[] {
  const groups: PlanGroup[] = [];
  for (const [index, step] of steps.entries()) {
    const last = groups[groups.length - 1];
    if (isParallelReadonly(step) && last?.parallel && last.group === step.parallelGroup) {
      last.steps.push(step);
      continue;
    }
    groups.push({
      key: `${index}-${step.action}`,
      parallel: isParallelReadonly(step),
      group: step.parallelGroup ?? "",
      steps: [step],
    });
  }
  return groups.map((group) => ({ ...group, parallel: group.parallel && group.steps.length > 1 }));
}

export interface CheckpointView {
  id: string;
  stepId: string;
  summary: string;
  spend: number | null;
}

export interface ValidatorView {
  name: string;
  pass: boolean;
  details: string;
  blocking: boolean;
}

export interface ApprovalView {
  id: string;
  /** id пришёл в payload сервера. Выдуманный ключ карточку не закрывает. */
  serverId: boolean;
  taskId: string | null;
  title: string;
  reason: string;
  risk: string;
  action: string;
  costEstimate: number | null;
  costCredits: { low: number; high: number } | null;
  warnings: string[];
  effects: string[];
  steps: ApprovalStepView[];
  costText: string;
  /** Проекты групповой карточки: одно нажатие одобряет шаг в каждом. */
  groupProjects?: number[];
  /** Проверка перед запуском уже нашла причину, по которой шаг не пройдёт. */
  blocked?: boolean;
  blockReasons?: string[];
  /** Последствия обратимы (откат правки): заголовок без «безвозвратно». */
  effectsReversible?: boolean;
  resolved: "approved" | "rejected" | "closed" | null;
  seq?: number;
}

export interface ApprovalStepView {
  title: string;
  action: string;
  paramsText: string;
  paid: boolean;
  risk: string;
}

function readApprovalSteps(value: unknown): ApprovalStepView[] {
  if (!Array.isArray(value)) return [];
  const out: ApprovalStepView[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const title = typeof row.title === "string" ? row.title.trim() : "";
    if (!title) continue;
    out.push({
      title,
      action: typeof row.action === "string" ? row.action : "",
      paramsText: typeof row.params_text === "string" ? row.params_text.trim() : "",
      paid: row.paid === true,
      risk: typeof row.risk === "string" ? row.risk : "",
    });
  }
  return out;
}

function readGroupProjects(value: unknown): number[] {
  if (!value || typeof value !== "object") return [];
  const projects = (value as Record<string, unknown>).projects;
  if (!Array.isArray(projects)) return [];
  return projects.filter((item): item is number => typeof item === "number" && Number.isInteger(item) && item > 0);
}

function textList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim()) out.push(item.trim());
    else if (item && typeof item === "object") {
      const text = (item as Record<string, unknown>).text;
      if (typeof text === "string" && text.trim()) out.push(text.trim());
    }
  }
  return out;
}

function unique(items: string[]): string[] {
  return [...new Set(items)];
}

const WARNING_PREFIX = "Внимание: ";

export function readApprovalExtras(
  body: Record<string, unknown>,
): Pick<
  ApprovalView,
  | "warnings"
  | "effects"
  | "costCredits"
  | "steps"
  | "costText"
  | "groupProjects"
  | "blocked"
  | "blockReasons"
  | "effectsReversible"
> {
  const preview = body.preview && typeof body.preview === "object" ? (body.preview as Record<string, unknown>) : {};
  const warnings = unique([...textList(body.warnings), ...textList(preview.warnings)]);
  const effects = unique(
    [...textList(body.destructive_effects), ...textList(preview.destructive_effects)].filter(
      (text) => !(text.startsWith(WARNING_PREFIX) && warnings.includes(text.slice(WARNING_PREFIX.length))),
    ),
  );
  const cost = preview.cost && typeof preview.cost === "object" ? (preview.cost as Record<string, unknown>) : null;
  const low = typeof cost?.low === "number" ? cost.low : null;
  const high = typeof cost?.high === "number" ? cost.high : null;
  return {
    warnings,
    effects,
    costCredits: low != null && high != null && high > 0 ? { low, high } : null,
    steps: readApprovalSteps(body.steps),
    costText: typeof body.cost_text === "string" ? body.cost_text.trim() : "",
    groupProjects: readGroupProjects(body.group),
    blocked: body.blocked === true,
    blockReasons: unique([...textList(body.preview_errors), ...textList(preview.preview_errors)]),
    effectsReversible: body.effects_reversible === true,
  };
}

function readProgress(value: unknown): number | null {
  const n = optNum(value);
  if (n == null || n < 0) return null;
  const share = n > 1 ? n / 100 : n;
  return Math.max(0, Math.min(1, share));
}

export interface TaskView {
  id: string;
  title: string;
  status: TaskStatus;
  steps: StepView[];
  checkpoints: CheckpointView[];
  validators: ValidatorView[];
  summary: string;
  error: string;
  reason: string;
  plan: PlanStepView[];
  lastSeq: number;
  stepSeq: number;
  waitSeq?: number;
  decidedSeq?: number;
}

export interface AckView {
  echo: string;
  text: string;
  seq: number;
}

export interface ClassificationView {
  mode: MessageMode;
  deterministic: boolean;
  detail: string;
  seq: number;
  killSwitch: boolean;
}

export type KillPhase = "stopping" | "stopped" | "error" | "unconfirmed";

export interface KillNoticeView {
  text: string;
  /** Платный шаг останавливается на границе. UI не ищет эту фразу в тексте. */
  paidBoundary: boolean;
  /** stopping — запрос ушёл; stopped — stop_key_set; error — отказ; unconfirmed — задача отменена без ключа. */
  phase: KillPhase;
  /** Задача, которую останавливали. Чужой task_id баннер не двигает. */
  taskId: string | null;
  sessionId: string | null;
}

export interface OrchScreenState {
  messages: ChatMessage[];
  agents: AgentView[];
  tasks: TaskView[];
  approvals: ApprovalView[];
  ack: AckView | null;
  classification: ClassificationView | null;
  killNotice: KillNoticeView | null;
}

export type OrchScreenAction =
  | { type: "event"; event: OrchEvent }
  | { type: "user-message"; id: string; text: string; taskId: string | null }
  | { type: "user-message-unsent"; id: string }
  | {
      type: "kill-notice";
      detail: string;
      paidBoundary: boolean;
      phase: KillPhase;
      taskId?: string | null;
      sessionId?: string | null;
    }
  | { type: "approval-resolved"; id: string; decision: "approved" | "rejected" | "closed" }
  | { type: "reset" };

const IDLE_AGENTS: AgentView[] = AGENT_ROLES.map((role) => ({
  role,
  status: "idle",
  detail: "",
}));

export function initialOrchScreen(): OrchScreenState {
  return {
    messages: [],
    agents: IDLE_AGENTS.map((agent) => ({ ...agent })),
    tasks: [],
    approvals: [],
    ack: null,
    classification: null,
    killNotice: null,
  };
}

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "в очереди",
  planning: "планирование",
  critic_review: "ревью критика",
  executing: "выполнение",
  paused: "пауза",
  waiting_approval: "ждёт подтверждения",
  done: "готово",
  failed: "ошибка",
  cancelled: "отменена",
};

export const AGENT_LABEL: Record<AgentRole, string> = {
  supervisor: "supervisor",
  planner: "planner",
  critic: "critic",
  executor: "executor",
  classifier: "classifier",
  summarizer: "summarizer",
};

function asRecord(payload: unknown): Record<string, unknown> {
  if (payload && typeof payload === "object") return payload as Record<string, unknown>;
  return {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** WS3: task_id — integer. В стейте карточки ключ строковый. */
function asTaskStatus(value: string): TaskStatus | null {
  return (TASK_STATUSES as readonly string[]).includes(value) ? (value as TaskStatus) : null;
}

const TERMINAL_TASK = new Set<TaskStatus>(["done", "failed", "cancelled"]);

/** Исход из BLOCK_OUTCOMES. Причины нет в таблице — null, UI не падает и не ставит failed сам. */
export function blockOutcome(reason: string): TaskStatus | null {
  if (Object.prototype.hasOwnProperty.call(BLOCK_OUTCOMES, reason)) {
    return BLOCK_OUTCOMES[reason as keyof typeof BLOCK_OUTCOMES];
  }
  return null;
}

export function isTerminalBlock(reason: string): boolean {
  const outcome = blockOutcome(reason);
  return outcome != null && TERMINAL_TASK.has(outcome);
}

function taskKey(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value) return value;
  return null;
}

/** Непустая строка. Число и пустота — не id. */
export function serverApprovalId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/**
 * Id решения из шины ws3-r3.3: `ApprovalEvent.change_id`.
 * Явный `approval_id` оставлен для моков, у которых это поле уже есть.
 * Вложенный `payload` — тот же объект, если событие завернуло ApprovalEvent.
 */
export function approvalServerKey(body: Record<string, unknown>): string | null {
  const direct = serverApprovalId(body.approval_id) ?? serverApprovalId(body.change_id);
  if (direct) return direct;
  const nested = body.payload;
  if (!nested || typeof nested !== "object") return null;
  const inner = nested as Record<string, unknown>;
  return serverApprovalId(inner.approval_id) ?? serverApprovalId(inner.change_id);
}

function approvalDecision(value: unknown): "approved" | "rejected" | null {
  if (value === "approved" || value === "approve") return "approved";
  if (value === "rejected" || value === "reject") return "rejected";
  return null;
}

function closeServerApproval(
  state: OrchScreenState,
  id: string,
  decision: "approved" | "rejected" | "closed",
): void {
  const item = state.approvals.find((entry) => entry.id === id && entry.serverId);
  if (!item || item.resolved) return;
  item.resolved = decision;
}

function isTerminalKillStatus(value: string): boolean {
  return value === "cancelled" || value === "killed";
}

function readStopKey(payload: Record<string, unknown>): boolean | null {
  if (payload.stop_key_set === true) return true;
  if (payload.stop_key_set === false) return false;
  return null;
}

function markKill(
  next: OrchScreenState,
  phase: KillPhase,
  text: string,
  paidBoundary: boolean,
  target?: { taskId: string | null; sessionId: string | null },
): void {
  const current = next.killNotice;
  if (current?.phase === "stopped" && phase !== "stopped") return;
  if (current?.phase === "error" && phase !== "error" && phase !== "stopped") return;
  next.killNotice = {
    phase,
    text,
    paidBoundary,
    taskId: target?.taskId ?? current?.taskId ?? null,
    sessionId: target?.sessionId ?? current?.sessionId ?? null,
  };
}

/** «Остановлено» только при stop_key_set=true. Иначе запрос или ошибка. */
function noteKillRequested(
  next: OrchScreenState,
  payload: Record<string, unknown>,
  target: { taskId: string | null; sessionId: string | null },
): void {
  const stopKey = readStopKey(payload);
  const detail = str(payload.detail);
  if (stopKey === true) {
    markKill(next, "stopped", detail, true, target);
    return;
  }
  if (stopKey === false) {
    markKill(next, "error", detail || "Стоп-ключ не установлен", false, target);
    return;
  }
  if (next.killNotice?.phase === "stopped" || next.killNotice?.phase === "error") return;
  markKill(next, "stopping", "", true, target);
}

/** cancelled/killed той же задачи и сессии. Шаг и чужая задача сюда не попадают. Ошибку ключа не затирает. */
function noteTaskCancelled(next: OrchScreenState, event: OrchEvent): void {
  const notice = next.killNotice;
  if (!notice || notice.phase !== "stopping") return;
  if (!notice.taskId || !notice.sessionId) return;
  if (taskKey(event.task_id) !== notice.taskId) return;
  if (event.session_id !== notice.sessionId) return;
  next.killNotice = {
    phase: "unconfirmed",
    text: "Задача отменена, стоп-ключ не подтверждён.",
    paidBoundary: false,
    taskId: notice.taskId,
    sessionId: notice.sessionId,
  };
}

function absorbLlmSignal(state: OrchScreenState, taskId: string | null, payload: Record<string, unknown>, seq: number): void {
  const status = asTaskStatus(str(payload.task_status));
  if (taskId && status) {
    ensureTask(state.tasks, taskId).status = status;
  }
  const approval = payload.approval;
  if (!approval || typeof approval !== "object") return;
  const body = approval as Record<string, unknown>;
  const reason = str(body.reason);
  if (!reason) return;
  const fromServer = approvalServerKey(body);
  const id = fromServer ?? `local-llm-${seq}`;
  if (!state.approvals.some((item) => item.id === id)) {
    state.approvals.push({
      id,
      serverId: fromServer != null,
      taskId: taskKey(body.task_id) || taskId,
      title: str(body.title, "Нужно подтверждение"),
      reason,
      risk: str(body.risk),
      action: str(body.action),
      costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
      ...readApprovalExtras(body),
      resolved: null,
    });
  }
  if (taskId && isTerminalBlock(reason)) {
    const task = ensureTask(state.tasks, taskId);
    task.status = blockOutcome(reason) ?? "failed";
    task.error = reason;
  } else if (taskId && (status === "waiting_approval" || blockOutcome(reason) === "waiting_approval" || reason === "budget_exhausted")) {
    ensureTask(state.tasks, taskId).status = "waiting_approval";
  }
}

function ensureTask(tasks: TaskView[], id: string, title?: string, seq?: number): TaskView {
  let task = tasks.find((item) => item.id === id);
  if (!task) {
    task = {
      id,
      title: title || "Задача",
      status: "queued",
      steps: [],
      checkpoints: [],
      validators: [],
      summary: "",
      error: "",
      reason: "",
      plan: [],
      lastSeq: 0,
      stepSeq: 0,
      waitSeq: 0,
      decidedSeq: 0,
    };
    tasks.push(task);
  } else if (title && task.title === "Задача") {
    task.title = title;
  }
  if (seq != null && seq > task.lastSeq) task.lastSeq = seq;
  return task;
}

export { ackAnswered, currentTask } from "./orchestratorBadge";

const PENDING_ORDER = Number.MAX_SAFE_INTEGER;

function messageOrder(message: ChatMessage): number {
  return message.seq ?? PENDING_ORDER;
}

function sortMessages(messages: ChatMessage[]): void {
  messages.sort((a, b) => messageOrder(a) - messageOrder(b));
}

function messageKey(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value.trim()) return value.trim();
  return null;
}

function newAssistant(
  state: OrchScreenState,
  seq: number,
  taskId: string | null,
  messageId: string | null,
  text: string,
  final: boolean,
): ChatMessage {
  const message: ChatMessage = {
    id: messageId ? `asst-m${messageId}` : `asst-${seq}`,
    role: "assistant",
    text,
    taskId,
    seq,
    messageId,
    streaming: !final,
    final,
    kind: "text",
  };
  state.messages.push(message);
  sortMessages(state.messages);
  return message;
}

function assistantById(state: OrchScreenState, messageId: string): ChatMessage | undefined {
  return state.messages.find((item) => item.role === "assistant" && item.messageId === messageId);
}

function openAssistant(state: OrchScreenState, taskId: string | null): ChatMessage | undefined {
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const message = state.messages[i];
    if (message && message.role === "assistant" && message.kind === "text" && message.streaming && message.taskId === taskId) {
      return message;
    }
  }
  return undefined;
}

function startAssistant(state: OrchScreenState, seq: number, taskId: string | null, messageId: string | null): void {
  if (!messageId) return;
  const found = assistantById(state, messageId);
  if (found) return;
  newAssistant(state, seq, taskId, messageId, "", false);
}

function appendToken(state: OrchScreenState, seq: number, taskId: string | null, text: string, messageId: string | null): void {
  if (!text) return;
  const target = messageId ? assistantById(state, messageId) : openAssistant(state, taskId);
  if (!target) {
    newAssistant(state, seq, taskId, messageId, text, false);
    return;
  }
  if (target.final) return;
  target.text += text;
}

function endAssistant(state: OrchScreenState, messageId: string | null): void {
  if (!messageId) return;
  const found = assistantById(state, messageId);
  if (found) found.streaming = false;
}

function lastAssistantBefore(state: OrchScreenState, seq: number): ChatMessage | undefined {
  let found: ChatMessage | undefined;
  for (const message of state.messages) {
    if (message.seq == null || message.seq >= seq) continue;
    if (message.role === "user") found = undefined;
    else if (message.kind === "text") found = message;
  }
  return found;
}

function finishAssistant(
  state: OrchScreenState,
  seq: number,
  taskId: string | null,
  text: string,
  messageId: string | null,
  summary = false,
): void {
  const mark = (message: ChatMessage) => {
    if (summary) message.summary = true;
  };
  if (messageId) {
    const found = assistantById(state, messageId);
    if (found) {
      found.text = text;
      found.final = true;
      found.streaming = false;
      mark(found);
      return;
    }
    mark(newAssistant(state, seq, taskId, messageId, text, true));
    return;
  }
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const message = state.messages[i];
    if (!message || message.seq == null || message.seq > seq) continue;
    if (message.role === "user") break;
    if (message.kind !== "text" || message.final || message.taskId !== taskId) continue;
    message.text = text;
    message.final = true;
    message.streaming = false;
    mark(message);
    return;
  }
  const previous = lastAssistantBefore(state, seq);
  if (previous && previous.final && previous.taskId === taskId && previous.text === text) {
    mark(previous);
    return;
  }
  mark(newAssistant(state, seq, taskId, null, text, true));
}

function placeDetail(state: OrchScreenState, seq: number, taskId: string | null, text: string): void {
  if (state.messages.some((item) => item.id === `asst-${seq}`)) return;
  newAssistant(state, seq, taskId, null, text, true).detail = true;
}

function placeUser(state: OrchScreenState, seq: number, taskId: string | null, text: string): void {
  if (state.messages.some((item) => item.role === "user" && item.seq === seq)) return;
  const pending = state.messages.find(
    (item) => item.role === "user" && (item.seq == null || !Number.isInteger(item.seq)) && (!text || item.text === text),
  );
  if (pending) {
    pending.seq = seq;
    if (taskId && !pending.taskId) pending.taskId = taskId;
  } else if (text) {
    state.messages.push({
      id: `user-s${seq}`,
      role: "user",
      text,
      taskId,
      seq,
      messageId: null,
      streaming: false,
      final: true,
      kind: "text",
    });
  }
  sortMessages(state.messages);
}

function closeTaskStreams(state: OrchScreenState, taskId: string): void {
  for (const message of state.messages) {
    if (message.taskId === taskId && message.streaming) message.streaming = false;
  }
}

function placeOutcome(state: OrchScreenState, seq: number, taskId: string, kind: "done" | "failed"): void {
  closeTaskStreams(state, taskId);
  const id = `outcome-${taskId}`;
  const found = state.messages.find((item) => item.id === id);
  if (found) {
    found.kind = kind;
    found.seq = seq;
  } else {
    state.messages.push({
      id,
      role: "assistant",
      text: "",
      taskId,
      seq,
      messageId: null,
      streaming: false,
      final: true,
      kind,
    });
  }
  sortMessages(state.messages);
}

export function outcomeCovered(messages: ChatMessage[], task: TaskView): boolean {
  let afterUser = false;
  for (const item of messages) {
    if (item.seq == null || item.seq <= task.stepSeq) continue;
    if (item.role === "user") {
      afterUser = true;
      continue;
    }
    if (afterUser) return false;
    if (item.kind === "text" && item.taskId === task.id && item.text.trim() !== "") return true;
  }
  return false;
}

export function taskDetails(messages: ChatMessage[], taskId: string | null): string[] {
  if (!taskId) return [];
  return messages
    .filter((item) => item.role === "assistant" && item.detail === true && item.taskId === taskId && item.text.trim() !== "")
    .map((item) => item.text);
}

export function hasTaskSummary(messages: ChatMessage[], taskId: string | null): boolean {
  if (!taskId) return false;
  return messages.some(
    (item) =>
      item.role === "assistant" && item.kind === "text" && item.summary === true && item.taskId === taskId && item.text.trim() !== "",
  );
}

export function openApprovals(approvals: ApprovalView[]): ApprovalView[] {
  return approvals.filter((item) => item.resolved == null);
}

export function approvalLost(task: TaskView | null, approvals: ApprovalView[]): boolean {
  if (!task || task.status !== "waiting_approval") return false;
  if (approvals.some((item) => item.resolved == null && (item.taskId === task.id || item.taskId == null))) return false;
  return (task.waitSeq ?? 0) > (task.decidedSeq ?? 0);
}

function noteWaiting(task: TaskView, seq: number): void {
  task.status = "waiting_approval";
  if (seq > (task.waitSeq ?? 0)) task.waitSeq = seq;
}

function noteDecided(task: TaskView, seq: number): void {
  if (seq > (task.decidedSeq ?? 0)) task.decidedSeq = seq;
}

function stepKey(payload: Record<string, unknown>, seq: number): string {
  const explicit = str(payload.step_id);
  if (explicit) return explicit;
  const position = optNum(payload.position);
  if (position != null) return `pos-${position}-${str(payload.action)}`;
  return `step-${seq}`;
}

function stepResult(payload: Record<string, unknown>): string {
  for (const key of ["summary", "result_summary", "result_text", "result"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function reduceOrchScreen(state: OrchScreenState, action: OrchScreenAction): OrchScreenState {
  if (action.type === "reset") return initialOrchScreen();
  if (action.type === "user-message") {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: action.id,
          role: "user",
          text: action.text,
          taskId: action.taskId,
          seq: null,
          messageId: null,
          streaming: false,
          final: true,
          kind: "text",
        },
      ],
    };
  }
  if (action.type === "user-message-unsent") {
    const known = state.messages.reduce((max, item) => (item.seq != null && item.seq > max ? item.seq : max), 0);
    return {
      ...state,
      messages: state.messages.map((item) =>
        item.id === action.id && item.seq == null ? { ...item, seq: known + 0.5 } : item,
      ),
    };
  }
  if (action.type === "kill-notice") {
    if (state.killNotice?.phase === "stopped" && action.phase !== "stopped" && action.phase !== "stopping") return state;
    if (state.killNotice?.phase === "error" && action.phase === "unconfirmed") return state;
    return {
      ...state,
      killNotice: {
        text: action.detail,
        paidBoundary: action.paidBoundary,
        phase: action.phase,
        taskId: action.taskId ?? state.killNotice?.taskId ?? null,
        sessionId: action.sessionId ?? state.killNotice?.sessionId ?? null,
      },
    };
  }
  if (action.type === "approval-resolved") {
    const target = state.approvals.find((item) => item.id === action.id);
    const applies = target != null && (action.decision === "closed" || target.serverId);
    const approvals = state.approvals.map((item) =>
      item.id === action.id && applies ? { ...item, resolved: action.decision } : item,
    );
    const known = state.tasks.reduce((max, item) => Math.max(max, item.lastSeq, item.waitSeq ?? 0), 0);
    const tasks =
      applies && target?.taskId
        ? state.tasks.map((item): TaskView => {
            if (item.id !== target.taskId) return item;
            const still = approvals.some((entry) => entry.resolved == null && entry.taskId === item.id);
            const resume = action.decision === "approved" && !still && item.status === "waiting_approval";
            return {
              ...item,
              decidedSeq: Math.max(item.decidedSeq ?? 0, known),
              status: resume ? "executing" : item.status,
            };
          })
        : state.tasks;
    return { ...state, approvals, tasks };
  }

  const event = action.event;
  const next: OrchScreenState = {
    ...state,
    messages: state.messages.map((message) => ({ ...message })),
    agents: state.agents.map((agent) => ({ ...agent })),
    tasks: state.tasks.map((task) => ({
      ...task,
      steps: task.steps.map((step) => ({
        ...step,
        node: step.node ? { ...step.node } : null,
      })),
      checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint })),
      validators: [...task.validators],
      plan: task.plan.map((step) => ({ ...step })),
    })),
    approvals: state.approvals.map((item) => ({ ...item })),
  };
  const payload = asRecord(event.payload);
  const taskId = taskKey(event.task_id);
  if (event.type !== "approval_required") {
    const decided = approvalDecision(payload.decision ?? payload.approval_decision);
    const decidedId = serverApprovalId(payload.approval_id);
    if (decided && decidedId) closeServerApproval(next, decidedId, decided);
  }

  if (event.type === "ack") {
    const accepted = payload.accepted === true;
    const echoed = str(payload.echo) || (accepted ? str(payload.text) : "");
    next.ack = {
      echo: echoed,
      text: accepted || !str(payload.text) ? "принял, разбираю" : str(payload.text),
      seq: event.seq,
    };
    if (accepted || echoed) placeUser(next, event.seq, taskId, echoed);
    return next;
  }

  if (event.type === "classification") {
    const mode = str(payload.mode) as ClassificationPayload["mode"];
    if (mode === "interrupt" || mode === "steer" || mode === "append" || mode === "task") {
      const killSwitch = payload.kill_switch === true;
      next.classification = {
        mode,
        deterministic: payload.deterministic === true,
        detail: str(payload.detail),
        seq: event.seq,
        killSwitch,
      };
      if (killSwitch) {
        noteKillRequested(next, payload, { taskId, sessionId: event.session_id || null });
      }
    }
    absorbLlmSignal(next, taskId, payload, event.seq);
    return next;
  }

  if (event.type === "message") {
    const text = str(payload.text);
    if (text && str(payload.role) === "user") placeUser(next, event.seq, taskId, text);
    else if (text && str(payload.kind) === "action_result" && taskId) placeDetail(next, event.seq, taskId, text);
    else if (text) {
      finishAssistant(next, event.seq, taskId, text, messageKey(payload.message_id), str(payload.kind) === "task_summary");
    }
    absorbLlmSignal(next, taskId, payload, event.seq);
    return next;
  }

  if (event.type === "message_start") {
    if (str(payload.role, "assistant") !== "user") startAssistant(next, event.seq, taskId, messageKey(payload.message_id));
    return next;
  }

  if (event.type === "message_end") {
    endAssistant(next, messageKey(payload.message_id));
    return next;
  }

  if (event.type === "token") {
    appendToken(next, event.seq, taskId, str(payload.text), messageKey(payload.message_id));
    return next;
  }

  if (event.type === "kill_switch") {
    noteKillRequested(next, payload, { taskId, sessionId: event.session_id || null });
    return next;
  }

  if (event.type === "agent_status") {
    const agent = str(payload.agent) as AgentStatusPayload["agent"];
    const found = next.agents.find((item) => item.role === agent);
    if (found) {
      found.status = str(payload.status, found.status);
      found.detail = str(payload.detail);
    }
    const taskStatusRaw = str(payload.task_status);
    if (isTerminalKillStatus(taskStatusRaw)) noteTaskCancelled(next, event);
    if (!taskId) return next;
    const task = ensureTask(next.tasks, taskId, str(payload.title) || undefined, event.seq);
    const taskStatus = asTaskStatus(taskStatusRaw);
    if (taskStatus) {
      task.status = taskStatus;
      if (taskStatus === "waiting_approval") noteWaiting(task, event.seq);
    } else if (agent === "supervisor" && !taskStatusRaw) {
      const own = asTaskStatus(str(payload.status));
      if (own && !(TERMINAL_TASK.has(task.status) && !TERMINAL_TASK.has(own))) {
        task.status = own;
        if (own === "waiting_approval") noteWaiting(task, event.seq);
        // Итог задачи: «empty_plan» (не понял) и «rejected» (отклонено) — серые, не ошибка.
        if (TERMINAL_TASK.has(own) && str(payload.reason)) task.reason = str(payload.reason);
      }
    }
    return next;
  }

  if (
    (event.type === "step_started" ||
      event.type === "step_finished" ||
      event.type === "step_progress" ||
      event.type === "step_failed" ||
      event.type === "step_skipped") &&
    taskId
  ) {
    const task = ensureTask(next.tasks, taskId, undefined, event.seq);
    task.stepSeq = Math.max(task.stepSeq, event.seq);
    if (event.type === "step_started" && task.status === "queued") task.status = "executing";
    const stepId = stepKey(payload, event.seq);
    let step = task.steps.find((item) => item.id === stepId);
    if (!step) {
      step = {
        id: stepId,
        title: str(payload.title, str(payload.action, stepId)),
        action: str(payload.action),
        paid: payload.paid === true,
        status: "running",
        note: "",
        node: null,
        progress: null,
        result: "",
      };
      task.steps.push(step);
    }
    step.title = str(payload.title, step.title);
    step.action = str(payload.action, step.action);
    if (payload.paid === true) step.paid = true;
    step.note = str(payload.note, step.note) || textList(payload.warnings).join("; ");
    step.result = stepResult(payload) || step.result;
    const node = readNodeDef(payload.node);
    if (node) step.node = node;
    const progress = readProgress(payload.progress ?? payload.percent);
    if (progress != null) step.progress = progress;
    if (event.type === "step_finished") {
      const body = payload as StepPayload & Record<string, unknown>;
      step.status = body.ok === false ? "failed" : "done";
      if (step.status === "done") step.progress = null;
    } else if (event.type === "step_failed") {
      step.status = "failed";
      step.note = str(payload.error, step.note);
    } else if (event.type === "step_skipped") {
      step.status = "skipped";
      step.note = str(payload.reason, step.note);
    }
    return next;
  }

  if (event.type === "checkpoint" && taskId) {
    const task = ensureTask(next.tasks, taskId, undefined, event.seq);
    const id = str(payload.checkpoint_id, `cp-${event.seq}`);
    const plan = readPlanSteps(payload.plan);
    if (plan.length) task.plan = plan;
    if (!task.checkpoints.some((item) => item.id === id)) {
      const body = payload as CheckpointPayload & Record<string, unknown>;
      task.checkpoints.push({
        id,
        stepId: str(body.step_id),
        summary: str(body.summary, plan.length ? "план" : "чекпоинт"),
        spend: typeof body.spend === "number" ? body.spend : null,
      });
    }
    return next;
  }

  if (event.type === "validator_result" && taskId) {
    const task = ensureTask(next.tasks, taskId, undefined, event.seq);
    const body = payload as ValidatorResultPayload & Record<string, unknown>;
    task.validators.push({
      name: str(body.name, "validator"),
      pass: body.pass !== false,
      details: str(body.details),
      blocking: body.blocking === true,
    });
    return next;
  }

  if (event.type === "approval_required") {
    const body = payload as ApprovalPayload & Record<string, unknown>;
    const fromServer = approvalServerKey(body);
    const id = fromServer ?? `local-${event.seq}`;
    if (!next.approvals.some((item) => item.id === id)) {
      next.approvals.push({
        id,
        serverId: fromServer != null,
        taskId,
        title: str(body.title, "Нужно подтверждение"),
        reason: str(body.reason),
        risk: str(body.risk, "medium"),
        action: str(body.action),
        costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
        ...readApprovalExtras(body),
        resolved: null,
        seq: event.seq,
      });
    }
    if (taskId) {
      const task = ensureTask(next.tasks, taskId, undefined, event.seq);
      const reason = str(body.reason);
      if (isTerminalBlock(reason)) {
        task.status = blockOutcome(reason) ?? "failed";
        task.error = reason;
      } else {
        noteWaiting(task, event.seq);
      }
    }
    return next;
  }

  if (event.type === "task_done" && taskId) {
    const task = ensureTask(next.tasks, taskId, undefined, event.seq);
    task.status = "done";
    // Задача завершена — её подтверждения уже не ждут решения.
    for (const item of next.approvals) {
      if (item.taskId === taskId && item.serverId && item.resolved == null) item.resolved = "closed";
    }
    task.summary = str(payload.summary);
    placeOutcome(next, event.seq, taskId, "done");
    return next;
  }

  if (event.type === "task_failed" && taskId) {
    const task = ensureTask(next.tasks, taskId, undefined, event.seq);
    task.status = "failed";
    const error = str(payload.error, str(payload.summary, "задача упала"));
    task.error = error;
    task.reason = str(payload.reason);
    if (!isTerminalBlock(error) && task.reason !== "rejected") placeOutcome(next, event.seq, taskId, "failed");
    else closeTaskStreams(next, taskId);
    if (isTerminalBlock(error) && !next.approvals.some((item) => item.id === `block-${event.seq}`)) {
      next.approvals.push({
        id: `block-${event.seq}`,
        serverId: false,
        taskId,
        title: "",
        reason: error,
        risk: "",
        action: "",
        costEstimate: null,
        costCredits: null,
        warnings: [],
        effects: [],
        steps: [],
        costText: "",
        resolved: null,
      });
    }
    return next;
  }

  if (event.type === "approval_decided") {
    const id = approvalServerKey(payload);
    // decision: null — сервер закрыл подтверждение без решения (заменено, задача снята):
    // без этого после перезапуска старая карточка вставала из истории с живыми кнопками.
    const decided =
      approvalDecision(payload.decision) ??
      (payload.decision === "deny" ? "rejected" : payload.decision == null ? "closed" : null);
    if (id && decided) closeServerApproval(next, id, decided);
    if (taskId) {
      const task = ensureTask(next.tasks, taskId, undefined, event.seq);
      noteDecided(task, event.seq);
      const still = next.approvals.some((item) => item.resolved == null && item.taskId === taskId);
      if (decided === "approved" && !still && task.status === "waiting_approval") task.status = "executing";
    }
    return next;
  }

  if (event.type === "paid_retry_blocked") {
    if (!next.messages.some((item) => item.id === `notice-${event.seq}`)) {
      next.messages.push({
        id: `notice-${event.seq}`,
        role: "assistant",
        text:
          "Повтор платного шага не запущен: неизвестно, прошла ли прошлая оплата. " +
          "Проверьте результат в кабинете провайдера и решите вручную.",
        taskId,
        seq: event.seq,
        messageId: null,
        streaming: false,
        final: true,
        kind: "text",
      });
      sortMessages(next.messages);
    }
    return next;
  }

  return next;
}
