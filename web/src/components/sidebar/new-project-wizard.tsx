"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { projectDisplayName } from "@/lib/project-display";
import type { ProjectSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";


const HERO_CHOICES = [
  { id: "auto", label: "Авто" },
  { id: "hero", label: "С героями" },
  { id: "no_hero", label: "Без героев" },
] as const;

export function NewProjectWizard({
  trigger,
  onCreated,
  folderId = null,
}: {
  trigger: React.ReactNode;
  onCreated: (p: ProjectSummary) => void;
  folderId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [heroMode, setHeroMode] = useState<"hero" | "no_hero" | "auto">("auto");
  const qc = useQueryClient();

  const reset = () => {
    setProjectTitle("");
    setTopic("");
    setHeroMode("auto");
  };

  const create = useMutation({
    mutationFn: async () => {
      const finalTopic = topic.trim();
      const rawTitle = projectTitle.trim() || (finalTopic ? finalTopic.slice(0, 40) : "Новый проект");
      const p = await api.createProject({
        title: rawTitle.slice(0, 120),
        topic: finalTopic || rawTitle,
        hero_mode: heroMode,
        auto_mode: false,
        sidebar_folder_id: folderId,
      });
      return p;
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onCreated(p);
      setOpen(false);
      reset();
      toast.success(`Проект «${projectDisplayName(p)}» создан`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        className="sm:max-w-[620px] max-h-[90vh] flex flex-col bg-card border-border p-6 shadow-2xl overflow-hidden"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0 space-y-1">
          <DialogTitle className="text-lg font-semibold tracking-tight">Новый проект</DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Задайте сюжет и ключевые параметры ролика. Технические настройки генераторов можно настроить прямо на нодах.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto pr-1.5 -mr-1.5 space-y-4 py-2">
          {/* Название */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Название проекта
            </label>
            <Input
              placeholder="Например: Warhammer 40K: Бастион Кровавых Ангелов"
              value={projectTitle}
              onChange={(e) => setProjectTitle(e.target.value)}
              className="h-9 bg-background"
            />
          </div>

          {/* Сюжет / Бриф */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Сюжет / Бриф
            </label>
            <Textarea
              placeholder="Опишите сюжет своими словами..."
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={5}
              className="resize-y min-h-[100px] max-h-[260px] bg-background text-sm leading-relaxed overflow-y-auto"
            />
          </div>

          {/* Персонажи */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Персонажи
            </label>
            <div className="inline-flex rounded-lg border border-border p-0.5 bg-muted/30">
              {HERO_CHOICES.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  onClick={() => setHeroMode(h.id)}
                  className={cn(
                    "rounded-md px-3 py-1 text-xs font-medium transition-all",
                    heroMode === h.id
                      ? "bg-cyan-500/20 text-cyan-300 border border-cyan-400/30 shadow-sm font-semibold"
                      : "text-muted-foreground hover:text-foreground border border-transparent"
                  )}
                >
                  {h.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter className="shrink-0 pt-3 flex items-center justify-between sm:justify-between border-t border-border mt-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setOpen(false);
              reset();
            }}
          >
            Отмена
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={create.isPending || (!projectTitle.trim() && !topic.trim())}
            onClick={() => create.mutate()}
            className="min-w-[140px] h-9 text-xs font-semibold text-white bg-cyan-600 hover:bg-cyan-500 border border-cyan-400/50 shadow-md shadow-cyan-500/25 rounded-xl transition-all disabled:opacity-50"
          >
            {create.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Создание...
              </>
            ) : (
              "Создать проект"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
