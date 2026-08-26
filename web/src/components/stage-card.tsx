"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Chip, Working } from "@/components/ui/bits";
import { credits } from "@/lib/format";
import type { Stage, StageNode } from "@/lib/types";

const STATE_CHIP: Record<string, { tone: "neutral" | "ok" | "warn" | "danger" | "accent"; text: string }> = {
  done: { tone: "ok", text: "готово" },
  running: { tone: "accent", text: "идёт" },
  ready: { tone: "neutral", text: "следующий" },
  locked: { tone: "neutral", text: "" },
  failed: { tone: "danger", text: "сбой" },
  paused: { tone: "warn", text: "пауза" },
  skipped: { tone: "neutral", text: "выключен" },
};

const NODE_DOT: Record<string, string> = {
  done: "bg-ok",
  running: "bg-accent animate-pulse",
  queued: "bg-accent",
  failed: "bg-danger",
  waiting_hitl: "bg-warn",
};

/**
 * Один шаг воронки: номер, название, цена и одно действие.
 *
 * Раскрытие — не украшение: результат шага занимает экран целиком, и если
 * бы все семь были раскрыты сразу, вернулась бы та самая стена. Готовый шаг
 * складывается сам, текущий раскрыт.
 *
 * Под заголовком — узлы графа, из которых стадия состоит. Стадия — свёртка,
 * и когда нужно перегенерировать один шаг из трёх, платить за все три
 * незачем: у каждого узла свой запуск и сброс.
 */
export function StageCard({
  index,
  stage,
  defaultOpen,
  onRun,
  onStop,
  onRunNode,
  onResetNode,
  busy,
  children,
}: {
  index: number;
  stage: Stage;
  defaultOpen: boolean;
  onRun: () => void;
  onStop: () => void;
  onRunNode?: (node: StageNode) => void;
  onResetNode?: (node: StageNode) => void;
  busy: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const chip = STATE_CHIP[stage.state] ?? STATE_CHIP.locked;
  const locked = stage.state === "locked";
  const skipped = stage.state === "skipped";
  const running = stage.state === "running";
  const done = stage.state === "done";
  const nodes = stage.nodes ?? [];

  return (
    <section
      className={`border-b border-border py-5 ${locked || skipped ? "opacity-45" : ""}`}
      aria-current={running ? "step" : undefined}
    >
      <header className="flex items-start gap-4">
        <div className="w-6 shrink-0 pt-1 font-mono text-[12px] tabular-nums text-content-faint">{index}</div>

        <div className="min-w-0 flex-1">
          <button
            onClick={() => !locked && setOpen((v) => !v)}
            disabled={locked}
            className="group flex items-baseline gap-3 text-left disabled:cursor-default"
          >
            <h2 className="font-display text-[19px] text-content">{stage.label}</h2>
            {chip.text && <Chip tone={chip.tone}>{chip.text}</Chip>}
          </button>
          <p className="mt-0.5 text-[13px] text-content-muted">{stage.hint}</p>

          {nodes.length > 0 && !locked && (
            <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              {nodes.map((n) => (
                <NodeRow
                  key={n.id}
                  node={n}
                  busy={busy || running}
                  onRun={onRunNode ? () => onRunNode(n) : undefined}
                  onReset={onResetNode ? () => onResetNode(n) : undefined}
                />
              ))}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-3 pt-0.5">
          <span
            className="font-mono text-[12px] tabular-nums text-content-muted"
            title={stage.exact ? "точная цена" : "оценка по прошлым прогонам"}
          >
            {skipped ? "" : credits(stage.price_credits)}
            {!stage.exact && stage.price_micro > 0 && !skipped ? " ≈" : ""}
          </span>

          {running ? (
            <Button size="sm" variant="ghost" onClick={onStop}>
              Остановить
            </Button>
          ) : (
            <Button
              size="sm"
              variant={locked || skipped ? "secondary" : done ? "ghost" : "primary"}
              disabled={locked || skipped || busy}
              title={skipped ? "Все узлы стадии выключены на схеме" : undefined}
              onClick={onRun}
            >
              {done ? "Заново" : "Сгенерировать"}
            </Button>
          )}
        </div>
      </header>

      {running && (
        <div className="mt-3 pl-10">
          <Working label="идёт генерация — можно закрыть вкладку, шаг не прервётся" />
        </div>
      )}

      {!locked && open && <div className="rise mt-4 pl-10">{children}</div>}

      {!locked && !open && (
        <button onClick={() => setOpen(true)} className="mt-2 ml-10 text-[12px] text-content-faint hover:text-accent">
          показать результат
        </button>
      )}
    </section>
  );
}

/** Узел внутри стадии: точка состояния, имя, и по наведению — запуск/сброс. */
function NodeRow({
  node,
  busy,
  onRun,
  onReset,
}: {
  node: StageNode;
  busy: boolean;
  onRun?: () => void;
  onReset?: () => void;
}) {
  const dot = node.disabled ? "bg-border-strong" : (NODE_DOT[node.state] ?? "bg-border-strong");
  const canRun = Boolean(onRun && node.step_code && !node.disabled && node.state !== "running");
  const canReset = Boolean(onReset && node.step_code && (node.state === "done" || node.state === "failed"));
  return (
    <li className={`group flex items-center gap-1.5 text-[12px] ${node.disabled ? "text-content-faint line-through" : "text-content-muted"}`}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
      <span title={node.step_code ?? node.type}>{node.label}</span>
      {node.price_micro > 0 && !node.disabled && (
        <span className="font-mono text-[11px] text-content-faint">{credits(node.price_credits)}</span>
      )}
      {(canRun || canReset) && (
        <span className="hidden gap-1 group-hover:inline-flex">
          {canRun && (
            <button onClick={onRun} disabled={busy} className="text-[11px] text-accent hover:underline disabled:opacity-40">
              {node.state === "done" ? "заново" : "запустить"}
            </button>
          )}
          {canReset && (
            <button onClick={onReset} disabled={busy} className="text-[11px] text-content-faint hover:text-danger disabled:opacity-40">
              сбросить
            </button>
          )}
        </span>
      )}
    </li>
  );
}
