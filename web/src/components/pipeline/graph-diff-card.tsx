"use client";

import { Button } from "@/components/ui/button";
import type { GraphDiff, ResetPlan } from "@/lib/types";

/**
 * Разница графа и что сгорит — одна карточка на два места.
 *
 * Показывается перед применением правок с холста и под предложением агента
 * в чате. Обе ситуации — одно решение: «вот что изменится, вот что придётся
 * переделать, применяем?». Две карточки разошлись бы на первой доработке.
 */
export function GraphDiffCard({
  title,
  diff,
  reset,
  warnings = [],
  busy,
  onApply,
  onCancel,
  applyLabel,
  cancelLabel = "Отмена",
}: {
  title?: string;
  diff: GraphDiff;
  reset: ResetPlan;
  warnings?: string[];
  busy: boolean;
  onApply: (reset: boolean) => void;
  onCancel: () => void;
  applyLabel?: string;
  cancelLabel?: string;
}) {
  const burns = reset.steps.length > 0;
  return (
    <div className="space-y-2 text-[13px]">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-display text-[15px] text-content">{title ?? "Что изменится"}</span>
        <span className="font-mono text-[12px] text-content-muted">{diff.summary}</span>
      </div>

      <ul className="space-y-0.5 text-content-muted">
        {diff.added_nodes.map((n) => (
          <li key={`+${n.id}`}>
            <span className="text-ok">+</span> {n.label} <span className="font-mono text-[11px] text-content-faint">{n.type}</span>
          </li>
        ))}
        {diff.removed_nodes.map((n) => (
          <li key={`-${n.id}`}>
            <span className="text-danger">−</span> {n.label}
          </li>
        ))}
        {diff.changed_nodes.map((n) => (
          <li key={`~${n.id}`}>
            <span className="text-warn">~</span> {n.label}
            {n.changes?.length ? (
              <span className="font-mono text-[11px] text-content-faint"> {n.changes.join(", ")}</span>
            ) : null}
          </li>
        ))}
        {(diff.added_edges.length > 0 || diff.removed_edges.length > 0 || diff.changed_edges.length > 0) && (
          <li className="text-content-faint">
            связи: +{diff.added_edges.length} −{diff.removed_edges.length} ~{diff.changed_edges.length}
          </li>
        )}
      </ul>

      {burns ? (
        <div className="rounded-sm border border-warn bg-warn-muted px-3 py-2 text-warn">
          <div>
            Сгорят шаги: <span className="font-mono">{reset.steps.join(" → ")}</span>
          </div>
          {Object.values(reset.reasons)
            .slice(0, 3)
            .map((r, i) => (
              <div key={i} className="text-[12px] opacity-80">
                {r}
              </div>
            ))}
        </div>
      ) : (
        <div className="text-[12px] text-content-faint">Сделанные шаги не пострадают.</div>
      )}

      {warnings.length > 0 && (
        <ul className="text-[12px] text-content-faint">
          {warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <Button size="sm" variant="primary" disabled={busy} onClick={() => onApply(true)}>
          {applyLabel ?? (burns ? "Применить и сбросить" : "Применить")}
        </Button>
        {burns && (
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onApply(false)} title="Оставить результаты как есть">
            Применить без сброса
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          {cancelLabel}
        </Button>
      </div>
    </div>
  );
}
