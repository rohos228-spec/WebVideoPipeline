"use client";

import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  CheckCircle2,
  Wand2,
  ShieldCheck,
  Zap,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
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

export function MetaPromptDialog({
  stepCode,
  projectId,
  onSaved,
}: {
  stepCode: string;
  projectId?: number;
  onSaved?: (fileName: string) => void;
}) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [intent, setIntent] = useState("");
  const [presetName, setPresetName] = useState("");
  const [compiledPrompt, setCompiledPrompt] = useState<string | null>(null);
  const [stats, setStats] = useState<{
    length_chars: number;
    lines_count: number;
    has_schema: boolean;
    has_guards: boolean;
  } | null>(null);

  const compileMutation = useMutation({
    mutationFn: async () => {
      if (!intent.trim()) {
        throw new Error("Введите описание задачи для Агента");
      }
      return await api.compileMetaPrompt({
        step_code: stepCode,
        user_intent: intent,
        project_id: projectId,
        target_name: presetName.trim() || undefined,
      });
    },
    onSuccess: (data) => {
      setCompiledPrompt(data.compiled_prompt);
      setStats(data.stats);
      if (!presetName.trim() && data.name) {
        setPresetName(data.name);
      }
      toast.success("Промпт успешно скомпилирован ИИ-Агентом!");
    },
    onError: (err) => {
      toast.error(errorMessageFromUnknown(err));
    },
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!compiledPrompt) return;
      const name = presetName.trim() || "custom_meta_prompt";
      return await api.saveAndActivateMetaPrompt({
        step_code: stepCode,
        name,
        content: compiledPrompt,
        project_id: projectId,
        activate: true,
      });
    },
    onSuccess: (data) => {
      if (!data) return;
      toast.success(`Личный промпт ${data.file_name} сохранён и активирован!`);
      qc.invalidateQueries({ queryKey: ["prompt-files"] });
      qc.invalidateQueries({ queryKey: ["project"] });
      if (onSaved) {
        onSaved(data.file_name);
      }
      setOpen(false);
      setCompiledPrompt(null);
      setIntent("");
      setPresetName("");
    },
    onError: (err) => {
      toast.error(errorMessageFromUnknown(err));
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="gap-1.5 border-cyan-500/40 bg-cyan-500/10 text-xs font-semibold text-cyan-300 hover:bg-cyan-500/20 shadow-sm transition-all"
        >
          Создать с ИИ-агентом
        </Button>
      </DialogTrigger>

      <DialogContent
        overlayClassName="z-[200]"
        className="z-[210] w-[94vw] max-w-5xl max-h-[92vh] overflow-y-auto overflow-x-hidden border-border/80 bg-card/95 backdrop-blur-xl p-6"
      >
        <DialogHeader className="space-y-1">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/20 text-primary shadow-sm">
              <Wand2 className="h-4.5 w-4.5" />
            </div>
            <div className="min-w-0 flex-1">
              <DialogTitle className="text-base font-semibold leading-tight">
                Конструктор мастер-промптов ИИ-Агентом
              </DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground break-words mt-0.5">
                Шаг: <span className="font-mono text-foreground font-semibold">{stepCode}</span> — напишите задачу своими словами или загрузите текст из файла, агент скомпилирует системный .md промпт под этот шаг.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Имя пресета и задача */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="sm:col-span-1 flex flex-col justify-between space-y-1.5">
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  Имя файла (.md):
                </label>
                <Input
                  placeholder="авто-генерация или имя"
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                  className="mt-1 h-9 text-xs font-mono"
                />
              </div>
              <span className="text-[10px] text-muted-foreground/60 leading-tight">
                Создаёт личный промпт, изолированный для вашего аккаунта
              </span>
            </div>
            <div className="sm:col-span-2 space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground">
                  Что должен делать этот промпт:
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-5 gap-1 px-2 text-[10px] text-muted-foreground hover:text-foreground"
                  onClick={() => fileInputRef.current?.click()}
                  title="Загрузить текст из файла (.txt, .md)"
                >
                  <Upload className="h-3 w-3" />
                  Из файла
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.md,text/plain,text/markdown"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = (event) => {
                      const text = event.target?.result;
                      if (typeof text === "string" && text.trim()) {
                        setIntent(text.trim());
                        if (!presetName.trim()) {
                          const autoName = file.name
                            .replace(/\.[^/.]+$/, "")
                            .replace(/[^a-zA-Z0-9_-]+/g, "_")
                            .toLowerCase();
                          setPresetName(autoName);
                        }
                        toast.success(`Текст из файла «${file.name}» загружен`);
                      }
                    };
                    reader.readAsText(file, "utf-8");
                    e.target.value = "";
                  }}
                />
              </div>
              <Textarea
                placeholder="Опишите желаемую логику, стиль, правила или вставьте свой черновик промпта..."
                value={intent}
                onChange={(e) => setIntent(e.target.value)}
                className="mt-1 min-h-[90px] text-xs resize-none leading-relaxed"
              />
            </div>
          </div>

          {/* Кнопка запуска компиляции */}
          <Button
            type="button"
            className="w-full gap-2 text-xs font-semibold h-9 shadow-sm"
            disabled={compileMutation.isPending || !intent.trim()}
            onClick={() => compileMutation.mutate()}
          >
            {compileMutation.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Компилируем промпт через ИИ-Агента...
              </>
            ) : (
              <>
                <Zap className="h-3.5 w-3.5 fill-current" />
                Скомпилировать системный .md промпт
              </>
            )}
          </Button>

          {/* Результат компиляции */}
          {compiledPrompt && (
            <div className="space-y-2 rounded-xl border border-border/80 bg-background/60 p-3.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Готовый результат компиляции
                </div>
                {stats && (
                  <div className="flex items-center gap-2.5 text-[10px] text-muted-foreground">
                    <span>{stats.lines_count} строк</span>
                    <span>·</span>
                    <span>{stats.length_chars} символов</span>
                    {stats.has_guards && (
                      <span className="flex items-center gap-0.5 text-emerald-400 font-medium">
                        <ShieldCheck className="h-3 w-3" /> No-Chat Guard
                      </span>
                    )}
                  </div>
                )}
              </div>

              <Textarea
                value={compiledPrompt}
                onChange={(e) => setCompiledPrompt(e.target.value)}
                className="min-h-[320px] font-mono text-[11px] leading-relaxed bg-black/50 whitespace-pre-wrap break-words resize-y overflow-y-auto"
              />
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:justify-between pt-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
            className="text-xs"
          >
            Отмена
          </Button>

          {compiledPrompt && (
            <Button
              type="button"
              size="sm"
              className="w-32 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm"
              disabled={saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              {saveMutation.isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Сохранить
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
