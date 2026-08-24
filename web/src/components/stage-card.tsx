"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Chip, Working } from "@/components/ui/bits";
import { credits } from "@/lib/format";
import type { Stage } from "@/lib/types";

const STATE_CHIP: Record<string, { tone: "neutral" | "ok" | "warn" | "danger" | "accent"; text: string }> = {
  done: { tone: "ok", text: "готово" },
  running: { tone: "accent", text: "идёт" },
  ready: { tone: "neutral", text: "следующий" },
  locked: { tone: "neutral", text: "" },
  failed: { tone: "danger", text: "сбой" },
  paused: { tone: "warn", text: "пауза" },
};

/**
 * Один шаг воронки: номер, название, цена и одно действие.
 *
 * Раскрытие — не украшение: результат шага занимает экран целиком, и если
 * бы все семь были раскрыты сразу, вернулась бы та самая стена. Готовый шаг
 * складывается сам, текущий раскрыт.
 */
export function StageCard({
  index,
  stage,
  defaultOpen,
  onRun,
  onStop,
  busy,
  children,
}: {
  index: number;
  stage: Stage;
  defaultOpen: boolean;
  onRun: () => void;
  onStop: () => void;
  busy: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const chip = STATE_CHIP[stage.state] ?? STATE_CHIP.locked;
  const locked = stage.state === "locked";
  const running = stage.state === "running";
  const done = stage.state === "done";

  return (
    <section
      className={`border-b border-border py-5 ${locked ? "opacity-45" : ""}`}
      aria-current={running ? "step" : undefined}
    >
      <header className="flex items-start gap-4">
        <div className="w-6 shrink-0 pt-1 font-mono text-[12px] tabular-nums text-content-faint">
          {index}
        </div>

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
        </div>

        <div className="flex shrink-0 items-center gap-3 pt-0.5">
          <span
            className="font-mono text-[12px] tabular-nums text-content-muted"
            title={stage.exact ? "точная цена" : "оценка по прошлым прогонам"}
          >
            {credits(stage.price_credits)}
            {!stage.exact && stage.price_micro > 0 ? " ≈" : ""}
          </span>

          {running ? (
            <Button size="sm" variant="ghost" onClick={onStop}>
              Остановить
            </Button>
          ) : (
            <Button
              size="sm"
              variant={locked ? "secondary" : done ? "ghost" : "primary"}
              disabled={locked || busy}
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
        <button
          onClick={() => setOpen(true)}
          className="mt-2 ml-10 text-[12px] text-content-faint hover:text-accent"
        >
          показать результат
        </button>
      )}
    </section>
  );
}
