"use client";

import { useState } from "react";
import { Brain, Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export type ReasoningEffortLevel = "off" | "low" | "medium" | "high" | "xhigh";

export interface ReasoningOption {
  id: ReasoningEffortLevel;
  label: string;
  badge: string;
  activeBadge: string;
  iconColor: string;
  dotColor: string;
  hint: string;
  desc: string;
}

export const REASONING_OPTIONS: ReasoningOption[] = [
  {
    id: "off",
    label: "Выкл",
    badge: "border-white/10 bg-black/40 text-zinc-400 hover:text-zinc-200 hover:border-white/20",
    activeBadge: "border-white/25 bg-white/[0.08] text-zinc-200",
    iconColor: "text-zinc-400",
    dotColor: "bg-zinc-500",
    hint: "Мгновенно",
    desc: "Быстрый ответ без скрытых рассуждений",
  },
  {
    id: "low",
    label: "Низкий",
    badge: "border-cyan-500/30 bg-cyan-950/40 text-cyan-300 hover:border-cyan-400/50 hover:bg-cyan-950/60",
    activeBadge: "border-cyan-400/60 bg-cyan-950/60 text-cyan-200 shadow-[0_0_12px_rgba(6,182,212,0.25)]",
    iconColor: "text-cyan-400",
    dotColor: "bg-cyan-400",
    hint: "~5-15 сек",
    desc: "Быстрое размышление, минимум таймаутов",
  },
  {
    id: "medium",
    label: "Средний",
    badge: "border-emerald-500/30 bg-emerald-950/40 text-emerald-300 hover:border-emerald-400/50 hover:bg-emerald-950/60",
    activeBadge: "border-emerald-400/60 bg-emerald-950/60 text-emerald-200 shadow-[0_0_12px_rgba(16,185,129,0.25)]",
    iconColor: "text-emerald-400",
    dotColor: "bg-emerald-400",
    hint: "Стандарт",
    desc: "Взвешенный анализ для сценариев",
  },
  {
    id: "high",
    label: "Высокий",
    badge: "border-amber-500/30 bg-amber-950/40 text-amber-300 hover:border-amber-400/50 hover:bg-amber-950/60",
    activeBadge: "border-amber-400/60 bg-amber-950/60 text-amber-200 shadow-[0_0_12px_rgba(245,158,11,0.25)]",
    iconColor: "text-amber-400",
    dotColor: "bg-amber-400",
    hint: "до 1-2 мин",
    desc: "Глубокая проработка логики и деталей",
  },
  {
    id: "xhigh",
    label: "Экстра",
    badge: "border-fuchsia-500/30 bg-fuchsia-950/40 text-fuchsia-300 hover:border-fuchsia-400/50 hover:bg-fuchsia-950/60",
    activeBadge: "border-fuchsia-400/60 bg-fuchsia-950/60 text-fuchsia-200 shadow-[0_0_12px_rgba(217,70,239,0.25)]",
    iconColor: "text-fuchsia-400",
    dotColor: "bg-fuchsia-400",
    hint: "Предел",
    desc: "Максимальное мышление GPT-5.6",
  },
];

export function NodeReasoningPicker({
  nodeKey,
  effort,
}: {
  nodeKey: string;
  effort?: string | null;
}) {
  const [open, setOpen] = useState(false);

  // Стандартное значение для нод — medium (если не задано)
  const currentEffort = (effort || "medium").toLowerCase() as ReasoningEffortLevel;
  const activeOption =
    REASONING_OPTIONS.find((opt) => opt.id === currentEffort) || REASONING_OPTIONS[2];

  const handleSelect = (newEffort: ReasoningEffortLevel) => {
    window.dispatchEvent(
      new CustomEvent("canvas-patch-node-data", {
        detail: {
          nodeKey,
          patch: { reasoningEffort: newEffort },
        },
      }),
    );
    window.dispatchEvent(new CustomEvent("canvas-save-workflow"));
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={`Уровень мышления LLM: ${activeOption.label} (${activeOption.hint})\n${activeOption.desc}`}
          className={cn(
            "nodrag nopan nowheel flex h-[27px] shrink-0 items-center gap-1 rounded-lg border px-2 text-[11px] font-semibold transition-all duration-200 select-none",
            activeOption.badge,
            open && activeOption.activeBadge,
          )}
          onPointerDown={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
          }}
        >
          <Brain className={cn("h-3 w-3 shrink-0", activeOption.iconColor)} />
          <span className="leading-none">{activeOption.label}</span>
          <ChevronDown
            className={cn(
              "h-2.5 w-2.5 shrink-0 opacity-60 transition-transform duration-200",
              open && "rotate-180",
            )}
          />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={6}
        className="nodrag nopan nowheel z-[250] w-64 rounded-xl border border-white/10 bg-zinc-950/95 p-1.5 shadow-2xl backdrop-blur-xl"
        onPointerDown={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-white/[0.06] px-2 py-1.5 mb-1">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
            Мышление LLM
          </span>
          <span className="text-[9px] font-mono text-zinc-500">Reasoning</span>
        </div>
        <div className="flex flex-col gap-0.5">
          {REASONING_OPTIONS.map((opt) => {
            const isSelected = activeOption.id === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => handleSelect(opt.id)}
                className={cn(
                  "group flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-all",
                  isSelected
                    ? opt.activeBadge
                    : "border border-transparent text-zinc-300 hover:bg-white/[0.05] hover:text-white",
                )}
              >
                <div className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
                  {isSelected ? (
                    <Check className={cn("h-3.5 w-3.5", opt.iconColor)} strokeWidth={2.5} />
                  ) : (
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full transition-transform group-hover:scale-125",
                        opt.dotColor,
                      )}
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="flex items-center justify-between gap-1 text-[11px] font-semibold">
                    <span className={opt.iconColor}>{opt.label}</span>
                    <span className="font-mono text-[9px] font-normal opacity-60">
                      {opt.hint}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[10px] text-zinc-400 leading-tight">
                    {opt.desc}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
