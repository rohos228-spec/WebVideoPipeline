"use client";

/**
 * Панель чата: разговор слева, канвас справа.
 *
 * Лента показывает не только реплики, но и каждый вызов инструмента
 * карточкой. Это не отладочный вывод: продукт продаёт прозрачность — человек
 * должен видеть, что именно система собирается сделать и во сколько это
 * обойдётся, ДО списания, а не в отчёте после.
 *
 * Отсюда три правила отрисовки, и все три про деньги:
 *
 * 1. Цена всегда рядом с действием. Карточка сметы показывает кредиты крупно.
 * 2. Дорогое выглядит дорого. Шаг, потребовавший подтверждения, рисуется
 *    отдельным видом — «перерисовать кадр» и «переделать раскадровку» не
 *    должны выглядеть одинаково (§7.3).
 * 3. Ошибка инструмента видна человеку как объяснение, а не как молчание.
 */

import * as React from "react";
import { AlertCircle, Coins, Loader2, Send, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { callTool, streamChat, type AgentEvent, type ChatHistoryItem } from "@/lib/chat-api";

interface FeedItem {
  id: string;
  kind: "user" | "assistant" | "tool" | "tool-error";
  text?: string;
  tool?: string;
  payload?: Record<string, unknown>;
  onConfirmed?: (result: Record<string, unknown>) => void;
}

let seq = 0;
const nextId = () => `item-${++seq}`;

export function ChatPanel({ className }: { className?: string }) {
  const [feed, setFeed] = React.useState<FeedItem[]>([]);
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [feed]);

  const history = React.useMemo<ChatHistoryItem[]>(
    () =>
      feed
        .filter((i) => i.kind === "user" || i.kind === "assistant")
        .map((i) => ({
          role: i.kind === "user" ? "user" : "assistant",
          content: i.text ?? "",
        })),
    [feed],
  );

  async function send() {
    const message = draft.trim();
    if (!message || busy) return;
    setDraft("");
    setError(null);
    setBusy(true);
    setFeed((f) => [...f, { id: nextId(), kind: "user", text: message }]);

    try {
      for await (const event of streamChat(message, history)) {
        setFeed((f) => appendEvent(f, event));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cn("flex h-full flex-col bg-background", className)}>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {feed.length === 0 && <EmptyState />}
        {feed.map((item) => (
          <FeedRow key={item.id} item={item} />
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="animate-spin" />
            думаю…
          </div>
        )}
        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
            <AlertCircle className="mt-0.5 text-destructive" />
            <span>{error}</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-white/8 p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter отправляет, Shift+Enter переносит строку: в чате
              // сообщения короткие, и лишний клик по кнопке на каждой реплике
              // заметен сильнее, чем редкий случайный перенос.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Какой ролик нужен?"
            rows={2}
            className="resize-none"
          />
          <Button onClick={() => void send()} disabled={busy || !draft.trim()} size="icon">
            <Send />
          </Button>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-white/8 bg-white/[0.02] p-4 text-sm text-muted-foreground">
      <p className="mb-2 text-foreground">Опиши ролик, который нужен.</p>
      <p>
        Раскадровка и кадры — бесплатно. Оплата только за рендер видео, и цену
        я покажу до запуска.
      </p>
    </div>
  );
}

function appendEvent(feed: FeedItem[], event: AgentEvent): FeedItem[] {
  switch (event.type) {
    case "message":
    case "limit":
      return [
        ...feed,
        { id: nextId(), kind: "assistant", text: String(event.payload.text ?? "") },
      ];
    case "tool_call":
      return [
        ...feed,
        { id: nextId(), kind: "tool", tool: String(event.payload.tool ?? ""), payload: event.payload },
      ];
    case "tool_result":
      // Результат дополняет карточку вызова, а не заводит вторую: человеку
      // нужно одно событие «сделано столько-то», а не две строки про одно.
      return feed.map((item, index) =>
        index === feed.length - 1 && item.kind === "tool"
          ? { ...item, payload: { ...item.payload, ...event.payload } }
          : item,
      );
    case "tool_error":
      return [
        ...feed,
        {
          id: nextId(),
          kind: "tool-error",
          tool: String(event.payload.tool ?? ""),
          text: String(event.payload.error ?? ""),
        },
      ];
    default:
      return feed;
  }
}

function FeedRow({ item }: { item: FeedItem }) {
  if (item.kind === "user") {
    return (
      <div className="ml-8 rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm">
        {item.text}
      </div>
    );
  }
  if (item.kind === "assistant") {
    return <div className="whitespace-pre-wrap p-1 text-sm">{item.text}</div>;
  }
  if (item.kind === "tool-error") {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm">
        <AlertCircle className="mt-0.5 shrink-0" />
        <span>
          <span className="text-muted-foreground">{item.tool}: </span>
          {item.text}
        </span>
      </div>
    );
  }
  return <ToolCard item={item} onConfirmed={item.onConfirmed} />;
}

function ToolCard({ item, onConfirmed }: { item: FeedItem; onConfirmed?: (r: Record<string, unknown>) => void }) {
  const result = (item.payload?.result ?? {}) as Record<string, unknown>;
  const price = typeof result.price_credits === "string" ? result.price_credits : null;
  const needsConfirm = result.needs_confirmation === true;

  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm",
        needsConfirm
          ? "border-warning/40 bg-warning/10"
          : "border-white/8 bg-white/[0.02]",
      )}
    >
      <div className="flex items-center gap-2 text-muted-foreground">
        <Wrench className="size-3.5" />
        <span>{TOOL_TITLES[item.tool ?? ""] ?? item.tool}</span>
      </div>
      {price && (
        <div className="mt-2 flex items-baseline gap-1.5">
          <Coins className="size-4 text-muted-foreground" />
          <span className="text-lg font-medium tabular-nums">{price}</span>
          <span className="text-muted-foreground">кр</span>
        </div>
      )}
      {needsConfirm && (
        <ConfirmRow item={item} result={result} onConfirmed={onConfirmed} />
      )}
      {Array.isArray(result.frames) && <ContactSheet frames={result.frames as Frame[]} />}
    </div>
  );
}

interface Frame {
  number: number;
  status: string;
  image_url?: string;
  voiceover?: string;
}

/**
 * Контактный лист прямо в ленте.
 *
 * «Кадров: 24» не отвечает на вопрос, ради которого человек попросил
 * раскадровку. Он пришёл посмотреть, что получилось, и решить, что
 * перерисовать, — значит видеть надо кадры, а не их количество.
 *
 * Пустые клетки показываются наравне с готовыми: дыра в ленте это тоже
 * ответ — «этот кадр ещё не нарисован», и прятать её значит делать вид, что
 * раскадровка полная.
 */
function ContactSheet({ frames }: { frames: Frame[] }) {
  if (!frames.length) return null;
  return (
    <div className="mt-3 grid grid-cols-4 gap-1.5">
      {frames.map((f) => (
        <div
          key={f.number}
          className="relative aspect-[9/16] overflow-hidden rounded border border-white/10 bg-white/[0.03]"
          title={f.voiceover ? `${f.number}. ${f.voiceover}` : `кадр ${f.number}`}
        >
          {f.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={f.image_url}
              alt={`кадр ${f.number}`}
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <span className="flex h-full items-center justify-center text-[10px] text-muted-foreground">
              {f.number}
            </span>
          )}
          {f.image_url && (
            <span className="absolute bottom-0 left-0 rounded-tr bg-background/80 px-1 text-[10px] tabular-nums">
              {f.number}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Кнопка «подтверждаю» под ценой.
 *
 * Нажатие идёт прямо в инструмент, а не пересказывается модели репликой
 * «да, давай». Согласие на списание обязано доезжать до кассы буквой:
 * пересказ даёт модели шанс понять его иначе, а цена ошибки здесь — деньги
 * клиента.
 */
function ConfirmRow({
  item,
  result,
  onConfirmed,
}: {
  item: FeedItem;
  result: Record<string, unknown>;
  onConfirmed?: (r: Record<string, unknown>) => void;
}) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);

  const args = (item.payload?.args ?? {}) as Record<string, unknown>;
  const price = typeof result.price_credits === "string" ? result.price_credits : "";

  if (done) {
    return <p className="mt-2 text-muted-foreground">Запущено.</p>;
  }

  return (
    <div className="mt-3 space-y-2">
      <p className="text-muted-foreground">
        {price ? `Спишется ${price} кр.` : "Шаг дороже порога."} Продолжить?
      </p>
      <Button
        size="sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            const res = await callTool(item.tool ?? "runStep", { ...args, confirm: true });
            setDone(true);
            onConfirmed?.(res);
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? <Loader2 className="animate-spin" /> : null}
        Подтверждаю{price ? ` — ${price} кр` : ""}
      </Button>
      {error && <p className="text-destructive">{error}</p>}
    </div>
  );
}

/** Имена инструментов человеку. Внутреннее имя ему ничего не говорит. */
const TOOL_TITLES: Record<string, string> = {
  createProject: "Новый проект",
  estimateStep: "Смета шага",
  runStep: "Запуск шага",
  showStoryboard: "Раскадровка",
  editFramePrompt: "Правка промта кадра",
  regenerateFrame: "Перерисовка кадра",
  approveStage: "Утверждение",
  showBalance: "Баланс",
};
