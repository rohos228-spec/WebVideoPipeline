"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { GraphCanvas } from "@/components/pipeline/graph-canvas";
import { NodeInspector } from "@/components/pipeline/node-inspector";
import { NodePalette } from "@/components/pipeline/node-palette";
import { Button } from "@/components/ui/button";
import { Chip, Working } from "@/components/ui/bits";
import type { GraphEdge, GraphNode } from "@/lib/types";

/**
 * Конструктор конвейера.
 *
 * Своя страница во всю ширину — см. пояснение в `graph-canvas.tsx`: схема
 * узлов и «редакторский лист» не уживаются в одной колонке.
 *
 * Работа идёт с черновиком в памяти, а на сервер уезжает по кнопке. Автосейв
 * здесь был бы вреден: полусобранная схема с висящей нодой — обычное
 * промежуточное состояние, и сохранять её значит ломать конвейер на время
 * правки.
 */
export default function PipelinePage() {
  const qc = useQueryClient();
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const catalog = useQuery({ queryKey: ["node-catalog"], queryFn: api.nodeCatalog });
  const list = useQuery({ queryKey: ["workflows"], queryFn: api.workflows });

  // Открываем схему по умолчанию: она и есть та, по которой идут проекты.
  useEffect(() => {
    if (workflowId !== null || !list.data?.length) return;
    setWorkflowId((list.data.find((w) => w.is_default) ?? list.data[0]).id);
  }, [list.data, workflowId]);

  const current = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api.workflow(workflowId as number),
    enabled: workflowId !== null,
  });

  useEffect(() => {
    if (current.data) setDraft({ nodes: current.data.nodes, edges: current.data.edges });
  }, [current.data]);

  const dirty = useMemo(() => {
    if (!draft || !current.data) return false;
    return (
      JSON.stringify(draft.nodes) !== JSON.stringify(current.data.nodes) ||
      JSON.stringify(draft.edges) !== JSON.stringify(current.data.edges)
    );
  }, [draft, current.data]);

  // Проверка идёт по черновику на каждое изменение: цикл или оборванная
  // цепочка видны сразу, а не при попытке сохранить.
  const check = useQuery({
    queryKey: ["graph-check", draft],
    queryFn: () => api.validateGraph(draft!.nodes, draft!.edges),
    enabled: Boolean(draft),
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveWorkflow({
        id: workflowId ?? undefined,
        name: current.data?.name ?? "Мой конвейер",
        description: current.data?.description ?? null,
        nodes: draft!.nodes,
        edges: draft!.edges,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      qc.invalidateQueries({ queryKey: ["workflows"] });
      toast.success("Схема сохранена");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reset = useMutation({
    mutationFn: api.resetDefaultWorkflow,
    onSuccess: (wf) => {
      setWorkflowId(wf.id);
      setDraft({ nodes: wf.nodes, edges: wf.edges });
      qc.invalidateQueries({ queryKey: ["workflows"] });
      toast.success("Штатная схема восстановлена");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const addNode = (type: string) => {
    if (!draft) return;
    const id = `n-${type}-${Date.now().toString(36)}`;
    // Ставим правее самого правого — новый узел не должен падать под чужой.
    const x = Math.max(0, ...draft.nodes.map((n) => n.position?.x ?? 0)) + 290;
    setDraft({
      ...draft,
      nodes: [...draft.nodes, { id, type, position: { x, y: 200 }, data: {} }],
    });
    setSelected(id);
  };

  const selectedNode = draft?.nodes.find((n) => n.id === selected) ?? null;

  if (catalog.isLoading || list.isLoading) {
    return (
      <div className="p-10">
        <Working label="открываю конструктор" />
      </div>
    );
  }

  if (catalog.isError || list.isError) {
    return (
      <div className="space-y-3 p-10">
        <p className="text-[14px] text-danger">
          Конструктор не открывается: {(catalog.error ?? list.error)?.message}
        </p>
        <Button
          variant="secondary"
          onClick={() => {
            catalog.refetch();
            list.refetch();
          }}
        >
          Ещё раз
        </Button>
      </div>
    );
  }

  if (!list.data?.length) {
    return (
      <div className="mx-auto max-w-[520px] space-y-4 px-10 py-16 text-center">
        <h1 className="font-display text-[22px] text-content">Схем ещё нет</h1>
        <p className="text-[14px] text-content-muted">
          Конвейер работает по схеме узлов. Создайте штатную — семь шагов от идеи до готового
          ролика, — а потом меняйте под себя.
        </p>
        <Button variant="primary" onClick={() => reset.mutate()} disabled={reset.isPending}>
          {reset.isPending ? "Создаю…" : "Создать штатную схему"}
        </Button>
      </div>
    );
  }

  const errors = check.data?.errors ?? [];

  return (
    <div className="flex h-screen min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="text-[13px] text-content-faint hover:text-accent">
            ← к роликам
          </Link>
          <select
            value={workflowId ?? ""}
            onChange={(e) => {
              setWorkflowId(Number(e.target.value));
              setSelected(null);
            }}
            className="max-w-[240px] truncate rounded-sm border border-border bg-surface px-2 py-1 text-[13px] text-content"
          >
            {list.data.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
                {w.is_default ? " (штатная)" : ""}
              </option>
            ))}
          </select>
          {dirty && <Chip tone="warn">не сохранено</Chip>}
          {errors.length > 0 && <Chip tone="danger">{errors.length} ошибк(и)</Chip>}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => reset.mutate()}>
            Вернуть штатную
          </Button>
          <Button
            size="sm"
            variant={dirty ? "primary" : "secondary"}
            disabled={!dirty || save.isPending || errors.length > 0}
            title={errors.length > 0 ? "Сначала почините ошибки схемы" : undefined}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </Button>
        </div>
      </header>

      {errors.length > 0 && (
        <ul className="shrink-0 space-y-0.5 border-b border-danger bg-danger-muted px-4 py-2">
          {errors.slice(0, 4).map((e, i) => (
            <li key={i} className="text-[12px] text-danger">
              {e}
            </li>
          ))}
          {errors.length > 4 && (
            <li className="text-[12px] text-danger">…и ещё {errors.length - 4}</li>
          )}
        </ul>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr_340px]">
        <aside className="min-h-0 overflow-y-auto border-r border-border">
          <NodePalette catalog={catalog.data} onAdd={addNode} />
        </aside>

        <div className="min-h-0">
          {draft && catalog.data ? (
            <GraphCanvas
              key={workflowId ?? "none"}
              nodes={draft.nodes}
              edges={draft.edges}
              catalog={catalog.data.nodes}
              onChange={(nodes, edges) => setDraft({ nodes, edges })}
              onSelect={setSelected}
            />
          ) : (
            <div className="p-6">
              <Working label="читаю схему" />
            </div>
          )}
        </div>

        <aside className="min-h-0 border-l border-border">
          <NodeInspector
            node={selectedNode}
            catalog={catalog.data?.nodes ?? []}
            onChange={(next) =>
              setDraft((d) =>
                d ? { ...d, nodes: d.nodes.map((n) => (n.id === next.id ? next : n)) } : d,
              )
            }
            onRemove={() => {
              setDraft((d) =>
                d
                  ? {
                      nodes: d.nodes.filter((n) => n.id !== selected),
                      edges: d.edges.filter((e) => e.source !== selected && e.target !== selected),
                    }
                  : d,
              );
              setSelected(null);
            }}
          />
        </aside>
      </div>
    </div>
  );
}
