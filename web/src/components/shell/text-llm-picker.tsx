"use client";

import { useEffect, useState } from "react";
import { Brain } from "lucide-react";
import { cn } from "@/lib/utils";

type TextLlmModel = {
  id: string;
  provider: string;
  label: string;
  site: string;
  model: string;
  key_configured: boolean;
  active: boolean;
};

type ReasoningOption = {
  id: string;
  label: string;
  hint: string;
};

type TextLlmStatus = {
  active_provider: string;
  active_label: string;
  active_model: string;
  active_reasoning_effort?: string;
  reasoning_options?: ReasoningOption[];
  models: TextLlmModel[];
};

const DEFAULT_REASONING_OPTIONS: ReasoningOption[] = [
  { id: "off", label: "Выкл", hint: "Мгновенный ответ без скрытых размышлений (быстро)" },
  { id: "low", label: "Низкий (low)", hint: "Быстрое размышление (~5-15 сек), минимальный шанс таймаута" },
  { id: "medium", label: "Средний (mid)", hint: "Взвешенное рассуждение, стандарт для сценариев" },
  { id: "high", label: "Высокий (high)", hint: "Глубокий анализ (может думать до 1-2 минут)" },
  { id: "xhigh", label: "Экстра (xhigh)", hint: "Предельное мышление (для GPT-5.6 / флагманов)" },
];

const EFFORT_STYLES: Record<string, { badge: string; icon: string }> = {
  off: {
    badge: "border-white/10 bg-black/40 text-zinc-400 hover:text-zinc-300",
    icon: "text-zinc-500",
  },
  low: {
    badge: "border-cyan-500/30 bg-cyan-950/30 text-cyan-300 hover:border-cyan-400/50",
    icon: "text-cyan-400",
  },
  medium: {
    badge: "border-emerald-500/30 bg-emerald-950/30 text-emerald-300 hover:border-emerald-400/50",
    icon: "text-emerald-400",
  },
  high: {
    badge: "border-amber-500/30 bg-amber-950/30 text-amber-300 hover:border-amber-400/50",
    icon: "text-amber-400",
  },
  xhigh: {
    badge: "border-fuchsia-500/30 bg-fuchsia-950/30 text-fuchsia-300 hover:border-fuchsia-400/50",
    icon: "text-fuchsia-400",
  },
};

export function TextLlmPicker() {
  const [status, setStatus] = useState<TextLlmStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    fetch("/api/text-llm", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: TextLlmStatus | null) => {
        if (data?.models) setStatus(data);
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    reload();
  }, []);

  const onModelChange = async (provider: string, modelId: string) => {
    setBusy(true);
    try {
      const r = await fetch("/api/text-llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model_id: modelId }),
      });
      if (r.ok) {
        const data = (await r.json()) as TextLlmStatus;
        setStatus(data);
        const cleanId = modelId.replace(/-vibecode$/, "");
        window.dispatchEvent(
          new CustomEvent("canvas-patch-global-model", {
            detail: { modelId: cleanId },
          }),
        );
      }
    } finally {
      setBusy(false);
    }
  };

  const onReasoningChange = async (effort: string) => {
    setBusy(true);
    try {
      const r = await fetch("/api/text-llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reasoning_effort: effort }),
      });
      if (r.ok) {
        const data = (await r.json()) as TextLlmStatus;
        setStatus(data);
      }
    } finally {
      setBusy(false);
    }
  };

  if (!status?.models?.length) return null;

  const active = status.models.find((m) => m.active) || status.models[0];
  const activeEffort = status.active_reasoning_effort || "off";
  const options = status.reasoning_options || DEFAULT_REASONING_OPTIONS;
  const currentEffortStyle = EFFORT_STYLES[activeEffort] || EFFORT_STYLES.off;
  const activeOption = options.find((o) => o.id === activeEffort) || options[0];

  return (
    <div className="flex items-center gap-1.5">
      <select
        className={cn(
          "h-8 max-w-[190px] truncate rounded-md border border-white/10 bg-black/50 px-2 text-[11px] text-white/85",
          "focus:outline-none focus:ring-1 focus:ring-primary/40",
          busy && "opacity-60",
        )}
        disabled={busy}
        value={active.id}
        title={`${status.active_label}\nGPT 5.6 Sol / 5.5 — vibecode.moe; kie и Kimi остаются в списке`}
        onChange={(e) => {
          const m = status.models.find((x) => x.id === e.target.value);
          if (m) void onModelChange(m.provider, m.id);
        }}
      >
        {status.models.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.key_configured && m.provider !== "kie"}>
            {m.label}
            {!m.key_configured && m.provider !== "kie" ? " · нет ключа" : ""}
          </option>
        ))}
      </select>

      <div
        className={cn(
          "relative flex h-8 items-center gap-1 rounded-md border px-2 text-[11px] font-medium transition-colors",
          currentEffortStyle.badge,
          busy && "opacity-60",
        )}
        title={`Мышление LLM (reasoning effort): ${activeOption.label}\n${activeOption.hint}`}
      >
        <Brain className={cn("h-3.5 w-3.5 shrink-0", currentEffortStyle.icon)} />
        <select
          className="cursor-pointer bg-transparent text-[11px] font-medium text-inherit focus:outline-none"
          disabled={busy}
          value={activeEffort}
          onChange={(e) => void onReasoningChange(e.target.value)}
        >
          {options.map((opt) => (
            <option key={opt.id} value={opt.id} className="bg-zinc-950 text-zinc-100">
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
