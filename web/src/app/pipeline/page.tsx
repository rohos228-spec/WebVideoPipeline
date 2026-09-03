"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { ChatPanel } from "@/components/studio/chat-panel";
import { FleetPanelSheet } from "@/components/fleet/fleet-panel-sheet";
import { CostsPanelSheet } from "@/components/costs/costs-panel-sheet";
import { FleetTransferBanner } from "@/components/fleet/fleet-transfer-banner";
import { OutseeCreateWorkspace } from "@/components/outsee/outsee-create-workspace";
import { GptWorkspace } from "@/components/gpt/gpt-workspace";
import { BazaWorkspace } from "@/components/baza/baza-workspace";
import { OrchestratorPanel } from "@/components/orchestrator/orchestrator-panel";
import { Button } from "@/components/ui/button";
import { useGlobalEvents } from "@/hooks/use-bus";
import {
  useFleetTransfer,
  FLEET_TRANSFER_PUSH_START,
  optimisticPushTransfer,
} from "@/hooks/use-fleet-transfer";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { api, subscribeWS } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { fleetPushToHub } from "@/lib/fleet-api";
import type { GraphProposal } from "@/lib/stage-types";

/**
 * Старая студия заказчика (Canon C) целиком: шапка с инструментами, сайдбар
 * проектов с мастером, канвас, инспектор справа, панель оркестратора,
 * чат, флот, стоимость, база, outsee, GPT. Это бывший `/` старой студии
 * (до пивота 01e9e5e), перенесённый на `/pipeline` один в один; упрощённый
 * режим («лист») остался на `/`.
 *
 * Отличия от старого `/`:
 *  - выбранный проект живёт в `?project=N` (ссылки с листа ролика ведут
 *    сюда), а localStorage — запасной источник, как раньше;
 *  - без проекта канвас показывает не онбординг, а шаблон для новых
 *    роликов (`/api/workflows`): выбор схемы, копия, переименование;
 *  - над канвасом ролика — баннер предложения агента и «штатная схема»
 *    (слой project_graph).
 *
 * `data-studio-scope` включает тёмную тему конструктора (см.
 * `app/studio-theme.css`): токены вешаются на body, чтобы дотянуться до
 * порталов (диалоги, дропдауны, тосты).
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
      <StudioHome />
    </Suspense>
  );
}

function StudioHome() {
  const params = useSearchParams();
  const router = useRouter();
  const urlProjectId = Number(params.get("project") ?? "");
  const [persistedProjectId, setPersistedProjectId] = usePersistedState<number | null>(
    "vp-studio-selected-project-id",
    null,
  );
  const selectedProjectId =
    Number.isFinite(urlProjectId) && urlProjectId > 0 ? urlProjectId : persistedProjectId;
  // Шаблон для новых роликов — явный режим (`?template=1`): сайдбар иначе
  // сам выбирает первый проект, и «без проекта» не бывает, пока проекты есть.
  const [templateMode, setTemplateMode] = useState(
    !(urlProjectId > 0) && params.get("template") === "1",
  );
  const canvasProjectId = templateMode ? null : selectedProjectId;

  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState(
    "vp-studio-sidebar-collapsed",
    false,
  );
  const [studioOpen, setStudioOpen] = useState(false);
  // Разговор слева, живой граф справа (SAAS-PIVOT §8.1). По умолчанию
  // свёрнут: канвас — основной инструмент, и отбирать у него треть экрана
  // ради панели, которой не пользуются, незачем.
  const [chatOpen, setChatOpen] = usePersistedState("vp-studio-chat-open", false);
  const [fleetOpen, setFleetOpen] = useState(false);
  const [costsOpen, setCostsOpen] = useState(false);
  const [outseeOpen, setOutseeOpen] = useState(false);
  const [gptOpen, setGptOpen] = useState(false);
  const [bazaOpen, setBazaOpen] = useState(false);
  const { transfer, dismiss } = useFleetTransfer(canvasProjectId);

  useGlobalEvents();

  // Один вход для смены проекта: адрес, localStorage, сброс выделения.
  const selectProject = (id: number) => {
    setPersistedProjectId(id);
    setTemplateMode(false);
    setSelectedNodeKey(null);
    setStudioOpen(false);
    router.replace(`/pipeline?project=${id}`);
  };

  const openTemplate = () => {
    setTemplateMode(true);
    setSelectedNodeKey(null);
    setStudioOpen(false);
    router.replace("/pipeline?template=1");
  };

  // Адрес без параметра, а в localStorage проект есть — дописываем адрес,
  // чтобы обновление страницы и ссылки вели туда же.
  useEffect(() => {
    if (!templateMode && !(urlProjectId > 0) && persistedProjectId != null) {
      router.replace(`/pipeline?project=${persistedProjectId}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const openSidebar = () => setSidebarCollapsed(false);
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    return () => window.removeEventListener("studio-open-projects-sidebar", openSidebar);
  }, [setSidebarCollapsed]);

  useEffect(() => {
    const toggleChat = () => setChatOpen((open) => !open);
    window.addEventListener("studio-toggle-chat", toggleChat);
    return () => window.removeEventListener("studio-toggle-chat", toggleChat);
  }, [setChatOpen]);

  useEffect(() => {
    const openFleet = () => setFleetOpen(true);
    window.addEventListener("studio-open-fleet", openFleet);
    return () => window.removeEventListener("studio-open-fleet", openFleet);
  }, []);

  useEffect(() => {
    const openCosts = () => setCostsOpen(true);
    window.addEventListener("studio-open-costs", openCosts);
    return () => window.removeEventListener("studio-open-costs", openCosts);
  }, []);

  useEffect(() => {
    const openOutsee = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) selectProject(detail.projectId);
      setOutseeOpen(true);
    };
    window.addEventListener("studio-open-outsee", openOutsee);
    return () => window.removeEventListener("studio-open-outsee", openOutsee);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const openGpt = () => setGptOpen(true);
    window.addEventListener("studio-open-gpt", openGpt);
    return () => window.removeEventListener("studio-open-gpt", openGpt);
  }, []);

  useEffect(() => {
    const openBaza = () => setBazaOpen(true);
    window.addEventListener("studio-open-baza", openBaza);
    return () => window.removeEventListener("studio-open-baza", openBaza);
  }, []);

  // Оркестратор создал проект → выделяем его в пайплайне.
  useEffect(() => {
    const onSelectProject = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) selectProject(detail.projectId);
    };
    window.addEventListener("studio-select-project", onSelectProject);
    return () => window.removeEventListener("studio-select-project", onSelectProject);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AppShell>
      <div data-studio-scope className="flex h-[calc(100vh-48px)] min-h-0">
        <ProjectSidebar
          // В режиме шаблона без выбранного проекта сайдбар получает 0, а не
          // null: иначе его автовыбор первого проекта выбьет из шаблона.
          selectedProjectId={selectedProjectId ?? (templateMode ? 0 : null)}
          onSelect={selectProject}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
        />
        {chatOpen && (
          <aside className="w-[380px] shrink-0 border-r border-white/8">
            <ChatPanel />
          </aside>
        )}
        <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
          {canvasProjectId != null ? (
            <ProjectSchema
              projectId={canvasProjectId}
              onOpenTemplate={openTemplate}
              selectedNodeKey={selectedNodeKey}
              onSelectNode={setSelectedNodeKey}
              studioOpen={studioOpen}
              onStudioOpenChange={setStudioOpen}
            />
          ) : (
            <TemplateSchema
              selectedNodeKey={selectedNodeKey}
              onSelectNode={setSelectedNodeKey}
              studioOpen={studioOpen}
              onStudioOpenChange={setStudioOpen}
            />
          )}
          <FleetTransferBanner
            transfer={transfer}
            onPushToHub={
              (transfer?.project_id ?? canvasProjectId) != null
                ? async () => {
                    const pid = transfer?.project_id ?? canvasProjectId!;
                    window.dispatchEvent(
                      new CustomEvent(FLEET_TRANSFER_PUSH_START, {
                        detail: optimisticPushTransfer(pid, transfer?.slug),
                      }),
                    );
                    const res = await fleetPushToHub(pid);
                    if ("started" in res && res.started) {
                      toast.message("Отправка идёт — смотри полоску внизу");
                      return;
                    }
                    toast.success(
                      res.size_mb
                        ? `Отправлено на главный ПК (${res.size_mb} MB)`
                        : "Отправлено на главный ПК",
                    );
                  }
                : undefined
            }
            onCancelTransfer={
              (transfer?.project_id ?? canvasProjectId) != null
                ? async () => {
                    await api.stopProject(transfer?.project_id ?? canvasProjectId!);
                  }
                : undefined
            }
            onDismiss={dismiss}
          />
          <OrchestratorPanel projectId={canvasProjectId} />
        </main>
        <FleetPanelSheet
          open={fleetOpen}
          onOpenChange={setFleetOpen}
          onOpenProject={selectProject}
        />
        <CostsPanelSheet
          open={costsOpen}
          onOpenChange={setCostsOpen}
          selectedProjectId={canvasProjectId}
        />
        <Inspector
          projectId={canvasProjectId}
          selectedNodeKey={selectedNodeKey}
          onOpenNodeStudio={() => {
            if (selectedNodeKey) {
              window.dispatchEvent(
                new CustomEvent("studio-open-node-prompts", {
                  detail: { nodeKey: selectedNodeKey },
                }),
              );
            } else {
              setStudioOpen(true);
            }
          }}
        />
      </div>
      <OutseeCreateWorkspace
        open={outseeOpen}
        onOpenChange={setOutseeOpen}
        projectId={canvasProjectId}
      />
      <GptWorkspace open={gptOpen} onOpenChange={setGptOpen} />
      <BazaWorkspace open={bazaOpen} onOpenChange={setBazaOpen} projectId={canvasProjectId} />
    </AppShell>
  );
}

interface CanvasProps {
  selectedNodeKey: string | null;
  onSelectNode: (key: string | null) => void;
  studioOpen: boolean;
  onStudioOpenChange: (open: boolean) => void;
}

// ── Схема ролика ─────────────────────────────────────────────────────────

function ProjectSchema({
  projectId,
  onOpenTemplate,
  ...canvas
}: CanvasProps & { projectId: number; onOpenTemplate: () => void }) {
  const qc = useQueryClient();

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

  const proposal = graphMeta.data?.proposal ?? null;

  return (
    <>
      <div className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-white/[0.06] px-3">
        <div className="flex min-w-0 items-center gap-3 text-[11px] text-muted-foreground">
          <Link href={`/?project=${projectId}`} className="transition-colors hover:text-foreground">
            ← лист ролика
          </Link>
          <button type="button" onClick={onOpenTemplate} className="transition-colors hover:text-foreground">
            шаблон для новых
          </button>
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-[11px]"
          disabled={resetGraph.isPending}
          onClick={() => {
            if (confirm("Вернуть ролику штатную схему? Позиции и правки узлов пропадут.")) {
              resetGraph.mutate();
            }
          }}
        >
          Штатная схема
        </Button>
      </div>

      {proposal && (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-primary/25 bg-primary/[0.06] px-4 py-2">
          <span className="text-xs font-medium text-primary">Агент предлагает правку схемы</span>
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {proposal.reason || proposal.diff.summary}
            {proposal.reset.steps.length > 0 && ` · сгорит шагов: ${proposal.reset.steps.length}`}
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
            {applyProposal.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Применить
          </Button>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <StudioWorkspace projectId={projectId} {...canvas} />
      </div>
    </>
  );
}

// ── Шаблон для новых роликов ─────────────────────────────────────────────

function TemplateSchema(canvas: CanvasProps) {
  const qc = useQueryClient();
  const [workflowId, setWorkflowId] = useState<number | null>(null);

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
    <>
      <div className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-white/[0.06] px-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
            шаблон для новых роликов
          </span>
          <select
            value={workflowId ?? ""}
            onChange={(e) => {
              setWorkflowId(Number(e.target.value));
              canvas.onSelectNode(null);
            }}
            className="studio-select max-w-[240px] truncate rounded-md border border-white/10 px-2 py-0.5 text-[11px]"
          >
            {(list.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
                {w.is_default ? " (штатная)" : ""}
              </option>
            ))}
          </select>
          <Button size="sm" variant="ghost" className="h-7 text-[11px]" onClick={() => rename.mutate()}>
            переименовать
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-[11px]" onClick={() => createNew.mutate()}>
            + новая
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-[11px]"
            disabled={duplicate.isPending}
            onClick={() => duplicate.mutate()}
          >
            копия
          </Button>
          {!isDefault && workflowId != null && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-[11px] text-destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              удалить
            </Button>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-[11px]"
          disabled={resetDefault.isPending}
          onClick={() => resetDefault.mutate()}
        >
          Вернуть штатную
        </Button>
      </div>

      <div className="relative min-h-0 flex-1">
        {workflowId != null ? (
          <StudioWorkspace projectId={null} workflowId={workflowId} {...canvas} />
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
    </>
  );
}
