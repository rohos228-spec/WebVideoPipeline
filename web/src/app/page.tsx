"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { ProjectSidebar } from "@/components/sidebar/project-sidebar";
import { Inspector } from "@/components/inspector/inspector";
import { StudioWorkspace } from "@/components/studio/studio-workspace";
import { FleetPanelSheet } from "@/components/fleet/fleet-panel-sheet";
import { FleetTransferBanner } from "@/components/fleet/fleet-transfer-banner";
import { OutseeCreateWorkspace } from "@/components/outsee/outsee-create-workspace";
import { GptWorkspace } from "@/components/gpt/gpt-workspace";
import { BazaWorkspace } from "@/components/baza/baza-workspace";
import { OrchestratorPanel } from "@/components/orchestrator/orchestrator-panel";
import { useGlobalEvents } from "@/hooks/use-bus";
import { useFleetTransfer, FLEET_TRANSFER_PUSH_START, optimisticPushTransfer } from "@/hooks/use-fleet-transfer";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { api } from "@/lib/api";
import { AuthGate } from "@/components/auth/auth-gate";
import { fleetPushToHub } from "@/lib/fleet-api";

export default function HomePage() {
  return (
    <AuthGate>
      <StudioPage />
    </AuthGate>
  );
}

function StudioPage() {
  const [selectedProjectId, setSelectedProjectId] = usePersistedState<number | null>(
    "vp-studio-selected-project-id",
    null,
  );
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState(
    "vp-studio-sidebar-collapsed",
    // small screens (1280/1366): start with the projects panel collapsed so the canvas gets the room.
    // Studio is rendered client-only (inside AuthGate), so reading window here is hydration-safe.
    typeof window !== "undefined" && window.innerWidth < 1440,
  );
  const [studioOpen, setStudioOpen] = useState(false);
  const [fleetOpen, setFleetOpen] = useState(false);
  const [outseeOpen, setOutseeOpen] = useState(false);
  const [gptOpen, setGptOpen] = useState(false);
  const [bazaOpen, setBazaOpen] = useState(false);
  const { transfer, dismiss } = useFleetTransfer(selectedProjectId);

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
    const openFleet = () => setFleetOpen(true);
    window.addEventListener("studio-open-fleet", openFleet);
    return () => window.removeEventListener("studio-open-fleet", openFleet);
  }, []);

  useEffect(() => {
    const openOutsee = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: number | null }>).detail;
      if (detail?.projectId != null) setSelectedProjectId(detail.projectId);
      setOutseeOpen(true);
    };
    window.addEventListener("studio-open-create", openOutsee);
    return () => window.removeEventListener("studio-open-create", openOutsee);
  }, [setSelectedProjectId]);

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
      if (detail?.projectId != null) {
        setSelectedProjectId(detail.projectId);
        setSelectedNodeKey(null);
        setStudioOpen(false);
      }
    };
    window.addEventListener("studio-select-project", onSelectProject);
    return () => window.removeEventListener("studio-select-project", onSelectProject);
  }, [setSelectedProjectId]);

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
        <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="relative min-h-0 flex-1">
          <StudioWorkspace
            projectId={selectedProjectId}
            selectedNodeKey={selectedNodeKey}
            onSelectNode={onSelectNode}
            studioOpen={studioOpen}
            onStudioOpenChange={setStudioOpen}
          />
          </div>
          <FleetTransferBanner
            transfer={transfer}
            onPushToHub={
              (transfer?.project_id ?? selectedProjectId) != null
                ? async () => {
                    const pid = transfer?.project_id ?? selectedProjectId!;
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
              (transfer?.project_id ?? selectedProjectId) != null
                ? async () => {
                    await api.stopProject(transfer?.project_id ?? selectedProjectId!);
                  }
                : undefined
            }
            onDismiss={dismiss}
          />
          <OrchestratorPanel projectId={selectedProjectId} />
        </main>
        <FleetPanelSheet
          open={fleetOpen}
          onOpenChange={setFleetOpen}
          onOpenProject={(projectId) => {
            setSelectedProjectId(projectId);
            setSelectedNodeKey(null);
            setStudioOpen(false);
          }}
        />
        <Inspector
          projectId={selectedProjectId}
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
        projectId={selectedProjectId}
      />
      <GptWorkspace
        open={gptOpen}
        onOpenChange={setGptOpen}
      />
      <BazaWorkspace
        open={bazaOpen}
        onOpenChange={setBazaOpen}
        projectId={selectedProjectId}
      />
    </AppShell>
  );
}
