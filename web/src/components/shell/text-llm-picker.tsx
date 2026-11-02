"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Loader2, Lock, Search, Sparkles } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type TextLlmModel = {
  id: string;
  provider: string;
  group?: string;
  label: string;
  site: string;
  model: string;
  key_configured: boolean;
  active: boolean;
};

type TextLlmStatus = {
  active_provider: string;
  active_label: string;
  active_model: string;
  models: TextLlmModel[];
};

const GROUP_ORDER = ["OpenAI", "Anthropic", "Google", "xAI", "GLM", "KIE"];

function providerTag(provider: string): string {
  if (provider === "pay52") return "52";
  if (provider === "vibecode") return "vibecode";
  if (provider === "kie") return "kie";
  return provider;
}

export function TextLlmPicker({
  className,
  side = "top",
  align = "end",
}: {
  className?: string;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
}) {
  const [status, setStatus] = useState<TextLlmStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

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

  const onChange = async (provider: string, modelId: string) => {
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
        const cleanId = modelId.replace(/-vibecode$/, "").replace(/-pay52(-low|-high)?$/, "");
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

  const groups = useMemo(() => {
    const models = status?.models || [];
    const q = searchQuery.trim().toLowerCase();

    const filtered = q
      ? models.filter(
          (m) =>
            m.label.toLowerCase().includes(q) ||
            m.id.toLowerCase().includes(q) ||
            m.provider.toLowerCase().includes(q) ||
            (m.group && m.group.toLowerCase().includes(q)),
        )
      : models;

    const byGroup = new Map<string, TextLlmModel[]>();
    for (const m of filtered) {
      const g = m.group || (m.provider === "kie" ? "KIE" : "OpenAI");
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g)!.push(m);
    }
    const ordered: { name: string; models: TextLlmModel[] }[] = [];
    for (const name of GROUP_ORDER) {
      const list = byGroup.get(name);
      if (list?.length) ordered.push({ name, models: list });
      byGroup.delete(name);
    }
    for (const [name, list] of byGroup) {
      if (list.length) ordered.push({ name, models: list });
    }
    return ordered;
  }, [status, searchQuery]);

  if (!status?.models?.length) return null;

  const active = status.models.find((m) => m.active) || status.models[0];

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={busy}
          className={cn(
            "inline-flex items-center justify-between gap-1.5 h-7 px-2 rounded-lg border border-white/10 bg-white/[0.04] text-[11px] text-white/80 hover:text-white hover:bg-white/[0.08] transition-colors cursor-pointer select-none",
            busy && "opacity-60",
            className,
          )}
          title={status.active_label}
          aria-label="Выбор текстовой модели"
        >
          <div className="flex items-center gap-1.5 min-w-0 truncate">
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin text-emerald-400 shrink-0" />
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0 shadow-[0_0_6px_#34d399]" />
            )}
            <span className="truncate">{active.label}</span>
            <span className="text-[9px] text-white/40 font-mono shrink-0">
              · {providerTag(active.provider)}
            </span>
          </div>
          <ChevronDown
            className={cn(
              "h-3 w-3 text-white/40 shrink-0 transition-transform duration-150 ml-0.5",
              isOpen && "rotate-180 text-emerald-400",
            )}
          />
        </button>
      </PopoverTrigger>

      <PopoverContent
        side={side}
        align={align}
        sideOffset={6}
        className="z-[15000] w-[300px] sm:w-[330px] p-0 border border-white/[0.14] bg-[#12131a]/98 text-white shadow-2xl shadow-black/95 backdrop-blur-2xl rounded-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-100"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-white/[0.08] bg-white/[0.02]">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-white/90">
            <Sparkles className="h-3.5 w-3.5 text-emerald-400" />
            <span>Текстовая модель</span>
          </div>
          <span className="text-[10px] font-mono text-white/40">
            {status.models.length} моделей
          </span>
        </div>

        {/* Search */}
        <div className="p-2 border-b border-white/[0.06] bg-black/20">
          <div className="relative flex items-center">
            <Search className="absolute left-2.5 h-3.5 w-3.5 text-white/35 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Поиск (Claude, GPT, Gemini...)"
              className="w-full h-7 pl-8 pr-2 text-xs bg-white/[0.04] border border-white/10 rounded-lg text-white placeholder:text-white/35 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/30 transition-all"
            />
          </div>
        </div>

        {/* Model list grouped */}
        <div className="max-h-[320px] overflow-y-auto p-1.5 space-y-2 divide-y divide-white/[0.04]">
          {groups.length === 0 ? (
            <div className="py-6 text-center text-xs text-white/40">
              Модели не найдены
            </div>
          ) : (
            groups.map((g) => (
              <div key={g.name} className="pt-1.5 first:pt-0">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-white/40 flex items-center justify-between">
                  <span>{g.name}</span>
                  <span className="text-[9px] font-mono text-white/30">{g.models.length}</span>
                </div>
                <div className="space-y-0.5">
                  {g.models.map((m) => {
                    const isSelected = m.id === active.id;
                    const isDisabled = !m.key_configured && m.provider !== "kie";

                    return (
                      <button
                        key={m.id}
                        type="button"
                        disabled={isDisabled || busy}
                        onClick={() => {
                          if (!isDisabled) {
                            void onChange(m.provider, m.id);
                            setIsOpen(false);
                          }
                        }}
                        className={cn(
                          "w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs transition-colors cursor-pointer",
                          isSelected
                            ? "bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 font-medium"
                            : isDisabled
                              ? "opacity-40 cursor-not-allowed hover:bg-transparent text-white/40"
                              : "hover:bg-white/[0.06] text-white/80 hover:text-white",
                        )}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <span className="truncate">{m.label}</span>
                          <span className="shrink-0 text-[9px] px-1.5 py-0.5 rounded border border-white/10 bg-white/[0.04] text-white/50 font-mono">
                            {providerTag(m.provider)}
                          </span>
                        </div>

                        {isSelected ? (
                          <Check className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                        ) : isDisabled ? (
                          <span className="text-[9px] text-amber-400/80 flex items-center gap-1 shrink-0">
                            <Lock className="h-2.5 w-2.5" />
                            нет ключа
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
