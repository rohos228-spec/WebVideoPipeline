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

function formatStepCount(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 19) return `${n} шагов`;
  if (mod10 === 1) return `${n} шаг`;
  if (mod10 >= 2 && mod10 <= 4) return `${n} шага`;
  return `${n} шагов`;
}

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
      <DialogContent
        className="w-[min(580px,94vw)] max-w-[580px] z-[200] overflow-hidden"
        overlayClassName="z-[190]"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Правка затрагивает сделанные шаги</DialogTitle>
          <DialogDescription>
            {summary ? `Изменения: ${summary}. ` : ""}
            Результаты этих шагов устареют:
          </DialogDescription>
        </DialogHeader>
        <ul className="max-h-48 space-y-1 overflow-y-auto text-xs pr-1">
          {reset.steps.map((s) => (
            <li key={s} className="flex items-baseline gap-2">
              <span className="font-medium text-foreground">{formatStepCode(s)}</span>
              {reset.reasons[s] && (
                <span className="text-muted-foreground">{reset.reasons[s]}</span>
              )}
            </li>
          ))}
        </ul>
        <DialogFooter className="flex-wrap items-center justify-end gap-2.5 pt-2 sm:space-x-0">
          <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel} className="text-xs">
            Отменить
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onApply(false)}
            title="Схема изменится, но посчитанные результаты останутся как есть"
            className="text-xs font-medium"
          >
            Применить без сброса
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy}
            onClick={() => onApply(true)}
            className="text-xs font-semibold"
          >
            {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            Применить и сбросить {formatStepCount(reset.steps.length)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
