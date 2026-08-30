"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, subscribeProject } from "@/lib/api";
import { GraphCanvas, type NodeMenuItem } from "@/components/pipeline/graph-canvas";
import { NodeInspector } from "@/components/pipeline/node-inspector";
import { NodePalette } from "@/components/pipeline/node-palette";
import { GraphDiffCard } from "@/components/pipeline/graph-diff-card";
import { useGraphDraft } from "@/components/pipeline/use-graph-draft";
import { Button } from "@/components/ui/button";
import { Chip, Working } from "@/components/ui/bits";
import { projectName } from "@/lib/format";
import type { GraphDiffResponse, NodeCatalog, NodeGroupSummary, NodeKindInfo } from "@/lib/types";

/**
 * Конструктор конвейера — в двух режимах.
 *
 * `/pipeline?project=N` — **граф ролика**: то, по чему он идёт на самом
 * деле. Здесь видны состояния узлов, цены, можно запустить или сбросить
 * шаг, выбрать модель, параметры и проверку на узел, вставить группу.
 * Сохранение показывает разницу и какие шаги сгорят, и только потом
 * применяет.
 *
 * `/pipeline` без параметра — **шаблон** (`/api/workflows`): схема, по
 * которой заводятся новые ролики. У уже созданного ролика она ничего не
 * меняет — это и была главная путаница старого экрана.
 *
 * Работа идёт с черновиком в памяти, а на сервер уезжает по кнопке. Автосейв
 * здесь был бы вреден: полусобранная схема с висящей нодой — обычное
 * промежуточное состояние, и сохранять её значит ломать конвейер на время
 * правки. Настройки, которые живут не в графе (параметры шага, проверка,
 * пульт оператора, файлы хранилища), уезжают сразу — они не ломают схему.
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

/** Пункты меню узла, общие для обоих режимов. */
function baseNodeMenu(
  g: ReturnType<typeof useGraphDraft>,
  id: string,
  node: { data?: Record<string, unknown> } | undefined,
): NodeMenuItem[] {
  const many = g.selected.length > 1 && g.selected.includes(id);
  const ids = many ? g.selected : [id];
  const disabled = node?.data?.disabled === true;
  return [
    { label: many ? `Дублировать (${ids.length})` : "Дублировать", onClick: () => g.duplicate(ids) },
    { label: many ? `Скопировать (${ids.length})` : "Скопировать", onClick: () => g.copy(ids) },
    {
      label: disabled ? "Включить" : "Выключить",
      onClick: () => {
        const cur = g.draft;
        if (!cur) return;
        const set = new Set(ids);
        g.setDraft({
          ...cur,
          nodes: cur.nodes.map((n) => (set.has(n.id) ? { ...n, data: { ...(n.data ?? {}), disabled: !disabled } } : n)),
        });
      },
    },
    { label: "Снять связи", onClick: () => g.detach(ids), gap: true },
    { label: "Убрать, соединив соседей", onClick: () => g.removeBridging(id), disabled: many },
    { label: many ? `Убрать из схемы (${ids.length})` : "Убрать из схемы", danger: true, onClick: () => g.remove(ids) },
  ];
}

function basePaneMenu(g: ReturnType<typeof useGraphDraft>): NodeMenuItem[] {
  return [
    { label: "Вставить", onClick: g.paste },
    { label: "Выделить всё", onClick: g.selectAll },
    { label: "Разложить по слоям", onClick: g.layout, gap: true },
    { label: "Отменить", onClick: g.undo, disabled: !g.canUndo, gap: true },
    { label: "Вернуть", onClick: g.redo, disabled: !g.canRedo },
  ];
}

function ErrorsBar({ errors }: { errors: string[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="shrink-0 space-y-0.5 border-b border-danger bg-danger-muted px-4 py-2">
      {errors.slice(0, 4).map((e, i) => (
        <li key={i} className="text-[12px] text-danger">
          {e}
        </li>
      ))}
      {errors.length > 4 && <li className="text-[12px] text-danger">…и ещё {errors.length - 4}</li>}
    </ul>
  );
}

/** Кнопки отмены/возврата/раскладки — одинаковые в обоих режимах. */
function EditTools({ g }: { g: ReturnType<typeof useGraphDraft> }) {
  return (
    <div className="flex items-center gap-1">
      <Button size="sm" variant="ghost" disabled={!g.canUndo} onClick={g.undo} title="Ctrl+Z">
        ↶
      </Button>
      <Button size="sm" variant="ghost" disabled={!g.canRedo} onClick={g.redo} title="Ctrl+Shift+Z">
        ↷
      </Button>
      <Button size="sm" variant="ghost" onClick={g.layout} title="Разложить узлы по слоям">
        Разложить
      </Button>
    </div>
  );
}

// ── Граф ролика ──────────────────────────────────────────────────────────

function ProjectGraphPage({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<GraphDiffResponse | null>(null);

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  const graph = useQuery({ queryKey: ["graph", projectId], queryFn: () => api.projectGraph(projectId) });
  const groups = useQuery({ queryKey: ["node-groups"], queryFn: api.nodeGroups, staleTime: 60_000 });
  const options = useQuery({ queryKey: ["options"], queryFn: api.options, staleTime: 10 * 60_000 });

  const base = useMemo(() => (graph.data ? { nodes: graph.data.nodes, edges: graph.data.edges } : undefined), [graph.data]);
  const g = useGraphDraft(base);
  const { draft, dirty, selected } = g;

  useEffect(() => {
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["hitl", projectId] });
    };
    return subscribeProject(projectId, refresh);
  }, [projectId, qc]);

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
  const fail = (e: Error) => toast.error(e.message);

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
    onError: fail,
  });

  const save = useMutation({
    mutationFn: (reset: boolean) => api.saveProjectGraph(projectId, draft!.nodes, draft!.edges, reset),
    onSuccess: (r) => {
      setPending(null);
      invalidate();
      toast.success(`Граф применён: ${r.diff.summary}${r.reset_done ? ", шаги сброшены" : ""}`);
    },
    onError: fail,
  });

  const resetGraph = useMutation({
    mutationFn: () => api.resetProjectGraph(projectId),
    onSuccess: () => {
      invalidate();
      toast.success("Штатная схема применена");
    },
    onError: fail,
  });

  const applyProposal = useMutation({
    mutationFn: (id: string) => api.applyGraphProposal(projectId, id),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Предложение применено: ${r.diff.summary}`);
    },
    onError: fail,
  });

  const discardProposal = useMutation({
    mutationFn: () => api.discardGraphProposal(projectId),
    onSuccess: invalidate,
    onError: fail,
  });

  const runStep = useMutation({
    mutationFn: ({ step, node }: { step: string; node: string }) => api.runStep(projectId, step, node),
    onSuccess: () => {
      invalidate();
      toast.success("Шаг запущен");
    },
    onError: fail,
  });

  const resetStep = useMutation({
    mutationFn: (step: string) => api.resetStep(projectId, step),
    onSuccess: () => {
      invalidate();
      toast.success("Результат сброшен");
    },
    onError: fail,
  });

  // Настройки вне графа уезжают сразу: они не ломают схему, а ждать кнопки
  // «Применить» ради громкости музыки — лишний шаг.
  const patchMeta = useMutation({
    mutationFn: (meta: Record<string, unknown>) => api.patchProject(projectId, { meta } as never),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: fail,
  });

  const patchProject = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patchProject(projectId, body as never),
    onSuccess: () => invalidate(),
    onError: fail,
  });

  const control = useMutation({
    mutationFn: (what: "pause" | "continue" | "stop") =>
      what === "pause" ? api.pauseProject(projectId) : what === "continue" ? api.continueProject(projectId) : api.stopProject(projectId),
    onSuccess: (_, what) => {
      invalidate();
      toast.success(what === "pause" ? "Пауза после текущего шага" : what === "continue" ? "Продолжаю" : "Остановлено");
    },
    onError: fail,
  });

  const insertGroup = useMutation({
    mutationFn: (group: NodeGroupSummary) => {
      if (dirty && !confirm("Есть несохранённые правки схемы — группа вставится в сохранённую, правки пропадут. Продолжить?")) {
        throw new Error("отменено");
      }
      // Вставляем после выделенного узла, если он один; иначе сервер сам найдёт место.
      const after = selected.length === 1 ? selected[0] : null;
      return api.insertNodeGroup(projectId, group.id, after);
    },
    onSuccess: (_, group) => {
      invalidate();
      toast.success(`Группа «${group.title}» вставлена`);
    },
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  const saveGroup = useMutation({
    mutationFn: async () => {
      if (selected.length < 2) throw new Error("Выделите хотя бы два узла");
      if (dirty) throw new Error("Сначала примените схему — группа собирается из сохранённых узлов");
      const title = prompt("Название группы", "Моя группа")?.trim();
      if (!title) throw new Error("отменено");
      return api.createNodeGroupFromSelection(projectId, { node_ids: selected, title });
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["node-groups"] });
      toast.success(`Группа «${r.title ?? "сохранена"}» появилась в палитре`);
    },
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  const deleteGroup = useMutation({
    mutationFn: (group: NodeGroupSummary) => {
      if (!confirm(`Удалить группу «${group.title}» из палитры?`)) throw new Error("отменено");
      return api.deleteNodeGroup(group.id);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["node-groups"] }),
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  // Хуки — до ранних выходов: число хуков на рендер должно быть постоянным.
  const prices = graph.data?.prices;
  const priceLabels = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(prices ?? {})
          .filter(([, pr]) => pr.price_micro > 0)
          .map(([code, pr]) => [code, `${pr.price_credits.replace(".", ",")} кр${pr.exact ? "" : " ≈"}`]),
      ),
    [prices],
  );
  const proposalData = graph.data?.proposal ?? null;
  const proposedMarks = useMemo(() => {
    const marks: Record<string, "added" | "removed" | "changed"> = {};
    if (proposalData) {
      proposalData.diff.added_nodes.forEach((n) => (marks[n.id] = "added"));
      proposalData.diff.removed_nodes.forEach((n) => (marks[n.id] = "removed"));
      proposalData.diff.changed_nodes.forEach((n) => (marks[n.id] = "changed"));
    }
    return marks;
  }, [proposalData]);

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

  const gd = graph.data;
  const p = project.data;
  const errors = check.data?.errors ?? [];
  const selectedNode = selected.length === 1 ? (draft?.nodes.find((n) => n.id === selected[0]) ?? null) : null;
  const selectedInfo = gd.catalog.find((c) => c.type === selectedNode?.type);
  const selectedPrice = selectedInfo?.step_code ? gd.prices[selectedInfo.step_code]?.price_credits : undefined;
  const proposal = gd.proposal;
  const catalog: NodeCatalog = { kinds: { work: "Шаги", hitl: "Проверки", config: "Настройка" }, nodes: gd.catalog };
  const busy = runStep.isPending || resetStep.isPending || save.isPending || patchMeta.isPending;
  const running = p.generation_active;
  const paused = p.status === "paused";

  const runFromMenu = (id: string) => {
    const node = draft?.nodes.find((n) => n.id === id);
    const info = node && gd.catalog.find((c) => c.type === node.type);
    if (!node || !info?.step_code) return;
    if (dirty && !confirm("Есть несохранённые правки схемы — запустить шаг по сохранённой?")) return;
    runStep.mutate({ step: info.step_code, node: id });
  };

  const nodeMenu = (id: string): NodeMenuItem[] => {
    const node = draft?.nodes.find((n) => n.id === id);
    const info = node && gd.catalog.find((c) => c.type === node.type);
    const state = gd.states[id];
    const items: NodeMenuItem[] = [];
    if (info?.step_code) {
      items.push({
        label: state === "done" ? "Запустить заново" : "Запустить шаг",
        onClick: () => runFromMenu(id),
        disabled: busy || node?.data?.disabled === true || state === "running",
      });
      if (state === "done" || state === "failed") {
        items.push({
          label: "Сбросить результат",
          onClick: () => {
            if (confirm(`Сбросить результат шага ${info.step_code} и всё, что от него зависит?`)) resetStep.mutate(info.step_code!);
          },
        });
      }
    }
    return [...items, ...baseNodeMenu(g, id, node).map((it, i) => (i === 0 && items.length ? { ...it, gap: true } : it))];
  };

  const paneMenu = (): NodeMenuItem[] => [
    ...basePaneMenu(g),
    {
      label: `Сохранить выделение как группу${selected.length > 1 ? ` (${selected.length})` : ""}`,
      onClick: () => saveGroup.mutate(),
      disabled: selected.length < 2,
      gap: true,
    },
  ];

  return (
    <div className="flex h-screen min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link href={`/?project=${projectId}`} className="text-[13px] text-content-faint hover:text-accent">
            ← к ролику
          </Link>
          <span className="truncate font-display text-[14px] text-content">{projectName(p)}</span>
          <span className="font-mono text-[11px] text-content-faint">{gd.status}</span>
          {gd.source !== "canvas" && <Chip tone="neutral">шаблон — станет своим при сохранении</Chip>}
          {dirty && <Chip tone="warn">не сохранено</Chip>}
          {errors.length > 0 && <Chip tone="danger">{errors.length} ошибк(и)</Chip>}
          {proposal && <Chip tone="accent">предложение агента</Chip>}
          {running && <Chip tone="accent">идёт генерация</Chip>}
          {paused && <Chip tone="warn">пауза</Chip>}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <label className="flex items-center gap-1.5 text-[12px] text-content-muted" title="Идти по схеме дальше без ручного запуска каждого шага">
            <input
              type="checkbox"
              checked={p.auto_mode}
              disabled={patchProject.isPending}
              onChange={(e) => patchProject.mutate({ auto_mode: e.target.checked })}
              className="h-3.5 w-3.5 accent-[var(--color-accent)]"
            />
            авто
          </label>
          {running ? (
            <>
              <Button size="sm" variant="ghost" disabled={control.isPending} onClick={() => control.mutate("pause")} title="Доделать текущий шаг и остановиться">
                Пауза
              </Button>
              <Button size="sm" variant="danger" disabled={control.isPending} onClick={() => control.mutate("stop")} title="Прервать текущий шаг">
                Стоп
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant={paused ? "primary" : "ghost"}
              disabled={control.isPending}
              onClick={() => control.mutate("continue")}
              title="Снять паузу и продвинуть ролик на следующий шаг"
            >
              Продолжить
            </Button>
          )}
          <span className="mx-1 h-5 border-l border-border" />
          <EditTools g={g} />
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

      <ErrorsBar errors={errors} />

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

      <div className="grid min-h-0 flex-1 grid-cols-[210px_1fr_380px]">
        <aside className="min-h-0 overflow-y-auto border-r border-border">
          <NodePalette
            catalog={catalog}
            groups={groups.data ?? []}
            onAdd={g.addNode}
            onInsertGroup={(grp) => insertGroup.mutate(grp)}
            onDeleteGroup={(grp) => deleteGroup.mutate(grp)}
          />
        </aside>

        <div className="min-h-0">
          {draft ? (
            <GraphCanvas
              key={`${projectId}-${gd.source}`}
              nodes={draft.nodes}
              edges={draft.edges}
              catalog={gd.catalog}
              states={gd.states}
              prices={priceLabels}
              proposed={proposedMarks}
              selectRequest={g.selectRequest}
              onChange={g.onCanvasChange}
              onSelect={g.setSelected}
              nodeMenu={nodeMenu}
              paneMenu={paneMenu}
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
            count={selected.length}
            catalog={gd.catalog}
            project={{
              id: projectId,
              state: selectedNode ? gd.states[selectedNode.id] : undefined,
              price: selectedPrice ? `${selectedPrice.replace(".", ",")} кр` : undefined,
              models: gd.models,
              voices: gd.voices,
              settings: gd.settings,
              options: options.data,
              busy,
              onRun: (step, node) => {
                if (dirty && !confirm("Есть несохранённые правки схемы — запустить шаг по сохранённой?")) return;
                runStep.mutate({ step, node });
              },
              onReset: (step) => {
                if (confirm(`Сбросить результат шага ${step} и всё, что от него зависит?`)) resetStep.mutate(step);
              },
              onStepParams: (step, patch) => patchMeta.mutate({ node_step_params: { [step]: patch } }),
              onMeta: (patch) => patchMeta.mutate(patch),
            }}
            onChange={g.updateNode}
            onRemove={() => g.remove()}
            onDetach={() => g.detach()}
            onDuplicate={() => g.duplicate()}
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

  const base = useMemo(
    () => (current.data ? { nodes: current.data.nodes, edges: current.data.edges } : undefined),
    [current.data],
  );
  const g = useGraphDraft(base);
  const { draft, dirty, selected } = g;

  const check = useQuery({
    queryKey: ["graph-check", draft],
    queryFn: () => api.validateGraph(draft!.nodes, draft!.edges),
    enabled: Boolean(draft),
  });

  const fail = (e: Error) => toast.error(e.message);
  const refreshList = () => qc.invalidateQueries({ queryKey: ["workflows"] });

  const save = useMutation({
    mutationFn: (name?: string) =>
      api.saveWorkflow({
        id: workflowId ?? undefined,
        name: name ?? current.data?.name ?? "Мой конвейер",
        description: current.data?.description ?? null,
        nodes: draft!.nodes,
        edges: draft!.edges,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      refreshList();
      toast.success("Шаблон сохранён");
    },
    onError: fail,
  });

  const reset = useMutation({
    mutationFn: api.resetDefaultWorkflow,
    onSuccess: (wf) => {
      setWorkflowId(wf.id);
      qc.invalidateQueries({ queryKey: ["workflow", wf.id] });
      refreshList();
      toast.success("Штатная схема восстановлена");
    },
    onError: fail,
  });

  const duplicate = useMutation({
    mutationFn: () => api.duplicateWorkflow(workflowId as number),
    onSuccess: (wf) => {
      refreshList();
      setWorkflowId(wf.id);
      toast.success(`Копия: ${wf.name}`);
    },
    onError: fail,
  });

  const createNew = useMutation({
    mutationFn: () => {
      const name = prompt("Название новой схемы", "Новый конвейер")?.trim();
      if (!name) throw new Error("отменено");
      return api.saveWorkflow({ name, description: null, nodes: [], edges: [] });
    },
    onSuccess: (wf) => {
      refreshList();
      setWorkflowId(wf.id);
      toast.success(`Схема «${wf.name}» создана — пустая, соберите из палитры`);
    },
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  const rename = useMutation({
    mutationFn: () => {
      const name = prompt("Новое название", current.data?.name ?? "")?.trim();
      if (!name || name === current.data?.name) throw new Error("отменено");
      return api.saveWorkflow({
        id: workflowId ?? undefined,
        name,
        description: current.data?.description ?? null,
        nodes: draft?.nodes ?? current.data?.nodes ?? [],
        edges: draft?.edges ?? current.data?.edges ?? [],
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      refreshList();
    },
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: () => {
      if (!confirm(`Удалить схему «${current.data?.name}»? Ролики, созданные по ней, не пострадают.`)) throw new Error("отменено");
      return api.deleteWorkflow(workflowId as number);
    },
    onSuccess: () => {
      setWorkflowId(null);
      refreshList();
      toast.success("Схема удалена");
    },
    onError: (e: Error) => e.message !== "отменено" && toast.error(e.message),
  });

  const selectedNode = selected.length === 1 ? (draft?.nodes.find((n) => n.id === selected[0]) ?? null) : null;

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
  const isDefault = list.data.find((w) => w.id === workflowId)?.is_default ?? false;
  const nodeMenu = (id: string) => baseNodeMenu(g, id, draft?.nodes.find((n) => n.id === id));

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
              g.requestSelect([]);
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
          <Button size="sm" variant="ghost" onClick={() => rename.mutate()} title="Переименовать схему">
            переименовать
          </Button>
          <Button size="sm" variant="ghost" onClick={() => createNew.mutate()} title="Новая пустая схема">
            + новая
          </Button>
          <Button size="sm" variant="ghost" disabled={duplicate.isPending} onClick={() => duplicate.mutate()} title="Копия этой схемы">
            копия
          </Button>
          {!isDefault && (
            <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>
              удалить
            </Button>
          )}
          {dirty && <Chip tone="warn">не сохранено</Chip>}
          {errors.length > 0 && <Chip tone="danger">{errors.length} ошибк(и)</Chip>}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <EditTools g={g} />
          <Button size="sm" variant="ghost" onClick={() => reset.mutate()}>
            Вернуть штатную
          </Button>
          <Button
            size="sm"
            variant={dirty ? "primary" : "secondary"}
            disabled={!dirty || save.isPending || errors.length > 0}
            title={errors.length > 0 ? "Сначала почините ошибки схемы" : undefined}
            onClick={() => save.mutate(undefined)}
          >
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </Button>
        </div>
      </header>

      <p className="shrink-0 border-b border-border bg-surface-sunken px-4 py-1.5 text-[12px] text-content-muted">
        Это схема для новых роликов. Схема уже созданного ролика открывается с его экрана — «схема ролика».
      </p>

      <ErrorsBar errors={errors} />

      <div className="grid min-h-0 flex-1 grid-cols-[210px_1fr_380px]">
        <aside className="min-h-0 overflow-y-auto border-r border-border">
          <NodePalette catalog={catalog.data} onAdd={g.addNode} />
        </aside>

        <div className="min-h-0">
          {draft && catalog.data ? (
            <GraphCanvas
              key={workflowId ?? "none"}
              nodes={draft.nodes}
              edges={draft.edges}
              catalog={catalog.data.nodes as NodeKindInfo[]}
              selectRequest={g.selectRequest}
              onChange={g.onCanvasChange}
              onSelect={g.setSelected}
              nodeMenu={nodeMenu}
              paneMenu={() => basePaneMenu(g)}
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
            count={selected.length}
            catalog={catalog.data?.nodes ?? []}
            onChange={g.updateNode}
            onRemove={() => g.remove()}
            onDetach={() => g.detach()}
            onDuplicate={() => g.duplicate()}
          />
        </aside>
      </div>
    </div>
  );
}
