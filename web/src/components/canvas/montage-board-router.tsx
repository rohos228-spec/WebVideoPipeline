"use client";

/**
 * Роутер монтажной доски по механике проекта (R1: строгая изоляция).
 *
 * V1 — классическая доска main (assemble-montage-board-v1).
 * V2 — режиссёрская доска (assemble-montage-board).
 * Сигнатуры обеих досок идентичны; выбор — только здесь, условий
 * `if mode` внутри досок нет. Дефолт при загрузке — v1.
 */

import {
  AssembleMontageBoard as AssembleMontageBoardV2,
  AssembleMontageTrigger as AssembleMontageTriggerV2,
} from "@/components/canvas/assemble-montage-board";
import {
  AssembleMontageBoardV1,
  AssembleMontageTriggerV1,
} from "@/components/canvas/assemble-montage-board-v1";
import { usePipelineMode, useProjectMode } from "@/hooks/use-pipeline-mode";

type BoardProps = {
  open: boolean;
  projectId: number | null;
  montageBusy?: boolean;
  onClose: () => void;
};

type TriggerProps = {
  onClick: () => void;
  active?: boolean;
  busy?: boolean;
};

export function AssembleMontageBoard(props: BoardProps) {
  const mode = useProjectMode(props.projectId);
  if (mode === "v2") return <AssembleMontageBoardV2 {...props} />;
  return <AssembleMontageBoardV1 {...props} />;
}

export function AssembleMontageTrigger(props: TriggerProps) {
  // Триггер — глупая кнопка; версию берём из воркспейса (проект в нём же:
  // автоследование в page.tsx держит их синхронно). Доска при открытии
  // всё равно резолвится по режиму проекта.
  const [workspaceMode] = usePipelineMode();
  if (workspaceMode === "v2") return <AssembleMontageTriggerV2 {...props} />;
  return <AssembleMontageTriggerV1 {...props} />;
}
