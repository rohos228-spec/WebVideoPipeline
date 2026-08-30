"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  SelectionMode,
  ViewportPortal,
  addEdge,
  getBezierPath,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
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
 * Что холст умеет сверх рисования:
 * - **связи** тянутся от правой ручки узла к левой ручке другого; клик по
 *   подписи стрелки переключает вид по кругу, правый клик — выбирает явно;
 * - **выделение**: клик, Ctrl+клик, рамка по Shift+протяжке; всё выделенное
 *   двигается вместе и уходит наверх списком id;
 * - **меню правой кнопки** на узле, связи и на пустом месте — пункты даёт
 *   страница, холст лишь показывает их там, где кликнули;
 * - **рамки групп**: узлы с одним `data.groupId` обведены пунктиром с
 *   подписью — так видно веер агентов сцен как одно целое;
 * - **состояние узла** и **цена** приходят с сервера и перекрашивают карточку,
 *   не трогая позиций.
 *
 * Наверх всегда уходит форма бэкенда (`GraphNode`/`GraphEdge`), а не форма
 * React Flow: сервер хранит граф в своём виде, и подмешивать сюда служебные
 * поля библиотеки значило бы записать их в базу.
 */

export type NodeMenuItem = {
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** Разделитель перед пунктом. */
  gap?: boolean;
};

type NodeData = {
  label: string;
  kind: string;
  nodeType: string;
  disabled?: boolean;
  state?: NodeState;
  price?: string;
  modelId?: string;
  media?: string;
  role?: string;
  groupId?: string;
  proposed?: "added" | "removed" | "changed";
  hitl?: boolean;
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
  waiting_hitl: "bg-warn animate-pulse",
};

const STATE_TEXT: Partial<Record<NodeState, string>> = {
  running: "идёт",
  queued: "в очереди",
  done: "готово",
  failed: "сбой",
  waiting_hitl: "ждёт проверки",
  skipped: "пропущен",
};

/** Цвет узла на миникарте — тем же словарём, что и точка на карточке. */
const STATE_MINIMAP: Partial<Record<NodeState, string>> = {
  running: "var(--color-accent)",
  queued: "var(--color-accent)",
  done: "var(--color-ok)",
  failed: "var(--color-danger)",
  waiting_hitl: "var(--color-warn)",
};

const PROPOSED_STYLE: Record<NonNullable<NodeData["proposed"]>, string> = {
  added: "ring-2 ring-ok border-dashed",
  removed: "ring-2 ring-danger opacity-50",
  changed: "ring-2 ring-warn",
};

const ROLE_TEXT: Record<string, string> = {
  assist: "помощник",
  review: "ок / не ок",
  transform: "переделывает",
  extract: "достаёт данные",
  compare: "сравнивает",
  gate: "шлагбаум",
};

/**
 * Узел схемы. Подпись человеческая, код типа — мелко под ней; ниже — то, что
 * выбрано на узле: модель, качество, роль. Ручки по бокам: слева вход,
 * справа выход — стрелка тянется от выхода к входу.
 */
function StepNode({ data, selected }: NodeProps) {
  const d = data as NodeData;
  const dot = d.state ? STATE_DOT[d.state] : undefined;
  const hint = [d.modelId, d.media].filter(Boolean).join(" · ");
  return (
    <div
      className={`group/node relative min-w-[160px] max-w-[220px] rounded-sm border px-3 py-2 transition-colors ${
        KIND_STYLE[d.kind] ?? KIND_STYLE.work
      } ${selected ? "ring-1 ring-accent" : ""} ${d.disabled ? "opacity-40" : ""} ${
        d.proposed ? PROPOSED_STYLE[d.proposed] : ""
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="!h-3 !w-3 !rounded-full !border !border-border-strong !bg-surface"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="!h-3 !w-3 !rounded-full !border !border-accent !bg-surface"
      />
      <div className="flex items-center gap-2">
        {dot && <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />}
        {d.groupId && (
          <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-sm bg-info" title={`группа: ${d.groupId}`} />
        )}
        <div className="truncate text-[13px] leading-tight text-content" title={d.label}>
          {d.label}
        </div>
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
      {d.role && <div className="mt-0.5 text-[10px] text-content-muted">{ROLE_TEXT[d.role] ?? d.role}</div>}
      {hint && (
        <div className="mt-0.5 truncate font-mono text-[10px] text-content-faint" title={hint}>
          {hint}
        </div>
      )}
    </div>
  );
}

export const EDGE_LABEL: Record<EdgeKind, string> = {
  after: "связь",
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
 * связь → ок → не ок → шлагбаум; правый клик открывает список, если
 * нужно выбрать сразу. Число файлов на стрелке (у «Работы с GPT») —
 * рядом с подписью.
 */
function KindEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } = props;
  const kind = ((data?.kind as EdgeKind) ?? "after") as EdgeKind;
  const onToggle = data?.onToggle as ((id: string, next: EdgeKind) => void) | undefined;
  const fileCount = typeof data?.fileCount === "number" && data.fileCount > 0 ? (data.fileCount as number) : 0;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const plain = kind === "after";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{ stroke: EDGE_COLOR[kind], strokeWidth: selected ? 2.5 : 1.5, strokeDasharray: plain ? "6 4" : undefined }}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          onClick={() => onToggle?.(id, EDGE_NEXT[kind])}
          title="Вид связи: клик — по кругу, правый клик — выбрать"
          className={`nodrag nopan pointer-events-auto absolute rounded-full border px-1.5 py-0.5 text-[10px] transition-opacity ${
            !plain || fileCount || selected
              ? "border-border-strong bg-surface-raised text-content-muted"
              : "border-transparent bg-transparent text-content-faint opacity-0 hover:opacity-100"
          }`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {EDGE_LABEL[kind]}
          {fileCount ? ` · ${fileCount}` : ""}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

const NODE_TYPES = { step: StepNode };
const EDGE_TYPES = { kind: KindEdge };

type Menu =
  | { kind: "node"; id: string; x: number; y: number }
  | { kind: "edge"; id: string; x: number; y: number }
  | { kind: "pane"; x: number; y: number };

const FIT_VIEW = { padding: 0.1, minZoom: 0.55 };
const NODE_W = 190;
const NODE_H = 64;

export function GraphCanvas({
  nodes: initialNodes,
  edges: initialEdges,
  catalog,
  states,
  prices,
  proposed,
  selectRequest,
  onChange,
  onSelect,
  nodeMenu,
  paneMenu,
  onOpenNode,
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
  /** Просьба выделить узлы (после вставки, добавления, Ctrl+A). `seq` растёт с каждой просьбой. */
  selectRequest?: { ids: string[]; seq: number };
  /**
   * Любая правка на холсте. `commit=false` — узел ещё тянут, снимок в
   * историю класть рано; `true` — правка закончена.
   */
  onChange: (nodes: GraphNode[], edges: GraphEdge[], commit: boolean) => void;
  onSelect: (ids: string[]) => void;
  /** Пункты меню правой кнопки на узле; их даёт страница — она знает режим. */
  nodeMenu?: (id: string) => NodeMenuItem[];
  /** Пункты меню на пустом месте: вставить, разложить, выделить всё. */
  paneMenu?: () => NodeMenuItem[];
  /** Двойной клик по узлу — открыть его в инспекторе целиком. */
  onOpenNode?: (id: string) => void;
}) {
  const byType = useMemo(() => new Map(catalog.map((n) => [n.type, n])), [catalog]);
  const [menu, setMenu] = useState<Menu | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Полные данные узлов и связей держим рядом, чтобы при любом изменении на
  // холсте вернуть наверх ровно форму бэкенда — с `data`, которых React Flow
  // не знает, — а не только то, что нарисовано.
  const sourceRef = useRef({ nodes: initialNodes, edges: initialEdges });
  sourceRef.current = { nodes: initialNodes, edges: initialEdges };

  const toFlow = useCallback(
    (n: GraphNode, prev?: Node): Node => {
      const info = byType.get(n.type);
      const step = info?.step_code ?? undefined;
      const d = n.data ?? {};
      const media = [d.imageResolution, d.imageQuality, typeof d.aspectRatio === "string" ? d.aspectRatio : null]
        .filter((v): v is string => typeof v === "string" && v.length > 0)
        .join(" ");
      return {
        id: n.id,
        type: "step",
        position: n.position ?? { x: 0, y: 0 },
        selected: prev?.selected,
        dragging: prev?.dragging,
        data: {
          label: (d.label as string) || info?.label || n.type,
          kind: info?.kind ?? "work",
          nodeType: n.type,
          disabled: d.disabled === true,
          state: states?.[n.id],
          price: step && prices?.[step] ? prices[step] : undefined,
          modelId: (d.modelId as string) || undefined,
          media: media || undefined,
          role: n.type === "excel_gpt" ? ((d.role as string) || undefined) : undefined,
          groupId: (d.groupId as string) || undefined,
          proposed: proposed?.[n.id],
        } satisfies NodeData,
      };
    },
    [byType, states, prices, proposed],
  );

  const pushRef = useRef<(ns: Node[], es: Edge[], commit: boolean) => void>(() => {});

  const toFlowEdge = useCallback(
    (e: GraphEdge, prev?: Edge): Edge => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? "out",
      targetHandle: e.targetHandle ?? "in",
      type: "kind",
      selected: prev?.selected,
      data: {
        kind: ((e.data?.kind as EdgeKind) ?? "after") as EdgeKind,
        fileCount: e.data?.fileCount,
        onToggle: (id: string, next: EdgeKind) => setEdgeKind(id, next),
      },
    }),
    // setEdgeKind определяется ниже; ссылка стабильна на всю жизнь хука.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(initialNodes.map((n) => toFlow(n)));
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(initialEdges.map((e) => toFlowEdge(e)));
  const flowNodesRef = useRef(flowNodes);
  flowNodesRef.current = flowNodes;
  const flowEdgesRef = useRef(flowEdges);
  flowEdgesRef.current = flowEdges;

  // Источник правды — страница. Всё, что она поменяла мимо холста (вставка,
  // отмена, раскладка, предложение агента), должно появиться здесь: узлы
  // добавляются и исчезают, позиции берутся из источника, а выделение и
  // перетаскивание — своё, холстовое, чтобы не сбрасываться на каждом тике.
  const toFlowRef = useRef(toFlow);
  toFlowRef.current = toFlow;

  useEffect(() => {
    setFlowNodes((cur) => {
      const prev = new Map(cur.map((n) => [n.id, n]));
      return initialNodes.map((src) => {
        const p = prev.get(src.id);
        const next = toFlowRef.current(src, p);
        return p?.dragging ? { ...next, position: p.position } : next;
      });
    });
  }, [initialNodes, setFlowNodes]);

  useEffect(() => {
    setFlowEdges((cur) => {
      const prev = new Map(cur.map((e) => [e.id, e]));
      return initialEdges.map((src) => toFlowEdge(src, prev.get(src.id)));
    });
  }, [initialEdges, toFlowEdge, setFlowEdges]);

  // Состояния и цены приходят с сервера позже узлов и меняются по ходу
  // генерации — перекрашиваем узлы, не трогая ни позиций, ни выделения.
  useEffect(() => {
    setFlowNodes((cur) =>
      cur.map((n) => {
        const src = sourceRef.current.nodes.find((o) => o.id === n.id);
        return src ? { ...n, data: toFlow(src, n).data } : n;
      }),
    );
  }, [toFlow, setFlowNodes]);

  // Программное выделение — одноразовый запрос, а не состояние. Держать
  // «что выделено» и на странице, и в xyflow значило бы два источника правды
  // с лагом в один коммит: страница ставит флаг, xyflow ещё отвечает старым
  // списком, страница снимает, xyflow отвечает новым — и так по кругу.
  // Поэтому выделением владеет холст; страница лишь просит «выдели вот эти»
  // после вставки или добавления и дальше слушает, что холст ей сообщит.
  const seqRef = useRef(0);
  useEffect(() => {
    if (!selectRequest || selectRequest.seq === seqRef.current) return;
    seqRef.current = selectRequest.seq;
    const want = new Set(selectRequest.ids);
    setFlowNodes((cur) => cur.map((n) => (Boolean(n.selected) === want.has(n.id) ? n : { ...n, selected: want.has(n.id) })));
  }, [selectRequest, setFlowNodes]);

  const push = useCallback(
    (ns: Node[], es: Edge[], commit: boolean) => {
      const { nodes: srcNodes, edges: srcEdges } = sourceRef.current;
      const srcById = new Map(srcNodes.map((n) => [n.id, n]));
      const edgeById = new Map(srcEdges.map((e) => [e.id, e]));
      onChange(
        ns.map((n) => {
          const src = srcById.get(n.id);
          const d = n.data as NodeData;
          return {
            id: n.id,
            type: d.nodeType,
            position: n.position,
            data: { ...(src?.data ?? {}), disabled: d.disabled === true },
          };
        }),
        es.map((e) => {
          const src = edgeById.get(e.id);
          const kind = ((e.data?.kind as EdgeKind) ?? "after") as EdgeKind;
          return {
            id: e.id,
            source: e.source,
            target: e.target,
            sourceHandle: e.sourceHandle ?? null,
            targetHandle: e.targetHandle ?? null,
            data: { ...(src?.data ?? {}), kind },
          };
        }),
        commit,
      );
    },
    [onChange],
  );
  pushRef.current = push;

  const setEdgeKind = useCallback(
    (id: string, next: EdgeKind) => {
      setFlowEdges((cur) => {
        const nextEdges = cur.map((x) => (x.id === id ? { ...x, data: { ...x.data, kind: next } } : x));
        pushRef.current(flowNodesRef.current, nextEdges, true);
        return nextEdges;
      });
    },
    [setFlowEdges],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChange(changes);
      // Выделение и размеры — дело холста, наверх их не носим: форма бэкенда
      // от них не меняется, а история отмены засорилась бы кликами.
      const meaningful = changes.filter((c) => c.type !== "select" && c.type !== "dimensions");
      if (meaningful.length === 0) return;
      const dragging = meaningful.every((c) => c.type === "position" && c.dragging);
      setFlowNodes((cur) => {
        pushRef.current(cur, flowEdgesRef.current, !dragging);
        return cur;
      });
    },
    [onNodesChange, setFlowNodes],
  );

  const menuPos = (e: ReactMouseEvent | MouseEvent) => {
    const box = wrapRef.current?.getBoundingClientRect();
    return { x: e.clientX - (box?.left ?? 0), y: e.clientY - (box?.top ?? 0) };
  };

  const closeMenu = () => setMenu(null);

  // Рамки групп: по одному прямоугольнику на `groupId`, считаются из позиций
  // узлов и рисуются в координатах холста — двигаются вместе с ним.
  const frames = useMemo(() => {
    const byGroup = new Map<string, { x1: number; y1: number; x2: number; y2: number; title: string; n: number }>();
    for (const n of flowNodes) {
      const g = (n.data as NodeData).groupId;
      if (!g) continue;
      const w = n.measured?.width ?? NODE_W;
      const h = n.measured?.height ?? NODE_H;
      const src = sourceRef.current.nodes.find((s) => s.id === n.id);
      const title = (src?.data?.groupTitle as string) || g;
      const cur = byGroup.get(g);
      const box = { x1: n.position.x, y1: n.position.y, x2: n.position.x + w, y2: n.position.y + h };
      if (!cur) byGroup.set(g, { ...box, title, n: 1 });
      else
        byGroup.set(g, {
          x1: Math.min(cur.x1, box.x1),
          y1: Math.min(cur.y1, box.y1),
          x2: Math.max(cur.x2, box.x2),
          y2: Math.max(cur.y2, box.y2),
          title: cur.title,
          n: cur.n + 1,
        });
    }
    return [...byGroup.entries()].filter(([, f]) => f.n > 1);
  }, [flowNodes]);

  const edgeMenuItems = (id: string): NodeMenuItem[] => {
    const edge = flowEdges.find((e) => e.id === id);
    const current = (edge?.data?.kind as EdgeKind) ?? "after";
    const kinds: EdgeKind[] = ["after", "pass", "fail", "gate"];
    return [
      ...kinds.map((k) => ({
        label: `${k === current ? "• " : ""}${EDGE_LABEL[k]}`,
        onClick: () => setEdgeKind(id, k),
      })),
      {
        label: "Удалить связь",
        danger: true,
        gap: true,
        onClick: () =>
          setFlowEdges((cur) => {
            const next = cur.filter((e) => e.id !== id);
            pushRef.current(flowNodesRef.current, next, true);
            return next;
          }),
      },
    ];
  };

  const items: NodeMenuItem[] =
    menu?.kind === "node"
      ? (nodeMenu?.(menu.id) ?? [])
      : menu?.kind === "edge"
        ? edgeMenuItems(menu.id)
        : menu?.kind === "pane"
          ? (paneMenu?.() ?? [])
          : [];

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={handleNodesChange}
        onEdgesChange={(c) => {
          onEdgesChange(c);
          if (c.every((x) => x.type === "select")) return;
          setFlowEdges((cur) => {
            pushRef.current(flowNodesRef.current, cur, true);
            return cur;
          });
        }}
        onConnect={(c: Connection) =>
          setFlowEdges((cur) => {
            if (cur.some((e) => e.source === c.source && e.target === c.target)) return cur;
            const next = addEdge(
              toFlowEdge({
                id: `e_${c.source}__${c.target}`,
                source: c.source,
                target: c.target,
                sourceHandle: c.sourceHandle,
                targetHandle: c.targetHandle,
                data: { kind: "after" },
              }),
              cur,
            );
            pushRef.current(flowNodesRef.current, next, true);
            return next;
          })
        }
        onSelectionChange={({ nodes }) => onSelect(nodes.map((n) => n.id))}
        onNodeContextMenu={(e, n) => {
          e.preventDefault();
          setMenu({ kind: "node", id: n.id, ...menuPos(e) });
        }}
        onEdgeContextMenu={(e, edge) => {
          e.preventDefault();
          setMenu({ kind: "edge", id: edge.id, ...menuPos(e) });
        }}
        onPaneContextMenu={(e) => {
          e.preventDefault();
          setMenu({ kind: "pane", ...menuPos(e) });
        }}
        onPaneClick={closeMenu}
        onNodeClick={closeMenu}
        onMoveStart={closeMenu}
        onNodeDoubleClick={(_, n) => onOpenNode?.(n.id)}
        selectionMode={SelectionMode.Partial}
        selectionKeyCode="Shift"
        multiSelectionKeyCode={["Meta", "Control"]}
        deleteKeyCode={["Delete", "Backspace"]}
        minZoom={0.15}
        maxZoom={1.6}
        fitView
        // Длинная цепочка из двадцати узлов в fitView превращается в чёрточки;
        // ниже этого масштаба подпись не прочесть — лучше показать середину
        // и оставить миникарту для навигации.
        fitViewOptions={FIT_VIEW}
        proOptions={{ hideAttribution: true }}
        className="bg-surface"
      >
        <Background gap={18} size={1} color="var(--color-border)" />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => {
            const d = n.data as NodeData;
            if (d.disabled) return "var(--color-border)";
            return (d.state && STATE_MINIMAP[d.state]) || "var(--color-border-strong)";
          }}
          maskColor="var(--color-surface-sunken)"
          className="!border !border-border !bg-surface-raised"
        />
        <ViewportPortal>
          {frames.map(([g, f]) => (
            <div
              key={g}
              className="pointer-events-none absolute rounded-md border border-dashed border-info"
              style={{
                left: f.x1 - 14,
                top: f.y1 - 26,
                width: f.x2 - f.x1 + 28,
                height: f.y2 - f.y1 + 40,
                zIndex: -1,
              }}
            >
              <span className="absolute left-2 top-1 text-[10px] uppercase tracking-wide text-info">{f.title}</span>
            </div>
          ))}
        </ViewportPortal>
      </ReactFlow>

      {menu && items.length > 0 && (
        <ContextMenu x={menu.x} y={menu.y} items={items} onClose={closeMenu} />
      )}
    </div>
  );
}

/** Меню правой кнопки: список действий там, где кликнули. Esc и клик мимо закрывают. */
function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: NodeMenuItem[]; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("[data-graph-menu]")) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  return (
    <div
      data-graph-menu
      className="absolute z-20 min-w-[200px] rounded-md border border-border-strong bg-surface-overlay py-1 text-[13px]"
      style={{ left: x, top: y }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((it, i) => (
        <div key={i}>
          {it.gap && <div className="my-1 border-t border-border" />}
          <button
            type="button"
            disabled={it.disabled}
            onClick={() => {
              it.onClick();
              onClose();
            }}
            className={`block w-full px-3 py-1.5 text-left transition-colors hover:bg-surface-sunken disabled:opacity-40 ${
              it.danger ? "text-danger" : "text-content"
            }`}
          >
            {it.label}
          </button>
        </div>
      ))}
    </div>
  );
}
