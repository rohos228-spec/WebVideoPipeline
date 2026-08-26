"use client";

import { Button } from "@/components/ui/button";
import { Chip, Empty, Label } from "@/components/ui/bits";
import { PromptEditor } from "@/components/editors/prompt-editor";
import type { GraphNode, ModelChoice, NodeKindInfo, NodeState } from "@/lib/types";

/**
 * Инспектор выбранного узла: что он делает, его промт, включён ли он.
 *
 * Промт правится здесь тем же компонентом, что и в ленте стадий. Один
 * редактор на два места, потому что это буквально один и тот же файл: правка
 * из схемы обязана быть видна в стадии и наоборот, а две реализации разошлись
 * бы на первой же доработке.
 *
 * В режиме проекта (`project` задан) у узла появляются рычаги прогона —
 * запустить шаг, сбросить его результат — и выбор модели. Всё это живёт на
 * узле, а не на проекте, потому что «сценарий — дешёвой моделью, картинки —
 * дорогой» — обычное желание, и одна модель на проект его не выражает.
 */

const STATE_CHIP: Partial<Record<NodeState, { tone: "neutral" | "ok" | "warn" | "danger" | "accent"; text: string }>> = {
  running: { tone: "accent", text: "идёт" },
  queued: { tone: "accent", text: "в очереди" },
  done: { tone: "ok", text: "готово" },
  failed: { tone: "danger", text: "сбой" },
  waiting_hitl: { tone: "warn", text: "ждёт проверки" },
  skipped: { tone: "neutral", text: "пропущен" },
};

const IMAGE_TYPES = new Set(["images", "hero", "items"]);
const VIDEO_TYPES = new Set(["videos"]);

export function NodeInspector({
  node,
  catalog,
  project,
  onChange,
  onRemove,
}: {
  node: GraphNode | null;
  catalog: NodeKindInfo[];
  /** Режим проекта: состояние, цена, запуск, сброс, модель. */
  project?: {
    id: number;
    state?: NodeState;
    price?: string;
    models: { text: ModelChoice[]; image: ModelChoice[]; video: ModelChoice[] };
    busy: boolean;
    onRun: (stepCode: string, nodeKey: string) => void;
    onReset: (stepCode: string) => void;
  };
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
  const label = (node.data?.label as string) || "";
  const modelId = (node.data?.modelId as string) || "";
  const step = info?.step_code ?? null;
  const chip = project?.state ? STATE_CHIP[project.state] : undefined;
  const models = project
    ? IMAGE_TYPES.has(node.type)
      ? project.models.image
      : VIDEO_TYPES.has(node.type)
        ? project.models.video
        : project.models.text
    : [];
  const canRun = Boolean(project && step && !disabled && project.state !== "running");
  const canReset = Boolean(project && step && (project.state === "done" || project.state === "failed"));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-[16px] text-content">{info?.label ?? node.type}</h2>
            <p className="mt-0.5 font-mono text-[11px] text-content-faint">
              {node.type}
              {step ? ` · ${step}` : ""}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            {disabled ? <Chip tone="warn">выключен</Chip> : chip?.text ? <Chip tone={chip.tone}>{chip.text}</Chip> : null}
            {project?.price && (
              <span className="font-mono text-[11px] tabular-nums text-content-muted">{project.price}</span>
            )}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {project && step && (
            <Button
              size="sm"
              variant="primary"
              disabled={!canRun || project.busy}
              title={disabled ? "Узел выключен" : undefined}
              onClick={() => project.onRun(step, node.id)}
            >
              {project.state === "done" ? "Заново" : "Запустить шаг"}
            </Button>
          )}
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onChange({ ...node, data: { ...node.data, disabled: !disabled } })}
          >
            {disabled ? "Включить" : "Выключить"}
          </Button>
          {project && step && canReset && (
            <Button size="sm" variant="ghost" disabled={project.busy} onClick={() => project.onReset(step)}>
              Сбросить результат
            </Button>
          )}
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

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        <div>
          <Label>Подпись</Label>
          <input
            value={label}
            placeholder={info?.label ?? node.type}
            onChange={(e) => onChange({ ...node, data: { ...node.data, label: e.target.value || undefined } })}
            className="mt-1.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content outline-none focus:border-accent"
          />
        </div>

        {project && step && models.length > 0 && (
          <div>
            <Label>Модель</Label>
            <select
              value={modelId}
              onChange={(e) =>
                onChange({ ...node, data: { ...node.data, modelId: e.target.value || undefined } })
              }
              className="mt-1.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
            >
              <option value="">по настройкам проекта</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.vendor ? ` · ${m.vendor}` : ""}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-content-faint">
              Смена модели на пройденном узле сбросит его результат при сохранении.
            </p>
          </div>
        )}

        {info?.has_prompt && step ? (
          // Тот же редактор, что в ленте стадий: это буквально один файл, и
          // правка из схемы обязана быть видна в стадии. Без проекта правится
          // общий промт, не переопределение.
          <div>
            <Label>Промт</Label>
            <div className="mt-1.5">
              <PromptEditor prompts={[{ step, label: "Промт узла" }]} projectId={project?.id ?? 0} />
            </div>
          </div>
        ) : (
          <Empty>У этого узла нет промта — он не обращается к модели, а делает работу кодом.</Empty>
        )}
      </div>
    </div>
  );
}
