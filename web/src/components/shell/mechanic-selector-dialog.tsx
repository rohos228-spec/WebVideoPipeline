"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Layers,
  Clapperboard,
  Check,
  ArrowRight,
  Sparkles,
  Palette,
  CheckCircle2,
  SlidersHorizontal,
  Loader2,
  FileVideo,
  Film,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { switchPipelineMode, usePipelineMode } from "@/hooks/use-pipeline-mode";
import type { PipelineMode } from "@/lib/types";
import { toast } from "sonner";

interface MechanicSelectorDialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function MechanicSelectorDialog({
  open: externalOpen,
  onOpenChange: setExternalOpen,
}: MechanicSelectorDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const [activeWorkspaceMode] = usePipelineMode();
  const [switchingTo, setSwitchingTo] = useState<PipelineMode | null>(null);

  const isControlled = externalOpen !== undefined;
  const open = isControlled ? externalOpen : internalOpen;
  const setOpen = (v: boolean) => {
    if (isControlled && setExternalOpen) {
      setExternalOpen(v);
    } else {
      setInternalOpen(v);
    }
  };

  useEffect(() => {
    const handleOpen = () => setOpen(true);
    window.addEventListener("studio-open-mechanic-selector", handleOpen);
    return () => window.removeEventListener("studio-open-mechanic-selector", handleOpen);
  }, []);

  const handleSelectMode = (targetMode: PipelineMode) => {
    if (targetMode === activeWorkspaceMode) {
      toast.info(
        targetMode === "v1"
          ? "Классический пайплайн уже активен"
          : "Режиссёрский монтаж уже активен",
      );
      setOpen(false);
      return;
    }

    setSwitchingTo(targetMode);
    toast.loading(
      targetMode === "v1"
        ? "Переключаем на Классический пайплайн (v1)..."
        : "Переключаем на Режиссёрский монтаж (v2)...",
      { duration: 1500 },
    );

    // Автоматическое бесшовное переключение без ручного нажатия F5
    setTimeout(() => {
      switchPipelineMode(targetMode);
    }, 150);
  };

  const handleOpenStyles = () => {
    setOpen(false);
    window.dispatchEvent(
      new CustomEvent("studio-open-outsee", { detail: { projectId: null } }),
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-[860px] max-h-[92vh] flex flex-col p-6 bg-zinc-950/95 border-white/10 shadow-[0_24px_80px_rgba(0,0,0,0.8)] backdrop-blur-2xl overflow-hidden">
        <DialogHeader className="shrink-0 space-y-1.5 pb-2 border-b border-white/[0.08]">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-400/20 text-cyan-400">
              <SlidersHorizontal className="h-4 w-4" />
            </div>
            <div>
              <DialogTitle className="text-lg font-bold tracking-tight text-white">
                Механики студии и стили производства
              </DialogTitle>
              <DialogDescription className="text-xs text-zinc-400">
                Выберите архитектуру пайплайна. Проекты каждой механики изолированы друг от друга.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto pr-1 -mr-1 py-4 space-y-5">
          {/* Сетка двух основных механик */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Карточка: Классический пайплайн (v1) */}
            <div
              className={cn(
                "relative flex flex-col justify-between rounded-2xl border p-5 transition-all duration-200",
                activeWorkspaceMode === "v1"
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

                  {activeWorkspaceMode === "v1" ? (
                    <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30 gap-1 text-[10px] font-semibold">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Активен
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="text-zinc-400 border-white/10 text-[10px]">
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

              <Button
                type="button"
                onClick={() => handleSelectMode("v1")}
                disabled={activeWorkspaceMode === "v1" || switchingTo !== null}
                className={cn(
                  "w-full text-xs font-semibold h-9 rounded-xl transition-all",
                  activeWorkspaceMode === "v1"
                    ? "bg-zinc-800/80 text-zinc-400 border border-white/5 cursor-default hover:bg-zinc-800/80"
                    : "bg-cyan-600 hover:bg-cyan-500 text-white shadow-md shadow-cyan-900/30",
                )}
              >
                {switchingTo === "v1" ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                    Переключение...
                  </>
                ) : activeWorkspaceMode === "v1" ? (
                  <>
                    <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" />
                    Текущая механика
                  </>
                ) : (
                  <>
                    Выбрать Классику (v1)
                    <ArrowRight className="h-3.5 w-3.5 ml-1.5" />
                  </>
                )}
              </Button>
            </div>

            {/* Карточка: Режиссёрский монтаж (v2) */}
            <div
              className={cn(
                "relative flex flex-col justify-between rounded-2xl border p-5 transition-all duration-200",
                activeWorkspaceMode === "v2"
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

                  {activeWorkspaceMode === "v2" ? (
                    <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30 gap-1 text-[10px] font-semibold">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Активен
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="text-zinc-400 border-white/10 text-[10px]">
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

              <Button
                type="button"
                onClick={() => handleSelectMode("v2")}
                disabled={activeWorkspaceMode === "v2" || switchingTo !== null}
                className={cn(
                  "w-full text-xs font-semibold h-9 rounded-xl transition-all",
                  activeWorkspaceMode === "v2"
                    ? "bg-zinc-800/80 text-zinc-400 border border-white/5 cursor-default hover:bg-zinc-800/80"
                    : "bg-violet-600 hover:bg-violet-500 text-white shadow-md shadow-violet-900/30",
                )}
              >
                {switchingTo === "v2" ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                    Переключение...
                  </>
                ) : activeWorkspaceMode === "v2" ? (
                  <>
                    <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" />
                    Текущая механика
                  </>
                ) : (
                  <>
                    Выбрать Режиссёрский монтаж (v2)
                    <ArrowRight className="h-3.5 w-3.5 ml-1.5" />
                  </>
                )}
              </Button>
            </div>
          </div>

          {/* Связка с визуальными стилями каналов (Outsee Styles) */}
          <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-r from-zinc-900/80 via-zinc-900/50 to-zinc-900/80 p-4">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-300 mt-0.5">
                  <Palette className="h-4 w-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white flex items-center gap-1.5">
                    Связка: 1 канал = 1 фиксированный стиль
                  </h4>
                  <p className="text-[11px] text-zinc-400 leading-relaxed mt-0.5">
                    Визуальный стиль (аниме, 3D Pixar, нуар, фотореализм) и шаблоны промптов настраиваются для каждого канала в каталоге стилей. Вы можете выбрать готовый пресет или загрузить свой по референсам.
                  </p>
                </div>
              </div>

              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleOpenStyles}
                className="shrink-0 text-xs font-semibold gap-1.5 border-amber-500/30 bg-amber-950/20 text-amber-300 hover:bg-amber-900/30 hover:border-amber-400/50 hover:text-amber-200"
              >
                <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                Каталог стилей Outsee
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
