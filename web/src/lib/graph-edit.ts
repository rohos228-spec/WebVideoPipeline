/**
 * Правки графа без холста: копирование, дублирование, раскладка, история.
 *
 * Всё здесь — чистые функции над формой бэкенда (`GraphNode`/`GraphEdge`),
 * а не над узлами React Flow. Холст рисует, страница решает; логика правки
 * лежит между ними и не зависит ни от того, ни от другого — иначе её нельзя
 * было бы ни проверить, ни вызвать с клавиатуры.
 */

import type { GraphEdge, GraphNode } from "./stage-types";

export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

/** Те же шаги, что у штатной раскладки на сервере (`project_graph.py`). */
export const LAYOUT = { stepX: 290, stepY: 130, baseX: 80, baseY: 200 } as const;

let seq = 0;

/** Уникальный id узла: тип + время + счётчик, чтобы два подряд не совпали. */
export function newNodeId(type: string, taken: Set<string>): string {
  for (;;) {
    seq += 1;
    const id = `n_${type}_${Date.now().toString(36)}${seq.toString(36)}`;
    if (!taken.has(id)) return id;
  }
}

export function edgeId(source: string, target: string): string {
  return `e_${source}__${target}`;
}

/**
 * Копия выделенных узлов вместе со связями между ними.
 *
 * Со стороны бэкенда узел — это тип и `data`; результаты работы к узлу не
 * привязаны, поэтому копия чистая. Конфиг «Работы с GPT» лежит в `meta`
 * по ключу узла, и при вставке в другой ролик его пришлось бы переносить
 * отдельно — здесь он не копируется, и это честнее, чем половина настроек.
 */
export function cloneSelection(
  graph: Graph,
  ids: string[],
  offset = { x: 56, y: 56 },
  /** Занятые id сверх тех, что в `graph` — при вставке из буфера в другой граф. */
  extraTaken: Iterable<string> = [],
): Graph {
  const wanted = new Set(ids);
  const taken = new Set([...graph.nodes.map((n) => n.id), ...extraTaken]);
  const map = new Map<string, string>();
  const nodes: GraphNode[] = [];
  for (const n of graph.nodes) {
    if (!wanted.has(n.id)) continue;
    const id = newNodeId(n.type, taken);
    taken.add(id);
    map.set(n.id, id);
    nodes.push({
      id,
      type: n.type,
      position: { x: (n.position?.x ?? 0) + offset.x, y: (n.position?.y ?? 0) + offset.y },
      data: { ...(n.data ?? {}) },
    });
  }
  const edges: GraphEdge[] = [];
  for (const e of graph.edges) {
    const s = map.get(e.source);
    const t = map.get(e.target);
    if (!s || !t) continue;
    edges.push({ id: edgeId(s, t), source: s, target: t, data: { ...(e.data ?? {}) } });
  }
  return { nodes, edges };
}

/** Буфер живёт в `sessionStorage`, чтобы вставка работала между роликами. */
const CLIPBOARD_KEY = "vp.graph-clipboard";

export function copyToClipboard(fragment: Graph): void {
  try {
    window.sessionStorage.setItem(CLIPBOARD_KEY, JSON.stringify(fragment));
  } catch {
    /* приватное окно — буфер просто не переживёт вкладку */
  }
}

export function readClipboard(): Graph | null {
  try {
    const raw = window.sessionStorage.getItem(CLIPBOARD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Graph;
    if (!Array.isArray(parsed.nodes)) return null;
    return { nodes: parsed.nodes, edges: Array.isArray(parsed.edges) ? parsed.edges : [] };
  } catch {
    return null;
  }
}

/**
 * Вставка фрагмента: новые id, смещение, связи только внутри фрагмента.
 * Клонируется сам фрагмент, а не граф с подмешанным фрагментом: в буфере
 * лежат копии узлов с теми же id, что и в графе, и слияние дало бы дубли.
 */
export function pasteFragment(graph: Graph, fragment: Graph): { graph: Graph; ids: string[] } {
  const clone = cloneSelection(
    fragment,
    fragment.nodes.map((n) => n.id),
    { x: 56, y: 56 },
    graph.nodes.map((n) => n.id),
  );
  return {
    graph: { nodes: [...graph.nodes, ...clone.nodes], edges: [...graph.edges, ...clone.edges] },
    ids: clone.nodes.map((n) => n.id),
  };
}

export function removeNodes(graph: Graph, ids: string[]): Graph {
  const gone = new Set(ids);
  return {
    nodes: graph.nodes.filter((n) => !gone.has(n.id)),
    edges: graph.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target)),
  };
}

/** Снять все связи узла, сам узел оставить. */
export function detachNodes(graph: Graph, ids: string[]): Graph {
  const set = new Set(ids);
  return { ...graph, edges: graph.edges.filter((e) => !set.has(e.source) && !set.has(e.target)) };
}

/**
 * Убрать узел из цепочки, соединив соседей: A → X → B становится A → B.
 * То, что делает «удалить» у планировщика (`remove_node bridge=true`).
 */
export function removeAndBridge(graph: Graph, id: string): Graph {
  const inbound = graph.edges.filter((e) => e.target === id && (e.data?.kind ?? "after") === "after");
  const outbound = graph.edges.filter((e) => e.source === id && (e.data?.kind ?? "after") === "after");
  const base = removeNodes(graph, [id]);
  const have = new Set(base.edges.map((e) => `${e.source}→${e.target}`));
  const bridged: GraphEdge[] = [];
  for (const a of inbound) {
    for (const b of outbound) {
      if (a.source === b.target || have.has(`${a.source}→${b.target}`)) continue;
      have.add(`${a.source}→${b.target}`);
      bridged.push({ id: edgeId(a.source, b.target), source: a.source, target: b.target, data: { kind: "after" } });
    }
  }
  return { nodes: base.nodes, edges: [...base.edges, ...bridged] };
}

/**
 * Раскладка по слоям: глубина по связям «после» — колонка, порядок в
 * колонке — по прежнему `y`, чтобы ветки не менялись местами. Та же
 * формула, что у сервера для штатной схемы, поэтому «разложить» после
 * «штатной схемы» ничего не двигает.
 */
export function autoLayout(graph: Graph): Graph {
  const ids = graph.nodes.map((n) => n.id);
  const preds = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of graph.edges) {
    if ((e.data?.kind ?? "after") === "fail") continue;
    preds.get(e.target)?.push(e.source);
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const dfs = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // цикл — не наша забота, его отловит проверка
    visiting.add(id);
    const d = Math.max(-1, ...(preds.get(id) ?? []).map(dfs)) + 1;
    visiting.delete(id);
    depth.set(id, d);
    return d;
  };
  ids.forEach(dfs);

  const columns = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const d = depth.get(n.id) ?? 0;
    if (!columns.has(d)) columns.set(d, []);
    columns.get(d)!.push(n);
  }
  const placed = new Map<string, { x: number; y: number }>();
  for (const [d, col] of columns) {
    col.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
    const top = LAYOUT.baseY - ((col.length - 1) * LAYOUT.stepY) / 2;
    col.forEach((n, i) => placed.set(n.id, { x: LAYOUT.baseX + d * LAYOUT.stepX, y: top + i * LAYOUT.stepY }));
  }
  return { ...graph, nodes: graph.nodes.map((n) => ({ ...n, position: placed.get(n.id) ?? n.position })) };
}

/** Где поставить новый узел: правее самого правого, на общей линии. */
export function nextFreePosition(graph: Graph): { x: number; y: number } {
  if (graph.nodes.length === 0) return { x: LAYOUT.baseX, y: LAYOUT.baseY };
  const x = Math.max(...graph.nodes.map((n) => n.position?.x ?? 0)) + LAYOUT.stepX;
  return { x, y: LAYOUT.baseY };
}

/**
 * История правок для отмены. Хранит снимки целиком: граф — сотня узлов,
 * снимок дешевле, чем обратные операции, и не имеет краевых случаев.
 */
export class History<T> {
  private past: T[] = [];
  private future: T[] = [];

  constructor(private readonly limit = 100) {}

  push(state: T): void {
    this.past.push(state);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
  }

  undo(current: T): T | null {
    const prev = this.past.pop();
    if (prev === undefined) return null;
    this.future.push(current);
    return prev;
  }

  redo(current: T): T | null {
    const next = this.future.pop();
    if (next === undefined) return null;
    this.past.push(current);
    return next;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  clear(): void {
    this.past = [];
    this.future = [];
  }
}
