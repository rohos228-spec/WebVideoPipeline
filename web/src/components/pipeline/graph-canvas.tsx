"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { EdgeKind, GraphEdge, GraphNode, NodeKindInfo, NodeState } from "@/lib/types";

/**
 * Холст конвейера.
 *
 * Отдельная поверхность, а не блок внутри ленты стадий, и это осознанно.
 * `Design.md` требует «редакторский лист»: одна колонка 760px, интерфейс
 * читается как документ. Схема узлов — прямая противоположность, «пульт с
 * приборами», и втиснутая в колонку она сломала бы и себя, и лист. Поэтому у
 * неё своя страница во всю ширину, а цвета берутся из тех же токенов, чтобы
 * это оставалось одной студией, а не двумя разными.
 *
 * Холст умеет две вещи сверх рисования: показывает **состояние узла** (идёт,
 * готово, выключен) и **вид связи** — «связь», «Ок», «Не ок». Оба берутся с
 * сервера и оба редактируются здесь: клик по подписи стрелки переключает
 * вид, а всё остальное — через инспектор справа.
 */

type NodeData = {
  label: string;
  kind: string;
  nodeType: string;
  disabled?: boolean;
  state?: NodeState;
  price?: string;
  modelId?: string;
  proposed?: "added" | "removed" | "changed";
};

const KIND_STYLE: Record<string, string> = {
  work: "border-border-strong bg-surface-raised",
  hitl: "border-warn bg-warn-muted",
  config: "border-border bg-surface-sunken",
};

const STATE_DOT: Partial<Record<NodeState, string>> = {
  running: "bg-accent animate-pulse",
  queued: "bg-accent",
  done: "bg-ok",
  failed: "bg-danger",
  waiting_hitl: "bg-warn",
};

const STATE_TEXT: Partial<Record<NodeState, string>> = {
  running: "идёт",
  queued: "в очереди",
  done: "готово",
  failed: "сбой",
  waiting_hitl: "ждёт проверки",
  skipped: "пропущен",
};

const PROPOSED_STYLE: Record<NonNullable<NodeData["proposed"]>, string> = {
  added: "ring-2 ring-ok border-dashed",
  removed: "ring-2 ring-danger opacity-50",
  changed: "ring-2 ring-warn",
};

/** Узел схемы. Подпись человеческая, код типа — мелко под ней. */
function StepNode({ data, selected }: NodeProps) {
  const d = data as NodeData;
  const dot = d.state ? STATE_DOT[d.state] : undefined;
  return (
    <div
      className={`min-w-[150px] rounded-sm border px-3 py-2 transition-colors ${
        KIND_STYLE[d.kind] ?? KIND_STYLE.work
      } ${selected ? "ring-1 ring-accent" : ""} ${d.disabled ? "opacity-40" : ""} ${
        d.proposed ? PROPOSED_STYLE[d.proposed] : ""
      }`}
    >
      <div className="flex items-center gap-2">
        {dot && <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />}
        <div className="text-[13px] leading-tight text-content">{d.label}</div>
      </div>
      <div className="mt-0.5 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[10px] text-content-faint">{d.nodeType}</span>
        {d.price && <span className="font-mono text-[10px] tabular-nums text-content-muted">{d.price}</span>}
      </div>
      {(d.disabled || (d.state && STATE_TEXT[d.state])) && (
        <div className="mt-1 text-[10px] text-content-faint">
          {d.disabled ? "выключен" : STATE_TEXT[d.state as NodeState]}
        </div>
      )}
      {d.modelId && (
        <div className="mt-0.5 truncate font-mono text-[10px] text-content-faint" title={d.modelId}>
          {d.modelId}
        </div>
      )}
    </div>
  );
}

const EDGE_LABEL: Record<EdgeKind, string> = {
  after: "",
  pass: "ок",
  fail: "не ок",
  gate: "шлагбаум",
};

const EDGE_NEXT: Record<EdgeKind, EdgeKind> = {
  after: "pass",
  pass: "fail",
  fail: "gate",
  gate: "after",
};

const EDGE_COLOR: Record<EdgeKind, string> = {
  after: "var(--color-border-strong)",
  pass: "var(--color-ok)",
  fail: "var(--color-danger)",
  gate: "var(--color-warn)",
};

/**
 * Стрелка с видом связи. Клик по подписи переключает вид по кругу —
 * связь → ок → не ок → шлагбаум. Четыре значения, и селект ради них был бы
 * тяжелее самой стрелки.
 */
function KindEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props;
  const kind = ((data?.kind as EdgeKind) ?? "after") as EdgeKind;
  const onToggle = data?.onToggle as ((id: string, next: EdgeKind) => void) | undefined;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const label = EDGE_LABEL[kind];
  return (
    <>
      <BaseEdge id={id} path={path} style={{ stroke: EDGE_COLOR[kind], strokeWidth: 1.5 }} />
      <EdgeLabelRenderer>
        <button
          type="button"
          onClick={() => onToggle?.(id, EDGE_NEXT[kind])}
          title="Вид связи: связь → ок → не ок → шлагбаум"
          className={`nodrag nopan pointer-events-auto absolute rounded-full border px-1.5 py-0.5 text-[10px] transition-colors ${
            label
              ? "border-border-strong bg-surface-raised text-content-muted"
              : "border-transparent bg-transparent text-content-faint opacity-0 hover:opacity-100"
          }`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {label || "связь"}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

const NODE_TYPES = { step: StepNode };
const EDGE_TYPES = { kind: KindEdge };

export function GraphCanvas({
  nodes: initialNodes,
  edges: initialEdges,
  catalog,
  states,
  prices,
  proposed,
  onChange,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  catalog: NodeKindInfo[];
  /** Состояние узлов из прогона — только в режиме проекта. */
  states?: Record<string, NodeState>;
  /** Цена по коду шага — подпись на узле. */
  prices?: Record<string, string>;
  /** Узлы предложения агента: id → как помечать. */
  proposed?: Record<string, NodeData["proposed"]>;
  onChange: (nodes: GraphNode[], edges: GraphEdge[]) => void;
  onSelect: (nodeId: string | null) => void;
}) {
  const byType = useMemo(() => new Map(catalog.map((n) => [n.type, n])), [catalog]);

  // Полные данные узлов и связей держим рядом, чтобы при любом изменении на
  // холсте вернуть наверх ровно форму бэкенда — с `data`, которых React Flow
  // не знает, — а не только то, что нарисовано.
  const sourceRef = useRef({ nodes: initialNodes, edges: initialEdges });
  sourceRef.current = { nodes: initialNodes, edges: initialEdges };

  const toFlow = useCallback(
    (n: GraphNode): Node => {
      const info = byType.get(n.type);
      const step = info?.step_code ?? undefined;
      return {
        id: n.id,
        type: "step",
        position: n.position ?? { x: 0, y: 0 },
        data: {
          label: (n.data?.label as string) || info?.label || n.type,
          kind: info?.kind ?? "work",
          nodeType: n.type,
          disabled: n.data?.disabled === true,
          state: states?.[n.id],
          price: step && prices?.[step] ? prices[step] : undefined,
          modelId: (n.data?.modelId as string) || undefined,
          proposed: proposed?.[n.id],
        } satisfies NodeData,
      };
    },
    [byType, states, prices, proposed],
  );

  const pushRef = useRef<(ns: Node[], es: Edge[]) => void>(() => {});

  const toFlowEdge = useCallback(
    (e: GraphEdge): Edge => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "kind",
      data: {
        kind: ((e.data?.kind as EdgeKind) ?? "after") as EdgeKind,
        onToggle: (id: string, next: EdgeKind) => {
          setFlowEdges((cur) => {
            const nextEdges = cur.map((x) => (x.id === id ? { ...x, data: { ...x.data, kind: next } } : x));
            pushRef.current(flowNodesRef.current, nextEdges);
            return nextEdges;
          });
        },
      },
    }),
    // setFlowEdges определяется ниже; ссылка стабильна на всю жизнь хука.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(initialNodes.map(toFlow));
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(initialEdges.map(toFlowEdge));
  const flowNodesRef = useRef(flowNodes);
  flowNodesRef.current = flowNodes;

  // Состояния и цены приходят с сервера позже узлов и меняются по ходу
  // генерации — перекрашиваем узлы, не трогая их позиции.
  useEffect(() => {
    setFlowNodes((cur) =>
      cur.map((n) => {
        const src = sourceRef.current.nodes.find((o) => o.id === n.id);
        if (!src) return n;
        return { ...n, data: toFlow({ ...src, position: n.position }).data };
      }),
    );
  }, [toFlow, setFlowNodes]);

  // Наверх уходит форма бэкенда, а не форма React Flow: сервер хранит граф в
  // своём виде, и подмешивать сюда служебные поля библиотеки значило бы
  // записать их в базу.
  const push = useCallback(
    (ns: Node[], es: Edge[]) => {
      const { nodes: srcNodes, edges: srcEdges } = sourceRef.current;
      onChange(
        ns.map((n) => {
          const src = srcNodes.find((o) => o.id === n.id);
          const d = n.data as NodeData;
          return {
            id: n.id,
            type: d.nodeType,
            position: n.position,
            data: { ...(src?.data ?? {}), disabled: d.disabled === true },
          };
        }),
        es.map((e) => {
          const src = srcEdges.find((o) => o.id === e.id);
          const kind = ((e.data?.kind as EdgeKind) ?? "after") as EdgeKind;
          return {
            id: e.id,
            source: e.source,
            target: e.target,
            data: { ...(src?.data ?? {}), kind },
          };
        }),
      );
    },
    [onChange],
  );
  pushRef.current = push;

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
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
          const next = addEdge(
            toFlowEdge({ id: `e-${c.source}-${c.target}`, source: c.source, target: c.target, data: { kind: "after" } }),
            cur,
          );
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
