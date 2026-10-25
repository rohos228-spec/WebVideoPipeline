"use client";

import { useEffect, useMemo, useState } from "react";
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

export function TextLlmPicker({ className }: { className?: string }) {
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
    const byGroup = new Map<string, TextLlmModel[]>();
    for (const m of models) {
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
  }, [status]);

  if (!status?.models?.length) return null;

  const active = status.models.find((m) => m.active) || status.models[0];

  return (
    <select
      className={cn(
        "h-8 max-w-[260px] truncate rounded-md border border-white/10 bg-black/50 px-2 text-[11px] text-white/85",
        "focus:outline-none focus:ring-1 focus:ring-primary/40",
        busy && "opacity-60",
        className,
      )}
      disabled={busy}
      value={active.id}
      title={status.active_label}
      aria-label="Текстовая модель"
      onChange={(e) => {
        const m = status.models.find((x) => x.id === e.target.value);
        if (m) void onChange(m.provider, m.id);
      }}
    >
      {groups.map((g) => (
        <optgroup key={g.name} label={g.name}>
          {g.models.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.key_configured && m.provider !== "kie"}>
              {m.label} · {providerTag(m.provider)}
              {!m.key_configured && m.provider !== "kie" ? " · нет ключа" : ""}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
