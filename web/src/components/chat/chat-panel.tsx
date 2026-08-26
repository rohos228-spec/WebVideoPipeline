"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Working } from "@/components/ui/bits";
import { GraphDiffCard } from "@/components/pipeline/graph-diff-card";
import { callTool, streamChat, type AgentEvent, type ChatHistoryItem } from "@/lib/chat-api";
import type { GraphDiff, ResetPlan } from "@/lib/types";

/**
 * Чат с оркестратором ролика.
 *
 * Лента показывает не только реплики, но и каждый вызов инструмента
 * карточкой. Это не отладочный вывод: человек должен видеть, что именно
 * система собирается сделать и во сколько это обойдётся, ДО списания.
 *
 * Три правила отрисовки:
 * 1. Цена рядом с действием.
 * 2. Всё, что требует согласия, — кнопка, а не реплика «да»: согласие
 *    доезжает до инструмента буквой, минуя модель.
 * 3. Предложение по графу рисуется той же карточкой разницы, что и на
 *    холсте: «+2 узла, сгорят видео и сборка», и кнопка «Применить».
 */

interface FeedItem {
  id: string;
  kind: "user" | "assistant" | "tool" | "tool-error";
  text?: string;
  tool?: string;
  payload?: Record<string, unknown>;
}

let seq = 0;
const nextId = () => `item-${++seq}`;

export function ChatPanel({ projectId }: { projectId: number | null }) {
  const qc = useQueryClient();
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [feed]);

  const history = useMemo<ChatHistoryItem[]>(
    () =>
      feed
        .filter((i) => i.kind === "user" || i.kind === "assistant")
        .map((i) => ({ role: i.kind === "user" ? "user" : "assistant", content: i.text ?? "" })),
    [feed],
  );

  const refresh = () => {
    if (projectId === null) {
      qc.invalidateQueries({ queryKey: ["projects"] });
      return;
    }
    qc.invalidateQueries({ queryKey: ["stages", projectId] });
    qc.invalidateQueries({ queryKey: ["project", projectId] });
    qc.invalidateQueries({ queryKey: ["graph", projectId] });
    qc.invalidateQueries({ queryKey: ["frames", projectId] });
    qc.invalidateQueries({ queryKey: ["media", projectId] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  async function send() {
    const message = draft.trim();
    if (!message || busy) return;
    setDraft("");
    setError(null);
    setBusy(true);
    setFeed((f) => [...f, { id: nextId(), kind: "user", text: message }]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      for await (const event of streamChat(message, history, projectId, ctrl.signal)) {
        setFeed((f) => appendEvent(f, event));
        if (event.type === "tool_result") refresh();
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      abortRef.current = null;
      refresh();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {feed.length === 0 && <EmptyState hasProject={projectId !== null} />}
        {feed.map((item) => (
          <FeedRow key={item.id} item={item} onChanged={refresh} />
        ))}
        {busy && (
          <div className="flex items-center justify-between">
            <Working label="думаю" />
            <button onClick={() => abortRef.current?.abort()} className="text-[12px] text-content-faint hover:text-danger">
              прервать
            </button>
          </div>
        )}
        {error && <div className="rounded-sm border border-danger bg-danger-muted px-3 py-2 text-[13px] text-danger">{error}</div>}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-border p-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder={projectId === null ? "Какой ролик нужен?" : "Что сделать с роликом?"}
          rows={2}
          className="w-full resize-none rounded-sm border border-border bg-surface-raised px-3 py-2 text-[13px] text-content outline-none placeholder:text-content-faint focus:border-accent"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[11px] text-content-faint">Enter — отправить, Shift+Enter — перенос</span>
          <Button size="sm" variant="primary" onClick={() => void send()} disabled={busy || !draft.trim()}>
            Отправить
          </Button>
        </div>
      </div>
    </div>
  );
}

function EmptyState({ hasProject }: { hasProject: boolean }) {
  return (
    <div className="space-y-2 text-[13px] text-content-muted">
      <p className="font-display text-[15px] text-content">Оркестратор ролика</p>
      {hasProject ? (
        <>
          <p>Скажите, что сделать: «покажи, где мы», «сделай картинки», «убери музыку», «добавь проверку после сценария», «пересобери схему: без героев, сразу картинки».</p>
          <p>Перестройку схемы и дорогие шаги он сначала покажет — с ценой и тем, что сгорит, — и применит только после вашего «да».</p>
        </>
      ) : (
        <p>Опишите ролик — заведу проект и проведу по шагам, показывая цену до запуска.</p>
      )}
    </div>
  );
}

function appendEvent(feed: FeedItem[], event: AgentEvent): FeedItem[] {
  switch (event.type) {
    case "message":
    case "limit":
      return [...feed, { id: nextId(), kind: "assistant", text: String(event.payload.text ?? "") }];
    case "tool_call":
      return [...feed, { id: nextId(), kind: "tool", tool: String(event.payload.tool ?? ""), payload: event.payload }];
    case "tool_result":
      return feed.map((item, index) =>
        index === feed.length - 1 && item.kind === "tool" ? { ...item, payload: { ...item.payload, ...event.payload } } : item,
      );
    case "tool_error":
      return [...feed, { id: nextId(), kind: "tool-error", tool: String(event.payload.tool ?? ""), text: String(event.payload.error ?? "") }];
    case "error":
      return [...feed, { id: nextId(), kind: "tool-error", tool: "", text: String(event.payload.error ?? "ошибка") }];
    default:
      return feed;
  }
}

function FeedRow({ item, onChanged }: { item: FeedItem; onChanged: () => void }) {
  if (item.kind === "user") {
    return <div className="ml-8 rounded-md bg-accent-muted px-3 py-2 text-[13px] text-content">{item.text}</div>;
  }
  if (item.kind === "assistant") {
    return <div className="whitespace-pre-wrap px-1 text-[13px] leading-relaxed text-content">{item.text}</div>;
  }
  if (item.kind === "tool-error") {
    return (
      <div className="rounded-sm border border-warn bg-warn-muted px-3 py-2 text-[12px] text-warn">
        {item.tool ? <span className="font-mono">{item.tool}: </span> : null}
        {item.text}
      </div>
    );
  }
  return <ToolCard item={item} onChanged={onChanged} />;
}

/** Имена инструментов человеку. Внутреннее имя ему ничего не говорит. */
const TOOL_TITLES: Record<string, string> = {
  createProject: "Новый проект",
  estimateStep: "Смета шага",
  videoOptions: "Разрешения видео",
  runStep: "Запуск шага",
  runStage: "Запуск стадии",
  showStoryboard: "Раскадровка",
  editFramePrompt: "Правка промта кадра",
  regenerateFrame: "Перерисовка кадра",
  approveStage: "Утверждение",
  showBalance: "Баланс",
  showStages: "Где проект",
  showGraph: "Схема ролика",
  editGraph: "Правка схемы",
  proposeGraph: "Пересборка схемы",
  applyGraph: "Применение схемы",
  discardGraph: "Отказ от правки",
  resetStep: "Сброс шага",
  stopStep: "Остановка",
  setProjectOptions: "Параметры ролика",
};

function ToolCard({ item, onChanged }: { item: FeedItem; onChanged: () => void }) {
  const result = (item.payload?.result ?? null) as Record<string, unknown> | null;
  const args = (item.payload?.args ?? {}) as Record<string, unknown>;
  const price = result && typeof result.price_credits === "string" ? result.price_credits : null;
  const needsConfirm = result?.needs_confirmation === true;
  const isGraph = needsConfirm && typeof result?.proposal_id === "string";

  return (
    <div className={`rounded-sm border px-3 py-2 text-[13px] ${needsConfirm ? "border-warn bg-warn-muted" : "border-border bg-surface-raised"}`}>
      <div className="flex items-center justify-between gap-3 text-[12px] text-content-muted">
        <span>{TOOL_TITLES[item.tool ?? ""] ?? item.tool}</span>
        {!result && <Working />}
      </div>
      {price && !isGraph && (
        <div className="mt-1 font-mono text-[15px] tabular-nums text-content">
          {price.replace(".", ",")} <span className="text-[12px] text-content-muted">кр</span>
        </div>
      )}
      {isGraph && result && (
        <GraphProposalRow item={item} result={result} onChanged={onChanged} />
      )}
      {needsConfirm && !isGraph && result && <ConfirmRow item={item} args={args} result={result} onChanged={onChanged} />}
      {result && Array.isArray(result.frames) && <ContactSheet frames={result.frames as FrameCard[]} />}
      {result && Array.isArray(result.stages) && <StagesRow stages={result.stages as StageCardData[]} />}
      {result && result.applied === true && (
        <p className="mt-1 text-[12px] text-ok">Схема применена{typeof result.diff === "string" ? `: ${result.diff}` : ""}.</p>
      )}
      {result && result.started === true && <p className="mt-1 text-[12px] text-ok">Запущено.</p>}
      {result && result.reset === true && <p className="mt-1 text-[12px] text-ok">Сброшено.</p>}
    </div>
  );
}

interface StageCardData {
  id: string;
  label: string;
  state: string;
  price_credits: string;
}

function StagesRow({ stages }: { stages: StageCardData[] }) {
  const tone: Record<string, string> = {
    done: "text-ok",
    running: "text-accent",
    ready: "text-content",
    failed: "text-danger",
    skipped: "text-content-faint line-through",
    locked: "text-content-faint",
  };
  return (
    <ol className="mt-2 space-y-0.5 text-[12px]">
      {stages.map((s, i) => (
        <li key={s.id} className={`flex justify-between ${tone[s.state] ?? "text-content-muted"}`}>
          <span>
            {i + 1}. {s.label}
          </span>
          <span className="font-mono">{s.state === "done" ? "✓" : s.price_credits.replace(".", ",")}</span>
        </li>
      ))}
    </ol>
  );
}

interface FrameCard {
  number: number;
  status: string;
  image_url?: string;
  voiceover?: string;
}

function ContactSheet({ frames }: { frames: FrameCard[] }) {
  if (!frames.length) return null;
  return (
    <div className="mt-2 grid grid-cols-4 gap-1">
      {frames.map((f) => (
        <div
          key={f.number}
          className="relative aspect-[9/16] overflow-hidden rounded-sm border border-border bg-surface-sunken"
          title={f.voiceover ? `${f.number}. ${f.voiceover}` : `кадр ${f.number}`}
        >
          {f.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={f.image_url} alt={`кадр ${f.number}`} className="h-full w-full object-cover" loading="lazy" />
          ) : (
            <span className="flex h-full items-center justify-center text-[10px] text-content-faint">{f.number}</span>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Кнопка «подтверждаю» под ценой — прямо в инструмент, минуя модель.
 */
function ConfirmRow({
  item,
  args,
  result,
  onChanged,
}: {
  item: FeedItem;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const price = typeof result.price_credits === "string" ? result.price_credits.replace(".", ",") : "";
  const willReset = Array.isArray(result.will_reset) ? (result.will_reset as string[]) : [];

  if (done) return <p className="mt-1 text-[12px] text-content-muted">Подтверждено.</p>;

  return (
    <div className="mt-2 space-y-1.5">
      {willReset.length > 0 && (
        <p className="text-[12px] text-warn">
          Сгорят шаги: <span className="font-mono">{willReset.join(" → ")}</span>
        </p>
      )}
      <p className="text-[12px] text-content-muted">{price ? `Спишется ${price} кр.` : "Действие необратимо."} Продолжить?</p>
      <Button
        size="sm"
        variant="primary"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await callTool(item.tool ?? "runStep", { ...args, confirm: true });
            setDone(true);
            onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        Подтверждаю{price ? ` — ${price} кр` : ""}
      </Button>
      {error && <p className="text-[12px] text-danger">{error}</p>}
    </div>
  );
}

/** Предложение агента по графу: та же карточка разницы, что на холсте. */
function GraphProposalRow({
  item,
  result,
  onChanged,
}: {
  item: FeedItem;
  result: Record<string, unknown>;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState<"open" | "applied" | "discarded">("open");
  const [error, setError] = useState<string | null>(null);
  const args = (item.payload?.args ?? {}) as Record<string, unknown>;
  const projectId = Number(result.project_id ?? args.project_id ?? 0);
  const diff = result.diff as GraphDiff | undefined;
  const reset: ResetPlan = {
    first_step: null,
    steps: Array.isArray(result.will_reset) ? (result.will_reset as string[]) : [],
    reasons: (result.reset_reasons as Record<string, string>) ?? {},
  };
  if (!diff) return null;
  if (state === "applied") return <p className="mt-1 text-[12px] text-ok">Схема применена.</p>;
  if (state === "discarded") return <p className="mt-1 text-[12px] text-content-muted">Предложение отклонено.</p>;

  const run = async (name: string, extra: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await callTool(name, { project_id: projectId, ...extra });
      setState(name === "applyGraph" ? "applied" : "discarded");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2">
      <GraphDiffCard
        title={typeof args.reason === "string" && args.reason ? args.reason : "Предложение по схеме"}
        diff={diff}
        reset={reset}
        warnings={Array.isArray(result.warnings) ? (result.warnings as string[]) : []}
        busy={busy}
        onApply={() => void run("applyGraph", { proposal_id: result.proposal_id, confirm: true })}
        onCancel={() => void run("discardGraph", {})}
        cancelLabel="Отклонить"
      />
      {error && <p className="mt-1 text-[12px] text-danger">{error}</p>}
    </div>
  );
}
