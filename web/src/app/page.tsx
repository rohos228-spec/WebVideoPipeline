"use client";

import { useEffect, useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { useMobileLayout } from "@/components/shell/use-mobile-layout";
import { AppShell } from "@/components/shell/app-shell";
import { SiteEntryGate } from "@/components/shell/site-entry-gate";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { StreamsPanel } from "@/components/inspector/streams-panel";
import { useQuery } from "@tanstack/react-query";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { OutseeCreateWorkspace } from "@/components/outsee/outsee-create-workspace";
import { GptWorkspace } from "@/components/gpt/gpt-workspace";
import { OrchestratorPanel } from "@/components/orchestrator/orchestrator-panel";
import { OPEN_ORCHESTRATOR_CHAT, OrchestratorDock } from "@/components/orchestrator/orchestrator-dock";
import { useGlobalEvents } from "@/hooks/use-bus";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { api } from "@/lib/api";

function HomePageInner() {
  const mobile = useMobileLayout();
  const [projectsOpen, setProjectsOpen] = useState(false);
  useEffect(() => {
    if (!mobile) setProjectsOpen(false);
  }, [mobile]);
  const [selectedProjectId, setSelectedProjectId] = usePersistedState<number | null>(
    "vp-studio-selected-project-id",
    null,
  );
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState(
    "vp-studio-sidebar-collapsed",
    false,
  );
  const [studioOpen, setStudioOpen] = useState(false);
  const [outseeOpen, setOutseeOpen] = useState(false);
  const [gptOpen, setGptOpen] = useState(false);

  useGlobalEvents();

  useEffect(() => {
    const openSidebar = () => mobile ? setProjectsOpen(true) : setSidebarCollapsed(false);
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    const toggleSidebar = () => mobile ? setProjectsOpen((v) => !v) : setSidebarCollapsed((v) => !v);
    window.addEventListener("studio-toggle-projects-sidebar", toggleSidebar);
    return () => {
      window.removeEventListener("studio-open-projects-sidebar", openSidebar);
      window.removeEventListener("studio-toggle-projects-sidebar", toggleSidebar);
    };
  }, [mobile, setSidebarCollapsed]);

  useEffect(() => {
    const openOutsee = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) setSelectedProjectId(detail.projectId);
      setOutseeOpen(true);
    };
    window.addEventListener("studio-open-outsee", openOutsee);
    return () => window.removeEventListener("studio-open-outsee", openOutsee);
  }, [setSelectedProjectId]);

  useEffect(() => {
    const openGpt = () => setGptOpen(true);
    window.addEventListener("studio-open-gpt", openGpt);
    return () => window.removeEventListener("studio-open-gpt", openGpt);
  }, []);

  // Оркестратор создал проект → выделяем его в пайплайне.
  useEffect(() => {
    const onSelectProject = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      // null — текущий проект удалён оркестратором: снять выделение,
      // чтобы канвас не держал граф удалённого проекта.
      if (detail?.projectId === undefined) return;
      setSelectedProjectId(detail.projectId);
      setSelectedNodeKey(null);
      setStudioOpen(false);
      setProjectsOpen(false);
    };
    window.addEventListener("studio-select-project", onSelectProject);
    return () => window.removeEventListener("studio-select-project", onSelectProject);
  }, [setSelectedProjectId]);

  // Кнопка «Оркестратор» с другой страницы ведёт на /?chat=open: открыть док чата супервизора.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("chat") !== "open") return;
    params.delete("chat");
    const rest = params.toString();
    window.history.replaceState(null, "", rest ? `/?${rest}` : "/");
    const timer = window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent(OPEN_ORCHESTRATOR_CHAT, { detail: { tab: "chat" } }));
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  // «Начать проект» с лендинга приходит сюда как /?new=1 — открываем окно нового
  // проекта и убираем параметр из адреса, чтобы F5 не открывал окно снова.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("new") !== "1") return;
    window.history.replaceState(null, "", "/");
    const t = window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent("studio-open-new-project"));
    }, 400);
    return () => window.clearTimeout(t);
  }, []);

  const onSelectNode = (key: string | null) => {
    setSelectedNodeKey(key);
  };

  return (
    <AppShell>
      <div className="flex min-h-0 flex-1">
        <ProjectSidebar
          selectedProjectId={selectedProjectId}
          onSelect={(id) => {
            setSelectedProjectId(id);
            setSelectedNodeKey(null);
            setStudioOpen(false);
            setProjectsOpen(false);
          }}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
          mobileOpen={projectsOpen}
          onMobileOpenChange={setProjectsOpen}
        />
        <main data-studio-main className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          <StudioWorkspace
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={onSelectNode}
            studioOpen={studioOpen}
            onStudioOpenChange={setStudioOpen}
          />
          <StreamsFloat projectId={selectedProjectId} />
          <OrchestratorDock projectId={selectedProjectId} />
        </main>
      </div>
      <OrchestratorPanel projectId={selectedProjectId} />
      <OutseeCreateWorkspace
        open={outseeOpen}
        onOpenChange={setOutseeOpen}
        projectId={selectedProjectId}
      />
      <GptWorkspace
        open={gptOpen}
        onOpenChange={setGptOpen}
      />
    </AppShell>
  );
}

function StreamsFloat({ projectId }: { projectId: number | null }) {
  const mobile = useMobileLayout();
  const [expanded, setExpanded] = useState(false);
  useEffect(() => setExpanded(false), [projectId]);
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
  });
  if (projectId == null) return null;
  return (
    <div className="pointer-events-none absolute right-2 top-16 z-30 max-w-[calc(100%-1rem)] md:right-4 md:top-4 md:w-[300px]">
      <button type="button" aria-label={expanded ? "Скрыть параметры проекта" : "Показать параметры проекта"} aria-expanded={expanded} aria-controls="studio-project-controls" onClick={() => setExpanded((v) => !v)} className="pointer-events-auto ml-auto flex h-11 items-center gap-2 rounded-xl border border-sky-500/25 bg-zinc-900/95 px-3 text-sm text-zinc-200 shadow-lg md:hidden">
        {expanded ? <X className="h-4 w-4" /> : <SlidersHorizontal className="h-4 w-4" />}Параметры
      </button>
      <div id="studio-project-controls" hidden={mobile && !expanded} className={`pointer-events-auto mt-2 ${mobile && !expanded ? "hidden" : "flex"} max-h-[calc(100dvh-12rem)] w-[300px] max-w-full flex-col gap-3 overflow-y-auto rounded-2xl border border-sky-500/25 bg-zinc-900/95 p-3.5 shadow-lg backdrop-blur-md md:mt-0 md:max-h-[calc(100dvh-2rem)] md:bg-zinc-900/80`}>
        <div id="vp-right-controls" />
        <StreamsPanel project={project.data ?? null} hideTextModels bare />
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <SiteEntryGate>
      <HomePageInner />
    </SiteEntryGate>
  );
}
