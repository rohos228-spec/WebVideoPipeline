"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, subscribeProject } from "@/lib/api";
import { GraphCanvas } from "@/components/pipeline/graph-canvas";
import { NodeInspector } from "@/components/pipeline/node-inspector";
import { NodePalette } from "@/components/pipeline/node-palette";
import { GraphDiffCard } from "@/components/pipeline/graph-diff-card";
import { Button } from "@/components/ui/button";
import { Chip, Working } from "@/components/ui/bits";
import { projectName } from "@/lib/format";
import type { GraphDiffResponse, GraphEdge, GraphNode, NodeCatalog } from "@/lib/types";

/**
 * Конструктор конвейера — в двух режимах.
 *
 * `/pipeline?project=N` — **граф ролика**: то, по чему он идёт на самом
 * деле. Здесь видны состояния узлов, цены, можно запустить или сбросить
 * шаг, выбрать модель на узел. Сохранение показывает разницу и какие шаги
 * сгорят, и только потом применяет.
 *
 * `/pipeline` без параметра — **шаблон** (`/api/workflows`): схема, по
 * которой заводятся новые ролики. У уже созданного ролика она ничего не
 * меняет — это и была главная путаница старого экрана.
 *
 * Работа идёт с черновиком в памяти, а на сервер уезжает по кнопке. Автосейв
 * здесь был бы вреден: полусобранная схема с висящей нодой — обычное
 * промежуточное состояние, и сохранять её значит ломать конвейер на время
 * правки.
 */
export default function PipelinePage() {
  return (
    <Suspense fallback={<Working label="открываю конструктор" />}>
      <PipelineRouter />
    </Suspense>
  );
}

function PipelineRouter() {
  const params = useSearchParams();
  const projectId = Number(params.get("project") ?? "");
  if (Number.isFinite(projectId) && projectId > 0) return <ProjectGraphPage projectId={projectId} />;
  return <TemplatePage />;
}

type Draft = { nodes: GraphNode[]; edges: GraphEdge[] };

function useDirty(draft: Draft | null, base: Draft | null | undefined) {
  return useMemo(() => {
    if (!draft || !base) return false;
    return JSON.stringify(draft.nodes) !== JSON.stringify(base.nodes) || JSON.stringify(draft.edges) !== JSON.stringify(base.edges);
  }, [draft, base]);
}

// ── Граф ролика ──────────────────────────────────────────────────────────

function ProjectGraphPage({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState<GraphDiffResponse | null>(null);

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  const graph = useQuery({ queryKey: ["graph", projectId], queryFn: () => api.projectGraph(projectId) });

  useEffect(() => {
    if (graph.data) setDraft({ nodes: graph.data.nodes, edges: graph.data.edges });
  }, [graph.data]);

  useEffect(() => {
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    };
    return subscribeProject(projectId, refresh);
  }, [projectId, qc]);

  const base = graph.data ? { nodes: graph.data.nodes, edges: graph.data.edges } : undefined;
  const dirty = useDirty(draft, base);

  const check = useQuery({
    queryKey: ["graph-check", draft],
    queryFn: () => api.validateGraph(draft!.nodes, draft!.edges),
    enabled: Boolean(draft),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["graph", projectId] });
    qc.invalidateQueries({ queryKey: ["stages", projectId] });
    qc.invalidateQueries({ queryKey: ["project", projectId] });
  };

  // Сохранение в два шага: сперва разница и что сгорит, потом применение.
  const preview = useMutation({
    mutationFn: () => api.projectGraphDiff(projectId, draft!.nodes, draft!.edges),
    onSuccess: (d) => {
      if (d.diff.empty) {
        toast("Изменений нет");
        return;
      }
      setPending(d);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const save = useMutation({
    mutationFn: (reset: boolean) => api.saveProjectGraph(projectId, draft!.nodes, draft!.edges, reset),
    onSuccess: (r) => {
      setPending(null);
      invalidate();
      toast.success(`Граф применён: ${r.diff.summary}${r.reset_done ? ", шаги сброшены" : ""}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const resetGraph = useMutation({
    mutationFn: () => api.resetProjectGraph(projectId),
    onSuccess: () => {
      invalidate();
      toast.success("Штатная схема применена");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const applyProposal = useMutation({
    mutationFn: (id: string) => api.applyGraphProposal(projectId, id),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Предложение применено: ${r.diff.summary}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const discardProposal = useMutation({
    mutationFn: () => api.discardGraphProposal(projectId),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  });

  const runStep = useMutation({
    mutationFn: ({ step, node }: { step: string; node: string }) => api.runStep(projectId, step, node),
    onSuccess: () => {
      invalidate();
      toast.success("Шаг запущен");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const resetStep = useMutation({
    mutationFn: (step: string) => api.resetStep(projectId, step),
    onSuccess: () => {
      invalidate();
      toast.success("Результат сброшен");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const addNode = (type: string) => {
    if (!draft) return;
    const id = `n-${type}-${Date.now().toString(36)}`;
    const x = Math.max(0, ...draft.nodes.map((n) => n.position?.x ?? 0)) + 290;
    setDraft({ ...draft, nodes: [...draft.nodes, { id, type, position: { x, y: 200 }, data: {} }] });
    setSelected(id);
  };

  if (graph.isLoading || project.isLoading) {
    return (
      <div className="p-10">
        <Working label="открываю схему ролика" />
      </div>
    );
  }
  if (graph.isError || !graph.data || !project.data) {
    return (
      <div className="space-y-3 p-10">
        <p className="text-[14px] text-danger">Схема не открывается: {graph.error?.message}</p>
        <Button variant="secondary" onClick={() => graph.refetch()}>
          Ещё раз
        </Button>
      </div>
    );
  }

  const g = graph.data;
  const errors = check.data?.errors ?? [];
  const selectedNode = draft?.nodes.find((n) => n.id === selected) ?? null;
  const selectedInfo = g.catalog.find((c) => c.type === selectedNode?.type);
  const selectedPrice = selectedInfo?.step_code ? g.prices[selectedInfo.step_code]?.price_credits : undefined;
  const priceLabels = Object.fromEntries(
    Object.entries(g.prices)
      .filter(([, p]) => p.price_micro > 0)
      .map(([code, p]) => [code, `${p.price_credits.replace(".", ",")} кр${p.exact ? "" : " ≈"}`]),
  );
  const proposal = g.proposal;
  const proposedMarks: Record<string, "added" | "removed" | "changed"> = {};
  if (proposal) {
    proposal.diff.added_nodes.forEach((n) => (proposedMarks[n.id] = "added"));
    proposal.diff.removed_nodes.forEach((n) => (proposedMarks[n.id] = "removed"));
    proposal.diff.changed_nodes.forEach((n) => (proposedMarks[n.id] = "changed"));
  }
  const catalog: NodeCatalog = { kinds: { work: "Шаги", hitl: "Проверки", config: "Настройка" }, nodes: g.catalog };
  const busy = runStep.isPending || resetStep.isPending || save.isPending;

  return (
    <div className="flex h-screen min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="text-[13px] text-content-faint hover:text-accent">
            ← к ролику
          </Link>
          <span className="truncate font-display text-[14px] text-content">{projectName(project.data)}</span>
          <span className="font-mono text-[11px] text-content-faint">{g.status}</span>
          {g.source !== "canvas" && (
            <Chip tone="neutral">шаблон — станет своим при сохранении</Chip>
          )}
          {dirty && <Chip tone="warn">не сохранено</Chip>}
          {errors.length > 0 && <Chip tone="danger">{errors.length} ошибк(и)</Chip>}
          {proposal && <Chip tone="accent">предложение агента</Chip>}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Link href="/pipeline" className="text-[12px] text-content-faint hover:text-accent">
            шаблон
          </Link>
          <Button
            size="sm"
            variant="ghost"
            disabled={resetGraph.isPending}
            onClick={() => {
              if (confirm("Вернуть штатную схему этому ролику? Позиции и правки узлов пропадут.")) resetGraph.mutate();
            }}
          >
            Штатная схема
          </Button>
          <Button
            size="sm"
            variant={dirty ? "primary" : "secondary"}
            disabled={!dirty || preview.isPending || errors.length > 0}
            title={errors.length > 0 ? "Сначала почините ошибки схемы" : undefined}
            onClick={() => preview.mutate()}
          >
            {preview.isPending ? "Считаю…" : "Применить"}
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
          {errors.length > 4 && <li className="text-[12px] text-danger">…и ещё {errors.length - 4}</li>}
        </ul>
      )}

      {pending && (
        <div className="shrink-0 border-b border-border bg-surface-sunken px-4 py-3">
          <GraphDiffCard
            diff={pending.diff}
            reset={pending.reset}
            warnings={pending.warnings}
            busy={save.isPending}
            onApply={(reset) => save.mutate(reset)}
            onCancel={() => setPending(null)}
          />
        </div>
      )}

      {proposal && !pending && (
        <div className="shrink-0 border-b border-accent bg-accent-muted px-4 py-3">
          <GraphDiffCard
            title={`Агент предлагает${proposal.reason ? `: ${proposal.reason}` : ""}`}
            diff={proposal.diff}
            reset={proposal.reset}
            warnings={proposal.warnings}
            busy={applyProposal.isPending || discardProposal.isPending}
            onApply={() => applyProposal.mutate(proposal.id)}
            onCancel={() => discardProposal.mutate()}
            cancelLabel="Отклонить"
          />
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr_340px]">
        <aside className="min-h-0 overflow-y-auto border-r border-border">
          <NodePalette catalog={catalog} onAdd={addNode} />
        </aside>

        <div className="min-h-0">
          {draft ? (
            <GraphCanvas
              key={`${projectId}-${g.source}`}
              nodes={draft.nodes}
              edges={draft.edges}
              catalog={g.catalog}
              states={g.states}
              prices={priceLabels}
              proposed={proposedMarks}
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
            catalog={g.catalog}
            project={{
              id: projectId,
              state: selectedNode ? g.states[selectedNode.id] : undefined,
              price: selectedPrice ? `${selectedPrice.replace(".", ",")} кр` : undefined,
              models: g.models,
              busy,
              onRun: (step, node) => {
                if (dirty && !confirm("Есть несохранённые правки схемы — запустить шаг по сохранённой?")) return;
                runStep.mutate({ step, node });
              },
              onReset: (step) => {
                if (confirm(`Сбросить результат шага ${step} и всё, что от него зависит?`)) resetStep.mutate(step);
              },
            }}
            onChange={(next) =>
              setDraft((d) => (d ? { ...d, nodes: d.nodes.map((n) => (n.id === next.id ? next : n)) } : d))
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

// ── Шаблон ───────────────────────────────────────────────────────────────

function TemplatePage() {
  const qc = useQueryClient();
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const catalog = useQuery({ queryKey: ["node-catalog"], queryFn: api.nodeCatalog });
  const list = useQuery({ queryKey: ["workflows"], queryFn: api.workflows });

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

  const dirty = useDirty(draft, current.data ? { nodes: current.data.nodes, edges: current.data.edges } : undefined);

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
      toast.success("Шаблон сохранён");
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
    const x = Math.max(0, ...draft.nodes.map((n) => n.position?.x ?? 0)) + 290;
    setDraft({ ...draft, nodes: [...draft.nodes, { id, type, position: { x, y: 200 }, data: {} }] });
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
        <p className="text-[14px] text-danger">Конструктор не открывается: {(catalog.error ?? list.error)?.message}</p>
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
          Новые ролики заводятся по схеме-шаблону. Создайте штатную — семь шагов от идеи до готового ролика, — а
          потом меняйте под себя.
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
          <Chip tone="neutral">шаблон для новых роликов</Chip>
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

      <p className="shrink-0 border-b border-border bg-surface-sunken px-4 py-1.5 text-[12px] text-content-muted">
        Это схема для новых роликов. Схема уже созданного ролика открывается с его экрана — «схема ролика».
      </p>

      {errors.length > 0 && (
        <ul className="shrink-0 space-y-0.5 border-b border-danger bg-danger-muted px-4 py-2">
          {errors.slice(0, 4).map((e, i) => (
            <li key={i} className="text-[12px] text-danger">
              {e}
            </li>
          ))}
          {errors.length > 4 && <li className="text-[12px] text-danger">…и ещё {errors.length - 4}</li>}
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
              setDraft((d) => (d ? { ...d, nodes: d.nodes.map((n) => (n.id === next.id ? next : n)) } : d))
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
