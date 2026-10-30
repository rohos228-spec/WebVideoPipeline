"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  GitBranch,
  Loader2,
  Maximize2,
  Minimize2,
  Play,
  Send,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, ApiError, type DbOrchestratorChatResult } from "@/lib/api";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { cn } from "@/lib/utils";
import { TextLlmPicker } from "@/components/shell/text-llm-picker";
import { MarkdownRenderer } from "@/components/gpt/markdown-renderer";

interface Props {
  projectId: number | null;
}

interface PendingConfirm {
  kind: string;
  project_id?: number;
  node_key?: string | null;
  node_type?: string | null;
  only?: string | null;
  count: number;
  nodes: string[];
  message?: string;
  files?: string[];
  plan_id?: string | number | null;
  needs_approval?: boolean;
  cost_low?: number | null;
  cost_high?: number | null;
  cost_unknown_steps?: number;
  risk?: string | null;
  steps?: { action_id: string }[];
  step_titles?: string[];
  resolved?: boolean;
  locked?: boolean;
}

interface ChatMsg {
  role: string;
  content: string;
  notes?: string[];
  created_at?: number;
  confirms?: PendingConfirm[];
  confirm?: PendingConfirm;
}

function cleanOrchestratorText(text: string): string {
  if (!text) return "";
  let t = text;
  // Убираем технические JSON-блоки действий из вывода для человека
  t = t.replace(/```(?:json)?\s*\{[\s\S]*?"(?:actions|ops)"[\s\S]*?\}\s*```/gi, "");
  t = t.replace(/\{[^{}]*"(?:actions|ops)"[^{}]*\}/gi, "");
  t = t.replace(/\n{3,}/g, "\n\n").trim();
  return t;
}

function formatTime(timestamp?: number): string {
  if (!timestamp) return "";
  try {
    const d = new Date(timestamp);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async (e: ReactMouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(cleanOrchestratorText(text));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="h-6 w-6 p-0 text-white/35 hover:text-white hover:bg-white/[0.08]"
      onClick={onCopy}
      title="Скопировать ответ"
    >
      {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
    </Button>
  );
}

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [expanded, setExpanded] = usePersistedState("vp-orchestrator-expanded", false);
  const storageKey = `vp-orchestrator-log-${projectId ?? "none"}`;
  const [chatLog, setChatLog] = usePersistedState<ChatMsg[]>(storageKey, []);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamingPhase, setStreamingPhase] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const userCancelRef = useRef(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      setChatLog(raw ? JSON.parse(raw) : []);
    } catch {
      setChatLog([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  useEffect(() => {
    if (isStreaming || busy) {
      const start = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedSec(Math.floor((Date.now() - start) / 1000));
      }, 500);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
      setElapsedSec(0);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isStreaming, busy]);

  useEffect(() => {
    if (isStreaming || streamingText || chatLog.length) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [isStreaming, streamingText, chatLog.length]);

  const cancelBusy = useCallback(() => {
    userCancelRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setIsStreaming(false);
    setStreamingText(null);
    setStreamingPhase(null);
  }, []);

  const handleChatResult = useCallback(
    (r: DbOrchestratorChatResult, currentChatLog: ChatMsg[]) => {
      const notes: string[] = [];
      if (r.applied) {
        notes.push(
          `записано операций: ${r.applied.updated}${
            r.applied.exported ? `, в проекте ячеек: ${r.applied.exported.cells}` : ""
          }`,
        );
      }
      for (const a of r.actions_run ?? []) {
        const firstNote = notes.length;
        if (a.error) notes.push(`ошибка: ${a.error}`);
        else if (a.inspect_project || a.project_graph) notes.push(`граф: ${JSON.stringify(a.inspect_project ?? a.project_graph)}`);
        else if (a.run_step) notes.push(`запущен шаг «${a.run_step}» → ${a.status}`);
        else if (a.stop_step) notes.push("генерация остановлена");
        else if (a.set_option) notes.push(`настройка: ${a.set_option}`);
        else if (a.set_prompt) notes.push(`промт: ${a.set_prompt}`);
        else if (a.set_text_llm) notes.push(`LLM: ${a.set_text_llm}`);
        else if (a.run_harness) notes.push(`проверки: ${a.run_harness}`);
        else if (a.read_file) {
          const rf = String(a.read_file);
          const first = rf.split("\n")[0] ?? rf;
          notes.push(`файл: ${first.slice(0, 160)}`);
        } else if (a.edit_files) notes.push(`код: ${a.edit_files}`);
        else if (a.run_tests) notes.push(`pytest: ${a.run_tests}`);
        else if (a.git_commit_push) notes.push(`git: ${a.git_commit_push}`);
        else if (a.delete_projects) notes.push(`проекты: ${a.delete_projects}`);
        else if (a.create_project) notes.push(`создан проект: ${a.create_project}`);
        else if (a.create_child) notes.push(`дочерний: ${a.create_child}`);
        else if (a.add_node) notes.push(`нода: ${typeof a.add_node === "string" ? a.add_node : JSON.stringify(a.add_node)}`);
        else if (a.connect_edges) notes.push(`связи: ${a.connect_edges}`);
        else if (a.rename_node) notes.push(`переименовано: ${a.rename_node}`);
        else if (a.repair_graph) notes.push(`граф: ${a.repair_graph}`);
        else if (a.hitl_decision) notes.push(`HITL: ${a.hitl_decision}`);
        else if (a.set_topic) notes.push(`тема: ${a.set_topic}`);
        else if (a.create_group) notes.push(`группа: ${a.create_group}`);
        if (a.project_id != null) {
          for (let index = firstNote; index < notes.length; index++) {
            notes[index] = `проект #${a.project_id}: ${notes[index]}`;
          }
        }
      }
      for (const u of r.ui_actions ?? []) {
        const nodeKey = u.node_key ?? (u.node_type ? `n_${u.node_type}` : null);
        if (u.kind === "step_prompts" && nodeKey) {
          window.dispatchEvent(
            new CustomEvent("studio-open-node-prompts", { detail: { nodeKey } }),
          );
          notes.push(`открыл окно промтов шага «${u.step}»`);
        } else if (u.kind === "node_studio" && nodeKey) {
          window.dispatchEvent(
            new CustomEvent("studio-open-node-prompts", { detail: { nodeKey } }),
          );
          notes.push(`открыл студию ноды «${u.node_type}»`);
        } else if (u.kind === "prompt_builder" && nodeKey) {
          window.dispatchEvent(
            new CustomEvent("studio-open-node-prompts", { detail: { nodeKey } }),
          );
          notes.push(`открыл промпты ноды «${u.node_type}»`);
        } else if (u.kind === "hitl" && u.hitl_id != null) {
          window.dispatchEvent(
            new CustomEvent("canvas-open-hitl-modal", { detail: { hitlId: u.hitl_id } }),
          );
          notes.push("открыл окно аппрува");
        } else if (u.kind === "topic") {
          window.dispatchEvent(
            new CustomEvent("canvas-select-node", { detail: { nodeKey: "n_topic" } }),
          );
          notes.push("тема проекта — в инспекторе справа");
        } else if (u.kind === "settings") {
          window.dispatchEvent(
            new CustomEvent("canvas-select-node", { detail: { nodeKey: null } }),
          );
          notes.push("настройки проекта — в инспекторе справа");
        } else if (u.kind === "shot_menu") {
          window.dispatchEvent(new CustomEvent("studio-open-shot-menu"));
          notes.push("открыл меню съёмки");
        } else if (u.kind === "gpt_chat") {
          window.dispatchEvent(new CustomEvent("studio-open-gpt"));
          notes.push("открыл общий чат");
        } else if (u.kind === "open_project" && u.project_id != null) {
          const createdHere = (r.actions_run ?? []).some(
            (action) => action.create_project || action.create_child,
          );
          window.dispatchEvent(new CustomEvent("studio-projects-changed"));
          if (createdHere) {
            notes.push(`проект #${u.project_id} добавлен в список проектов`);
          } else {
            window.dispatchEvent(
              new CustomEvent("studio-select-project", {
                detail: { projectId: u.project_id },
              }),
            );
            notes.push(`открыл проект #${u.project_id}`);
          }
        } else if (u.kind === "canvas_group") {
          window.dispatchEvent(
            new CustomEvent("canvas-select-node", {
              detail: { nodeKey: u.node_keys?.[0] ?? null },
            }),
          );
          notes.push(`группа «${u.title}» на канвасе`);
        } else if (u.kind === "create") {
          window.dispatchEvent(
            new CustomEvent("studio-open-outsee", {
              detail: { projectId },
            }),
          );
          notes.push("открыл Create / Outsee");
        }
      }
      if (r.error) notes.push(`ошибка: ${r.error}`);
      const pendAll = (r.pending_confirm ?? []).map((p) => ({ ...p, resolved: false }));
      for (const p of pendAll) {
        if (p.kind === "delete_projects") {
          notes.push(`к удалению проектов: ${p.count} — кнопки ниже`);
        } else if (p.kind === "remove_node") {
          notes.push(`к удалению нод: ${p.count} — кнопки ниже`);
        } else if (p.kind === "git_commit_push") {
          notes.push(`к push: подтверди кнопку ниже`);
        } else if (p.kind === "action_plan") {
          notes.push(`план #${p.plan_id}: подтверди кнопку ниже`);
        }
      }

      const cleanReply = cleanOrchestratorText(r.reply || "");

      setChatLog([
        ...currentChatLog,
        {
          role: "assistant",
          content: cleanReply || (notes.length ? "Действие выполнено." : "(пустой ответ)"),
          notes: notes.length ? notes : undefined,
          confirms: pendAll.length ? pendAll : undefined,
          created_at: Date.now(),
        },
      ]);
    },
    [projectId, setChatLog],
  );

  const sendMessage = useCallback(
    async (userText: string) => {
      if (projectId == null || !userText.trim() || busy || isStreaming || abortRef.current) return;
      const cleanInput = userText.trim();
      setChatInput("");

      userCancelRef.current = false;
      const ac = new AbortController();
      abortRef.current = ac;
      setBusy(true);
      setOpen(true);

      const userMsg: ChatMsg = { role: "user", content: cleanInput, created_at: Date.now() };
      const next: ChatMsg[] = [...chatLog, userMsg];
      setChatLog(next);

      setIsStreaming(true);
      setStreamingText("");
      setStreamingPhase("Анализирую граф проекта…");

      let streamFinished = false;

      try {
        const historyPayload = next.slice(-9, -1).map((m) => ({ role: m.role, content: m.content }));
        const r = await api.dbOrchestratorChatStream(
          projectId,
          cleanInput,
          historyPayload,
          {
            onDelta: (delta) => {
              setStreamingText((prev) => (prev ?? "") + delta);
            },
            onPhase: (_phase, detail) => {
              setStreamingPhase(detail || "Оркестратор печатает ответ…");
            },
            onDone: (result) => {
              streamFinished = true;
              setIsStreaming(false);
              setStreamingText(null);
              setStreamingPhase(null);
              handleChatResult(result, next);
            },
            onError: (err) => {
              streamFinished = true;
              setIsStreaming(false);
              setStreamingText(null);
              setStreamingPhase(null);
              setChatLog([
                ...next,
                { role: "assistant", content: `Ошибка: ${err}`, created_at: Date.now() },
              ]);
            },
          },
          ac.signal,
        );

        if (!streamFinished && r) {
          setIsStreaming(false);
          setStreamingText(null);
          setStreamingPhase(null);
          handleChatResult(r, next);
        }
      } catch (e: any) {
        if (ac.signal.aborted) {
          if (userCancelRef.current) {
            userCancelRef.current = false;
            setChatLog([
              ...next,
              { role: "assistant", content: "Генерация отменена пользователем.", created_at: Date.now() },
            ]);
          }
          return;
        }

        // Graceful fallback to non-streaming if stream endpoint had an issue
        try {
          const fallbackRes = await api.dbOrchestratorChat(
            projectId,
            cleanInput,
            next.slice(-9, -1).map((m) => ({ role: m.role, content: m.content })),
            { signal: ac.signal },
          );
          handleChatResult(fallbackRes, next);
        } catch (fallbackErr: any) {
          const unauthorized = fallbackErr instanceof ApiError && fallbackErr.status === 401;
          setChatLog([
            ...next,
            {
              role: "assistant",
              content: unauthorized
                ? "Сессия истекла или требуется авторизация в студии."
                : `Ошибка: ${fallbackErr instanceof Error ? fallbackErr.message : fallbackErr}`,
              created_at: Date.now(),
            },
          ]);
        }
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
        setBusy(false);
        setIsStreaming(false);
        setStreamingText(null);
        setStreamingPhase(null);
      }
    },
    [projectId, busy, isStreaming, chatLog, setChatLog, setOpen, handleChatResult],
  );

  const sendChat = useCallback(async () => {
    if (!chatInput.trim()) return;
    await sendMessage(chatInput);
  }, [chatInput, sendMessage]);

  const onToggleOpen = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setOpen(!open);
    },
    [open, setOpen],
  );

  const onToggleExpanded = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!open) {
        setOpen(true);
        setExpanded(true);
        return;
      }
      setExpanded(!expanded);
    },
    [open, expanded, setOpen, setExpanded],
  );

  const withConfirmResolved = useCallback(
    (prev: ChatMsg[], msgIndex: number, confirmIndex: number, followUp: ChatMsg) => {
      const next = prev.map((m, i) => {
        if (i !== msgIndex) return m;
        const list = [...(m.confirms ?? (m.confirm ? [m.confirm] : []))];
        if (list[confirmIndex]) {
          list[confirmIndex] = { ...list[confirmIndex], resolved: true };
        }
        return { ...m, confirms: list, confirm: undefined };
      });
      return [...next, followUp];
    },
    [],
  );

  const resolveRemove = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmRemove(confirm.project_id ?? projectId, {
            node_key: confirm.node_key,
            node_type: confirm.node_type,
            only: confirm.only,
          });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `Подтверждено: ${r.remove_node}.`,
              created_at: Date.now(),
            }),
          );
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Удаление отменено — ноды на месте.",
              created_at: Date.now(),
            }),
          );
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка удаления: ${e instanceof Error ? e.message : e}`,
            created_at: Date.now(),
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  const resolveDeleteProjects = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const ids = (confirm.nodes || [])
            .map((x) => Number(x))
            .filter((n) => Number.isFinite(n) && n > 0);
          const r = await api.dbOrchestratorConfirmDeleteProjects(projectId, { ids });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `${r.delete_projects}`,
              created_at: Date.now(),
            }),
          );
          window.dispatchEvent(new CustomEvent("studio-projects-changed"));
          if (ids.includes(projectId)) {
            window.dispatchEvent(
              new CustomEvent("studio-select-project", { detail: { projectId: null } }),
            );
          }
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Удаление проектов отменено — ничего не стёрто.",
              created_at: Date.now(),
            }),
          );
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка удаления проектов: ${e instanceof Error ? e.message : e}`,
            created_at: Date.now(),
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  const resolveGitPush = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      setBusy(true);
      try {
        if (approve) {
          const r = await api.dbOrchestratorConfirmGitPush(projectId, {
            message: confirm.message || "fix: orchestrator",
            files: confirm.files ?? confirm.nodes,
          });
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `${r.git_commit_push}`,
              created_at: Date.now(),
            }),
          );
        } else {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: "Push отменён — правки на диске остались, в git не ушли.",
              created_at: Date.now(),
            }),
          );
        }
      } catch (e) {
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Ошибка push: ${e instanceof Error ? e.message : e}`,
            created_at: Date.now(),
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  const resolvePlan = useCallback(
    async (
      msgIndex: number,
      confirmIndex: number,
      confirm: PendingConfirm,
      approve: boolean,
    ) => {
      if (projectId == null || busy) return;
      if (confirm.locked) return;
      const planId = confirm.plan_id == null ? "" : String(confirm.plan_id).trim();
      if (!planId) {
        setChatLog((prev) => [
          ...prev,
          { role: "assistant", content: "Ошибка плана: сервер не прислал plan_id.", created_at: Date.now() },
        ]);
        return;
      }
      setBusy(true);
      try {
        const r = await api.dbOrchestratorConfirmPlan(projectId, {
          plan_id: planId,
          approve,
        });
        setChatLog((prev) =>
          withConfirmResolved(prev, msgIndex, confirmIndex, {
            role: "assistant",
            content: approve
              ? `План #${planId} запущен${r.status ? ` (${r.status})` : ""}.`
              : `План #${planId} не подтверждён — ничего не запущено.`,
            created_at: Date.now(),
          }),
        );
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        if (status === 409) {
          setChatLog((prev) =>
            withConfirmResolved(prev, msgIndex, confirmIndex, {
              role: "assistant",
              content: `План #${planId} уже подтверждён ранее — повторный запуск не нужен.`,
              created_at: Date.now(),
            }),
          );
          return;
        }
        setChatLog((prev) => [
          ...prev,
          {
            role: "assistant",
            content:
              status === 404
                ? `План #${planId} не найден на сервере (устарел) — попроси оркестратор построить план заново.`
                : status === 401
                  ? "Сессия истекла или требуется авторизация в студии."
                  : `Ошибка запуска плана: ${e instanceof Error ? e.message : e}`,
            created_at: Date.now(),
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [projectId, busy, setChatLog, withConfirmResolved],
  );

  return (
    <div
      className={cn(
        "absolute z-40 flex flex-col border border-white/[0.1] bg-[#0c0c0e]/98 shadow-2xl backdrop-blur transition-all duration-200",
        open && expanded
          ? "inset-x-0 bottom-0 top-[158px] rounded-t-xl border-x-0 border-b-0"
          : open
            ? "inset-x-0 bottom-0 h-72 rounded-none border-x-0 border-b-0"
            : "inset-x-0 bottom-0 h-10 rounded-none border-x-0 border-b-0",
      )}
    >
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-white/[0.08] px-2.5 bg-white/[0.02]">
        <div className="flex min-w-0 flex-1 items-center gap-2 px-1 text-[11px] text-white/60">
          <Bot className="h-4 w-4 shrink-0 text-primary" />
          <span className="shrink-0 font-semibold text-white/90">Оркестратор</span>
          <span className="truncate text-white/40">
            {projectId == null
              ? "открой проект для начала"
              : isStreaming
                ? `${streamingPhase || "Печатает ответ…"} (${elapsedSec}с)`
                : busy
                  ? `Анализирует… (${elapsedSec}с)`
                  : "ИИ-режиссёр проекта"}
          </span>
        </div>

        <div
          className="hidden min-w-0 shrink sm:block"
          onClick={(e) => e.stopPropagation()}
          data-testid="orch-text-llm-picker"
        >
          <TextLlmPicker className="max-w-[220px]" />
        </div>

        {busy || isStreaming ? (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="h-7 shrink-0 gap-1.5 text-[11px] px-2.5"
            onClick={(e) => {
              e.stopPropagation();
              cancelBusy();
            }}
            title="Прервать генерацию"
          >
            <Square className="h-3 w-3 fill-current" />
            Отмена
          </Button>
        ) : null}

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50 hover:text-white"
          onClick={onToggleExpanded}
          title={expanded && open ? "Меньше окно" : "Большое окно"}
        >
          {expanded && open ? (
            <Minimize2 className="h-3.5 w-3.5" />
          ) : (
            <Maximize2 className="h-3.5 w-3.5" />
          )}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50 hover:text-white"
          onClick={onToggleOpen}
          title={open ? "Свернуть панель" : "Открыть панель"}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </Button>
      </div>

      {open ? (
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col",
            expanded ? "min-h-0" : "h-60",
          )}
        >
          <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3 space-y-3">
            {chatLog.map((m, i) => {
              const isUser = m.role === "user";

              if (isUser) {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-white/[0.07] border border-white/[0.12] px-3.5 py-2.5 shadow-sm">
                      <div className="flex items-center justify-between gap-3 mb-1">
                        <span className="text-[10px] font-semibold tracking-wider text-primary">Ты</span>
                        {m.created_at ? (
                          <span className="text-[10px] text-white/30 font-mono">
                            {formatTime(m.created_at)}
                          </span>
                        ) : null}
                      </div>
                      <div className="text-xs text-white/95 leading-relaxed whitespace-pre-wrap selection:bg-primary/20">
                        {m.content}
                      </div>
                    </div>
                  </div>
                );
              }

              return (
                <div key={i} className="flex gap-2.5 max-w-[95%]">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/15 border border-primary/30 text-primary shadow-sm mt-0.5">
                    <Bot className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0 rounded-2xl rounded-tl-sm bg-[#121216]/95 border border-white/[0.08] p-3.5 shadow-md">
                    <div className="flex items-center justify-between gap-2 mb-2 border-b border-white/[0.06] pb-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-semibold text-white/90">Оркестратор</span>
                        <span className="rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-white/40 font-mono">
                          ИИ-ассистент
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {m.created_at ? (
                          <span className="text-[10px] text-white/30 font-mono mr-1">
                            {formatTime(m.created_at)}
                          </span>
                        ) : null}
                        <CopyButton text={m.content} />
                      </div>
                    </div>

                    <div className="text-xs leading-relaxed text-white/90">
                      <MarkdownRenderer content={cleanOrchestratorText(m.content)} />
                    </div>

                    {m.notes && m.notes.length > 0 && (
                      <div className="mt-2.5 flex flex-wrap gap-1.5 pt-2 border-t border-white/[0.06]">
                        {m.notes.map((note, ni) => (
                          <span
                            key={ni}
                            className="inline-flex items-center gap-1 rounded bg-white/[0.05] border border-white/10 px-2 py-0.5 text-[10px] text-white/70 font-mono"
                          >
                            <Sparkles className="h-2.5 w-2.5 text-primary" />
                            {note}
                          </span>
                        ))}
                      </div>
                    )}

                    {(m.confirms ?? (m.confirm ? [m.confirm] : [])).map((c, ci) => {
                      if (c.resolved) return null;
                      if (c.kind === "remove_node") {
                        return (
                          <div
                            key={`rm-${ci}`}
                            className="mt-2.5 flex items-center gap-2 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2"
                          >
                            <Trash2 className="h-4 w-4 text-red-400 shrink-0" />
                            <span className="flex-1 text-[11px] text-white/85">
                              Удалить {c.count} нод
                              {c.project_id != null ? ` в проекте #${c.project_id}` : ""}
                              {c.node_type ? ` типа «${c.node_type}»` : ""}
                              {c.only === "duplicates" ? " (только дубли)" : ""}?
                            </span>
                            <Button
                              size="sm"
                              variant="destructive"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveRemove(i, ci, c, true)}
                            >
                              Подтвердить удаление
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveRemove(i, ci, c, false)}
                            >
                              Отмена
                            </Button>
                          </div>
                        );
                      }
                      if (c.kind === "delete_projects") {
                        return (
                          <div
                            key={`del-${ci}`}
                            className="mt-2.5 flex items-center gap-2 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2"
                          >
                            <Trash2 className="h-4 w-4 text-red-400 shrink-0" />
                            <span className="flex-1 text-[11px] text-white/85">
                              Удалить {c.count} проект(ов)
                              {(c.files ?? []).length
                                ? `: ${(c.files ?? []).slice(0, 4).join(", ")}`
                                : ""}
                              ?
                            </span>
                            <Button
                              size="sm"
                              variant="destructive"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveDeleteProjects(i, ci, c, true)}
                            >
                              Подтвердить удаление
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveDeleteProjects(i, ci, c, false)}
                            >
                              Отмена
                            </Button>
                          </div>
                        );
                      }
                      if (c.kind === "git_commit_push") {
                        return (
                          <div
                            key={`git-${ci}`}
                            className="mt-2.5 flex items-center gap-2 rounded-xl border border-emerald-400/30 bg-emerald-500/10 px-3 py-2"
                          >
                            <GitBranch className="h-4 w-4 text-emerald-400 shrink-0" />
                            <span className="flex-1 text-[11px] text-white/85">
                              Push: {c.message || "commit"}
                              {(c.files ?? c.nodes).length
                                ? ` (${(c.files ?? c.nodes).slice(0, 3).join(", ")})`
                                : ""}
                            </span>
                            <Button
                              size="sm"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveGitPush(i, ci, c, true)}
                            >
                              Подтвердить push
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-[11px]"
                              disabled={busy}
                              onClick={() => void resolveGitPush(i, ci, c, false)}
                            >
                              Отмена
                            </Button>
                          </div>
                        );
                      }
                      if (c.kind === "action_plan_error") {
                        return (
                          <div
                            key={`plan-err-${ci}`}
                            data-testid="orch-legacy-plan-error"
                            className="mt-2.5 flex items-start gap-2 rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100/90"
                          >
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                            <span className="flex-1">
                              {c.message || "План не построен. Попробуй переформулировать."}
                              {c.steps?.length ? ` Шаги: ${c.steps.map((s) => s.action_id).join(", ")}` : ""}
                            </span>
                          </div>
                        );
                      }
                      if (c.kind === "action_plan") {
                        const steps = c.steps ?? [];
                        const titles = (c.step_titles ?? []).filter((t) => t.trim());
                        const chips = (titles.length ? titles : steps.map((s) => s.action_id)).slice(0, 12);
                        const unknownCovered = c.cost_unknown_steps ?? 0;
                        const cost =
                          c.cost_low != null || c.cost_high != null
                            ? c.cost_low === c.cost_high || c.cost_high == null
                              ? `${c.cost_low ?? 0}`
                              : `${c.cost_low ?? 0}–${c.cost_high}`
                            : "не оценена";
                        return (
                          <div
                            key={`plan-${ci}`}
                            data-testid="orch-legacy-plan"
                            data-plan-id={c.plan_id ?? ""}
                            className="mt-2.5 flex flex-col gap-2 rounded-xl border border-primary/30 bg-primary/10 p-3"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Play className="h-4 w-4 shrink-0 text-primary" />
                              <span className="flex-1 text-[11px] text-white/90">
                                План #{c.plan_id}: {steps.length || c.count} шаг(ов), цена{" "}
                                <span className="font-semibold text-white">{cost} кредитов</span>
                                {unknownCovered ? ` (+${unknownCovered} без оценки)` : ""}
                                {c.risk ? `, риск ${c.risk}` : ""}
                                {c.needs_approval === false ? " (approval не нужен)" : ""}
                              </span>
                              <Button
                                size="sm"
                                className="h-7 text-[11px]"
                                data-testid="orch-plan-run"
                                disabled={busy}
                                onClick={() => void resolvePlan(i, ci, c, true)}
                              >
                                Запустить план
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-[11px]"
                                data-testid="orch-plan-skip"
                                disabled={busy}
                                onClick={() => void resolvePlan(i, ci, c, false)}
                              >
                                Не запускать
                              </Button>
                            </div>
                            {chips.length ? (
                              <div className="flex flex-wrap gap-1 text-[10px] text-white/60">
                                {chips.map((label, si) => (
                                  <span
                                    key={`${label}-${si}`}
                                    className={`rounded bg-black/40 border border-white/10 px-2 py-0.5 ${
                                      titles.length ? "" : "font-mono"
                                    }`}
                                  >
                                    {label}
                                  </span>
                                ))}
                                {(titles.length ? titles : steps).length > 12 ? (
                                  <span className="px-1 text-white/40">
                                    +{(titles.length ? titles : steps).length - 12}
                                  </span>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        );
                      }
                      return null;
                    })}
                  </div>
                </div>
              );
            })}

            {isStreaming && (
              <div className="flex gap-2.5 max-w-[95%] animate-in fade-in duration-200">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/20 border border-primary/40 text-primary shadow-sm animate-pulse mt-0.5">
                  <Bot className="h-4 w-4" />
                </div>
                <div className="flex-1 min-w-0 rounded-2xl rounded-tl-sm bg-[#121216]/95 border border-primary/30 p-3.5 shadow-lg backdrop-blur-xl">
                  <div className="flex items-center justify-between gap-2 mb-2 border-b border-white/[0.06] pb-1.5">
                    <div className="flex items-center gap-2 text-[11px] text-primary font-medium">
                      <Loader2 className="h-3 w-3 animate-spin text-primary" />
                      <span>{streamingPhase || "Печатает ответ…"}</span>
                    </div>
                    <span className="text-[10px] text-white/40 font-mono">{elapsedSec}с</span>
                  </div>
                  {streamingText ? (
                    <div className="text-xs leading-relaxed text-white/90">
                      <MarkdownRenderer content={streamingText} />
                      <span className="inline-block w-1.5 h-3.5 ml-1 bg-primary animate-pulse align-middle" />
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 py-1 text-white/40 text-xs italic">
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary animate-ping" />
                      <span>Оркестратор формулирует ответ…</span>
                    </div>
                  )}
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="flex shrink-0 items-center gap-2 border-t border-white/[0.08] px-3 py-2.5 bg-white/[0.01]">
            <input
              ref={inputRef}
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !busy && !isStreaming && void sendChat()}
              disabled={projectId == null || busy || isStreaming}
              placeholder={
                projectId == null
                  ? "сначала открой проект"
                  : isStreaming
                    ? "оркестратор печатает ответ… жми Отмена для остановки"
                    : busy
                      ? "оркестратор думает… жми Отмена"
                      : "сообщение оркестратору…"
              }
              className="h-9 flex-1 rounded-lg border border-white/10 bg-black/50 px-3 text-xs text-white placeholder:text-white/30 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/40 disabled:opacity-40 transition-all"
            />
            <Button
              type="button"
              size="sm"
              variant="default"
              disabled={projectId == null || busy || isStreaming || !chatInput.trim()}
              onClick={() => void sendChat()}
              className="h-9 gap-1.5 text-xs px-3.5"
            >
              <Send className="h-3.5 w-3.5" />
              {isStreaming ? "…" : "Отправить"}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
