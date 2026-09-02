"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { Button } from "@/components/ui/button";
import { api, subscribeWS } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import type { GraphProposal } from "@/lib/stage-types";

/**
 * Конструктор схемы — старый канвас студии (Canon C), вернувшийся после
 * пивота: заказчик долго делал его дизайн, и упрощённый режим его не
 * заменяет, а дополняет. Упрощённый режим («лист») живёт на `/`, здесь —
 * узлы, связи, V-меню, НодСтудия, автосейв.
 *
 * Два режима:
 *  - `/pipeline?project=N` — схема ролика: то, по чему он реально идёт.
 *    Сохраняется автосейвом через слой project_graph; если правка жжёт
 *    сделанные шаги — канвас останавливается и спрашивает (диалог сброса).
 *  - `/pipeline` — шаблон для НОВЫХ роликов (`/api/workflows`): выбор
 *    схемы, копия, переименование, удаление. Уже созданные ролики от правок
 *    шаблона не меняются.
 *
 * `data-studio-scope` включает тёмную тему конструктора (см.
 * `app/studio-theme.css`): токены вешаются на body, чтобы дотянуться до
 * порталов (диалоги, дропдауны).
 */
export default function PipelinePage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      }
    >
      <PipelineRouter />
    </Suspense>
  );
}

function PipelineRouter() {
  const params = useSearchParams();
  const projectId = Number(params.get("project") ?? "");
  if (Number.isFinite(projectId) && projectId > 0) {
    return <ProjectSchemaPage projectId={projectId} />;
  }
  return <TemplateSchemaPage />;
}

// ── Схема ролика ─────────────────────────────────────────────────────────

function ProjectSchemaPage({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  // Слой project_graph нужен здесь ради предложения агента: канвас сам его
  // не знает, а кнопки «применить/отклонить» должны быть на схеме.
  const graphMeta = useQuery({
    queryKey: ["project-graph-meta", projectId],
    queryFn: () => api.getProjectGraph(projectId),
    refetchInterval: 10_000,
  });

  useEffect(() => {
    return subscribeWS(`projects.${projectId}`, () => {
      void qc.invalidateQueries({ queryKey: ["project-graph-meta", projectId] });
    });
  }, [projectId, qc]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["project", projectId] });
    void qc.invalidateQueries({ queryKey: ["project-graph-meta", projectId] });
    void qc.invalidateQueries({ queryKey: ["project-run", projectId] });
  };

  const applyProposal = useMutation({
    mutationFn: (p: GraphProposal) => api.applyGraphProposal(projectId, p.id),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Предложение применено: ${r.diff.summary}`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const discardProposal = useMutation({
    mutationFn: () => api.discardGraphProposal(projectId),
    onSuccess: () => {
      invalidate();
      toast.message("Предложение отклонено");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const resetGraph = useMutation({
    mutationFn: () => api.resetProjectGraph(projectId),
    onSuccess: () => {
      invalidate();
      toast.success("Штатная схема применена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const p = project.data;
  const title = (p?.title && p.title.trim()) || p?.topic || `Ролик #${projectId}`;
  const proposal = graphMeta.data?.proposal ?? null;

  return (
    <div
      data-studio-scope
      className="flex h-screen min-h-0 flex-col bg-background text-foreground"
    >
      <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-white/8 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href={`/?project=${projectId}`}
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            ← к ролику
          </Link>
          <span className="truncate text-sm font-medium">{title}</span>
          {p?.status && (
            <span className="font-mono text-[11px] text-muted-foreground">{p.status}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            href="/pipeline"
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            шаблон
          </Link>
          <Button
            size="sm"
            variant="ghost"
            className="h-8 text-xs"
            disabled={resetGraph.isPending}
            onClick={() => {
              if (
                confirm(
                  "Вернуть ролику штатную схему? Позиции и правки узлов пропадут.",
                )
              ) {
                resetGraph.mutate();
              }
            }}
          >
            Штатная схема
          </Button>
        </div>
      </header>

      {proposal && (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-primary/25 bg-primary/[0.06] px-4 py-2">
          <span className="text-xs font-medium text-primary">
            Агент предлагает правку схемы
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {proposal.reason || proposal.diff.summary}
            {proposal.reset.steps.length > 0 &&
              ` · сгорит шагов: ${proposal.reset.steps.length}`}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            disabled={applyProposal.isPending || discardProposal.isPending}
            onClick={() => discardProposal.mutate()}
          >
            Отклонить
          </Button>
          <Button
            size="sm"
            className="h-7 text-xs"
            disabled={applyProposal.isPending || discardProposal.isPending}
            onClick={() => applyProposal.mutate(proposal)}
          >
            {applyProposal.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            Применить
          </Button>
        </div>
      )}

      <div className="min-h-0 flex-1">
        <StudioWorkspace
          projectId={projectId}
          selectedNodeKey={selectedNodeKey}
          onSelectNode={setSelectedNodeKey}
          studioOpen={studioOpen}
          onStudioOpenChange={setStudioOpen}
        />
      </div>
    </div>
  );
}

// ── Шаблон для новых роликов ─────────────────────────────────────────────

function TemplateSchemaPage() {
  const qc = useQueryClient();
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);

  const list = useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows });

  useEffect(() => {
    if (workflowId !== null || !list.data?.length) return;
    setWorkflowId((list.data.find((w) => w.is_default) ?? list.data[0]).id);
  }, [list.data, workflowId]);

  // Тот же ключ, что у канваса: кэш общий, деталь не грузится дважды.
  const current = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api.getWorkflow(workflowId as number),
    enabled: workflowId !== null,
  });

  const refreshList = () => void qc.invalidateQueries({ queryKey: ["workflows"] });
  const fail = (e: unknown) => toast.error(errorMessageFromUnknown(e));

  const createNew = useMutation({
    mutationFn: () => {
      const name = prompt("Название новой схемы", "Новый конвейер")?.trim();
      if (!name) throw new Error("отменено");
      return api.createWorkflow({ name, nodes: [], edges: [] });
    },
    onSuccess: (wf) => {
      refreshList();
      setWorkflowId(wf.id);
      toast.success(`Схема «${wf.name}» создана — пустая, соберите из палитры`);
    },
    onError: (e: Error) => e.message !== "отменено" && fail(e),
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

  const rename = useMutation({
    mutationFn: () => {
      const wf = current.data;
      if (!wf) throw new Error("отменено");
      const name = prompt("Новое название", wf.name)?.trim();
      if (!name || name === wf.name) throw new Error("отменено");
      return api.saveWorkflow(wf.id, { name, nodes: wf.nodes, edges: wf.edges });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      refreshList();
    },
    onError: (e: Error) => e.message !== "отменено" && fail(e),
  });

  const remove = useMutation({
    mutationFn: () => {
      if (
        !confirm(
          `Удалить схему «${current.data?.name}»? Ролики, созданные по ней, не пострадают.`,
        )
      ) {
        throw new Error("отменено");
      }
      return api.deleteWorkflow(workflowId as number);
    },
    onSuccess: () => {
      setWorkflowId(null);
      refreshList();
      toast.success("Схема удалена");
    },
    onError: (e: Error) => e.message !== "отменено" && fail(e),
  });

  const resetDefault = useMutation({
    mutationFn: api.resetDefaultWorkflow,
    onSuccess: (wf) => {
      refreshList();
      setWorkflowId(wf.id);
      void qc.invalidateQueries({ queryKey: ["workflow", wf.id] });
      toast.success("Штатная схема восстановлена");
    },
    onError: fail,
  });

  const isDefault = list.data?.find((w) => w.id === workflowId)?.is_default ?? false;

  return (
    <div
      data-studio-scope
      className="flex h-screen min-h-0 flex-col bg-background text-foreground"
    >
      <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-white/8 px-4">
        <div className="flex min-w-0 items-center gap-2">
          <Link
            href="/"
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            ← к роликам
          </Link>
          <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
            шаблон для новых роликов
          </span>
          <select
            value={workflowId ?? ""}
            onChange={(e) => {
              setWorkflowId(Number(e.target.value));
              setSelectedNodeKey(null);
            }}
            className="studio-select max-w-[240px] truncate rounded-md border border-white/10 px-2 py-1 text-xs"
          >
            {(list.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
                {w.is_default ? " (штатная)" : ""}
              </option>
            ))}
          </select>
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => rename.mutate()}>
            переименовать
          </Button>
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => createNew.mutate()}>
            + новая
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-8 text-xs"
            disabled={duplicate.isPending}
            onClick={() => duplicate.mutate()}
          >
            копия
          </Button>
          {!isDefault && workflowId != null && (
            <Button
              size="sm"
              variant="ghost"
              className="h-8 text-xs text-destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              удалить
            </Button>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="h-8 text-xs"
            disabled={resetDefault.isPending}
            onClick={() => resetDefault.mutate()}
          >
            Вернуть штатную
          </Button>
        </div>
      </header>

      <p className="shrink-0 border-b border-white/8 px-4 py-1.5 text-[11px] text-muted-foreground">
        Это схема для новых роликов — правки сохраняются сами. Схема уже созданного ролика
        открывается с его экрана: «схема ролика».
      </p>

      <div className="min-h-0 flex-1">
        {workflowId != null ? (
          <StudioWorkspace
            projectId={null}
            workflowId={workflowId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={setSelectedNodeKey}
            studioOpen={studioOpen}
            onStudioOpenChange={setStudioOpen}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            {list.isLoading ? (
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            ) : (
              <div className="max-w-md space-y-3 text-center">
                <h2 className="text-lg font-semibold tracking-tight">Схем ещё нет</h2>
                <p className="text-sm text-muted-foreground">
                  Новые ролики заводятся по схеме-шаблону. Создайте штатную — и меняйте под
                  себя.
                </p>
                <Button size="sm" onClick={() => resetDefault.mutate()} disabled={resetDefault.isPending}>
                  Создать штатную схему
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
