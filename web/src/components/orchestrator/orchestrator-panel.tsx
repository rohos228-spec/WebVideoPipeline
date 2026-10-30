"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Clapperboard,
  Copy,
  GitBranch,
  Loader2,
  Maximize2,
  Minimize2,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Pencil,
  Play,
  Plus,
  Send,
  Settings,
  Shield,
  Sparkles,
  Square,
  Trash2,
  User,
  X,
  Zap,
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

function OrchestratorAvatar({
  size = "md",
  pulse = false,
  className,
}: {
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
  className?: string;
}) {
  const [imgError, setImgError] = useState(false);
  const sizeClasses = {
    sm: "h-6 w-6",
    md: "h-8 w-8",
    lg: "h-12 w-12",
  }[size];

  return (
    <div
      className={cn(
        "relative shrink-0 rounded-full overflow-hidden border border-emerald-400/50 bg-gradient-to-br from-[#0c241e] to-[#081512] shadow-md shadow-emerald-950/60 flex items-center justify-center select-none ring-1 ring-emerald-500/20",
        sizeClasses,
        pulse && "ring-2 ring-emerald-400 ring-offset-1 ring-offset-[#0c0c0e] animate-pulse",
        className,
      )}
    >
      {!imgError ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src="/orchestrator-avatar.jpg"
          alt="ИИ-агент"
          className="h-full w-full object-cover rounded-full"
          onError={() => setImgError(true)}
        />
      ) : (
        <Bot className="h-1/2 w-1/2 text-emerald-400" />
      )}
    </div>
  );
}

interface WindowRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

const DEFAULT_RECT: WindowRect = {
  x: 72,
  y: 150,
  width: 550,
  height: 640,
};

interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  projectId?: number | null;
}

export function OrchestratorPanel({ projectId }: Props) {
  const [open, setOpen] = usePersistedState("vp-orchestrator-open", true);
  const [rect, setRect] = usePersistedState<WindowRect>("vp-ai-agent-window-rect-v1", DEFAULT_RECT);
  const [isMaximized, setIsMaximized] = useState(false);

  // Единое хранилище всех сессий и логов чатов по всей Студии (переживает переключение проектов)
  const sessionsKey = "vp-ai-agent-sessions-unified";
  const activeSessionKey = "vp-ai-agent-active-session-unified";
  const [sessions, setSessions] = usePersistedState<ChatSession[]>(sessionsKey, [
    { id: "default", title: "Основной чат", createdAt: Date.now(), updatedAt: Date.now() },
  ]);
  const [activeSessionId, setActiveSessionId] = usePersistedState<string>(activeSessionKey, "default");
  const [isChatsOpen, setIsChatsOpen] = usePersistedState("vp-ai-agent-chats-panel-open", false);
  const isChatsOpenRef = useRef(isChatsOpen);
  useEffect(() => {
    isChatsOpenRef.current = isChatsOpen;
  }, [isChatsOpen]);

  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  // Режим выполнения действий (С подтверждением / Автоматически)
  const [actionMode, setActionMode] = usePersistedState<"ask" | "auto">("vp-ai-agent-action-mode", "ask");
  const [isModeOpen, setIsModeOpen] = useState(false);
  const modeMenuRef = useRef<HTMLDivElement | null>(null);

  // Прикрепленные файлы
  const [attachments, setAttachments] = useState<
    { id: string; name: string; size: number; type: string; content?: string }[]
  >([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Хранилище всех логов чатов в единой структуре
  const allLogsKey = "vp-ai-agent-all-logs-unified";
  const [chatLogsMap, setChatLogsMap] = usePersistedState<Record<string, ChatMsg[]>>(allLogsKey, {});

  // Инициализация/миграция логов и сессий из старых ключей при первом рендере
  useEffect(() => {
    setSessions((prev) => {
      let merged = [...(prev || [])];
      let changed = false;
      const oldKeys = [
        `vp-ai-agent-sessions-${projectId ?? "none"}`,
        "vp-ai-agent-sessions-none",
      ];
      for (const k of oldKeys) {
        try {
          const raw = localStorage.getItem(k);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed) && parsed.length > 0) {
              for (const item of parsed) {
                if (!merged.some((m) => m.id === item.id)) {
                  merged.push(item);
                  changed = true;
                }
              }
            }
          }
        } catch {}
      }
      return changed ? merged : prev;
    });

    setChatLogsMap((prev) => {
      let changed = false;
      const updated = { ...prev };

      const oldMapKeys = [
        `vp-ai-agent-all-logs-${projectId ?? "none"}`,
        "vp-ai-agent-all-logs-none",
      ];
      for (const k of oldMapKeys) {
        try {
          const raw = localStorage.getItem(k);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === "object") {
              for (const [sId, msgs] of Object.entries(parsed)) {
                if ((!updated[sId] || updated[sId].length === 0) && Array.isArray(msgs) && msgs.length > 0) {
                  updated[sId] = msgs as ChatMsg[];
                  changed = true;
                }
              }
            }
          }
        } catch {}
      }

      // Миграция default (основного чата)
      const defKey = `vp-orchestrator-log-${projectId ?? "none"}`;
      if (!updated["default"] || updated["default"].length === 0) {
        try {
          const raw = localStorage.getItem(defKey);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed) && parsed.length > 0) {
              updated["default"] = parsed;
              changed = true;
            }
          }
        } catch {}
      }

      return changed ? updated : prev;
    });
  }, [projectId, setSessions, setChatLogsMap]);

  const chatLog: ChatMsg[] = useMemo(() => {
    return chatLogsMap[activeSessionId] || [];
  }, [chatLogsMap, activeSessionId]);

  const setChatLog = useCallback(
    (next: ChatMsg[] | ((prev: ChatMsg[]) => ChatMsg[])) => {
      setChatLogsMap((prev) => {
        const currentList = prev[activeSessionId] || [];
        const resolved = typeof next === "function" ? next(currentList) : next;
        const updated = { ...prev, [activeSessionId]: resolved };
        try {
          const key =
            activeSessionId === "default"
              ? `vp-orchestrator-log-${projectId ?? "none"}`
              : `vp-orchestrator-log-${projectId ?? "none"}-${activeSessionId}`;
          localStorage.setItem(key, JSON.stringify(resolved));
        } catch {}
        return updated;
      });
    },
    [activeSessionId, projectId, setChatLogsMap],
  );

  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamingPhase, setStreamingPhase] = useState<string | null>(null);
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(null);
  const streamingSessionIdRef = useRef<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const userCancelRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const currentRectRef = useRef<WindowRect>(rect);
  const rafIdRef = useRef<number | null>(null);
  const autoResolveRef = useRef<((confirms: PendingConfirm[], msgIndex: number) => void) | null>(null);

  const currentSession = (sessions || []).find((s) => s.id === activeSessionId) || sessions?.[0];

  const toggleChats = useCallback(() => {
    setIsChatsOpen((prev) => {
      const next = !prev;
      if (next && !isMaximized && currentRectRef.current.x < 270) {
        const adjustedX = 270;
        currentRectRef.current.x = adjustedX;
        setRect((r) => ({ ...r, x: adjustedX }));
        if (panelRef.current) {
          panelRef.current.style.left = `${adjustedX}px`;
        }
      }
      return next;
    });
  }, [isMaximized, setRect, setIsChatsOpen]);

  const handleCreateNewChat = useCallback(() => {
    const newId = `session-${Date.now()}`;
    const newSession: ChatSession = {
      id: newId,
      title: `Чат ${(sessions?.length || 0) + 1}`,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      projectId: projectId ?? null,
    };
    setSessions((prev) => [newSession, ...(prev || [])]);
    setChatLogsMap((prev) => ({ ...prev, [newId]: [] }));
    setActiveSessionId(newId);
  }, [projectId, sessions, setSessions, setChatLogsMap, setActiveSessionId]);

  const handleSwitchChat = useCallback(
    (id: string) => {
      setActiveSessionId(id);
    },
    [setActiveSessionId],
  );

  const handleStartRename = useCallback((s: ChatSession, e: ReactMouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(s.id);
    setEditingTitle(s.title);
  }, []);

  const handleSaveRename = useCallback(
    (id: string) => {
      const trimmed = editingTitle.trim();
      if (trimmed) {
        setSessions((prev) =>
          (prev || []).map((s) => (s.id === id ? { ...s, title: trimmed } : s)),
        );
      }
      setEditingSessionId(null);
    },
    [editingTitle, setSessions],
  );

  const handleDeleteChat = useCallback(
    (id: string, e: ReactMouseEvent) => {
      e.stopPropagation();
      const remaining = (sessions || []).filter((s) => s.id !== id);
      try {
        const key =
          id === "default"
            ? `vp-orchestrator-log-${projectId ?? "none"}`
            : `vp-orchestrator-log-${projectId ?? "none"}-${id}`;
        localStorage.removeItem(key);
      } catch {}

      setChatLogsMap((prev) => {
        const copy = { ...prev };
        delete copy[id];
        return copy;
      });

      if (remaining.length === 0) {
        const def: ChatSession = {
          id: "default",
          title: "Основной чат",
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        setSessions([def]);
        setActiveSessionId("default");
      } else {
        setSessions(remaining);
        if (activeSessionId === id) {
          setActiveSessionId(remaining[0].id);
        }
      }
    },
    [sessions, setSessions, activeSessionId, projectId, setActiveSessionId, setChatLogsMap],
  );

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    Array.from(files).forEach((file) => {
      const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const isText =
        file.type.startsWith("text/") ||
        /\.(txt|json|md|py|csv|log|yaml|yml|js|ts|tsx|html|css)$/i.test(file.name);

      if (isText && file.size < 500_000) {
        const reader = new FileReader();
        reader.onload = (ev) => {
          const content = ev.target?.result as string;
          setAttachments((prev) => [
            ...prev,
            { id, name: file.name, size: file.size, type: file.type || "text", content },
          ]);
        };
        reader.readAsText(file);
      } else {
        setAttachments((prev) => [
          ...prev,
          {
            id,
            name: file.name,
            size: file.size,
            type: file.type || (file.type.startsWith("image/") ? "изображение" : "файл"),
          },
        ]);
      }
    });

    e.target.value = "";
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(140, Math.max(28, textareaRef.current.scrollHeight))}px`;
    }
  }, [chatInput]);

  useEffect(() => {
    if (!isModeOpen) return;
    const onClickOutside = (e: MouseEvent) => {
      if (modeMenuRef.current && !modeMenuRef.current.contains(e.target as Node)) {
        setIsModeOpen(false);
      }
    };
    window.addEventListener("mousedown", onClickOutside);
    return () => window.removeEventListener("mousedown", onClickOutside);
  }, [isModeOpen]);

  useEffect(() => {
    currentRectRef.current = rect;
  }, [rect]);

  useEffect(() => {
    return () => {
      if (rafIdRef.current != null) {
        cancelAnimationFrame(rafIdRef.current);
      }
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, []);

  // Синхронизация состояния видимости с левой панелью
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("studio-orchestrator-state-changed", { detail: { open } })
    );
  }, [open]);

  // Слушатель событий переключения/открытия из левой панели (кнопка «ИИ-агент»)
  useEffect(() => {
    const onToggle = () => setOpen((prev) => !prev);
    const onOpen = () => setOpen(true);
    const onClose = () => setOpen(false);

    window.addEventListener("studio-toggle-orchestrator", onToggle);
    window.addEventListener("studio-open-orchestrator", onOpen);
    window.addEventListener("studio-close-orchestrator", onClose);

    return () => {
      window.removeEventListener("studio-toggle-orchestrator", onToggle);
      window.removeEventListener("studio-open-orchestrator", onOpen);
      window.removeEventListener("studio-close-orchestrator", onClose);
    };
  }, [setOpen]);

  // Проверка границ экрана при первом рендере и изменении режима/панели
  useEffect(() => {
    if (typeof window !== "undefined") {
      setRect((prev) => {
        const p = prev || DEFAULT_RECT;
        const maxW = Math.min(p.width ?? DEFAULT_RECT.width, Math.max(380, window.innerWidth - 80));
        const maxH = Math.min(p.height ?? DEFAULT_RECT.height, Math.max(340, window.innerHeight - 80));
        const minX = isChatsOpen && !isMaximized ? 270 : 0;
        const safeX = Math.max(minX, Math.min(window.innerWidth - 120, p.x ?? DEFAULT_RECT.x));
        const safeY = Math.max(0, Math.min(window.innerHeight - 80, p.y ?? DEFAULT_RECT.y));
        return {
          x: safeX,
          y: safeY,
          width: maxW,
          height: maxH,
        };
      });
    }
  }, [setRect, isChatsOpen, isMaximized]);

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

  const scrollToBottom = useCallback((smooth = false) => {
    if (scrollContainerRef.current) {
      const el = scrollContainerRef.current;
      if (smooth) {
        el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      } else {
        el.scrollTop = el.scrollHeight;
      }
    }
    if (messagesEndRef.current) {
      try {
        messagesEndRef.current.scrollIntoView({
          behavior: smooth ? "smooth" : "instant",
          block: "end",
        });
      } catch {
        messagesEndRef.current.scrollIntoView(false);
      }
    }
  }, []);

  // Автоскролл в самый конец при открытии окна ИИ-агента или изменении истории сообщений
  useEffect(() => {
    if (!open) return;
    // Мгновенный скролл вниз
    scrollToBottom(false);

    // Дополнительные скроллы по мере расчёта размеров Markdown и картинок
    const raf1 = requestAnimationFrame(() => scrollToBottom(false));
    const raf2 = requestAnimationFrame(() => requestAnimationFrame(() => scrollToBottom(false)));
    const t1 = setTimeout(() => scrollToBottom(false), 40);
    const t2 = setTimeout(() => scrollToBottom(false), 120);
    const t3 = setTimeout(() => scrollToBottom(false), 250);

    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [open, chatLog.length, projectId, scrollToBottom]);

  // Автоскролл во время стриминга ответа ИИ-агента
  useEffect(() => {
    if (isStreaming || streamingText) {
      scrollToBottom(false);
    }
  }, [isStreaming, streamingText, scrollToBottom]);

  const cancelBusy = useCallback(() => {
    userCancelRef.current = true;
    const targetId = streamingSessionIdRef.current;
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setIsStreaming(false);
    setStreamingText(null);
    setStreamingPhase(null);
    setStreamingSessionId(null);
    streamingSessionIdRef.current = null;

    if (targetId) {
      setChatLogsMap((prev) => {
        const existing = prev[targetId] || [];
        const cancelMsg: ChatMsg = {
          role: "assistant",
          content: "Генерация отменена пользователем.",
          created_at: Date.now(),
        };
        const updated = [...existing, cancelMsg];
        try {
          const key =
            targetId === "default"
              ? `vp-orchestrator-log-${projectId ?? "none"}`
              : `vp-orchestrator-log-${projectId ?? "none"}-${targetId}`;
          localStorage.setItem(key, JSON.stringify(updated));
        } catch {}
        return { ...prev, [targetId]: updated };
      });
    }
  }, [projectId, setChatLogsMap]);

  const handleChatResult = useCallback(
    (
      r: DbOrchestratorChatResult,
      targetSessionId: string,
      currentChatLog: ChatMsg[],
      targetProjectId?: number | null,
    ) => {
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
      const assistantMsg: ChatMsg = {
        role: "assistant",
        content: cleanReply || (notes.length ? "Действие выполнено." : "(пустой ответ)"),
        notes: notes.length ? notes : undefined,
        confirms: pendAll.length ? pendAll : undefined,
        created_at: Date.now(),
      };

      setChatLogsMap((prev) => {
        const existing = prev[targetSessionId] || currentChatLog;
        const updated = [...existing, assistantMsg];
        try {
          const key =
            targetSessionId === "default"
              ? `vp-orchestrator-log-${targetProjectId ?? projectId ?? "none"}`
              : `vp-orchestrator-log-${targetProjectId ?? projectId ?? "none"}-${targetSessionId}`;
          localStorage.setItem(key, JSON.stringify(updated));
        } catch {}
        return { ...prev, [targetSessionId]: updated };
      });

      setStreamingSessionId(null);
      streamingSessionIdRef.current = null;
      setIsStreaming(false);
      setStreamingText(null);
      setStreamingPhase(null);
      setBusy(false);

      if (actionMode === "auto" && pendAll.length > 0) {
        setTimeout(() => {
          autoResolveRef.current?.(pendAll, currentChatLog.length);
        }, 300);
      }
    },
    [projectId, setChatLogsMap, actionMode],
  );

  const sendMessage = useCallback(
    async (
      userText: string,
      attachedFiles?: { id: string; name: string; size: number; type: string; content?: string }[],
    ) => {
      if (projectId == null || busy || isStreaming || abortRef.current) return;
      const currentFiles = attachedFiles !== undefined ? attachedFiles : attachments;
      const cleanInput = userText.trim();
      if (!cleanInput && currentFiles.length === 0) return;

      setChatInput("");

      let messageForAgent = cleanInput;
      let displayContent = cleanInput;

      if (currentFiles.length > 0) {
        const fileDescs = currentFiles
          .map(
            (f) =>
              `- ${f.name} (${Math.round(f.size / 1024)} КБ, ${f.type || "файл"})${
                f.content ? `:\n\`\`\`\n${f.content.slice(0, 4000)}\n\`\`\`` : ""
              }`,
          )
          .join("\n");
        messageForAgent = `${cleanInput ? cleanInput + "\n\n" : ""}[Вложенные файлы:\n${fileDescs}]`;
        displayContent = `${cleanInput ? cleanInput + "\n\n" : ""}[📎 Вложено файлов: ${currentFiles.length} (${currentFiles.map((f) => f.name).join(", ")})]`;
        setAttachments([]);
      }

      if (actionMode === "auto") {
        messageForAgent = `[РЕЖИМ: АВТОНОМНЫЙ. Выполняй действия и сохраняй изменения сразу, без ожидания подтверждения]\n\n${messageForAgent}`;
      }

      const targetSessionId = activeSessionId;
      const targetProjectId = currentSession?.projectId ?? projectId;

      userCancelRef.current = false;
      const ac = new AbortController();
      abortRef.current = ac;
      setBusy(true);
      setOpen(true);
      setStreamingSessionId(targetSessionId);
      streamingSessionIdRef.current = targetSessionId;

      const userMsg: ChatMsg = { role: "user", content: displayContent || cleanInput, created_at: Date.now() };
      const currentLogs = chatLogsMap[targetSessionId] || [];
      const next: ChatMsg[] = [...currentLogs, userMsg];

      setChatLogsMap((prev) => {
        const updated = { ...prev, [targetSessionId]: next };
        try {
          const key =
            targetSessionId === "default"
              ? `vp-orchestrator-log-${targetProjectId ?? "none"}`
              : `vp-orchestrator-log-${targetProjectId ?? "none"}-${targetSessionId}`;
          localStorage.setItem(key, JSON.stringify(next));
        } catch {}
        return updated;
      });

      if (cleanInput) {
        setSessions((prev) =>
          (prev || []).map((s) =>
            s.id === targetSessionId && (s.title.startsWith("Чат") || s.title === "Основной чат")
              ? { ...s, title: cleanInput.slice(0, 26).trim() || s.title, updatedAt: Date.now() }
              : s,
          ),
        );
      }

      setIsStreaming(true);
      setStreamingText("");
      setStreamingPhase("Анализирую граф проекта…");

      let streamFinished = false;

      try {
        const historyPayload = next.slice(-9, -1).map((m) => ({ role: m.role, content: m.content }));
        const r = await api.dbOrchestratorChatStream(
          targetProjectId,
          messageForAgent,
          historyPayload,
          {
            onDelta: (delta) => {
              setStreamingText((prev) => (prev ?? "") + delta);
            },
            onPhase: (_phase, detail) => {
              setStreamingPhase(detail || "Я думаю, пожалуйста, подождите…");
            },
            onDone: (result) => {
              streamFinished = true;
              setIsStreaming(false);
              setStreamingText(null);
              setStreamingPhase(null);
              handleChatResult(result, targetSessionId, next, targetProjectId);
            },
            onError: (err) => {
              streamFinished = true;
              setIsStreaming(false);
              setStreamingText(null);
              setStreamingPhase(null);
              setStreamingSessionId(null);
              streamingSessionIdRef.current = null;
              setBusy(false);
              const errMsg: ChatMsg = { role: "assistant", content: `Ошибка: ${err}`, created_at: Date.now() };
              setChatLogsMap((prev) => {
                const existing = prev[targetSessionId] || next;
                const updated = [...existing, errMsg];
                try {
                  const key =
                    targetSessionId === "default"
                      ? `vp-orchestrator-log-${targetProjectId ?? "none"}`
                      : `vp-orchestrator-log-${targetProjectId ?? "none"}-${targetSessionId}`;
                  localStorage.setItem(key, JSON.stringify(updated));
                } catch {}
                return { ...prev, [targetSessionId]: updated };
              });
            },
          },
          ac.signal,
        );

        if (!streamFinished && r) {
          setIsStreaming(false);
          setStreamingText(null);
          setStreamingPhase(null);
          handleChatResult(r, targetSessionId, next, targetProjectId);
        }
      } catch (e: any) {
        if (ac.signal.aborted) {
          if (userCancelRef.current) {
            userCancelRef.current = false;
            const cancelMsg: ChatMsg = {
              role: "assistant",
              content: "Генерация отменена пользователем.",
              created_at: Date.now(),
            };
            setChatLogsMap((prev) => {
              const existing = prev[targetSessionId] || next;
              return { ...prev, [targetSessionId]: [...existing, cancelMsg] };
            });
          }
          setStreamingSessionId(null);
          streamingSessionIdRef.current = null;
          setIsStreaming(false);
          setBusy(false);
          return;
        }

        // Graceful fallback to non-streaming if stream endpoint had an issue
        try {
          const fallbackRes = await api.dbOrchestratorChat(
            targetProjectId,
            messageForAgent,
            next.slice(-9, -1).map((m) => ({ role: m.role, content: m.content })),
            { signal: ac.signal },
          );
          handleChatResult(fallbackRes, targetSessionId, next, targetProjectId);
        } catch (fallbackErr: any) {
          const unauthorized = fallbackErr instanceof ApiError && fallbackErr.status === 401;
          const errMsg: ChatMsg = {
            role: "assistant",
            content: unauthorized
              ? "Сессия истекла или требуется авторизация в студии."
              : `Ошибка: ${fallbackErr instanceof Error ? fallbackErr.message : fallbackErr}`,
            created_at: Date.now(),
          };
          setChatLogsMap((prev) => {
            const existing = prev[targetSessionId] || next;
            return { ...prev, [targetSessionId]: [...existing, errMsg] };
          });
        }
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
        setStreamingSessionId(null);
        streamingSessionIdRef.current = null;
        setBusy(false);
        setIsStreaming(false);
        setStreamingText(null);
        setStreamingPhase(null);
      }
    },
    [projectId, currentSession, busy, isStreaming, chatLogsMap, activeSessionId, actionMode, setOpen, setChatLogsMap, setSessions, handleChatResult],
  );

  const sendChat = useCallback(async () => {
    if (!chatInput.trim() && attachments.length === 0) return;
    await sendMessage(chatInput, attachments);
  }, [chatInput, attachments, sendMessage]);

  const onToggleMaximized = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsMaximized((prev) => !prev);
    },
    [],
  );

  // Перетаскивание за заголовок (drag & move) с аппаратным ускорением и rAF
  const isDraggingRef = useRef(false);

  const onHeaderMouseDown = useCallback(
    (e: ReactMouseEvent) => {
      if ((e.target as HTMLElement).closest("button, input, select, textarea, [data-testid], [role]")) {
        return;
      }
      if (isMaximized) return;
      e.preventDefault();

      isDraggingRef.current = true;
      const startMouseX = e.clientX;
      const startMouseY = e.clientY;
      const startRectX = currentRectRef.current.x;
      const startRectY = currentRectRef.current.y;

      let nextX = startRectX;
      let nextY = startRectY;

      if (panelRef.current) {
        panelRef.current.style.willChange = "left, top";
        panelRef.current.style.transition = "none";
      }
      document.body.style.userSelect = "none";
      document.body.style.cursor = "grabbing";

      const onMouseMove = (ev: MouseEvent) => {
        if (!isDraggingRef.current) return;
        const dx = ev.clientX - startMouseX;
        const dy = ev.clientY - startMouseY;

        const minX = isChatsOpenRef.current && !isMaximized ? 270 : 0;
        nextX = Math.max(minX, Math.min(window.innerWidth - 120, startRectX + dx));
        nextY = Math.max(0, Math.min(window.innerHeight - 80, startRectY + dy));
        currentRectRef.current.x = nextX;
        currentRectRef.current.y = nextY;

        if (rafIdRef.current == null) {
          rafIdRef.current = requestAnimationFrame(() => {
            rafIdRef.current = null;
            if (panelRef.current) {
              panelRef.current.style.left = `${currentRectRef.current.x}px`;
              panelRef.current.style.top = `${currentRectRef.current.y}px`;
            }
          });
        }
      };

      const onMouseUp = () => {
        isDraggingRef.current = false;
        if (rafIdRef.current != null) {
          cancelAnimationFrame(rafIdRef.current);
          rafIdRef.current = null;
        }
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        if (panelRef.current) {
          panelRef.current.style.willChange = "auto";
        }
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);

        // Синхронизация со стейтом и localStorage происходит ровно один раз по окончании drag
        setRect((prev) => ({
          ...prev,
          x: nextX,
          y: nextY,
        }));
      };

      window.addEventListener("mousemove", onMouseMove, { passive: true });
      window.addEventListener("mouseup", onMouseUp);
    },
    [isMaximized, setRect],
  );

  // Изменение размера окна (resizing) по 4 сторонам и 4 углам с аппаратным ускорением и rAF
  type ResizeDirection = "e" | "w" | "s" | "n" | "se" | "sw" | "ne" | "nw";

  const getResizeCursor = (dir: ResizeDirection) => {
    switch (dir) {
      case "e":
      case "w":
        return "ew-resize";
      case "n":
      case "s":
        return "ns-resize";
      case "se":
      case "nw":
        return "nwse-resize";
      case "ne":
      case "sw":
        return "nesw-resize";
    }
  };

  const isResizingRef = useRef(false);

  const startResize = useCallback(
    (dir: ResizeDirection, e: ReactMouseEvent) => {
      if (isMaximized) return;
      e.preventDefault();
      e.stopPropagation();

      isResizingRef.current = true;
      const startX = e.clientX;
      const startY = e.clientY;
      const initialRect = { ...currentRectRef.current };

      const minW = 380;
      const minH = 340;
      const maxW = window.innerWidth - 10;
      const maxH = window.innerHeight - 10;

      let newW = initialRect.width;
      let newH = initialRect.height;
      let newX = initialRect.x;
      let newY = initialRect.y;

      if (panelRef.current) {
        panelRef.current.style.willChange = "left, top, width, height";
        panelRef.current.style.transition = "none";
      }
      document.body.style.userSelect = "none";
      document.body.style.cursor = getResizeCursor(dir);

      const onMouseMove = (ev: MouseEvent) => {
        if (!isResizingRef.current) return;
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;

        if (dir.includes("e")) {
          newW = Math.max(minW, Math.min(maxW - initialRect.x, initialRect.width + dx));
        }
        if (dir.includes("s")) {
          newH = Math.max(minH, Math.min(maxH - initialRect.y, initialRect.height + dy));
        }
        if (dir.includes("w")) {
          const minX = isChatsOpenRef.current ? 270 : 0;
          const potentialW = initialRect.width - dx;
          if (potentialW >= minW) {
            newW = Math.min(maxW, potentialW);
            newX = Math.max(minX, initialRect.x + dx);
          }
        }
        if (dir.includes("n")) {
          const potentialH = initialRect.height - dy;
          if (potentialH >= minH) {
            newH = Math.min(maxH, potentialH);
            newY = Math.max(0, initialRect.y + dy);
          }
        }

        currentRectRef.current = { x: newX, y: newY, width: newW, height: newH };

        if (rafIdRef.current == null) {
          rafIdRef.current = requestAnimationFrame(() => {
            rafIdRef.current = null;
            if (panelRef.current) {
              panelRef.current.style.left = `${currentRectRef.current.x}px`;
              panelRef.current.style.top = `${currentRectRef.current.y}px`;
              panelRef.current.style.width = `${currentRectRef.current.width}px`;
              panelRef.current.style.height = `${currentRectRef.current.height}px`;
            }
          });
        }
      };

      const onMouseUp = () => {
        isResizingRef.current = false;
        if (rafIdRef.current != null) {
          cancelAnimationFrame(rafIdRef.current);
          rafIdRef.current = null;
        }
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        if (panelRef.current) {
          panelRef.current.style.willChange = "auto";
        }
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);

        // Синхронизация со стейтом и localStorage происходит ровно один раз по окончании resize
        setRect({
          x: newX,
          y: newY,
          width: newW,
          height: newH,
        });
      };

      window.addEventListener("mousemove", onMouseMove, { passive: true });
      window.addEventListener("mouseup", onMouseUp);
    },
    [isMaximized, setRect],
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
                ? `План #${planId} не найден на сервере (устарел) — попроси ИИ-агента построить план заново.`
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

  useEffect(() => {
    autoResolveRef.current = (confirms: PendingConfirm[], msgIndex: number) => {
      confirms.forEach((c, ci) => {
        if (c.kind === "action_plan") void resolvePlan(msgIndex, ci, c, true);
        else if (c.kind === "remove_node") void resolveRemove(msgIndex, ci, c, true);
        else if (c.kind === "delete_projects") void resolveDeleteProjects(msgIndex, ci, c, true);
        else if (c.kind === "git_commit_push") void resolveGitPush(msgIndex, ci, c, true);
      });
    };
  }, [resolvePlan, resolveRemove, resolveDeleteProjects, resolveGitPush]);

  if (!open) return null;

  const renderChatsSidebar = () => (
    <div
      className={cn(
        "flex flex-col bg-[#0c0c0e]/98 backdrop-blur-2xl select-none",
        isMaximized
          ? "w-64 shrink-0 h-full border-r border-white/[0.08]"
          : "pointer-events-auto absolute top-0 right-[calc(100%+8px)] h-full w-64 border border-white/[0.14] rounded-2xl shadow-2xl shadow-black/90 ring-1 ring-emerald-500/20 overflow-hidden animate-in fade-in slide-in-from-right-3 duration-150",
      )}
    >
      {/* Шапка боковой панели чатов */}
      <div
        onMouseDown={!isMaximized ? onHeaderMouseDown : undefined}
        className={cn(
          "flex h-11 shrink-0 items-center justify-between border-b border-white/[0.08] px-3 bg-gradient-to-r from-emerald-950/40 via-black/40 to-black/40 select-none",
          !isMaximized ? "cursor-grab active:cursor-grabbing" : "cursor-default",
        )}
      >
        <div className="flex items-center gap-2 text-white/90">
          <MessageSquare className="h-4 w-4 text-emerald-400" />
          <span className="text-xs font-semibold tracking-wide">Диалоги</span>
          <span className="rounded-full bg-white/[0.06] border border-white/10 px-1.5 py-0.2 text-[10px] font-mono text-white/60">
            {(sessions || []).length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleCreateNewChat}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-300 hover:text-white bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 rounded-md px-2 py-0.5 transition-colors cursor-pointer"
            title="Создать новый чат"
          >
            <Plus className="h-3 w-3" />
            <span>Новый</span>
          </button>
        </div>
      </div>

      {/* Список сессий диалогов */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {(sessions || []).map((s) => {
          const isActive = s.id === activeSessionId;
          const sLogs = chatLogsMap[s.id] || [];
          const lastMsg = sLogs[sLogs.length - 1];
          const msgCount = sLogs.length;
          const hasUnresolved =
            lastMsg?.confirms?.some((c) => !c.resolved) ||
            (lastMsg?.confirm && !lastMsg.confirm.resolved);
          const isSessionThinking = (isStreaming || busy) && s.id === streamingSessionId;
          const isEditing = editingSessionId === s.id;

          const snippet = lastMsg
            ? cleanOrchestratorText(lastMsg.content) ||
              (lastMsg.role === "user" ? "Запрос пользователя" : "Действия по проекту")
            : "Новый пустой диалог";

          return (
            <div
              key={s.id}
              onClick={() => handleSwitchChat(s.id)}
              className={cn(
                "group relative flex flex-col gap-1 rounded-xl p-2.5 cursor-pointer transition-all border",
                isActive
                  ? "bg-emerald-950/40 border-emerald-500/40 text-emerald-100 shadow-sm shadow-emerald-950/50 ring-1 ring-emerald-500/20"
                  : "bg-white/[0.02] border-white/[0.06] text-white/70 hover:bg-white/[0.05] hover:border-white/10 hover:text-white",
              )}
            >
              <div className="flex items-center justify-between gap-1.5">
                {isEditing ? (
                  <div
                    className="flex items-center gap-1 w-full"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      // eslint-disable-next-line jsx-a11y/no-autofocus
                      autoFocus
                      type="text"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveRename(s.id);
                        if (e.key === "Escape") setEditingSessionId(null);
                      }}
                      className="bg-black/90 border border-emerald-500/60 rounded px-1.5 py-0.5 text-xs text-white outline-none w-full ring-1 ring-emerald-500/30"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveRename(s.id)}
                      className="p-1 text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/20 rounded cursor-pointer shrink-0"
                      title="Сохранить название"
                    >
                      <Check className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingSessionId(null)}
                      className="p-1 text-white/40 hover:text-white hover:bg-white/[0.08] rounded cursor-pointer shrink-0"
                      title="Отмена"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-1.5 min-w-0 flex-1">
                      <span
                        className={cn(
                          "h-2 w-2 rounded-full shrink-0 transition-all",
                          isActive
                            ? "bg-emerald-400 shadow-[0_0_6px_#34d399]"
                            : "bg-white/20 group-hover:bg-white/40",
                        )}
                      />
                      <span
                        onDoubleClick={(e) => handleStartRename(s, e)}
                        title="Нажмите на карандаш или дважды кликните для переименования"
                        className="truncate text-xs font-medium"
                      >
                        {s.title}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => handleStartRename(s, e)}
                        className="opacity-0 group-hover:opacity-100 text-white/35 hover:text-emerald-300 p-0.5 rounded transition-all cursor-pointer shrink-0"
                        title="Переименовать диалог"
                      >
                        <Pencil className="h-2.5 w-2.5" />
                      </button>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {isSessionThinking && (
                        <Loader2 className="h-3 w-3 animate-spin text-emerald-400" />
                      )}
                      {hasUnresolved && !isSessionThinking && (
                        <span
                          className="h-2 w-2 rounded-full bg-amber-400 animate-pulse"
                          title="Требует решения"
                        />
                      )}
                      <span className="text-[10px] text-white/40 font-mono">
                        {msgCount}
                      </span>
                      {(sessions || []).length > 1 && (
                        <button
                          type="button"
                          onClick={(e) => handleDeleteChat(s.id, e)}
                          className="opacity-0 group-hover:opacity-100 text-white/40 hover:text-red-400 p-0.5 rounded transition-all cursor-pointer"
                          title="Удалить диалог"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* Snippet preview */}
              <div className="text-[10px] text-white/40 line-clamp-2 leading-relaxed pl-3.5 pr-1">
                {snippet}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );

  return (
    <div
      ref={panelRef}
      style={
        isMaximized
          ? {
              left: "64px",
              top: 0,
              width: "calc(100vw - 64px)",
              height: "100vh",
            }
          : {
              left: `${rect.x}px`,
              top: `${rect.y}px`,
              width: `${rect.width}px`,
              height: `${rect.height}px`,
            }
      }
      className={cn(
        "fixed z-50 transition-none",
        isMaximized
          ? "pointer-events-auto flex flex-row overflow-hidden bg-[#0c0c0e]/95 backdrop-blur-2xl border-l border-white/[0.14]"
          : "pointer-events-none",
      )}
    >
      {isChatsOpen && renderChatsSidebar()}

      <div
        className={cn(
          "flex flex-col overflow-hidden",
          isMaximized
            ? "flex-1 min-w-0 h-full"
            : "pointer-events-auto relative w-full h-full bg-[#0c0c0e]/95 backdrop-blur-2xl border border-white/[0.14] shadow-2xl shadow-black/90 ring-1 ring-emerald-500/20 rounded-2xl",
        )}
      >
        {/* Ручки изменения размера (resizing handles) по всем 4 сторонам и 4 углам */}
        {!isMaximized && (
          <>
            <div
              onMouseDown={(e) => startResize("e", e)}
              className="absolute top-0 right-0 w-2 h-full cursor-ew-resize z-20"
            />
            <div
              onMouseDown={(e) => startResize("w", e)}
              className="absolute top-0 left-0 w-2 h-full cursor-ew-resize z-20"
            />
            <div
              onMouseDown={(e) => startResize("s", e)}
              className="absolute bottom-0 left-0 w-full h-2 cursor-ns-resize z-20"
            />
            <div
              onMouseDown={(e) => startResize("n", e)}
              className="absolute top-0 left-0 w-full h-2 cursor-ns-resize z-20"
            />

            <div
              onMouseDown={(e) => startResize("se", e)}
              className="absolute bottom-0 right-0 w-5 h-5 cursor-nwse-resize z-30"
            />
            <div
              onMouseDown={(e) => startResize("sw", e)}
              className="absolute bottom-0 left-0 w-5 h-5 cursor-nesw-resize z-30"
            />
            <div
              onMouseDown={(e) => startResize("ne", e)}
              className="absolute top-0 right-0 w-5 h-5 cursor-nesw-resize z-30"
            />
            <div
              onMouseDown={(e) => startResize("nw", e)}
              className="absolute top-0 left-0 w-5 h-5 cursor-nwse-resize z-30"
            />

            {/* Декоративный уголок-грип в нижнем правом углу */}
            <div
              onMouseDown={(e) => startResize("se", e)}
              className="absolute bottom-1 right-1 w-3.5 h-3.5 cursor-nwse-resize z-30 flex items-center justify-center opacity-40 hover:opacity-100 transition-opacity pointer-events-auto"
              title="Потяните за угол для изменения размера"
            >
              <div className="w-2.5 h-2.5 border-r-2 border-b-2 border-white/60 rounded-br-sm" />
            </div>
          </>
        )}

        {/* Заголовок окна (drag handle для перемещения) */}
        <div
          onMouseDown={onHeaderMouseDown}
          className={cn(
            "flex h-11 shrink-0 items-center gap-2 border-b border-white/[0.08] px-3.5 bg-gradient-to-r from-emerald-950/40 via-black/50 to-black/50 select-none",
            !isMaximized ? "cursor-grab active:cursor-grabbing" : "cursor-default",
          )}
        >
          <div className="flex min-w-0 flex-1 items-center gap-2 px-0.5 text-[11px]">
            {/* Кнопка 6 точек для открытия панели диалогов из левого ребра */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                toggleChats();
              }}
              onMouseDown={(e) => e.stopPropagation()}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-lg border transition-all cursor-pointer select-none shrink-0",
                isChatsOpen
                  ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300 shadow-sm shadow-emerald-950/50"
                  : "bg-white/[0.04] border-white/10 text-white/60 hover:bg-white/[0.08] hover:text-white",
              )}
              title={isChatsOpen ? "Скрыть панель диалогов" : "Показать список диалогов (чатов)"}
            >
              {isChatsOpen ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
            </button>

            <div className="relative shrink-0">
              <OrchestratorAvatar size="sm" pulse={isStreaming && streamingSessionId === activeSessionId} />
              <span className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400 border border-[#0c0c0e] shadow-[0_0_6px_#34d399]" />
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <span className="font-semibold text-white/95 text-xs tracking-wide">ИИ-агент</span>
              <span className="rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.2 text-[9px] font-mono text-emerald-300">
                AI Director
              </span>
              <span
                className="text-[11px] text-white/40 font-normal truncate max-w-[140px] hidden sm:inline"
                title={currentSession?.title}
              >
                · «{currentSession?.title || "Основной чат"}»
              </span>
            </div>
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
          className="h-7 w-7 shrink-0 p-0 text-white/50 hover:text-white hover:bg-white/[0.08]"
          onClick={onToggleMaximized}
          title={isMaximized ? "Восстановить плавающее окно" : "Развернуть на весь экран"}
        >
          {isMaximized ? (
            <Minimize2 className="h-3.5 w-3.5" />
          ) : (
            <Maximize2 className="h-3.5 w-3.5" />
          )}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-7 shrink-0 p-0 text-white/50 hover:text-white hover:bg-white/[0.08]"
          onClick={() => setOpen(false)}
          title="Скрыть окно (открыть можно из левой панели)"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
          <div
            ref={scrollContainerRef}
            className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-3.5 space-y-3.5"
          >
            {chatLog.length === 0 && !isStreaming ? (
              <div className="flex flex-col items-center justify-center py-8 text-center animate-in fade-in duration-300">
                <div className="relative mb-3">
                  <OrchestratorAvatar size="lg" />
                  <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full bg-emerald-400 border-2 border-[#0c0c0e] shadow-[0_0_8px_#34d399]" />
                </div>
                <h4 className="text-xs font-semibold text-white tracking-wide mb-1">
                  ИИ-агент видеостудии
                </h4>
                <p className="text-[11px] text-white/45 max-w-sm mb-3.5 leading-relaxed">
                  Управление пайплайном, графом нод, генерацией и настройками проекта.
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={projectId == null || busy || isStreaming}
                  onClick={() => void sendMessage("Покажи доступные действия по проекту")}
                  className="h-7 gap-1.5 rounded-full border border-white/20 hover:border-emerald-400/60 bg-transparent hover:bg-white/[0.06] px-3.5 text-xs font-medium text-white/80 hover:text-white shadow-none transition-all active:scale-95 cursor-pointer"
                >
                  <Settings className="h-3.5 w-3.5 text-emerald-400" />
                  <span>Покажи доступные действия</span>
                </Button>
              </div>
            ) : null}

            {chatLog.map((m, i) => {
              const isUser = m.role === "user";

              if (isUser) {
                return (
                  <div key={i} className="flex justify-end max-w-4xl mx-auto w-full">
                    <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-[#102738] to-[#0c1c28] border border-cyan-500/35 px-4 py-3 shadow-md shadow-black/40">
                      <div className="flex items-center justify-between gap-3 mb-1.5">
                        <div className="flex items-center gap-1.5 text-cyan-300">
                          <Clapperboard className="h-3 w-3 text-cyan-400" />
                          <span className="text-[10px] font-semibold tracking-wider uppercase text-cyan-200">
                            Режиссёр
                          </span>
                        </div>
                        {m.created_at ? (
                          <span className="text-[10px] text-cyan-300/40 font-mono">
                            {formatTime(m.created_at)}
                          </span>
                        ) : null}
                      </div>
                      <div className="text-xs text-white/95 leading-relaxed whitespace-pre-wrap selection:bg-cyan-500/30 font-medium">
                        {m.content}
                      </div>
                    </div>
                  </div>
                );
              }

              return (
                <div key={i} className="flex gap-3 max-w-4xl mx-auto w-full items-start min-w-0">
                  <OrchestratorAvatar size="md" />
                  <div className="flex-1 min-w-0 rounded-2xl rounded-tl-sm bg-gradient-to-b from-[#161722]/98 to-[#111218]/98 border border-white/[0.12] p-4 shadow-xl backdrop-blur-md overflow-hidden">
                    <div className="flex items-center justify-between gap-2 mb-2 border-b border-white/[0.08] pb-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold tracking-wide bg-gradient-to-r from-white via-emerald-100 to-white/90 bg-clip-text text-transparent">
                          ИИ-агент
                        </span>
                        <span className="rounded-full bg-emerald-500/10 border border-emerald-500/25 px-2 py-0.2 text-[9px] text-emerald-300 font-mono">
                          AI Director
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        {m.created_at ? (
                          <span className="text-[10px] text-white/35 font-mono">
                            {formatTime(m.created_at)}
                          </span>
                        ) : null}
                        <CopyButton text={m.content} />
                      </div>
                    </div>

                    <div className="text-xs leading-relaxed text-white/90 space-y-2 break-words [overflow-wrap:anywhere] min-w-0">
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

            {isStreaming && streamingSessionId === activeSessionId && (
              <div className="flex gap-3 max-w-4xl mx-auto w-full items-start min-w-0 animate-in fade-in duration-200">
                <OrchestratorAvatar size="md" pulse />
                <div className="flex-1 min-w-0 rounded-2xl rounded-tl-sm bg-[#161722]/98 border border-emerald-500/35 p-4 shadow-xl backdrop-blur-xl overflow-hidden">
                  <div className="flex items-center justify-between gap-2 mb-2 border-b border-white/[0.08] pb-1.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-emerald-300">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-400" />
                      <span>{streamingPhase || "Я думаю, пожалуйста, подождите…"}</span>
                    </div>
                    <span className="text-[10px] text-white/40 font-mono">{elapsedSec}с</span>
                  </div>
                  {streamingText ? (
                    <div className="text-xs leading-relaxed text-white/90 space-y-2 break-words [overflow-wrap:anywhere] min-w-0">
                      <MarkdownRenderer content={streamingText} />
                      <span className="inline-block w-1.5 h-3.5 ml-1 bg-emerald-400 animate-pulse align-middle" />
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 py-1 text-white/45 text-xs italic">
                      <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
                      <span>ИИ-агент формулирует ответ…</span>
                    </div>
                  )}
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Bottom input card styled after reference */}
          <div className="shrink-0 p-3 pt-1.5 bg-[#0c0c0e]/95 border-t border-white/[0.08]">
            <div className="rounded-2xl border border-white/[0.12] bg-[#141520]/95 p-2.5 shadow-xl transition-all focus-within:border-emerald-500/50 focus-within:ring-1 focus-within:ring-emerald-500/30">
              {/* Attachment chips */}
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pb-2 border-b border-white/[0.06] mb-2">
                  {attachments.map((file) => (
                    <span
                      key={file.id}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.06] border border-white/10 px-2.5 py-1 text-[11px] text-white/90"
                    >
                      <Paperclip className="h-3 w-3 text-emerald-400 shrink-0" />
                      <span className="max-w-[130px] truncate">{file.name}</span>
                      <span className="text-[9px] text-white/40 font-mono">
                        ({Math.round(file.size / 1024)} КБ)
                      </span>
                      <button
                        type="button"
                        onClick={() => removeAttachment(file.id)}
                        className="text-white/40 hover:text-white ml-0.5 cursor-pointer"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Textarea */}
              <textarea
                ref={textareaRef}
                value={chatInput}
                rows={1}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !busy && !isStreaming) {
                    e.preventDefault();
                    void sendChat();
                  }
                }}
                disabled={projectId == null || busy || isStreaming}
                placeholder={
                  projectId == null
                    ? "Сначала открой проект"
                    : isStreaming || busy
                      ? streamingSessionId === activeSessionId
                        ? "Я думаю, пожалуйста, подождите… жми Отмена для остановки"
                        : "ИИ-агент занят в другом диалоге… жми Отмена для остановки"
                      : "Введите сообщение или задачу..."
                }
                style={{ outline: "none", boxShadow: "none" }}
                className="w-full resize-none bg-transparent px-1 text-xs text-white placeholder:text-white/35 outline-none border-none focus:outline-none focus:border-none focus:ring-0 focus-visible:outline-none focus-visible:ring-0 min-h-[28px] max-h-36 leading-relaxed"
              />

              {/* Controls bar (matches reference screenshot 2) */}
              <div className="flex items-center justify-between gap-2 pt-1.5">
                {/* Left controls: Attachment button + Action mode dropdown */}
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={projectId == null || busy || isStreaming}
                    className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/[0.04] border border-white/10 text-white/60 hover:text-white hover:bg-white/[0.09] transition-colors disabled:opacity-40 cursor-pointer"
                    title="Прикрепить файл (картинку, документ, скрипт)"
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={handleFileSelect}
                    className="hidden"
                  />

                  {/* Режим выполнения действий (С подтверждением / Автоматически) */}
                  <div className="relative" ref={modeMenuRef}>
                    <button
                      type="button"
                      onClick={() => setIsModeOpen((prev) => !prev)}
                      className="inline-flex items-center gap-1.5 h-7 rounded-lg border border-white/10 bg-white/[0.04] text-white/80 hover:bg-white/[0.08] hover:text-white px-2 text-[11px] font-medium transition-colors cursor-pointer select-none"
                      title="Режим выполнения действий ИИ-агентом"
                    >
                      {actionMode === "auto" ? (
                        <Zap className="h-3 w-3 text-emerald-400 shrink-0" />
                      ) : (
                        <Shield className="h-3 w-3 text-emerald-400 shrink-0" />
                      )}
                      <span className="hidden xs:inline sm:inline">
                        {actionMode === "auto" ? "Автоматически" : "С подтверждением"}
                      </span>
                      <ChevronDown
                        className={cn(
                          "h-3 w-3 opacity-60 transition-transform duration-150",
                          isModeOpen && "rotate-180",
                        )}
                      />
                    </button>

                    {isModeOpen && (
                      <div
                        className="absolute left-0 bottom-full mb-1.5 z-50 w-64 rounded-xl border border-white/[0.14] bg-[#12131a] p-1.5 shadow-2xl shadow-black/95 backdrop-blur-xl animate-in fade-in zoom-in-95 duration-100"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-white/40 border-b border-white/[0.08] mb-1">
                          Режим выполнения действий
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setActionMode("ask");
                            setIsModeOpen(false);
                          }}
                          className={cn(
                            "w-full flex items-start gap-2 rounded-lg p-2 text-left transition-colors cursor-pointer",
                            actionMode === "ask"
                              ? "bg-emerald-500/15 text-emerald-200 border border-emerald-500/30"
                              : "hover:bg-white/[0.06] text-white/80",
                          )}
                        >
                          <Shield className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium">С подтверждением</div>
                            <div className="text-[10px] text-white/45 leading-tight">
                              Выполнение действий после подтверждения оператором
                            </div>
                          </div>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setActionMode("auto");
                            setIsModeOpen(false);
                          }}
                          className={cn(
                            "w-full flex items-start gap-2 rounded-lg p-2 text-left transition-colors mt-0.5 cursor-pointer",
                            actionMode === "auto"
                              ? "bg-emerald-500/15 text-emerald-200 border border-emerald-500/30"
                              : "hover:bg-white/[0.06] text-white/80",
                          )}
                        >
                          <Zap className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium">Автоматически</div>
                            <div className="text-[10px] text-white/45 leading-tight">
                              Автоматическое выполнение всех действий
                            </div>
                          </div>
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Right controls: Model picker (moved here) + Send button */}
                <div className="flex items-center gap-1.5">
                  <TextLlmPicker className="h-7 max-w-[140px] sm:max-w-[180px] bg-white/[0.04] hover:bg-white/[0.08] border-white/10 rounded-lg text-[11px] text-white/70 hover:text-white cursor-pointer transition-colors" />

                  <Button
                    type="button"
                    size="sm"
                    variant="default"
                    disabled={
                      projectId == null ||
                      busy ||
                      isStreaming ||
                      (!chatInput.trim() && attachments.length === 0)
                    }
                    onClick={() => void sendChat()}
                    className="h-7 w-7 p-0 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-md shadow-emerald-950/40 transition-all shrink-0 flex items-center justify-center disabled:opacity-40 cursor-pointer"
                    title="Отправить (Enter)"
                  >
                    <ArrowUp className="h-3.5 w-3.5 stroke-[2.5]" />
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
