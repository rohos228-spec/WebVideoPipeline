"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/stage-api";
import { Button } from "@/components/ui/bits";

/**
 * Пауза vision-проверки: GPT забраковал кадры N кругов подряд
 * (vision_check_loop ставит status=paused + meta.pause_reason.code).
 * Ручной перезапуск ноды счетчик НЕ сбрасывает — нужно явное решение:
 * more_rounds (еще круги) или accept_pending (принять кадры как есть).
 * Показывается только в этой паузе, в остальное время — ничего.
 */
export function VisionPauseBanner({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.project(projectId),
  });

  const decide = useMutation({
    mutationFn: (action: "more_rounds" | "accept_pending") => api.visionDecision(projectId, action),
    onSuccess: (_, action) => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["stages", projectId] });
      toast.success(
        action === "more_rounds" ? "Проверка продолжится еще кругами" : "Кадры приняты как есть",
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reason = (project?.meta as Record<string, unknown> | null | undefined)?.[
    "pause_reason"
  ] as { code?: string } | null | undefined;
  if (project?.status !== "paused" || reason?.code !== "vision_rounds_exhausted") return null;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-warn bg-warn-muted px-4 py-3 text-[13px] text-content">
      <span>
        Проверка кадров не прошла за отведенные круги. Перезапуск ноды не поможет —
        нужно решение:
      </span>
      <Button
        size="sm"
        variant="primary"
        disabled={decide.isPending}
        onClick={() => decide.mutate("more_rounds")}
        title="Сбросить счетчик проверки и продолжить теми же кадрами"
      >
        Ещё круги
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={decide.isPending}
        onClick={() => decide.mutate("accept_pending")}
        title="Принять ожидающие кадры без допроверки"
      >
        Принять как есть
      </Button>
    </div>
  );
}
