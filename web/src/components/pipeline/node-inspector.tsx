"use client";

import { Button } from "@/components/ui/button";
import { Chip, Empty } from "@/components/ui/bits";
import { PromptEditor } from "@/components/editors/prompt-editor";
import type { GraphNode, NodeKindInfo } from "@/lib/types";

/**
 * Инспектор выбранного узла: что он делает, его промт, включён ли он.
 *
 * Промт правится здесь тем же компонентом, что и в ленте стадий. Один
 * редактор на два места, потому что это буквально один и тот же файл: правка
 * из схемы обязана быть видна в стадии и наоборот, а две реализации разошлись
 * бы на первой же доработке.
 */
export function NodeInspector({
  node,
  catalog,
  onChange,
  onRemove,
}: {
  node: GraphNode | null;
  catalog: NodeKindInfo[];
  onChange: (next: GraphNode) => void;
  onRemove: () => void;
}) {
  const info = catalog.find((n) => n.type === node?.type);

  if (!node) {
    return (
      <div className="p-4">
        <Empty>Выберите узел на схеме — здесь появятся его промт и настройки.</Empty>
      </div>
    );
  }

  const disabled = node.data?.disabled === true;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-[16px] text-content">{info?.label ?? node.type}</h2>
            <p className="mt-0.5 font-mono text-[11px] text-content-faint">{node.type}</p>
          </div>
          {disabled && <Chip tone="warn">выключен</Chip>}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onChange({ ...node, data: { ...node.data, disabled: !disabled } })}
          >
            {disabled ? "Включить" : "Выключить"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onRemove}>
            Убрать из схемы
          </Button>
        </div>

        {disabled && (
          <p className="mt-2 text-[12px] text-content-muted">
            Выключенный узел конвейер пропускает — цепочка идёт дальше без него.
          </p>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {info?.has_prompt && info.step_code ? (
          // Тот же редактор, что в ленте стадий: это буквально один файл, и
          // правка из схемы обязана быть видна в стадии. `projectId={0}` —
          // «без проекта»: в схеме правится общий промт, не переопределение.
          <PromptEditor prompts={[{ step: info.step_code, label: "Промт узла" }]} projectId={0} />
        ) : (
          <Empty>
            У этого узла нет промта — он не обращается к модели, а делает работу кодом.
          </Empty>
        )}
      </div>
    </div>
  );
}
