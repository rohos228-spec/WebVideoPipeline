"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { SiteEntryGate } from "@/components/shell/site-entry-gate";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { StreamsPanel } from "@/components/inspector/streams-panel";
import { useQuery } from "@tanstack/react-query";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { OutseeCreateWorkspace } from "@/components/outsee/outsee-create-workspace";
import { GptWorkspace } from "@/components/gpt/gpt-workspace";
import { OrchestratorPanel } from "@/components/orchestrator/orchestrator-panel";
import { useGlobalEvents } from "@/hooks/use-bus";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { api } from "@/lib/api";

function HomePageInner() {
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
    const openSidebar = () => setSidebarCollapsed(false);
    window.addEventListener("studio-open-projects-sidebar", openSidebar);
    const toggleSidebar = () => setSidebarCollapsed((v) => !v);
    window.addEventListener("studio-toggle-projects-sidebar", toggleSidebar);
    return () => {
      window.removeEventListener("studio-open-projects-sidebar", openSidebar);
      window.removeEventListener("studio-toggle-projects-sidebar", toggleSidebar);
    };
  }, []);

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
    };
    window.addEventListener("studio-select-project", onSelectProject);
    return () => window.removeEventListener("studio-select-project", onSelectProject);
  }, [setSelectedProjectId]);

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
      <div className="flex h-screen min-h-0">
        <ProjectSidebar
          selectedProjectId={selectedProjectId}
          onSelect={(id) => {
            setSelectedProjectId(id);
            setSelectedNodeKey(null);
            setStudioOpen(false);
          }}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
        />
        <main className="relative min-w-0 flex-1 overflow-hidden">
          <StudioWorkspace
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={onSelectNode}
            studioOpen={studioOpen}
            onStudioOpenChange={setStudioOpen}
          />
          <StreamsFloat projectId={selectedProjectId} />
        </main>
      </div>
      <OrchestratorPanel key={selectedProjectId ?? "none"} projectId={selectedProjectId} />
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
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null,
  });
  if (projectId == null) return null;
  return (
    <div className="pointer-events-none absolute right-4 top-4 z-30 w-[300px]">
      <div className="pointer-events-auto flex max-h-[calc(100vh-2rem)] flex-col gap-3 overflow-y-auto rounded-2xl border border-sky-500/25 bg-zinc-900/80 p-3.5 shadow-lg backdrop-blur-md">
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
