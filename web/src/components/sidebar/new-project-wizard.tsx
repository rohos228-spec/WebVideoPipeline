"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Layers, Clapperboard, Check, Film } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { projectDisplayName } from "@/lib/project-display";
import type { PipelineMode, ProjectSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
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

export function NewProjectWizard({
  trigger,
  onCreated,
  folderId = null,
  open: openProp,
  onOpenChange,
}: {
  trigger?: React.ReactNode;
  onCreated: (p: ProjectSummary) => void;
  folderId?: string | null;
  /** Внешнее управление: нужно, когда окно открывают не кнопкой «+», а событием. */
  open?: boolean;
  onOpenChange?: (v: boolean) => void;
}) {
  const [innerOpen, setInnerOpen] = useState(false);
  const open = openProp ?? innerOpen;
  const setOpen = onOpenChange ?? setInnerOpen;
  const [projectTitle, setProjectTitle] = useState("");
  const [pipelineMode, setPipelineMode] = useState<PipelineMode>("v1");
  const qc = useQueryClient();

  const reset = () => {
    setProjectTitle("");
    setPipelineMode("v1");
  };

  const create = useMutation({
    mutationFn: async () => {
      const rawTitle = projectTitle.trim() || "Новый проект";
      const p = await api.createProject({
        title: rawTitle.slice(0, 120),
        topic: rawTitle,
        hero_mode: "auto",
        auto_mode: false,
        pipeline_mode: pipelineMode,
        sidebar_folder_id: folderId,
      });
      return p;
    },
    onSuccess: (p) => {
      try {
        localStorage.setItem("studio.pipeline_mode", JSON.stringify(p.pipeline_mode === "v2" ? "v2" : "v1"));
      } catch {}
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
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent
        className="sm:max-w-[820px] max-h-[92vh] flex flex-col p-6 bg-zinc-950/95 border-white/10 shadow-[0_24px_80px_rgba(0,0,0,0.8)] [contain:paint] overflow-hidden"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0 space-y-1.5 pb-2 border-b border-white/[0.08]">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-400/20 text-cyan-400">
              <Film className="h-4 w-4" />
            </div>
            <div>
              <DialogTitle className="text-lg font-bold tracking-tight text-white">
                Новый проект
              </DialogTitle>
              <DialogDescription className="text-xs text-zinc-400">
                Выберите архитектуру пайплайна и укажите название проекта. Механика закрепляется за проектом при создании.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto pr-1 -mr-1 py-4 space-y-5">
          {/* Сетка двух механик с подробным описанием */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Карточка: Классический пайплайн (v1) */}
            <div
              onClick={() => setPipelineMode("v1")}
              className={cn(
                "relative flex flex-col justify-between rounded-2xl border p-5 transition-all duration-200 cursor-pointer text-left select-none",
                pipelineMode === "v1"
                  ? "border-cyan-400/50 bg-gradient-to-b from-cyan-950/30 to-zinc-900/40 shadow-lg shadow-cyan-950/20 ring-1 ring-cyan-500/30"
                  : "border-white/[0.08] bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]",
              )}
            >
              <div>
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
                      <Layers className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-white flex items-center gap-1.5">
                        Классика (v1)
                      </h3>
                      <p className="text-[11px] font-medium text-cyan-300/80">Линейный конвейер</p>
                    </div>
                  </div>

                  {pipelineMode === "v1" ? (
                    <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30 gap-1 text-[10px] font-semibold uppercase tracking-wider">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Выбрано
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="text-zinc-500 border-white/10 text-[10px]">
                      v1
                    </Badge>
                  )}
                </div>

                <p className="text-xs text-zinc-300 leading-relaxed mb-4">
                  Сквозное линейное производство от общей идеи до готовых кадров. Быстро, просто и стабильно.
                </p>

                {/* Что внутри */}
                <div className="space-y-2 mb-4 bg-black/30 rounded-xl p-3 border border-white/[0.05]">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                    Что внутри:
                  </span>
                  <ul className="space-y-1.5 text-xs text-zinc-300">
                    <li className="flex items-start gap-2">
                      <span className="text-cyan-400 font-bold">•</span>
                      <span><strong>Авто-сценарий:</strong> единый текст ролика и тайминг</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-cyan-400 font-bold">•</span>
                      <span><strong>Линейная раскадровка:</strong> кадры строятся последовательно по тексту</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-cyan-400 font-bold">•</span>
                      <span><strong>Пакетная генерация:</strong> параллельный рендер картинок и видео (KIE / Midjourney)</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-cyan-400 font-bold">•</span>
                      <span><strong>Быстрый монтаж:</strong> мгновенный экспорт в Premiere / CapCut</span>
                    </li>
                  </ul>
                </div>

                {/* Рекомендации каналов */}
                <div className="rounded-lg bg-cyan-950/20 border border-cyan-800/30 px-3 py-2 mb-4">
                  <span className="text-[10px] font-semibold text-cyan-300 block mb-0.5">
                    Идеально для каналов:
                  </span>
                  <p className="text-[11px] text-zinc-300 leading-snug">
                    Shorts, Reels, TikTok, познавательные факты, цитаты, конвейерные каналы с частыми публикациями.
                  </p>
                </div>
              </div>

              <div
                className={cn(
                  "w-full text-xs font-semibold h-9 rounded-xl flex items-center justify-center transition-all",
                  pipelineMode === "v1"
                    ? "bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 shadow-sm"
                    : "bg-white/[0.04] text-zinc-400 border border-white/10 hover:text-zinc-200",
                )}
              >
                {pipelineMode === "v1" ? (
                  <>
                    <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" />
                    Выбрана Классика
                  </>
                ) : (
                  "Выбрать Классику (v1)"
                )}
              </div>
            </div>

            {/* Карточка: Режиссёрский монтаж (v2) */}
            <div
              onClick={() => setPipelineMode("v2")}
              className={cn(
                "relative flex flex-col justify-between rounded-2xl border p-5 transition-all duration-200 cursor-pointer text-left select-none",
                pipelineMode === "v2"
                  ? "border-violet-400/50 bg-gradient-to-b from-violet-950/30 to-zinc-900/40 shadow-lg shadow-violet-950/20 ring-1 ring-violet-500/30"
                  : "border-white/[0.08] bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]",
              )}
            >
              <div>
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-violet-500/30 bg-violet-500/10 text-violet-300">
                      <Clapperboard className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-white flex items-center gap-1.5">
                        Режиссёрский монтаж (v2)
                      </h3>
                      <p className="text-[11px] font-medium text-violet-300/80">Сцены и биты</p>
                    </div>
                  </div>

                  {pipelineMode === "v2" ? (
                    <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30 gap-1 text-[10px] font-semibold uppercase tracking-wider">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Выбрано
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="text-zinc-500 border-white/10 text-[10px]">
                      v2
                    </Badge>
                  )}
                </div>

                <p className="text-xs text-zinc-300 leading-relaxed mb-4">
                  Драматургический подход с делением на сцены, эмоциональные биты и дубли. Полный контроль персонажей, локаций и операторской работы.
                </p>

                {/* Что внутри */}
                <div className="space-y-2 mb-4 bg-black/30 rounded-xl p-3 border border-white/[0.05]">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                    Что внутри:
                  </span>
                  <ul className="space-y-1.5 text-xs text-zinc-300">
                    <li className="flex items-start gap-2">
                      <span className="text-violet-400 font-bold">•</span>
                      <span><strong>Дизайн сцен:</strong> персонажи, окружение, освещение и ракурсы для каждой сцены</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-violet-400 font-bold">•</span>
                      <span><strong>Смысловые биты:</strong> расстановка смысловых акцентов и темпа сюжета</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-violet-400 font-bold">•</span>
                      <span><strong>Система дублей:</strong> варианты кадров (shot_01, shot_02) и отбор лучших шотов</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-violet-400 font-bold">•</span>
                      <span><strong>Монтажная доска:</strong> гибкое перекрытие планов и точный монтажный ритм</span>
                    </li>
                  </ul>
                </div>

                {/* Рекомендации каналов */}
                <div className="rounded-lg bg-violet-950/20 border border-violet-800/30 px-3 py-2 mb-4">
                  <span className="text-[10px] font-semibold text-violet-300 block mb-0.5">
                    Идеально для каналов:
                  </span>
                  <p className="text-[11px] text-zinc-300 leading-snug">
                    Длинные ролики (YouTube 16:9), документальные фильмы, сюжетные истории, авторские каналы с глубокой режиссурой.
                  </p>
                </div>
              </div>

              <div
                className={cn(
                  "w-full text-xs font-semibold h-9 rounded-xl flex items-center justify-center transition-all",
                  pipelineMode === "v2"
                    ? "bg-violet-500/20 text-violet-300 border border-violet-400/40 shadow-sm"
                    : "bg-white/[0.04] text-zinc-400 border border-white/10 hover:text-zinc-200",
                )}
              >
                {pipelineMode === "v2" ? (
                  <>
                    <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" />
                    Выбран Монтаж
                  </>
                ) : (
                  "Выбрать Монтаж (v2)"
                )}
              </div>
            </div>
          </div>

          {/* Название проекта */}
          <div className="space-y-2 pt-2 border-t border-white/[0.08]">
            <label className="text-xs font-semibold uppercase tracking-wider text-zinc-300 flex items-center justify-between">
              <span>Название проекта</span>
              <span className="text-[11px] font-normal text-zinc-500 lowercase">
                обязательное поле
              </span>
            </label>
            <Input
              placeholder="Например: Киберпанк: Хроники мегаполиса"
              value={projectTitle}
              onChange={(e) => setProjectTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && projectTitle.trim() && !create.isPending) {
                  create.mutate();
                }
              }}
              className="h-10 bg-zinc-900/80 border-white/10 text-sm text-zinc-100 placeholder:text-zinc-500 focus-visible:ring-1 focus-visible:ring-cyan-500/50"
              autoFocus
            />
          </div>
        </div>

        <DialogFooter className="shrink-0 pt-3 flex items-center justify-between sm:justify-between border-t border-white/[0.08] mt-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setOpen(false);
              reset();
            }}
            className="text-xs text-zinc-400 hover:text-white"
          >
            Отмена
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={create.isPending || !projectTitle.trim()}
            onClick={() => create.mutate()}
            className={cn(
              "min-w-[150px] h-9 text-xs font-semibold text-white shadow-md rounded-xl transition-all disabled:opacity-40",
              pipelineMode === "v2"
                ? "bg-violet-600 hover:bg-violet-500 border border-violet-400/50 shadow-violet-900/30"
                : "bg-cyan-600 hover:bg-cyan-500 border border-cyan-400/50 shadow-cyan-900/30",
            )}
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
