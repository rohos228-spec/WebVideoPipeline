"use client";

import { useCallback, useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphEdge, GraphNode, NodeKindInfo } from "@/lib/types";

/**
 * Холст конвейера.
 *
 * Отдельная поверхность, а не блок внутри ленты стадий, и это осознанно.
 * `Design.md` требует «редакторский лист»: одна колонка 760px, интерфейс
 * читается как документ. Схема узлов — прямая противоположность, «пульт с
 * приборами», и втиснутая в колонку она сломала бы и себя, и лист. Поэтому у
 * неё своя страница во всю ширину, а цвета берутся из тех же токенов, чтобы
 * это оставалось одной студией, а не двумя разными.
 */

type NodeData = {
  label: string;
  kind: string;
  nodeType: string;
  disabled?: boolean;
};

const KIND_STYLE: Record<string, string> = {
  work: "border-border-strong bg-surface-raised",
  hitl: "border-warn bg-warn-muted",
  config: "border-border bg-surface-sunken",
};

/** Узел схемы. Подпись человеческая, код типа — мелко под ней. */
function StepNode({ data, selected }: NodeProps) {
  const d = data as NodeData;
  return (
    <div
      className={`min-w-[150px] rounded-sm border px-3 py-2 transition-colors ${
        KIND_STYLE[d.kind] ?? KIND_STYLE.work
      } ${selected ? "ring-1 ring-accent" : ""} ${d.disabled ? "opacity-40" : ""}`}
    >
      <div className="text-[13px] leading-tight text-content">{d.label}</div>
      <div className="mt-0.5 font-mono text-[10px] text-content-faint">{d.nodeType}</div>
      {d.disabled && <div className="mt-1 text-[10px] text-content-faint">выключен</div>}
    </div>
  );
}

const NODE_TYPES = { step: StepNode };

export function GraphCanvas({
  nodes: initialNodes,
  edges: initialEdges,
  catalog,
  onChange,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  catalog: NodeKindInfo[];
  onChange: (nodes: GraphNode[], edges: GraphEdge[]) => void;
  onSelect: (nodeId: string | null) => void;
}) {
  const byType = useMemo(() => new Map(catalog.map((n) => [n.type, n])), [catalog]);

  const toFlow = useCallback(
    (n: GraphNode): Node => {
      const info = byType.get(n.type);
      return {
        id: n.id,
        type: "step",
        position: n.position ?? { x: 0, y: 0 },
        data: {
          label: (n.data?.label as string) || info?.label || n.type,
          kind: info?.kind ?? "work",
          nodeType: n.type,
          disabled: n.data?.disabled === true,
        },
      };
    },
    [byType],
  );

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(initialNodes.map(toFlow));
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(
    initialEdges.map((e) => ({ id: e.id, source: e.source, target: e.target }) as Edge),
  );

  // Наверх уходит форма бэкенда, а не форма React Flow: сервер хранит граф в
  // своём виде, и подмешивать сюда служебные поля библиотеки значило бы
  // записать их в базу.
  const push = useCallback(
    (ns: Node[], es: Edge[]) => {
      onChange(
        ns.map((n) => {
          const src = initialNodes.find((o) => o.id === n.id);
          return {
            id: n.id,
            type: (n.data as NodeData).nodeType,
            position: n.position,
            data: { ...(src?.data ?? {}), disabled: (n.data as NodeData).disabled === true },
          };
        }),
        es.map((e) => ({ id: e.id, source: e.source, target: e.target })),
      );
    },
    [initialNodes, onChange],
  );

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      onNodesChange={(c) => {
        onNodesChange(c);
        setFlowNodes((cur) => {
          push(cur, flowEdges);
          return cur;
        });
      }}
      onEdgesChange={(c) => {
        onEdgesChange(c);
        setFlowEdges((cur) => {
          push(flowNodes, cur);
          return cur;
        });
      }}
      onConnect={(c: Connection) =>
        setFlowEdges((cur) => {
          const next = addEdge({ ...c, id: `e-${c.source}-${c.target}` }, cur);
          push(flowNodes, next);
          return next;
        })
      }
      onSelectionChange={({ nodes }) => onSelect(nodes[0]?.id ?? null)}
      fitView
      proOptions={{ hideAttribution: true }}
      className="bg-surface"
    >
      <Background gap={18} size={1} color="var(--color-border)" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
