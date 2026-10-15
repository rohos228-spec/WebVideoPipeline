"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { PromptDTO } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { PromptCatalogPicker } from "@/components/prompts/prompt-card-catalog";

const PROMPT_LABELS: Record<string, string> = {
  PLAN_SHORTS: "План",
  SCRIPT_SHORTS: "Сценарий",
  IMAGE_SHORTS: "Image prompt",
  VIDEO_SHORTS: "Animation prompt",
  IMAGE_CHECK: "Проверка картинок",
  VIDEO_CHECK: "Проверка видео",
  HERO_SHORTS: "Герой (reference)",
  RAZBIVKA_SLOV: "Разбивка слов",
};

export function PromptEditor({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: api.listPrompts,
    enabled: open,
  });
  const [activeId, setActiveId] = useState<number | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  // По умолчанию выбираем первый активный.
  useEffect(() => {
    if (!activeId && prompts.data && prompts.data.length > 0) {
      setActiveId(prompts.data.find((p) => p.active)?.id ?? prompts.data[0].id);
    }
  }, [activeId, prompts.data]);

  const active = (prompts.data ?? []).find((p) => p.id === activeId) ?? null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="!max-w-6xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <KeyRound className="h-3.5 w-3.5 text-primary" />
            Мастер-промты
          </SheetTitle>
          <SheetDescription>
            Шаблоны для ChatGPT-шагов. Подставляются в каждый шаг через
            переменные. Изменения применяются к новым проектам — старые остаются
            на своих snapshot'ах.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-3 overflow-hidden">
          {prompts.isLoading && (
            <div className="flex items-center justify-center p-4">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}
          {prompts.data && (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              className="flex shrink-0 items-center gap-3 rounded-xl border border-white/10 bg-[#101015] p-2.5 text-left transition hover:border-[#22d3ee]/50"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[10px] uppercase tracking-wider text-white/45">Мастер-промт</span>
                <span className="block truncate text-[13px] font-semibold text-white">
                  {active ? (PROMPT_LABELS[active.key] ?? active.key) : "не выбран"}
                </span>
              </span>
              <span className="rounded-lg bg-[#22d3ee] px-3 py-1.5 text-[11px] font-semibold text-black">
                Каталог промтов
              </span>
            </button>
          )}
          <PromptCatalogPicker
            open={pickerOpen}
            onOpenChange={setPickerOpen}
            title="Мастер-промты"
            selectedId={active ? String(active.id) : null}
            onDone={(id) => setActiveId(Number(id))}
            items={groupByKey(prompts.data ?? []).map(({ key, versions }) => ({
              id: String(versions[0].id),
              label: PROMPT_LABELS[key] ?? key,
              kind: "text" as const,
              category: versions[0].active ? "Активные" : "Неактивные",
              description: `${key} · v${versions[0].version}`,
              content: versions[0].text,
              previewUrl: null,
            }))}
          />
          <div className="flex flex-1 flex-col overflow-hidden">
            {active ? (
              <PromptEditorPanel prompt={active} />
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                Выбери промт слева.
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function PromptEditorPanel({ prompt }: { prompt: PromptDTO }) {
  const qc = useQueryClient();
  const [text, setText] = useState(prompt.text);

  useEffect(() => {
    setText(prompt.text);
  }, [prompt.id, prompt.text]);

  const dirty = text !== prompt.text;

  const save = useMutation({
    mutationFn: () => api.patchPrompt(prompt.id, { text }),
    onSuccess: () => {
      toast.success(`Промт «${prompt.key}» сохранён`);
      qc.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) => toast.error(`Не сохранилось: ${String(e)}`),
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-muted/30 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">
            {PROMPT_LABELS[prompt.key] ?? prompt.key}
          </span>
          <Badge variant={prompt.active ? "success" : "muted"} className="h-4 px-1.5 text-[9px]">
            {prompt.active ? "active" : "inactive"}
          </Badge>
          <span className="font-mono text-[10px] text-muted-foreground">
            v{prompt.version} · {text.length.toLocaleString("ru-RU")} симв.
          </span>
        </div>
        <Button
          size="sm"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="h-7 gap-1.5 px-3 text-xs"
        >
          {save.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          Сохранить
        </Button>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        className="flex-1 resize-none border-0 bg-canvas-bg/30 px-4 py-3 font-mono text-[12px] leading-relaxed focus:outline-none"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            if (dirty && !save.isPending) save.mutate();
          }
        }}
      />
    </div>
  );
}

function groupByKey(prompts: PromptDTO[]): { key: string; versions: PromptDTO[] }[] {
  const map = new Map<string, PromptDTO[]>();
  for (const p of prompts) {
    if (!map.has(p.key)) map.set(p.key, []);
    map.get(p.key)!.push(p);
  }
  return Array.from(map.entries())
    .map(([key, versions]) => ({
      key,
      versions: versions.sort((a, b) => b.version - a.version),
    }))
    .sort((a, b) => a.key.localeCompare(b.key));
}
