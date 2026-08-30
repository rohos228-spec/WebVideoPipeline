"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  History,
  autoLayout,
  cloneSelection,
  copyToClipboard,
  detachNodes,
  newNodeId,
  nextFreePosition,
  pasteFragment,
  readClipboard,
  removeAndBridge,
  removeNodes,
  type Graph,
} from "@/lib/graph-edit";
import type { GraphEdge, GraphNode } from "@/lib/types";

/**
 * Черновик графа с историей, выделением и правками с клавиатуры.
 *
 * Страница владеет графом, холст его рисует. Всё, что можно сделать с
 * графом, не глядя на холст — вставить, продублировать, снять связи,
 * разложить, отменить, — собрано здесь, чтобы страница ролика и страница
 * шаблона не расходились в мелочах: у них одни и те же горячие клавиши
 * и одно и то же меню.
 */
export function useGraphDraft(base: Graph | null | undefined) {
  const [draft, setDraftRaw] = useState<Graph | null>(null);
  // Выделением владеет холст: `selected` — зеркало того, что он сообщил.
  // Страница может только попросить выделить (после вставки, добавления,
  // Ctrl+A) — одноразовым запросом с растущим номером, см. GraphCanvas.
  const [selected, setSelectedRaw] = useState<string[]>([]);
  const [selectRequest, setSelectRequest] = useState<{ ids: string[]; seq: number }>({ ids: [], seq: 0 });
  const setSelected = useCallback((ids: string[]) => {
    setSelectedRaw((cur) => (cur.length === ids.length && cur.every((x, i) => x === ids[i]) ? cur : ids));
  }, []);
  const requestSelect = useCallback((ids: string[]) => {
    setSelectedRaw(ids);
    setSelectRequest((r) => ({ ids, seq: r.seq + 1 }));
  }, []);
  const history = useRef(new History<Graph>()).current;
  const [, bump] = useState(0);
  const draftRef = useRef<Graph | null>(null);
  draftRef.current = draft;

  // Новая версия с сервера — начинаем историю заново: отменять чужое
  // сохранение в свою старую копию было бы ловушкой.
  useEffect(() => {
    if (!base) return;
    setDraftRaw({ nodes: base.nodes, edges: base.edges });
    history.clear();
    bump((x) => x + 1);
  }, [base, history]);

  /** Правка с записью в историю. `commit=false` — узел ещё тянут. */
  const setDraft = useCallback(
    (next: Graph, commit = true) => {
      const cur = draftRef.current;
      if (commit && cur) history.push(cur);
      setDraftRaw(next);
      bump((x) => x + 1);
    },
    [history],
  );

  const undo = useCallback(() => {
    const cur = draftRef.current;
    if (!cur) return;
    const prev = history.undo(cur);
    if (prev) {
      setDraftRaw(prev);
      bump((x) => x + 1);
    }
  }, [history]);

  const redo = useCallback(() => {
    const cur = draftRef.current;
    if (!cur) return;
    const next = history.redo(cur);
    if (next) {
      setDraftRaw(next);
      bump((x) => x + 1);
    }
  }, [history]);

  const dirty = useMemo(() => {
    if (!draft || !base) return false;
    return JSON.stringify(draft.nodes) !== JSON.stringify(base.nodes) || JSON.stringify(draft.edges) !== JSON.stringify(base.edges);
  }, [draft, base]);

  const ids = selected;

  const addNode = useCallback(
    (type: string) => {
      const cur = draftRef.current;
      if (!cur) return;
      const id = newNodeId(type, new Set(cur.nodes.map((n) => n.id)));
      const pos = nextFreePosition(cur);
      setDraft({ ...cur, nodes: [...cur.nodes, { id, type, position: pos, data: {} }] });
      requestSelect([id]);
    },
    [setDraft, requestSelect],
  );

  const updateNode = useCallback(
    (next: GraphNode) => {
      const cur = draftRef.current;
      if (!cur) return;
      setDraft({ ...cur, nodes: cur.nodes.map((n) => (n.id === next.id ? next : n)) });
    },
    [setDraft],
  );

  const remove = useCallback(
    (which: string[] = ids) => {
      const cur = draftRef.current;
      if (!cur || which.length === 0) return;
      setDraft(removeNodes(cur, which));
      requestSelect([]);
    },
    [ids, setDraft, requestSelect],
  );

  const removeBridging = useCallback(
    (id: string) => {
      const cur = draftRef.current;
      if (!cur) return;
      setDraft(removeAndBridge(cur, id));
      requestSelect([]);
    },
    [setDraft, requestSelect],
  );

  const detach = useCallback(
    (which: string[] = ids) => {
      const cur = draftRef.current;
      if (!cur || which.length === 0) return;
      setDraft(detachNodes(cur, which));
    },
    [ids, setDraft],
  );

  const duplicate = useCallback(
    (which: string[] = ids) => {
      const cur = draftRef.current;
      if (!cur || which.length === 0) return;
      const clone = cloneSelection(cur, which);
      setDraft({ nodes: [...cur.nodes, ...clone.nodes], edges: [...cur.edges, ...clone.edges] });
      requestSelect(clone.nodes.map((n) => n.id));
    },
    [ids, setDraft, requestSelect],
  );

  const copy = useCallback(
    (which: string[] = ids) => {
      const cur = draftRef.current;
      if (!cur || which.length === 0) return;
      const set = new Set(which);
      copyToClipboard({
        nodes: cur.nodes.filter((n) => set.has(n.id)),
        edges: cur.edges.filter((e) => set.has(e.source) && set.has(e.target)),
      });
      toast(`Скопировано узлов: ${which.length}`);
    },
    [ids],
  );

  const paste = useCallback(() => {
    const cur = draftRef.current;
    const frag = readClipboard();
    if (!cur || !frag || frag.nodes.length === 0) {
      toast("Буфер пуст — сначала скопируйте узлы");
      return;
    }
    const { graph, ids: added } = pasteFragment(cur, frag);
    setDraft(graph);
    requestSelect(added);
  }, [setDraft, requestSelect]);

  const layout = useCallback(() => {
    const cur = draftRef.current;
    if (!cur) return;
    setDraft(autoLayout(cur));
  }, [setDraft]);

  const selectAll = useCallback(() => {
    const cur = draftRef.current;
    if (cur) requestSelect(cur.nodes.map((n) => n.id));
  }, [requestSelect]);

  // Горячие клавиши — только когда фокус не в поле ввода: иначе Ctrl+C в
  // промте копировал бы узлы вместо текста.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((k === "z" && e.shiftKey) || k === "y") {
        e.preventDefault();
        redo();
      } else if (k === "c") {
        if (window.getSelection()?.toString()) return;
        e.preventDefault();
        copy();
      } else if (k === "v") {
        e.preventDefault();
        paste();
      } else if (k === "d") {
        e.preventDefault();
        duplicate();
      } else if (k === "a") {
        e.preventDefault();
        selectAll();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo, copy, paste, duplicate, selectAll]);

  const onCanvasChange = useCallback(
    (nodes: GraphNode[], edges: GraphEdge[], commit: boolean) => setDraft({ nodes, edges }, commit),
    [setDraft],
  );

  return {
    draft,
    setDraft,
    dirty,
    selected,
    setSelected,
    selectRequest,
    requestSelect,
    canUndo: history.canUndo,
    canRedo: history.canRedo,
    undo,
    redo,
    addNode,
    updateNode,
    remove,
    removeBridging,
    detach,
    duplicate,
    copy,
    paste,
    layout,
    selectAll,
    onCanvasChange,
  };
}
