"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  consumeOrchStream,
  isAbortError,
  orchTaskModeEnabled,
  OrchApiError,
  postOrchApprove,
  postOrchKill,
  postOrchMessage,
  readOrchProjectId,
  postOrchReject,
  probeOrchAuth,
  readOrchBoot,
  readOrchToken,
  sleepWithSignal,
  writeOrchToken,
  type OrchClientConfig,
} from "@/lib/orchestratorApi";
import { loadOperatorToken } from "@/lib/api-operator-token";
import { getToken } from "@/lib/identity-api";
import {
  currentTask,
  initialOrchScreen,
  reduceOrchScreen,
  type OrchScreenState,
} from "@/lib/orchestratorSession";

export type ConnPhase = "idle" | "live" | "reconnecting" | "restored";

/** Сколько ждём подтверждение kill. Дольше — ошибка и «Повторить». */
const KILL_CONFIRM_MS = 12_000;

const STREAM_IDLE_MS = 45_000;

function approvalPostAccepted(
  result: { ok?: boolean; matched?: boolean; approval_id?: string } | undefined,
  approvalId: string,
): boolean {
  if (!result || result.ok === false || result.matched === false) return false;
  if (typeof result.approval_id === "string" && result.approval_id !== approvalId) return false;
  return true;
}

export interface OrchestratorChatOptions {
  session?: string;
  projectId?: number | null;
}

export function useOrchestratorChat(options?: OrchestratorChatOptions) {
  const sessionOverride = options?.session;
  const scoped = options != null && "projectId" in options;
  const scopedProjectId = options?.projectId ?? null;
  const boot = useMemo(() => {
    const base = readOrchBoot();
    return sessionOverride ? { ...base, session: sessionOverride } : base;
  }, [sessionOverride]);
  const [token, setToken] = useState("");
  const [operatorToken, setOperatorToken] = useState("");
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [conn, setConn] = useState<ConnPhase>("idle");
  const [sendError, setSendError] = useState("");
  const [pending, setPending] = useState(false);
  const [decisionPendingId, setDecisionPendingId] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<{ id: string; message: string } | null>(null);
  const [ackMs, setAckMs] = useState<number | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [state, dispatch] = useReducer(reduceOrchScreen, undefined, initialOrchScreen);
  const lastSeq = useRef(0);
  const seen = useRef(new Set<number>());
  const sendStartedAt = useRef<number | null>(null);
  const decisionLock = useRef(false);

  useEffect(() => {
    let alive = true;
    // Эта линия: движок пускает по входу в студию (IdentityMiddleware), отдельного
    // ORCH-токена нет. Свой токен чата (тесты, мок) важнее, иначе — токен входа.
    setToken(readOrchToken() || getToken() || "");
    void loadOperatorToken().then((value) => {
      if (!alive) return;
      setOperatorToken(value ?? "");
      setReady(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const cfg = useMemo<OrchClientConfig>(
    () => ({
      apiBase: boot.apiBase,
      session: boot.session,
      token,
      operatorToken,
      scenario: boot.scenario,
    }),
    [boot.apiBase, boot.scenario, boot.session, operatorToken, token],
  );

  useEffect(() => {
    if (!ready || blocked) return;
    const ac = new AbortController();
    let stopped = false;
    let attempt = 0;

    const loop = async () => {
      while (!stopped) {
        try {
          await consumeOrchStream(cfg, {
            signal: ac.signal,
            lastEventId: lastSeq.current,
            idleMs: STREAM_IDLE_MS,
            onOpen: () => {
              if (stopped) return;
              setConn(attempt > 0 ? "restored" : "live");
            },
            onEvent: (event) => {
              if (stopped) return;
              if (seen.current.has(event.seq)) return;
              seen.current.add(event.seq);
              if (event.seq > lastSeq.current) lastSeq.current = event.seq;
              if (event.type === "ack" && sendStartedAt.current != null) {
                setAckMs(performance.now() - sendStartedAt.current);
              }
              dispatch({ type: "event", event });
            },
          });
          if (stopped || ac.signal.aborted) return;
          attempt += 1;
          setConn("reconnecting");
          await sleepWithSignal(Math.min(300 * attempt, 2000), ac.signal);
        } catch (error) {
          if (stopped || ac.signal.aborted || isAbortError(error)) return;
          if (error instanceof OrchApiError && (error.status === 401 || error.status === 403)) {
            setAuthError(error.message);
            setBlocked(true);
            setConn("idle");
            return;
          }
          attempt += 1;
          setConn("reconnecting");
          try {
            await sleepWithSignal(Math.min(300 * attempt, 2000), ac.signal);
          } catch {
            return;
          }
        }
      }
    };

    void loop();
    return () => {
      stopped = true;
      ac.abort();
    };
  }, [blocked, cfg, ready, epoch]);

  const resync = useCallback(() => {
    lastSeq.current = 0;
    seen.current = new Set();
    dispatch({ type: "reset" });
    setConn("idle");
    setEpoch((value) => value + 1);
  }, []);

  const noteAuth = useCallback((error: unknown): boolean => {
    if (error instanceof OrchApiError && (error.status === 401 || error.status === 403)) {
      setAuthError(error.message);
      setBlocked(true);
      return true;
    }
    return false;
  }, []);

  const submitToken = useCallback(
    async (value: string) => {
      const next = value.trim();
      if (!next) return;
      try {
        await probeOrchAuth({ ...cfg, token: next });
      } catch (error) {
        if (error instanceof OrchApiError && (error.status === 401 || error.status === 403)) {
          setAuthError(error.message);
        } else if (!noteAuth(error)) {
          setAuthError(error instanceof Error ? error.message : "Токен не удалось проверить. Повторите.");
        }
        return;
      }
      writeOrchToken(next);
      lastSeq.current = 0;
      seen.current = new Set();
      sendStartedAt.current = null;
      setAckMs(null);
      dispatch({ type: "reset" });
      setAuthError("");
      setSendError("");
      setDecisionError(null);
      setDecisionPendingId(null);
      setConn("idle");
      setBlocked(false);
      setToken(next);
    },
    [cfg, noteAuth],
  );

  const retryAccess = useCallback(() => {
    setAuthError("");
    setConn("idle");
    setBlocked(false);
  }, []);

  const sendMessage = useCallback(
    async (text: string, mode?: "task") => {
      const trimmed = text.trim();
      if (!trimmed || !ready || blocked) return;
      const localId = `user-${Date.now()}-${state.messages.length}`;
      dispatch({
        type: "user-message",
        id: localId,
        text: trimmed,
        taskId: null,
      });
      sendStartedAt.current = performance.now();
      setAckMs(null);
      setPending(true);
      const asTask = orchTaskModeEnabled() && mode === "task";
      const projectId = scoped ? scopedProjectId : readOrchProjectId();
      const body = {
        text: trimmed,
        ...(asTask ? { mode: "task" as const } : {}),
        ...(projectId != null ? { project_id: projectId } : {}),
      };
      try {
        await postOrchMessage(cfg, body);
        setSendError("");
      } catch (error) {
        dispatch({ type: "user-message-unsent", id: localId });
        if (!noteAuth(error)) {
          setSendError(error instanceof Error ? error.message : "Сообщение не отправлено. Повторите.");
        }
      } finally {
        setPending(false);
      }
    },
    [blocked, cfg, noteAuth, ready, scoped, scopedProjectId, state.messages.length],
  );

  const decide = useCallback(
    async (approvalId: string, taskId: string | null, decision: "approved" | "rejected") => {
      if (decisionLock.current) return;
      const card = state.approvals.find((item) => item.id === approvalId);
      if (!card?.serverId) {
        setDecisionError({
          id: approvalId,
          message: "Сервер не прислал номер подтверждения, поэтому решение записать нельзя. Попросите оркестратор повторить шаг.",
        });
        return;
      }
      decisionLock.current = true;
      setDecisionPendingId(approvalId);
      setDecisionError(null);
      try {
        const body = { approval_id: approvalId, task_id: taskId };
        const result =
          decision === "approved"
            ? await postOrchApprove(cfg, body)
            : await postOrchReject(cfg, { ...body, reason: "отклонено в чате" });
        if (!approvalPostAccepted(result, approvalId)) {
          setDecisionError({
            id: approvalId,
            message: "Решение не записано: сервер не подтвердил его. Обновите чат и посмотрите на свежую карточку.",
          });
          return;
        }
        dispatch({ type: "approval-resolved", id: approvalId, decision });
        setSendError("");
      } catch (error) {
        if (!noteAuth(error)) {
          setDecisionError({
            id: approvalId,
            message: error instanceof Error ? error.message : "Решение не записано. Повторите.",
          });
        }
      } finally {
        decisionLock.current = false;
        setDecisionPendingId(null);
      }
    },
    [cfg, noteAuth, state.approvals],
  );

  const dismissBlock = useCallback((approvalId: string) => {
    dispatch({ type: "approval-resolved", id: approvalId, decision: "closed" });
  }, []);

  const killTaskId = state.killNotice?.taskId ?? null;
  const killSessionId = state.killNotice?.sessionId ?? null;
  const killPhase = state.killNotice?.phase;
  useEffect(() => {
    if (killPhase !== "stopping") return;
    const timer = window.setTimeout(() => {
      dispatch({
        type: "kill-notice",
        detail: "Нет подтверждения остановки. Нажмите «Повторить»",
        paidBoundary: false,
        phase: "error",
        taskId: killTaskId,
        sessionId: killSessionId,
      });
    }, KILL_CONFIRM_MS);
    return () => window.clearTimeout(timer);
  }, [killPhase, killSessionId, killTaskId]);

  const kill = useCallback(async () => {
    const taskId = currentTask(state.tasks)?.id ?? null;
    const sessionId = boot.session;
    const notice = { taskId, sessionId };
    dispatch({ type: "kill-notice", detail: "", paidBoundary: true, phase: "stopping", ...notice });
    const ac = new AbortController();
    const timer = window.setTimeout(() => ac.abort(), KILL_CONFIRM_MS);
    const fail = (detail: string) => {
      dispatch({ type: "kill-notice", detail, paidBoundary: false, phase: "error", ...notice });
    };
    const projectId = scoped ? scopedProjectId : readOrchProjectId();
    try {
      const result = (await postOrchKill(
        cfg,
        {
          session_id: boot.session,
          reason: "остановка задачи из чата",
          ...(projectId != null ? { project_id: projectId } : {}),
        },
        ac.signal,
      )) as { ok?: boolean; detail?: string; stop_key_set?: boolean } | undefined;
      const body = result ?? {};
      if (body.stop_key_set === true && body.ok !== false) {
        dispatch({
          type: "kill-notice",
          detail: body.detail || "",
          paidBoundary: true,
          phase: "stopped",
          ...notice,
        });
        setSendError("");
        return;
      }
      const missing = body.stop_key_set !== true && body.stop_key_set !== false;
      fail(
        body.detail ||
          (missing
            ? "Сервер ответил, но не сообщил, включён ли стоп. Нажмите «Повторить»"
            : "Стоп не включился. Нажмите «Повторить»"),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Остановка не прошла. Нажмите «Повторить»";
      if (isAbortError(error)) {
        fail("Нет подтверждения остановки. Нажмите «Повторить»");
        return;
      }
      noteAuth(error);
      fail(message);
    } finally {
      window.clearTimeout(timer);
    }
  }, [boot.session, cfg, noteAuth, scoped, scopedProjectId, state.tasks]);

  return {
    boot,
    ready,
    token,
    authError,
    needsToken: blocked,
    projectId: scoped ? scopedProjectId : null,
    retryAccess,
    conn,
    sendError,
    pending,
    decisionPendingId,
    decisionError,
    ackMs,
    state: state as OrchScreenState,
    submitToken,
    sendMessage,
    decide,
    dismissBlock,
    kill,
    resync,
  };
}
