"use client";

import { Suspense, useEffect, useState } from "react";
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
import { usePipelineMode } from "@/hooks/use-pipeline-mode";
import { api, subscribeWS } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { fleetPushToHub } from "@/lib/fleet-api";
import type { GraphProposal } from "@/lib/stage-types";

/**
 * Главная страница Студии: полный конвейер (пайплайн), канвас, сайдбар проектов,
 * инспектор, генерация Outsee Create, шторка стилей, балансы и чат.
 */
export default function StudioPage() {
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
  // Шаблон для новых роликов — явный режим (?template=1)
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
  const [chatOpen, setChatOpen] = usePersistedState("vp-studio-chat-open", false);
  const [fleetOpen, setFleetOpen] = useState(false);
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
    router.replace(`?project=${id}`);
  };

  const openTemplate = () => {
    setTemplateMode(true);
    setSelectedNodeKey(null);
    setStudioOpen(false);
    router.replace("?template=1");
  };

  // Адрес без параметра, а в localStorage проект есть — дописываем адрес,
  // чтобы обновление страницы и ссылки вели туда же.
  useEffect(() => {
    if (!templateMode && !(urlProjectId > 0) && persistedProjectId != null) {
      router.replace(`?project=${persistedProjectId}`);
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
      <WorkspaceModeSync projectId={selectedProjectId} />
      <div data-studio-scope className="flex h-[calc(100vh-48px)] min-h-0">
        <ProjectSidebar
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

function WorkspaceModeSync({ projectId }: { projectId: number | null }) {
  // Открыт проект чужой механики (диплинк, F5 со старым id) — воркспейс
  // следует за проектом. Обратного пути нет: воркспейс проект не меняет.
  const [mode, setMode] = usePipelineMode();
  const detail = useQuery({
    queryKey: ["project-mode", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null && projectId > 0,
    staleTime: 30_000,
    retry: false,
  });
  const projectMode = detail.data?.pipeline_mode;
  useEffect(() => {
    if ((projectMode === "v1" || projectMode === "v2") && projectMode !== mode) {
      setMode(projectMode);
    }
  }, [projectMode, mode, setMode]);
  return null;
}

function ProjectSchema({
  projectId,
  onOpenTemplate,
  ...canvas
}: CanvasProps & { projectId: number; onOpenTemplate: () => void }) {
  const qc = useQueryClient();

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

  const proposal = graphMeta.data?.proposal ?? null;

  return (
    <>

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

  const current = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => api.getWorkflow(workflowId as number),
    enabled: workflowId !== null,
  });

  const resetDefault = useMutation({
    mutationFn: api.resetDefaultWorkflow,
    onSuccess: (wf) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      setWorkflowId(wf.id);
      void qc.invalidateQueries({ queryKey: ["workflow", wf.id] });
      toast.success("Штатная схема восстановлена");
    },
    onError: (e: unknown) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
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
  );
}
