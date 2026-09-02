"use client";

/**
 * «Правка жжёт сделанные шаги» — предохранитель поверх автосейва канваса.
 *
 * Автосейв остался как в старой студии: позиции, связи и настройки уезжают
 * сами. Но если сервер (слой project_graph) говорит, что от правки устареют
 * уже посчитанные результаты, применение останавливается здесь — человек
 * решает: сбросить устаревшее, оставить как есть или отменить.
 */

import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatStepCode } from "@/lib/format-labels";
import type { ResetPlan } from "@/lib/stage-types";
import type { WorkflowEdge, WorkflowNode } from "@/lib/types";

export function GraphResetDialog({
  pending,
  busy,
  onApply,
  onCancel,
}: {
  pending: { nodes: WorkflowNode[]; edges: WorkflowEdge[]; reset: ResetPlan; summary: string } | null;
  busy: boolean;
  onApply: (reset: boolean) => void;
  onCancel: () => void;
}) {
  if (!pending) return null;
  const { reset, summary } = pending;
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Правка затрагивает сделанные шаги</DialogTitle>
          <DialogDescription>
            {summary ? `Изменения: ${summary}. ` : ""}
            Результаты этих шагов устареют:
          </DialogDescription>
        </DialogHeader>
        <ul className="max-h-48 space-y-1 overflow-y-auto text-xs">
          {reset.steps.map((s) => (
            <li key={s} className="flex items-baseline gap-2">
              <span className="font-medium text-foreground">{formatStepCode(s)}</span>
              {reset.reasons[s] && (
                <span className="text-muted-foreground">{reset.reasons[s]}</span>
              )}
            </li>
          ))}
        </ul>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            Отменить
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onApply(false)}
            title="Схема изменится, но посчитанные результаты останутся как есть"
          >
            Применить без сброса
          </Button>
          <Button size="sm" variant="destructive" disabled={busy} onClick={() => onApply(true)}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Применить и сбросить {reset.steps.length} шаг(а)
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
